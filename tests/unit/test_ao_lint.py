# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Tests for tools/ao_lint.py and the orchestrator definitions: the shipped
definitions pass with the allowed template list, the template list matches
the definitions and the platform rulebooks and names only this project's
fixed job templates, every artifact a definition reads is one its template's
playbook publishes and documents, no per-environment id has been committed,
every rule has a fixture under tests/fixtures/orchestrator/ that trips it,
and the CLI exits and prints as documented."""

import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
LINTER = ROOT / "tools" / "ao_lint.py"
DEFINITIONS = sorted((ROOT / "orchestrator").glob("*.json"))
DAY2_DEFINITIONS = [p for p in DEFINITIONS if p.name.startswith("day2_")]
TEMPLATES = ROOT / "orchestrator" / "job_templates.txt"
PLATFORM_RULEBOOKS = ROOT / "extensions" / "eda" / "rulebooks" / "platform"
FIXTURES = sorted((ROOT / "tests" / "fixtures" / "orchestrator").glob("bad_*.json"))
SEED = ROOT / "aap" / "seed.yml"
ARTIFACTS_TEST = ROOT / "tests" / "unit" / "test_playbook_artifacts.py"
PLACEHOLDER = "REPLACE_AT_SYNC"
ORGANIZATION = "FlashBlade"
NOTIFY = "FlashBlade - notify"
TRIGGER_FIELD = re.compile(r"\$\{trigger\.([A-Za-z_][A-Za-z0-9_]*)")
# a problem line is file:node:rule:message; the file part may hold no colon
# because the CLI is given relative POSIX paths
PROBLEM_LINE = re.compile(
    r"^(?P<file>[^:]+):(?P<node>[^:]*):(?P<rule>[a-z_]+):(?P<message>.+)$"
)
# the EDA routes: webhook path -> the event type the envelope carries
ROUTES = {"fb_capacity": "purefb_capacity", "fb_replication": "purefb_replica_link"}
ENVELOPE_KEYS = [
    "schema",
    "event_type",
    "array",
    "maintenance_active",
    "observed_at",
    "data",
]

# job template -> playbook: the fixed template names (one per playbook under
# playbooks/) that aap/seed.yml creates and tools/platform_rulebooks.py maps
# the standalone rules to; a definition and job_templates.txt may only name
# one of these
TEMPLATE_PLAYBOOKS = {
    "FlashBlade - alert runbook": "alert_runbook.yml",
    "FlashBlade - apply baseline": "apply_baseline.yml",
    "FlashBlade - auditor guard": "auditor_guard.yml",
    "FlashBlade - canary": "canary.yml",
    "FlashBlade - capacity reclaim": "capacity_reclaim.yml",
    "FlashBlade - capacity request": "capacity_request.yml",
    "FlashBlade - client diagnose": "client_diagnose.yml",
    "FlashBlade - DR drill": "dr_drill.yml",
    "FlashBlade - flag alert": "flag_alert.yml",
    "FlashBlade - grow file system": "grow_fs.yml",
    "FlashBlade - identify LED": "identify_led.yml",
    "FlashBlade - landing": "landing.yml",
    "FlashBlade - lock on arrival": "lock_on_arrival.yml",
    "FlashBlade - maintenance window": "maintenance_window.yml",
    "FlashBlade - notify": "notify.yml",
    "FlashBlade - orchestrator post": "orchestrator_post.yml",
    "FlashBlade - posture revert": "posture_revert.yml",
    "FlashBlade - QoS cap": "qos_cap.yml",
    "FlashBlade - QoS lift": "qos_lift.yml",
    "FlashBlade - quarantine client": "quarantine_client.yml",
    "FlashBlade - reconcile": "reconcile.yml",
    "FlashBlade - release locks": "release_locks.yml",
    "FlashBlade - remediate DNS": "remediate_dns.yml",
    "FlashBlade - remediate NTP": "remediate_ntp.yml",
    "FlashBlade - renew certificate": "renew_certificate.yml",
    "FlashBlade - replication resume": "replication_resume.yml",
    "FlashBlade - S3 anomaly": "s3_anomaly.yml",
    "FlashBlade - S3 approval": "s3_approval.yml",
    "FlashBlade - selftest": "selftest.yml",
    "FlashBlade - session check": "session_check.yml",
    "FlashBlade - showback": "showback.yml",
    "FlashBlade - tarpit": "tarpit.yml",
    "FlashBlade - telemetry check": "telemetry_check.yml",
    "FlashBlade - telemetry setup": "telemetry_setup.yml",
    "FlashBlade - user quota": "user_quota.yml",
}


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ao_lint():
    return _load_module("ao_lint", LINTER)


@pytest.fixture(scope="module")
def allowed(ao_lint):
    return ao_lint.load_allowed_templates(TEMPLATES)


def _rule_of(fixture):
    """bad_<rule>.json or bad_<rule>__<variant>.json -> rule."""
    return fixture.stem[len("bad_") :].split("__")[0]


def _definition(name):
    return json.loads((ROOT / "orchestrator" / name).read_text())


def _node(doc, node_id):
    return next(n for n in doc["nodes"] if n["id"] == node_id)


def _job_template_names(doc):
    return {
        node["parameters"]["job_template_name"]
        for node in doc["nodes"]
        if node["type"] == "aap_job_template"
    }


def _platform_job_template_names():
    """Every run_job_template name in the generated platform rulebooks."""
    names = set()
    for path in sorted(PLATFORM_RULEBOOKS.glob("*.yml")):
        for ruleset in yaml.safe_load(path.read_text()) or []:
            for rule in ruleset.get("rules", []):
                actions = rule.get("actions") or [rule.get("action", {})]
                for action in actions:
                    job = action.get("run_job_template")
                    if job:
                        names.add(job["name"])
    return names


def test_there_are_seven_shipped_definitions():
    assert [p.name for p in DEFINITIONS] == [
        "capacity_reclaim_approval.json",
        "day2_grow_fs.json",
        "day2_identify_led.json",
        "day2_qos_cap.json",
        "day2_quarantine_client.json",
        "dr_drill_schedule.json",
        "replication_paused_approval.json",
    ]


@pytest.mark.parametrize("path", DEFINITIONS, ids=lambda p: p.name)
def test_shipped_definition_passes(path, ao_lint, allowed):
    problems = ao_lint.lint_file(path, allowed)
    assert problems == [], [ao_lint.format_problem(p) for p in problems]


@pytest.mark.parametrize("path", DEFINITIONS, ids=lambda p: p.name)
def test_shipped_definition_is_ascii_and_pretty(path):
    raw = path.read_bytes()
    raw.decode("ascii")
    assert b"\r" not in raw
    assert raw.endswith(b"}\n")
    doc = json.loads(raw)
    assert doc["schema_version"] == "2.0.0"
    assert doc["name"].startswith("FlashBlade - ")


def test_job_templates_txt_matches_the_definitions(allowed):
    """The definitions and the platform rulebooks name only listed templates,
    and every listed template is named by one of them. The reverse check
    needs the generated platform rulebooks; without that directory only the
    definitions' side is checked."""
    used = set()
    for path in DEFINITIONS:
        used |= _job_template_names(json.loads(path.read_text()))
    assert used <= allowed, f"used but not listed: {used - allowed}"
    if PLATFORM_RULEBOOKS.is_dir():
        used |= _platform_job_template_names()
        assert used == allowed, (
            f"used but not listed: {used - allowed}; "
            f"listed but unused: {allowed - used}"
        )


def test_job_templates_txt_names_only_fixed_templates(allowed):
    """Every listed name is one of the fixed job template names (one per
    playbook); a template no rulebook or definition launches outside a
    webhook or tick ruleset is not listed."""
    assert allowed <= set(TEMPLATE_PLAYBOOKS), sorted(allowed - set(TEMPLATE_PLAYBOOKS))
    for name in (
        "FlashBlade - apply baseline",
        "FlashBlade - maintenance window",
        "FlashBlade - release locks",
        "FlashBlade - selftest",
        "FlashBlade - showback",
        "FlashBlade - telemetry setup",
    ):
        assert name not in allowed, name
    for playbook in sorted((ROOT / "playbooks").glob("*.yml")):
        assert playbook.name in TEMPLATE_PLAYBOOKS.values(), playbook.name
    assert len(TEMPLATE_PLAYBOOKS) == len(list((ROOT / "playbooks").glob("*.yml")))


class _AnyTagLoader(yaml.SafeLoader):
    """safe_load that keeps the text of Ansible-only tags such as !unsafe."""


def _any_tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_AnyTagLoader.add_multi_constructor("!", _any_tag)


def _seed_templates():
    """job template name -> playbook as aap/seed.yml creates them."""
    templates = {}

    def walk(obj):
        if isinstance(obj, dict):
            if (
                str(obj.get("name", "")).startswith("FlashBlade - ")
                and "playbook" in obj
            ):
                templates[obj["name"]] = str(obj["playbook"]).split("/")[-1]
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(yaml.load(SEED.read_text(), Loader=_AnyTagLoader))
    return templates


def test_seed_creates_the_listed_templates_from_the_same_playbooks(allowed):
    """aap/seed.yml creates every template job_templates.txt lists, from the
    playbook the table here names; checked when the seed is in the tree and
    its templates are written as {name, playbook} objects."""
    if not SEED.exists():
        pytest.skip("aap/seed.yml is not in this tree")
    templates = _seed_templates()
    if not templates:
        pytest.skip("aap/seed.yml does not spell its templates as name/playbook")
    missing = allowed - set(templates)
    assert not missing, f"job_templates.txt names templates the seed lacks: {missing}"
    for name, playbook in sorted(templates.items()):
        assert TEMPLATE_PLAYBOOKS.get(name) == playbook, (name, playbook)


def _first_job_node(doc):
    """The first node on the path from the trigger that runs a template
    other than notify: the node that does the work the form asked for."""
    nodes = {node["id"]: node for node in doc["nodes"]}
    (trigger_id,) = [t["id"] for t in doc["triggers"]]
    queue = [trigger_id]
    seen = set()
    while queue:
        current = queue.pop(0)
        node = nodes.get(current)
        if (
            node is not None
            and node["type"] == "aap_job_template"
            and node["parameters"]["job_template_name"] != NOTIFY
        ):
            return node
        for edge in doc["edges"]:
            if edge["from"] == current and edge["to"] not in seen:
                seen.add(edge["to"])
                queue.append(edge["to"])
    raise AssertionError("no job node other than notify")


@pytest.mark.parametrize("path", DAY2_DEFINITIONS, ids=lambda p: p.name)
def test_day2_form_declares_exactly_what_its_job_node_reads(path):
    """Each day-2 form (manual trigger) declares, and requires, exactly the
    inputs its job node passes to the playbook or uses to pick the array's
    credential: an input nobody reads is noise on the form, and a reference
    the form does not declare is a run-time failure the linter also flags.
    Every day-2 route runs behind an approval."""
    doc = json.loads(path.read_text())
    schema = doc["triggers"][0]["parameters"]["input_schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    node = _first_job_node(doc)
    params = node["parameters"]
    read = set(TRIGGER_FIELD.findall(json.dumps(params["extra_vars"])))
    read |= set(TRIGGER_FIELD.findall(json.dumps(params.get("credential_names", []))))
    assert read == set(schema["properties"]), node["id"]
    assert params["credential_names"] == ["FlashBlade - ${trigger.array}"]
    assert params["extra_vars"]["target_array"] == "${trigger.array}"
    for key in ("alert_log", "request_dir", "fb_api_version", "object_store"):
        assert key not in params["extra_vars"], key
    # a boolean or integer input is passed whole, so it keeps its type
    for name, prop in schema["properties"].items():
        if prop["type"] in ("boolean", "integer"):
            assert "${trigger." + name + "}" in params["extra_vars"].values(), name
    approvals = [n for n in doc["nodes"] if n["type"] == "approval"]
    assert len(approvals) == 1, path.name
    approved = [
        e["to"]
        for e in doc["edges"]
        if e["from"] == approvals[0]["id"] and e.get("from_port") == "approved"
    ]
    assert approved == [node["id"]], path.name


ARTIFACT_REF = re.compile(
    r"\$\{\s*([A-Za-z_][A-Za-z0-9_]*)\.artifacts\.([A-Za-z_][A-Za-z0-9_]*)"
)
DATA_KEY = re.compile(r"'([a-z][a-z0-9_]*)':")


def _flatten(obj):
    if isinstance(obj, dict):
        yield obj
        for key in ("tasks", "pre_tasks", "post_tasks", "block", "always", "rescue"):
            for item in obj.get(key) or []:
                yield from _flatten(item)
    elif isinstance(obj, list):
        for item in obj:
            yield from _flatten(item)


def _published_keys(playbook):
    """Keys of every set_stats data dict in the playbook and in the task
    files under playbooks/ or playbooks/tasks/ it names."""
    path = ROOT / "playbooks" / playbook
    text = path.read_text()
    files = [path]
    for name in sorted(set(re.findall(r"([a-z_0-9]+\.yml)", text))):
        if name == playbook:
            continue
        for candidate in (
            ROOT / "playbooks" / name,
            ROOT / "playbooks" / "tasks" / name,
        ):
            if candidate.exists():
                files.append(candidate)
    keys = set()
    for item_path in files:
        for item in _flatten(yaml.load(item_path.read_text(), Loader=_AnyTagLoader)):
            if "ansible.builtin.set_stats" in item:
                keys |= set(
                    DATA_KEY.findall(
                        str(item["ansible.builtin.set_stats"].get("data", ""))
                    )
                )
    return keys


def test_artifacts_a_definition_reads_are_published_and_documented():
    """`${<node>.artifacts.<key>}` works only when the node's job template's
    playbook publishes <key> with set_stats; a missing key fails the node at
    run time, which the orchestrator's validator cannot know. The key must
    also be one tests/unit/test_playbook_artifacts.py documents
    (ARTIFACT_KEYS), the set every workflow may rely on."""
    documented = _load_module("test_playbook_artifacts", ARTIFACTS_TEST).ARTIFACT_KEYS
    checked = 0
    for path in DEFINITIONS:
        text = path.read_text()
        doc = json.loads(text)
        node_template = {
            n["id"]: n["parameters"].get("job_template_name")
            for n in doc["nodes"]
            if n["type"] == "aap_job_template"
        }
        for node_id, key in sorted(set(ARTIFACT_REF.findall(text))):
            assert (
                node_id in node_template
            ), f"{path.name}: {node_id} is not a job template node"
            playbook = TEMPLATE_PLAYBOOKS[node_template[node_id]]
            assert key in _published_keys(
                playbook
            ), f"{path.name}: {node_id}.artifacts.{key} is not published by {playbook}"
            assert key in documented.get(
                playbook, set()
            ), f"{path.name}: {node_id}.artifacts.{key} is not documented for {playbook}"
            checked += 1
    assert checked >= 10


@pytest.mark.parametrize("path", DEFINITIONS, ids=lambda p: p.name)
def test_only_placeholders_for_per_environment_ids(path):
    doc = json.loads(path.read_text())
    for node in doc["nodes"]:
        params = node["parameters"]
        if node["type"] == "aap_job_template":
            assert params["credential_id"] == PLACEHOLDER, node["id"]
            assert params["organization_name"] == ORGANIZATION, node["id"]
        if node["type"] == "approval":
            # approvers are named by group name, not id: the built-in group
            # and the one for the people who decide
            assert params["approver_groups"] == ["admins", "approvers"], node["id"]
            # 24 hours, or one hour for the time-sensitive replication route
            assert params["decision_window"] in (86400, 3600), node["id"]
            assert "fallback_decision" not in params, node["id"]
    for trigger in doc["triggers"]:
        accounts = trigger["parameters"].get("authorized_service_account_ids")
        if accounts is not None:
            assert accounts == [PLACEHOLDER], trigger["id"]


def test_eda_triggers_require_the_bridge_envelope():
    """Both EDA routes require what playbooks/orchestrator_post.yml sends
    (schema flashblade-eda/1, the event type of the route, the array name
    pattern the playbooks assert on) and listen on the paths
    vars/example.yml names under orchestrator_paths."""
    paths = {}
    for path in DEFINITIONS:
        doc = json.loads(path.read_text())
        for trigger in doc["triggers"]:
            if trigger["type"] != "eda_trigger":
                continue
            params = trigger["parameters"]
            event = params["input_schema"]["properties"]["event"]
            assert event["required"] == ENVELOPE_KEYS, path.name
            props = event["properties"]
            assert props["schema"]["const"] == "flashblade-eda/1", path.name
            assert props["array"]["pattern"] == "^[A-Za-z0-9][A-Za-z0-9._-]*$"
            assert props["maintenance_active"]["type"] == "boolean"
            assert ROUTES[params["webhook_path"]] == props["event_type"]["const"]
            paths[params["webhook_path"]] = path.name
    assert set(paths) == set(ROUTES)
    example = yaml.safe_load((ROOT / "vars" / "example.yml").read_text())
    assert set(example["orchestrator_paths"].values()) == set(ROUTES)


def test_capacity_definition_feeds_the_request_playbook():
    """The request node passes exactly the event-derived variables
    playbooks/capacity_request.yml reads; paths and switches come from the
    template."""
    doc = _definition("capacity_reclaim_approval.json")
    nodes = {node["id"]: node for node in doc["nodes"]}
    extra = nodes["capacity_request"]["parameters"]["extra_vars"]
    assert extra["at_risk_array"] == "${trigger.event.array}"
    assert set(extra) == {
        "at_risk_array",
        "used_pct",
        "used_bytes",
        "capacity_bytes",
        "days_to_full",
        "growth_bytes_per_day",
        "top_growers",
        "pending_eradication_file_systems",
        "pending_eradication_buckets",
        "pending_eradication_snapshots",
        "candidate_targets",
    }
    assert extra["candidate_targets"] == "${trigger.event.data.others}"
    playbook = (ROOT / "playbooks" / "capacity_request.yml").read_text()
    for key in extra:
        assert key in playbook, f"capacity_request.yml does not read {key}"
    for key in ("request_dir", "alert_log", "object_store", "s3_requests_enabled"):
        assert key not in extra, key
    reclaim = nodes["capacity_reclaim"]["parameters"]["extra_vars"]
    assert reclaim["request_id"] == "${capacity_request.artifacts.request_id}"
    assert reclaim["candidates"] == "${capacity_request.artifacts.candidates}"
    assert reclaim["array"] == "${trigger.event.array}"
    assert reclaim["rebuild_from_artifacts"] is True
    assert reclaim["reclaim_action"] == "eradicate_pending"
    assert reclaim["approver"] == "orchestrator"
    assert doc["triggers"][0]["parameters"]["webhook_path"] == "fb_capacity"
    assert nodes["has_candidates"]["parameters"]["condition"] == (
        "${capacity_request.artifacts.candidate_count} > 0"
    )
    # both array-touching nodes run with the event's array's own credential
    for node_id in ("capacity_request", "capacity_reclaim"):
        assert nodes[node_id]["parameters"]["credential_names"] == [
            "FlashBlade - ${trigger.event.array}"
        ], node_id


def test_replication_definition_feeds_the_resume_playbook():
    """A paused bucket replica link: the resume node passes exactly what
    playbooks/replication_resume.yml reads from the event (the array and the
    local bucket, plus a reason), behind a one-hour approval, with the
    event array's credential; the closing notify links the job."""
    doc = _definition("replication_paused_approval.json")
    nodes = {node["id"]: node for node in doc["nodes"]}
    assert doc["triggers"][0]["parameters"]["webhook_path"] == "fb_replication"
    resume = nodes["replication_resume"]["parameters"]
    assert set(resume["extra_vars"]) == {"target_array", "bucket", "reason"}
    assert resume["extra_vars"]["target_array"] == "${trigger.event.array}"
    assert resume["extra_vars"]["bucket"] == "${trigger.event.data.local}"
    assert resume["extra_vars"]["reason"].startswith("approved")
    assert resume["credential_names"] == ["FlashBlade - ${trigger.event.array}"]
    assert nodes["approve_resume"]["parameters"]["decision_window"] == 3600
    detail = nodes["notify_resumed"]["parameters"]["extra_vars"]["detail"]
    assert "${replication_resume.job_url}" in detail
    assert "${replication_resume.artifacts.resumed}" in detail
    # the notify nodes need no array credential
    for node_id, node in nodes.items():
        if node_id != "replication_resume" and node["type"] == "aap_job_template":
            assert "credential_names" not in node["parameters"], node_id


SIZE_SAMPLES = {
    "2T": True,
    "500G": True,
    "1024": True,
    "1.5T": False,
    "2TB": False,
    "0x10": False,
}


def _playbook_size_regex():
    text = (ROOT / "playbooks" / "grow_fs.yml").read_text()
    match = re.search(r"new_size \| string is match\('([^']+)'\)", text)
    assert match, "playbooks/grow_fs.yml does not assert on new_size"
    return match.group(1)


def test_grow_form_and_playbook_agree_on_sizes():
    """The manual trigger's form must not accept a size the playbook refuses
    (1.5T and 2TB) nor refuse one of these it takes; the form keeps to the
    upper-case unit letters the description names."""
    doc = _definition("day2_grow_fs.json")
    schema = doc["triggers"][0]["parameters"]["input_schema"]["properties"]["new_size"]
    assert schema["maxLength"] == 16
    form = re.compile(schema["pattern"])
    playbook = re.compile(_playbook_size_regex())
    for sample, accepted in SIZE_SAMPLES.items():
        assert bool(form.fullmatch(sample)) is accepted, f"form: {sample}"
        assert bool(playbook.fullmatch(sample)) is accepted, f"playbook: {sample}"
    name = doc["triggers"][0]["parameters"]["input_schema"]["properties"]["file_system"]
    text = (ROOT / "playbooks" / "grow_fs.yml").read_text()
    assert f"file_system is match('{name['pattern']}')" in text


def test_grow_node_uses_the_requested_arrays_credential():
    """The form accepts any array name, so the grow node must run with that
    array's credential (as the capacity nodes do with the event's); with the
    template's default credential a request for another array would be
    approved and then fail grow_fs.yml's credential_array guard, with no
    notify on the approved branch."""
    doc = _definition("day2_grow_fs.json")
    nodes = {node["id"]: node for node in doc["nodes"]}
    params = nodes["grow_fs"]["parameters"]
    assert params["credential_names"] == ["FlashBlade - ${trigger.array}"]
    assert params["extra_vars"] == {
        "target_array": "${trigger.array}",
        "file_system": "${trigger.file_system}",
        "new_size": "${trigger.new_size}",
    }
    # the notify nodes need no array credential
    for node_id, node in nodes.items():
        if node_id != "grow_fs" and node["type"] == "aap_job_template":
            assert "credential_names" not in node["parameters"], node_id


def test_day2_forms_match_the_playbooks_inputs():
    """The LED form offers on or off (identify_led.yml's led_state through
    the bool filter) at severity info; the QoS form names the kind the
    playbook accepts and a positive count; the quarantine form allows an
    empty file system (every export that names the client) and the two
    actions the playbook knows."""
    led = _definition("day2_identify_led.json")
    props = led["triggers"][0]["parameters"]["input_schema"]["properties"]
    assert props["led_state"]["enum"] == ["on", "off"]
    extra = _node(led, "identify_led")["parameters"]["extra_vars"]
    assert extra["led_state"] == "${trigger.led_state}"
    assert extra["severity"] == "info"

    qos = _definition("day2_qos_cap.json")
    props = qos["triggers"][0]["parameters"]["input_schema"]["properties"]
    assert props["object_kind"]["enum"] == ["file_system", "bucket"]
    assert props["cap_ops_per_sec"]["type"] == "integer"
    assert props["cap_ops_per_sec"]["minimum"] == 1
    extra = _node(qos, "qos_cap")["parameters"]["extra_vars"]
    assert extra["cap_ops_per_sec"] == "${trigger.cap_ops_per_sec}"
    assert "reason" in extra and "mode" not in extra

    quarantine = _definition("day2_quarantine_client.json")
    props = quarantine["triggers"][0]["parameters"]["input_schema"]["properties"]
    assert props["action"]["enum"] == ["quarantine", "release"]
    assert re.fullmatch(props["file_system"]["pattern"], "")
    assert re.fullmatch(props["file_system"]["pattern"], "eda-demo-quota")
    assert re.fullmatch(props["client"]["pattern"], "192.0.2.10")
    assert re.fullmatch(props["client"]["pattern"], "2001:db8::10")
    assert not re.fullmatch(props["client"]["pattern"], "client-01")
    extra = _node(quarantine, "quarantine_client")["parameters"]["extra_vars"]
    assert set(extra) == {"target_array", "client", "file_system", "action", "reason"}


def test_dr_drill_schedule_forces_the_drill():
    """The orchestrator's schedule is the clock: the node passes
    dr_drill_force so dr_drill.yml does not wait for dr_drill_hour; the site
    list of drills stays on the template; a missed run is skipped."""
    doc = _definition("dr_drill_schedule.json")
    trigger = doc["triggers"][0]["parameters"]
    assert trigger == {
        "schedule_type": "cron",
        "cron": "0 3 * * *",
        "timezone": "UTC",
        "missed_schedule_policy": "skip",
    }
    params = _node(doc, "dr_drill")["parameters"]
    assert params["extra_vars"] == {"dr_drill_force": True}
    assert "credential_names" not in params
    assert _node(doc, "drills_failed")["parameters"]["condition"] == (
        "${dr_drill.artifacts.failed_count} > 0"
    )
    assert _node(doc, "notify_incident")["parameters"]["extra_vars"]["severity"] == (
        "critical"
    )


def test_trigger_id_is_an_alias_of_trigger(ao_lint):
    """The engine registers the trigger output under trigger and under the
    trigger's id, so ${<trigger id>.array} resolves like ${trigger.array}
    and is checked the same way."""
    doc = _definition("day2_grow_fs.json")
    assert ao_lint.lint_definition(doc) == []
    trigger_id = doc["triggers"][0]["id"]
    node = _node(doc, "notify_requested")
    node["parameters"]["extra_vars"]["target_array"] = "${" + trigger_id + ".array}"
    assert ao_lint.lint_definition(doc) == []
    node["parameters"]["extra_vars"]["target_array"] = (
        "${" + trigger_id + ".payload.array}"
    )
    problems = ao_lint.lint_definition(doc)
    assert [(p.node, p.rule) for p in problems] == [("notify_requested", "trigger_ref")]
    assert "array, file_system, new_size" in problems[0].message
    node["parameters"]["extra_vars"]["target_array"] = "${no_such_trigger.array}"
    problems = ao_lint.lint_definition(doc)
    assert [(p.node, p.rule) for p in problems] == [
        ("notify_requested", "expression_ref")
    ]
    assert "node id, trigger id or namespace" in problems[0].message


def test_trigger_refs_follow_the_trigger_type(ao_lint):
    """Manual inputs and webhook or EDA bodies are both read as
    ${trigger.<top-level key>} (the body is not wrapped), a schedule has
    scheduled_at and triggered_at; the wrapped form, a key the input_schema
    does not have and a payload on a schedule all resolve to nothing at run
    time and lint as trigger_ref."""
    doc = _definition("day2_grow_fs.json")
    extra = _node(doc, "notify_requested")["parameters"]["extra_vars"]
    extra["target_array"] = "${trigger.payload.event.array}"
    problems = ao_lint.lint_definition(doc)
    assert [(p.node, p.rule) for p in problems] == [("notify_requested", "trigger_ref")]
    assert "${trigger.payload.event.array}" in problems[0].message
    assert "array, file_system, new_size" in problems[0].message

    doc = _definition("capacity_reclaim_approval.json")
    _node(doc, "capacity_reclaim")["parameters"]["extra_vars"][
        "array"
    ] = "${trigger.payload.event.array}"
    problems = ao_lint.lint_definition(doc)
    assert [(p.node, p.rule) for p in problems] == [("capacity_reclaim", "trigger_ref")]
    assert "input_schema has event (the body is not wrapped)" in problems[0].message
    # an EDA trigger without an input_schema takes any top-level key
    del doc["triggers"][0]["parameters"]["input_schema"]
    assert ao_lint.lint_definition(doc) == []

    doc = _definition("dr_drill_schedule.json")
    extra = _node(doc, "notify_summary")["parameters"]["extra_vars"]
    extra["detail"] = "ran at ${trigger.scheduled_at}; ${trigger.payload.x}"
    problems = ao_lint.lint_definition(doc)
    assert [(p.node, p.rule) for p in problems] == [("notify_summary", "trigger_ref")]
    assert "${trigger.payload.x}" in problems[0].message
    # a manual trigger without an input_schema takes any name
    doc = _definition("day2_grow_fs.json")
    doc["triggers"][0]["parameters"] = {}
    assert ao_lint.lint_definition(doc) == []


def test_parameter_keys_follow_the_node_schemas(ao_lint):
    """Every node schema has additionalProperties false, so a key it does
    not list (an old parameter name, the controller's names) is refused at
    import; the linter holds the types whose schemas it has read to their
    key sets, and a scheduled or webhook trigger to its required fields."""
    doc = _definition("capacity_reclaim_approval.json")
    _node(doc, "capacity_request")["parameters"]["credentials"] = ["x"]
    _node(doc, "approve_reclaim")["parameters"]["timeout"] = 60
    doc["triggers"][0]["parameters"]["secret"] = "x"
    problems = ao_lint.lint_definition(doc)
    assert sorted((p.node, p.rule) for p in problems) == [
        ("approve_reclaim", "parameter_keys"),
        ("capacity_request", "parameter_keys"),
        ("fb_capacity_event", "parameter_keys"),
    ]
    messages = {p.node: p.message for p in problems}
    assert "has no parameter credentials; it takes" in messages["capacity_request"]
    assert "credential_names" in messages["capacity_request"]
    assert "has no parameter timeout" in messages["approve_reclaim"]
    assert "decision_window" in messages["approve_reclaim"]
    # a type whose schema has not been read is not held to a key set
    doc = _definition("dr_drill_schedule.json")
    doc["nodes"].append(
        {"id": "pause", "type": "wait", "name": "Pause", "parameters": {"anything": 1}}
    )
    doc["edges"].append({"from": "notify_summary", "to": "pause"})
    assert ao_lint.lint_definition(doc) == []

    doc = _definition("dr_drill_schedule.json")
    params = doc["triggers"][0]["parameters"]
    del params["schedule_type"]
    problems = ao_lint.lint_definition(doc)
    assert [(p.node, p.rule) for p in problems] == [("nightly", "trigger_parameters")]
    assert "schedule_type" in problems[0].message
    params["schedule_type"] = "interval"
    problems = ao_lint.lint_definition(doc)
    assert [(p.node, p.rule) for p in problems] == [("nightly", "trigger_parameters")]
    assert "has no interval" in problems[0].message

    doc = _definition("capacity_reclaim_approval.json")
    doc["triggers"][0]["parameters"]["authorized_service_account_ids"] = []
    problems = ao_lint.lint_definition(doc)
    assert [(p.node, p.rule) for p in problems] == [
        ("fb_capacity_event", "trigger_parameters")
    ]
    assert "authorized_service_account_ids" in problems[0].message


def test_node_references_must_name_an_ancestor(ao_lint):
    """${x...} resolves only when x ran before this node: a reference to a
    parallel branch or to a later node is expression_ref."""
    doc = _definition("capacity_reclaim_approval.json")
    _node(doc, "notify_no_candidates")["parameters"]["extra_vars"][
        "detail"
    ] = "${approve_reclaim.status}"
    _node(doc, "notify_pending")["parameters"]["extra_vars"][
        "detail"
    ] = "${capacity_reclaim.artifacts.result}"
    problems = ao_lint.lint_definition(doc)
    assert sorted((p.node, p.rule) for p in problems) == [
        ("notify_no_candidates", "expression_ref"),
        ("notify_pending", "expression_ref"),
    ]
    for problem in problems:
        assert "does not run before this one" in problem.message


def test_unreachable_nodes_portless_edges_and_unparsable_conditions(ao_lint):
    base = json.dumps(_definition("dr_drill_schedule.json"))

    doc = json.loads(base)
    doc["nodes"].append(
        {"id": "orphan", "type": "wait", "name": "Orphan", "parameters": {"seconds": 1}}
    )
    assert [(p.node, p.rule) for p in ao_lint.lint_definition(doc)] == [
        ("orphan", "unreachable")
    ]

    doc = json.loads(base)
    doc["edges"][0]["from_port"] = "fired"  # leaves the trigger
    doc["edges"][1]["from_port"] = "success"  # leaves the job template node
    assert sorted((p.node, p.rule) for p in ao_lint.lint_definition(doc)) == [
        ("dr_drill", "edge_keys"),
        ("nightly", "edge_keys"),
    ]

    rejected = (
        "${dr_drill.artifacts.failed_count} >",
        "${dr_drill.artifacts.failed_count} > 0 && true",
        "len(${dr_drill.artifacts.drills}) > 0",
        "${dr_drill.artifacts.failed_count} + 1 > 1",
        "${dr_drill.artifacts.failed_count} if True else 0",
    )
    for condition in rejected:
        doc = json.loads(base)
        _node(doc, "drills_failed")["parameters"]["condition"] = condition
        problems = ao_lint.lint_definition(doc)
        assert [(p.node, p.rule) for p in problems] == [
            ("drills_failed", "condition_expression")
        ], condition
        assert condition in problems[0].message
    accepted = (
        "${dr_drill.artifacts.failed_count} > 0",
        "${dr_drill.artifacts.status} == 'failed' or not ${dr_drill.artifacts.ok}",
        "${dr_drill.artifacts.status} in ['failed', 'error'] and true",
        "${dr_drill.artifacts.failed_count}",
    )
    for condition in accepted:
        doc = json.loads(base)
        _node(doc, "drills_failed")["parameters"]["condition"] = condition
        assert ao_lint.lint_definition(doc) == [], condition


def test_template_nodes_need_credential_organization_and_a_template(ao_lint):
    """Both AAP node types are checked the same way, by name or by id."""

    def lint(ntype, params):
        doc = {
            "schema_version": "2.0.0",
            "name": "t",
            "description": "t",
            "triggers": [{"id": "start", "type": "manual_trigger", "parameters": {}}],
            "nodes": [
                {"id": "run", "type": ntype, "name": "Run", "parameters": params}
            ],
            "edges": [{"from": "start", "to": "run"}],
        }
        return sorted({p.rule for p in ao_lint.lint_definition(doc, {"ok"})})

    base = {"organization_name": ORGANIZATION, "credential_id": PLACEHOLDER}
    assert lint("aap_job_template", dict(base, job_template_name="ok")) == []
    assert lint("aap_job_template", dict(base, job_template_id=12)) == []
    assert lint("aap_job_template", dict(base, job_template_id="12")) == []
    assert (
        lint("aap_workflow_job_template", dict(base, workflow_job_template_name="ok"))
        == []
    )
    assert (
        lint("aap_workflow_job_template", dict(base, workflow_job_template_id=3)) == []
    )
    # the wrong family's key, a bool or zero id, or no template at all
    assert lint("aap_workflow_job_template", dict(base, job_template_name="ok")) == [
        "job_template"
    ]
    assert lint("aap_job_template", dict(base, job_template_id=True)) == [
        "job_template"
    ]
    assert lint("aap_job_template", dict(base, job_template_id=0)) == ["job_template"]
    assert lint("aap_job_template", base) == ["job_template"]
    # the allowed list applies to whichever name is present
    assert lint(
        "aap_workflow_job_template", dict(base, workflow_job_template_name="x")
    ) == ["job_template_allowed"]
    for missing in ("organization_name", "credential_id"):
        params = dict(base, job_template_name="ok")
        del params[missing]
        assert lint("aap_job_template", params) == ["job_template"], missing
        params[missing] = "  "
        assert lint("aap_job_template", params) == ["job_template"], missing


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_each_fixture_trips_its_rule(path, ao_lint, allowed):
    problems = ao_lint.lint_file(path, allowed)
    rules = {p.rule for p in problems}
    assert _rule_of(path) in rules, [ao_lint.format_problem(p) for p in problems]
    for problem in problems:
        assert problem.file == str(path)
        assert problem.rule in ao_lint.RULES
        assert problem.message


def test_every_rule_has_a_fixture(ao_lint):
    # cap_size needs a 1.5 MB file, which test_oversize_definition builds
    # instead of committing it
    covered = {_rule_of(p) for p in FIXTURES} | {"cap_size"}
    assert set(ao_lint.RULES) <= covered, sorted(set(ao_lint.RULES) - covered)
    assert covered <= set(ao_lint.RULES), sorted(covered - set(ao_lint.RULES))


def test_fixtures_carry_no_other_projects_names():
    """The fixtures are this project's: the one template name they share
    with the definitions is FlashBlade's notify."""
    for path in FIXTURES:
        text = path.read_text()
        assert not re.search("Flash" "Array|pure" "fa", text), path.name


def test_oversize_definition(tmp_path, ao_lint, allowed):
    doc = _definition("dr_drill_schedule.json")
    doc["description"] = "x" * (ao_lint.MAX_BYTES + 1)
    path = tmp_path / "huge.json"
    path.write_text(json.dumps(doc))
    rules = {p.rule for p in ao_lint.lint_file(path, allowed)}
    assert rules == {"cap_size"}


def test_loop_body_returns_over_an_iterate_edge(ao_lint):
    """A loop body hangs off the 'iterate' port and returns over an edge with
    to_port 'iterate', the one back edge the orchestrator's validator treats as
    feedback; 'complete' continues. Anything else is still a cycle."""
    doc = _definition("dr_drill_schedule.json")
    doc["nodes"] += [
        {
            "id": "each",
            "type": "loop",
            "name": "Each",
            "parameters": {"type": "for_each", "items": "${dr_drill.artifacts.drills}"},
        },
        {"id": "body", "type": "wait", "name": "Body", "parameters": {"seconds": 1}},
        {"id": "done", "type": "wait", "name": "Done", "parameters": {"seconds": 1}},
    ]
    doc["edges"] += [
        {"from": "notify_summary", "to": "each"},
        {"from": "each", "to": "body", "from_port": "iterate"},
        {"from": "body", "to": "each", "to_port": "iterate"},
        {"from": "each", "to": "done", "from_port": "complete"},
    ]
    assert ao_lint.lint_definition(doc) == []
    # without the to_port the return is a plain back edge: a cycle and a second input
    plain = json.loads(json.dumps(doc))
    del plain["edges"][-2]["to_port"]
    rules = {p.rule for p in ao_lint.lint_definition(plain)}
    assert rules == {"cycle", "single_input", "loop_ports"}
    # a to_port on an edge into anything but a loop node
    odd = json.loads(json.dumps(doc))
    odd["edges"][-1]["to_port"] = "iterate"
    assert {p.rule for p in ao_lint.lint_definition(odd)} == {"loop_ports"}
    # no complete edge, a return from outside the body, an unknown port
    broken = json.loads(json.dumps(doc))
    broken["edges"][-1]["from_port"] = "finished"
    broken["edges"].append({"from": "done", "to": "each", "to_port": "iterate"})
    messages = [
        p.message for p in ao_lint.lint_definition(broken) if p.rule == "loop_ports"
    ]
    assert len(messages) == 3 and any("'complete'" in m for m in messages)
    assert any("unknown port finished" in m for m in messages)
    assert any("'done'" in m for m in messages)
    # the loop node's parameter keys follow the schema
    odd_param = json.loads(json.dumps(doc))
    odd_param["nodes"][-3]["parameters"]["overlap"] = "skip"
    assert {p.rule for p in ao_lint.lint_definition(odd_param)} == {"parameter_keys"}


def test_problem_line_format(ao_lint):
    problem = ao_lint.Problem(
        "orchestrator/x.json",
        "gate",
        "approval_ports",
        "no edge leaves port 'rejected'",
    )
    assert (
        ao_lint.format_problem(problem)
        == "orchestrator/x.json:gate:approval_ports:no edge leaves port 'rejected'"
    )


def _cli(*args):
    return subprocess.run(
        [sys.executable, "-I", "tools/ao_lint.py", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_passes_the_shipped_definitions():
    result = _cli(
        "orchestrator/*.json", "--allowed-templates", "orchestrator/job_templates.txt"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"ao_lint: {len(DEFINITIONS)} definition(s) ok"


def test_cli_reports_one_line_per_problem_and_fails():
    result = _cli(
        "tests/fixtures/orchestrator/bad_cycle.json",
        "tests/fixtures/orchestrator/bad_approval_ports.json",
    )
    assert result.returncode == 1
    lines = result.stdout.splitlines()
    assert lines, result.stderr
    for line in lines:
        match = PROBLEM_LINE.match(line)
        assert match, line
        assert match["file"].startswith("tests/fixtures/orchestrator/bad_")
    assert {PROBLEM_LINE.match(line)["rule"] for line in lines} == {
        "cycle",
        "approval_ports",
    }
    assert "2 problem(s) in 2 definition(s)" in result.stderr


def test_cli_usage_errors(tmp_path):
    assert _cli("orchestrator/does_not_exist_*.json").returncode == 2
    assert (
        _cli(
            "orchestrator/dr_drill_schedule.json",
            "--allowed-templates",
            str(tmp_path / "none.txt"),
        ).returncode
        == 2
    )
    unreadable = tmp_path / "gone.json"
    result = _cli(str(unreadable))
    assert result.returncode == 1
    assert ":json:" in result.stdout


def test_allowed_templates_file_ignores_comments_and_blanks(tmp_path, ao_lint):
    listing = tmp_path / "templates.txt"
    listing.write_text("# comment\n\nFlashBlade - notify\n  FlashBlade - DR drill  \n")
    assert ao_lint.load_allowed_templates(listing) == {
        "FlashBlade - notify",
        "FlashBlade - DR drill",
    }


def test_linter_is_standard_library_only():
    text = LINTER.read_text()
    imported = set(re.findall(r"^(?:import|from)\s+([a-z_]+)", text, re.MULTILINE))
    assert imported <= {
        "argparse",
        "ast",
        "collections",
        "glob",
        "ipaddress",
        "json",
        "pathlib",
        "re",
        "sys",
        "urllib",
    }
    assert "SPDX-License-Identifier: Apache-2.0" in text
    assert shutil.which(sys.executable)
