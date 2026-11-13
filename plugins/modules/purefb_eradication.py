#!/usr/bin/python
# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Manage the eradication settings of an Everpure FlashBlade."""

DOCUMENTATION = r"""
---
module: purefb_eradication
version_added: "0.1.0"
short_description: Manage the FlashBlade eradication settings
description:
  - Set how long a destroyed file system, bucket or snapshot waits before
    the array eradicates it, and whether a user may eradicate by hand
    before then. Both settings protect data, so the module is a ratchet.
    It lengthens the delay and disables manual eradication freely, and
    refuses to shorten the delay or re-enable manual eradication unless
    I(force) is set.
  - The array may itself refuse a change once its own ratchet is engaged.
author:
  - Simon Dodsley (@sdodsley)
options:
  eradication_delay_days:
    description:
      - Days a destroyed object waits before the array eradicates it, 1 to
        30.
    type: int
  manual_eradication:
    description:
      - Whether users may eradicate destroyed objects before the delay
        runs out. The array also reports C(partially-enabled), which the
        module treats as enabled when deciding whether a change needs
        I(force).
    type: str
    choices: [ all-enabled, all-disabled ]
  force:
    description:
      - Allow shortening the delay or re-enabling manual eradication.
    type: bool
    default: false
extends_documentation_fragment:
  - everpure.flashblade.everpure.fb
"""

EXAMPLES = r"""
- name: Keep destroyed objects a week and take the manual shortcut away
  sdodsley.flashblade_eda.purefb_eradication:
    eradication_delay_days: 7
    manual_eradication: all-disabled
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
    disable_warnings: true

- name: Back to one day, which the ratchet only allows with force
  sdodsley.flashblade_eda.purefb_eradication:
    eradication_delay_days: 1
    force: true
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
"""

RETURN = r"""
eradication_config:
  description: The eradication settings after the run.
  returned: always
  type: dict
  contains:
    eradication_delay_days:
      description: Days a destroyed object waits; fractional when the array
        holds a delay that is not whole days.
      type: float
    eradication_delay_ms:
      description: The same delay in milliseconds, as the array stores it.
      type: int
    manual_eradication:
      description: C(all-enabled), C(all-disabled) or C(partially-enabled).
      type: str
  sample:
    eradication_delay_days: 7
    eradication_delay_ms: 604800000
    manual_eradication: all-disabled
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
DAY_MS = 86400000
MIN_DAYS = 1
MAX_DAYS = 30
MANUAL_CHOICES = ["all-enabled", "all-disabled"]


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


def get_config(module, blade):
    """The array's eradication settings as plain values."""
    res = blade.get_arrays()
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to read the array. Error: {0}".format(error_message(res))
        )
    items = list(res.items)
    config = getattr(items[0], "eradication_config", None) if items else None
    return {
        "eradication_delay": getattr(config, "eradication_delay", None),
        "manual_eradication": getattr(config, "manual_eradication", None),
    }


def patch_config(module, blade, changes):
    body = model(module, "Array")(
        eradication_config=model(module, "ArrayEradicationConfig")(**changes)
    )
    res = blade.patch_arrays(array=body)
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to change the eradication settings. Error: {0}".format(
                error_message(res)
            )
        )


def days(ms):
    if ms is None:
        return None
    return ms // DAY_MS if ms % DAY_MS == 0 else ms / DAY_MS


def facts(config):
    return {
        "eradication_delay_days": days(config["eradication_delay"]),
        "eradication_delay_ms": config["eradication_delay"],
        "manual_eradication": config["manual_eradication"],
    }


def main():
    argument_spec = purefb_argument_spec()
    argument_spec.update(
        dict(
            eradication_delay_days=dict(type="int"),
            manual_eradication=dict(type="str", choices=MANUAL_CHOICES),
            force=dict(type="bool", default=False),
        )
    )
    module = AnsibleModule(
        argument_spec,
        required_one_of=[["eradication_delay_days", "manual_eradication"]],
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

    wanted_days = module.params["eradication_delay_days"]
    manual = module.params["manual_eradication"]
    force = module.params["force"]
    if wanted_days is not None and not MIN_DAYS <= wanted_days <= MAX_DAYS:
        module.fail_json(
            msg="eradication_delay_days must be between {0} and {1}; got {2}".format(
                MIN_DAYS, MAX_DAYS, wanted_days
            )
        )

    blade = get_system(module)
    current = get_config(module, blade)
    changes = {}
    if wanted_days is not None:
        wanted_ms = wanted_days * DAY_MS
        current_ms = current["eradication_delay"]
        if wanted_ms != current_ms:
            if current_ms is not None and wanted_ms < current_ms and not force:
                module.fail_json(
                    msg="Shortening the eradication delay from {0} to {1} days "
                    "needs force: true".format(days(current_ms), wanted_days)
                )
            changes["eradication_delay"] = wanted_ms
    if manual is not None and manual != current["manual_eradication"]:
        if manual == "all-enabled" and not force:
            module.fail_json(
                msg="Re-enabling manual eradication (now {0}) needs force: "
                "true".format(current["manual_eradication"])
            )
        changes["manual_eradication"] = manual

    if not changes:
        module.exit_json(changed=False, eradication_config=facts(current))
    if not module.check_mode:
        patch_config(module, blade, changes)
    module.exit_json(changed=True, eradication_config=facts(dict(current, **changes)))


if __name__ == "__main__":
    main()
