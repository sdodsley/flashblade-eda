#!/usr/bin/python
# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Manage the OpenTelemetry metrics targets of an Everpure FlashBlade."""

DOCUMENTATION = r"""
---
module: purefb_telemetry_target
version_added: "0.1.0"
short_description: Manage FlashBlade telemetry targets
description:
  - Create, update and delete telemetry targets, the OTLP receivers the
    array pushes its metrics to, and attach a target to the built-in
    telemetry metrics policies that decide which metrics it receives.
  - The array opens a connection to the endpoint when a target is created
    and refuses an endpoint nothing listens on.
  - Works over REST (2.21, Purity//FB 4.6.4 or later), because the SDK
    client negotiated for that release has no telemetry methods. Needs the
    C(requests) library.
author:
  - Simon Dodsley (@sdodsley)
options:
  name:
    description:
      - Name of the telemetry target.
    type: str
    required: true
  endpoint:
    description:
      - Where the receiver listens, as C(host:port) or C(host:port/path),
        for example C(prom.example.net:9090/api/v1/otlp/v1/metrics). The array takes no
        scheme; one given here is removed with a warning. Required when
        I(state) is C(present).
    type: str
  transport:
    description:
      - How the metrics travel. C(otlp-http-protobuf-without-tls) is for a
        plain HTTP receiver.
    type: str
    default: otlp-http-protobuf
    choices: [ otlp-http-protobuf, otlp-http-protobuf-without-tls ]
  source:
    description:
      - Name of the network interface the array sends from, a data VIP such
        as C(2200-datavip). Required when I(state) is C(present).
    type: str
  enabled:
    description:
      - Whether the array pushes to the target.
    type: bool
    default: true
  policies:
    description:
      - Telemetry metrics policies the target must be attached to. Each
        named policy's target list is extended with this target and the
        policy's other targets are kept. Omit it to leave the policies as
        they are. Deleting a target detaches it from every policy first.
    type: list
    elements: str
  test:
    description:
      - Ask the array to test the target after the run and return the
        answer under C(test). Not a change; skipped in check mode and when
        the target is absent.
    type: bool
    default: false
  api_version:
    description:
      - REST version the requests ask for. Telemetry targets arrived with
        2.21.
    type: str
    default: "2.21"
  state:
    description:
      - Create or update the target, or delete it.
    type: str
    default: present
    choices: [ present, absent ]
extends_documentation_fragment:
  - everpure.flashblade.everpure.fb
"""

EXAMPLES = r"""
- name: Push metrics to a plain Prometheus OTLP receiver from a data VIP
  sdodsley.flashblade_eda.purefb_telemetry_target:
    name: eda-prom
    endpoint: prom.example.net:9090/api/v1/otlp/v1/metrics
    transport: otlp-http-protobuf-without-tls
    source: 2200-datavip
    policies:
      - device_perf_metrics
      - device_space_metrics
      - fs_perf_metrics
    test: true
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
    disable_warnings: true

- name: Stop pushing without deleting the target
  sdodsley.flashblade_eda.purefb_telemetry_target:
    name: eda-prom
    endpoint: prom.example.net:9090/api/v1/otlp/v1/metrics
    transport: otlp-http-protobuf-without-tls
    source: 2200-datavip
    enabled: false
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"

- name: Delete the target and detach it from every policy
  sdodsley.flashblade_eda.purefb_telemetry_target:
    name: eda-prom
    state: absent
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
"""

RETURN = r"""
target:
  description: The target after the run; empty when it is absent.
  returned: always
  type: dict
  contains:
    name:
      description: Name of the target.
      type: str
    endpoint:
      description: Where the receiver listens, without a scheme.
      type: str
    transport:
      description: How the metrics travel.
      type: str
    source:
      description: Network interface the array sends from.
      type: str
    enabled:
      description: Whether the array pushes to the target.
      type: bool
    policies:
      description: Sorted names of the policies the target is attached to.
      type: list
      elements: str
  sample:
    name: eda-prom
    endpoint: prom.example.net:9090/api/v1/otlp/v1/metrics
    transport: otlp-http-protobuf-without-tls
    source: 2200-datavip
    enabled: true
    policies: [device_perf_metrics, device_space_metrics]
test:
  description: The array's answer to the target test.
  returned: when I(test) is true, the target is present and not in check mode
  type: dict
  contains:
    status:
      description: HTTP status of the test request; 200 means delivered.
      type: int
    ok:
      description: Whether the test delivered.
      type: bool
    body:
      description: The parsed answer, empty when the array sent none.
      type: dict
  sample:
    status: 200
    ok: true
    body: {}
"""

import warnings

HAS_REQUESTS = True
try:
    import requests
except ImportError:
    HAS_REQUESTS = False

from ansible.module_utils.basic import AnsibleModule

try:
    from ansible_collections.everpure.flashblade.plugins.module_utils.purefb import (
        purefb_argument_spec,
    )
except ImportError:

    def purefb_argument_spec():
        """The connection options, the same three the collection's modules
        take; this module runs over REST, so it does not need the collection
        itself."""
        return dict(
            fb_url=dict(type="str"),
            api_token=dict(type="str", no_log=True),
            disable_warnings=dict(type="bool", default=False),
        )


TRANSPORTS = ["otlp-http-protobuf", "otlp-http-protobuf-without-tls"]
REQUESTS_MISSING = (
    "the requests library is required for this module: pip install requests"
)


def rest_error(status, data):
    """The array's message for a failed request, or the status when the body
    carries none."""
    errors = data.get("errors") if isinstance(data, dict) else None
    message = errors[0].get("message") if errors else None
    return message or "status {0}".format(status)


class Rest:
    """The array over REST for what the negotiated SDK client lacks: one
    login with the api-token header, x-auth-token on every request under
    /api/<version>/, logout when done. No message built here carries the
    token."""

    TIMEOUT = 60

    def __init__(self, module):
        self.module = module
        base = str(module.params["fb_url"] or "").rstrip("/")
        if not base.startswith(("http://", "https://")):
            base = "https://" + base
        self.base = base
        self.version = module.params["api_version"]
        self.session = requests.Session()
        # disable_warnings is the collection's name for "self-signed
        # certificate": do not verify, and keep urllib3 quiet about it
        self.session.verify = not module.params["disable_warnings"]
        if not self.session.verify:
            warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        self.logged_in = False

    def login(self):
        try:
            res = self.session.request(
                "POST",
                self.base + "/api/login",
                headers={"api-token": self.module.params["api_token"]},
                timeout=self.TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:
            self.module.fail_json(
                msg="Failed to reach {0} for login: {1}".format(self.base, exc)
            )
        token = res.headers.get("x-auth-token") if res.status_code == 200 else None
        if not token:
            self.module.fail_json(
                msg="Login to {0} failed with status {1}".format(
                    self.base, res.status_code
                )
            )
        self.session.headers["x-auth-token"] = token
        self.logged_in = True
        return self

    def call(self, method, path, params=None, body=None):
        """One request under /api/<version>/; returns the status and the
        parsed body, {} when there is none."""
        if not self.logged_in:
            self.login()
        try:
            res = self.session.request(
                method,
                "{0}/api/{1}/{2}".format(self.base, self.version, path),
                params=params,
                json=body,
                timeout=self.TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:
            self.module.fail_json(
                msg="{0} {1} on {2} failed: {3}".format(method, path, self.base, exc)
            )
        try:
            data = res.json()
        except ValueError:
            data = {}
        return res.status_code, data if isinstance(data, dict) else {}

    def logout(self):
        if self.logged_in:
            try:
                self.session.request(
                    "POST", self.base + "/api/logout", timeout=self.TIMEOUT
                )
            except requests.exceptions.RequestException:
                # the session expires on its own; nothing to report
                pass
        self.session.close()


def get_target(module, rest, name):
    """The existing target as a dict, or None. The array answers a missing
    name with a 400, which reads as absent."""
    status, data = rest.call("GET", "telemetry-targets", params={"names": name})
    if status == 200:
        items = data.get("items") or []
        return items[0] if items else None
    if status in (400, 404):
        return None
    module.fail_json(
        msg="Failed to read telemetry target {0}. Error: {1}".format(
            name, rest_error(status, data)
        )
    )


def list_policies(module, rest):
    """The telemetry metrics policies by name."""
    status, data = rest.call("GET", "telemetry-metrics-policies")
    if status != 200:
        module.fail_json(
            msg="Failed to list the telemetry metrics policies. Error: {0}".format(
                rest_error(status, data)
            )
        )
    return {item.get("name"): item for item in data.get("items") or []}


def policy_targets(policy):
    return sorted(
        ref.get("name") for ref in policy.get("targets") or [] if ref.get("name")
    )


def set_policy_targets(module, rest, policy, names):
    """PATCH a policy's full target list."""
    status, data = rest.call(
        "PATCH",
        "telemetry-metrics-policies",
        params={"names": policy},
        body={"targets": [{"name": name} for name in names]},
    )
    if status != 200:
        module.fail_json(
            msg="Failed to set the targets of telemetry policy {0}. Error: {1}".format(
                policy, rest_error(status, data)
            )
        )


def create_target(module, rest, name, wanted):
    body = {
        "endpoint": wanted["endpoint"],
        "transport": wanted["transport"],
        "source": {"name": wanted["source"]},
    }
    status, data = rest.call(
        "POST", "telemetry-targets", params={"names": name}, body=body
    )
    if status != 200:
        module.fail_json(
            msg="Failed to create telemetry target {0}. Error: {1}".format(
                name, rest_error(status, data)
            )
        )


def update_target(module, rest, name, changes):
    """PATCH only what differs; the source travels as a reference."""
    body = dict(changes)
    if "source" in body:
        body["source"] = {"name": body["source"]}
    status, data = rest.call(
        "PATCH", "telemetry-targets", params={"names": name}, body=body
    )
    if status != 200:
        module.fail_json(
            msg="Failed to update telemetry target {0}. Error: {1}".format(
                name, rest_error(status, data)
            )
        )


def delete_target(module, rest, name):
    status, data = rest.call("DELETE", "telemetry-targets", params={"names": name})
    if status != 200:
        module.fail_json(
            msg="Failed to delete telemetry target {0}. Error: {1}".format(
                name, rest_error(status, data)
            )
        )


def test_target(rest, name):
    """The array's test of the target: 200 when the receiver took a metric.
    A refusal is an answer here, not a failure."""
    status, data = rest.call("GET", "telemetry-targets/test", params={"names": name})
    return {"status": status, "ok": status == 200, "body": data}


def current_values(target):
    return {
        "endpoint": target.get("endpoint"),
        "transport": target.get("transport"),
        "source": (target.get("source") or {}).get("name"),
        "enabled": target.get("enabled"),
    }


def facts(name, values, policies):
    return dict(name=name, policies=sorted(policies), **values)


def run(module, rest):
    name = module.params["name"]
    state = module.params["state"]
    wanted_policies = module.params["policies"]
    if wanted_policies is not None:
        wanted_policies = sorted(set(wanted_policies))

    target = get_target(module, rest, name)
    policies = list_policies(module, rest)
    carrying = sorted(p for p, item in policies.items() if name in policy_targets(item))

    if state == "absent":
        if target is None:
            module.exit_json(changed=False, target={})
        if not module.check_mode:
            for policy in carrying:
                others = [t for t in policy_targets(policies[policy]) if t != name]
                set_policy_targets(module, rest, policy, others)
            delete_target(module, rest, name)
        module.exit_json(changed=True, target={})

    endpoint = module.params["endpoint"]
    if "://" in endpoint:
        scheme, endpoint = endpoint.split("://", 1)
        module.warn(
            "endpoint takes no scheme; {0}:// was removed from it".format(scheme)
        )
    if ":" not in endpoint.split("/", 1)[0]:
        module.fail_json(
            msg="endpoint must be host:port or host:port/path; got {0!r}".format(
                endpoint
            )
        )
    wanted = {
        "endpoint": endpoint,
        "transport": module.params["transport"],
        "source": module.params["source"],
        "enabled": module.params["enabled"],
    }
    unknown = [p for p in wanted_policies or [] if p not in policies]
    if unknown:
        module.fail_json(
            msg="Telemetry metrics policies {0} do not exist; the array has {1}".format(
                ", ".join(unknown), ", ".join(sorted(policies))
            )
        )

    changed = False
    if target is None:
        changed = True
        if not module.check_mode:
            create_target(module, rest, name, wanted)
            # a new target is enabled; enabled is not known to be a POST field
            if not wanted["enabled"]:
                update_target(module, rest, name, {"enabled": False})
    else:
        current = current_values(target)
        changes = {k: v for k, v in wanted.items() if current[k] != v}
        if changes:
            changed = True
            if not module.check_mode:
                update_target(module, rest, name, changes)

    to_attach = [p for p in wanted_policies or [] if p not in carrying]
    if to_attach:
        changed = True
        if not module.check_mode:
            for policy in to_attach:
                set_policy_targets(
                    module, rest, policy, policy_targets(policies[policy]) + [name]
                )

    result = dict(changed=changed, target=facts(name, wanted, carrying + to_attach))
    if module.params["test"] and not module.check_mode:
        result["test"] = test_target(rest, name)
    module.exit_json(**result)


def main():
    argument_spec = purefb_argument_spec()
    argument_spec.update(
        dict(
            name=dict(type="str", required=True),
            endpoint=dict(type="str"),
            transport=dict(type="str", default=TRANSPORTS[0], choices=TRANSPORTS),
            source=dict(type="str"),
            enabled=dict(type="bool", default=True),
            policies=dict(type="list", elements="str"),
            test=dict(type="bool", default=False),
            api_version=dict(type="str", default="2.21"),
            state=dict(type="str", default="present", choices=["present", "absent"]),
        )
    )
    module = AnsibleModule(
        argument_spec,
        required_if=[["state", "present", ["endpoint", "source"]]],
        supports_check_mode=True,
    )
    if not HAS_REQUESTS:
        module.fail_json(msg=REQUESTS_MISSING)
    if not module.params["fb_url"] or not module.params["api_token"]:
        module.fail_json(msg="fb_url and api_token are required")

    rest = Rest(module)
    try:
        run(module, rest)
    finally:
        rest.logout()


if __name__ == "__main__":
    main()
