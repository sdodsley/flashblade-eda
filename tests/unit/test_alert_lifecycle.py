# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The alert lifecycle as the rulebooks and playbooks see it: FlashBlade
alerts are open or closed, so alerts.yml logs the initial ones, routes an
opened one through the runbook map (and flags it on the array when the
switch is on), notifies a severity rise and notifies at closed with the time
the alert was open; the runbook map falls through code -> default and every
tasks route has its task file; health.yml clears the LED a recovered
component still shows; the fixtures carry the codes the lab raises. The
plugin's own diff (initial/opened/updated/closed, severity_raised,
resolved_after_s) is covered in test_events_plugin.py."""

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _rules(path):
    doc = yaml.safe_load((ROOT / path).read_text())
    return {r["name"]: r for rs in doc for r in rs.get("rules", [])}


def _playbooks(rule):
    actions = rule.get("actions") or [rule["action"]]
    return [a["run_playbook"]["name"] for a in actions if "run_playbook" in a]


def test_alerts_rulebook_logs_initial_routes_opened_and_notifies_rise_and_closed():
    rules = _rules("extensions/eda/rulebooks/alerts.yml")
    names = list(rules)
    initial = rules["Alert already open at start-up - log only"]
    assert 'transition == "initial"' in initial["condition"]
    assert "debug" in initial["action"]
    # opened: routed; with the flag switch on the flag follows the runbook
    flagged = rules[
        "Alert opened - route through the runbook map and flag it on the array"
    ]
    assert "vars.alert_flag_handled == true" in flagged["condition"]
    assert _playbooks(flagged) == [
        "playbooks/alert_runbook.yml",
        "playbooks/flag_alert.yml",
    ]
    plain = rules["Alert opened - route through the runbook map"]
    assert "vars.alert_flag_handled == false" in plain["condition"]
    assert _playbooks(plain) == ["playbooks/alert_runbook.yml"]
    for rule in (flagged, plain):
        assert 'transition == "opened"' in rule["condition"]
        assert "maintenance_active == false" in rule["condition"]
        extra = (rule.get("actions") or [rule["action"]])[0]["run_playbook"][
            "extra_vars"
        ]
        for key in (
            "alert_runbooks",
            "alert_variables",
            "component_name",
            "fb_api_version",
        ):
            assert key in extra, key
        assert extra["transition"] == "opened"
    window = rules["Alert opened during a maintenance window - log only"]
    assert "maintenance_active == true" in window["condition"]
    assert "debug" in window["action"]
    # a rise is a notice at the new severity carrying the old one
    raised = rules["Alert severity raised - notify"]
    assert "severity_raised == true" in raised["condition"]
    assert _playbooks(raised) == ["playbooks/notify.yml"]
    extra = raised["action"]["run_playbook"]["extra_vars"]
    assert extra["severity"] == "{{ event.purefb_alert.severity }}"
    assert "previous_severity" in extra["detail"]
    # closed is the resolved signal: info, with the time it was open
    closed = rules["Alert closed - notify"]
    assert 'transition == "closed"' in closed["condition"]
    assert _playbooks(closed) == ["playbooks/notify.yml"]
    extra = closed["action"]["run_playbook"]["extra_vars"]
    assert extra["severity"] == "info"
    assert "resolved_after_s" in extra["detail"]
    assert "closing" not in yaml.safe_dump(list(rules.values()))
    assert names.index("Alert already open at start-up - log only") < names.index(
        "Alert closed - notify"
    )


def test_flag_alert_patches_the_flag_and_never_fails_the_route():
    play = yaml.safe_load((ROOT / "playbooks" / "flag_alert.yml").read_text())[0]
    assert play["hosts"] == "{{ target_array }}"
    flag = next(t for t in play["tasks"] if t["name"] == "Flag the alert")
    assert flag["vars"]["fb_rest_method"] == "PATCH"
    assert flag["vars"]["fb_rest_path"] == "alerts?names={{ alert_id }}"
    assert flag["vars"]["fb_rest_body"] == {"flagged": True}
    assert flag["vars"]["fb_rest_ignore_errors"] is True
    guard = next(t for t in play["tasks"] if "numeric alert name" in t["name"])
    assert (
        "alert_id | string is match('^[0-9]+$')"
        in guard["ansible.builtin.assert"]["that"]
    )


def test_health_rulebook_clears_the_led_a_recovered_component_still_shows():
    rules = _rules("extensions/eda/rulebooks/health.yml")
    clear = rules["Hardware recovered - clear the identify LED"]
    assert "maintenance_active == false" in clear["condition"]
    extra = clear["action"]["run_playbook"]["extra_vars"]
    assert clear["action"]["run_playbook"]["name"] == "playbooks/identify_led.yml"
    assert extra["led_state"] is False and extra["severity"] == "info"
    assert "Hardware recovered during a maintenance window - log" in rules
    lit = rules["Hardware issue outside a maintenance window - identify LED and notify"]
    extra = lit["action"]["run_playbook"]["extra_vars"]
    for key in ("neighbor_switch", "neighbor_switch_address", "neighbor_port"):
        assert key in extra, key
    assert extra["led_state"] is True
    lag = rules["Link aggregation group degraded - notify"]
    assert lag["action"]["run_playbook"]["extra_vars"]["severity"] == "critical"
    assert "ports" in lag["action"]["run_playbook"]["extra_vars"]["detail"]


def test_runbook_map_falls_through_to_default_and_every_tasks_route_has_a_file():
    play = yaml.safe_load((ROOT / "playbooks" / "alert_runbook.yml").read_text())[0]
    pick = next(
        t for t in play["tasks"] if t["name"].startswith("Pick the runbook entry")
    )
    expr = pick["ansible.builtin.set_fact"]["runbook"]
    assert "alert_runbooks[alert_code | string]" in expr
    assert "alert_runbooks['default']" in expr
    example = yaml.safe_load((ROOT / "vars" / "example.yml").read_text())
    runbooks = example["alert_runbooks"]
    assert runbooks["default"]["route"] == "notify"
    assert runbooks["1101"]["route"] == "tasks"
    for code, entry in runbooks.items():
        assert entry["route"] in ("notify", "ticket", "tasks"), code
        if entry["route"] == "tasks":
            assert (ROOT / "playbooks" / "runbooks" / f"{code}.yml").exists(), code
    # the per-code tasks read the array through the REST helper with the
    # resolved credentials of the target array, never the play host's
    for code in ("1101", "1016", "1139"):
        tasks = yaml.safe_load(
            (ROOT / "playbooks" / "runbooks" / f"{code}.yml").read_text()
        )
        reads = [t for t in tasks if "ansible.builtin.include_tasks" in t]
        assert reads, code
        for task in reads:
            assert task["ansible.builtin.include_tasks"]["file"].endswith(
                "tasks/fb_rest.yml"
            )
            assert task["vars"]["fb_rest_url"] == "{{ hostvars[target_array].fb_url }}"
            assert (
                task["vars"]["fb_rest_token"]
                == "{{ hostvars[target_array].api_token }}"
            )
            assert task["vars"]["fb_rest_method"] == "GET"
            assert task["vars"]["fb_rest_ignore_errors"] is True


def test_fixtures_carry_the_codes_the_lab_raises():
    alerts = yaml.safe_load((ROOT / "tests" / "fixtures" / "alerts.yml").read_text())
    events = [e["purefb_alert"] for e in alerts["fixture_events"]]
    codes = {e["code"] for e in events}
    assert {1101, 1140, 9999} <= codes
    transitions = {e["transition"] for e in events}
    assert {"opened", "closed"} <= transitions
    assert any(e["maintenance_active"] for e in events if e["transition"] == "opened")
    closed = next(e for e in events if e["transition"] == "closed")
    assert isinstance(closed["resolved_after_s"], int)
    hardware = yaml.safe_load(
        (ROOT / "tests" / "fixtures" / "hardware.yml").read_text()
    )
    kinds = [next(iter(e)) for e in hardware["fixture_events"]]
    assert {"purefb_hardware", "purefb_lag", "purefb_hardware_recovered"} <= set(kinds)
    blade = next(
        e["purefb_hardware"]
        for e in hardware["fixture_events"]
        if "purefb_hardware" in e
    )
    for key in ("neighbor_switch", "neighbor_switch_address", "neighbor_port"):
        assert key in blade, key
    lag = next(e["purefb_lag"] for e in hardware["fixture_events"] if "purefb_lag" in e)
    assert lag["degraded"] is True and lag["lag_speed"] < lag["expected_speed"]
    assert all(
        {"name", "neighbor_switch", "neighbor_port"} <= set(p) for p in lag["ports"]
    )
