# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The jinja_safe event filter: every string of an event, however deep and
keys included, has its template delimiters broken by a space, so event text
stays text in the playbooks a rule starts; nothing that is not a string is
touched, and a second pass changes nothing."""

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FILTER = ROOT / "extensions" / "eda" / "plugins" / "event_filter" / "jinja_safe.py"

# (text as it would start a template, text as the filter leaves it)
MARKS = [
    ("{{", "{ {"),
    ("{%", "{ %"),
    ("{#", "{ #"),
    ("#jinja2:", "# jinja2:"),
]

# strings with no delimiter: FlashBlade names, addresses, prose and the
# single braces and closing delimiters a template never starts with
PLAIN = [
    "fb-a",
    "fs-01_data.v2",
    "bucket.name:tag",
    "198.51.100.7",
    "2001:db8::7",
    "alert on fb-a: file system fs-01 is 91% full",
    "a { b }} c %} d #} e # jinja2: f",
    "",
]


def _load():
    """Load the filter module from its file, as conftest._load does for the
    source plugins; the filter needs nothing beyond the standard library."""
    spec = importlib.util.spec_from_file_location("jinja_safe", FILTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def jinja_safe():
    return _load()


@pytest.mark.parametrize("mark, safe", MARKS, ids=[m for m, _ in MARKS])
def test_each_delimiter_is_broken_in_a_plain_string(jinja_safe, mark, safe):
    assert jinja_safe.main({"text": f"{mark} x"}) == {"text": f"{safe} x"}
    # a brace or hash that ends the string gets a space of its own as well
    bare = safe + (" " if safe[-1] in "{#" else "")
    assert jinja_safe.main({"text": mark}) == {"text": bare}


def test_a_delimiter_inside_longer_text_changes_only_the_delimiter(jinja_safe):
    before = "fb-a alert: {{ summary }} on fs-01 ({% raw %})"
    after = jinja_safe.main({"text": before})["text"]
    assert after == "fb-a alert: { { summary }} on fs-01 ({ % raw %})"
    assert len(after) == len(before) + 2
    assert after.startswith("fb-a alert: ") and after.endswith(" raw %})")


def test_the_jinja2_header_at_the_start_of_a_string_is_broken(jinja_safe):
    header = "#jinja2: variable_start_string='[[', variable_end_string=']]'"
    out = jinja_safe.main({"text": header + "\n[[ x ]]"})["text"]
    assert out.startswith("# jinja2: variable_start_string")
    assert "#jinja2:" not in out
    assert out.endswith("\n[[ x ]]")


def test_nested_dicts_lists_and_tuples_are_walked_keys_included(jinja_safe):
    event = {
        "{{ key": [{"inner": ("{% a", "{# b", 3)}, "{{ c"],
        "plain": {"deep": {"{# k": ["#jinja2: d", None]}},
    }
    out = jinja_safe.main(event)
    assert out == {
        "{ { key": [{"inner": ("{ % a", "{ # b", 3)}, "{ { c"],
        "plain": {"deep": {"{ # k": ["# jinja2: d", None]}},
    }
    assert isinstance(out["{ { key"], list)
    assert isinstance(out["{ { key"][0]["inner"], tuple)
    # the event passed in is left as it was
    assert "{{ key" in event and event["{{ key"][1] == "{{ c"


@pytest.mark.parametrize("value", [7, 0, -1, 2.5, 0.0, True, False, None], ids=repr)
def test_non_strings_pass_through_with_their_type(jinja_safe, value):
    out = jinja_safe.main({"value": value, "nested": [value, {"k": value}]})
    for got in (out["value"], out["nested"][0], out["nested"][1]["k"]):
        assert got == value
        assert type(got) is type(value)


@pytest.mark.parametrize("text", PLAIN, ids=repr)
def test_an_event_without_a_delimiter_comes_back_equal(jinja_safe, text):
    event = {
        "text": text,
        "count": 3,
        "ok": True,
        "nothing": None,
        "nested": {text: [text, 1.5, (text, False)]},
    }
    assert jinja_safe.main(event) == event
    assert isinstance(jinja_safe.main(event), dict)


def test_applying_the_filter_twice_equals_applying_it_once(jinja_safe):
    event = {
        "{{ key": ["{% a", "{# b", "#jinja2: c", "plain", 1, None, True],
        "mixed": {"{{": "{%", "{#": "#jinja2:"},
        "text": "fb-a alert: {{ summary }} ({% raw %}{# note #})",
    }
    once = jinja_safe.main(event)
    assert jinja_safe.main(once) == once
    assert once != event


@pytest.mark.parametrize("run", ["{{{", "{{{{", "{{{{{", "{{{%", "{{{#"], ids=repr)
def test_a_run_of_braces_leaves_no_delimiter(jinja_safe, run):
    """Three or more braces in a row hold a delimiter that starts after the
    first brace; one pass leaves none of the four marks in the text, and a
    second pass changes nothing."""
    once = jinja_safe.main({"text": f"{run} x"})
    for mark, _ in MARKS:
        assert mark not in once["text"], once["text"]
    assert jinja_safe.main(once) == once


def test_main_accepts_and_ignores_extra_keyword_arguments(jinja_safe):
    out = jinja_safe.main({"text": "{{ x"}, some_option=True, other="value")
    assert out == {"text": "{ { x"}


def test_main_returns_a_dict_for_an_empty_event(jinja_safe):
    assert jinja_safe.main({}) == {}


@pytest.mark.parametrize(
    "text, expected",
    [
        ("#jinja2", "# jinja2"),
        ("x #jinja2", "x # jinja2"),
        ("ends with {", "ends with { "),
        ("ends with #", "ends with # "),
        ("{", "{ "),
        ("#", "# "),
        ("ends with %", "ends with %"),
    ],
)
def test_the_header_without_its_colon_and_a_trailing_brace_or_hash_are_broken(
    jinja_safe, text, expected
):
    assert jinja_safe.clean_text(text) == expected
    assert jinja_safe.clean_text(expected) == expected


@pytest.mark.parametrize(
    "left, right",
    [
        ("{", "{ 7 * 7 }}"),
        ("{", "% x %}"),
        ("{", "# x #}"),
        ("#jinja2", ":variable_start_string:'[%'"),
        ("#", "jinja2:x"),
        ("a {", "{ b"),
    ],
)
def test_a_join_of_two_cleaned_strings_forms_no_delimiter(jinja_safe, left, right):
    """Two cleaned strings put together, with nothing or a colon between
    them, never form a template delimiter or the override header."""
    for glue in ("", ":", ": "):
        joined = jinja_safe.clean_text(left) + glue + jinja_safe.clean_text(right)
        for mark in ("{{", "{%", "{#", "#jinja2"):
            assert mark not in joined, (left, glue, right, joined)
