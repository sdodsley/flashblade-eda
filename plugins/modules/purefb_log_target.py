#!/usr/bin/python
# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Manage the object store audit log targets of an Everpure FlashBlade."""

DOCUMENTATION = r"""
---
module: purefb_log_target
version_added: "0.1.0"
short_description: Manage FlashBlade object store audit log targets
description:
  - Create, update and delete object store log targets, the destinations an
    object store audit policy writes its log objects to. A log target names
    the bucket that receives the logs, the key prefix of the log objects and
    how often a new log object is started.
  - Needs Purity//FB 4.6.3 (REST 2.20) or later and py-pure-client 1.98.0 or
    later.
author:
  - Simon Dodsley (@sdodsley)
options:
  name:
    description:
      - Name of the log target.
    type: str
    required: true
  bucket:
    description:
      - Bucket that receives the log objects. Required when I(state) is
        C(present).
    type: str
  log_name_prefix:
    description:
      - Key prefix of the log objects in the bucket. The array's own default
        is C(audit-logs).
    type: str
    default: audit-logs
  log_rotate:
    description:
      - How often a new log object is started, as a count with the unit C(s),
        C(m) or C(h) (C(5m), C(24h)) or as a plain count of milliseconds. The
        array accepts 5 minutes to 24 hours; its own default is C(15m).
    type: str
    default: 15m
  state:
    description:
      - Create or update the log target, or delete it. The array refuses to
        delete a log target an audit policy still writes to.
    type: str
    default: present
    choices: [ present, absent ]
extends_documentation_fragment:
  - everpure.flashblade.everpure.fb
"""

EXAMPLES = r"""
- name: Create the log target the audit policy writes to
  sdodsley.flashblade_eda.purefb_log_target:
    name: eda-audit
    bucket: eda-log
    log_name_prefix: audit_
    log_rotate: 5m
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
    disable_warnings: true

- name: Rotate the log objects every hour
  sdodsley.flashblade_eda.purefb_log_target:
    name: eda-audit
    bucket: eda-log
    log_name_prefix: audit_
    log_rotate: 1h
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"

- name: Delete the log target
  sdodsley.flashblade_eda.purefb_log_target:
    name: eda-audit
    state: absent
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
"""

RETURN = r"""
log_target:
  description: The log target after the run; empty when it is absent.
  returned: always
  type: dict
  contains:
    name:
      description: Name of the log target.
      type: str
    bucket:
      description: Bucket that receives the log objects.
      type: str
    log_name_prefix:
      description: Key prefix of the log objects.
      type: str
    log_rotate:
      description: Rotation interval in milliseconds.
      type: int
  sample:
    name: eda-audit
    bucket: eda-log
    log_name_prefix: audit_
    log_rotate: 300000
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
ROTATE_UNITS_MS = {"s": 1000, "m": 60000, "h": 3600000}
# the range the array accepts for log_rotate
ROTATE_MIN_MS = 300000
ROTATE_MAX_MS = 86400000


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


def rotate_ms(text):
    """log_rotate as milliseconds: '<n>s', '<n>m', '<n>h' or a bare count of
    milliseconds; None when the text is neither."""
    text = str(text).strip().lower()
    if text.isdigit():
        return int(text)
    if text[-1:] in ROTATE_UNITS_MS and text[:-1].isdigit():
        return int(text[:-1]) * ROTATE_UNITS_MS[text[-1]]
    return None


def get_target(blade, name):
    """The existing log target, or None. The array answers a missing name
    with an error, which the collection's modules read as absent."""
    res = blade.get_log_targets_object_store(names=[name])
    if res.status_code != 200:
        return None
    items = list(res.items)
    return items[0] if items else None


def current_values(target):
    """Bucket, prefix and rotation of an existing log target as plain values."""
    return {
        "bucket": getattr(getattr(target, "bucket", None), "name", None),
        "log_name_prefix": getattr(
            getattr(target, "log_name_prefix", None), "prefix", None
        ),
        "log_rotate": getattr(getattr(target, "log_rotate", None), "duration", None),
    }


def fields(module, values):
    """The model objects for the bucket, prefix and rotation values given."""
    out = {}
    if "bucket" in values:
        out["bucket"] = model(module, "Reference")(name=values["bucket"])
    if "log_name_prefix" in values:
        out["log_name_prefix"] = model(module, "AuditLogNamePrefix")(
            prefix=values["log_name_prefix"]
        )
    if "log_rotate" in values:
        out["log_rotate"] = model(module, "AuditLogRotate")(
            duration=values["log_rotate"]
        )
    return out


def create_target(module, blade, name, wanted):
    body = model(module, "LogTargetsObjectStorePost")(**fields(module, wanted))
    res = blade.post_log_targets_object_store(
        names=[name], log_target_object_store=body
    )
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to create log target {0}. Error: {1}".format(
                name, error_message(res)
            )
        )


def update_target(module, blade, name, changes):
    """PATCH only what differs. A changed bucket is sent too; whether a log
    target may move to another bucket is the array's decision."""
    body = model(module, "LogTargetsObjectStore")(**fields(module, changes))
    res = blade.patch_log_targets_object_store(
        names=[name], log_target_object_store=body
    )
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to update log target {0}. Error: {1}".format(
                name, error_message(res)
            )
        )


def delete_target(module, blade, name):
    res = blade.delete_log_targets_object_store(names=[name])
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to delete log target {0}. Error: {1}".format(
                name, error_message(res)
            )
        )


def main():
    argument_spec = purefb_argument_spec()
    argument_spec.update(
        dict(
            name=dict(type="str", required=True),
            bucket=dict(type="str"),
            log_name_prefix=dict(type="str", default="audit-logs"),
            log_rotate=dict(type="str", default="15m"),
            state=dict(type="str", default="present", choices=["present", "absent"]),
        )
    )
    module = AnsibleModule(
        argument_spec,
        required_if=[["state", "present", ["bucket"]]],
        supports_check_mode=True,
    )
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
    rotate = rotate_ms(module.params["log_rotate"])
    if rotate is None:
        module.fail_json(
            msg="log_rotate must be a count with the unit s, m or h, or a count "
            "of milliseconds; got {0!r}".format(module.params["log_rotate"])
        )
    if not ROTATE_MIN_MS <= rotate <= ROTATE_MAX_MS:
        module.fail_json(
            msg="log_rotate must be between 5m ({0} ms) and 24h ({1} ms); "
            "got {2} ms".format(ROTATE_MIN_MS, ROTATE_MAX_MS, rotate)
        )
    wanted = {
        "bucket": module.params["bucket"],
        "log_name_prefix": module.params["log_name_prefix"],
        "log_rotate": rotate,
    }

    blade = get_system(module)
    target = get_target(blade, name)

    if state == "absent":
        if target is None:
            module.exit_json(changed=False, log_target={})
        if not module.check_mode:
            delete_target(module, blade, name)
        module.exit_json(changed=True, log_target={})

    if target is None:
        if not module.check_mode:
            create_target(module, blade, name, wanted)
        module.exit_json(changed=True, log_target=dict(name=name, **wanted))

    current = current_values(target)
    changes = {key: value for key, value in wanted.items() if current[key] != value}
    if not changes:
        module.exit_json(changed=False, log_target=dict(name=name, **current))
    if not module.check_mode:
        update_target(module, blade, name, changes)
    module.exit_json(changed=True, log_target=dict(name=name, **wanted))


if __name__ == "__main__":
    main()
