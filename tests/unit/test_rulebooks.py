# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Static checks over the rulebooks: they validate against ansible-rulebook's
own schema, name their sources by FQCN, every variable they reference is in
vars/example.yml, every playbook they run exists and receives the extra vars
it needs, nothing from the FlashArray project is left behind, the replay
copies in tests/ have not drifted from the real rules, and every action
string built from an event field is one expression that hands the playbook
the field's value as it was."""

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
    "sdodsley.flashblade_eda.purefb_object_audit",
    "sdodsley.flashblade_eda.purefb_syslog",
    "ansible.eda.webhook",
    "ansible.eda.alertmanager",
    "ansible.eda.generic",
    "ansible.eda.tick",
}
# the sources that poll an array and emit purefb_poll_error
POLLING_SOURCES = (
    "sdodsley.flashblade_eda.purefb_events",
    "sdodsley.flashblade_eda.purefb_object_audit",
)

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
    "playbooks/canary.yml": {"target_array", "access_key_id", "alert_log"},
    "playbooks/s3_anomaly.yml": {"target_array", "access_key_id", "kind", "alert_log"},
    "playbooks/landing.yml": {"target_array", "bucket", "key", "alert_log"},
    "playbooks/lock_on_arrival.yml": {
        "target_array",
        "bucket",
        "key",
        "object_store",
        "alert_log",
    },
    "playbooks/s3_approval.yml": {
        "target_array",
        "request_id",
        "approver_key",
        "object_store",
        "request_dir",
        "alert_log",
    },
    "playbooks/auditor_guard.yml": {
        "target_array",
        "mode",
        "audit_baseline",
        "alert_log",
    },
    "playbooks/posture_revert.yml": {"target_array", "buckets", "alert_log"},
    "playbooks/telemetry_check.yml": {
        "target_array",
        "alert",
        "telemetry_target_name",
        "alert_log",
    },
    "playbooks/telemetry_setup.yml": {
        "target_array",
        "telemetry_target_name",
        "telemetry_endpoint",
        "telemetry_source",
        "telemetry_transport",
        "telemetry_policies",
        "alert_log",
    },
    # the exclusion lists have defaults in the playbook, but a rule that
    # left them out would quarantine the automation's own hosts
    "playbooks/quarantine_client.yml": {
        "target_array",
        "client",
        "action",
        "contain_exclude_clients",
        "contain_exclude_file_systems",
        "alert_log",
    },
    "playbooks/tarpit.yml": {
        "target_array",
        "file_system",
        "action",
        "tarpit_ops_per_sec",
        "alert_log",
    },
    "playbooks/release_locks.yml": {"target_array", "client", "approver", "alert_log"},
    "playbooks/replication_resume.yml": {"target_array", "bucket", "alert_log"},
    "playbooks/dr_drill.yml": {
        "dr_drills",
        "dr_drill_hour",
        "object_store",
        "showback_prefix",
        "alert_log",
    },
    "playbooks/showback.yml": {
        "target_array",
        "object_store",
        "showback_bucket",
        "showback_prefix",
        "alert_log",
    },
    "playbooks/selftest.yml": {
        "target_array",
        "object_store",
        "selftest_checks",
        "audit_baseline",
        "telemetry_target_name",
        "telemetry_endpoint",
        "prometheus_url",
        "alert_log",
    },
    "playbooks/renew_certificate.yml": {
        "target_array",
        "certificate",
        "certs_renew_days",
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
    """Every ruleset fed by a polling source (purefb_events,
    purefb_object_audit) opens with the poll-error trio: an array nobody can
    reach is reported once, escalated once, logged after."""
    doc = yaml.safe_load(path.read_text())
    for ruleset in doc:
        polls = any(
            source in s for s in ruleset["sources"] for source in POLLING_SOURCES
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


def test_housekeeping_runs_every_rule_on_each_tick(repo_root):
    """housekeeping.yml has one tick source and matches every rule per tick
    (match_multiple_rules), so the selftest, the showback and the drills
    all run from the same clock; the playbooks decide by hour and date."""
    doc = yaml.safe_load(
        (
            repo_root / "extensions" / "eda" / "rulebooks" / "housekeeping.yml"
        ).read_text()
    )
    assert len(doc) == 1
    ruleset = doc[0]
    assert ruleset.get("match_multiple_rules") is True
    assert [list(s)[0] for s in ruleset["sources"]] == ["ansible.eda.tick"]
    names = [r["name"] for r in ruleset["rules"]]
    assert names == ["Hourly selftest", "Daily showback", "DR drills"]
    for rule in ruleset["rules"]:
        assert rule["condition"].startswith("event.i is defined"), rule["name"]
    # the fan-out playbooks get every polled array as one host pattern
    for rule in ruleset["rules"][:2]:
        target = rule["action"]["run_playbook"]["extra_vars"]["target_array"]
        assert target == "{{ purefb_arrays | map(attribute='name') | join(',') }}"


def test_containment_acts_only_behind_its_switches(repo_root):
    """Every acting rule of contain.yml's telemetry ruleset is gated by
    ir_auto_contain or ir_auto_tarpit, the lift reads the plugin's
    eda_tarpit flag (not eda_capped, which is client.yml's cap), and the
    approvals ruleset is a token-protected webhook on locks_port."""
    doc = yaml.safe_load(
        (repo_root / "extensions" / "eda" / "rulebooks" / "contain.yml").read_text()
    )
    telemetry, approvals = doc
    acting = {
        r["name"]: r
        for r in telemetry["rules"]
        if r["name"] not in POLL_ERROR_RULES
        and r["action"]["run_playbook"]["name"] != "playbooks/notify.yml"
    }
    assert set(acting) == {
        "Client holding the array - quarantine",
        "File system hogging - tarpit",
        "Tarpitted file system calm - lift",
    }
    assert (
        "vars.ir_auto_contain == true"
        in acting["Client holding the array - quarantine"]["condition"]
    )
    assert (
        "vars.ir_auto_tarpit == true"
        in acting["File system hogging - tarpit"]["condition"]
    )
    lift = acting["Tarpitted file system calm - lift"]["condition"]
    assert "eda_tarpit == true" in lift and "eda_capped" not in lift
    assert "vars.contain_release_normal_polls" in lift
    source = approvals["sources"][0]["ansible.eda.webhook"]
    assert source["port"] == "{{ locks_port }}"
    assert source["token"] == "{{ APPROVAL_TOKEN }}"
    actions = {
        r["action"]["run_playbook"]["name"]
        for r in approvals["rules"]
        if "run_playbook" in r["action"]
    }
    assert actions == {
        "playbooks/quarantine_client.yml",
        "playbooks/release_locks.yml",
        "playbooks/tarpit.yml",
    }


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


def test_syslog_alert_templates_never_print_none():
    """An alert line outside the layout arrives with every alert field None
    and the severity from the PRI; the notify templates, rendered the way
    ansible-rulebook renders an action's arguments, then carry the message,
    never the word None (Jinja's default() leaves a None alone unless told
    otherwise)."""
    util = pytest.importorskip("ansible_rulebook.util")
    alert = dict.fromkeys(
        (
            "severity",
            "state",
            "subject",
            "code",
            "details",
            "action",
            "knowledge_base_url",
            "first_seen",
            "first_seen_array",
            "alert_id",
            "array_id",
            "purity_version",
            "host",
            "syslog_time",
            "program",
            "syslog_severity",
            "facility",
            "protocol",
            "raw",
        )
    )
    alert.update(
        array="fb-a",
        severity="critical",
        message="CRITICAL Waiting To Downgrade - fb-a: something new",
        time="Nov 16 17:02:39",
        source="192.0.2.10",
    )
    for path in (
        pathlib.Path("extensions/eda/rulebooks/syslog.yml"),
        pathlib.Path("extensions/eda/rulebooks/platform/syslog.yml"),
    ):
        rulebook = yaml.safe_load(path.read_text(encoding="utf-8"))
        rule = next(
            r for r in rulebook[0]["rules"] if r["name"] == "Alert via syslog - notify"
        )
        action = rule["action"]
        extra = (
            action["run_playbook"]["extra_vars"]
            if "run_playbook" in action
            else action["run_job_template"]["job_args"]["extra_vars"]
        )
        rendered = util.substitute_variables(
            extra,
            {"event": {"purefb_syslog_alert": alert}, "alert_log": "/tmp/alerts.log"},
        )
        for key, value in rendered.items():
            text = str(value)
            assert "None" not in text, f"{path.name} {key}: {text!r}"
            if key == "subject":
                assert text.startswith("fb-a alert:") and alert["message"] in text


def test_syslog_alerts_are_graded_by_state():
    """The array repeats the alert's severity word on every state change, so
    a closing alert arrives as CRITICAL: the syslog rulebook notifies at the
    alert's severity for an open alert only, gives a closed one a notice at
    info and logs closing and waiting to downgrade, as alerts.yml does for
    the REST transitions."""
    for path in (
        pathlib.Path("extensions/eda/rulebooks/syslog.yml"),
        pathlib.Path("extensions/eda/rulebooks/platform/syslog.yml"),
    ):
        rules = {
            r["name"]: r
            for r in yaml.safe_load(path.read_text(encoding="utf-8"))[0]["rules"]
        }
        notify = rules["Alert via syslog - notify"]["condition"]
        assert 'severity in ["warning", "critical"]' in notify
        assert 'state not in ["closing", "closed", "waiting to downgrade"]' in notify
        closed = rules["Alert closed via syslog - notify"]
        assert 'event.purefb_syslog_alert.state == "closed"' in closed["condition"]
        action = closed["action"]
        extra = (
            action["run_playbook"]["extra_vars"]
            if "run_playbook" in action
            else action["run_job_template"]["job_args"]["extra_vars"]
        )
        assert extra["severity"] == "info" and " closed:" in extra["subject"]
        closing = rules["Alert closing via syslog - log"]
        assert 'state in ["closing", "waiting to downgrade"]' in closing["condition"]
        assert "debug" in closing["action"]


def test_syslog_open_alerts_are_notified_once_a_day():
    """The array repeats an open alert's line every quarter hour; the syslog
    rulebook holds the notification for a day per alert, keyed by the
    alert's first-seen time so a re-opened alert is notified again."""
    for path in (
        pathlib.Path("extensions/eda/rulebooks/syslog.yml"),
        pathlib.Path("extensions/eda/rulebooks/platform/syslog.yml"),
    ):
        rules = {
            r["name"]: r
            for r in yaml.safe_load(path.read_text(encoding="utf-8"))[0]["rules"]
        }
        throttle = rules["Alert via syslog - notify"]["throttle"]
        assert throttle["once_within"] == "24 hours"
        assert throttle["group_by_attributes"] == [
            "event.purefb_syslog_alert.array",
            "event.purefb_syslog_alert.code",
            "event.purefb_syslog_alert.subject",
            "event.purefb_syslog_alert.first_seen",
        ]


# Event text is data: the project's own sources emit plain text themselves
# and every stock source runs this filter, which keeps the template
# delimiters in every string of an event inert before a rule reads it.
JINJA_SAFE_FILTER = "sdodsley.flashblade_eda.jinja_safe"
OWN_SOURCE_PREFIX = "sdodsley.flashblade_eda.purefb_"
JINJA_SAFE_FILE = pathlib.Path("extensions/eda/plugins/event_filter/jinja_safe.py")

# The shape a sender-chosen value must have before a rule passes it to a
# playbook's hosts line or as an object name. The condition compares the
# attribute and never renders it, so a value of another shape starts nothing.
NAME_RE = "^[A-Za-z0-9][A-Za-z0-9_.-]*$"  # array and host names, label values
FS_RE = "^[A-Za-z0-9][A-Za-z0-9_.:-]*$"  # file system and bucket names
ID_RE = "^[A-Za-z0-9][A-Za-z0-9._-]*$"  # request ids
IP_RE = "^[0-9A-Fa-f.:]+$"  # client addresses, IPv4 or IPv6
REF_RE = "^refs/[A-Za-z0-9/_.-]+$"  # a git ref
SHA_RE = "^[0-9a-f]{40,64}$"  # a git commit id


def _pin(attribute, regex):
    """The condition fragment that accepts only a well-formed value."""
    return f'{attribute} is match("{regex}")'


# (rulebook, rule name, fragments its condition must contain). alertmanager's
# "Telemetry silent without an array label - notify" is not here: it fires
# when the label is absent and passes an empty target.
PINNED_CONDITIONS = [
    (
        "contain.yml",
        "Approved quarantine or release - apply it",
        [_pin("event.payload.array", NAME_RE), _pin("event.payload.client", IP_RE)],
    ),
    (
        "contain.yml",
        "Approved lock and session release - run it",
        [_pin("event.payload.array", NAME_RE), _pin("event.payload.client", IP_RE)],
    ),
    (
        "contain.yml",
        "Approved tarpit - apply it",
        [
            _pin("event.payload.array", NAME_RE),
            _pin("event.payload.file_system", FS_RE),
        ],
    ),
    (
        "contain.yml",
        "Approved lift - apply it",
        [
            _pin("event.payload.array", NAME_RE),
            _pin("event.payload.file_system", FS_RE),
        ],
    ),
    (
        "capacity.yml",
        "Approved request - reclaim",
        [_pin("event.payload.request_id", ID_RE)],
    ),
    (
        "maintenance.yml",
        "Window request - open, extend or close it",
        [_pin("event.payload.array", NAME_RE)],
    ),
    (
        "change_control.yml",
        "Push to the baseline repository - apply the baseline to every array",
        [_pin("event.payload.ref", REF_RE), _pin("event.payload.after", SHA_RE)],
    ),
    # every alertmanager rule whose action names the array from the alert's label
    (
        "alertmanager.yml",
        "Telemetry silent - check the pipeline",
        [_pin("event.alert.labels.array_name", NAME_RE)],
    ),
    (
        "alertmanager.yml",
        "Health not ok - notify",
        [_pin("event.alert.labels.array_name", NAME_RE)],
    ),
    (
        "alertmanager.yml",
        "Capacity threshold - approval request",
        [_pin("event.alert.labels.array_name", NAME_RE)],
    ),
    (
        "alertmanager.yml",
        "Latency, connector errors or stalled replication - notify",
        [_pin("event.alert.labels.array_name", NAME_RE)],
    ),
    (
        "alertmanager.yml",
        "Any other firing FlashBlade alert - notify",
        [_pin("event.alert.labels.array_name", NAME_RE)],
    ),
    (
        "alertmanager.yml",
        "Resolved - log",
        [_pin("event.alert.labels.array_name", NAME_RE)],
    ),
    # the syslog rules that run notify.yml: the array attribute of their event
    (
        "syslog.yml",
        "Alert via syslog - notify",
        [_pin("event.purefb_syslog_alert.array", NAME_RE)],
    ),
    (
        "syslog.yml",
        "Alert closed via syslog - notify",
        [_pin("event.purefb_syslog_alert.array", NAME_RE)],
    ),
    (
        "syslog.yml",
        "Human action via syslog audit - fast notification",
        [_pin("event.purefb_syslog_audit.array", NAME_RE)],
    ),
    (
        "syslog.yml",
        "File activity over threshold - notify",
        [_pin("event.purefb_file_activity.array", NAME_RE)],
    ),
    # the landing ingest runs only where the deployment turns it on
    (
        "object_audit.yml",
        "Landing marker arrived - start the ingest",
        ["vars.landing_enabled == true"],
    ),
]


@pytest.mark.parametrize("path", RULEBOOKS + REPLAYS, ids=lambda p: p.name)
def test_every_source_runs_the_jinja_safe_filter(path, repo_root):
    """Every stock source of every ruleset runs the jinja_safe filter, so a
    template delimiter in any string of an event is inert by the time a rule
    reads it or hands it to a playbook; the project's own sources emit plain
    text themselves and carry no filters line (each FQCN costs the activation
    an ansible-galaxy lookup at start-up). The replays' generic sources run
    the filter too, since their fixtures stand in for the real sources."""
    assert (repo_root / JINJA_SAFE_FILE).exists(), f"{JINJA_SAFE_FILE} is missing"
    doc = yaml.safe_load(path.read_text())
    for ruleset in doc:
        for source in ruleset.get("sources") or []:
            kind = [k for k in source if k not in ("name", "filters")][0]
            where = f"{path.name}: {ruleset['name']} source {kind}"
            if kind.startswith(OWN_SOURCE_PREFIX):
                assert "filters" not in source, f"{where} carries a filters line"
                continue
            filters = source.get("filters")
            assert isinstance(filters, list), f"{where} has no filters"
            assert any(
                list(f) == [JINJA_SAFE_FILTER] for f in filters
            ), f"{where} does not run {JINJA_SAFE_FILTER}"


def test_sender_named_fields_are_pinned_in_the_rule(repo_root):
    """A value the sender of an event chooses (a webhook body's array, client,
    file system, request id, ref or commit; an alert's array_name label; the
    array a syslog line names) reaches a playbook's hosts line or an object
    name only after the rule's condition has matched it against the shape
    such a name has, and the landing ingest only behind its switch."""
    rulebooks = repo_root / "extensions" / "eda" / "rulebooks"
    missing = []
    for file, rule_name, fragments in PINNED_CONDITIONS:
        rules = _rules_by_name(rulebooks / file)
        assert rule_name in rules, f"{file}: no rule named '{rule_name}'"
        condition = rules[rule_name]["condition"]
        assert isinstance(condition, str), f"{file}: '{rule_name}' condition"
        missing.extend(
            f"{file}: '{rule_name}' lacks {fragment}"
            for fragment in fragments
            if fragment not in condition
        )
    assert missing == [], "\n".join(missing)


# An action string that opens with an event field is one Jinja expression
# ending in pprint: the rule's render then hands the playbook the field's
# value as it was, text as text and a number as a number. A string that
# opens with a literal word is left as written.
PLAYBOOK_ACTIONS = ("run_playbook", "run_job_template")
EVENT_WORD = re.compile(r"\b(event|events|fact|facts)\b")
EVENT_FIELD = re.compile(r"\b(event|events|fact|facts)((?:\.[A-Za-z_][A-Za-z0-9_]*)+)")
# a literal opening: a word (letters, digits, underscores) and what follows it
LITERAL_START = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?=[ =:(/,;-])")
PRINTED = "| pprint }}"
# the one-field forms: the field alone, or the field with a default
SINGLE_FIELD = re.compile(
    r"^{{ (?P<field>(?:event|events|fact|facts)(?:\.[A-Za-z_][A-Za-z0-9_]*)+)"
    r"(?: \| default\((?P<default>[^|]*)\))? \| pprint }}$"
)
# a field deliberately handed over as a number or a boolean
CAST = re.compile(r"\| (int|float|bool) \| pprint }}$")
CAST_TYPES = {"int": int, "float": float, "bool": bool}
# text an event may carry: a Python literal in several spellings, numbers,
# the names of the constants, words, nothing
TEXT_VALUES = [
    "'{' '{ 7 * 7 }}'",
    "'\\x7b\\x7b 6 * 7 }}'",
    "['{' '{ 5 * 5 }}']",
    "85.2",
    "007",
    "True",
    "None",
    "plain text",
    "",
]
# values of another type a one-field string must hand over unchanged
TYPED_VALUES = [5, 5.5, True, None, [1, "a"], {"b": "c"}]


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


def _playbook_action_strings(path):
    """(rule name, string) for every string of every run_playbook and
    run_job_template action in the file, whitespace flattened. A debug
    action's message is the rulebook's own output and is not here."""
    out = []
    for ruleset in yaml.safe_load(path.read_text(encoding="utf-8")):
        for rule in ruleset.get("rules", []):
            for action in rule.get("actions") or [rule.get("action", {})]:
                for kind in PLAYBOOK_ACTIONS:
                    if kind in action:
                        out.extend(
                            (rule["name"], " ".join(text.split()))
                            for text in _strings(action[kind])
                        )
    return out


def _event_strings(path):
    """The action strings that template an event field."""
    return [
        (rule, text)
        for rule, text in _playbook_action_strings(path)
        if "{{" in text and EVENT_WORD.search(text)
    ]


@pytest.mark.parametrize("path", RULEBOOKS + REPLAYS, ids=lambda p: p.name)
def test_an_action_string_that_opens_with_an_event_field_is_one_expression(path):
    """A run_playbook or run_job_template string that opens with an event
    field is exactly one expression ending in pprint, with no second
    expression and no statement; any other string that templates an event
    field opens with a literal word (not True, False or None)."""
    wrong = []
    for rule, text in _event_strings(path):
        where = f"{path.name}: '{rule}': {text}"
        if text.startswith("{{"):
            one = text.endswith(PRINTED) and text.count("{{") == 1 and "{%" not in text
            if not one:
                wrong.append(f"{where} is not one expression ending in pprint")
        else:
            word = LITERAL_START.match(text)
            if word is None or word.group(0) in ("True", "False", "None"):
                wrong.append(f"{where} does not open with a word")
    assert wrong == [], "\n".join(wrong)


def _context(text, value, base):
    """A render context for one action string: the rulebook vars and every
    event field the string names set to the value. The longer paths go
    first, so a field that is also a branch of another stays a branch."""
    context = dict(base)
    paths = {match.group(1) + match.group(2) for match in EVENT_FIELD.finditer(text)}
    for path in sorted(paths, key=lambda p: -p.count(".")):
        *branch, leaf = path.split(".")
        node = context
        for key in branch:
            node = node.setdefault(key, {})
        if leaf not in node:
            node[leaf] = value
    return context


def _render(substitute, text, value, base):
    """(rendered value, None) or (None, the error) for one string."""
    try:
        return substitute(text, _context(text, value, base)), None
    except Exception as error:
        return None, f"{type(error).__name__}: {error}"


@pytest.mark.parametrize("path", RULEBOOKS, ids=lambda p: p.name)
def test_an_event_field_reaches_the_action_as_the_value_it_was(path, repo_root):
    """Rendered the way ansible-rulebook renders an action's arguments, every
    string that opens with an event field hands the playbook text as text,
    whatever the text spells: a one-field string gives the value back equal
    and of the same type, a field cast to a number stays that number, and a
    composed string is text."""
    util = pytest.importorskip("ansible_rulebook.util")
    base = yaml.safe_load((repo_root / "vars" / "example.yml").read_text("utf-8"))
    wrong = []
    for rule, text in _event_strings(path):
        if not text.startswith("{{"):
            continue
        single = SINGLE_FIELD.match(text)
        cast = CAST.search(text)
        # a boolean default swaps a falsy value for the default
        default = single.group("default") if single else None
        swaps_falsy = default is not None and default.rstrip().endswith("true")
        values = TEXT_VALUES + (TYPED_VALUES if single else [])
        for value in values:
            if swaps_falsy and not value:
                continue
            rendered, error = _render(util.substitute_variables, text, value, base)
            where = f"{path.name}: '{rule}': {text} with {value!r}"
            if error:
                wrong.append(f"{where} raised {error}")
            elif single:
                if rendered != value or type(rendered) is not type(value):
                    wrong.append(f"{where} came back as {rendered!r}")
            elif cast:
                wanted = CAST_TYPES[cast.group(1)]
                if not isinstance(rendered, wanted) or (
                    wanted is int and isinstance(rendered, bool)
                ):
                    wrong.append(f"{where} came back as {rendered!r}, not {wanted}")
            elif not isinstance(rendered, str):
                wrong.append(f"{where} came back as {rendered!r}, not text")
    assert wrong == [], "\n".join(wrong)


@pytest.mark.parametrize("path", RULEBOOKS + REPLAYS, ids=lambda p: p.name)
def test_no_two_expressions_touch_in_an_action_string(path):
    """Two expressions side by side would hand the playbook their values
    joined into one; a literal word or a space keeps them apart."""
    touching = [
        f"{path.name}: '{rule}': {text}"
        for rule, text in _playbook_action_strings(path)
        if "}}{{" in text
    ]
    assert touching == [], "\n".join(touching)


@pytest.mark.parametrize("path", RULEBOOKS + REPLAYS, ids=lambda p: p.name)
def test_pprint_wraps_the_whole_expression(path):
    """An action string that opens with an event field parses to one
    output node whose only child is the pprint filter: the whole expression
    is handed over, not its last operand."""
    jinja2 = pytest.importorskip("jinja2")
    from jinja2 import nodes

    env = jinja2.Environment()
    for rule, flat in _playbook_action_strings(path):
        if not flat.startswith("{{") or not EVENT_WORD.search(flat):
            continue
        tree = env.parse(flat)
        assert len(tree.body) == 1 and isinstance(tree.body[0], nodes.Output), (
            path.name,
            rule,
            flat[:80],
        )
        children = tree.body[0].nodes
        assert len(children) == 1, (path.name, rule, flat[:80])
        node = children[0]
        assert isinstance(node, nodes.Filter) and node.name == "pprint", (
            path.name,
            rule,
            flat[:80],
        )
