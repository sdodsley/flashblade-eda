# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Tests for tools/telemetry_rules.py, the generator of the Prometheus rule
file: the lab's policy listings (tests/fixtures/telemetry_rules.json, the
twelve policies and 35 metrics of Purity//FB 4.6.4, one metric per rule;
tests/fixtures/telemetry_rules_488.json, the same policies on 4.8.8, one
metrics collection per rule with its own resolution) render the golden
file and the committed prometheus/flashblade.rules.yml byte for byte; the
output is valid YAML carrying the alert names the Alertmanager rulebook
routes by and the labels every alert must have; the window and the
first-metric choice behave; the live reader logs in, pages, reads the
collections when the rules name them and logs out without ever showing
the token. Nothing here reaches an array."""

import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import urllib.error
from email.message import Message

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "telemetry_rules.py"
FIXTURE = ROOT / "tests" / "fixtures" / "telemetry_rules.json"
FIXTURE_488 = ROOT / "tests" / "fixtures" / "telemetry_rules_488.json"
GOLDEN = ROOT / "tests" / "fixtures" / "flashblade.rules.yml"
COMMITTED = ROOT / "prometheus" / "flashblade.rules.yml"
ARRAY = "fb-a"
POLICIES = [
    "bucket_client_perf_metrics",
    "bucket_perf_metrics",
    "bucket_space_metrics",
    "device_client_perf_metrics",
    "device_connector_metrics",
    "device_perf_metrics",
    "device_replication_perf_metrics",
    "device_space_metrics",
    "device_state_metrics",
    "fs_perf_metrics",
    "fs_space_metrics",
    "obj_store_account_space_metrics",
]
# the policies a 4.6.4 array pushes every minute; the rest every ten
ONE_MINUTE = {"device_connector_metrics", "device_perf_metrics", "device_space_metrics"}
# the policies a 4.8.8 array pushes every 30 seconds; the rest every five minutes
THIRTY_SECONDS = {
    "bucket_perf_metrics",
    "device_connector_metrics",
    "device_perf_metrics",
    "fs_perf_metrics",
}
ALERTS = {
    "FlashBladeTelemetrySilent",
    "FlashBladeHealthNotOk",
    "FlashBladeCapacityHigh",
    "FlashBladeLatencyHigh",
    "FlashBladeConnectorErrors",
    "FlashBladeReplicationStalled",
    "FlashBladeCriticalAlertOpen",
}
TOKEN = "not-a-real-token-0123456789abcdef"


@pytest.fixture(scope="module")
def gen():
    spec = importlib.util.spec_from_file_location("telemetry_rules", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def items():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["items"]


@pytest.fixture(scope="module")
def listing_488():
    return json.loads(FIXTURE_488.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rendered(gen, items):
    return gen.render(gen.policies_from_rules(items), [ARRAY], 10, "device_name")


@pytest.fixture(scope="module")
def rendered_488(gen, listing_488):
    policies = gen.policies_from_rules(listing_488["items"], listing_488["collections"])
    return gen.render(policies, [ARRAY], 10, "device_name")


def _rules(text):
    return [rule for group in yaml.safe_load(text)["groups"] for rule in group["rules"]]


# ------------------------------------------------------------- the fixture
def test_fixture_is_the_lab_listing(items):
    assert len(items) == 35
    assert sorted({i["policy"]["name"] for i in items}) == POLICIES
    for item in items:
        assert item["name"] == f"{item['policy']['name']}.{item['index']}"
        assert item["metric_name"].startswith("storage.device.")
        expected = 60000 if item["policy"]["name"] in ONE_MINUTE else 600000
        assert item["resolution"] == expected, item["name"]


# ------------------------------------------------------------- the output
def test_golden_file_is_the_rendering(rendered):
    assert rendered == GOLDEN.read_text(encoding="utf-8")


def test_488_fixture_is_the_lab_listing(listing_488):
    items = listing_488["items"]
    assert len(items) == 12
    assert sorted(i["policy"]["name"] for i in items) == POLICIES
    for item in items:
        assert item["name"] == f"{item['policy']['name']}.{item['index']}"
        assert "metric_name" not in item and item["collection"]["name"].startswith("_")
        expected = 30000 if item["policy"]["name"] in THIRTY_SECONDS else 300000
        assert item["resolution"] == expected, item["name"]
    collections = listing_488["collections"]
    assert len(collections) == 19
    used = [collections[i["collection"]["name"]] for i in items]
    assert sum(len(c) for c in used) == 36
    assert all(m.startswith("storage.device.") for c in used for m in c)


def test_committed_rule_file_is_the_rendering(rendered_488):
    """prometheus/flashblade.rules.yml is the generator's output for the
    4.8.8 fixture listing with --array-name fb-a; regenerate it with
    `python tools/telemetry_rules.py --from-file tests/fixtures/telemetry_rules_488.json --array-name fb-a`.
    """
    assert rendered_488 == COMMITTED.read_text(encoding="utf-8")


def test_output_is_yaml_with_the_expected_alerts(rendered):
    assert rendered.startswith("---\n# Copyright 2026 Everpure, Inc.\n")
    rules = _rules(rendered)
    assert {r["alert"] for r in rules} == ALERTS
    silence = [r for r in rules if r["alert"] == "FlashBladeTelemetrySilent"]
    # the per-client policies push nothing on an idle array, so no silence rule
    assert [r["labels"]["policy"] for r in silence] == [
        p for p in POLICIES if "client" not in p
    ]
    for rule in rules:
        assert set(rule["labels"]) >= {"severity", "domain", "array_name"}, rule
        assert rule["labels"]["array_name"] == "{{ $labels.device_name }}"
        assert rule["labels"]["severity"] in ("critical", "warning")
        assert "summary" in rule["annotations"], rule["alert"]
    capacity = next(r for r in rules if r["alert"] == "FlashBladeCapacityHigh")
    assert capacity["annotations"]["value"] == '{{ $value | printf "%.1f" }}'
    assert capacity["labels"]["domain"] == "capacity"
    for rule in silence:
        assert rule["labels"]["severity"] == "critical"
        assert rule["labels"]["domain"] == "telemetry"
        assert "for" not in rule, "the window is the delay"


def test_silence_rules_name_the_array_and_the_first_metric(rendered):
    silence = {
        r["labels"]["policy"]: r["expr"]
        for r in _rules(rendered)
        if r["alert"] == "FlashBladeTelemetrySilent"
    }
    # 1 minute policies wait the 10 minute floor; 10 minute policies twice theirs
    assert (
        silence["device_perf_metrics"]
        == 'absent_over_time(storage_device_bw_bpo{device_name="fb-a"}[10m])'
    )
    assert (
        silence["fs_perf_metrics"]
        == 'absent_over_time(storage_device_fs_bw_bpo{device_name="fb-a"}[20m])'
    )
    assert (
        silence["device_state_metrics"]
        == 'absent_over_time(storage_device_alerts{device_name="fb-a"}[20m])'
    )
    for expr in silence.values():
        assert "[10m]" in expr or "[20m]" in expr


def test_several_arrays_give_one_silence_rule_each(gen, items):
    text = gen.render(
        gen.policies_from_rules(items), ["fb-b", "fb-a"], 10, "device_name"
    )
    silence = [r for r in _rules(text) if r["alert"] == "FlashBladeTelemetrySilent"]
    # ten policies (the two per-client ones get no rule) for two arrays
    assert len(silence) == 20
    # sorted arrays inside each policy, so the output does not depend on the
    # order the names were given in
    assert silence[0]["expr"].endswith('{device_name="fb-a"}[20m])')
    assert silence[1]["expr"].endswith('{device_name="fb-b"}[20m])')
    assert "arrays fb-a, fb-b" in text


def test_array_label_option_changes_every_rule(gen, items):
    text = gen.render(gen.policies_from_rules(items), [ARRAY], 10, "array")
    rules = _rules(text)
    assert all(r["labels"]["array_name"] == "{{ $labels.array }}" for r in rules)
    assert "device_name" not in text


def test_render_refuses_nothing_to_watch(gen, items):
    with pytest.raises(gen.RulesError):
        gen.render(gen.policies_from_rules(items), [], 10, "device_name")
    with pytest.raises(gen.RulesError):
        gen.render({}, [ARRAY], 10, "device_name")


# ------------------------------------------------------------- the maths
def test_policies_from_rules_orders_by_index_and_keeps_the_first_resolution(gen):
    listing = [
        {
            "name": "p.2",
            "policy": {"name": "p"},
            "index": 2,
            "metric_name": "a.second",
            "resolution": 600000,
        },
        {
            "name": "p.1",
            "policy": {"name": "p"},
            "index": 1,
            "metric_name": "a.first",
            "resolution": 60000,
        },
        {"name": "q.1", "policy": {"name": "q"}, "index": 1, "resolution": 60000},
        {"name": "a.1", "index": 1, "metric_name": "z", "resolution": 30000},
    ]
    policies = gen.policies_from_rules(listing)
    # q has no metric name and is dropped; a's policy comes from its rule name
    assert list(policies) == ["a", "p"]
    assert policies["p"] == {"metrics": ["a.first", "a.second"], "resolution_ms": 60000}
    assert policies["a"] == {"metrics": ["z"], "resolution_ms": 30000}


def test_policies_from_rules_reads_collections(gen):
    listing = [
        {
            "name": "p.2",
            "policy": {"name": "p"},
            "index": 2,
            "resolution": 300000,
            "collection": {"name": "_p_slow"},
        },
        {
            "name": "p.1",
            "policy": {"name": "p"},
            "index": 1,
            "resolution": 30000,
            "collection": {"name": "_p_fast"},
        },
        {
            "name": "q.1",
            "policy": {"name": "q"},
            "index": 1,
            "resolution": 30000,
            "collection": {"name": "_unknown"},
        },
        {"name": "r.1", "policy": {"name": "r"}, "index": 1, "resolution": 60000},
    ]
    collections = {"_p_fast": ["b.first", "a.first"], "_p_slow": ["z.slow"]}
    policies = gen.policies_from_rules(listing, collections)
    # q names a collection the map lacks and r names nothing: both dropped
    assert list(policies) == ["p"]
    assert policies["p"] == {
        "metrics": ["b.first", "a.first", "z.slow"],
        "resolution_ms": 30000,
    }
    assert gen.policies_from_rules(listing) == {}


def test_from_file_with_collections_renders_the_committed_file(
    gen, capsys, rendered_488
):
    argv = ["--from-file", str(FIXTURE_488), "--array-name", ARRAY, "--out", "-"]
    assert gen.main(argv) == 0
    assert capsys.readouterr().out == rendered_488
    assert (
        gen.main(["--from-file", str(FIXTURE_488), "--array-name", ARRAY, "--check"])
        == 0
    )
    assert "12 policies, 36 metrics, 1 array" in capsys.readouterr().out


def test_prometheus_name_and_window(gen):
    assert gen.prometheus_name("storage.device.bw_bpo") == "storage_device_bw_bpo"
    assert gen.prometheus_name("a.b-c:d") == "a_b_c:d"
    assert gen.silence_window_minutes(60000, 10) == 10
    assert gen.silence_window_minutes(600000, 10) == 20
    assert gen.silence_window_minutes(30000, 1) == 1
    assert gen.silence_window_minutes(90000, 1) == 3


# ------------------------------------------------------------- the command
def test_from_file_writes_and_checks(gen, tmp_path, capsys):
    out = tmp_path / "rules.yml"
    argv = ["--from-file", str(FIXTURE), "--array-name", ARRAY, "--out", str(out)]
    assert gen.main(argv) == 0
    assert out.read_text(encoding="utf-8") == GOLDEN.read_text(encoding="utf-8")
    assert "12 policies, 35 metrics, 1 array" in capsys.readouterr().out
    assert gen.main(argv + ["--check"]) == 0
    out.write_text("stale\n", encoding="utf-8")
    assert gen.main(argv + ["--check"]) == 1
    assert "differs" in capsys.readouterr().out
    assert gen.main(argv + ["--silence-minutes", "30", "--check"]) == 1


def test_from_file_to_stdout_and_a_bare_list(gen, tmp_path, capsys, items):
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps(items), encoding="utf-8")
    assert (
        gen.main(["--from-file", str(bare), "--array-name", ARRAY, "--out", "-"]) == 0
    )
    assert capsys.readouterr().out == GOLDEN.read_text(encoding="utf-8")


def test_usage_errors(gen, tmp_path):
    with pytest.raises(SystemExit) as exc:
        gen.main([])
    assert exc.value.code == 2
    with pytest.raises(SystemExit):
        gen.main(["--from-file", str(FIXTURE)])  # no --array-name
    with pytest.raises(SystemExit):
        gen.main(
            ["fb-a", "FB_A_TOKEN", "--from-file", str(FIXTURE), "--array-name", "x"]
        )
    with pytest.raises(SystemExit):
        gen.main(
            ["--from-file", str(FIXTURE), "--array-name", "x", "--silence-minutes", "0"]
        )
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert (
        gen.main(["--from-file", str(broken), "--array-name", "x", "--out", "-"]) == 1
    )
    notalist = tmp_path / "dict.json"
    notalist.write_text('{"total_item_count": 0}', encoding="utf-8")
    assert (
        gen.main(["--from-file", str(notalist), "--array-name", "x", "--out", "-"]) == 1
    )


def test_missing_token_variable_is_named_not_read(gen, monkeypatch, capsys):
    monkeypatch.delenv("FB_X_TOKEN", raising=False)
    assert gen.main(["fb-x.example.com", "FB_X_TOKEN", "--out", "-"]) == 1
    assert "FB_X_TOKEN is not set" in capsys.readouterr().err


# ------------------------------------------------------------- the array
class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self._body = body
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeArray:
    """urllib.request.urlopen as the array answers it: version listing,
    login, two pages of rules (the first with a continuation token), the
    arrays listing, logout. Every request is kept for the assertions."""

    def __init__(self, items, versions=("2.19", "2.20", "2.21")):
        self.items = items
        self.versions = list(versions)
        self.requests = []
        self.logged_out = False

    def __call__(self, request, timeout=None, context=None):
        self.requests.append(request)
        path = request.full_url.split("/api", 1)[1]
        method = request.get_method()
        if path == "/api_version":
            return FakeResponse(body=json.dumps({"versions": self.versions}).encode())
        if path == "/login":
            if request.get_header("Api-token") != TOKEN:
                return FakeResponse(
                    401, json.dumps({"errors": [{"message": "invalid token"}]}).encode()
                )
            return FakeResponse(headers={"x-auth-token": "session-1"})
        if request.get_header("X-auth-token") != "session-1":
            return FakeResponse(401, b'{"errors": [{"message": "not logged in"}]}')
        if path == "/logout":
            self.logged_out = True
            return FakeResponse()
        if path.startswith("/2.21/telemetry-metrics-policies/rules?"):
            half = len(self.items) // 2
            if "continuation_token=page2" in path:
                page = {"items": self.items[half:], "continuation_token": None}
            else:
                page = {"items": self.items[:half], "continuation_token": "page2"}
            return FakeResponse(body=json.dumps(page).encode())
        if path.startswith("/2.21/arrays?"):
            return FakeResponse(body=json.dumps({"items": [{"name": ARRAY}]}).encode())
        if method == "GET":
            return FakeResponse(404, b'{"errors": [{"message": "not found"}]}')
        raise AssertionError(f"unexpected {method} {path}")


def test_live_listing_logs_in_pages_and_logs_out(gen, items, monkeypatch, capsys):
    fake = FakeArray(items)
    monkeypatch.setattr(gen.urllib.request, "urlopen", fake)
    monkeypatch.setenv("FB_T_TOKEN", TOKEN)
    assert gen.main(["fb-t.example.com", "FB_T_TOKEN", "--out", "-"]) == 0
    out = capsys.readouterr()
    assert out.out == GOLDEN.read_text(encoding="utf-8")
    assert TOKEN not in out.out and TOKEN not in out.err
    paths = [r.full_url.split("/api", 1)[1] for r in fake.requests]
    assert paths[0] == "/api_version" and paths[1] == "/login"
    assert paths[-1] == "/logout" and fake.logged_out
    assert sum("telemetry-metrics-policies/rules" in p for p in paths) == 2
    assert any(p.startswith("/2.21/arrays?") for p in paths)
    assert fake.requests[0].full_url.startswith("https://fb-t.example.com/api")
    # the token travels in the login header only
    assert [r for r in fake.requests if r.get_header("Api-token")] == [fake.requests[1]]


def test_live_listing_accepts_a_scheme_a_given_version_and_names(
    gen, items, monkeypatch, capsys
):
    fake = FakeArray(items)
    monkeypatch.setattr(gen.urllib.request, "urlopen", fake)
    monkeypatch.setenv("FB_T_TOKEN", TOKEN)
    argv = [
        "https://fb-t.example.com/",
        "FB_T_TOKEN",
        "--api-version",
        "2.21",
        "--array-name",
        "fb-b",
        "--array-name",
        "fb-a",
        "--out",
        "-",
    ]
    assert gen.main(argv) == 0
    paths = [r.full_url.split("/api", 1)[1] for r in fake.requests]
    assert "/api_version" not in paths, "a given version skips the negotiation"
    assert "arrays fb-a, fb-b" in capsys.readouterr().out


def test_live_failures_never_show_the_token(gen, items, monkeypatch, capsys):
    monkeypatch.setenv("FB_T_TOKEN", TOKEN)
    # an array too old for the listing
    fake = FakeArray(items, versions=("2.16", "2.17"))
    monkeypatch.setattr(gen.urllib.request, "urlopen", fake)
    assert gen.main(["fb-t.example.com", "FB_T_TOKEN", "--out", "-"]) == 1
    err = capsys.readouterr().err
    assert "2.17" in err and "2.21" in err and TOKEN not in err
    # a token the array refuses
    monkeypatch.setenv("FB_T_TOKEN", "wrong-token")
    fake = FakeArray(items)
    monkeypatch.setattr(gen.urllib.request, "urlopen", fake)
    assert gen.main(["fb-t.example.com", "FB_T_TOKEN", "--out", "-"]) == 1
    err = capsys.readouterr().err
    assert "login" in err and "401" in err
    assert "wrong-token" not in err and TOKEN not in err
    assert not fake.logged_out, "nothing to log out of"

    # a network failure before any answer
    def unreachable(request, timeout=None, context=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(gen.urllib.request, "urlopen", unreachable)
    monkeypatch.setenv("FB_T_TOKEN", TOKEN)
    assert gen.main(["fb-t.example.com", "FB_T_TOKEN", "--out", "-"]) == 1
    err = capsys.readouterr().err
    assert "connection refused" in err and TOKEN not in err


def test_live_error_answer_is_reported_and_the_session_closed(
    gen, items, monkeypatch, capsys
):
    class Failing(FakeArray):
        def __call__(self, request, timeout=None, context=None):
            path = request.full_url.split("/api", 1)[1]
            if path.startswith("/2.21/arrays?"):
                self.requests.append(request)
                return FakeResponse(
                    500, json.dumps({"errors": [{"message": "internal"}]}).encode()
                )
            return super().__call__(request, timeout, context)

    fake = Failing(items)
    monkeypatch.setattr(gen.urllib.request, "urlopen", fake)
    monkeypatch.setenv("FB_T_TOKEN", TOKEN)
    assert gen.main(["fb-t.example.com", "FB_T_TOKEN", "--out", "-"]) == 1
    err = capsys.readouterr().err
    assert "arrays" in err and "internal" in err and TOKEN not in err
    assert fake.logged_out, "the session is closed on failure too"


def test_prometheus_material_has_no_sibling_leftovers():
    """The rulebook test scans rulebooks, playbooks, vars and fixtures for
    words from the sibling project; prometheus/ and tools/ are outside its
    walk, so the same regex runs over them here."""
    leftovers = re.compile(
        r"purefa_|fa_url|ARRAY_[AB]_|flasharray|protection group|ActiveCluster|\bpods?\b|\bvolumes?\b",
        re.IGNORECASE,
    )
    files = sorted((ROOT / "prometheus").glob("*")) + sorted(
        (ROOT / "tools").glob("*.py")
    )
    offenders = [
        f"{p.relative_to(ROOT)}:{i}: {line.strip()}"
        for p in files
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if leftovers.search(line)
    ]
    assert offenders == [], "\n".join(offenders)


def test_tool_runs_as_a_script(items):
    """python tools/telemetry_rules.py is the documented entry point."""
    proc = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--from-file",
            str(FIXTURE),
            "--array-name",
            ARRAY,
            "--out",
            "-",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.replace("\r\n", "\n") == GOLDEN.read_text(encoding="utf-8")


def test_nothing_imported_beyond_the_standard_library():
    """The tool runs on a receiver host with no Python packages installed."""
    stdlib = getattr(sys, "stdlib_module_names", None)
    if stdlib is None:
        pytest.skip("sys.stdlib_module_names needs Python 3.10")
    source = TOOL.read_text(encoding="utf-8")
    for line in source.splitlines():
        if line.startswith(("import ", "from ")):
            module = line.split()[1].split(".")[0]
            assert module in stdlib, line


class FakeArray488(FakeArray):
    """A 4.8.8 array: REST up to 2.30, rules naming collections, the
    collection membership listing, no telemetry endpoint below 2.29."""

    def __init__(self, listing):
        super().__init__(listing["items"], versions=("2.21", "2.29", "2.30"))
        self.collections = listing["collections"]

    def __call__(self, request, timeout=None, context=None):
        path = request.full_url.split("/api", 1)[1]
        if request.get_header("X-auth-token") == "session-1" and path.startswith(
            "/2.30/"
        ):
            self.requests.append(request)
            if path.startswith("/2.30/telemetry-metrics-policies/rules?"):
                page = {"items": self.items, "continuation_token": None}
            elif path.startswith("/2.30/telemetry-metrics-collections/metrics?"):
                rows = [
                    {"group": {"name": group}, "member": {"name": metric}}
                    for group, metrics in self.collections.items()
                    for metric in reversed(metrics)
                ]
                page = {"items": rows, "continuation_token": None}
            elif path.startswith("/2.30/arrays?"):
                page = {"items": [{"name": ARRAY}]}
            else:
                return FakeResponse(404, b'{"errors": [{"message": "not found"}]}')
            return FakeResponse(body=json.dumps(page).encode())
        return super().__call__(request, timeout, context)


def test_live_listing_with_collections_renders_the_committed_file(
    gen, listing_488, rendered_488, monkeypatch, capsys
):
    fake = FakeArray488(listing_488)
    monkeypatch.setattr(gen.urllib.request, "urlopen", fake)
    monkeypatch.setenv("FB_T_TOKEN", TOKEN)
    assert gen.main(["fb-t.example.com", "FB_T_TOKEN", "--out", "-"]) == 0
    out = capsys.readouterr()
    assert out.out == rendered_488 and TOKEN not in out.out
    paths = [r.full_url.split("/api", 1)[1] for r in fake.requests]
    # the newest version is negotiated; the collections are read once
    assert sum("/2.30/telemetry-metrics-policies/rules" in p for p in paths) == 1
    assert sum("/2.30/telemetry-metrics-collections/metrics" in p for p in paths) == 1
    assert paths[-1] == "/logout" and fake.logged_out
