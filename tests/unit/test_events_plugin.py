# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the purefb_events source plugin that need no array: the helpers,
the alert transition diff and its unfiltered fallback, the audit and session
cursors, the health issue sets and the LAG check with the LLDP cache, the
capacity history, the file system quota events, the per-client and
per-object share streaks, the config fields, the SDK client construction
and the asyncio driver, all driven by a fake SDK client."""

import asyncio
import contextlib
import json
import logging
import pathlib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import yaml

NOW_MS = int(time.time() * 1000)
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
GBIT = 1_000_000_000


class FakeResponse:
    """What py-pure-client returns: .items of model objects with to_dict()."""

    def __init__(self, items, total=None):
        self.status_code = 200
        self.items = [SimpleNamespace(to_dict=lambda d=d: d, **d) for d in items]
        self.total_item_count = total if total is not None else len(items)


class FakeError:
    """An ErrorResponse: no .items, a list of errors with a message."""

    def __init__(self, message, status=400):
        self.status_code = status
        self.errors = [SimpleNamespace(message=message)]


def _page(rows, kw, default_limit=None):
    off = kw.get("offset", 0)
    limit = kw.get("limit", default_limit or len(rows))
    return rows[off : off + limit]


def _sorted(rows, kw):
    if kw.get("sort"):
        key = kw["sort"][0].rstrip("-")
        rows = sorted(rows, key=lambda r: r.get(key) or 0, reverse=True)
    return rows


class FakeClient:
    """Enough of pypureclient.flashblade.Client for every poller: the rows
    are plain dicts in the array's shape, the calls are recorded."""

    def __init__(self, **data):
        self.calls = []
        self.reject_state_filter = False
        self.alerts = data.get("alerts", [])
        self.audits = data.get("audits", [])
        self.sessions = data.get("sessions", [])
        self.hardware = data.get("hardware", [])
        self.blades = data.get("blades", [])
        self.drives = data.get("drives", [])
        self.lags = data.get("lags", [])
        self.connectors = data.get("connectors", [])
        self.neighbors = data.get("neighbors", [])
        self.windows = data.get("windows", [])
        self.space = data.get(
            "space",
            {
                "time": NOW_MS,
                "capacity": 1000,
                "space": {
                    "total_physical": 500,
                    "data_reduction": 3.0,
                    "snapshots": 50,
                    "destroyed": 7,
                    "destroyed_virtual": 9,
                    "total_provisioned": 2000,
                    "virtual": 600,
                },
            },
        )
        self.space_by_type = data.get("space_by_type", {})
        self.history = data.get("history", {})  # type (or None) -> rows
        self.file_systems = data.get("file_systems", [])
        self.buckets = data.get("buckets", [])
        self.destroyed = data.get("destroyed", {})  # kind -> count
        self.fs_perf = data.get("fs_perf", [])
        self.bucket_perf = data.get("bucket_perf", [])
        self.clients = data.get("clients", [])
        self.s3_clients = data.get("s3_clients", [])
        self.perf = data.get(
            "perf",
            {
                "time": NOW_MS,
                "usec_per_read_op": 300,
                "usec_per_write_op": 400,
                "usec_per_other_op": 100,
                "reads_per_sec": 1000,
                "writes_per_sec": 1000,
                "others_per_sec": 0,
                "read_bytes_per_sec": 1,
                "write_bytes_per_sec": 1,
            },
        )
        self.baseline = data.get("baseline", [])
        self.nfs_perf = data.get(
            "nfs_perf",
            [
                {
                    "aggregate_file_metadata_modifies_per_sec": 5.0,
                    "aggregate_file_metadata_reads_per_sec": 7.0,
                }
            ],
        )
        self.arrays = data.get(
            "arrays",
            {
                "name": "fb-lab",
                "version": "4.6.4",
                "ntp_servers": ["1.pool.ntp.org", "0.pool.ntp.org"],
                "time_zone": "Europe/London",
                "banner": "NO UNAUTHORIZED ACCESS",
                "default_inbound_tls_policy": {"name": "default-tls-policy"},
                "encryption": {"data_at_rest": {"enabled": True}},
                "eradication_config": {
                    "eradication_delay": 86400000,
                    "manual_eradication": "all-enabled",
                },
                "network_access_policy": {"name": "default-network-access-policy"},
                "smb_mode": "native",
            },
        )
        self.dns = data.get(
            "dns",
            [
                {"name": "other", "domain": "x.example", "nameservers": ["9.9.9.9"]},
                {
                    "name": "management",
                    "domain": "lab.example",
                    "nameservers": ["8.8.8.8", "1.1.1.1"],
                },
            ],
        )
        self.ds = data.get(
            "ds",
            [
                {
                    "name": "management",
                    "enabled": True,
                    "uris": ["ldap://10.0.0.2", "ldap://10.0.0.1"],
                    "base_dn": "DC=lab,DC=example",
                    "bind_user": "svc-fusion",
                },
                {"name": "nfs", "enabled": False, "uris": []},
            ],
        )
        self.ad = data.get("ad", [])
        self.idps = data.get("idps", [{"name": "PureSSO", "enabled": False}])
        self.syslog = data.get(
            "syslog",
            [
                {"name": "s2", "uri": "tcp://10.0.0.6:514", "services": ["management"]},
                {
                    "name": "s1",
                    "uri": "tcp://10.0.0.5:514",
                    "services": ["management", "data-audit"],
                },
            ],
        )
        self.smtp = data.get(
            "smtp",
            [{"name": "management", "relay_host": None, "sender_domain": "example"}],
        )
        self.watchers = data.get(
            "watchers",
            [
                {"name": "ops@example", "enabled": True},
                {"name": "old@example", "enabled": False},
            ],
        )
        self.support = data.get(
            "support",
            [
                {
                    "remote_assist_active": False,
                    "remote_assist_status": "disconnected",
                    "phonehome_enabled": True,
                }
            ],
        )
        self.admins = data.get(
            "admins",
            [
                {
                    "lockout_duration": 300000,
                    "max_login_attempts": 5,
                    "min_password_length": 10,
                }
            ],
        )

    # --- alerts / audits / sessions --------------------------------------
    def get_alerts(self, **kw):
        self.calls.append(("get_alerts", kw))
        if "names" in kw:
            return FakeResponse([a for a in self.alerts if a["name"] in kw["names"]])
        flt = kw.get("filter")
        if flt == "state!='closed'":
            if self.reject_state_filter:
                return FakeError("invalid filter")
            return FakeResponse([a for a in self.alerts if a["state"] != "closed"])
        if flt == "state='open'":
            rows = [a for a in self.alerts if a["state"] == "open"]
            return FakeResponse(_page(rows, kw), total=len(rows))
        rows = sorted(self.alerts, key=lambda a: a.get("created") or 0, reverse=True)
        return FakeResponse(_page(rows, kw))

    def get_audits(self, **kw):
        self.calls.append(("get_audits", kw))
        rows = sorted(
            self.audits, key=lambda r: (r["time"], int(r["name"])), reverse=True
        )
        return FakeResponse(_page(rows, kw, 200))

    def get_sessions(self, **kw):
        self.calls.append(("get_sessions", kw))
        rows = sorted(
            self.sessions,
            key=lambda r: (r["start_time"], int(r["name"])),
            reverse=True,
        )
        return FakeResponse(_page(rows, kw, 200))

    def get_maintenance_windows(self, **kw):
        self.calls.append(("get_maintenance_windows", kw))
        return FakeResponse(self.windows)

    # --- health ----------------------------------------------------------
    def get_hardware(self, **kw):
        return FakeResponse(self.hardware)

    def get_blades(self, **kw):
        return FakeResponse(self.blades)

    def get_drives(self, **kw):
        return FakeResponse(self.drives)

    def get_link_aggregation_groups(self, **kw):
        return FakeResponse(self.lags)

    def get_hardware_connectors(self, **kw):
        return FakeResponse(self.connectors)

    def get_network_interfaces_neighbors(self, **kw):
        self.calls.append(("get_network_interfaces_neighbors", kw))
        return FakeResponse(self.neighbors)

    def get_support(self, **kw):
        return FakeResponse(self.support)

    def get_arrays(self, **kw):
        return FakeResponse([self.arrays])

    # --- telemetry -------------------------------------------------------
    def get_arrays_space(self, **kw):
        self.calls.append(("get_arrays_space", kw))
        kind = kw.get("type")
        if "resolution" in kw:
            return FakeResponse(self.history.get(kind, []))
        if kind:
            return FakeResponse([self.space_by_type.get(kind, self.space)])
        return FakeResponse([self.space])

    def get_arrays_performance(self, **kw):
        self.calls.append(("get_arrays_performance", kw))
        if "resolution" in kw:
            return FakeResponse(self.baseline)
        return FakeResponse([self.perf])

    def get_arrays_nfs_specific_performance(self, **kw):
        return FakeResponse(self.nfs_perf)

    def get_arrays_clients_performance(self, **kw):
        self.calls.append(("get_arrays_clients_performance", kw))
        rows = self.s3_clients if kw.get("protocol") == "S3" else self.clients
        return FakeResponse(_page(_sorted(list(rows), kw), kw))

    def _objects(self, rows, kind, kw):
        if kw.get("destroyed") is True:
            return FakeResponse([], total=self.destroyed.get(kind, 0))
        rows = [r for r in rows if not r.get("destroyed")]
        if kw.get("sort"):
            rows = sorted(
                rows,
                key=lambda r: (r.get("space") or {}).get("total_physical") or 0,
                reverse=True,
            )
        return FakeResponse(_page(rows, kw))

    def get_file_systems(self, **kw):
        self.calls.append(("get_file_systems", kw))
        return self._objects(self.file_systems, "file_system", kw)

    def get_buckets(self, **kw):
        self.calls.append(("get_buckets", kw))
        return self._objects(self.buckets, "bucket", kw)

    def get_file_system_snapshots(self, **kw):
        self.calls.append(("get_file_system_snapshots", kw))
        return FakeResponse([], total=self.destroyed.get("snapshot", 0))

    def _perf_rows(self, rows, what, kw):
        if "names" in kw:
            known = {r["name"] for r in rows}
            if any(n not in known for n in kw["names"]):
                return FakeError(f"{what} does not exist.")
            return FakeResponse([r for r in rows if r["name"] in kw["names"]])
        return FakeResponse(_page(_sorted(list(rows), kw), kw))

    def get_file_systems_performance(self, **kw):
        self.calls.append(("get_file_systems_performance", kw))
        return self._perf_rows(self.fs_perf, "File system", kw)

    def get_buckets_performance(self, **kw):
        self.calls.append(("get_buckets_performance", kw))
        return self._perf_rows(self.bucket_perf, "Bucket", kw)

    # --- config ----------------------------------------------------------
    def get_dns(self, **kw):
        return FakeResponse(self.dns)

    def get_directory_services(self, **kw):
        return FakeResponse(self.ds)

    def get_active_directory(self, **kw):
        return FakeResponse(self.ad)

    def get_admins_settings(self, **kw):
        return FakeResponse(self.admins)

    def get_sso_saml2_idps(self, **kw):
        return FakeResponse(self.idps)

    def get_syslog_servers(self, **kw):
        return FakeResponse(self.syslog)

    def get_smtp_servers(self, **kw):
        return FakeResponse(self.smtp)

    def get_alert_watchers(self, **kw):
        return FakeResponse(self.watchers)


class FakeArray:
    def __init__(self, client, name="fb-a"):
        self.name = name
        self.client = client


def alert(name, state="open", updated=1, **extra):
    return {
        "name": name,
        "state": state,
        "updated": updated,
        "code": 1000,
        "severity": "warning",
        "created": NOW_MS - 90_000,
        "flagged": False,
        "variables": {"blade": "CH1.FB1"},
        **extra,
    }


def audit(name, time, command="purefs", subcommand="create", user="eda-human", **x):
    return {
        "name": name,
        "time": time,
        "user": user,
        "command": command,
        "subcommand": subcommand,
        "arguments": "x",
        "ip_address": "10.0.0.9",
        "user_agent": x.get("user_agent", "Mozilla/5.0"),
        "user_interface": x.get("user_interface", "GUI"),
        "origin": {"name": "fb-lab"},
    }


def session(name, start_time, event="login", user="pureuser", **x):
    return {
        "name": name,
        "start_time": start_time,
        "end_time": x.get("end_time"),
        "event": event,
        "event_count": x.get("event_count", 1),
        "user": user,
        "location": x.get("location", "10.0.0.9"),
        "method": "API token",
        "user_interface": "REST",
    }


def perf_row(name, reads, writes, latency=400, others=0):
    return {
        "name": name,
        "reads_per_sec": reads,
        "writes_per_sec": writes,
        "others_per_sec": others,
        "read_bytes_per_sec": 1,
        "write_bytes_per_sec": 1,
        "usec_per_read_op": latency,
        "usec_per_write_op": latency,
        "usec_per_other_op": latency,
    }


# --- helpers ---------------------------------------------------------------
def test_csv_sorts_and_joins(events):
    assert events._csv(["b", "a"]) == "a,b"
    assert events._csv(None) == ""


def test_ratio_avg_and_pct_tolerate_missing_values(events):
    assert events._ratio(300, 100) == 3.0
    assert events._ratio(None, 100) is None
    assert events._ratio(300, 0) is None
    assert events._avg([1, None, 2]) == 1.5
    assert events._avg([]) is None
    assert events._pct(50, 200) == 25.0
    assert events._pct(300, 200) == 100.0  # clamped: samples are not simultaneous
    assert events._pct(1, 0) is None


def test_as_list_and_truthy(events):
    assert events._as_list("a, b") == ["a", "b"]
    assert events._as_list(["x"]) == ["x"]
    assert events._as_list(None) == []
    assert events._truthy("Yes") and events._truthy(1) and not events._truthy("off")
    assert events._truthy(None, True) is True


def test_poll_error_shape(events):
    e = events._poll_error("audits", "fb-a", RuntimeError("boom"))
    assert e == {
        "purefb_poll_error": {
            "signal": "audits",
            "array": "fb-a",
            "error": "boom",
            "consecutive": 1,
            "escalate": False,
        }
    }


def test_poll_streak_counts_consecutive_failures_per_array_and_signal(events):
    state = {}
    assert events._poll_streak(state, "alerts", "a", True) == 1
    assert events._poll_streak(state, "alerts", "a", True) == 2
    assert events._poll_streak(state, "alerts", "b", True) == 1
    assert events._poll_streak(state, "health", "a", True) == 1
    # a success ends the streak; the next failure starts a new one
    assert events._poll_streak(state, "alerts", "a", False) == 0
    assert events._poll_streak(state, "alerts", "a", True) == 1
    e = events._poll_error("alerts", "a", RuntimeError("x"), 10, True)
    assert e["purefb_poll_error"]["consecutive"] == 10
    assert e["purefb_poll_error"]["escalate"] is True


def test_documentation_block_covers_every_argument(events):
    """DOCUMENTATION parses as YAML, names the plugin and every argument the
    code reads, and offers exactly the supported signals."""
    doc = yaml.safe_load(events.DOCUMENTATION)
    assert doc["name"] == "purefb_events"
    options = set(doc["options"])
    source = pathlib.Path(events.__file__).read_text()
    read = set(re.findall(r'args\.get\(\s*"([a-z_]+)"', source))
    assert read <= options, f"undocumented arguments: {sorted(read - options)}"
    for signal in events.SIGNALS:
        assert f"{signal}_interval" in options
    assert doc["options"]["signals"]["choices"] == list(events.SIGNALS)
    assert doc["options"]["request_timeout"]["default"] == 30
    assert doc["options"]["fs_quota_warn_pct"]["default"] == 80
    assert yaml.safe_load(events.EXAMPLES)[0]["sources"][0][
        "sdodsley.flashblade_eda.purefb_events"
    ]
    # the module docstring names every event the pollers emit
    emitted = set(re.findall(r'"(purefb_[a-z_]+)":', source))
    for name in emitted:
        assert name in events.__doc__, f"{name} missing from the docstring"
    assert "purefa" not in source.lower()


# --- SDK client construction -----------------------------------------------
def test_client_is_built_with_timeouts_and_user_agent(events, monkeypatch):
    monkeypatch.setenv("FB_URL_T", "https://192.0.2.1/")
    monkeypatch.setenv("FB_TOKEN_T", "secret")
    events.flashblade.Client.reset_mock()
    arr = events.Array(
        {"name": "a", "fb_url_env": "FB_URL_T", "api_token_env": "FB_TOKEN_T"},
        False,
        request_timeout=12,
    )
    assert arr.host == "192.0.2.1"
    arr.client
    kw = events.flashblade.Client.call_args.kwargs
    assert kw["target"] == "192.0.2.1"
    assert kw["timeout"] == (events.CONNECT_TIMEOUT, 12.0)
    assert kw["user_agent"] == events.USER_AGENT
    assert kw["api_token"] == "secret" and kw["verify_ssl"] is False
    # the default, and the client is reused until reset
    arr = events.Array({"name": "b", "fb_url": "192.0.2.2", "api_token": "t"}, True)
    arr.client
    assert events.flashblade.Client.call_args.kwargs["timeout"] == (10, 30.0)
    built = events.flashblade.Client.call_count
    arr.client
    assert events.flashblade.Client.call_count == built  # reused until reset
    arr.reset()
    arr.client
    assert events.flashblade.Client.call_count == built + 1


def test_client_kwargs_follow_the_installed_signature(events):
    def old_client(target, api_token=None, verify_ssl=None):
        return None

    assert events._accepts(old_client, "timeout") is False
    assert events._accepts(old_client, "api_token") is True

    def any_client(target, **kwargs):
        return None

    assert events._accepts(any_client, "timeout") is True


def test_array_requires_url_and_token(events):
    with pytest.raises(ValueError):
        events.Array({"name": "a", "api_token": "t"}, False)
    with pytest.raises(ValueError):
        events.Array({"name": "a", "fb_url": "x"}, False)


# --- the arrays argument as a string, and the signal check -----------------
class _NoQueue:
    async def put(self, event):
        raise AssertionError("nothing should be queued")


def test_arrays_argument_accepts_json_and_yaml_strings(events):
    spec = [{"name": "a", "fb_url": "192.0.2.1", "api_token": "t"}]
    assert events._decode_arrays(json.dumps(spec), "purefb_events") == spec
    text = "- name: a\n  fb_url: 192.0.2.1\n  api_token: t\n"
    assert events._decode_arrays(text, "purefb_events") == spec
    assert events._decode_arrays(spec, "purefb_events") is spec
    assert events._decode_arrays("  ", "purefb_events") is None
    assert events._decode_arrays(None, "purefb_events") is None


def test_main_decodes_the_string_before_validating_and_never_logs_the_token(
    events, caplog
):
    token = "s3cret-token-value"
    caplog.set_level(logging.DEBUG)
    arrays = json.dumps([{"name": "a", "fb_url": "192.0.2.1"}])
    with pytest.raises(ValueError, match="api_token or api_token_env"):
        asyncio.run(events.main(_NoQueue(), {"arrays": arrays}))
    arrays = json.dumps([{"name": "a", "fb_url": "192.0.2.1", "api_token": token}])
    with pytest.raises(ValueError, match="unknown signals") as exc:
        asyncio.run(events.main(_NoQueue(), {"arrays": arrays, "signals": "bogus"}))
    assert token not in str(exc.value) and token not in caplog.text


@pytest.mark.parametrize(
    "text",
    ['[{"name": "a", "api_token": "s3cret"', "just words s3cret", "42"],
)
def test_main_rejects_a_malformed_arrays_string_without_echoing_it(
    events, caplog, text
):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ValueError, match="JSON") as exc:
        asyncio.run(events.main(_NoQueue(), {"arrays": text}))
    assert "arrays" in str(exc.value)
    assert "s3cret" not in str(exc.value) and "s3cret" not in caplog.text


@pytest.mark.parametrize("signal", ["replication", "posture"])
def test_main_rejects_the_reserved_signals_with_a_clear_error(events, signal):
    arrays = [{"name": "a", "fb_url": "192.0.2.1", "api_token": "t"}]
    with pytest.raises(ValueError, match="not supported") as exc:
        asyncio.run(
            events.main(_NoQueue(), {"arrays": arrays, "signals": f"alerts,{signal}"})
        )
    assert signal in str(exc.value)
    for supported in events.SIGNALS:
        assert supported in str(exc.value)


def test_main_requires_an_array(events):
    with pytest.raises(ValueError, match="at least one array"):
        asyncio.run(events.main(_NoQueue(), {"arrays": []}))


# --- alert diff ------------------------------------------------------------
def test_alerts_first_poll_emits_initial_then_transitions(events):
    client = FakeClient(alerts=[alert("1"), alert("2", flagged=True)])
    arr = FakeArray(client)
    state = {}
    first = events.poll_alerts(arr, state, {})
    assert sorted(e["purefb_alert"]["transition"] for e in first) == [
        "initial",
        "initial",
    ]
    by_id = {e["purefb_alert"]["alert_id"]: e["purefb_alert"] for e in first}
    assert by_id["2"]["flagged"] is True
    assert by_id["1"]["variables"] == {"blade": "CH1.FB1"}
    assert by_id["1"]["age_s"] == pytest.approx(90, abs=2)
    assert by_id["1"]["resolved_after_s"] is None
    assert by_id["1"]["severity_raised"] is False
    assert by_id["1"]["maintenance_active"] is False
    # the cheap read: the state filter, once per poll
    assert client.calls[0] == ("get_alerts", {"filter": "state!='closed'"})

    # nothing changed: no events
    assert events.poll_alerts(arr, state, {}) == []

    # alert 2 is updated and raised, alert 3 opens, alert 1 closes
    client.alerts = [
        alert("2", updated=2, severity="critical"),
        alert("3"),
        alert("1", state="closed", updated=3, duration=100_000),
    ]
    third = events.poll_alerts(arr, state, {})
    by_id = {e["purefb_alert"]["alert_id"]: e["purefb_alert"] for e in third}
    assert {k: v["transition"] for k, v in by_id.items()} == {
        "2": "updated",
        "3": "opened",
        "1": "closed",
    }
    assert by_id["2"]["previous_severity"] == "warning"
    assert by_id["2"]["severity_raised"] is True
    assert by_id["1"]["resolved_after_s"] == 100
    assert by_id["1"]["state"] == "closed"
    assert all(e["purefb_alert"]["array"] == "fb-a" for e in third)


def test_alert_parked_in_closing_is_a_closing_transition(events):
    # the array held 1101 in "closing" after the grow before it closed
    client = FakeClient(alerts=[alert("1")])
    arr = FakeArray(client)
    state = {}
    events.poll_alerts(arr, state, {})
    client.alerts = [alert("1", state="closing", updated=5)]
    out = events.poll_alerts(arr, state, {})
    assert [e["purefb_alert"]["transition"] for e in out] == ["closing"]
    assert out[0]["purefb_alert"]["state"] == "closing"
    # still closing on the next poll: nothing; closed: the closed transition
    assert events.poll_alerts(arr, state, {}) == []
    client.alerts = [alert("1", state="closed", updated=9, duration=4000)]
    out = events.poll_alerts(arr, state, {})
    assert [e["purefb_alert"]["transition"] for e in out] == ["closed"]
    assert out[0]["purefb_alert"]["resolved_after_s"] == 4


def test_alert_updated_timestamp_alone_is_not_a_transition(events):
    # the lab's 1112 alerts bump `updated` every minute; only a change in
    # severity, flag or summary is worth an "updated" event
    client = FakeClient(alerts=[alert("1", summary="link down")])
    arr = FakeArray(client)
    state = {}
    events.poll_alerts(arr, state, {})
    client.alerts = [alert("1", updated=7, summary="link down")]
    assert events.poll_alerts(arr, state, {}) == []
    client.alerts = [alert("1", updated=8, summary="link down", flagged=True)]
    out = events.poll_alerts(arr, state, {})
    assert [e["purefb_alert"]["transition"] for e in out] == ["updated"]
    assert out[0]["purefb_alert"]["flagged"] is True
    assert out[0]["purefb_alert"]["severity_raised"] is False
    client.alerts = [alert("1", updated=9, summary="link still down", flagged=True)]
    out = events.poll_alerts(arr, state, {})
    assert [e["purefb_alert"]["transition"] for e in out] == ["updated"]


def test_alert_closed_without_a_duration_uses_updated_minus_created(events):
    client = FakeClient(alerts=[alert("1")])
    arr = FakeArray(client)
    state = {}
    events.poll_alerts(arr, state, {})
    created = client.alerts[0]["created"]
    client.alerts = [alert("1", state="closed", updated=created + 30_000)]
    (closed,) = events.poll_alerts(arr, state, {})
    assert closed["purefb_alert"]["resolved_after_s"] == 30
    # an alert that vanished without a final record still closes
    client.alerts = [alert("9")]
    events.poll_alerts(arr, state, {})
    client.alerts = []
    (gone,) = events.poll_alerts(arr, state, {})
    assert gone["purefb_alert"]["alert_id"] == "9"
    assert gone["purefb_alert"]["transition"] == "closed"


def test_alerts_initial_can_be_suppressed(events):
    arr = FakeArray(FakeClient(alerts=[alert("1")]))
    assert events.poll_alerts(arr, {}, {"include_initial_alerts": False}) == []


def test_alerts_fall_back_to_an_unfiltered_read_when_the_filter_is_rejected(
    events, caplog
):
    client = FakeClient(
        alerts=[alert("1"), alert("2", state="closed", updated=5), alert("3")]
    )
    client.reject_state_filter = True
    arr = FakeArray(client)
    state = {}
    caplog.set_level(logging.INFO)
    first = events.poll_alerts(arr, state, {})
    assert sorted(e["purefb_alert"]["alert_id"] for e in first) == ["1", "3"]
    unfiltered = [kw for n, kw in client.calls if n == "get_alerts" and "limit" in kw]
    assert unfiltered == [
        {"limit": events.ALERTS_UNFILTERED_LIMIT, "sort": ["created-"]}
    ]
    assert "rejected" in caplog.text
    caplog.clear()
    client.calls.clear()
    # from then on the filter is not retried and nothing more is logged
    assert events.poll_alerts(arr, state, {}) == []
    assert all("filter" not in kw for n, kw in client.calls)
    assert "rejected" not in caplog.text


def test_alerts_carry_the_maintenance_flag(events):
    client = FakeClient(
        alerts=[alert("1")],
        windows=[{"name": "array", "created": NOW_MS, "expires": NOW_MS + HOUR_MS}],
    )
    (initial,) = events.poll_alerts(FakeArray(client), {}, {})
    assert initial["purefb_alert"]["maintenance_active"] is True


def test_maintenance_is_false_on_an_array_without_the_endpoint(events, caplog):
    class OldClient(FakeClient):
        @property
        def get_maintenance_windows(self):
            raise AttributeError("no such method at this REST version")

    client = OldClient(alerts=[alert("1")])
    caplog.set_level(logging.INFO)
    state = {}
    (initial,) = events.poll_alerts(FakeArray(client), state, {})
    assert initial["purefb_alert"]["maintenance_active"] is False
    assert "maintenance-windows endpoint" in caplog.text
    caplog.clear()
    health = events.poll_health(FakeArray(client), state, {})
    assert health[0]["purefb_health"]["maintenance_active"] is False
    assert "maintenance-windows endpoint" not in caplog.text  # said once


# --- audit cursor ----------------------------------------------------------
def test_audits_start_at_newest_and_emit_only_new_rows(events):
    client = FakeClient(audits=[audit("1", 100), audit("2", 200)])
    arr = FakeArray(client)
    state = {}
    # first poll with no lookback: the newest record becomes the cursor, nothing emitted
    assert events.poll_audits(arr, state, {}) == []
    client.audits.append(audit("3", 300))
    client.audits.append(audit("4", 300))  # same timestamp as another new row
    out = events.poll_audits(arr, state, {"automation_users": ["svc-eda"]})
    assert [e["purefb_audit"]["audit_id"] for e in out] == ["3", "4"]
    row = out[0]["purefb_audit"]
    assert row["by_automation"] is False
    assert row["origin"] == "fb-lab"
    assert row["ip_address"] == "10.0.0.9"
    assert row["user_interface"] == "GUI"
    assert row["maintenance_active"] is False
    # a repeat poll emits nothing, including the rows sharing the cursor timestamp
    assert events.poll_audits(arr, state, {}) == []
    client.audits.append(audit("5", 300, user="svc-eda"))
    again = events.poll_audits(arr, state, {"automation_users": ["svc-eda"]})
    assert [e["purefb_audit"]["audit_id"] for e in again] == ["5"]
    assert again[0]["purefb_audit"]["by_automation"] is True


def test_automation_users_accepts_a_comma_separated_string(events):
    client = FakeClient(audits=[audit("1", 1)])
    arr = FakeArray(client)
    state = {}
    events.poll_audits(arr, state, {})
    client.audits.append(audit("2", 2, user="svc-eda"))
    out = events.poll_audits(arr, state, {"automation_users": "ops, svc-eda"})
    assert out[0]["purefb_audit"]["by_automation"] is True


def test_audit_agent_and_destructive_flags(events):
    client = FakeClient(audits=[audit("1", 1)])
    arr = FakeArray(client)
    state = {}
    events.poll_audits(arr, state, {})
    client.audits.extend(
        [
            audit("2", 2, user_agent="pure/py-pure-client/1.96.0", subcommand="create"),
            audit("3", 3, command="purepolicy", subcommand="nfs delete"),
            audit("4", 4, command="purefs", subcommand="eradicate"),
            audit("5", 5, command="purefs", subcommand="setattr"),
        ]
    )
    out = {
        e["purefb_audit"]["audit_id"]: e["purefb_audit"]
        for e in events.poll_audits(arr, state, {})
    }
    assert out["2"]["automation_agent"] is True and out["2"]["destructive"] is False
    assert out["3"]["automation_agent"] is False and out["3"]["destructive"] is True
    assert out["4"]["destructive"] is True
    assert out["5"]["destructive"] is False
    assert out["2"]["user_agent"] == "pure/py-pure-client/1.96.0"
    # both lists take the operator's own words
    client.audits.append(audit("6", 6, user_agent="ansible/2.16", subcommand="zap"))
    (row,) = events.poll_audits(
        arr,
        state,
        {"automation_user_agents": "ansible/", "destructive_subcommands": ["zap"]},
    )
    assert row["purefb_audit"]["automation_agent"] is True
    assert row["purefb_audit"]["destructive"] is True


def test_audits_lookback_replays_history(events):
    client = FakeClient(audits=[audit("1", 1_000), audit("2", 61_000)])
    arr = FakeArray(client)
    out = events.poll_audits(arr, {}, {"audit_lookback": 120})
    assert [e["purefb_audit"]["audit_id"] for e in out] == ["1", "2"]


def test_audits_page_past_audit_batch(events):
    """More new records than audit_batch: every one is emitted, oldest first,
    across several offset pages, with no overflow report."""
    client = FakeClient(audits=[audit("1", 100)])
    arr = FakeArray(client)
    state = {}
    events.poll_audits(arr, state, {"audit_batch": 3})
    for i in range(2, 7):
        client.audits.append(audit(str(i), 100 * i))
    client.calls.clear()
    out = events.poll_audits(arr, state, {"audit_batch": 3})
    assert [e["purefb_audit"]["audit_id"] for e in out] == ["2", "3", "4", "5", "6"]
    assert all("purefb_audit" in e for e in out)
    offsets = [kw["offset"] for name, kw in client.calls if name == "get_audits"]
    assert offsets == [0, 3, 6]
    assert events.poll_audits(arr, state, {"audit_batch": 3}) == []


def test_audits_overflow_is_reported_not_silent(events):
    client = FakeClient(audits=[audit("1", 100)])
    arr = FakeArray(client)
    state = {}
    events.poll_audits(arr, state, {"audit_batch": 2})
    for i in range(2, 27):  # 25 new rows, 2 per page, 10 pages: 20 read, 5 skipped
        client.audits.append(audit(str(i), 100 * i))
    out = events.poll_audits(arr, state, {"audit_batch": 2})
    audits = [e["purefb_audit"]["audit_id"] for e in out if "purefb_audit" in e]
    assert audits == [str(i) for i in range(7, 27)]
    errors = [e["purefb_poll_error"] for e in out if "purefb_poll_error" in e]
    assert len(errors) == 1
    assert errors[0]["signal"] == "audits" and errors[0]["array"] == "fb-a"
    assert errors[0]["consecutive"] == 0
    assert "audit_batch overflow" in errors[0]["error"]
    assert events.poll_audits(arr, state, {"audit_batch": 2}) == []


# --- session cursor --------------------------------------------------------
def test_sessions_start_at_newest_and_emit_new_logins(events):
    client = FakeClient(sessions=[session("10", 1_000), session("11", 2_000)])
    arr = FakeArray(client)
    state = {}
    assert events.poll_sessions(arr, state, {}) == []
    client.sessions.append(session("12", 3_000, location="203.0.113.5"))
    client.sessions.append(
        session("13", 3_000, event="failed login", event_count=3, user="root")
    )
    out = events.poll_sessions(arr, state, {})
    assert [e["purefb_session"]["session_id"] for e in out] == ["12", "13"]
    first = out[0]["purefb_session"]
    assert first["location"] == "203.0.113.5"
    assert first["method"] == "API token"
    assert first["user_interface"] == "REST"
    assert first["start_time"] == 3_000 and first["end_time"] is None
    assert first["maintenance_active"] is False
    assert out[1]["purefb_session"]["event"] == "failed login"
    assert out[1]["purefb_session"]["event_count"] == 3
    # the rows at the cursor time are not repeated
    assert events.poll_sessions(arr, state, {}) == []
    kw = next(kw for n, kw in client.calls if n == "get_sessions")
    assert kw["sort"] == ["start_time-"]


# --- health ----------------------------------------------------------------
PORTS = [f"CH1.FM{1 + i // 4}.ETH{1 + i % 4}" for i in range(8)]


def lag(speed_gbit=800, status="healthy", ports=PORTS):
    return {
        "name": "uplink",
        "status": status,
        "lag_speed": speed_gbit * GBIT,
        "port_speed": 100 * GBIT,
        "ports": [{"name": p} for p in ports],
    }


def connectors(ports=PORTS):
    return [{"name": p, "port_speed": 100 * GBIT} for p in ports]


def neighbors(ports=PORTS):
    return [
        {
            "local_port": {"name": p},
            "neighbor_chassis": {"name": "slc6-rsw-1", "addresses": ["10.21.12.72"]},
            "neighbor_port": {
                "description": f"Ethernet1/{13 + PORTS.index(p)}",
                "id": {"type": "ifname", "value": f"Ethernet1/{13 + PORTS.index(p)}"},
            },
        }
        for p in ports
    ]


def hw(name, type_, status="healthy", **extra):
    return {
        "name": name,
        "type": type_,
        "status": status,
        "details": extra.get("details"),
        "slot": extra.get("slot"),
        "serial": extra.get("serial", "SN" + name),
        "part_number": extra.get("part_number", "PN-1"),
        "identify_enabled": False,
    }


def test_health_issue_sets_and_counts(events):
    client = FakeClient(
        hardware=[
            hw("CH1", "ch"),
            hw("CH1.FB1", "fb", "critical", details="blade failed", slot=1),
            hw("CH1.FM1.ETH1", "eth", "unhealthy"),
            hw("CH1.PWR3", "psu", "not_installed"),
        ],
        blades=[
            {"name": "CH1.FB1", "status": "critical", "details": "failed"},
            {"name": "CH1.FB2", "status": "healthy"},
            {
                "name": "CH1.FB3",
                "status": "evacuating",
                "progress": 0.4,
                "target": "CH1.FB4",
            },
        ],
        drives=[
            {"name": "CH1.FB1.BAY1", "status": "unhealthy", "type": "DFM"},
            {"name": "CH1.FB1.BAY2", "status": "healthy", "type": "DFM"},
            {"name": "CH1.FB1.BAY3", "status": "evacuated", "type": "DFM"},
            {"name": "CH1.FB1.BAY4", "status": "empty", "type": "DFM"},
        ],
        lags=[lag()],
        connectors=connectors(),
        neighbors=neighbors(),
        alerts=[alert("1"), alert("2"), alert("3", state="closed")],
        windows=[{"name": "array", "created": 1, "expires": 2}],
    )
    out = events.poll_health(FakeArray(client), {}, {})
    health = out[0]["purefb_health"]
    assert health["purity_version"] == "4.6.4"
    assert [h["component"] for h in health["hardware_issues"]] == [
        "CH1.FB1",
        "CH1.FM1.ETH1",
    ]
    assert health["hardware_issues"][0]["details"] == "blade failed"
    assert health["hardware_issue_count"] == 2
    assert [b["blade"] for b in health["blade_issues"]] == ["CH1.FB1", "CH1.FB3"]
    assert health["blade_issues"][1] == {
        "blade": "CH1.FB3",
        "status": "evacuating",
        "progress": 0.4,
        "target": "CH1.FB4",
        "details": None,
    }
    assert health["blade_issue_count"] == 2 and health["blade_count"] == 3
    assert [d["drive"] for d in health["drive_issues"]] == ["CH1.FB1.BAY1"]
    assert health["drive_issues"][0]["type"] == "DFM"
    assert health["drive_issue_count"] == 1 and health["drive_count"] == 4
    assert health["lags"] == [
        {
            "lag": "uplink",
            "status": "healthy",
            "lag_speed": 800 * GBIT,
            "expected_speed": 800 * GBIT,
            "port_count": 8,
            "degraded": False,
        }
    ]
    assert health["lag_degraded"] is False
    assert health["maintenance_active"] is True
    assert health["maintenance_windows"] == [
        {"name": "array", "created": 1, "expires": 2}
    ]
    assert health["remote_assist_active"] is False
    assert health["phonehome_enabled"] is True
    assert health["open_alert_count"] == 2
    # one purefb_hardware per issue, the Ethernet port enriched from LLDP
    hardware = {
        e["purefb_hardware"]["component"]: e["purefb_hardware"]
        for e in out
        if "purefb_hardware" in e
    }
    assert set(hardware) == {"CH1.FB1", "CH1.FM1.ETH1"}
    assert hardware["CH1.FB1"]["slot"] == 1
    assert hardware["CH1.FB1"]["part_number"] == "PN-1"
    assert hardware["CH1.FB1"]["neighbor_switch"] == ""
    assert hardware["CH1.FB1"]["neighbor_port"] == ""
    assert hardware["CH1.FB1"]["maintenance_active"] is True
    eth = hardware["CH1.FM1.ETH1"]
    assert eth["neighbor_switch"] == "slc6-rsw-1"
    assert eth["neighbor_switch_address"] == "10.21.12.72"
    assert eth["neighbor_port"] == "Ethernet1/13"
    assert not any("purefb_lag" in e for e in out)


def test_health_ignore_lists_are_arguments(events):
    client = FakeClient(
        hardware=[hw("CH1.FB1", "fb", "critical")],
        blades=[{"name": "CH1.FB1", "status": "critical"}],
        drives=[{"name": "CH1.FB1.BAY1", "status": "evacuated", "type": "DFM"}],
    )
    out = events.poll_health(
        FakeArray(client),
        {},
        {
            "hardware_ignore_status": "critical",
            "blade_ignore_status": ["critical"],
            "drive_ignore_status": ["healthy"],
        },
    )
    health = out[0]["purefb_health"]
    assert health["hardware_issue_count"] == 0
    assert health["blade_issue_count"] == 0
    assert health["drive_issue_count"] == 1


def test_hardware_recovered_event(events):
    client = FakeClient(hardware=[hw("CH1.FB1", "fb", "critical")])
    arr = FakeArray(client)
    state = {}
    events.poll_health(arr, state, {})
    client.hardware = [hw("CH1.FB1", "fb", "healthy")]
    out = events.poll_health(arr, state, {})
    (rec,) = [
        e["purefb_hardware_recovered"] for e in out if "purefb_hardware_recovered" in e
    ]
    assert rec == {
        "array": "fb-a",
        "component": "CH1.FB1",
        "type": "fb",
        "status": "healthy",
        "previous_status": "critical",
        "maintenance_active": False,
    }
    # a component that vanished recovers too, with its remembered type
    client.hardware = [hw("CH1.FB2", "fb", "critical")]
    events.poll_health(arr, state, {})
    client.hardware = []
    out = events.poll_health(arr, state, {})
    (rec,) = [
        e["purefb_hardware_recovered"] for e in out if "purefb_hardware_recovered" in e
    ]
    assert rec["component"] == "CH1.FB2" and rec["type"] == "fb"
    assert rec["status"] is None


def test_lag_degradation_uses_the_last_good_lldp_view(events):
    client = FakeClient(lags=[lag()], connectors=connectors(), neighbors=neighbors())
    arr = FakeArray(client)
    state = {}

    def lag_events(out):
        return [e["purefb_lag"] for e in out if "purefb_lag" in e]

    def neighbor_reads():
        return len(
            [1 for n, _ in client.calls if n == "get_network_interfaces_neighbors"]
        )

    # healthy: no LAG event, the neighbour view is read
    out = events.poll_health(arr, state, {})
    assert lag_events(out) == []
    assert neighbor_reads() == 1
    assert set(state["health"]["fb-a"]["lldp"]) == set(PORTS)

    # one member link goes down: the LAG carries 700 of 800 Gbit, the port has
    # no LLDP neighbour any more, and the view is not refreshed
    client.lags = [lag(700)]
    client.neighbors = neighbors(PORTS[1:])
    out = events.poll_health(arr, state, {})
    assert out[0]["purefb_health"]["lag_degraded"] is True
    assert out[0]["purefb_health"]["lags"][0]["degraded"] is True
    (degraded,) = lag_events(out)
    assert degraded["degraded"] is True and degraded["recovered"] is False
    assert degraded["lag_speed"] == 700 * GBIT
    assert degraded["expected_speed"] == 800 * GBIT
    assert degraded["status"] == "healthy"
    assert degraded["ports"][0] == {
        "name": "CH1.FM1.ETH1",
        "neighbor_switch": "slc6-rsw-1",
        "neighbor_port": "Ethernet1/13",
    }
    assert len(degraded["ports"]) == 8
    assert neighbor_reads() == 1

    # still degraded: no repeat event, no refresh
    out = events.poll_health(arr, state, {})
    assert lag_events(out) == []
    assert neighbor_reads() == 1

    # the link comes back: a recovered event and the view is refreshed
    client.lags = [lag(800)]
    client.neighbors = neighbors()
    out = events.poll_health(arr, state, {})
    (recovered,) = lag_events(out)
    assert recovered["degraded"] is False and recovered["recovered"] is True
    assert neighbor_reads() == 2

    # a status other than healthy is a degradation on its own
    client.lags = [lag(800, status="unhealthy")]
    out = events.poll_health(arr, state, {})
    (degraded,) = lag_events(out)
    assert degraded["degraded"] is True and degraded["status"] == "unhealthy"


def test_lag_found_degraded_on_the_first_poll_is_reported(events):
    client = FakeClient(
        lags=[lag(700)], connectors=connectors(), neighbors=neighbors(PORTS[1:])
    )
    out = events.poll_health(FakeArray(client), {}, {})
    (degraded,) = [e["purefb_lag"] for e in out if "purefb_lag" in e]
    assert degraded["degraded"] is True
    # the down port is unknown to LLDP, so its switch port is empty
    assert degraded["ports"][0]["neighbor_port"] == ""
    assert degraded["ports"][1]["neighbor_port"] == "Ethernet1/14"


def test_lag_expected_speed_without_connector_rows(events):
    # no connector row for a member: the LAG's own port_speed stands in
    assert events._lag_expected_speed(lag(), {}) == 800 * GBIT
    assert events._lag_expected_speed({"name": "empty", "ports": []}, {}) is None
    assert events._lag_degraded({"status": "healthy", "lag_speed": None}, 800) is False


def test_lldp_read_failure_keeps_the_old_view(events):
    client = FakeClient(lags=[lag()], connectors=connectors(), neighbors=neighbors())
    arr = FakeArray(client)
    state = {}
    events.poll_health(arr, state, {})
    client.get_network_interfaces_neighbors = lambda **kw: FakeError("no lldp")
    out = events.poll_health(arr, state, {})
    assert "purefb_health" in out[0]
    assert set(state["health"]["fb-a"]["lldp"]) == set(PORTS)


# --- capacity --------------------------------------------------------------
def history(start, step, n=7):
    return [
        {
            "time": NOW_MS - (n - 1 - i) * DAY_MS,
            "space": {
                "total_physical": start + i * step,
                "data_reduction": 3.0 if i else 4.0,
                "snapshots": 50 if i else 40,
            },
        }
        for i in range(n)
    ]


def test_capacity_days_to_full_from_seven_daily_points_and_the_type_split(events):
    client = FakeClient(
        history={
            None: history(100, 10),
            "file-system": history(60, 8),
            "object-store": history(40, 2),
        },
        space_by_type={
            "file-system": {"capacity": 1000, "space": {"total_physical": 320}},
            "object-store": {"capacity": 1000, "space": {"total_physical": 180}},
        },
        destroyed={"file_system": 2, "bucket": 1, "snapshot": 5},
    )
    row = events._capacity_row(FakeArray(client), {}, {})
    assert row["capacity_bytes"] == 1000 and row["used_bytes"] == 500
    assert row["used_pct"] == 50.0
    assert row["growth_bytes_per_day"] == 10
    assert row["days_to_full"] == 50.0 and row["days_to_full_known"] is True
    assert row["file_system_bytes"] == 320 and row["object_store_bytes"] == 180
    assert row["file_system_growth_bytes_per_day"] == 8
    assert row["object_store_growth_bytes_per_day"] == 2
    assert row["snapshot_bytes"] == 50 and row["destroyed_bytes"] == 7
    assert row["destroyed_virtual_bytes"] == 9
    assert row["total_provisioned_bytes"] == 2000
    assert row["pending_eradication_file_systems"] == 2
    assert row["pending_eradication_buckets"] == 1
    assert row["pending_eradication_snapshots"] == 5
    # the week-ago anomaly inputs come from the oldest history point
    assert row["drr_week_ago"] == 4.0
    assert row["drr_drop_pct"] == 25.0 and row["drr_drop"] is True
    assert row["snapshot_growth_pct"] == 25.0 and row["snapshot_surge"] is True
    assert row["time"] == NOW_MS
    # the history reads: daily resolution, a start_time a week back, no end
    # time and no limit, once per type
    hist = [
        kw for n, kw in client.calls if n == "get_arrays_space" and "resolution" in kw
    ]
    assert [kw.get("type") for kw in hist] == [None, "file-system", "object-store"]
    for kw in hist:
        assert kw["resolution"] == events.DAY_MS
        assert (
            NOW_MS - 7 * DAY_MS - 5_000
            <= kw["start_time"]
            <= NOW_MS - 7 * DAY_MS + 5_000
        )
        assert "end_time" not in kw and "limit" not in kw
    # the pending counts ask for one destroyed row and read the total
    destroyed = [(n, kw) for n, kw in client.calls if kw.get("destroyed") is True]
    assert {n for n, _ in destroyed} == {
        "get_file_systems",
        "get_buckets",
        "get_file_system_snapshots",
    }
    assert all(kw["limit"] == 1 for _, kw in destroyed)


def test_capacity_without_history_is_unknown_not_an_error(events):
    client = FakeClient()
    client.get_arrays_space = lambda **kw: (
        FakeError("no history") if "resolution" in kw else FakeResponse([client.space])
    )
    row = events._capacity_row(FakeArray(client), {}, {})
    assert row["days_to_full"] is None and row["days_to_full_known"] is False
    assert row["growth_bytes_per_day"] is None
    assert row["drr_drop"] is False and row["snapshot_surge"] is False
    # the current per-type reading still works; only its growth is unknown
    assert row["file_system_bytes"] == 500
    assert row["file_system_growth_bytes_per_day"] is None


def test_capacity_thresholds_are_arguments(events):
    client = FakeClient(history={None: history(100, 10)})
    arr = FakeArray(client)
    row = events._capacity_row(
        arr, {}, {"drr_drop_threshold_pct": 30, "snapshot_growth_threshold_pct": 30}
    )
    assert row["drr_drop"] is False and row["snapshot_surge"] is False
    # flat growth: no days_to_full
    client.history = {None: history(100, 0)}
    row = events._capacity_row(arr, {}, {})
    assert row["growth_bytes_per_day"] == 0 and row["days_to_full"] is None


def fs(name, physical, virtual=0, provisioned=1000, qos=None, **extra):
    return {
        "name": name,
        "provisioned": provisioned,
        "hard_limit_enabled": extra.get("hard_limit_enabled", True),
        "destroyed": False,
        "qos_policy": {"name": qos} if qos else None,
        "space": {"total_physical": physical, "virtual": virtual, "snapshots": 3},
    }


def bucket(name, physical, qos=None):
    return {
        "name": name,
        "destroyed": False,
        "qos_policy": {"name": qos} if qos else None,
        "space": {"total_physical": physical, "virtual": physical},
    }


def test_top_growers_cover_file_systems_and_buckets(events):
    client = FakeClient(
        file_systems=[fs("fs-a", 100), fs("fs-b", 100), fs("fs-c", 100)],
        buckets=[bucket("b-a", 100)],
    )
    arr = FakeArray(client)
    state = {}
    assert events._capacity_row(arr, state, {})["top_growers"] == []
    client.file_systems = [fs("fs-a", 150), fs("fs-b", 90), fs("fs-c", 110)]
    client.buckets = [bucket("b-a", 300)]
    growers = events._capacity_row(arr, state, {"top_growers": 2})["top_growers"]
    assert growers == [
        {"name": "b-a", "kind": "bucket", "delta_bytes": 200, "total_physical": 300},
        {
            "name": "fs-a",
            "kind": "file_system",
            "delta_bytes": 50,
            "total_physical": 150,
        },
    ]
    kw = next(
        kw
        for n, kw in client.calls
        if n == "get_file_systems" and kw.get("destroyed") is False
    )
    assert kw["limit"] == events.GROWTH_OBJECTS
    assert kw["sort"] == ["space.total_physical-"]


def test_object_lists_fall_back_to_the_default_order(events):
    client = FakeClient(file_systems=[fs("fs-a", 100)])
    real = client.get_file_systems

    def picky(**kw):
        if "sort" in kw:
            client.calls.append(("get_file_systems", kw))
            return FakeError("sort not supported")
        return real(**kw)

    client.get_file_systems = picky
    arr = FakeArray(client)
    state = {}
    row = events._capacity_row(arr, state, {})
    assert row["array"] == "fb-a"
    # the sorted read was tried, the unsorted one served, and the sizes were kept
    kws = [
        kw
        for n, kw in client.calls
        if n == "get_file_systems" and kw.get("destroyed") is False
    ]
    assert [("sort" in kw) for kw in kws] == [True, False]
    assert state["telemetry"]["fb-a"]["sizes"] == {("file_system", "fs-a"): 100}


def test_capacity_events_list_the_other_arrays_least_used_first(events):
    rows = [
        {"array": "a", "used_pct": 50.0},
        {"array": "b", "used_pct": 20.0},
        {"array": "c", "used_pct": None},
    ]
    out = events._capacity_events(rows)
    assert [e["purefb_capacity"]["array"] for e in out] == ["a", "b", "c"]
    assert out[0]["purefb_capacity"]["others"] == [
        {"array": "b", "used_pct": 20.0},
        {"array": "c", "used_pct": None},
    ]
    assert out[2]["purefb_capacity"]["others"][0]["array"] == "b"
    assert events._capacity_events([]) == []


# --- file system quota ------------------------------------------------------
def test_fs_quota_events_at_or_above_the_threshold(events):
    client = FakeClient(
        file_systems=[
            fs("full", 10, virtual=860, provisioned=1000, qos="eda-cap-full"),
            fs("fine", 10, virtual=100, provisioned=1000),
            fs("edge", 10, virtual=800, provisioned=1000, hard_limit_enabled=False),
            fs("unlimited", 10, virtual=5000, provisioned=0),
        ]
    )
    out = events._fs_quota_events(FakeArray(client), {}, {})
    quotas = {e["purefb_fs_quota"]["file_system"]: e["purefb_fs_quota"] for e in out}
    assert set(quotas) == {"full", "edge"}
    assert quotas["full"] == {
        "array": "fb-a",
        "file_system": "full",
        "provisioned": 1000,
        "used_bytes": 860,
        "used_pct": 86.0,
        "hard_limit_enabled": True,
        "snapshot_bytes": 3,
        "qos_policy": "eda-cap-full",
        "destroyed": False,
    }
    assert quotas["edge"]["hard_limit_enabled"] is False
    # the threshold is an argument
    out = events._fs_quota_events(FakeArray(client), {}, {"fs_quota_warn_pct": 85})
    assert [e["purefb_fs_quota"]["file_system"] for e in out] == ["full"]


def test_quota_step_reuses_the_capacity_steps_object_list(events):
    client = FakeClient(file_systems=[fs("full", 10, virtual=900)])
    arr = FakeArray(client)
    state = {}
    events._capacity_row(arr, state, {})
    client.calls.clear()
    out = events._fs_quota_events(arr, state, {})
    assert [e["purefb_fs_quota"]["file_system"] for e in out] == ["full"]
    assert not any(n == "get_file_systems" for n, _ in client.calls)
    # run alone, it fetches for itself
    client.calls.clear()
    events._fs_quota_events(arr, {}, {})
    assert any(n == "get_file_systems" for n, _ in client.calls)


# --- performance -----------------------------------------------------------
def test_array_performance_with_nfs_metadata_and_baseline(events):
    client = FakeClient(
        baseline=[
            {
                "usec_per_read_op": 100,
                "usec_per_write_op": 200,
                "write_bytes_per_sec": 0.2,
            },
            {
                "usec_per_read_op": 200,
                "usec_per_write_op": 200,
                "write_bytes_per_sec": 0.2,
            },
        ]
    )
    out = events._performance_events(FakeArray(client), {}, {})
    perf = out[0]["purefb_array_performance"]
    assert perf["usec_per_read_op"] == 300 and perf["others_per_sec"] == 0
    assert perf["nfs_metadata_modifies_per_sec"] == 5.0
    assert perf["nfs_metadata_reads_per_sec"] == 7.0
    assert perf["baseline_usec_per_read_op"] == 150.0
    assert perf["baseline_write_bytes_per_sec"] == 0.2
    assert perf["read_latency_ratio"] == 2.0
    assert perf["write_latency_ratio"] == 2.0
    assert perf["write_bw_ratio"] == 5.0
    assert perf["write_surge"] is True and perf["baseline_known"] is True
    kw = next(
        kw
        for n, kw in client.calls
        if n == "get_arrays_performance" and "resolution" in kw
    )
    assert kw["resolution"] == events.HALF_HOUR_MS
    assert kw["end_time"] - kw["start_time"] == 2 * events.HALF_HOUR_MS
    week_ago = NOW_MS - 7 * DAY_MS
    assert abs((kw["start_time"] + events.HALF_HOUR_MS) - week_ago) < 5_000
    # without the baseline, or without history, the ratios are unknown
    out = events._performance_events(FakeArray(client), {}, {"baseline": False})
    perf = out[0]["purefb_array_performance"]
    assert perf["baseline_known"] is False and perf["write_surge"] is False
    assert "read_latency_ratio" not in perf
    client.baseline = []
    perf = events._performance_events(FakeArray(client), {}, {})[0][
        "purefb_array_performance"
    ]
    assert perf["baseline_known"] is False


def test_nfs_specific_read_is_optional(events):
    client = FakeClient()
    client.get_arrays_nfs_specific_performance = lambda **kw: FakeError("nope")
    perf = events._performance_events(FakeArray(client), {}, {"baseline": False})[0][
        "purefb_array_performance"
    ]
    assert perf["nfs_metadata_modifies_per_sec"] is None


def test_client_share_streaks_per_protocol(events):
    client = FakeClient(
        clients=[perf_row("10.0.0.1", 800, 800), perf_row("10.0.0.2", 200, 200)],
        s3_clients=[perf_row("10.0.0.9", 100, 100)],
        baseline=[{"usec_per_write_op": 200}],
    )
    arr = FakeArray(client)
    state = {}
    args = {"top_clients": 5, "client_min_share_pct": 40}

    def clients_of(out):
        return {
            (
                e["purefb_client_performance"]["protocol"],
                e["purefb_client_performance"]["client"],
            ): e["purefb_client_performance"]
            for e in out
            if "purefb_client_performance" in e
        }

    rows = clients_of(events._performance_events(arr, state, args))
    assert set(rows) == {("nfs", "10.0.0.1"), ("nfs", "10.0.0.2"), ("s3", "10.0.0.9")}
    busy = rows[("nfs", "10.0.0.1")]
    assert busy["ops_share_pct"] == 80.0
    assert busy["high_share_polls"] == 1 and busy["normal_polls"] == 0
    assert busy["array_usec_per_write_op"] == 400
    assert busy["array_write_latency_ratio"] == 2.0 and busy["baseline_known"] is True
    assert busy["usec_per_write_op"] == 400 and busy["reads_per_sec"] == 800
    quiet = rows[("nfs", "10.0.0.2")]
    assert quiet["ops_share_pct"] == 20.0
    assert quiet["high_share_polls"] == 0 and quiet["normal_polls"] == 1
    assert rows[("s3", "10.0.0.9")]["ops_share_pct"] == 10.0
    # the reads: NFS by default, S3 by protocol, busiest writers first
    reads = [kw for n, kw in client.calls if n == "get_arrays_clients_performance"]
    assert reads == [
        {"sort": ["writes_per_sec-"], "limit": 5},
        {"sort": ["writes_per_sec-"], "limit": 5, "protocol": "S3"},
    ]
    # the streak grows, then breaks when the client calms down
    rows = clients_of(events._performance_events(arr, state, args))
    assert rows[("nfs", "10.0.0.1")]["high_share_polls"] == 2
    client.clients = [perf_row("10.0.0.1", 10, 10), perf_row("10.0.0.2", 200, 200)]
    rows = clients_of(events._performance_events(arr, state, args))
    assert rows[("nfs", "10.0.0.1")]["high_share_polls"] == 0
    assert rows[("nfs", "10.0.0.1")]["normal_polls"] == 1
    assert rows[("nfs", "10.0.0.2")]["normal_polls"] == 3
    # a client that disappears is forgotten
    client.clients = [perf_row("10.0.0.2", 200, 200)]
    events._performance_events(arr, state, args)
    counters = state["telemetry"]["fb-a"]["high_share_polls"]
    assert "client:nfs:10.0.0.1" not in counters
    assert "client:nfs:10.0.0.2" in counters


def test_client_rows_split_the_port_off_the_address(events):
    # the array names a client row <address>:<port> (lab: 10.21.200.129:893)
    client = FakeClient(
        clients=[
            perf_row("10.21.200.129:893", 800, 800),
            perf_row("[fe80::1]:2049", 1, 1),
        ],
        baseline=[],
    )
    arr = FakeArray(client)
    state = {}
    out = events._performance_events(arr, state, {"top_clients": 5})
    rows = {
        e["purefb_client_performance"]["client"]: e["purefb_client_performance"]
        for e in out
        if "purefb_client_performance" in e
    }
    assert set(rows) == {"10.21.200.129", "fe80::1"}
    assert rows["10.21.200.129"]["client_port"] == 893
    assert rows["fe80::1"]["client_port"] == 2049
    # the streak is kept per address, so a reconnect on a new port continues it
    client.clients = [perf_row("10.21.200.129:901", 800, 800)]
    out = events._performance_events(arr, state, {"top_clients": 5})
    busy = [e for e in out if "purefb_client_performance" in e][0]
    assert busy["purefb_client_performance"]["high_share_polls"] == 2
    assert events._split_client("10.0.0.1") == ("10.0.0.1", None)
    assert events._split_client(None) == ("", None)


def test_s3_clients_are_skipped_where_the_call_lacks_protocol(events):
    client = FakeClient(clients=[perf_row("10.0.0.1", 1, 1)])

    def nfs_only(sort=None, limit=None):
        return FakeResponse(client.clients)

    client.get_arrays_clients_performance = nfs_only
    out = events._performance_events(FakeArray(client), {}, {"baseline": False})
    protocols = [
        e["purefb_client_performance"]["protocol"]
        for e in out
        if "purefb_client_performance" in e
    ]
    assert protocols == ["nfs"]
    # a client read that fails skips the rows for that poll and nothing else:
    # the lab answered "Service temporarily unavailable" to this call once
    client = FakeClient(clients=[perf_row("10.0.0.1", 1, 1)])
    real = client.get_arrays_clients_performance
    client.get_arrays_clients_performance = lambda **kw: (
        FakeError("no s3") if kw.get("protocol") else real(**kw)
    )
    out = events._performance_events(FakeArray(client), {}, {"baseline": False})
    assert len([e for e in out if "purefb_client_performance" in e]) == 1
    client.get_arrays_clients_performance = lambda **kw: FakeError("down")
    out = events._performance_events(FakeArray(client), {}, {"baseline": False})
    assert [e for e in out if "purefb_client_performance" in e] == []
    assert any("purefb_array_performance" in e for e in out)


def test_capped_file_system_outside_top_n_is_still_reported(events):
    client = FakeClient(
        fs_perf=[
            perf_row("busy-1", 500, 500),
            perf_row("busy-2", 400, 400),
            perf_row("busy-3", 300, 300),
            perf_row("capped", 0, 0),  # idle and outside the top 2
        ],
        file_systems=[
            fs("busy-1", 1),
            fs("busy-2", 1),
            fs("busy-3", 1),
            fs("capped", 1, qos="eda-cap-capped"),
        ],
    )
    arr = FakeArray(client)
    state = {}
    args = {"top_file_systems": 2, "baseline": False}
    out = events._performance_events(arr, state, args)
    rows = {
        e["purefb_fs_performance"]["file_system"]: e["purefb_fs_performance"]
        for e in out
        if "purefb_fs_performance" in e
    }
    assert set(rows) == {"busy-1", "busy-2", "capped"}
    assert rows["capped"]["eda_capped"] is True
    assert rows["capped"]["qos_policy"] == "eda-cap-capped"
    assert rows["capped"]["ops_share_pct"] == 0.0
    assert rows["capped"]["normal_polls"] == 1
    assert (
        rows["busy-1"]["eda_capped"] is False and rows["busy-1"]["qos_policy"] is None
    )
    assert rows["busy-1"]["ops_share_pct"] == 50.0
    assert rows["busy-1"]["high_share_polls"] == 1
    by_name = [
        kw["names"]
        for n, kw in client.calls
        if n == "get_file_systems_performance" and "names" in kw
    ]
    assert by_name == [["capped"]]
    top = next(
        kw
        for n, kw in client.calls
        if n == "get_file_systems_performance" and "names" not in kw
    )
    assert top == {"limit": 2, "sort": ["writes_per_sec-"]}
    # the counter keeps accumulating poll after poll
    out = events._performance_events(arr, state, args)
    capped = next(
        e["purefb_fs_performance"]
        for e in out
        if e.get("purefb_fs_performance", {}).get("file_system") == "capped"
    )
    assert capped["normal_polls"] == 2


def test_cap_on_a_missing_file_system_does_not_fail_the_poll(events):
    client = FakeClient(
        fs_perf=[perf_row("busy-1", 500, 500), perf_row("capped", 0, 0)],
        file_systems=[
            fs("capped", 1, qos="eda-cap-capped"),
            fs("gone", 1, qos="eda-cap-gone"),
        ],
    )
    out = events._performance_events(
        FakeArray(client), {}, {"top_file_systems": 1, "baseline": False}
    )
    names = {
        e["purefb_fs_performance"]["file_system"]
        for e in out
        if "purefb_fs_performance" in e
    }
    assert names == {"busy-1", "capped"}


def test_normal_polls_count_from_the_cap(events):
    # a file system that has been quiet for a while is capped by hand: the
    # lift's counter starts again at the cap
    client = FakeClient(fs_perf=[perf_row("v", 10, 10)], file_systems=[fs("v", 1)])
    arr = FakeArray(client)
    state = {}
    args = {"top_file_systems": 5, "baseline": False}

    def fs_event():
        out = events._performance_events(arr, state, args)
        return next(
            e["purefb_fs_performance"]
            for e in out
            if e.get("purefb_fs_performance", {}).get("file_system") == "v"
        )

    assert fs_event()["normal_polls"] == 1
    assert fs_event()["normal_polls"] == 2
    client.file_systems = [fs("v", 1, qos="eda-cap-v")]
    capped = fs_event()
    assert capped["eda_capped"] is True and capped["normal_polls"] == 0
    assert fs_event()["normal_polls"] == 1
    assert state["telemetry"]["fb-a"]["file_system_capped_last_poll"] == ["v"]


def test_bucket_performance_events(events):
    client = FakeClient(
        bucket_perf=[perf_row("logs", 900, 900), perf_row("archive", 100, 100)],
        buckets=[bucket("logs", 1, qos="eda-cap-logs"), bucket("archive", 1)],
    )
    out = events._performance_events(
        FakeArray(client), {}, {"top_buckets": 1, "baseline": False}
    )
    rows = {
        e["purefb_bucket_performance"]["bucket"]: e["purefb_bucket_performance"]
        for e in out
        if "purefb_bucket_performance" in e
    }
    assert set(rows) == {"logs"}  # top 1, and it is the capped one too
    assert rows["logs"]["eda_capped"] is True
    assert rows["logs"]["ops_share_pct"] == 90.0
    assert rows["logs"]["high_share_polls"] == 1
    assert rows["logs"]["qos_policy"] == "eda-cap-logs"
    assert "reads_per_sec" in rows["logs"] and "usec_per_other_op" in rows["logs"]


# --- config ----------------------------------------------------------------
def test_config_fields_and_csv_sorting(events):
    client = FakeClient(ad=[{"name": "ad1", "domain": "lab.example"}])
    (out,) = events.poll_config(FakeArray(client), {}, {})
    cfg = out["purefb_config"]
    assert cfg["array"] == "fb-a"
    assert cfg["purity_version"] == "4.6.4"
    assert cfg["ntp_servers_csv"] == "0.pool.ntp.org,1.pool.ntp.org"
    assert cfg["time_zone"] == "Europe/London"
    assert cfg["dns_domain"] == "lab.example"
    assert cfg["dns_nameservers_csv"] == "1.1.1.1,8.8.8.8"
    assert cfg["ds_management_enabled"] is True
    assert cfg["ds_management_uris_csv"] == "ldap://10.0.0.1,ldap://10.0.0.2"
    assert cfg["ds_management_base_dn"] == "DC=lab,DC=example"
    assert cfg["ds_management_bind_user"] == "svc-fusion"
    assert cfg["ds_nfs_enabled"] is False
    assert cfg["ad_joined"] is True
    assert cfg["manual_eradication"] == "all-enabled"
    assert cfg["eradication_delay_ms"] == 86400000
    assert cfg["lockout_duration_ms"] == 300000
    assert cfg["max_login_attempts"] == 5
    assert cfg["min_password_length"] == 10
    assert cfg["sso_enabled"] is False
    assert cfg["syslog_servers_csv"] == "tcp://10.0.0.5:514,tcp://10.0.0.6:514"
    assert cfg["syslog_data_audit_servers_csv"] == "tcp://10.0.0.5:514"
    assert cfg["smtp_relay_host"] is None
    assert cfg["smtp_sender_domain"] == "example"
    assert cfg["alert_watchers_enabled_csv"] == "ops@example"
    assert cfg["phonehome_enabled"] is True
    assert cfg["remote_assist_status"] == "disconnected"
    assert cfg["banner_set"] is True
    assert cfg["default_inbound_tls_policy"] == "default-tls-policy"
    assert cfg["data_at_rest_encryption"] is True
    assert cfg["smb_mode"] == "native"
    assert cfg["network_access_policy"] == "default-network-access-policy"
    assert cfg["maintenance_active"] is False


def test_config_tolerates_missing_optional_endpoints_and_fields(events):
    client = FakeClient(
        arrays={"version": "4.5.6", "ntp_servers": []},
        dns=[{"name": "management", "domain": None, "nameservers": []}],
        ds=[],
        idps=[{"enabled": True}],
        syslog=[],
        smtp=[],
        watchers=[],
    )
    client.get_active_directory = lambda **kw: FakeError("not here", 404)
    (out,) = events.poll_config(FakeArray(client), {}, {})
    cfg = out["purefb_config"]
    assert cfg["ad_joined"] is False
    assert cfg["sso_enabled"] is True
    assert cfg["ntp_servers_csv"] == "" and cfg["syslog_servers_csv"] == ""
    assert cfg["ds_management_enabled"] is False and cfg["ds_nfs_enabled"] is False
    assert cfg["banner_set"] is False
    assert cfg["data_at_rest_encryption"] is None
    assert cfg["default_inbound_tls_policy"] is None
    assert cfg["smtp_relay_host"] is None and cfg["eradication_delay_ms"] is None

    class NoSso(FakeClient):
        @property
        def get_sso_saml2_idps(self):
            raise AttributeError("absent at this REST version")

    (out,) = events.poll_config(FakeArray(NoSso()), {}, {})
    assert out["purefb_config"]["sso_enabled"] is False


# --- the asyncio driver ----------------------------------------------------
class LockedArray(FakeArray):
    def __init__(self, client, name):
        super().__init__(client, name)
        self.lock = asyncio.Lock()
        self.resets = 0

    def reset(self):
        self.resets += 1


def _run_loop(events, signal, arrays, args, done_when, timeout=20):
    got = []

    async def go():
        done = asyncio.Event()

        class Q:
            async def put(self, event):
                # emitted after run() returned, never while an array lock is held
                assert all(not a.lock.locked() for a in arrays)
                got.append(event)
                if done_when(got):
                    done.set()

        with ThreadPoolExecutor(max_workers=len(arrays) + 1) as ex:
            task = asyncio.create_task(
                events._signal_loop(Q(), signal, arrays, {}, args, ex)
            )
            await asyncio.wait_for(done.wait(), timeout)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(go())
    return got


def test_signal_loop_telemetry_polls_each_array_under_its_lock(events):
    """One cycle of the telemetry signal: the three steps run per array
    through run() (lock held, reset() on failure), the failing array yields
    one purefb_poll_error and nothing else, and the capacity events come
    last, after every array's row is in."""
    good = LockedArray(FakeClient(file_systems=[fs("full", 10, virtual=900)]), "fb-a")
    bad_client = FakeClient()
    bad_client.get_arrays_space = lambda **kw: FakeError("array down")
    bad = LockedArray(bad_client, "fb-b")
    got = _run_loop(
        events,
        "telemetry",
        [good, bad],
        {"baseline": False},
        lambda got: any("purefb_capacity" in e for e in got),
    )
    kinds = [next(iter(e)) for e in got]
    assert kinds == [
        "purefb_array_performance",
        "purefb_fs_quota",
        "purefb_poll_error",
        "purefb_capacity",
    ]
    err = got[2]["purefb_poll_error"]
    assert err == {
        "signal": "telemetry",
        "array": "fb-b",
        "error": "arrays/space: array down",
        "consecutive": 1,
        "escalate": False,
    }
    assert bad.resets == 1 and good.resets == 0
    cap = got[3]["purefb_capacity"]
    assert cap["array"] == "fb-a" and cap["others"] == []
    assert cap["used_pct"] == 50.0


def test_signal_loop_other_signals_report_a_failed_array_and_carry_on(events):
    good = LockedArray(FakeClient(alerts=[alert("1")]), "fb-a")
    bad_client = FakeClient()
    bad_client.get_alerts = lambda **kw: FakeError("nope", 500)
    bad = LockedArray(bad_client, "fb-b")
    got = _run_loop(events, "alerts", [bad, good], {}, lambda got: len(got) >= 2)
    assert got[0]["purefb_poll_error"]["array"] == "fb-b"
    assert got[0]["purefb_poll_error"]["signal"] == "alerts"
    assert got[1]["purefb_alert"]["array"] == "fb-a"
    assert bad.resets == 1


def test_signal_loop_counts_a_streak_and_escalates_at_the_threshold(events):
    # fail, fail, succeed, fail: the streak reaches the threshold on the
    # second failure, the success ends it, and the next failure starts again
    client = FakeClient(alerts=[alert("1")])
    outcomes = iter([False, False, True, False])
    real_get_alerts = client.get_alerts

    def get_alerts(**kw):
        # a 500, not a 400: a 400 on the state filter means "filter rejected"
        return real_get_alerts(**kw) if next(outcomes, True) else FakeError("nope", 500)

    client.get_alerts = get_alerts
    arr = LockedArray(client, "fb-b")
    got = _run_loop(
        events,
        "alerts",
        [arr],
        {"alerts_interval": 1, "poll_error_escalate": 2},
        lambda got: len(got) >= 4,  # error, error, the initial alert, error
    )
    errors = [e["purefb_poll_error"] for e in got if "purefb_poll_error" in e]
    assert [(e["consecutive"], e["escalate"]) for e in errors] == [
        (1, False),
        (2, True),
        (1, False),
    ]
    assert any("purefb_alert" in e for e in got)


def test_signal_loop_runs_every_poller(events):
    """Each non-telemetry signal has a poller and one quiet cycle of each
    emits only its own event kinds."""
    assert set(events.POLLERS) == set(events.SIGNALS) - {"telemetry"}
    client = FakeClient(
        alerts=[alert("1")],
        lags=[lag()],
        connectors=connectors(),
        neighbors=neighbors(),
    )
    arr = LockedArray(client, "fb-a")
    expected = {
        "alerts": "purefb_alert",
        "audits": None,
        "sessions": None,
        "health": "purefb_health",
        "config": "purefb_config",
    }
    for signal, kind in expected.items():
        if kind is None:
            continue
        got = _run_loop(events, signal, [arr], {}, lambda got: len(got) >= 1)
        assert next(iter(got[0])) == kind
        assert got[0][kind]["array"] == "fb-a"
