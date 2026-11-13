#!/usr/bin/python
# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Manage the targets of a telemetry metrics policy on an Everpure FlashBlade."""

DOCUMENTATION = r"""
---
module: purefb_telemetry_policy
version_added: "0.1.0"
short_description: Manage the targets of a FlashBlade telemetry metrics policy
description:
  - Set the telemetry targets a built-in telemetry metrics policy pushes
    to. The policies are fixed by the array (C(device_perf_metrics),
    C(fs_space_metrics), C(bucket_perf_metrics) and so on); each carries a
    set of rules that name a metric and its resolution, and the targets it
    sends them to. The module makes the target list exactly the one given.
  - A policy's C(enabled) flag is read-only on REST 2.21 and is reported,
    not managed.
  - Works over REST (2.21, Purity//FB 4.6.4 or later), because the SDK
    client negotiated for that release has no telemetry methods. Needs the
    C(requests) library.
author:
  - Simon Dodsley (@sdodsley)
options:
  name:
    description:
      - Name of the built-in policy.
    type: str
    required: true
  targets:
    description:
      - The telemetry targets the policy pushes to, the full list. An empty
        list detaches every target.
    type: list
    elements: str
    required: true
  api_version:
    description:
      - REST version the requests ask for. Telemetry policies arrived with
        2.21.
    type: str
    default: "2.21"
  state:
    description:
      - The policies are built in, so only C(present) exists.
    type: str
    default: present
    choices: [ present ]
extends_documentation_fragment:
  - everpure.flashblade.everpure.fb
"""

EXAMPLES = r"""
- name: Send the device performance metrics to one receiver only
  sdodsley.flashblade_eda.purefb_telemetry_policy:
    name: device_perf_metrics
    targets:
      - eda-prom
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
    disable_warnings: true

- name: Detach every target from the bucket client metrics
  sdodsley.flashblade_eda.purefb_telemetry_policy:
    name: bucket_client_perf_metrics
    targets: []
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
"""

RETURN = r"""
policy:
  description: The policy after the run.
  returned: always
  type: dict
  contains:
    name:
      description: Name of the policy.
      type: str
    enabled:
      description: Whether the array evaluates the policy; read-only.
      type: bool
    targets:
      description: Sorted names of the targets the policy pushes to.
      type: list
      elements: str
    metric_names:
      description: The OTLP metric names of the policy's rules, in rule order.
      type: list
      elements: str
    resolution_ms:
      description: How often the policy's first rule emits, in milliseconds.
      type: int
  sample:
    name: device_perf_metrics
    enabled: true
    targets: [eda-prom]
    metric_names:
      - storage.device.bw_bpo
      - storage.device.bw_bps
      - storage.device.iops
      - storage.device.latency_us
    resolution_ms: 60000
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


def get_policy(module, rest, name):
    """The built-in policy as a dict; a missing name is a typo, so it fails
    with the names the array has."""
    status, data = rest.call(
        "GET", "telemetry-metrics-policies", params={"names": name}
    )
    items = (data.get("items") or []) if status == 200 else []
    if items:
        return items[0]
    status, listing = rest.call("GET", "telemetry-metrics-policies")
    known = sorted(item.get("name") for item in listing.get("items") or [])
    module.fail_json(
        msg="Telemetry metrics policy {0} does not exist. Error: {1}. The array "
        "has: {2}".format(name, rest_error(status, data), ", ".join(known))
    )


def policy_targets(policy):
    return sorted(
        ref.get("name") for ref in policy.get("targets") or [] if ref.get("name")
    )


def set_targets(module, rest, name, targets):
    status, data = rest.call(
        "PATCH",
        "telemetry-metrics-policies",
        params={"names": name},
        body={"targets": [{"name": target} for target in targets]},
    )
    if status != 200:
        module.fail_json(
            msg="Failed to set the targets of telemetry policy {0}. Error: {1}".format(
                name, rest_error(status, data)
            )
        )


def rules_of(module, rest, name):
    """Metric names in rule order and the first rule's resolution. The rules
    listing is information, so a refusal becomes a warning, not a failure."""
    status, data = rest.call("GET", "telemetry-metrics-policies/rules")
    if status != 200:
        module.warn(
            "Could not list the telemetry rules: {0}".format(rest_error(status, data))
        )
        return [], None
    rules = sorted(
        (
            rule
            for rule in data.get("items") or []
            if (rule.get("policy") or {}).get("name") == name
        ),
        key=lambda rule: rule.get("index") or 0,
    )
    names = [rule.get("metric_name") for rule in rules if rule.get("metric_name")]
    resolution = rules[0].get("resolution") if rules else None
    return names, resolution


def run(module, rest):
    name = module.params["name"]
    wanted = sorted(set(module.params["targets"]))

    policy = get_policy(module, rest, name)
    current = policy_targets(policy)
    changed = current != wanted
    if changed and not module.check_mode:
        set_targets(module, rest, name, wanted)
    metric_names, resolution = rules_of(module, rest, name)
    module.exit_json(
        changed=changed,
        policy={
            "name": name,
            "enabled": policy.get("enabled"),
            "targets": wanted if changed else current,
            "metric_names": metric_names,
            "resolution_ms": resolution,
        },
    )


def main():
    argument_spec = purefb_argument_spec()
    argument_spec.update(
        dict(
            name=dict(type="str", required=True),
            targets=dict(type="list", elements="str", required=True),
            api_version=dict(type="str", default="2.21"),
            state=dict(type="str", default="present", choices=["present"]),
        )
    )
    module = AnsibleModule(argument_spec, supports_check_mode=True)
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
