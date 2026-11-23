# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""jinja_safe: keep event text as text.

An event's strings come from outside the rulebook: an alert's summary from
the array, a syslog line, an S3 object key, a webhook body, an LLDP
neighbour name. The playbooks an action starts receive those strings as
variables and render them, so text that happens to look like a template
would be evaluated there. This filter runs on every source and puts a space
after each template delimiter in every string of the event, keys and nested
values included, so the text stays text wherever it travels:

    {{  ->  { {        {%  ->  { %        {#  ->  { #        {{{  ->  { { {
    #jinja2:  ->  #jinja2 :  (the header that would change the delimiters)

Numbers, booleans and null are left alone. The change is one space per
delimiter, visible in a notification or a record and harmless to the names
FlashBlade allows (file systems, buckets, policies, arrays and users cannot
contain a brace); an object key or a free-text field that carried a
delimiter is reported with the space in it and is never run. Applying the
filter twice changes nothing.

    sources:
      - sdodsley.flashblade_eda.purefb_events:
          ...
        filters:
          - sdodsley.flashblade_eda.jinja_safe:

The filter takes no arguments.
"""

DOCUMENTATION = r"""
---
short_description: Neutralise template delimiters in every string of an event.
description:
  - Puts a space after each Jinja delimiter ("{{", "{%", "{#") and inside the
    "#jinja2:" override header in every string value and key of the event,
    recursively, so event text is never evaluated as a template by a playbook
    the rulebook starts. Numbers, booleans and null are unchanged. Idempotent.
options: {}
"""

EXAMPLES = r"""
- sdodsley.flashblade_eda.purefb_syslog:
    port: 5140
  filters:
    - sdodsley.flashblade_eda.jinja_safe:
"""

import re
from typing import Any

# a brace that starts a delimiter: followed by another brace, % or #
DELIMITER_BRACE = re.compile(r"\{(?=[{%#])")
# the header that would change the delimiters
HEADER = "#jinja2:"
HEADER_SAFE = "#jinja2 :"


def clean_text(value: str) -> str:
    """The string with every template delimiter broken by a space: each
    brace that starts one gets a space after it (so a run of braces becomes
    "{ { {", with nothing left to open a template) and the override header
    is split."""
    if "{" in value:
        value = DELIMITER_BRACE.sub("{ ", value)
    if HEADER in value:
        value = value.replace(HEADER, HEADER_SAFE)
    return value


def clean(value: Any) -> Any:
    """`value` with every string in it, however deep, passed through
    clean_text; other types unchanged."""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return {clean(k): clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, tuple):
        return tuple(clean(v) for v in value)
    return value


def main(event: dict, **kwargs: Any) -> dict:
    """ansible-rulebook entry point: the event with its text kept as text."""
    return clean(event)
