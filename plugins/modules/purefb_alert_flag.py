#!/usr/bin/python
# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Flag or unflag an alert on an Everpure FlashBlade."""

DOCUMENTATION = r"""
---
module: purefb_alert_flag
version_added: "0.1.0"
short_description: Flag or unflag a FlashBlade alert
description:
  - Set the flag on one alert of the array. The flag is the array's own
    "somebody is looking at this" mark; the automation sets it when it has
    acted on an alert and clears it when the alert is handed back.
  - Works over REST (C(PATCH /alerts), at the newest version the array
    serves), which the collection has no module for. Needs the C(requests)
    library.
author:
  - Simon Dodsley (@sdodsley)
options:
  alert:
    description:
      - Name of the alert, the number the array gives each alert instance
        (not the alert code).
    type: str
    required: true
  flagged:
    description:
      - Whether the alert carries the flag.
    type: bool
    required: true
  api_version:
    description:
      - REST version the requests ask for. By default the newest version
        the array serves, 2.16 at least.
    type: str
extends_documentation_fragment:
  - everpure.flashblade.everpure.fb
"""

EXAMPLES = r"""
- name: Flag the alert the rulebook has acted on
  sdodsley.flashblade_eda.purefb_alert_flag:
    alert: "1042"
    flagged: true
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
    disable_warnings: true

- name: Hand the alert back
  sdodsley.flashblade_eda.purefb_alert_flag:
    alert: "1042"
    flagged: false
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
"""

RETURN = r"""
alert:
  description: The alert after the run.
  returned: always
  type: dict
  contains:
    name:
      description: Name of the alert.
      type: str
    code:
      description: The alert code, which names the condition.
      type: int
    flagged:
      description: Whether the alert carries the flag.
      type: bool
  sample:
    name: "1042"
    code: 1016
    flagged: true
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


# the first REST version with the endpoints this module uses
MIN_VERSION = (2, 16)


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


def get_alert(module, rest, name):
    status, data = rest.call("GET", "alerts", params={"names": name})
    items = (data.get("items") or []) if status == 200 else []
    if not items:
        module.fail_json(
            msg="Alert {0} not found. Error: {1}".format(
                name, rest_error(status, data) if status != 200 else "no such alert"
            )
        )
    return items[0]


def set_flag(module, rest, name, flagged):
    status, data = rest.call(
        "PATCH", "alerts", params={"names": name}, body={"flagged": flagged}
    )
    if status != 200:
        module.fail_json(
            msg="Failed to {0} alert {1}. Error: {2}".format(
                "flag" if flagged else "unflag", name, rest_error(status, data)
            )
        )


def facts(alert, flagged):
    return {"name": alert.get("name"), "code": alert.get("code"), "flagged": flagged}


def run(module, rest):
    name = module.params["alert"]
    flagged = module.params["flagged"]
    alert = get_alert(module, rest, name)
    if bool(alert.get("flagged")) == flagged:
        module.exit_json(changed=False, alert=facts(alert, flagged))
    if not module.check_mode:
        set_flag(module, rest, name, flagged)
    module.exit_json(changed=True, alert=facts(alert, flagged))


def main():
    argument_spec = purefb_argument_spec()
    argument_spec.update(
        dict(
            alert=dict(type="str", required=True),
            flagged=dict(type="bool", required=True),
            api_version=dict(type="str"),
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
