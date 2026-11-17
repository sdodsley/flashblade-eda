#!/usr/bin/python
# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Manage the QoS policies of an Everpure FlashBlade and what carries them."""

DOCUMENTATION = r"""
---
module: purefb_qos
version_added: "0.1.0"
short_description: Manage FlashBlade QoS policies and their assignment
description:
  - Create, update and delete QoS policies, which cap the operations or
    bytes per second of the file systems and buckets that carry them, and
    assign a policy to file systems and buckets.
  - Deleting a policy clears it first from the objects listed and from
    every live file system and bucket the array reports carrying it, so
    nothing keeps a reference to a policy that is gone.
  - The policy itself goes through the SDK; assigning and clearing go
    over REST (C(PATCH /file-systems) and C(/buckets) with a C(qos_policy)
    reference), which needs the C(requests) library. Clearing is sent as
    an empty policy name and, should the array refuse that, as a null
    policy, with a warning that says which form it took.
  - Needs Purity//FB 4.5.3 (REST 2.17) or later.
author:
  - Simon Dodsley (@sdodsley)
options:
  name:
    description:
      - Name of the policy.
    type: str
    required: true
  max_total_ops_per_sec:
    description:
      - Cap on operations per second, 100 or more. On creation at least one
        of the two caps is required; on an existing policy an omitted cap is
        left as it is.
    type: int
  max_total_bytes_per_sec:
    description:
      - Cap on bytes per second.
    type: int
  enabled:
    description:
      - Whether the policy caps anything.
    type: bool
    default: true
  file_systems:
    description:
      - File systems to assign the policy to. Objects not listed keep
        whatever policy they carry.
    type: list
    elements: str
  buckets:
    description:
      - Buckets to assign the policy to.
    type: list
    elements: str
  api_version:
    description:
      - REST version the assignment requests ask for. By default the newest
        version the array serves, 2.17 at least, where QoS policies arrived.
    type: str
  state:
    description:
      - Create or update the policy and assign it, or clear and delete it.
    type: str
    default: present
    choices: [ present, absent ]
extends_documentation_fragment:
  - everpure.flashblade.everpure.fb
"""

EXAMPLES = r"""
- name: Tarpit one file system at 100 operations per second
  sdodsley.flashblade_eda.purefb_qos:
    name: eda-tarpit
    max_total_ops_per_sec: 100
    file_systems:
      - scratch-01
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
    disable_warnings: true

- name: Assign an existing policy to a bucket without touching its caps
  sdodsley.flashblade_eda.purefb_qos:
    name: eda-tarpit
    buckets:
      - eda-data
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"

- name: Lift the tarpit everywhere and delete the policy
  sdodsley.flashblade_eda.purefb_qos:
    name: eda-tarpit
    state: absent
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
"""

RETURN = r"""
policy:
  description: The policy after the run; empty when it is absent.
  returned: always
  type: dict
  contains:
    name:
      description: Name of the policy.
      type: str
    enabled:
      description: Whether the policy caps anything.
      type: bool
    max_total_ops_per_sec:
      description: Cap on operations per second, or none.
      type: int
    max_total_bytes_per_sec:
      description: Cap on bytes per second, or none.
      type: int
    file_systems:
      description: Sorted file systems the run assigned or confirmed.
      type: list
      elements: str
    buckets:
      description: Sorted buckets the run assigned or confirmed.
      type: list
      elements: str
  sample:
    name: eda-tarpit
    enabled: true
    max_total_ops_per_sec: 100
    max_total_bytes_per_sec: null
    file_systems: [scratch-01]
    buckets: []
"""

import warnings

HAS_PURESTORAGE = True
try:
    import pypureclient
    from pypureclient import flashblade
except ImportError:
    HAS_PURESTORAGE = False

HAS_REQUESTS = True
try:
    import requests
except ImportError:
    HAS_REQUESTS = False

from ansible.module_utils.basic import AnsibleModule

HAS_EVERPURE = True
try:
    from ansible_collections.everpure.flashblade.plugins.module_utils.purefb import (
        get_system,
        purefb_argument_spec,
    )
except ImportError:
    HAS_EVERPURE = False

    def purefb_argument_spec():
        """The connection options, so that the module still parses its
        arguments and then says that the collection is missing."""
        return dict(
            fb_url=dict(type="str"),
            api_token=dict(type="str", no_log=True),
            disable_warnings=dict(type="bool", default=False),
        )


# the SDK release whose negotiated client carries every model used here
SDK_MIN = "1.98.0"
EVERPURE_MISSING = (
    "the everpure.flashblade collection is required: "
    "ansible-galaxy collection install everpure.flashblade"
)
REQUESTS_MISSING = (
    "the requests library is required for this module: pip install requests"
)
MIN_OPS = 100
LIMITS = ("max_total_ops_per_sec", "max_total_bytes_per_sec")
# the REST paths of the two kinds of object a policy is assigned to
PATHS = {"file_system": "file-systems", "bucket": "buckets"}
# the two forms a clear is tried in; the second is a fallback
CLEAR_BODIES = ({"qos_policy": {"name": ""}}, {"qos_policy": None})


def error_message(res):
    """The array's message for a failed call, or the status code when the
    response carries none."""
    errors = getattr(res, "errors", None) or []
    message = getattr(errors[0], "message", None) if errors else None
    return message or "status {0}".format(getattr(res, "status_code", "unknown"))


def model(module, name):
    """A pypureclient.flashblade model class. The attributes are lazy, so an
    SDK that predates a model raises ImportError here, not at import time."""
    try:
        return getattr(flashblade, name)
    except (ImportError, AttributeError):
        module.fail_json(
            msg="py-pure-client {0} has no {1} model; this module needs "
            "py-pure-client {2} or later".format(
                getattr(pypureclient, "__version__", "unknown"), name, SDK_MIN
            )
        )


def rest_error(status, data):
    """The array's message for a failed request, or the status when the body
    carries none."""
    errors = data.get("errors") if isinstance(data, dict) else None
    message = errors[0].get("message") if errors else None
    return message or "status {0}".format(status)


# the first REST version with the endpoints this module uses
MIN_VERSION = (2, 17)


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

    def negotiate(self):
        """Settle the REST version when none was given: the newest the array
        serves, MIN_VERSION at least (GET /api/api_version needs no token)."""
        if self.version:
            return
        versions = []
        try:
            res = self.session.request(
                "GET", self.base + "/api/api_version", timeout=self.TIMEOUT
            )
            data = res.json()
            if isinstance(data, dict):
                versions = data.get("versions") or []
        except (requests.exceptions.RequestException, ValueError, AttributeError):
            versions = []
        parsed = []
        for text in versions:
            parts = str(text).split(".")
            if len(parts) == 2 and all(p.isdigit() for p in parts):
                parsed.append((int(parts[0]), int(parts[1])))
        parsed.sort()
        if not parsed or parsed[-1] < MIN_VERSION:
            self.module.fail_json(
                msg="{0} serves REST {1}; this module needs {2}.{3} or newer".format(
                    self.base,
                    versions[-1] if versions else "nothing",
                    MIN_VERSION[0],
                    MIN_VERSION[1],
                )
            )
        self.version = "{0}.{1}".format(parsed[-1][0], parsed[-1][1])

    def login(self):
        self.negotiate()
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
        parsed body, {} when there is none. Logs in on the first call, so a
        run that never needs REST never opens a session."""
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


def get_policy(blade, name):
    """The existing policy, or None. The array answers a missing name with
    an error, which the collection's modules read as absent."""
    res = blade.get_qos_policies(names=[name])
    if res.status_code != 200:
        return None
    items = list(res.items)
    return items[0] if items else None


def policy_values(policy):
    return {
        "enabled": getattr(policy, "enabled", None),
        "max_total_ops_per_sec": getattr(policy, "max_total_ops_per_sec", None),
        "max_total_bytes_per_sec": getattr(policy, "max_total_bytes_per_sec", None),
    }


def create_policy(module, blade, name, values):
    body = model(module, "QosPolicy")(**values)
    res = blade.post_qos_policies(names=[name], policy=body)
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to create QoS policy {0}. Error: {1}".format(
                name, error_message(res)
            )
        )


def update_policy(module, blade, name, changes):
    body = model(module, "QosPolicy")(**changes)
    res = blade.patch_qos_policies(names=[name], policy=body)
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to update QoS policy {0}. Error: {1}".format(
                name, error_message(res)
            )
        )


def delete_policy(module, blade, name):
    res = blade.delete_qos_policies(names=[name])
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to delete QoS policy {0}. Error: {1}".format(
                name, error_message(res)
            )
        )


def carried_policy(item):
    return getattr(getattr(item, "qos_policy", None), "name", None)


def read_objects(module, blade, kind, names=None):
    """File systems or buckets as (name, policy carried); names None lists
    them all, for the sweep before a delete. Destroyed objects are left out:
    the array refuses to change them."""
    getter = getattr(blade, "get_" + PATHS[kind].replace("-", "_"))
    res = getter(names=names) if names else getter()
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to read {0} {1}. Error: {2}".format(
                PATHS[kind], ", ".join(names or ["(all)"]), error_message(res)
            )
        )
    return [
        (item.name, carried_policy(item))
        for item in res.items
        if not getattr(item, "destroyed", False)
    ]


def assign(module, rest, kind, obj, name):
    status, data = rest.call(
        "PATCH", PATHS[kind], params={"names": obj}, body={"qos_policy": {"name": name}}
    )
    if status != 200:
        module.fail_json(
            msg="Failed to assign QoS policy {0} to {1} {2}. Error: {3}".format(
                name, kind.replace("_", " "), obj, rest_error(status, data)
            )
        )


def clear(module, rest, kind, obj):
    """Clear the policy from one object, trying the two body forms in turn
    and saying which one the array took when it was not the first."""
    refusals = []
    for body in CLEAR_BODIES:
        status, data = rest.call("PATCH", PATHS[kind], params={"names": obj}, body=body)
        if status == 200:
            if refusals:
                module.warn(
                    "Cleared the QoS policy from {0} {1} with {2!r}; the array "
                    "refused the empty-name form: {3}".format(
                        kind.replace("_", " "), obj, body, refusals[0]
                    )
                )
            return
        refusals.append(rest_error(status, data))
    module.fail_json(
        msg="Failed to clear the QoS policy from {0} {1}. Errors: {2}".format(
            kind.replace("_", " "), obj, "; ".join(refusals)
        )
    )


def facts(name, values, file_systems, buckets):
    return dict(
        name=name, file_systems=sorted(file_systems), buckets=sorted(buckets), **values
    )


def run(module, blade, rest):
    name = module.params["name"]
    state = module.params["state"]
    listed = {
        kind: sorted(set(module.params[kind + "s"] or []))
        for kind in ("file_system", "bucket")
    }

    policy = get_policy(blade, name)

    if state == "absent":
        if policy is None:
            module.exit_json(changed=False, policy={})
        carriers = []
        for kind in PATHS:
            swept = read_objects(module, blade, kind)
            carriers.extend((kind, obj) for obj, carried in swept if carried == name)
        if not module.check_mode:
            for kind, obj in carriers:
                clear(module, rest, kind, obj)
            delete_policy(module, blade, name)
        module.exit_json(changed=True, policy={})

    given = {
        limit: module.params[limit]
        for limit in LIMITS
        if module.params[limit] is not None
    }
    changed = False
    if policy is None:
        if not given:
            module.fail_json(
                msg="Creating QoS policy {0} needs max_total_ops_per_sec or "
                "max_total_bytes_per_sec".format(name)
            )
        values = dict(
            {limit: None for limit in LIMITS}, enabled=module.params["enabled"], **given
        )
        changed = True
        if not module.check_mode:
            create_policy(module, blade, name, dict(given, enabled=values["enabled"]))
    else:
        current = policy_values(policy)
        changes = {k: v for k, v in given.items() if current[k] != v}
        if current["enabled"] != module.params["enabled"]:
            changes["enabled"] = module.params["enabled"]
        values = dict(current, **changes)
        if changes:
            changed = True
            if not module.check_mode:
                update_policy(module, blade, name, changes)

    for kind, names in listed.items():
        if not names:
            continue
        for obj, carried in read_objects(module, blade, kind, names):
            if carried == name:
                continue
            changed = True
            if not module.check_mode:
                assign(module, rest, kind, obj, name)

    module.exit_json(
        changed=changed,
        policy=facts(name, values, listed["file_system"], listed["bucket"]),
    )


def main():
    argument_spec = purefb_argument_spec()
    argument_spec.update(
        dict(
            name=dict(type="str", required=True),
            max_total_ops_per_sec=dict(type="int"),
            max_total_bytes_per_sec=dict(type="int"),
            enabled=dict(type="bool", default=True),
            file_systems=dict(type="list", elements="str"),
            buckets=dict(type="list", elements="str"),
            api_version=dict(type="str"),
            state=dict(type="str", default="present", choices=["present", "absent"]),
        )
    )
    module = AnsibleModule(argument_spec, supports_check_mode=True)
    if not HAS_EVERPURE:
        module.fail_json(msg=EVERPURE_MISSING)
    if not HAS_PURESTORAGE:
        module.fail_json(
            msg="py-pure-client {0} or later is required for this module".format(
                SDK_MIN
            )
        )
    if not HAS_REQUESTS:
        module.fail_json(msg=REQUESTS_MISSING)
    ops = module.params["max_total_ops_per_sec"]
    if ops is not None and ops < MIN_OPS:
        module.fail_json(
            msg="max_total_ops_per_sec must be {0} or more; got {1}".format(
                MIN_OPS, ops
            )
        )

    blade = get_system(module)
    rest = Rest(module)
    try:
        run(module, blade, rest)
    finally:
        rest.logout()


if __name__ == "__main__":
    main()
