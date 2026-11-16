# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Static checks over the playbooks: every playbook an Automation Platform
workflow or an orchestrator node may follow ends with one
ansible.builtin.set_stats task (aggregate off, per host off, run once on
localhost) that publishes the documented keys; grow_fs.yml keeps the
project's guard rails; every array-facing playbook refuses to run with
another array's credential (credential_array, injected by the FlashBlade
API credential type) and resolves the array's credentials through
playbooks/tasks/resolve_array.yml before it touches the token; the REST
helper never shows a token; every alert-log record carries time, kind,
severity and array; no Jinja expression hides an escape literal; and
aap/seed.yml carries the fixed job template names, builds its activations
from the platform rulebooks and loads its credential types from JSON that
parses and injects what the playbooks read."""

import json
import pathlib
import re
import urllib.parse

import pytest
import yaml

# playbook -> keys every workflow may rely on
ARTIFACT_KEYS = {
    "capacity_request.yml": {
        "request_id",
        "candidate_count",
        "candidates",
        "at_risk_array",
    },
    "capacity_reclaim.yml": {"request_id", "eradicated", "refused", "result"},
    "identify_led.yml": {"component", "led_state"},
    "reconcile.yml": {"ntp_changed", "dns_changed"},
    "apply_baseline.yml": {"ntp_changed", "dns_changed", "git_head"},
    "notify.yml": {"record_kind"},
    "flag_alert.yml": {"alert_id", "flagged"},
    "grow_fs.yml": {"file_system", "old_size", "new_size", "outcome"},
    "user_quota.yml": {
        "file_system",
        "principal_kind",
        "principals",
        "raised",
    },
    "client_diagnose.yml": {"client", "client_host", "commands_ran"},
    "qos_cap.yml": {"object_kind", "object_name", "policy", "capped"},
    "qos_lift.yml": {"object_kind", "object_name", "policy", "lifted"},
    "maintenance_window.yml": {"array", "action", "status"},
    "canary.yml": {"access_key_id", "user", "disabled"},
    "s3_anomaly.yml": {"access_key_id", "anomaly_kind", "disabled"},
    "landing.yml": {"dataset", "key", "command_ran", "rc"},
    "lock_on_arrival.yml": {"bucket", "key", "held"},
    "s3_approval.yml": {"request_id", "action", "outcome"},
    "auditor_guard.yml": {"mode", "changed_objects", "outcome"},
    "posture_revert.yml": {"buckets", "reverted"},
    "telemetry_check.yml": {
        "verdict",
        "test_status",
        "array_reachable",
        "receiver_reachable",
    },
    "telemetry_setup.yml": {"target", "policies_attached", "test_ok"},
    "quarantine_client.yml": {"client", "action", "file_systems", "changed_count"},
    "tarpit.yml": {"file_system", "action", "policy", "changed"},
    "release_locks.yml": {"client", "removed_sessions", "removed_locks"},
    "replication_resume.yml": {"bucket", "resumed"},
    "dr_drill.yml": {"drills", "failed_count"},
    "showback.yml": {"date", "object_key", "file_systems", "buckets"},
    "selftest.yml": {"checks", "failed"},
    "renew_certificate.yml": {"certificate", "renewed", "valid_to"},
}

# playbook -> the task file it includes per loop item; the array work (the
# resolve, the guard, the token) lives there and is checked as part of the
# playbook
INCLUDED_TASKS = {"dr_drill.yml": "tasks/dr_drill_one.yml"}

# playbook -> the array whose credential it must have been given; every
# playbook addressed with hosts: "{{ target_array }}" compares with
# inventory_hostname and is added at test time
CREDENTIAL_ARRAY_CHECKS = {
    "capacity_request.yml": "at_risk_array",
    "capacity_reclaim.yml": "request.array",
    "dr_drill.yml": "drill.peer",
}
CREDENTIAL_GUARD = "credential_array is not defined or credential_array == {}"

RESOLVE_ARRAY = "{{ playbook_dir }}/tasks/resolve_array.yml"
FB_REST = "{{ playbook_dir }}/tasks/fb_rest.yml"
# playbook -> the expression it resolves; alert_runbook resolves only for a
# per-code runbook (its other routes touch no array), capacity_request
# only when the array is an inventory host (the request must still be
# written for one that is not) and dr_drill resolves each drill's peer in
# the task file it includes
RESOLVES = {
    "alert_runbook.yml": "target_array",
    "apply_baseline.yml": "inventory_hostname",
    "auditor_guard.yml": "inventory_hostname",
    "canary.yml": "inventory_hostname",
    "capacity_reclaim.yml": "request.array",
    "capacity_request.yml": "at_risk_array",
    "dr_drill.yml": "drill.peer",
    "flag_alert.yml": "inventory_hostname",
    "grow_fs.yml": "inventory_hostname",
    "identify_led.yml": "inventory_hostname",
    "maintenance_window.yml": "inventory_hostname",
    "posture_revert.yml": "inventory_hostname",
    "qos_cap.yml": "inventory_hostname",
    "qos_lift.yml": "inventory_hostname",
    "quarantine_client.yml": "inventory_hostname",
    "reconcile.yml": "inventory_hostname",
    "release_locks.yml": "inventory_hostname",
    "remediate_dns.yml": "inventory_hostname",
    "remediate_ntp.yml": "inventory_hostname",
    "renew_certificate.yml": "inventory_hostname",
    "replication_resume.yml": "inventory_hostname",
    "s3_anomaly.yml": "inventory_hostname",
    "s3_approval.yml": "inventory_hostname",
    "selftest.yml": "inventory_hostname",
    "showback.yml": "inventory_hostname",
    "tarpit.yml": "inventory_hostname",
    "telemetry_check.yml": "inventory_hostname",
    "telemetry_setup.yml": "inventory_hostname",
    "user_quota.yml": "inventory_hostname",
}
S3_OBJECT = "{{ playbook_dir }}/tasks/s3_object.yml"

SET_STATS = "ansible.builtin.set_stats"
DATA_KEY = re.compile(r"'([a-z][a-z0-9_]*)':")
RECORD_KEYS = ("'time'", "'kind'", "'severity'", "'array'")


class _Loader(yaml.SafeLoader):
    """SafeLoader that accepts Ansible's !unsafe tag."""


_Loader.add_constructor("!unsafe", lambda loader, node: loader.construct_scalar(node))


def _load(path):
    return yaml.load(path.read_text(), Loader=_Loader)


def _task_lists(tasks):
    """Yield every list of tasks under `tasks`, blocks included."""
    yield tasks
    for task in tasks:
        for key in ("block", "rescue", "always"):
            if key in task:
                yield from _task_lists(task[key])


def _all_task_lists(doc):
    for play in doc:
        for key in ("pre_tasks", "tasks", "post_tasks"):
            if key in play:
                yield from _task_lists(play[key])


def _data_keys(data):
    """Keys of a set_stats data block, written as a mapping or as one
    templated dict ("{{ {'key': value, ...} }}", which keeps native types)."""
    if isinstance(data, dict):
        return set(data)
    return set(DATA_KEY.findall(data))


@pytest.mark.parametrize("name", sorted(ARTIFACT_KEYS))
def test_playbook_ends_with_one_set_stats_publishing_the_documented_keys(
    name, repo_root
):
    path = repo_root / "playbooks" / name
    doc = _load(path)
    found = [
        (tasks, i)
        for tasks in _all_task_lists(doc)
        for i, task in enumerate(tasks)
        if SET_STATS in task
    ]
    assert len(found) == 1, f"{name}: expected one {SET_STATS} task, found {len(found)}"
    tasks, index = found[0]
    task = tasks[index]
    assert index == len(tasks) - 1, f"{name}: {SET_STATS} is not the last task"
    assert doc[-1]["tasks"][-1] is task, f"{name}: set_stats is not in the last play"
    args = task[SET_STATS]
    assert args.get("aggregate") is False, f"{name}: aggregate must be false"
    assert args.get("per_host") is False, f"{name}: per_host must be false"
    assert task.get("run_once") is True, f"{name}: set_stats must run once"
    assert task.get("delegate_to") == "localhost", f"{name}: delegate to localhost"
    missing = ARTIFACT_KEYS[name] - _data_keys(args["data"])
    assert not missing, f"{name}: artifacts missing {sorted(missing)}"


def _walk_tasks(tasks, out):
    """Flatten a task list in run order (block, then rescue, then always)."""
    for task in tasks:
        if "block" in task:
            _walk_tasks(task["block"], out)
            _walk_tasks(task.get("rescue", []), out)
            _walk_tasks(task.get("always", []), out)
        else:
            out.append(task)
    return out


def _ordered_tasks(doc):
    """Every task in run order, blocks flattened (rescue and always last)."""
    out = []
    for play in doc:
        if "import_playbook" in play:
            continue
        for key in ("pre_tasks", "tasks", "post_tasks"):
            _walk_tasks(play.get(key, []), out)
    return out


def _playbook_tasks(repo_root, name):
    """The playbook's tasks followed by those of the task file it includes
    per item (INCLUDED_TASKS), so the array work in that file is checked
    with the playbook that runs it."""
    tasks = _ordered_tasks(_load(repo_root / "playbooks" / name))
    if name in INCLUDED_TASKS:
        included = _load(repo_root / "playbooks" / INCLUDED_TASKS[name])
        tasks = tasks + _walk_tasks(included, [])
    return tasks


def _include_file(task):
    """The file an include_tasks task names, written as a string or a dict."""
    args = task.get("ansible.builtin.include_tasks")
    return args.get("file") if isinstance(args, dict) else args


def test_grow_fs_keeps_the_guard_rails(repo_root):
    """The size grown from is the array's own, the name and the size are
    checked before anything is sent, the exclusion list and the ceiling come
    before the write, and the write is one PATCH of one field."""
    doc = _load(repo_root / "playbooks" / "grow_fs.yml")
    assert len(doc) == 1
    play = doc[0]
    assert play["hosts"] == "{{ target_array }}"
    assert play.get("gather_facts") is False
    tasks = _ordered_tasks(doc)
    asserts = [
        t["ansible.builtin.assert"] for t in tasks if "ansible.builtin.assert" in t
    ]
    conditions = "\n".join("\n".join(a["that"]) for a in asserts)
    assert "inventory_hostname == target_array" in conditions
    assert "file_system is match(" in conditions
    assert "new_size | string is match(" in conditions
    assert "new_size_bytes | int > before.provisioned" in conditions
    names = [t.get("name", "") for t in tasks]
    read = names.index("Read the file system")
    grow = names.index("Grow the file system")
    assert (
        read < names.index("Refuse a file system that is missing or destroyed") < grow
    )
    assert (
        names.index("Decide whether the name is excluded from automatic growth") < grow
    )
    assert (
        names.index("Decide whether the target is above the automatic ceiling") < grow
    )
    assert names.index("Refuse a size that would not grow the file system") < grow
    write = tasks[grow]
    assert _include_file(write) == FB_REST
    assert write["vars"]["fb_rest_method"] == "PATCH"
    assert (
        write["vars"]["fb_rest_body"] == "{{ {'provisioned': new_size_bytes | int} }}"
    )
    assert "not excluded | bool and not over_cap | bool" in write["when"]
    record = next(t for t in tasks if "ansible.builtin.lineinfile" in t)
    line = record["ansible.builtin.lineinfile"]["line"]
    assert record["ansible.builtin.lineinfile"]["path"] == "{{ alert_log }}"
    assert "'kind': 'fs_grow'" in line
    assert record.get("delegate_to") == "localhost"
    # the ceiling writes a request, never a size
    request = next(t for t in tasks if t.get("name") == "Write the request file")
    assert "'kind': 'fs_grow_request'" in request["ansible.builtin.copy"]["content"]
    assert (
        "'proposed_actions': ['grow_fs']" in request["ansible.builtin.copy"]["content"]
    )


def _assert_conditions(tasks):
    return [
        condition
        for task in tasks
        if "ansible.builtin.assert" in task
        for condition in task["ansible.builtin.assert"]["that"]
    ]


def test_playbooks_refuse_another_arrays_credential(repo_root):
    """With one credential attached, hostvars[<any array>].fb_url is the
    attached array's, so each playbook must compare credential_array with the
    array it was asked to act on."""
    expected = dict(CREDENTIAL_ARRAY_CHECKS)
    for path in sorted((repo_root / "playbooks").glob("*.yml")):
        doc = _load(path)
        if isinstance(doc[0], dict) and doc[0].get("hosts") == "{{ target_array }}":
            expected[path.name] = "inventory_hostname"
    assert len(expected) >= 3 + 19, sorted(expected)
    for name, array in sorted(expected.items()):
        conditions = _assert_conditions(_playbook_tasks(repo_root, name))
        assert CREDENTIAL_GUARD.format(array) in conditions, f"{name}: no guard"
    # the drill resolves its peer's credentials and then checks the guard as
    # the first two tasks of its block, so a missing entry or a mismatch is
    # recorded as a failed drill and nothing is promoted on another array
    drill = _load(repo_root / "playbooks" / INCLUDED_TASKS["dr_drill.yml"])
    block = next(t for t in drill if "block" in t)
    assert _include_file(block["block"][0]) == RESOLVE_ARRAY
    assert block["block"][0]["vars"]["resolve_array_name"] == "{{ drill.peer }}"
    second = block["block"][1]["ansible.builtin.assert"]["that"]
    assert CREDENTIAL_GUARD.format("drill.peer") in second
    # the demote runs in the always, only after this drill promoted, through
    # the REST helper with the peer's own URL and token
    demote = next(
        t
        for t in _walk_tasks(block["always"], [])
        if _include_file(t) == FB_REST and t["vars"].get("fb_rest_method") == "PATCH"
    )
    assert demote["vars"]["fb_rest_body"] == (
        "{{ {'requested_promotion_state': 'demoted'} }}"
    )
    assert demote["vars"]["fb_rest_url"] == "{{ hostvars[drill.peer].fb_url }}"
    always_block = next(t for t in block["always"] if "block" in t)
    assert always_block["when"] == "drill_promoted | bool"
    record = next(
        t for t in _walk_tasks(block["always"], []) if "ansible.builtin.lineinfile" in t
    )
    line = record["ansible.builtin.lineinfile"]["line"]
    assert record["ansible.builtin.lineinfile"]["path"] == "{{ alert_log }}"
    for key in RECORD_KEYS + ("'kind': 'dr_drill'",):
        assert key in line, f"dr_drill_one.yml: record lacks {key}"


def test_fan_out_playbooks_accept_the_rulebooks_host_list(repo_root):
    """selftest.yml and showback.yml are run by the tick rulebook for every
    array at once (target_array is a comma list, an Ansible host pattern),
    so their guard checks membership in that list instead of equality, and
    still refuses another array's credential."""
    for name in ("selftest.yml", "showback.yml"):
        doc = _load(repo_root / "playbooks" / name)
        assert doc[0]["hosts"] == "{{ target_array }}", name
        conditions = _assert_conditions(_ordered_tasks(doc))
        assert "inventory_hostname in (target_array | string | split(','))" in (
            conditions
        ), name
        assert CREDENTIAL_GUARD.format("inventory_hostname") in conditions, name
    # apply_baseline spans every array (hosts: all:!localhost), so instead of
    # an assert that would fail every multi-array launch its first task ends
    # the hosts whose credential is not attached, before any baseline is read
    baseline = _load(repo_root / "playbooks" / "apply_baseline.yml")
    assert baseline[1]["hosts"] == "all:!localhost"
    first = baseline[1]["tasks"][0]
    assert (
        "ansible.builtin.meta" in first
    ), "apply_baseline.yml: no end_host guard first"
    assert first["ansible.builtin.meta"] == "end_host"
    assert "credential_array" in first["when"]
    assert "inventory_hostname" in first["when"]


def test_array_facing_playbooks_resolve_the_array_before_touching_the_token(
    repo_root,
):
    """Every playbook that uses api_token first includes resolve_array.yml
    with resolve_array_name set to the array it acts on, and the include
    comes before any task that references the token or the REST helper;
    playbooks that never touch an array over REST (notify, orchestrator_post,
    client_diagnose, session_check, landing, lock_on_arrival) never resolve
    one and never name the token or the REST helper (the S3 helper, which
    takes its keys from the object_store entry, is allowed). dr_drill.yml is
    checked together with the task file it includes per drill."""
    playbooks = repo_root / "playbooks"
    for path in sorted(playbooks.glob("*.yml")):
        tasks = _playbook_tasks(repo_root, path.name)
        includes = [t for t in tasks if _include_file(t) == RESOLVE_ARRAY]
        if path.name not in RESOLVES:
            assert not includes, f"{path.name}: includes resolve_array.yml"
            assert "api_token" not in path.read_text(), f"{path.name}: unresolved"
            assert "fb_rest.yml" not in path.read_text(), f"{path.name}: unresolved"
            continue
        assert len(includes) == 1, f"{path.name}: resolve includes"
        include = includes[0]
        expected = "{{ " + RESOLVES[path.name] + " }}"
        assert include["vars"]["resolve_array_name"] == expected, path.name
        # alert_runbook.yml itself never names the token: its per-code task
        # files do, through the REST helper, so only the include is checked
        first_token = next(
            (
                i
                for i, t in enumerate(tasks)
                if "api_token" in yaml.safe_dump(t, default_flow_style=False)
                or _include_file(t) == FB_REST
            ),
            None,
        )
        if first_token is not None:
            assert tasks.index(include) < first_token, f"{path.name}: token late"
    tasks = _load(playbooks / "tasks" / "resolve_array.yml")
    # every task that handles the token is no_log, and the fleet entry is
    # applied to the array's own host so hostvars[<array>] sees it
    for task in tasks:
        if "set_fact" in str(task):
            assert task.get("no_log") is True, task["name"]
    last = tasks[-1]
    assert set(last["ansible.builtin.set_fact"]) == {
        "fb_url",
        "api_token",
        "credential_array",
    }
    assert last["delegate_to"] == "{{ resolve_array_name }}"
    assert last["delegate_facts"] is True
    refusal = next(t for t in tasks if "ansible.builtin.assert" in t)
    assert "purefb_fleet_json is defined" in refusal["ansible.builtin.assert"]["that"]
    assert "FlashBlade fleet" in refusal["ansible.builtin.assert"]["fail_msg"]
    assert "FlashBlade API" in refusal["ansible.builtin.assert"]["fail_msg"]


def test_rest_helper_never_shows_the_token_and_always_logs_out(repo_root):
    """tasks/fb_rest.yml logs in with the api-token header, sends the one
    request with x-auth-token, keeps only status, body and error, logs out
    whatever happened, and fails last, with a message that names the request."""
    tasks = _load(repo_root / "playbooks" / "tasks" / "fb_rest.yml")
    uri = [t for t in tasks if "ansible.builtin.uri" in t]
    assert [t["name"] for t in uri] == [
        "Log in to the array",
        "Send the request",
        "Log out of the array",
    ]
    for task in uri:
        assert task.get("no_log") is True, task["name"]
    login, request, logout = uri
    assert login["ansible.builtin.uri"]["url"].endswith("/api/login")
    assert set(login["ansible.builtin.uri"]["headers"]) == {"api-token"}
    assert set(request["ansible.builtin.uri"]["headers"]) == {"x-auth-token"}
    assert request["failed_when"] is False
    assert logout["ansible.builtin.uri"]["url"].endswith("/api/logout")
    assert logout["failed_when"] is False
    keep = next(t for t in tasks if t["name"].startswith("Keep the outcome"))
    assert set(keep["ansible.builtin.set_fact"]) == {
        "fb_rest_status",
        "fb_rest_json",
        "fb_rest_error",
    }
    names = [t["name"] for t in tasks]
    assert names.index("Send the request") < names.index("Log out of the array")
    last = tasks[-1]
    assert "ansible.builtin.assert" in last
    assert "fb_rest_ignore_errors" in last["when"]
    assert "token" not in last["ansible.builtin.assert"]["fail_msg"].lower()
    text = (repo_root / "playbooks" / "tasks" / "fb_rest.yml").read_text()
    assert "fb_rest_api_version | default(fb_api_version | default('2.16'))" in text


def test_s3_helper_never_shows_a_key_and_never_fails(repo_root):
    """tasks/s3_object.yml runs one python helper with the keys in its
    environment (read from the variables the store entry names, never on
    the command line), keeps only status, body and error, and leaves the
    decision to the caller as fb_rest.yml does."""
    tasks = _load(repo_root / "playbooks" / "tasks" / "s3_object.yml")
    commands = [t for t in tasks if "ansible.builtin.command" in t]
    assert len(commands) == 1
    helper = commands[0]
    assert helper["ansible.builtin.command"]["cmd"] == "{{ ansible_playbook_python }} -"
    assert "stdin" in helper["ansible.builtin.command"]
    assert helper.get("no_log") is True
    assert helper["failed_when"] is False
    env = helper["environment"]
    assert env["S3_ACCESS_KEY"] == "{{ lookup('env', s3_access_key_env) }}"
    assert env["S3_SECRET_KEY"] == "{{ lookup('env', s3_secret_key_env) }}"
    assert env["S3_ENDPOINT"] == "{{ lookup('env', s3_store.endpoint_env) }}"
    for arg in helper["ansible.builtin.command"].values():
        assert "lookup('env'" not in arg, "a key on the command line or in the code"
    keep = next(t for t in tasks if t["name"].startswith("Keep the outcome"))
    assert set(keep["ansible.builtin.set_fact"]) == {"s3_status", "s3_json", "s3_error"}
    assert not any("ansible.builtin.assert" in t for t in tasks[1:]), "fails late"
    # every caller passes the store entry and never a key
    for path in sorted((repo_root / "playbooks").glob("*.yml")):
        for task in _ordered_tasks(_load(path)):
            if _include_file(task) == S3_OBJECT:
                passed = task["vars"]
                assert "s3_store" in passed, f"{path.name}: no store entry"
                assert passed.get("s3_identity") in ("tail", "state"), path.name
                assert not {"s3_access_key", "s3_secret_key"} & set(passed), path.name


def test_every_alert_log_record_carries_the_common_keys(repo_root):
    """Whatever playbook writes a line to alert_log, the line is one JSON
    object with time, kind, severity and array, so one reader serves them all.
    A record built as a set_fact mapping (notify, alert_runbook,
    capacity_request) is checked by its keys, a templated dict by its text."""
    for path in sorted((repo_root / "playbooks").glob("*.yml")):
        doc = _load(path)
        for tasks in _all_task_lists(doc):
            for task in tasks:
                args = task.get("ansible.builtin.lineinfile")
                if not args or args.get("path") != "{{ alert_log }}":
                    continue
                line = args["line"]
                if line.startswith("{{ {"):
                    for key in RECORD_KEYS:
                        assert key in line, f"{path.name}: record lacks {key}"
                else:
                    fact = re.fullmatch(r"{{ (\w+) \| to_json }}", line.strip())
                    assert fact, f"{path.name}: unexpected line {line!r}"
                    builder = next(
                        t["ansible.builtin.set_fact"][fact.group(1)]
                        for ts in _all_task_lists(doc)
                        for t in ts
                        if fact.group(1) in t.get("ansible.builtin.set_fact", {})
                    )
                    for key in ("time", "kind", "severity", "array"):
                        assert (
                            key in builder
                        ), f"{path.name}: {fact.group(1)} lacks {key}"


def test_every_rulebook_has_a_plain_file_name(repo_root):
    """Activation names are derived from the rulebook files; every file must
    be a plain name that survives the template."""
    for path in sorted((repo_root / "extensions" / "eda" / "rulebooks").glob("*.yml")):
        assert re.fullmatch(r"[a-z_]+\.yml", path.name), path.name


# Inside a Jinja expression Ansible doubles backslashes before Jinja parses
# the string, so a '\n' written there is a backslash and an n, never a line
# break (a real one belongs to the YAML: a block scalar, or "\n" in a
# double-quoted scalar). Regex arguments are the exception: the doubling is
# what carries '\r' to the regex engine as a carriage-return class. The scan
# runs over the loaded YAML, where a YAML escape is already a newline and only
# a Jinja literal is still two characters.
ESCAPE_LITERAL = re.compile(r"""(['"])(?:\\.|(?!\1).)*\\[nrt](?:\\.|(?!\1).)*\1""")
REGEX_FILTER = re.compile(r"regex_(?:replace|search|findall|escape)\(\s*$")
JINJA_EXPRESSION = re.compile(r"\{\{.*?\}\}", re.S)


class _TolerantLoader(yaml.SafeLoader):
    """SafeLoader that passes any local tag (!unsafe, !vault) through."""


def _any_tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_TolerantLoader.add_multi_constructor("!", _any_tag)


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def test_no_escape_literals_inside_jinja_expressions(repo_root):
    files = (
        list((repo_root / "playbooks").rglob("*.yml"))
        + list((repo_root / "extensions" / "eda" / "rulebooks").rglob("*.yml"))
        + list((repo_root / "tests").glob("*.yml"))
    )
    offenders = []
    for path in sorted(files):
        documents = list(yaml.load_all(path.read_text(), Loader=_TolerantLoader))
        for text in _strings(documents):
            for expression in JINJA_EXPRESSION.finditer(text):
                body = expression.group(0)
                for literal in ESCAPE_LITERAL.finditer(body):
                    if REGEX_FILTER.search(body[: literal.start()]):
                        continue
                    offenders.append(
                        f"{path.relative_to(repo_root)}: {literal.group(0)} in {body[:70]!r}"
                    )
    assert offenders == [], "\n".join(offenders)


# ---- the platform seed (aap/) -----------------------------------------------

# the job templates aap/seed.yml creates: one per playbook, named as
# aap/README.md lists them; the platform rulebooks' run_job_template actions
# and the orchestrator definitions launch them by these names
JOB_TEMPLATE_NAMES = {
    "FlashBlade - alert runbook",
    "FlashBlade - apply baseline",
    "FlashBlade - auditor guard",
    "FlashBlade - canary",
    "FlashBlade - capacity reclaim",
    "FlashBlade - capacity request",
    "FlashBlade - client diagnose",
    "FlashBlade - DR drill",
    "FlashBlade - flag alert",
    "FlashBlade - grow file system",
    "FlashBlade - identify LED",
    "FlashBlade - landing",
    "FlashBlade - lock on arrival",
    "FlashBlade - maintenance window",
    "FlashBlade - notify",
    "FlashBlade - orchestrator post",
    "FlashBlade - posture revert",
    "FlashBlade - QoS cap",
    "FlashBlade - QoS lift",
    "FlashBlade - quarantine client",
    "FlashBlade - reconcile",
    "FlashBlade - release locks",
    "FlashBlade - remediate DNS",
    "FlashBlade - remediate NTP",
    "FlashBlade - renew certificate",
    "FlashBlade - replication resume",
    "FlashBlade - S3 anomaly",
    "FlashBlade - S3 approval",
    "FlashBlade - selftest",
    "FlashBlade - session check",
    "FlashBlade - showback",
    "FlashBlade - tarpit",
    "FlashBlade - telemetry check",
    "FlashBlade - telemetry setup",
    "FlashBlade - user quota",
}
WORKFLOW_NAME = "FlashBlade - capacity reclaim (controller tier)"
ACTIVATION_PREFIX = "FlashBlade rulebook - "
PLATFORM_RULEBOOKS = "extensions/eda/rulebooks/platform"
# what each credential type injects, as the playbooks (resolve_array.yml,
# the S3 helper), the source plugins and the orchestrator bridge read it:
# (env names, extra_vars names), one row per injectors file in aap/files
INJECTOR_SHAPES = [
    # FlashBlade API: the array's URL, token and inventory name
    (set(), {"fb_url", "api_token", "credential_array"}),
    # FlashBlade fleet: the JSON list, for resolve_array.yml on the
    # controller and for the purefb_events source on the EDA side
    (set(), {"purefb_fleet_json"}),
    (set(), {"purefb_arrays"}),
    # FlashBlade object store: the environment the object-side playbooks read
    (
        {
            "FB_OBJ_URL",
            "FB_OBJ_TOKEN",
            "FB_OBJ_S3_ENDPOINT",
            "FB_OBJ_S3_ACCESS_KEY",
            "FB_OBJ_S3_SECRET_KEY",
            "FB_OBJ_S3_STATE_ACCESS_KEY",
            "FB_OBJ_S3_STATE_SECRET_KEY",
        },
        set(),
    ),
    # Orchestrator service account and Slack webhook
    ({"AO_CLIENT_ID", "AO_CLIENT_SECRET"}, {"orchestrator_url"}),
    ({"SLACK_WEBHOOK_URL"}, set()),
]
# inputs that hold a secret wherever a credential type defines them
SECRET_INPUTS = {
    "api_token",
    "arrays",
    "secret_key",
    "state_secret_key",
    "client_secret",
    "webhook_url",
}
# hosts a URL in aap/ may name: the project's repository and public
# registries; a site's own platform, orchestrator or array never
PUBLIC_URL_HOSTS = {
    "github.com",
    "galaxy.ansible.com",
    "console.redhat.com",
    "registry.redhat.io",
    "registry.access.redhat.com",
    "quay.io",
    "pypi.org",
    "docs.ansible.com",
}
INJECTED = re.compile(r"{{\s*([a-z_]+)\s*}}")


def _seed(repo_root):
    return _load(repo_root / "aap" / "seed.yml")


def _seed_tasks(repo_root, module):
    """Every task of the seed that calls `module`, blocks included."""
    return [
        task
        for tasks in _all_task_lists(_seed(repo_root))
        for task in tasks
        if module in task
    ]


def _literal(play, value):
    """A play-level value that may be written as "{{ some_var }}"."""
    match = re.fullmatch(r"{{\s*(\w+)\s*}}", str(value).strip())
    return play["vars"][match.group(1)] if match else value


def test_seed_carries_the_fixed_job_template_names(repo_root):
    """One job template per playbook, named as aap/README.md and the
    platform rulebooks name them, created by one task that prompts for
    variables, credential and limit; the seed's module defaults cover the
    three platform collections."""
    play = _seed(repo_root)[0]
    templates = play["vars"]["job_templates"]
    assert {t["name"] for t in templates} == JOB_TEMPLATE_NAMES
    assert len(templates) == len(JOB_TEMPLATE_NAMES), "a template named twice"
    playbooks = {p.name for p in (repo_root / "playbooks").glob("*.yml")}
    named = {pathlib.PurePosixPath(t["playbook"]).name for t in templates}
    assert named == playbooks, sorted(named ^ playbooks)
    for template in templates:
        path = repo_root / template["playbook"]
        assert path.exists(), f"{template['name']}: {template['playbook']} missing"
    (task,) = _seed_tasks(repo_root, "ansible.controller.job_template")
    args = task["ansible.controller.job_template"]
    for prompt in (
        "ask_variables_on_launch",
        "ask_credential_on_launch",
        "ask_limit_on_launch",
    ):
        assert args.get(prompt) is True, prompt
    assert set(play["module_defaults"]) == {
        "group/ansible.platform.gateway",
        "group/ansible.controller.controller",
        "group/ansible.eda.eda",
    }


def test_seed_limits_apply_baseline_to_the_default_array(repo_root):
    """One launch applies one array's baseline: the template is limited to
    localhost (the play that refreshes the checkout) plus the default
    array, and no other template carries a limit; every template prompts
    for the limit, so another array's launch changes the credential and
    the limit together."""
    play = _seed(repo_root)[0]
    templates = play["vars"]["job_templates"]
    baseline = next(t for t in templates if t["name"] == "FlashBlade - apply baseline")
    assert baseline["limit"] == "localhost,{{ aap_default_array }}"
    assert [t for t in templates if "limit" in t] == [baseline]


def test_seed_workflow_runs_request_approval_reclaim(repo_root):
    """The controller-tier workflow: the request template, then a workflow
    approval, then the reclaim template with the fixed extra data; each of
    the three takes a failure edge to a notify node."""
    play = _seed(repo_root)[0]
    (task,) = _seed_tasks(repo_root, "ansible.controller.workflow_job_template")
    workflow = task["ansible.controller.workflow_job_template"]
    assert _literal(play, workflow["name"]) == WORKFLOW_NAME
    nodes = workflow["workflow_nodes"]
    by_id = {n["identifier"]: n for n in nodes}
    assert len(by_id) == len(nodes), "an identifier twice"

    def node_for(name):
        return next(n for n in nodes if n["unified_job_template"].get("name") == name)

    request = node_for("FlashBlade - capacity request")
    reclaim = node_for("FlashBlade - capacity reclaim")
    (approve,) = [
        n for n in nodes if n["unified_job_template"]["type"] == "workflow_approval"
    ]
    assert request["related"]["success_nodes"] == [
        {"identifier": approve["identifier"]}
    ]
    assert approve["related"]["success_nodes"] == [
        {"identifier": reclaim["identifier"]}
    ]
    extra = reclaim["extra_data"]
    assert extra["reclaim_action"] == "eradicate_pending"
    assert extra["approver"] == "workflow-approval"
    assert extra["rebuild_from_artifacts"] is True
    for node in (request, approve, reclaim):
        failures = node["related"].get("failure_nodes")
        assert failures, f"{node['identifier']}: no failure edge"
        for edge in failures:
            target = by_id[edge["identifier"]]["unified_job_template"]
            assert target.get("name") == "FlashBlade - notify", edge


def test_seed_enforces_the_enabled_flag_on_flashblade_activations_only(repo_root):
    """The EDA controller's name filter is a substring match and the only
    filter it applies, so the activations the flag is enforced on are read
    by their full names and matched exactly; on a shared controller the flag
    must never touch another project's activations."""
    tasks = [task for tasks in _all_task_lists(_seed(repo_root)) for task in tasks]
    tasks = [t for t in tasks if isinstance(t, dict)]
    (enforce,) = [
        t
        for t in tasks
        if str(t.get("name", "")).startswith("Enforce the enabled flag")
    ]
    assert enforce["loop"].strip() == "{{ flashblade_activations }}"
    (matched,) = [
        t for t in tasks if t.get("name") == "The seed's activations, matched exactly"
    ]
    expression = matched["ansible.builtin.set_fact"]["flashblade_activations"]
    assert "selectattr('name', 'in', flashblade_activation_names)" in expression
    assert "unique(attribute='id')" in expression


def test_seed_reads_activations_by_exact_name_and_never_lists(repo_root):
    """The activations listing takes ten seconds whatever the page size and
    a timeout gets the EDA API ejected at the gateway for every reader, so
    every read of the activations carries a name= filter and loops over the
    names the seed owns; nothing lists."""
    tasks = [task for tasks in _all_task_lists(_seed(repo_root)) for task in tasks]
    tasks = [t for t in tasks if isinstance(t, dict)]
    reads = [
        t
        for t in tasks
        if "/api/eda/v1/activations/?"
        in str(t.get("ansible.builtin.uri", {}).get("url", ""))
    ]
    assert len(reads) == 2, [t.get("name") for t in reads]
    for task in reads:
        url = task["ansible.builtin.uri"]["url"]
        assert "name={{ item | urlencode }}" in url, url
        assert "name__startswith" not in url and "search=" not in url
        assert task["loop"].strip() == "{{ flashblade_activation_names }}"
    (names,) = [
        t for t in tasks if t.get("name") == "The activation names this seed owns"
    ]
    assert (
        "extensions/eda/rulebooks/platform/*.yml"
        in names["ansible.builtin.set_fact"]["flashblade_activation_names"]
    )


def test_seed_activations_come_from_the_platform_rulebooks(repo_root):
    """The seed makes one activation per file of the platform rulebook
    directory, named FlashBlade rulebook - <name>, pinned to
    platform/<file> and switched by aap_eda_activations_enabled; the
    directory holds plain file names that survive the template."""
    (task,) = _seed_tasks(repo_root, "ansible.eda.rulebook_activation")
    loop = task["loop"]
    assert "fileglob" in loop, loop
    assert f"{PLATFORM_RULEBOOKS}/*.yml" in loop, loop
    args = task["ansible.eda.rulebook_activation"]
    assert args["name"].startswith(ACTIVATION_PREFIX), args["name"]
    assert args["rulebook_name"].startswith("platform/"), args["rulebook_name"]
    assert "aap_eda_activations_enabled" in str(args["enabled"])
    files = sorted((repo_root / PLATFORM_RULEBOOKS).glob("*.yml"))
    assert files, f"{PLATFORM_RULEBOOKS} holds no rulebook to activate"
    for path in files:
        assert re.fullmatch(r"[a-z_]+\.yml", path.name), path.name


def _credential_files(repo_root):
    files = sorted((repo_root / "aap" / "files").glob("*.json"))
    assert files, "aap/files holds no credential type JSON"
    return {path.name: json.loads(path.read_text()) for path in files}


def test_credential_type_files_parse_and_pair_up(repo_root):
    """Every JSON under aap/files parses; an *_inputs.json lists fields, an
    *_injectors.json (or *_eda_injectors.json) injects extra_vars or env,
    refers only to inputs its pair defines, and every token-like input is
    marked secret."""
    loaded = _credential_files(repo_root)
    inputs = {n: d for n, d in loaded.items() if n.endswith("_inputs.json")}
    injectors = {n: d for n, d in loaded.items() if n.endswith("_injectors.json")}
    assert set(loaded) == set(inputs) | set(injectors), sorted(loaded)
    for name, doc in inputs.items():
        fields = doc.get("fields")
        assert isinstance(fields, list) and fields, f"{name}: no fields"
        ids = [f["id"] for f in fields]
        assert len(ids) == len(set(ids)), f"{name}: an input twice"
        assert set(doc.get("required", [])) <= set(ids), f"{name}: required"
        for field in fields:
            if field["id"] in SECRET_INPUTS:
                assert field.get("secret") is True, f"{name}: {field['id']} open"
    for name, doc in injectors.items():
        sections = set(doc)
        assert sections & {"extra_vars", "env"}, f"{name}: no extra_vars, no env"
        assert sections <= {"extra_vars", "env", "file"}, f"{name}: {sections}"
        pair = re.sub(r"(_eda)?_injectors\.json$", "_inputs.json", name)
        assert pair in inputs, f"{name}: no {pair}"
        ids = {f["id"] for f in inputs[pair]["fields"]}
        used = {
            v
            for section in doc.values()
            for value in section.values()
            for v in INJECTED.findall(value)
        }
        assert used <= ids, f"{name}: injects {sorted(used - ids)}, undefined"


def test_credential_types_inject_the_documented_variables(repo_root):
    """Each credential type injects what the playbooks and the source
    plugins read: one injectors file per row of INJECTOR_SHAPES. The
    FlashBlade API type names the array it belongs to as credential_array,
    which every array-facing playbook compares with the array it was asked
    to act on, and the seed fills that input from the array's name on the
    controller and on the EDA side."""
    loaded = _credential_files(repo_root)
    injectors = [d for n, d in loaded.items() if n.endswith("_injectors.json")]
    shapes = [(set(d.get("env", {})), set(d.get("extra_vars", {}))) for d in injectors]
    for env, extra in INJECTOR_SHAPES:
        assert (env, extra) in shapes, f"no injectors with {sorted(env | extra)}"
    api = next(
        d for d in injectors if set(d.get("extra_vars", {})) == INJECTOR_SHAPES[0][1]
    )
    assert api["extra_vars"] == {
        "fb_url": "{{ fb_url }}",
        "api_token": "{{ api_token }}",
        "credential_array": "{{ array_name }}",
    }
    play = _seed(repo_root)[0]
    credential_inputs = [
        t[k]["inputs"]
        for tasks in _all_task_lists([play])
        for t in tasks
        for k in t
        if k in ("ansible.controller.credential", "ansible.eda.credential")
        and t[k].get("credential_type", t[k].get("credential_type_name"))
        == "FlashBlade API"
    ]
    assert len(credential_inputs) == 2, "one FlashBlade API credential task a side"
    for inputs in credential_inputs:
        assert "{{" in inputs["array_name"], inputs["array_name"]


def test_seed_variables_are_documented_and_hold_no_site_specifics(repo_root):
    """Every aap_* variable the seed reads is in aap/vars/example.yml; the
    aap/ tree names no address, no home directory and no host other than
    the project's repository and public registries; every task that writes
    a credential (its inputs hold a token) is no_log."""
    seed_text = (repo_root / "aap" / "seed.yml").read_text()
    example = _load(repo_root / "aap" / "vars" / "example.yml")
    referenced = set(re.findall(r"\baap_[a-z0-9_]+", seed_text))
    missing = sorted(referenced - set(example))
    assert not missing, f"aap/seed.yml reads variables example.yml lacks: {missing}"
    tree = "\n".join(
        p.read_text() for p in sorted((repo_root / "aap").rglob("*")) if p.is_file()
    )
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", tree), "an address in aap/"
    urls = {u for u in re.findall(r"https?://[^\s'\")]+", tree) if "<" not in u}
    hosts = {urllib.parse.urlparse(u).hostname for u in urls}
    assert hosts <= PUBLIC_URL_HOSTS, sorted(urls)
    github = {u for u in urls if "github.com" in u}
    assert github <= {"https://github.com/sdodsley/flashblade-eda"}, sorted(github)
    assert "/home/" not in tree and "C:\\" not in tree
    credential_tasks = [
        task
        for tasks in _all_task_lists(_seed(repo_root))
        for task in tasks
        if any(k.endswith(".credential") for k in task)
    ]
    # controller: per array, fleet, object store, orchestrator, Slack
    # webhook, source control; EDA: per array, fleet, object store,
    # orchestrator, platform user, source control
    assert len(credential_tasks) >= 10, len(credential_tasks)
    for task in credential_tasks:
        assert task.get("no_log") is True, f"{task.get('name')}: credential shown"
