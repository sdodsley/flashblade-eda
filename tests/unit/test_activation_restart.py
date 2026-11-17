# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""tools/activation_restart.py waits for the EDA API, reads each expected
activation by its full name and restarts, one at a time, only the enabled
ones that are not running. The fake controller is the audit test's, with
the status call and the restart endpoint added."""

import importlib.util
import pathlib
import threading
from http.server import ThreadingHTTPServer

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "activation_restart.py"
AUDIT_TEST = pathlib.Path(__file__).with_name("test_activation_audit.py")
PREFIX = "FlashBlade rulebook - "


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fake = _load("activation_audit_fake", AUDIT_TEST)


@pytest.fixture(scope="module")
def restart():
    return _load("activation_restart", TOOL)


class Handler(fake.Handler):
    def do_GET(self):
        store = self.server.store
        if self.path == "/api/eda/v1/status/":
            store.calls.append(("GET", self.path))
            if store.status_down:
                # the API is still coming up behind the gateway
                store.status_down -= 1
                return self._send(503, {"detail": "starting"})
            return self._send(200, {"status": "ok"})
        for a in store.activations:
            if self.path == f"/api/eda/v1/activations/{a['id']}/":
                # the API, just up, still shows the state from before the reboot
                stale = store.stale_running.get(a["id"])
                if stale:
                    store.stale_running[a["id"]] = stale - 1
                    store.calls.append(("GET", self.path))
                    return self._send(200, dict(a, status="running"))
                # a start under way reaches running after so many reads
                left = store.reads_to_running.get(a["id"])
                if left is not None:
                    if left <= 1:
                        a["status"] = "running"
                        del store.reads_to_running[a["id"]]
                    else:
                        store.reads_to_running[a["id"]] = left - 1
        return super().do_GET()

    def do_POST(self):
        store = self.server.store
        if self.path.endswith("/restart/"):
            store.calls.append(("POST", self.path))
            aid = int(self.path.split("/")[-3])
            for a in store.activations:
                if a["id"] == aid:
                    a["restart_count"] = a.get("restart_count", 0) + 1
                    if store.restart_fails.get(aid):
                        store.restart_fails[aid] -= 1
                        a["status"] = "failed"
                    elif aid in store.restart_starting:
                        a["status"] = "starting"
                        store.reads_to_running[aid] = store.restart_starting.pop(aid)
                    else:
                        a["status"] = "running"
                    return self._send(204, {})
            return self._send(404, {"detail": "not found"})
        return super().do_POST()


class Store(fake.Store):
    def __init__(self):
        super().__init__()
        self.status_down = 0  # status calls answered 503 before the first 200
        self.restart_fails = {}  # id -> restarts that end failed before one runs
        self.restart_starting = {}  # id -> reads a restart stays "starting"
        self.reads_to_running = {}  # id -> reads until a start reaches running
        self.stale_running = {}  # id -> reads answered "running" from before a reboot


@pytest.fixture
def store():
    store = Store()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    httpd.store = store
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    store.url = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield store
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


@pytest.fixture
def rulebooks(tmp_path):
    """Four platform rulebooks: capacity, alerts and health have an
    activation in the tests, syslog never does."""
    d = tmp_path / "platform"
    d.mkdir()
    for stem in ("capacity", "alerts", "health", "syslog"):
        (d / f"{stem}.yml").write_text("")
    return d


def _activation(aid, stem, status, enabled=True, restarts=3):
    a = fake._activation(aid, PREFIX + stem, {"k": "v"}, enabled, status)
    a["restart_count"] = restarts
    return a


def _quick(monkeypatch, restart):
    monkeypatch.setenv("AAP_TOKEN", "tok")
    monkeypatch.setattr(restart, "POLL_SECONDS", 0)
    monkeypatch.setattr(restart, "GAP_SECONDS", 0)
    monkeypatch.setattr(restart, "PASS_GAP_SECONDS", 0)


def _posts(store):
    return [path for method, path in store.calls if method == "POST"]


def _status_reads(store):
    return sum(1 for m, p in store.calls if m == "GET" and p == "/api/eda/v1/status/")


def test_restarts_only_the_enabled_activations_that_are_not_running(
    restart, store, rulebooks, capsys, monkeypatch
):
    _quick(monkeypatch, restart)
    store.status_down = 2
    store.activations = [
        _activation(1, "capacity", "running"),
        _activation(2, "alerts", "failed"),
        _activation(3, "health", "failed", enabled=False),
    ]
    code = restart.main(["--url", store.url, "--rulebooks", str(rulebooks)])
    out = capsys.readouterr().out
    assert code == 0
    assert _status_reads(store) == 3
    assert _posts(store) == ["/api/eda/v1/activations/2/restart/"]
    assert "FlashBlade rulebook - alerts (id 2): running after 1 restart(s)" in out
    assert "FlashBlade rulebook - capacity (id 1): running" in out
    assert "FlashBlade rulebook - health (id 3): disabled, left alone" in out
    assert "FlashBlade rulebook - syslog: not on the controller" in out
    assert "activation_restart: 4 name(s), 1 restarted, 0 not running" in out
    # every read of the activations carries a name; the listing is never asked for
    lists = [p for m, p in store.calls if m == "GET" and "activations/?" in p]
    assert lists and all("name=" in p for p in lists)


def test_a_failed_restart_is_tried_three_times_then_given_up(
    restart, store, rulebooks, capsys, monkeypatch
):
    _quick(monkeypatch, restart)
    store.activations = [_activation(2, "alerts", "failed")]
    store.restart_fails = {2: 2}
    assert restart.main(["--url", store.url, "--rulebooks", str(rulebooks)]) == 0
    assert len(_posts(store)) == 3
    assert "running after 3 restart(s)" in capsys.readouterr().out

    store.calls.clear()
    store.activations = [_activation(2, "alerts", "failed")]
    store.restart_fails = {2: 9}
    assert restart.main(["--url", store.url, "--rulebooks", str(rulebooks)]) == 1
    out = capsys.readouterr().out
    assert len(_posts(store)) == 3
    assert "FlashBlade rulebook - alerts (id 2): still failed after 3 restart(s)" in out
    assert "1 restarted, 1 not running" in out


def test_a_start_under_way_is_waited_for_not_restarted(
    restart, store, rulebooks, capsys, monkeypatch
):
    _quick(monkeypatch, restart)
    store.activations = [_activation(2, "alerts", "starting")]
    store.reads_to_running = {2: 3}
    assert restart.main(["--url", store.url, "--rulebooks", str(rulebooks)]) == 0
    out = capsys.readouterr().out
    assert _posts(store) == []
    assert "FlashBlade rulebook - alerts (id 2): starting, waiting for it" in out
    assert "running after 0 restart(s)" in out


def test_a_restart_is_given_time_to_start(
    restart, store, rulebooks, capsys, monkeypatch
):
    _quick(monkeypatch, restart)
    store.activations = [_activation(2, "alerts", "failed")]
    store.restart_starting = {2: 3}
    assert restart.main(["--url", store.url, "--rulebooks", str(rulebooks)]) == 0
    assert len(_posts(store)) == 1
    assert "running after 1 restart(s)" in capsys.readouterr().out


def test_gives_up_when_the_api_never_answers(
    restart, store, rulebooks, capsys, monkeypatch
):
    _quick(monkeypatch, restart)
    store.status_down = 100
    store.activations = [_activation(2, "alerts", "failed")]
    code = restart.main(
        ["--url", store.url, "--rulebooks", str(rulebooks), "--wait-minutes", "0"]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "the EDA API did not answer within 0 minute(s)" in out
    assert not [p for m, p in store.calls if "activations" in p]


def test_a_second_pass_catches_the_state_from_before_the_reboot(
    restart, store, rulebooks, capsys, monkeypatch
):
    _quick(monkeypatch, restart)
    store.activations = [_activation(2, "alerts", "failed")]
    store.stale_running = {2: 1}
    assert restart.main(["--url", store.url, "--rulebooks", str(rulebooks)]) == 0
    out = capsys.readouterr().out
    assert "pass 1: 0 restarted, 0 not running" in out
    assert "pass 2: 1 restarted, 0 not running" in out
    assert len(_posts(store)) == 1
    assert "3 pass(es)" in out


def test_an_activation_that_used_its_restarts_is_not_restarted_again(
    restart, store, rulebooks, capsys, monkeypatch
):
    _quick(monkeypatch, restart)
    store.activations = [_activation(2, "alerts", "failed")]
    store.restart_fails = {2: 9}
    assert restart.main(["--url", store.url, "--rulebooks", str(rulebooks)]) == 1
    out = capsys.readouterr().out
    assert len(_posts(store)) == 3
    assert "gave up earlier" in out and "4 pass(es)" in out
