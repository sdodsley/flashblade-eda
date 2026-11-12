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
severity and array; and no Jinja expression hides an escape literal."""

import re

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
}

# playbook -> the array whose credential it must have been given; every
# playbook addressed with hosts: "{{ target_array }}" compares with
# inventory_hostname and is added at test time
CREDENTIAL_ARRAY_CHECKS = {
    "capacity_request.yml": "at_risk_array",
    "capacity_reclaim.yml": "request.array",
}
CREDENTIAL_GUARD = "credential_array is not defined or credential_array == {}"

RESOLVE_ARRAY = "{{ playbook_dir }}/tasks/resolve_array.yml"
FB_REST = "{{ playbook_dir }}/tasks/fb_rest.yml"
# playbook -> the expression it resolves; alert_runbook resolves only for a
# per-code runbook (its other routes touch no array) and capacity_request
# only when the array is an inventory host (the request must still be
# written for one that is not)
RESOLVES = {
    "alert_runbook.yml": "target_array",
    "apply_baseline.yml": "inventory_hostname",
    "auditor_guard.yml": "inventory_hostname",
    "canary.yml": "inventory_hostname",
    "capacity_reclaim.yml": "request.array",
    "capacity_request.yml": "at_risk_array",
    "flag_alert.yml": "inventory_hostname",
    "grow_fs.yml": "inventory_hostname",
    "identify_led.yml": "inventory_hostname",
    "maintenance_window.yml": "inventory_hostname",
    "posture_revert.yml": "inventory_hostname",
    "qos_cap.yml": "inventory_hostname",
    "qos_lift.yml": "inventory_hostname",
    "reconcile.yml": "inventory_hostname",
    "remediate_dns.yml": "inventory_hostname",
    "remediate_ntp.yml": "inventory_hostname",
    "s3_anomaly.yml": "inventory_hostname",
    "s3_approval.yml": "inventory_hostname",
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


def _ordered_tasks(doc):
    """Every task in run order, blocks flattened (rescue and always last)."""
    out = []

    def walk(tasks):
        for task in tasks:
            if "block" in task:
                walk(task["block"])
                walk(task.get("rescue", []))
                walk(task.get("always", []))
            else:
                out.append(task)

    for play in doc:
        if "import_playbook" in play:
            continue
        for key in ("pre_tasks", "tasks", "post_tasks"):
            walk(play.get(key, []))
    return out


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


def _assert_conditions(doc):
    out = []
    for tasks in _all_task_lists(doc):
        for task in tasks:
            if "ansible.builtin.assert" in task:
                out.extend(task["ansible.builtin.assert"]["that"])
    return out


def test_playbooks_refuse_another_arrays_credential(repo_root):
    """With one credential attached, hostvars[<any array>].fb_url is the
    attached array's, so each playbook must compare credential_array with the
    array it was asked to act on."""
    expected = dict(CREDENTIAL_ARRAY_CHECKS)
    for path in sorted((repo_root / "playbooks").glob("*.yml")):
        doc = _load(path)
        if isinstance(doc[0], dict) and doc[0].get("hosts") == "{{ target_array }}":
            expected[path.name] = "inventory_hostname"
    assert len(expected) >= 2 + 10, sorted(expected)
    for name, array in sorted(expected.items()):
        doc = _load(repo_root / "playbooks" / name)
        conditions = _assert_conditions(doc)
        assert CREDENTIAL_GUARD.format(array) in conditions, f"{name}: no guard"
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
    takes its keys from the object_store entry, is allowed)."""
    playbooks = repo_root / "playbooks"
    for path in sorted(playbooks.glob("*.yml")):
        doc = _load(path)
        tasks = _ordered_tasks(doc)
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
