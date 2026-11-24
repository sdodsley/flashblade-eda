# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The plain-text helpers (DELIMITER_BRACE, HEADER_HASH, TRAILING, clean_text
and clean) are defined in the jinja_safe event filter and copied into each
of the three event sources, which ansible-rulebook loads from their own
files; the four copies are one text, and every event a source queues goes
through clean."""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLUGINS = ROOT / "extensions" / "eda" / "plugins"
FILTER = PLUGINS / "event_filter" / "jinja_safe.py"
SOURCES = [
    PLUGINS / "event_source" / "purefb_events.py",
    PLUGINS / "event_source" / "purefb_syslog.py",
    PLUGINS / "event_source" / "purefb_object_audit.py",
]


def _helpers(path):
    """The text from the DELIMITER_BRACE assignment to the end of clean()."""
    text = path.read_text(encoding="utf-8")
    start = text.index("DELIMITER_BRACE = ")
    head = text.index("\ndef clean(", start)
    end = text.index("\n    return value\n", head) + len("\n    return value\n")
    return text[start:end]


def test_the_four_copies_of_the_plain_text_helpers_are_identical():
    reference = _helpers(FILTER)
    for name in (
        "HEADER_HASH = ",
        "VAULT = ",
        "VAULT_TAIL = ",
        "TRAILING = ",
        "def clean_text(",
        "def clean(",
    ):
        assert name in reference, name
    for path in SOURCES:
        assert _helpers(path) == reference, path.name


def test_every_event_a_source_queues_goes_through_clean():
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        callees = re.findall(r"\bqueue\.put\(([^()]*)", text)
        assert callees and set(callees) == {"clean"}, (path.name, callees)
