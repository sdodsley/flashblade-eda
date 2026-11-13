#!/usr/bin/python
# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Manage the legal holds of an Everpure FlashBlade and what they hold."""

DOCUMENTATION = r"""
---
module: purefb_legal_hold
version_added: "0.1.0"
short_description: Manage FlashBlade legal holds and their held entities
description:
  - Create and delete legal holds, and place a hold on, or release it from,
    paths of a file system. A held file cannot be changed or deleted until
    every hold on it is released.
  - Deleting a hold releases its held entities first, so the array does
    not refuse the delete for what the hold still covers.
  - The description is set when the hold is created; a different one on
    an existing hold is reported with a warning, not changed.
  - Everything goes through the SDK; a held entity is released with
    C(PATCH /legal-holds/held-entities) and C(released=true), which the
    array requires (a DELETE is refused with 405).
  - Needs Purity//FB 4.5.3 (REST 2.17) or later.
author:
  - Simon Dodsley (@sdodsley)
options:
  name:
    description:
      - Name of the legal hold.
    type: str
    required: true
  description:
    description:
      - What the hold is for, kept on the array.
    type: str
  state:
    description:
      - Create the hold, or release everything it holds and delete it.
    type: str
    default: present
    choices: [ present, absent ]
  file_system:
    description:
      - File system of the paths in I(paths). Given together with I(paths).
    type: str
  paths:
    description:
      - Paths within I(file_system) the hold covers (I(entities_state)
        C(present)) or stops covering (C(absent)).
    type: list
    elements: str
  recursive:
    description:
      - Hold everything under each path as well as the path.
    type: bool
    default: false
  entities_state:
    description:
      - Whether the paths are held or released.
    type: str
    default: present
    choices: [ present, absent ]
extends_documentation_fragment:
  - everpure.flashblade.everpure.fb
"""

EXAMPLES = r"""
- name: Hold a case directory until the matter closes
  sdodsley.flashblade_eda.purefb_legal_hold:
    name: case-4411
    description: Litigation hold, matter 4411
    file_system: eda-dr-demo
    paths:
      - /cases/4411
    recursive: true
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
    disable_warnings: true

- name: Release one file from the hold and keep the rest
  sdodsley.flashblade_eda.purefb_legal_hold:
    name: case-4411
    file_system: eda-dr-demo
    paths:
      - /cases/4411/draft.txt
    entities_state: absent
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"

- name: The matter closed; release everything and delete the hold
  sdodsley.flashblade_eda.purefb_legal_hold:
    name: case-4411
    state: absent
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
"""

RETURN = r"""
hold:
  description: The hold after the run; empty when it is absent.
  returned: always
  type: dict
  contains:
    name:
      description: Name of the hold.
      type: str
    description:
      description: What the hold is for.
      type: str
    held_entities:
      description: What the hold covers, sorted by file system and path.
      type: list
      elements: dict
      contains:
        file_system:
          description: File system of the path.
          type: str
        path:
          description: The held path.
          type: str
        status:
          description: The array's status of the entity; unknown in check
            mode for an entity not yet held.
          type: str
  sample:
    name: case-4411
    description: Litigation hold, matter 4411
    held_entities:
      - file_system: eda-dr-demo
        path: /cases/4411
        status: held
"""


import time

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


# a released entity leaves the listing within seconds; how long to wait before the delete
RELEASE_WAIT_TRIES = 15
RELEASE_WAIT_SECONDS = 2
# the SDK release whose negotiated client carries every model used here
SDK_MIN = "1.98.0"
EVERPURE_MISSING = (
    "the everpure.flashblade collection is required: "
    "ansible-galaxy collection install everpure.flashblade"
)


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


def get_hold(blade, name):
    """The existing hold, or None. The array answers a missing name with an
    error, which the collection's modules read as absent."""
    res = blade.get_legal_holds(names=[name])
    if res.status_code != 200:
        return None
    items = list(res.items)
    return items[0] if items else None


def held_entities(module, blade, name):
    """What the hold covers, as {file_system, path, status} sorted."""
    res = blade.get_legal_holds_held_entities(names=[name])
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to read the held entities of legal hold {0}. Error: {1}".format(
                name, error_message(res)
            )
        )
    rows = []
    for item in res.items:
        entity = as_dict(item)
        hold = ref_name(entity.get("legal_hold"))
        if hold is not None and hold != name:
            continue
        rows.append(
            {
                "file_system": ref_name(entity.get("file_system")),
                "path": entity.get("path"),
                "status": entity.get("status"),
            }
        )
    return sorted(rows, key=lambda row: (row["file_system"] or "", row["path"] or ""))


def create_hold(module, blade, name, description):
    values = {} if description is None else {"description": description}
    res = blade.post_legal_holds(
        names=[name], hold=model(module, "LegalHold")(**values)
    )
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to create legal hold {0}. Error: {1}".format(
                name, error_message(res)
            )
        )


def delete_hold(module, blade, name):
    # a hold applied or released moments ago is still "in use" while the
    # array works through the paths; the delete is retried for a while
    for attempt in range(RELEASE_WAIT_TRIES):
        res = blade.delete_legal_holds(names=[name])
        if res.status_code == 200 or "in use" not in error_message(res).lower():
            break
        time.sleep(RELEASE_WAIT_SECONDS)
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to delete legal hold {0}. Error: {1}".format(
                name, error_message(res)
            )
        )


def hold_paths(module, blade, name, file_system, paths, recursive):
    res = blade.post_legal_holds_held_entities(
        names=[name], file_system_names=[file_system], paths=paths, recursive=recursive
    )
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to hold {0} on {1} with legal hold {2}. Error: {3}".format(
                ", ".join(paths), file_system, name, error_message(res)
            )
        )


def release_path(module, blade, name, file_system, path, recursive):
    # the array releases a held entity with PATCH released=true (DELETE is 405)
    # and wants the recursive flag for a directory, which the held entity
    # listing does not say: a release refused for that reason is retried
    # recursively
    res = blade.patch_legal_holds_held_entities(
        names=[name],
        file_system_names=[file_system],
        paths=[path],
        released=True,
        recursive=recursive,
    )
    if res.status_code != 200 and not recursive:
        if "recursive" in error_message(res).lower():
            return release_path(module, blade, name, file_system, path, True)
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to release {0} on {1} from legal hold {2}. Error: {3}".format(
                path, file_system, name, error_message(res)
            )
        )


def facts(name, description, entities):
    return {"name": name, "description": description, "held_entities": entities}


def run(module, blade):
    name = module.params["name"]
    state = module.params["state"]
    description = module.params["description"]
    file_system = module.params["file_system"]
    paths = module.params["paths"]
    if paths is not None:
        paths = sorted(set(paths))

    hold = get_hold(blade, name)

    if state == "absent":
        if hold is None:
            module.exit_json(changed=False, hold={})
        if not module.check_mode:
            for entity in held_entities(module, blade, name):
                release_path(
                    module,
                    blade,
                    name,
                    entity["file_system"],
                    entity["path"],
                    module.params["recursive"],
                )
            # a release is asynchronous: the hold stays in use for a few seconds
            # and the array refuses to delete it until then
            for _ in range(RELEASE_WAIT_TRIES):
                if not held_entities(module, blade, name):
                    break
                time.sleep(RELEASE_WAIT_SECONDS)
            delete_hold(module, blade, name)
        module.exit_json(changed=True, hold={})

    changed = False
    if hold is None:
        changed = True
        entities = []
        if not module.check_mode:
            create_hold(module, blade, name, description)
    else:
        entities = held_entities(module, blade, name)
        current = getattr(hold, "description", None)
        if description is not None and description != current:
            module.warn(
                "Legal hold {0} keeps its description {1!r}; the description is "
                "set when a hold is created".format(name, current)
            )
        description = current

    if file_system is not None:
        current_paths = {e["path"] for e in entities if e["file_system"] == file_system}
        if module.params["entities_state"] == "present":
            to_add = [p for p in paths if p not in current_paths]
            if to_add:
                changed = True
                if not module.check_mode:
                    hold_paths(
                        module,
                        blade,
                        name,
                        file_system,
                        to_add,
                        module.params["recursive"],
                    )
                entities = entities + [
                    {"file_system": file_system, "path": p, "status": None}
                    for p in to_add
                ]
        else:
            to_remove = [p for p in paths if p in current_paths]
            if to_remove:
                changed = True
                if not module.check_mode:
                    for path in to_remove:
                        release_path(
                            module,
                            blade,
                            name,
                            file_system,
                            path,
                            module.params["recursive"],
                        )
                entities = [
                    e
                    for e in entities
                    if not (e["file_system"] == file_system and e["path"] in to_remove)
                ]

    if changed and not module.check_mode:
        # the array's own view, with the status of what was just held
        entities = held_entities(module, blade, name)
    module.exit_json(
        changed=changed,
        hold=facts(
            name,
            description,
            sorted(entities, key=lambda e: (e["file_system"] or "", e["path"] or "")),
        ),
    )


def main():
    argument_spec = purefb_argument_spec()
    argument_spec.update(
        dict(
            name=dict(type="str", required=True),
            description=dict(type="str"),
            state=dict(type="str", default="present", choices=["present", "absent"]),
            file_system=dict(type="str"),
            paths=dict(type="list", elements="str"),
            recursive=dict(type="bool", default=False),
            entities_state=dict(
                type="str", default="present", choices=["present", "absent"]
            ),
        )
    )
    module = AnsibleModule(
        argument_spec,
        required_together=[["file_system", "paths"]],
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
    blade = get_system(module)
    run(module, blade)


if __name__ == "__main__":
    main()
