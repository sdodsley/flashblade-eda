# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Static checks over the rulebooks: they validate against ansible-rulebook's
own schema, name their sources by FQCN, every variable they reference is in
vars/example.yml, every playbook they run exists and receives the extra vars
it needs, nothing from the FlashArray project is left behind, and the replay
copies in tests/ have not drifted from the real rules."""

import json
import pathlib
import re

import pytest
import yaml

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

RULEBOOKS = sorted(pathlib.Path("extensions/eda/rulebooks").glob("*.yml"))
REPLAYS = sorted(pathlib.Path("tests").glob("replay_*.yml"))
VAR_REF = re.compile(r"\bvars\.([a-z_][a-z0-9_]*)")
JINJA_REF = re.compile(r"{{\s*([a-z_][a-z0-9_]*)\b")
SOURCES = {
    "sdodsley.flashblade_eda.purefb_events",
    "sdodsley.flashblade_eda.purefb_syslog",
    "ansible.eda.webhook",
    "ansible.eda.alertmanager",
    "ansible.eda.generic",
}

# playbook -> the extra vars every rule that runs it must pass. The entries
# are what the playbook reads without a default; a rule may pass more.
REQUIRED_EXTRA_VARS = {
    "playbooks/notify.yml": {"severity", "subject", "target_array", "alert_log"},
    "playbooks/alert_runbook.yml": {
        "severity",
        "subject",
        "target_array",
        "alert_id",
        "alert_code",
        "alert_runbooks",
        "alert_log",
    },
    "playbooks/flag_alert.yml": {"target_array", "alert_id", "alert_log"},
    "playbooks/identify_led.yml": {
        "target_array",
        "component",
        "component_type",
        "component_status",
        "led_state",
        "alert_log",
    },
    "playbooks/reconcile.yml": {
        "target_array",
        "baseline_dir",
        "audit_user",
        "audit_command",
        "alert_log",
    },
    "playbooks/remediate_ntp.yml": {
        "target_array",
        "baseline_ntp_servers_csv",
        "alert_log",
    },
    "playbooks/remediate_dns.yml": {
        "target_array",
        "baseline_dns_domain",
        "baseline_dns_nameservers_csv",
        "alert_log",
    },
    "playbooks/apply_baseline.yml": {"baseline_dir", "alert_log"},
    "playbooks/capacity_request.yml": {"at_risk_array", "request_dir", "alert_log"},
    "playbooks/capacity_reclaim.yml": {
        "request_id",
        "reclaim_action",
        "request_dir",
        "alert_log",
    },
    "playbooks/orchestrator_post.yml": {
        "orchestrator_url",
        "orchestrator_webhook_path",
        "event",
        "array",
        "alert_log",
    },
    "playbooks/grow_fs.yml": {
        "target_array",
        "file_system",
        "fs_grow_step_pct",
        "fs_grow_max_bytes",
        "fs_grow_exclude",
        "request_dir",
        "alert_log",
    },
    "playbooks/user_quota.yml": {
        "target_array",
        "alert_id",
        "alert_code",
        "alert_variables",
        "component_name",
        "auto_raise_user_quota",
        "user_quota_step_pct",
        "user_quota_max_bytes",
        "alert_log",
    },
    "playbooks/client_diagnose.yml": {"target_array", "client", "alert_log"},
    "playbooks/qos_cap.yml": {
        "target_array",
        "object_kind",
        "object_name",
        "cap_ops_per_sec",
        "alert_log",
    },
    "playbooks/qos_lift.yml": {
        "target_array",
        "object_kind",
        "object_name",
        "alert_log",
    },
    "playbooks/maintenance_window.yml": {
        "target_array",
        "window_action",
        "window_hours",
        "alert_log",
    },
    "playbooks/session_check.yml": {
        "target_array",
        "session_user",
        "session_location",
        "admin_cidrs",
        "alert_log",
    },
}

# every polling rulebook starts with these three rules, in this order
POLL_ERROR_RULES = [
    "Poll failing for many polls - escalate",
    "Poll failed - notify once",
    "Report poll errors without acting",
]


def _rulebook_schema():
    try:
        import ansible_rulebook
    except ImportError:
        return None
    for candidate in pathlib.Path(ansible_rulebook.__file__).parent.rglob(
        "ruleset_schema.json"
    ):
        return json.loads(candidate.read_text())
    return None


SCHEMA = _rulebook_schema()


def _runs(rule):
    """Every run_playbook of a rule, whether it has one action or a list."""
    actions = rule.get("actions") or [rule.get("action", {})]
    return [a["run_playbook"] for a in actions if "run_playbook" in a]


@pytest.mark.parametrize("path", RULEBOOKS + REPLAYS, ids=lambda p: p.name)
def test_rulebook_matches_the_ansible_rulebook_schema(path):
    if SCHEMA is None or jsonschema is None:
        pytest.skip("ansible-rulebook or jsonschema not installed")
    doc = yaml.safe_load(path.read_text())
    jsonschema.validate(doc, SCHEMA)


@pytest.mark.parametrize("path", RULEBOOKS + REPLAYS, ids=lambda p: p.name)
def test_every_source_is_named_by_fqcn(path):
    doc = yaml.safe_load(path.read_text())
    for ruleset in doc:
        for source in ruleset["sources"]:
            names = [k for k in source if k not in ("name", "filters")]
            assert len(names) == 1, f"{path.name}: source {source}"
            assert names[0] in SOURCES, f"{path.name}: unknown source {names[0]}"


def _example_vars(repo_root):
    return set(yaml.safe_load((repo_root / "vars" / "example.yml").read_text()))


@pytest.mark.parametrize("path", RULEBOOKS, ids=lambda p: p.name)
def test_every_referenced_var_is_documented(path, repo_root):
    text = path.read_text()
    known = _example_vars(repo_root)
    referenced = set(VAR_REF.findall(text))
    # {{ name }} at the top level of a rulebook is a vars-file key too, except
    # event/events/fact(s) and the loop-ish names rulebooks use
    for name in JINJA_REF.findall(text):
        if name not in ("event", "events", "fact", "facts", "vars"):
            referenced.add(name)
    missing = sorted(referenced - known)
    assert (
        not missing
    ), f"{path.name} references vars not in vars/example.yml: {missing}"


@pytest.mark.parametrize("path", RULEBOOKS + REPLAYS, ids=lambda p: p.name)
def test_every_playbook_a_rule_runs_exists_and_gets_its_extra_vars(path, repo_root):
    doc = yaml.safe_load(path.read_text())
    for ruleset in doc:
        for rule in ruleset.get("rules", []):
            for run in _runs(rule):
                name = run["name"]
                assert (
                    repo_root / name
                ).exists(), f"{path.name}: {name} does not exist"
                assert (
                    name in REQUIRED_EXTRA_VARS
                ), f"{path.name}: {name} not in the table"
                passed = set(run.get("extra_vars", {}))
                missing = REQUIRED_EXTRA_VARS[name] - passed
                assert (
                    not missing
                ), f"{path.name}: '{rule['name']}' lacks {sorted(missing)}"


def test_every_playbook_is_in_the_extra_vars_table(repo_root):
    """A playbook nobody lists is one no rule can be checked against."""
    playbooks = {f"playbooks/{p.name}" for p in (repo_root / "playbooks").glob("*.yml")}
    assert playbooks == set(REQUIRED_EXTRA_VARS), sorted(
        playbooks ^ set(REQUIRED_EXTRA_VARS)
    )


@pytest.mark.parametrize("path", RULEBOOKS, ids=lambda p: p.name)
def test_polling_rulesets_start_with_the_poll_error_rules(path):
    """Every ruleset fed by purefb_events opens with the poll-error trio: an
    array nobody can reach is reported once, escalated once, logged after."""
    doc = yaml.safe_load(path.read_text())
    for ruleset in doc:
        polls = any(
            "sdodsley.flashblade_eda.purefb_events" in s for s in ruleset["sources"]
        )
        names = [r["name"] for r in ruleset.get("rules", [])]
        if polls:
            assert names[:3] == POLL_ERROR_RULES, f"{path.name}: {ruleset['name']}"
            first = ruleset["rules"][1]["action"]["run_playbook"]["extra_vars"]
            expected = "warning" if path.name == "health.yml" else "info"
            assert first["severity"] == expected, f"{path.name}: first-failure severity"
        else:
            assert not set(names) & set(
                POLL_ERROR_RULES
            ), f"{path.name}: {ruleset['name']}"


def test_rulebook_and_playbook_tree_has_no_flasharray_leftovers(repo_root):
    files = (
        list((repo_root / "extensions" / "eda" / "rulebooks").glob("*.yml"))
        + list((repo_root / "playbooks").rglob("*.yml"))
        + list((repo_root / "vars").glob("example.yml"))
        + list((repo_root / "inventory").glob("example.yml"))
        + list((repo_root / "tests").glob("*.yml"))
        + list((repo_root / "tests" / "fixtures").glob("*.yml"))
    )
    leftovers = re.compile(
        r"purefa_|fa_url|ARRAY_[AB]_|flasharray|protection group|ActiveCluster|\bpods?\b|\bvolumes?\b",
        re.IGNORECASE,
    )
    offenders = [
        f"{p.relative_to(repo_root)}:{i}: {line.strip()}"
        for p in files
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if leftovers.search(line)
    ]
    assert offenders == [], "\n".join(offenders)


def _rules_by_name(path):
    out = {}
    for ruleset in yaml.safe_load(path.read_text()):
        for rule in ruleset.get("rules", []):
            out[rule["name"]] = rule
    return out


@pytest.mark.parametrize("replay", REPLAYS, ids=lambda p: p.name)
def test_replay_rules_match_the_real_rulebook(replay):
    """tests/replay_<x>.yml copies rules from extensions/eda/rulebooks/<x>.yml
    (ansible-rulebook has no include). Every copied rule must be identical,
    throttle included: each fixture hits a throttled rule once with its own
    key and every run is a fresh process, so nothing is suppressed and the
    throttle form gets validated too."""
    real = pathlib.Path("extensions/eda/rulebooks") / replay.name.replace("replay_", "")
    assert real.exists(), f"no real rulebook for {replay.name}"
    real_rules = _rules_by_name(real)
    for name, rule in _rules_by_name(replay).items():
        assert name in real_rules, f"{replay.name}: rule '{name}' is not in {real.name}"
        assert (
            rule == real_rules[name]
        ), f"{replay.name}: rule '{name}' has drifted from {real.name}"
