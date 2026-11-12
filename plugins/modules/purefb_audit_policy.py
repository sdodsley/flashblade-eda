#!/usr/bin/python
# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Manage the object store audit policies of an Everpure FlashBlade."""

DOCUMENTATION = r"""
---
module: purefb_audit_policy
version_added: "0.1.0"
short_description: Manage FlashBlade object store audit policies
description:
  - Create, update and delete object store audit policies and the buckets
    that are their members. An enabled policy writes one record per S3
    request on its member buckets, as far as each bucket's audit filters
    allow, to its log targets.
  - Needs Purity//FB 4.6.3 (REST 2.20) or later and py-pure-client 1.98.0 or
    later.
author:
  - Simon Dodsley (@sdodsley)
options:
  name:
    description:
      - Name of the audit policy.
    type: str
    required: true
  enabled:
    description:
      - Whether the policy records anything.
    type: bool
    default: true
  log_targets:
    description:
      - Names of the object store log targets the policy writes to. When
        given, the policy's list is made exactly this list; when omitted, it
        is left as it is.
    type: list
    elements: str
  buckets:
    description:
      - Buckets that are members of the policy. When given, membership is
        made exactly this set, missing buckets added and other members
        removed; when omitted, membership is left as it is.
    type: list
    elements: str
  state:
    description:
      - Create or update the policy, or delete it. Deleting removes the
        members first.
    type: str
    default: present
    choices: [ present, absent ]
extends_documentation_fragment:
  - everpure.flashblade.everpure.fb
"""

EXAMPLES = r"""
- name: Audit two buckets through the eda-audit log target
  sdodsley.flashblade_eda.purefb_audit_policy:
    name: eda-audit
    log_targets:
      - eda-audit
    buckets:
      - eda-data
      - eda-log
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
    disable_warnings: true

- name: Pause the policy without touching its members
  sdodsley.flashblade_eda.purefb_audit_policy:
    name: eda-audit
    enabled: false
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"

- name: Delete the policy and its memberships
  sdodsley.flashblade_eda.purefb_audit_policy:
    name: eda-audit
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
      description: Whether the policy records anything.
      type: bool
    log_targets:
      description: Sorted names of the log targets the policy writes to.
      type: list
      elements: str
    buckets:
      description: Sorted names of the member buckets.
      type: list
      elements: str
  sample:
    name: eda-audit
    enabled: true
    log_targets: [eda-audit]
    buckets: [eda-data, eda-log]
"""

HAS_PURESTORAGE = True
try:
    import pypureclient
    from pypureclient import flashblade
except ImportError:
    HAS_PURESTORAGE = False

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
# the resource_type a policy's log_targets reference carries
LOG_TARGET_TYPE = "log-targets/object-store"


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


def get_policy(blade, name):
    """The existing policy, or None. The array answers a missing name with
    an error, which the collection's modules read as absent."""
    res = blade.get_audit_object_store_policies(names=[name])
    if res.status_code != 200:
        return None
    items = list(res.items)
    return items[0] if items else None


def get_members(module, blade, name):
    """Sorted member bucket names of an existing policy."""
    res = blade.get_audit_object_store_policies_members(policy_names=[name])
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to read the members of audit policy {0}. Error: {1}".format(
                name, error_message(res)
            )
        )
    return sorted(item.member.name for item in res.items)


def target_names(policy):
    return sorted(item.name for item in getattr(policy, "log_targets", None) or [])


def references(module, names):
    writable = model(module, "ReferenceWritable")
    return [writable(name=name, resource_type=LOG_TARGET_TYPE) for name in names]


def set_members(module, blade, name, add, remove):
    """Add and remove member buckets, one call each; the array names the
    bucket it objects to."""
    if add:
        res = blade.post_audit_object_store_policies_members(
            policy_names=[name], member_names=add
        )
        if res.status_code != 200:
            module.fail_json(
                msg="Failed to add {0} to audit policy {1}. Error: {2}".format(
                    ", ".join(add), name, error_message(res)
                )
            )
    if remove:
        res = blade.delete_audit_object_store_policies_members(
            policy_names=[name], member_names=remove
        )
        if res.status_code != 200:
            module.fail_json(
                msg="Failed to remove {0} from audit policy {1}. Error: {2}".format(
                    ", ".join(remove), name, error_message(res)
                )
            )


def create_policy(module, blade, name, enabled, log_targets):
    """POST the policy; log_targets is None when the option was not given
    and the array then applies its own default."""
    values = {"enabled": enabled}
    if log_targets is not None:
        values["log_targets"] = references(module, log_targets)
    body = model(module, "AuditObjectStorePoliciesPost")(**values)
    res = blade.post_audit_object_store_policies(names=[name], policy=body)
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to create audit policy {0}. Error: {1}".format(
                name, error_message(res)
            )
        )


def update_policy(module, blade, name, changes):
    """PATCH only what differs; log_targets is the full list."""
    if "log_targets" in changes:
        changes["log_targets"] = references(module, changes["log_targets"])
    body = model(module, "AuditObjectStorePoliciesPatch")(**changes)
    res = blade.patch_audit_object_store_policies(names=[name], policy=body)
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to update audit policy {0}. Error: {1}".format(
                name, error_message(res)
            )
        )


def delete_policy(module, blade, name):
    res = blade.delete_audit_object_store_policies(names=[name])
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to delete audit policy {0}. Error: {1}".format(
                name, error_message(res)
            )
        )


def facts(name, enabled, log_targets, buckets):
    return {
        "name": name,
        "enabled": enabled,
        "log_targets": list(log_targets),
        "buckets": list(buckets),
    }


def main():
    argument_spec = purefb_argument_spec()
    argument_spec.update(
        dict(
            name=dict(type="str", required=True),
            enabled=dict(type="bool", default=True),
            log_targets=dict(type="list", elements="str"),
            buckets=dict(type="list", elements="str"),
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

    name = module.params["name"]
    state = module.params["state"]
    enabled = module.params["enabled"]
    log_targets = module.params["log_targets"]
    if log_targets is not None:
        log_targets = sorted(set(log_targets))
    buckets = module.params["buckets"]
    if buckets is not None:
        buckets = sorted(set(buckets))

    blade = get_system(module)
    policy = get_policy(blade, name)

    if state == "absent":
        if policy is None:
            module.exit_json(changed=False, policy={})
        members = get_members(module, blade, name)
        if not module.check_mode:
            set_members(module, blade, name, [], members)
            delete_policy(module, blade, name)
        module.exit_json(changed=True, policy={})

    if policy is None:
        if not module.check_mode:
            create_policy(module, blade, name, enabled, log_targets)
            set_members(module, blade, name, buckets or [], [])
        module.exit_json(
            changed=True,
            policy=facts(name, enabled, log_targets or [], buckets or []),
        )

    current_targets = target_names(policy)
    current_members = get_members(module, blade, name)
    changes = {}
    if getattr(policy, "enabled", None) != enabled:
        changes["enabled"] = enabled
    final_targets = current_targets
    if log_targets is not None and log_targets != current_targets:
        final_targets = log_targets
        changes["log_targets"] = log_targets
    add, remove = [], []
    final_members = current_members
    if buckets is not None:
        add = [bucket for bucket in buckets if bucket not in current_members]
        remove = [bucket for bucket in current_members if bucket not in buckets]
        final_members = buckets
    changed = bool(changes or add or remove)
    if changed and not module.check_mode:
        if changes:
            update_policy(module, blade, name, changes)
        set_members(module, blade, name, add, remove)
    module.exit_json(
        changed=changed, policy=facts(name, enabled, final_targets, final_members)
    )


if __name__ == "__main__":
    main()
