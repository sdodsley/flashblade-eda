# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Static checks over how the playbooks keep the text a rule hands them:
every set_fact value, play var and import var built from that text is one
Jinja expression whose last filter is string, so the fact keeps the text as
text whatever shape the text has; the Slack message puts a space where the
detail had a carriage return; and no set_fact removes characters from the
text a rule passed in."""

import pytest
import yaml

SET_FACT = ("ansible.builtin.set_fact", "set_fact")

# (playbook, task, fact, member): the set_fact values built from the text a
# rule passes in. member is the field of a record fact (dotted for a nested
# field), None for a plain fact.
STRING_FACTS = [
    ("notify.yml", "Build the alert record", "alert_record", "severity"),
    ("notify.yml", "Build the alert record", "alert_record", "array"),
    ("notify.yml", "Build the alert record", "alert_record", "subject"),
    ("notify.yml", "Build the alert record", "alert_record", "detail"),
    ("notify.yml", "Build the alert record", "alert_record", "alert_id"),
    ("notify.yml", "Format the Slack message", "slack_text", None),
    ("alert_runbook.yml", "Build the record", "alert_record", "severity"),
    ("alert_runbook.yml", "Build the record", "alert_record", "array"),
    ("alert_runbook.yml", "Build the record", "alert_record", "alert_id"),
    ("alert_runbook.yml", "Build the record", "alert_record", "subject"),
    ("alert_runbook.yml", "Build the record", "alert_record", "component"),
    ("alert_runbook.yml", "Build the record", "alert_record", "description"),
    ("alert_runbook.yml", "Build the record", "alert_record", "array_action"),
    ("alert_runbook.yml", "Build the record", "alert_record", "knowledge_base_url"),
    ("capacity_request.yml", "Pin the request id and time", "request_id", None),
    ("capacity_request.yml", "Parse the candidates", "candidates_error", None),
    ("capacity_request.yml", "Build the request", "capacity_request", "request_id"),
    ("capacity_request.yml", "Build the request", "capacity_request", "array"),
    ("capacity_request.yml", "Build the request", "capacity_request", "used_pct"),
    ("capacity_request.yml", "Build the request", "capacity_request", "used_bytes"),
    (
        "capacity_request.yml",
        "Build the request",
        "capacity_request",
        "capacity_bytes",
    ),
    ("capacity_request.yml", "Build the request", "capacity_request", "days_to_full"),
    (
        "capacity_request.yml",
        "Build the request",
        "capacity_request",
        "growth_bytes_per_day",
    ),
    (
        "capacity_request.yml",
        "Build the request",
        "capacity_request",
        "candidates_error",
    ),
    ("orchestrator_post.yml", "Pin the event type and its fields", "event_type", None),
    ("orchestrator_post.yml", "Build the envelope", "envelope", "event.event_type"),
    ("orchestrator_post.yml", "Build the envelope", "envelope", "event.array"),
    ("user_quota.yml", "Work out the file system the alert names", "quota_fs", None),
    ("maintenance_window.yml", "Settle the outcome text", "outcome", None),
    ("runbooks/1101.yml", "Pick the file system the alert names", "quota_fs", None),
    ("runbooks/1101.yml", "Keep what the array said", "quota_fs_error", None),
    (
        "s3_approval.yml",
        "Note an approved eradication for the reclaim playbook",
        "outcome",
        None,
    ),
    ("s3_approval.yml", "Settle the outcome", "outcome", None),
    ("s3_anomaly.yml", "Describe the outcome", "outcome", None),
    ("auditor_guard.yml", "Describe the quota outcome", "outcome", None),
    ("renew_certificate.yml", "Describe the outcome", "outcome", None),
    (
        "replication_resume.yml",
        "Decide whether there is anything to resume",
        "remote_bucket",
        None,
    ),
    ("replication_resume.yml", "Describe the outcome", "outcome", None),
    (
        "client_diagnose.yml",
        "Match the address against ansible_host and the host names",
        "client_host",
        None,
    ),
    ("quarantine_client.yml", "Settle the outcome text", "outcome", None),
    ("tarpit.yml", "Describe the outcome", "outcome", None),
    ("grow_fs.yml", "Pin the request id", "grow_request_id", None),
    ("grow_fs.yml", "Note where the request object went", "request_object", None),
    ("grow_fs.yml", "Describe the outcome", "outcome", None),
    ("tasks/fb_rest.yml", "Say why the request cannot be made", "fb_rest_error", None),
]

# (playbook, var): play vars built from the text a rule passes in, read by a
# set_fact later in the play
PLAY_VARS = [
    ("capacity_reclaim.yml", "request_file"),
    ("s3_approval.yml", "request_file"),
    ("auditor_guard.yml", "guard_bucket"),
    ("qos_cap.yml", "policy_name"),
]

# (playbook, imported playbook, var): vars an import_playbook hands on,
# built from the text a rule passed the importing playbook
IMPORT_VARS = [
    ("orchestrator_post.yml", "capacity_request.yml", "at_risk_array"),
    ("session_check.yml", "notify.yml", "subject"),
    ("session_check.yml", "notify.yml", "detail"),
]

# (playbook, task, fact, key): members of a dict a set_fact builds in Jinja
# whose value is the text a rule passed in; each member ends in string
DICT_MEMBERS = [
    ("s3_approval.yml", "Build the receipt", "receipt", "request_id"),
    ("s3_approval.yml", "Build the receipt", "receipt", "approver_key"),
    ("s3_approval.yml", "Build the receipt", "receipt", "approver_user"),
    ("s3_approval.yml", "Build the receipt", "receipt", "source_ip"),
    ("s3_approval.yml", "Build the receipt", "receipt", "outcome"),
]

# (playbook, task, fact): the set_fact values that remove characters from
# text that is the array's or the operator's, never a rule's: the remote
# prefix of the array's own credential name, the trailing slash of the
# management address, the "2." of the REST versions the array lists
ALLOWED_REMOVALS = {
    (
        "replication_resume.yml",
        "Decide whether there is anything to resume",
        "remote_credential",
    ),
    ("tasks/fb_rest.yml", "Settle the array URL for the request", "fb_rest_base"),
    ("tasks/fb_rest.yml", "Settle the REST version for the request", "fb_rest_floor"),
    (
        "tasks/fb_rest.yml",
        "Settle the REST version for the request",
        "fb_rest_version",
    ),
}


def _load(repo_root, name):
    return yaml.safe_load((repo_root / "playbooks" / name).read_text(encoding="utf-8"))


def _tasks(items):
    """Every task of a task list, the ones inside blocks included."""
    for item in items or []:
        yield item
        for section in ("block", "rescue", "always"):
            if section in item:
                yield from _tasks(item[section])


def _all_tasks(doc):
    """The tasks of a playbook (every play, every section) or a task file."""
    for item in doc or []:
        if "hosts" in item or "import_playbook" in item:
            for section in ("pre_tasks", "tasks", "post_tasks", "handlers"):
                yield from _tasks(item.get(section))
        else:
            yield from _tasks([item])


def _set_fact(doc, task_name):
    """The facts a set_fact task sets, by the task's name."""
    tasks = [t for t in _all_tasks(doc) if t.get("name") == task_name]
    assert len(tasks) == 1, f"{len(tasks)} tasks named '{task_name}'"
    facts = next((tasks[0][key] for key in SET_FACT if key in tasks[0]), None)
    assert facts is not None, f"'{task_name}' is not a set_fact task"
    return facts


def _set_facts(doc):
    """(task name, facts) for every set_fact task of a file."""
    for task in _all_tasks(doc):
        for key in SET_FACT:
            if key in task:
                yield task.get("name", "?"), task[key]


def _member(value, path):
    for key in path.split("."):
        value = value[key]
    return value


def _flat(value):
    return " ".join(str(value).split())


def _is_one_string_expression(text):
    return (
        text.startswith("{{")
        and text.endswith("| string }}")
        and text.count("{{") == 1
        and "{%" not in text
    )


def _strings(node):
    """Every string in a nested structure of dicts and lists."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item)


def _arguments(text, start):
    """The arguments of the call whose opening parenthesis is at start,
    split at the commas of its own level; quoted text is skipped."""
    depth, quote, piece, pieces = 0, None, [], []
    for char in text[start + 1 :]:
        if quote:
            piece.append(char)
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            piece.append(char)
        elif char in "([{":
            depth += 1
            piece.append(char)
        elif char in ")]}":
            if depth == 0:
                pieces.append("".join(piece).strip())
                return pieces
            depth -= 1
            piece.append(char)
        elif char == "," and depth == 0:
            pieces.append("".join(piece).strip())
            piece = []
        else:
            piece.append(char)
    pieces.append("".join(piece).strip())
    return pieces


def _removes_characters(text):
    """Whether the text applies regex_replace or replace with an empty
    replacement, as a filter or through map."""
    for callee in ("regex_replace", "replace", "map"):
        at = 0
        while True:
            at = text.find(callee + "(", at)
            if at < 0:
                break
            before = text[at - 1] if at else " "
            arguments = _arguments(text, at + len(callee))
            at += len(callee) + 1
            if before.isalnum() or before == "_":
                continue
            if callee == "map":
                if len(arguments) >= 3 and arguments[0] in (
                    "'regex_replace'",
                    "'replace'",
                ):
                    arguments = arguments[1:]
                else:
                    continue
            if len(arguments) >= 2 and arguments[1] in ("''", '""'):
                return True
    return False


def _dict_member(text, key):
    """The expression of one member of a Jinja dict literal: from after
    'key': to the comma or closing brace of the dict's own level."""
    start = text.index(f"'{key}':") + len(f"'{key}':")
    return _arguments("(" + text[start:], 0)[0]


def _ids(table):
    """One id per row: its parts, the None of a plain fact left out."""
    return [":".join(str(p) for p in row if p is not None) for row in table]


@pytest.mark.parametrize(
    "playbook, task, fact, member", STRING_FACTS, ids=_ids(STRING_FACTS)
)
def test_a_fact_built_from_rule_text_is_one_string_expression(
    repo_root, playbook, task, fact, member
):
    """The value is one Jinja expression whose last filter is string, so the
    fact keeps the text a rule handed the playbook as text."""
    facts = _set_fact(_load(repo_root, playbook), task)
    assert fact in facts, f"{playbook} '{task}' sets no {fact}"
    value = facts[fact] if member is None else _member(facts[fact], member)
    text = _flat(value)
    where = f"{playbook} '{task}' {fact}{'.' + member if member else ''}"
    assert _is_one_string_expression(text), f"{where}: {text}"


@pytest.mark.parametrize("playbook, var", PLAY_VARS, ids=_ids(PLAY_VARS))
def test_a_play_var_built_from_rule_text_is_one_string_expression(
    repo_root, playbook, var
):
    """A play var a set_fact reads is evaluated before the set_fact's own
    filters see it, so the var is one expression ending in string itself."""
    plays = [p for p in _load(repo_root, playbook) if var in (p.get("vars") or {})]
    assert len(plays) == 1, f"{playbook}: {len(plays)} plays set the var {var}"
    text = _flat(plays[0]["vars"][var])
    assert _is_one_string_expression(text), f"{playbook} vars {var}: {text}"


@pytest.mark.parametrize("playbook, imported, var", IMPORT_VARS, ids=_ids(IMPORT_VARS))
def test_an_import_var_built_from_rule_text_is_one_string_expression(
    repo_root, playbook, imported, var
):
    """The vars an import_playbook hands on are read by the imported
    playbook's set_fact tasks, so each is one expression ending in string."""
    imports = [
        p for p in _load(repo_root, playbook) if p.get("import_playbook") == imported
    ]
    assert len(imports) == 1, f"{playbook}: {len(imports)} imports of {imported}"
    assert var in (imports[0].get("vars") or {}), f"{playbook}: no var {var}"
    text = _flat(imports[0]["vars"][var])
    assert _is_one_string_expression(text), f"{playbook} import {var}: {text}"


@pytest.mark.parametrize(
    "playbook, task, fact, key", DICT_MEMBERS, ids=_ids(DICT_MEMBERS)
)
def test_a_dict_member_built_from_rule_text_ends_in_string(
    repo_root, playbook, task, fact, key
):
    """A dict a set_fact builds in Jinja keeps its type; each member that
    carries the text a rule passed in ends in string."""
    text = _flat(_set_fact(_load(repo_root, playbook), task)[fact])
    assert f"'{key}':" in text, f"{playbook} '{task}' {fact} has no member {key}"
    member = _dict_member(text, key)
    assert member.endswith("| string"), f"{playbook} '{task}' {fact}.{key}: {member}"


def test_the_slack_message_keeps_a_space_for_a_carriage_return(repo_root):
    """A carriage return in the detail becomes a space in the Slack text,
    never nothing, so two fragments of the detail do not meet."""
    facts = _set_fact(_load(repo_root, "notify.yml"), "Format the Slack message")
    text = _flat(facts["slack_text"])
    assert "regex_replace('\\r', ' ')" in text, text
    assert "regex_replace('\\r', '')" not in text, text


def test_no_set_fact_removes_characters_from_rule_text(repo_root):
    """No set_fact applies regex_replace or replace with an empty replacement
    to text a rule passed in: removing characters can join two fragments of
    the text into one. The removals on the array's or the operator's own
    text are listed in ALLOWED_REMOVALS."""
    playbooks = repo_root / "playbooks"
    found = set()
    for path in sorted(playbooks.rglob("*.yml")):
        name = path.relative_to(playbooks).as_posix()
        for task, facts in _set_facts(_load(repo_root, name)):
            for fact, value in (facts or {}).items():
                if any(_removes_characters(_flat(s)) for s in _strings(value)):
                    found.add((name, task, fact))
    unexpected = sorted(found - ALLOWED_REMOVALS)
    assert unexpected == [], "\n".join(
        f"{name}: '{task}' {fact} removes characters" for name, task, fact in unexpected
    )
    stale = sorted(ALLOWED_REMOVALS - found)
    assert stale == [], f"ALLOWED_REMOVALS lists removals no longer there: {stale}"
