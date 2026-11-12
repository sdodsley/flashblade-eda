#!/usr/bin/python
# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Manage the audit filters of a bucket on an Everpure FlashBlade."""

DOCUMENTATION = r"""
---
module: purefb_bucket_audit_filter
version_added: "0.1.0"
short_description: Manage FlashBlade bucket audit filters
description:
  - Create, replace and delete the audit filters of a bucket. A filter names
    the S3 actions an object store audit policy records for the bucket,
    optionally only under some key prefixes; a member bucket without a filter
    audits nothing. The array has no update call for a filter, so a filter
    whose actions or prefixes differ is deleted and created again.
  - Needs Purity//FB 4.6.3 (REST 2.20) or later and py-pure-client 1.98.0 or
    later.
author:
  - Simon Dodsley (@sdodsley)
options:
  bucket:
    description:
      - Bucket the filter belongs to.
    type: str
    required: true
  name:
    description:
      - Name of the filter, 1 to 63 letters and digits with at least one
        letter. The array accepts no other character.
    type: str
    required: true
  actions:
    description:
      - S3 actions the filter records, for example C(s3:All), C(s3:GetObject)
        or C(s3:PutObject). The names are checked against the list the array
        publishes. Required when I(state) is C(present).
    type: list
    elements: str
  s3_prefixes:
    description:
      - Key prefixes the filter is limited to. Omit it to record the actions
        on every key of the bucket.
    type: list
    elements: str
  state:
    description:
      - Create or replace the filter, or delete it.
    type: str
    default: present
    choices: [ present, absent ]
extends_documentation_fragment:
  - everpure.flashblade.everpure.fb
"""

EXAMPLES = r"""
- name: Record every S3 request on the data bucket
  sdodsley.flashblade_eda.purefb_bucket_audit_filter:
    bucket: eda-data
    name: edaall
    actions:
      - s3:All
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
    disable_warnings: true

- name: Record only the writes into the approval and request folders
  sdodsley.flashblade_eda.purefb_bucket_audit_filter:
    bucket: eda-log
    name: edaapprovals
    actions:
      - s3:PutObject
    s3_prefixes:
      - approvals/
      - requests/
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"

- name: Delete the filter
  sdodsley.flashblade_eda.purefb_bucket_audit_filter:
    bucket: eda-data
    name: edaall
    state: absent
    fb_url: "{{ fb_url }}"
    api_token: "{{ api_token }}"
"""

RETURN = r"""
filter:
  description: The filter after the run; empty when it is absent.
  returned: always
  type: dict
  contains:
    bucket:
      description: Bucket the filter belongs to.
      type: str
    name:
      description: Name of the filter.
      type: str
    actions:
      description: Sorted S3 actions the filter records.
      type: list
      elements: str
    s3_prefixes:
      description: Sorted key prefixes the filter is limited to; empty when
        it covers the whole bucket.
      type: list
      elements: str
  sample:
    bucket: eda-log
    name: edaapprovals
    actions: [s3:PutObject]
    s3_prefixes: [approvals/, requests/]
"""

import re

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
# the array rejects anything but letters and digits in a filter name
NAME_PATTERN = re.compile(r"[A-Za-z0-9]{1,63}")
LETTER = re.compile(r"[A-Za-z]")


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


def valid_name(name):
    return bool(NAME_PATTERN.fullmatch(name) and LETTER.search(name))


def known_actions(blade):
    """The action names the array publishes, or None when that call fails;
    the names given are then sent unchecked and the array judges them."""
    res = blade.get_bucket_audit_filter_actions()
    if res.status_code != 200:
        return None
    return {item.name for item in res.items}


def get_filter(blade, bucket, name):
    """The existing filter, or None. Listing the bucket's filters and picking
    by name tells a missing filter from a failed call."""
    res = blade.get_buckets_audit_filters(bucket_names=[bucket])
    if res.status_code != 200:
        return None
    for item in res.items:
        if item.name == name:
            return item
    return None


def same_filter(existing, actions, prefixes):
    """Order does not matter to the array, so compare as sets."""
    return set(getattr(existing, "actions", None) or []) == set(actions) and set(
        getattr(existing, "s3_prefixes", None) or []
    ) == set(prefixes)


def create_filter(module, blade, bucket, name, actions, prefixes):
    values = {"actions": actions}
    if prefixes:
        values["s3_prefixes"] = prefixes
    body = model(module, "BucketAuditFilterPost")(**values)
    res = blade.post_buckets_audit_filters(
        bucket_names=[bucket], names=[name], audit_filter=body
    )
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to create audit filter {0} on bucket {1}. Error: {2}".format(
                name, bucket, error_message(res)
            )
        )


def delete_filter(module, blade, bucket, name):
    res = blade.delete_buckets_audit_filters(bucket_names=[bucket], names=[name])
    if res.status_code != 200:
        module.fail_json(
            msg="Failed to delete audit filter {0} on bucket {1}. Error: {2}".format(
                name, bucket, error_message(res)
            )
        )


def facts(bucket, name, actions, prefixes):
    return {
        "bucket": bucket,
        "name": name,
        "actions": sorted(actions),
        "s3_prefixes": sorted(prefixes),
    }


def main():
    argument_spec = purefb_argument_spec()
    argument_spec.update(
        dict(
            bucket=dict(type="str", required=True),
            name=dict(type="str", required=True),
            actions=dict(type="list", elements="str"),
            s3_prefixes=dict(type="list", elements="str"),
            state=dict(type="str", default="present", choices=["present", "absent"]),
        )
    )
    module = AnsibleModule(
        argument_spec,
        required_if=[["state", "present", ["actions"]]],
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

    bucket = module.params["bucket"]
    name = module.params["name"]
    state = module.params["state"]
    actions = sorted(set(module.params["actions"] or []))
    prefixes = sorted(set(module.params["s3_prefixes"] or []))
    if not valid_name(name):
        module.fail_json(
            msg="audit filter names are 1 to 63 letters and digits with at least "
            "one letter; got {0!r}".format(name)
        )
    if state == "present" and not actions:
        module.fail_json(msg="actions must name at least one S3 action")

    blade = get_system(module)
    if state == "present":
        known = known_actions(blade)
        unknown = sorted(set(actions) - known) if known is not None else []
        if unknown:
            module.fail_json(
                msg="unknown audit filter action(s) {0}; the array offers {1}".format(
                    ", ".join(unknown), ", ".join(sorted(known))
                )
            )
    existing = get_filter(blade, bucket, name)

    if state == "absent":
        if existing is None:
            module.exit_json(changed=False, filter={})
        if not module.check_mode:
            delete_filter(module, blade, bucket, name)
        module.exit_json(changed=True, filter={})

    if existing is not None:
        if same_filter(existing, actions, prefixes):
            module.exit_json(
                changed=False, filter=facts(bucket, name, actions, prefixes)
            )
        if not module.check_mode:
            delete_filter(module, blade, bucket, name)
            create_filter(module, blade, bucket, name, actions, prefixes)
        module.exit_json(changed=True, filter=facts(bucket, name, actions, prefixes))

    if not module.check_mode:
        create_filter(module, blade, bucket, name, actions, prefixes)
    module.exit_json(changed=True, filter=facts(bucket, name, actions, prefixes))


if __name__ == "__main__":
    main()
