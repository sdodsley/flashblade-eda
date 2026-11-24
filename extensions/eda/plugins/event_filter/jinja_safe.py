# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""jinja_safe: keep event text as text.

An event's strings come from outside the rulebook: an alert's summary from
the array, a syslog line, an S3 object key, a webhook body, an LLDP
neighbour name. The playbooks an action starts receive those strings as
variables and render them, so text that happens to look like a template
would be evaluated there. The project's own sources emit plain text
themselves; this filter does the same for the stock sources (webhook,
Alertmanager, tick), putting a space after each template delimiter in every
string of the event, keys and nested values included, so the text stays text
wherever it travels:

    {{  ->  { {        {%  ->  { %        {#  ->  { #        {{{  ->  { { {
    #jinja2  ->  # jinja2  (the header that would change the delimiters)
    $ANSIBLE_VAULT  ->  $ ANSIBLE_VAULT  (the header of an encrypted value)
    a brace, hash or dollar at the very end of a string gets a space as well

Numbers, booleans and null are left alone. The change is one space per
delimiter, visible in a notification or a record and harmless to the names
FlashBlade allows (file systems, buckets, policies, arrays and users cannot
contain a brace); an object key or a free-text field that carried a
delimiter is reported with the space in it and is never run. Applying the
filter twice changes nothing.

    sources:
      - ansible.eda.webhook:
          ...
        filters:
          - sdodsley.flashblade_eda.jinja_safe:

The filter takes no arguments.
"""

DOCUMENTATION = r"""
---
short_description: Neutralise template delimiters in every string of an event.
description:
  - Puts a space after each Jinja delimiter ("{{", "{%", "{#"), after the "#"
    of the "#jinja2" override header, after the "$" of the "$ANSIBLE_VAULT"
    header and after a brace, hash or dollar that ends a string, in every
    string value and key of the event,
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

DELIMITER_BRACE = re.compile(r"\{(?=[{%#])")
# the override header, with or without its colon ("#jinja2" + ":" joined
# later would rebuild it)
HEADER_HASH = re.compile(r"#(?=jinja2)")
# the vault header: text that opens with it is handed to a vault decrypt
VAULT = re.compile(r"\$(?=ANSIBLE_VAULT)")
VAULT_WORD = "ANSIBLE_VAULT"
# a "$" that ends a string, alone or with the start of the vault word
VAULT_TAIL = re.compile(r"\$([A-Z_]{0,12})$")
# a brace or hash left at the very end could complete a delimiter or the
# header when two strings are joined
TRAILING = re.compile(r"[{#]$")


def clean_text(value: str) -> str:
    """The string with every template delimiter broken by a space: each
    brace that starts one gets a space after it (so a run of braces becomes
    "{ { {", with nothing left to open a template), the "#" of the override
    header is split from "jinja2", the "$" of the vault header from
    "ANSIBLE_VAULT", and a brace, hash or dollar at the very end gets a
    space too."""
    if "{" in value:
        value = DELIMITER_BRACE.sub("{ ", value)
    if "#" in value:
        value = HEADER_HASH.sub("# ", value)
    if "$" in value:
        value = VAULT.sub("$ ", value)
        tail = VAULT_TAIL.search(value)
        if tail and VAULT_WORD.startswith(tail.group(1)):
            value = value[: tail.start() + 1] + " " + value[tail.start() + 1 :]
    if value and value[-1] in "{#":
        value = TRAILING.sub(lambda m: m.group(0) + " ", value)
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
