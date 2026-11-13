#!/usr/bin/python
# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Open or close the maintenance window of an Everpure FlashBlade."""

DOCUMENTATION = r"""
---
module: purefb_maintenance
version_added: "0.1.0"
short_description: Open or close the FlashBlade maintenance window
description:
  - Open the array's maintenance window for a number of hours, or close it.
    While the window is open the array holds back most alerts, so the
    rulebooks treat a planned change as planned. The array has one window,
    named C(array).
  - Needs Purity//FB 4.5.2 (REST 2.16) or later.
author:
  - Simon Dodsley (@sdodsley)
options:
  state:
    description:
      - Open the window or close it.
    type: str
    default: present
    choices: [ present, absent ]
  timeout:
    description:
      - How long the window stays open, in hours, 1 to 48.
    type: int
    default: 4
  extend:
    description:
      - With I(state) C(present) and a window already open, close it and
        open a new one of I(timeout) hours. Without it an open window is
        left as it is, whatever its remaining time. Every run with
        I(extend) and an open window is a change.
    type: bool
    default: false
extends_documentation_fragment:
  - everpure.flashblade.everpure.fb
"""

EXAMPLES = r"""
- name: Open a four hour window before a planned change
  sdodsley.flashblade_eda.purefb_maintenance:
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
    disable_warnings: true

- name: The change runs long; start a fresh eight hour window
  sdodsley.flashblade_eda.purefb_maintenance:
    timeout: 8
    extend: true
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"

- name: Close the window
  sdodsley.flashblade_eda.purefb_maintenance:
    state: absent
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
"""

RETURN = r"""
window:
  description: The open window after the run; empty when there is none.
  returned: always
  type: dict
  contains:
    name:
      description: Name of the window, always C(array).
      type: str
    created:
      description: When it was opened, in milliseconds since the epoch.
      type: int
    expires:
      description: When it closes on its own, in milliseconds since the epoch.
      type: int
  sample:
    name: array
    created: 1778750400000
    expires: 1778764800000
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
# the array's one window and the range it accepts
WINDOW = "array"
HOUR_MS = 3600000
MIN_HOURS = 1
MAX_HOURS = 48


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


def get_window(module, blade):
    """The open window, or None."""
    res = blade.get_maintenance_windows()
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to read the maintenance window. Error: {0}".format(
                error_message(res)
            )
        )
    items = list(res.items)
    return items[0] if items else None


def open_window(module, blade, hours):
    body = model(module, "MaintenanceWindowPost")(timeout=hours * HOUR_MS)
    res = blade.post_maintenance_windows(names=[WINDOW], maintenance_window=body)
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to open a {0} hour maintenance window. Error: {1}".format(
                hours, error_message(res)
            )
        )
    items = list(res.items)
    return items[0] if items else None


def close_window(module, blade):
    res = blade.delete_maintenance_windows(names=[WINDOW])
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to close the maintenance window. Error: {0}".format(
                error_message(res)
            )
        )


def facts(window):
    """The window as plain values; name only when the array sent nothing
    back, as in check mode."""
    return {
        "name": getattr(window, "name", None) or WINDOW,
        "created": getattr(window, "created", None),
        "expires": getattr(window, "expires", None),
    }


def main():
    argument_spec = purefb_argument_spec()
    argument_spec.update(
        dict(
            state=dict(type="str", default="present", choices=["present", "absent"]),
            timeout=dict(type="int", default=4),
            extend=dict(type="bool", default=False),
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

    state = module.params["state"]
    hours = module.params["timeout"]
    if not MIN_HOURS <= hours <= MAX_HOURS:
        module.fail_json(
            msg="timeout must be between {0} and {1} hours; got {2}".format(
                MIN_HOURS, MAX_HOURS, hours
            )
        )

    blade = get_system(module)
    window = get_window(module, blade)

    if state == "absent":
        if window is None:
            module.exit_json(changed=False, window={})
        if not module.check_mode:
            close_window(module, blade)
        module.exit_json(changed=True, window={})

    if window is not None and not module.params["extend"]:
        module.exit_json(changed=False, window=facts(window))
    opened = None
    if not module.check_mode:
        # the array extends a window by a fresh POST too, but closing first
        # makes the new expiry exactly timeout hours from now
        if window is not None:
            close_window(module, blade)
        opened = open_window(module, blade, hours)
    module.exit_json(changed=True, window=facts(opened))


if __name__ == "__main__":
    main()
