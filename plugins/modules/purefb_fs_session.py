#!/usr/bin/python
# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Remove file sessions and locks from an Everpure FlashBlade."""

DOCUMENTATION = r"""
---
module: purefb_fs_session
version_added: "0.1.0"
short_description: Remove FlashBlade file sessions and locks
description:
  - List the array's NFS and SMB sessions and file locks, keep those that
    match the client, user, file system, path or inode given, and remove
    them. This is an action, not a state; a run that finds nothing to
    remove changes nothing, and a run in check mode reports what it would
    remove.
  - Sessions are matched only when I(client) or I(user) is given; locks
    only when I(client), I(file_system), I(path) or I(inode) is. A kind
    without a matching filter is skipped, so a file system name alone never
    drops sessions.
  - The session and lock listings are read for the fields the Purity//FB
    REST reference documents, and a field the array leaves out is read as
    unknown rather than failing the run.
author:
  - Simon Dodsley (@sdodsley)
options:
  kind:
    description:
      - What to remove.
    type: str
    default: sessions
    choices: [ sessions, locks, both ]
  client:
    description:
      - Address of the client whose sessions or locks go.
    type: str
  user:
    description:
      - Name of the user whose sessions go.
    type: str
  protocols:
    description:
      - Only sessions of these protocols (C(nfs), C(smb)).
    type: list
    elements: str
  disruptive:
    description:
      - Remove sessions that hold open files as well.
    type: bool
    default: false
  file_system:
    description:
      - Only locks on this file system.
    type: str
  path:
    description:
      - Only locks on this path within the file system.
    type: str
  recursive:
    description:
      - With I(path), locks under the path as well as on it.
    type: bool
    default: false
  inode:
    description:
      - Only the locks on this inode.
    type: int
extends_documentation_fragment:
  - everpure.flashblade.everpure.fb
"""

EXAMPLES = r"""
- name: Drop every session of a client that holds the array
  sdodsley.flashblade_eda.purefb_fs_session:
    client: 192.0.2.10
    disruptive: true
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
    disable_warnings: true

- name: Release the locks a client left on one directory
  sdodsley.flashblade_eda.purefb_fs_session:
    kind: locks
    client: 192.0.2.10
    file_system: eda-demo-quota
    path: /jobs/2026-11
    recursive: true
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"

- name: Everything a client holds, sessions and locks
  sdodsley.flashblade_eda.purefb_fs_session:
    kind: both
    client: 192.0.2.10
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
"""

RETURN = r"""
removed:
  description: What the run removed, or would remove in check mode.
  returned: always
  type: dict
  contains:
    sessions:
      description: Number of sessions removed.
      type: int
    locks:
      description: Number of locks removed.
      type: int
    items:
      description: Names of the sessions and locks removed, as the array
        lists them.
      type: list
      elements: str
  sample:
    sessions: 2
    locks: 1
    items: ["192.0.2.10:nfs:1", "192.0.2.10:nfs:2", "lock-77"]
"""

# no SDK model is built here, so the SDK import and its floor are left to the
# collection's get_system, which fails with its own message without it
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


EVERPURE_MISSING = (
    "the everpure.flashblade collection is required: "
    "ansible-galaxy collection install everpure.flashblade"
)
KINDS = ["sessions", "locks", "both"]


def error_message(res):
    """The array's message for a failed call, or the status code when the
    response carries none."""
    errors = getattr(res, "errors", None) or []
    message = getattr(errors[0], "message", None) if errors else None
    return message or "status {0}".format(getattr(res, "status_code", "unknown"))


def as_dict(item):
    """An SDK item as a plain dict, so that fields the array may leave out
    read with .get instead of raising."""
    if isinstance(item, dict):
        return item
    to_dict = getattr(item, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return dict(vars(item))


def ref_name(value):
    """A name out of a reference, whether it reads as a dict, an object or
    is already the plain string."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("name")
    return getattr(value, "name", None)


def list_items(module, blade, what, **filters):
    """Sessions or locks as dicts."""
    getter = getattr(blade, "get_file_systems_" + what)
    res = getter(**filters)
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to list the file system {0}. Error: {1}".format(
                what, error_message(res)
            )
        )
    return [as_dict(item) for item in res.items]


def session_matches(session, client, user):
    if client is not None and ref_name(session.get("client")) != client:
        return False
    if user is not None and ref_name(session.get("user")) != user:
        return False
    return True


def path_matches(lock_path, path, recursive):
    if lock_path is None:
        return False
    if lock_path == path:
        return True
    return recursive and lock_path.startswith(path.rstrip("/") + "/")


def lock_matches(lock, client, file_system, path, recursive, inode):
    if client is not None and ref_name(lock.get("client")) != client:
        return False
    if file_system is not None and ref_name(lock.get("file_system")) != file_system:
        return False
    if path is not None and not path_matches(lock.get("path"), path, recursive):
        return False
    if inode is not None and lock.get("inode") != inode:
        return False
    return True


def remove(module, blade, what, names, **extra):
    deleter = getattr(blade, "delete_file_systems_" + what)
    res = deleter(names=names, **extra)
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to remove {0} {1}. Error: {2}".format(
                what, ", ".join(names), error_message(res)
            )
        )


def main():
    argument_spec = purefb_argument_spec()
    argument_spec.update(
        dict(
            kind=dict(type="str", default="sessions", choices=KINDS),
            client=dict(type="str"),
            user=dict(type="str"),
            protocols=dict(type="list", elements="str"),
            disruptive=dict(type="bool", default=False),
            file_system=dict(type="str"),
            path=dict(type="str"),
            recursive=dict(type="bool", default=False),
            inode=dict(type="int"),
        )
    )
    module = AnsibleModule(
        argument_spec,
        required_one_of=[["client", "user", "file_system"]],
        supports_check_mode=True,
    )
    if not HAS_EVERPURE:
        module.fail_json(msg=EVERPURE_MISSING)

    kind = module.params["kind"]
    client = module.params["client"]
    user = module.params["user"]
    file_system = module.params["file_system"]
    path = module.params["path"]
    inode = module.params["inode"]

    blade = get_system(module)
    removed = {"sessions": 0, "locks": 0, "items": []}

    if kind in ("sessions", "both") and (client is not None or user is not None):
        filters = {}
        if module.params["protocols"]:
            filters["protocols"] = module.params["protocols"]
        sessions = list_items(module, blade, "sessions", **filters)
        matched = [s for s in sessions if session_matches(s, client, user)]
        names = [s.get("name") for s in matched if s.get("name")]
        if names and not module.check_mode:
            remove(
                module, blade, "sessions", names, disruptive=module.params["disruptive"]
            )
        removed["sessions"] = len(names)
        removed["items"].extend(names)

    lock_filter = any(v is not None for v in (client, file_system, path, inode))
    if kind in ("locks", "both") and lock_filter:
        locks = list_items(module, blade, "locks")
        matched = [
            lock
            for lock in locks
            if lock_matches(
                lock, client, file_system, path, module.params["recursive"], inode
            )
        ]
        names = [lock.get("name") for lock in matched if lock.get("name")]
        if names and not module.check_mode:
            remove(module, blade, "locks", names)
        removed["locks"] = len(names)
        removed["items"].extend(names)

    module.exit_json(changed=bool(removed["items"]), removed=removed)


if __name__ == "__main__":
    main()
