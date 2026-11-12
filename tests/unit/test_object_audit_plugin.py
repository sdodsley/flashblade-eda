# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the purefb_object_audit source plugin that need no array and no
bucket: the helpers, the key floor and the late-authority pickup, the cursor
file and its state bucket copy, replay_objects, every derived boolean of
purefb_s3_event, the rollup counts and storms, the health fields with and
without REST, a bad line, a listing failure, the arrays string, the client
construction, the documentation block and the asyncio driver, all driven by
a fake S3 client and a fake SDK client built from the lab's log records."""

import asyncio
import contextlib
import io
import json
import logging
import pathlib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import yaml

ARRAY_ID = "0f1e2d3c-4b5a-4978-8796-a5b4c3d2e1f0"
PREFIX = f"{ARRAY_ID}/audit_/"
ROTATION = 300_000
# the lab's window: traffic at 20:08:50Z, the objects appeared at 20:10:0xZ
NOW = "2026-11-12T20:10:30Z"
APP_KEY = "PSFBSAZRTESTKEY0001"  # the demo traffic, the "attacker"
STATE_KEY = "PSFBSAZRTESTKEY0002"  # the automation's writer
TAIL_KEY = "PSFBSAZRTESTKEY0003"  # the plugin's own read-only key
SECRET = "test-secret-value-never-logged"
ERRORS = {
    "NoSuchKey": "The specified key does not exist.",
    "SignatureDoesNotMatch": "The request signature we calculated does not match.",
    "AccessDenied": "Access Denied",
}

# the field inventory of the contract, checked against the docstring and
# against every emitted event
S3_EVENT_FIELDS = (
    "bucket",
    "key",
    "event_name",
    "event_time",
    "event_time_ms",
    "event_id",
    "read_only",
    "access_key_id",
    "user",
    "account",
    "source_ip",
    "user_agent",
    "error_code",
    "error_message",
    "ok",
    "version_id",
    "log_object",
    "by_automation",
    "canary",
    "canary_allowed",
    "landing",
    "dataset",
    "evidence",
    "honey",
    "approval",
    "request",
    "request_id",
    "denied",
    "delete",
)
ROLLUP_FIELDS = (
    "log_object",
    "window",
    "record_count",
    "first_time",
    "last_time",
    "buckets",
    "by_event_name",
    "by_access_key",
    "by_source_ip",
    "by_error_code",
    "error_count",
    "error_pct",
    "denied_count",
    "delete_count",
    "top_access_key",
    "top_access_key_count",
    "top_source_ip",
    "error_storm",
    "denied_storm",
    "mass_delete",
    "canary_hits",
    "honey_hits",
    "bad_lines",
)
HEALTH_FIELDS = (
    "log_bucket",
    "log_key_prefix",
    "objects_this_poll",
    "records_this_poll",
    "bad_lines_this_poll",
    "newest_log_object",
    "newest_log_age_s",
    "rotation_ms",
    "stale",
    "log_bucket_used_bytes",
    "log_bucket_quota_bytes",
    "log_bucket_used_pct",
    "log_bucket_quota_known",
    "log_bucket_object_count",
    "policies",
    "policies_enabled",
    "policies_disabled",
    "uncovered_buckets",
    "uncovered_count",
    "members_without_filters",
    "members_without_filters_count",
    "rest_available",
    "cursor_persisted",
    "healthy",
)


def ms(text):
    return int(dt(text).timestamp() * 1000)


def dt(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def log_key(hhmmss, authority, seq=1, day="2026/11/12"):
    return f"{PREFIX}{day}/{hhmmss}.A{authority}.{seq}.log"


def record(
    name="GetObject",
    key="canary/do-not-read.txt",
    bucket="eda-data",
    access_key=APP_KEY,
    user="eda-app",
    time="2026-11-12T20:08:50.692Z",
    error=None,
    event_id=1,
    version="AAAAAAAAAAOfDjBzWH7_ugCM",
    ip="10.21.22.12",
):
    """One record in the shape the lab array writes (see the samples): the
    key is absent for a bucket operation, responseElements is null on an
    error and errorCode/errorMessage follow the CloudTrail convention."""
    rec = {
        "eventId": event_id,
        "eventTime": time,
        "eventVersion": "1.0",
        "eventName": name,
        "readOnly": name.startswith(("Get", "Head", "List")),
        "eventCategory": "Data",
        "managementEvent": False,
        "eventSource": "onPrem",
        "sourceIPAddress": ip,
        "bucketName": bucket,
        "recipientAccountId": "eda",
        "resources": [
            {"type": "Pure::S3::Object", "ARN": f"prn::s3:::{bucket}/{key or ''}"},
            {
                "accountId": "eda",
                "type": "Pure::S3::Bucket",
                "ARN": f"prn::s3:::{bucket}",
            },
        ],
        "userIdentity": {
            "type": "IAMUser",
            "arn": f"prn::iam:array-id/local:obj-account-id/143:user/{user}",
            "accountId": "eda",
            "accessKeyId": access_key,
            "userName": user,
        },
        "userAgent": "Boto3/1.44.1 md/Botocore#1.44.1 ua/2.1 os/linux",
        "requestParameters": {"bucketName": bucket, "Host": "10.21.200.233"},
        "responseElements": (
            None if error else {"x-amz-version-id": version, "ETag": '"e5eb"'}
        ),
    }
    if key is not None:
        rec["requestParameters"]["key"] = key
    if error:
        rec["errorCode"] = error
        rec["errorMessage"] = ERRORS.get(error, "error")
    return rec


def ndjson(*records):
    return "".join(
        (r if isinstance(r, str) else json.dumps(r)) + "\n" for r in records
    ).encode()


class FakeS3:
    """Enough of a boto3 S3 client: list_objects_v2 with Prefix, StartAfter,
    Delimiter and MaxKeys/ContinuationToken pagination, get_object with a
    Body that reads, put_object recording the call, and failure injection."""

    def __init__(self, plugin, page_size=1000):
        self._error = plugin.ClientError
        self.page_size = page_size
        self.objects = {}  # key -> (body bytes, LastModified)
        self.calls = []
        self.puts = []
        self.fail_list = None
        self.fail_get = None
        self.fail_put = None

    def add(self, key, body, modified="2026-11-12T20:10:02Z"):
        body = body if isinstance(body, bytes) else body.encode()
        self.objects[key] = (body, dt(modified))
        return key

    def list_objects_v2(self, **kw):
        self.calls.append(("list_objects_v2", kw))
        if self.fail_list:
            raise self.fail_list
        prefix = kw.get("Prefix", "")
        after = kw.get("ContinuationToken") or kw.get("StartAfter") or ""
        keys = sorted(k for k in self.objects if k.startswith(prefix) and k > after)
        delimiter = kw.get("Delimiter")
        contents, common = [], []
        for k in keys:
            rest = k[len(prefix) :]
            if delimiter and delimiter in rest:
                p = prefix + rest.split(delimiter, 1)[0] + delimiter
                if p not in common:
                    common.append(p)
            else:
                contents.append(k)
        limit = kw.get("MaxKeys", self.page_size)
        page, more = contents[:limit], contents[limit:]
        resp = {
            "Contents": [
                {
                    "Key": k,
                    "LastModified": self.objects[k][1],
                    "Size": len(self.objects[k][0]),
                }
                for k in page
            ],
            "CommonPrefixes": [{"Prefix": p} for p in common],
            "IsTruncated": bool(more),
            "KeyCount": len(page),
        }
        if more:
            resp["NextContinuationToken"] = page[-1]
        return resp

    def get_object(self, **kw):
        self.calls.append(("get_object", kw))
        if self.fail_get:
            raise self.fail_get
        key = kw["Key"]
        if key not in self.objects:
            raise self._error(
                {"Error": {"Code": "NoSuchKey", "Message": ERRORS["NoSuchKey"]}},
                "GetObject",
            )
        return {"Body": io.BytesIO(self.objects[key][0])}

    def put_object(self, **kw):
        self.calls.append(("put_object", kw))
        if self.fail_put:
            raise self.fail_put
        self.puts.append(kw)
        self.objects[kw["Key"]] = (kw["Body"], dt(NOW))
        return {}


class FakeResponse:
    """What py-pure-client returns: .items of model objects with to_dict()."""

    def __init__(self, items):
        self.status_code = 200
        self.items = [SimpleNamespace(to_dict=lambda d=d: d, **d) for d in items]


class FakeError:
    """An ErrorResponse: no .items, a list of errors with a message."""

    def __init__(self, message, status=400):
        self.status_code = status
        self.errors = [SimpleNamespace(message=message)]


def bucket_row(name, virtual=100, quota=None, count=3, destroyed=False):
    return {
        "name": name,
        "destroyed": destroyed,
        "space": {"virtual": virtual, "total_physical": virtual // 2},
        "quota_limit": quota,
        "hard_limit_enabled": False,
        "object_count": count,
        "account": {"name": "eda"},
    }


def policy_row(name="eda-audit", enabled=True, targets=("eda-audit",)):
    return {
        "name": name,
        "enabled": enabled,
        "is_local": True,
        "policy_type": "audit",
        "log_targets": [
            {"name": t, "resource_type": "log-targets/object-store"} for t in targets
        ],
    }


def member_row(policy, bucket):
    return {
        "policy": {"name": policy, "resource_type": "audit-object-store-policies"},
        "member": {"name": bucket, "resource_type": "buckets"},
    }


class FakeClient:
    """Enough of pypureclient.flashblade.Client for the REST reads: rows are
    plain dicts in the array's shape, the calls are recorded."""

    def __init__(self, **data):
        self.calls = []
        self.arrays = data.get("arrays", {"name": "fb-lab", "id": ARRAY_ID})
        self.targets = data.get(
            "targets",
            [
                {
                    "name": "eda-audit",
                    "bucket": {"name": "eda-log"},
                    "log_name_prefix": {"prefix": "audit_"},
                    "log_rotate": {"duration": ROTATION},
                }
            ],
        )
        self.policies = data.get("policies", [policy_row()])
        self.members = data.get(
            "members",
            [member_row("eda-audit", "eda-data"), member_row("eda-audit", "eda-log")],
        )
        self.buckets = data.get(
            "buckets",
            [
                bucket_row("eda-data"),
                bucket_row("eda-log", virtual=900, quota=1000, count=12),
                bucket_row("eda-state"),
            ],
        )
        self.filters = data.get(
            "filters", {"eda-data": ["edaall"], "eda-log": ["edaapprovals"]}
        )

    def get_arrays(self, **kw):
        return FakeResponse([self.arrays])

    def get_log_targets_object_store(self, **kw):
        return FakeResponse(self.targets)

    def get_audit_object_store_policies(self, **kw):
        return FakeResponse(self.policies)

    def get_audit_object_store_policies_members(self, **kw):
        self.calls.append(("get_audit_object_store_policies_members", kw))
        names = kw.get("policy_names") or []
        return FakeResponse([m for m in self.members if m["policy"]["name"] in names])

    def get_buckets(self, **kw):
        self.calls.append(("get_buckets", kw))
        return FakeResponse(self.buckets)

    def get_buckets_audit_filters(self, **kw):
        self.calls.append(("get_buckets_audit_filters", kw))
        (bucket,) = kw["bucket_names"]
        if bucket not in {b["name"] for b in self.buckets}:
            return FakeError("Bucket does not exist.")
        return FakeResponse(
            [
                {
                    "name": n,
                    "actions": ["s3:All"],
                    "s3_prefixes": None,
                    "bucket": {"name": bucket},
                }
                for n in self.filters.get(bucket, [])
            ]
        )


def make_array(plugin, monkeypatch, s3, client=None, state_s3=None, **spec):
    """A real Array built from environment variables, with the fakes slotted
    in where the boto3 and SDK clients would be."""
    monkeypatch.setenv("FB_T_S3_ENDPOINT", "https://192.0.2.10")
    monkeypatch.setenv("FB_T_S3_ACCESS_KEY", TAIL_KEY)
    monkeypatch.setenv("FB_T_S3_SECRET_KEY", SECRET)
    entry = {
        "name": "fb-a",
        "s3_endpoint_env": "FB_T_S3_ENDPOINT",
        "s3_access_key_env": "FB_T_S3_ACCESS_KEY",
        "s3_secret_key_env": "FB_T_S3_SECRET_KEY",
    }
    if client is not None:
        monkeypatch.setenv("FB_T_URL", "https://192.0.2.1")
        monkeypatch.setenv("FB_T_TOKEN", "token-" + SECRET)
        entry.update(fb_url_env="FB_T_URL", api_token_env="FB_T_TOKEN")
    if state_s3 is not None:
        monkeypatch.setenv("FB_T_STATE_ACCESS_KEY", STATE_KEY)
        monkeypatch.setenv("FB_T_STATE_SECRET_KEY", "state-" + SECRET)
        entry.update(
            state_bucket="eda-state",
            state_access_key_env="FB_T_STATE_ACCESS_KEY",
            state_secret_key_env="FB_T_STATE_SECRET_KEY",
        )
    entry.update(spec)
    arr = plugin.Array(entry, False)
    arr._s3 = s3
    arr._client = client
    arr._state_s3 = state_s3
    return arr


def sample_bucket(plugin):
    """The lab's log bucket in miniature: an approval object that is not a
    log object, and two authorities of the 20:05 window."""
    s3 = FakeS3(plugin)
    s3.add("approvals/probe-1.json", b"{}", "2026-11-12T20:08:53Z")
    s3.add(
        log_key("200500", 133),
        ndjson(
            record(
                key="missing/20261112T150849-2",
                error="NoSuchKey",
                event_id=1,
                time="2026-11-12T20:08:51.805Z",
            )
        ),
        "2026-11-12T20:10:03Z",
    )
    s3.add(
        log_key("200500", 198),
        ndjson(
            record(name="PutObject", event_id=2, time="2026-11-12T20:08:50.346Z"),
            record(name="GetObject", event_id=3, time="2026-11-12T20:08:50.692Z"),
        ),
        "2026-11-12T20:10:00Z",
    )
    return s3


def cursor_args(tmp_path, **over):
    args = {"cursor_dir": str(tmp_path / "cursors")}
    args.update(over)
    return args


def kinds(events):
    return [next(iter(e)) for e in events]


def only(events, kind):
    return [e[kind] for e in events if kind in e]


@pytest.fixture
def clock(object_audit, monkeypatch):
    """The plugin's clock, frozen at NOW and advanced by the tests."""
    now = {"ms": ms(NOW)}
    monkeypatch.setattr(object_audit, "_now_ms", lambda: now["ms"])
    return now


# --- helpers ---------------------------------------------------------------
def test_shared_helpers_have_the_purefb_events_shapes(object_audit):
    assert object_audit._as_list("a, b") == ["a", "b"]
    assert object_audit._as_list(["x"]) == ["x"]
    assert object_audit._as_list(None) == []
    assert object_audit._truthy("Yes") and not object_audit._truthy("off")
    assert object_audit._truthy(None, True) is True
    assert object_audit._poll_error("object_audit", "fb-a", RuntimeError("boom")) == {
        "purefb_poll_error": {
            "signal": "object_audit",
            "array": "fb-a",
            "error": "boom",
            "consecutive": 1,
            "escalate": False,
        }
    }
    state = {}
    assert object_audit._poll_streak(state, "object_audit", "a", True) == 1
    assert object_audit._poll_streak(state, "object_audit", "a", True) == 2
    assert object_audit._poll_streak(state, "object_audit", "b", True) == 1
    assert object_audit._poll_streak(state, "object_audit", "a", False) == 0
    assert object_audit.SIGNAL == "object_audit"


def test_time_and_key_helpers(object_audit):
    assert object_audit._event_time_ms("2026-11-12T20:08:51.805Z") == (
        ms("2026-11-12T20:08:51Z") + 805
    )
    assert object_audit._event_time_ms("2026-11-12T20:08:51Z") == ms(
        "2026-11-12T20:08:51Z"
    )
    assert object_audit._event_time_ms("2026-11-12T20:08:51.1Z") == (
        ms("2026-11-12T20:08:51Z") + 100
    )
    assert object_audit._event_time_ms("yesterday") is None
    assert object_audit._event_time_ms(None) is None
    assert object_audit._log_window(log_key("200500", 133)) == "2026/11/12/200500"
    assert object_audit._log_window("approvals/probe-1.json") is None
    assert object_audit._log_window(None) is None
    assert object_audit._window_iso("2026/11/12/200500") == "2026-11-12T20:05:00Z"
    assert object_audit._window_ms("2026/11/12/200500") == ms("2026-11-12T20:05:00Z")
    assert object_audit._window_path(ms(NOW), ROTATION) == "2026/11/12/201000"
    assert object_audit._window_path(ms(NOW) - 2 * ROTATION, ROTATION) == (
        "2026/11/12/200000"
    )
    assert object_audit._under("landing/ds1/_SUCCESS", ("canary/", "landing/")) == (
        "landing/"
    )
    assert object_audit._under("other/x", ("canary/",)) == ""
    assert object_audit._top({"b": 2, "a": 2, "c": 1}) == ("a", 2)
    assert object_audit._top({}) == ("", 0)


def test_documentation_block_covers_every_argument_and_event(object_audit):
    """DOCUMENTATION parses as YAML, names the plugin and exactly the
    arguments the code reads; the module docstring names every event and
    every field of the contract."""
    doc = yaml.safe_load(object_audit.DOCUMENTATION)
    assert doc["name"] == "purefb_object_audit"
    options = set(doc["options"])
    source = pathlib.Path(object_audit.__file__).read_text()
    read = set(re.findall(r'args\.get\(\s*"([a-z_]+)"', source))
    assert (
        read == options
    ), f"undocumented: {sorted(read - options)}; unread: {sorted(options - read)}"
    assert doc["options"]["arrays"]["required"] is True
    assert doc["options"]["interval"]["default"] == 60
    assert doc["options"]["late_windows"]["default"] == 2
    assert doc["options"]["replay_objects"]["default"] == 0
    assert doc["options"]["log_bucket_quota_warn_pct"]["default"] == 80
    assert doc["options"]["coverage_exclude"]["default"] == ["eda-state"]
    emitted = set(re.findall(r'"(purefb_[a-z0-9_]+)":', source))
    assert emitted == {
        "purefb_s3_event",
        "purefb_s3_rollup",
        "purefb_audit_health",
        "purefb_poll_error",
    }
    for name in emitted:
        assert name in object_audit.__doc__, f"{name} missing from the docstring"
    for field in S3_EVENT_FIELDS + ROLLUP_FIELDS + HEALTH_FIELDS:
        assert field in object_audit.__doc__, f"{field} missing from the docstring"
    for option in options:
        assert option in object_audit.__doc__, f"{option} missing from the docstring"
    examples = yaml.safe_load(object_audit.EXAMPLES)
    assert examples[0]["sources"][0]["sdodsley.flashblade_eda.purefb_object_audit"]
    assert "purefa" not in source.lower()


# --- the array handle and the clients --------------------------------------
def test_array_reads_credentials_from_the_environment_and_never_shows_them(
    object_audit, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)
    arr = make_array(object_audit, monkeypatch, FakeS3(object_audit))
    assert arr.name == "fb-a" and arr.endpoint == "https://192.0.2.10"
    assert arr.access_key_id == TAIL_KEY and arr.state_access_key_id == ""
    assert arr.log_bucket == "eda-log" and arr.log_key_prefix == ""
    assert arr.has_rest is False and arr.state_bucket == ""
    # the endpoint and both keys are required; the messages name the fields
    with pytest.raises(ValueError, match="s3_endpoint") as exc:
        object_audit.Array({"name": "a", "s3_access_key": "k", "s3_secret_key": "s"}, 0)
    assert SECRET not in str(exc.value)
    with pytest.raises(ValueError, match="s3_access_key_env"):
        object_audit.Array(
            {"name": "a", "s3_endpoint": "https://x", "s3_access_key_env": "UNSET_X"},
            False,
        )
    # a literal key is accepted (discouraged), the token needs its url
    arr = object_audit.Array(
        {
            "name": "b",
            "s3_endpoint": "https://x/",
            "s3_access_key": "k",
            "s3_secret_key": SECRET,
        },
        False,
    )
    assert arr.endpoint == "https://x" and arr.access_key_id == "k"
    with pytest.raises(ValueError, match="fb_url and api_token") as exc:
        object_audit.Array(
            {
                "name": "c",
                "s3_endpoint": "https://x",
                "s3_access_key": "k",
                "s3_secret_key": SECRET,
                "api_token": "token-" + SECRET,
            },
            False,
        )
    assert SECRET not in str(exc.value) and SECRET not in caplog.text
    # a state bucket without its keys stays local, with a warning
    arr = make_array(
        object_audit, monkeypatch, FakeS3(object_audit), state_bucket="eda-state"
    )
    assert arr.state_bucket == "" and "state_access_key_env" in caplog.text
    assert SECRET not in caplog.text
    # with all three the state writer is known
    arr = make_array(
        object_audit, monkeypatch, FakeS3(object_audit), state_s3=FakeS3(object_audit)
    )
    assert arr.state_bucket == "eda-state" and arr.state_access_key_id == STATE_KEY
    # a bare log_key_prefix gets its trailing slash
    arr = make_array(
        object_audit, monkeypatch, FakeS3(object_audit), log_key_prefix="custom/prefix"
    )
    assert arr.log_key_prefix == "custom/prefix/"


def test_s3_client_is_built_path_style_with_timeouts(object_audit, monkeypatch):
    built = []

    class Config:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(object_audit, "Config", Config)
    monkeypatch.setattr(
        object_audit,
        "boto3",
        SimpleNamespace(client=lambda *a, **kw: built.append((a, kw)) or len(built)),
    )
    monkeypatch.setenv("FB_T_S3_ENDPOINT", "https://192.0.2.10")
    monkeypatch.setenv("FB_T_S3_ACCESS_KEY", TAIL_KEY)
    monkeypatch.setenv("FB_T_S3_SECRET_KEY", SECRET)
    monkeypatch.setenv("FB_T_STATE_ACCESS_KEY", STATE_KEY)
    monkeypatch.setenv("FB_T_STATE_SECRET_KEY", "state-" + SECRET)
    arr = object_audit.Array(
        {
            "name": "fb-a",
            "s3_endpoint_env": "FB_T_S3_ENDPOINT",
            "s3_access_key_env": "FB_T_S3_ACCESS_KEY",
            "s3_secret_key_env": "FB_T_S3_SECRET_KEY",
            "state_bucket": "eda-state",
            "state_access_key_env": "FB_T_STATE_ACCESS_KEY",
            "state_secret_key_env": "FB_T_STATE_SECRET_KEY",
        },
        False,
        request_timeout=12,
    )
    arr.s3
    args, kw = built[0]
    assert args == ("s3",)
    assert kw["endpoint_url"] == "https://192.0.2.10"
    assert kw["aws_access_key_id"] == TAIL_KEY
    assert kw["aws_secret_access_key"] == SECRET
    assert kw["verify"] is False and kw["region_name"] == "us-east-1"
    assert kw["config"].kwargs == {
        "s3": {"addressing_style": "path"},
        "connect_timeout": object_audit.CONNECT_TIMEOUT,
        "read_timeout": 12.0,
        "retries": {"max_attempts": 2},
    }
    # reused until reset; the state client carries the state key
    arr.s3
    assert len(built) == 1
    arr.state_s3
    assert built[1][1]["aws_access_key_id"] == STATE_KEY
    assert built[1][1]["aws_secret_access_key"] == "state-" + SECRET
    arr.reset()
    arr.s3
    assert len(built) == 3
    # verify_ssl true and the default read timeout
    arr = object_audit.Array(
        {
            "name": "b",
            "s3_endpoint": "https://x",
            "s3_access_key": "k",
            "s3_secret_key": "s",
        },
        True,
    )
    arr.s3
    assert built[-1][1]["verify"] is True
    assert built[-1][1]["config"].kwargs["read_timeout"] == 30.0


def test_rest_client_is_built_like_purefb_events(object_audit, monkeypatch):
    object_audit.flashblade.Client.reset_mock()
    monkeypatch.setenv("FB_T_URL", "https://192.0.2.1/")
    monkeypatch.setenv("FB_T_TOKEN", "token-" + SECRET)
    arr = object_audit.Array(
        {
            "name": "fb-a",
            "s3_endpoint": "https://192.0.2.10",
            "s3_access_key": "k",
            "s3_secret_key": "s",
            "fb_url_env": "FB_T_URL",
            "api_token_env": "FB_T_TOKEN",
        },
        False,
        request_timeout=12,
    )
    assert arr.has_rest is True and arr.host == "192.0.2.1"
    arr.client
    kw = object_audit.flashblade.Client.call_args.kwargs
    assert kw["target"] == "192.0.2.1"
    assert kw["api_token"] == "token-" + SECRET and kw["verify_ssl"] is False
    assert kw["timeout"] == (object_audit.CONNECT_TIMEOUT, 12.0)
    assert kw["user_agent"] == object_audit.USER_AGENT
    built = object_audit.flashblade.Client.call_count
    arr.client
    assert object_audit.flashblade.Client.call_count == built
    arr.reset()
    arr.client
    assert object_audit.flashblade.Client.call_count == built + 1
    # without the SDK a token is refused with a clear message
    monkeypatch.setattr(object_audit, "flashblade", None)
    with pytest.raises(ValueError, match="py-pure-client") as exc:
        object_audit.Array(
            {
                "name": "fb-a",
                "s3_endpoint": "https://192.0.2.10",
                "s3_access_key": "k",
                "s3_secret_key": "s",
                "fb_url": "192.0.2.1",
                "api_token": "token-" + SECRET,
            },
            False,
        )
    assert SECRET not in str(exc.value)


# --- the arrays argument and main ------------------------------------------
class _NoQueue:
    async def put(self, event):
        raise AssertionError("nothing should be queued")


def test_arrays_argument_accepts_json_and_yaml_strings(object_audit):
    spec = [{"name": "a", "s3_endpoint": "https://x", "s3_access_key_env": "K"}]
    assert object_audit._decode_arrays(json.dumps(spec), "p") == spec
    text = "- name: a\n  s3_endpoint: https://x\n  s3_access_key_env: K\n"
    assert object_audit._decode_arrays(text, "p") == spec
    assert object_audit._decode_arrays(spec, "p") is spec
    assert object_audit._decode_arrays("  ", "p") is None
    assert object_audit._decode_arrays(None, "p") is None


def test_main_decodes_the_string_before_validating_and_never_logs_a_key(
    object_audit, caplog
):
    caplog.set_level(logging.DEBUG)
    arrays = json.dumps([{"name": "a", "s3_endpoint": "https://x"}])
    with pytest.raises(ValueError, match="s3_access_key_env"):
        asyncio.run(object_audit.main(_NoQueue(), {"arrays": arrays}))
    arrays = json.dumps(
        [
            {
                "name": "a",
                "s3_endpoint": "https://x",
                "s3_access_key": "k",
                "s3_secret_key": SECRET,
                "api_token": "token-" + SECRET,
            }
        ]
    )
    with pytest.raises(ValueError, match="fb_url and api_token") as exc:
        asyncio.run(object_audit.main(_NoQueue(), {"arrays": arrays}))
    assert SECRET not in str(exc.value) and SECRET not in caplog.text
    # a mapping keyed by array name decodes too
    arrays = json.dumps({"a": {"s3_endpoint": "https://x"}})
    with pytest.raises(ValueError, match="array a: s3_access_key_env"):
        asyncio.run(object_audit.main(_NoQueue(), {"arrays": arrays}))
    with pytest.raises(ValueError, match="at least one array"):
        asyncio.run(object_audit.main(_NoQueue(), {"arrays": []}))


@pytest.mark.parametrize(
    "text",
    ['[{"name": "a", "s3_secret_key": "s3cret"', "just words s3cret", "42"],
)
def test_main_rejects_a_malformed_arrays_string_without_echoing_it(
    object_audit, caplog, text
):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ValueError, match="JSON") as exc:
        asyncio.run(object_audit.main(_NoQueue(), {"arrays": text}))
    assert "arrays" in str(exc.value)
    assert "s3cret" not in str(exc.value) and "s3cret" not in caplog.text


# --- the first polls: floor, cursor, late authority, restart ---------------
def test_first_poll_records_the_newest_key_and_emits_only_health(
    object_audit, monkeypatch, tmp_path, clock
):
    s3 = sample_bucket(object_audit)
    arr = make_array(object_audit, monkeypatch, s3)
    state = {}
    out = object_audit.poll_object_audit(arr, state, cursor_args(tmp_path))
    assert kinds(out) == ["purefb_audit_health"]
    health = out[0]["purefb_audit_health"]
    assert set(health) == {"array", *HEALTH_FIELDS}
    assert health["array"] == "fb-a" and health["log_bucket"] == "eda-log"
    # the prefix was read off the bucket: the UUID prefix and its one sub-prefix
    assert health["log_key_prefix"] == PREFIX
    assert health["newest_log_object"] == log_key("200500", 198)
    assert health["newest_log_age_s"] == 30  # LastModified 20:10:00, now 20:10:30
    assert health["rotation_ms"] == ROTATION and health["stale"] is False
    assert health["objects_this_poll"] == 0 and health["records_this_poll"] == 0
    assert health["rest_available"] is False and health["cursor_persisted"] is True
    assert health["policies"] == [] and health["uncovered_buckets"] == []
    assert health["log_bucket_quota_known"] is False
    assert health["log_bucket_used_pct"] == 0.0 and health["healthy"] is True
    # the listing: Prefix plus StartAfter the floor key, two rotations back
    listings = [kw for n, kw in s3.calls if n == "list_objects_v2"]
    assert listings[0] == {"Bucket": "eda-log", "Delimiter": "/"}
    assert listings[1] == {
        "Bucket": "eda-log",
        "Prefix": f"{ARRAY_ID}/",
        "Delimiter": "/",
    }
    assert listings[2] == {
        "Bucket": "eda-log",
        "Prefix": PREFIX,
        "StartAfter": PREFIX + "2026/11/12/200000",
    }
    assert not any(n == "get_object" for n, _ in s3.calls)  # nothing replayed
    # the cursor file holds the newest key, both seen keys and the floor;
    # written through a temporary name that is gone
    cursor_dir = tmp_path / "cursors"
    assert sorted(p.name for p in cursor_dir.iterdir()) == ["fb-a.json"]
    cursor = json.loads((cursor_dir / "fb-a.json").read_text())
    assert cursor["last_key"] == log_key("200500", 198)
    assert cursor["last_key_time_ms"] == ms("2026-11-12T20:10:00Z")
    assert cursor["seen"] == [log_key("200500", 133), log_key("200500", 198)]
    assert cursor["floor"] == "2026/11/12/200000"
    assert cursor["time"] == NOW and cursor["array"] == "fb-a"


def test_second_poll_emits_new_records_then_the_rollup_then_health(
    object_audit, monkeypatch, tmp_path, clock
):
    s3 = sample_bucket(object_audit)
    arr = make_array(object_audit, monkeypatch, s3)
    state, args = {}, cursor_args(tmp_path)
    object_audit.poll_object_audit(arr, state, args)
    s3.add(
        log_key("200500", 205),
        ndjson(
            record(
                name="PutObject",
                key="landing/ds1/_SUCCESS",
                event_id=4,
                time="2026-11-12T20:08:50.953Z",
                version="AAAAAAAAAAOfDjBzWH7_ugCa",
            )
        ),
        "2026-11-12T20:10:02Z",
    )
    clock["ms"] += 60_000
    out = object_audit.poll_object_audit(arr, state, args)
    assert kinds(out) == ["purefb_s3_event", "purefb_s3_rollup", "purefb_audit_health"]
    ev = out[0]["purefb_s3_event"]
    assert set(ev) == {"array", *S3_EVENT_FIELDS}
    assert ev["array"] == "fb-a" and ev["bucket"] == "eda-data"
    assert ev["key"] == "landing/ds1/_SUCCESS" and ev["event_name"] == "PutObject"
    assert ev["event_time"] == "2026-11-12T20:08:50.953Z"
    assert ev["event_time_ms"] == ms("2026-11-12T20:08:50Z") + 953
    assert ev["event_id"] == 4 and ev["read_only"] is False
    assert ev["access_key_id"] == APP_KEY and ev["user"] == "eda-app"
    assert ev["account"] == "eda" and ev["source_ip"] == "10.21.22.12"
    assert ev["user_agent"].startswith("Boto3/")
    assert ev["error_code"] == "" and ev["error_message"] == "" and ev["ok"] is True
    assert ev["version_id"] == "AAAAAAAAAAOfDjBzWH7_ugCa"
    assert ev["log_object"] == log_key("200500", 205)
    assert ev["landing"] is True and ev["dataset"] == "ds1"
    assert ev["by_automation"] is False
    rollup = out[1]["purefb_s3_rollup"]
    assert set(rollup) == {"array", *ROLLUP_FIELDS}
    assert rollup["log_object"] == log_key("200500", 205)
    assert rollup["window"] == "2026-11-12T20:05:00Z"
    assert rollup["record_count"] == 1 and rollup["error_count"] == 0
    health = out[2]["purefb_audit_health"]
    assert health["objects_this_poll"] == 1 and health["records_this_poll"] == 1
    assert health["newest_log_object"] == log_key("200500", 205)
    assert health["newest_log_age_s"] == 88  # 20:11:30 - 20:10:02
    # only the new object was fetched
    fetched = [kw["Key"] for n, kw in s3.calls if n == "get_object"]
    assert fetched == [log_key("200500", 205)]
    # a repeat poll emits nothing but health
    assert kinds(object_audit.poll_object_audit(arr, state, args)) == [
        "purefb_audit_health"
    ]


def test_late_authority_with_a_lower_name_is_still_picked_up(
    object_audit, monkeypatch, tmp_path, clock
):
    """An authority that writes its object for a window already seen, with
    a name that sorts before the keys already read, is listed again from the
    floor and emitted once."""
    s3 = sample_bucket(object_audit)
    arr = make_array(object_audit, monkeypatch, s3)
    state, args = {}, cursor_args(tmp_path)
    object_audit.poll_object_audit(arr, state, args)
    clock["ms"] += 60_000
    object_audit.poll_object_audit(arr, state, args)
    late = s3.add(
        log_key("200500", 100),
        ndjson(record(name="HeadObject", event_id=9, time="2026-11-12T20:08:52.000Z")),
        "2026-11-12T20:12:10Z",
    )
    assert late < log_key("200500", 133)  # sorts before what was read
    clock["ms"] += 60_000
    out = object_audit.poll_object_audit(arr, state, args)
    assert kinds(out) == ["purefb_s3_event", "purefb_s3_rollup", "purefb_audit_health"]
    assert out[0]["purefb_s3_event"]["event_id"] == 9
    assert out[0]["purefb_s3_event"]["canary"] is True
    health = out[2]["purefb_audit_health"]
    # the newest key is still the lexically newest one
    assert health["newest_log_object"] == log_key("200500", 198)
    assert kinds(object_audit.poll_object_audit(arr, state, args)) == [
        "purefb_audit_health"
    ]


def test_restart_from_the_cursor_file_emits_nothing_twice(
    object_audit, monkeypatch, tmp_path, clock, caplog
):
    s3 = sample_bucket(object_audit)
    arr = make_array(object_audit, monkeypatch, s3)
    args = cursor_args(tmp_path)
    object_audit.poll_object_audit(arr, {}, args)
    s3.add(log_key("200500", 205), ndjson(record(event_id=4)), "2026-11-12T20:10:02Z")
    clock["ms"] += 60_000
    first_run = object_audit.poll_object_audit(arr, {}, args)
    assert kinds(first_run) == [
        "purefb_s3_event",
        "purefb_s3_rollup",
        "purefb_audit_health",
    ]
    # a new process: the state is empty, the cursor file is not
    caplog.set_level(logging.INFO)
    clock["ms"] += 60_000
    s3.calls.clear()
    out = object_audit.poll_object_audit(arr, {}, args)
    assert kinds(out) == ["purefb_audit_health"]
    health = out[0]["purefb_audit_health"]
    assert health["newest_log_object"] == log_key("200500", 205)
    assert health["newest_log_age_s"] == 148
    assert not any(n == "get_object" for n, _ in s3.calls)
    assert "cursor restored" in caplog.text
    # an unreadable file starts fresh rather than failing
    (tmp_path / "cursors" / "fb-a.json").write_text("{not json")
    caplog.clear()
    out = object_audit.poll_object_audit(arr, {}, args)
    assert kinds(out) == ["purefb_audit_health"]
    assert "cursor file unreadable" in caplog.text
    assert out[0]["purefb_audit_health"]["cursor_persisted"] is True
    assert json.loads((tmp_path / "cursors" / "fb-a.json").read_text())["last_key"]


def test_cursor_is_mirrored_to_the_state_bucket_and_restored_from_it(
    object_audit, monkeypatch, tmp_path, clock, caplog
):
    caplog.set_level(logging.DEBUG)
    s3 = sample_bucket(object_audit)
    state_s3 = FakeS3(object_audit)
    arr = make_array(object_audit, monkeypatch, s3, state_s3=state_s3)
    args = cursor_args(tmp_path)
    out = object_audit.poll_object_audit(arr, {}, args)
    assert out[0]["purefb_audit_health"]["cursor_persisted"] is True
    # the first start found no copy yet: a debug line, not a warning
    assert "cursor copy not read" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    (put,) = state_s3.puts
    assert put["Bucket"] == "eda-state"
    assert put["Key"] == "cursors/object_audit/fb-a.json"
    assert put["ContentType"] == "application/json"
    assert json.loads(put["Body"])["last_key"] == log_key("200500", 198)
    assert json.loads(put["Body"]) == json.loads(
        (tmp_path / "cursors" / "fb-a.json").read_text()
    )
    # the local file is lost (a new host): the copy in the bucket is read
    (tmp_path / "cursors" / "fb-a.json").unlink()
    clock["ms"] += 60_000
    out = object_audit.poll_object_audit(arr, {}, args)
    assert kinds(out) == ["purefb_audit_health"]
    assert out[0]["purefb_audit_health"]["newest_log_object"] == log_key("200500", 198)
    # the copy failing is a warning, the local file still counts
    caplog.clear()
    state_s3.fail_put = RuntimeError("state bucket down")
    out = object_audit.poll_object_audit(arr, {}, args)
    assert out[0]["purefb_audit_health"]["cursor_persisted"] is True
    assert "cursor copy not written" in caplog.text
    assert SECRET not in caplog.text and STATE_KEY not in caplog.text
    # neither landing: cursor_persisted false, the poll still succeeds
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the directory should be")
    caplog.clear()
    out = object_audit.poll_object_audit(arr, {}, {"cursor_dir": str(blocked)})
    assert out[-1]["purefb_audit_health"]["cursor_persisted"] is False
    assert "cursor file not written" in caplog.text
    state_s3.fail_put = None
    out = object_audit.poll_object_audit(arr, {}, {"cursor_dir": str(blocked)})
    assert out[-1]["purefb_audit_health"]["cursor_persisted"] is True


def test_replay_objects_replays_the_newest_objects_at_start_up(
    object_audit, monkeypatch, tmp_path, clock
):
    s3 = sample_bucket(object_audit)
    arr = make_array(object_audit, monkeypatch, s3)
    out = object_audit.poll_object_audit(
        arr, {}, cursor_args(tmp_path, replay_objects=1)
    )
    # the newest object only: its two records, oldest first, then its rollup
    assert kinds(out) == [
        "purefb_s3_event",
        "purefb_s3_event",
        "purefb_s3_rollup",
        "purefb_audit_health",
    ]
    assert [e["purefb_s3_event"]["event_id"] for e in out[:2]] == [2, 3]
    assert out[2]["purefb_s3_rollup"]["log_object"] == log_key("200500", 198)
    assert out[3]["purefb_audit_health"]["objects_this_poll"] == 1
    assert out[3]["purefb_audit_health"]["records_this_poll"] == 2
    # the listing had no floor: the whole prefix is read once
    listing = [kw for n, kw in s3.calls if n == "list_objects_v2" and "Prefix" in kw]
    assert "StartAfter" not in listing[-1]
    # more than there are: every object, oldest first
    out = object_audit.poll_object_audit(
        arr, {}, cursor_args(tmp_path / "again", replay_objects=5)
    )
    assert kinds(out) == [
        "purefb_s3_event",
        "purefb_s3_rollup",
        "purefb_s3_event",
        "purefb_s3_event",
        "purefb_s3_rollup",
        "purefb_audit_health",
    ]
    assert [r["log_object"] for r in only(out, "purefb_s3_rollup")] == [
        log_key("200500", 133),
        log_key("200500", 198),
    ]
    # a cursor file wins over replay: the next process replays nothing
    out = object_audit.poll_object_audit(
        arr, {}, cursor_args(tmp_path / "again", replay_objects=5)
    )
    assert kinds(out) == ["purefb_audit_health"]


def test_seen_keys_are_pruned_to_the_retained_windows(
    object_audit, monkeypatch, tmp_path, clock
):
    s3 = sample_bucket(object_audit)
    arr = make_array(object_audit, monkeypatch, s3)
    state, args = {}, cursor_args(tmp_path)
    object_audit.poll_object_audit(arr, state, args)
    st = state["object_audit"]["fb-a"]
    assert len(st["seen"]) == 2 and st["floor"] == "2026/11/12/200000"
    # twenty minutes on: the 20:05 window is behind the floor and forgotten,
    # the floor used for this listing was still the persisted older one
    clock["ms"] += 20 * 60_000
    s3.calls.clear()
    out = object_audit.poll_object_audit(arr, state, args)
    assert kinds(out) == ["purefb_audit_health"]
    listing = [kw for n, kw in s3.calls if "StartAfter" in kw]
    assert listing[0]["StartAfter"] == PREFIX + "2026/11/12/200000"
    assert st["seen"] == set() and st["floor"] == "2026/11/12/202000"
    cursor = json.loads((tmp_path / "cursors" / "fb-a.json").read_text())
    assert cursor["seen"] == [] and cursor["floor"] == "2026/11/12/202000"
    # the newest key is remembered beyond the retained windows
    assert out[0]["purefb_audit_health"]["newest_log_object"] == log_key("200500", 198)
    s3.calls.clear()
    object_audit.poll_object_audit(arr, state, args)
    listing = [kw for n, kw in s3.calls if "StartAfter" in kw]
    assert listing[0]["StartAfter"] == PREFIX + "2026/11/12/202000"


def test_listing_pages_through_continuation_tokens(
    object_audit, monkeypatch, tmp_path, clock
):
    s3 = FakeS3(object_audit, page_size=2)
    for authority in (101, 102, 103, 104, 105):
        s3.add(log_key("200500", authority), ndjson(record(event_id=authority)))
    arr = make_array(object_audit, monkeypatch, s3, log_key_prefix=PREFIX)
    out = object_audit.poll_object_audit(
        arr, {}, cursor_args(tmp_path, replay_objects=5)
    )
    assert [r["log_object"] for r in only(out, "purefb_s3_rollup")] == [
        log_key("200500", a) for a in (101, 102, 103, 104, 105)
    ]
    listings = [kw for n, kw in s3.calls if n == "list_objects_v2"]
    assert len(listings) == 3
    assert "ContinuationToken" not in listings[0]
    assert listings[1]["ContinuationToken"] == log_key("200500", 102)
    assert listings[2]["ContinuationToken"] == log_key("200500", 104)
    # the explicit prefix was used as given: no discovery listing
    assert all(kw.get("Prefix") == PREFIX for kw in listings)
    assert out[-1]["purefb_audit_health"]["log_key_prefix"] == PREFIX


def test_prefix_discovery_needs_exactly_one_uuid_prefix(
    object_audit, monkeypatch, tmp_path, clock
):
    """Two array ids in one bucket (or none): no prefix, and the window
    check alone decides what is new, so log objects under both are read."""
    other = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d/audit_/"
    s3 = FakeS3(object_audit)
    s3.add(log_key("200500", 133), ndjson(record(event_id=1)))
    s3.add(f"{other}2026/11/12/200500.A7.1.log", ndjson(record(event_id=2)))
    arr = make_array(object_audit, monkeypatch, s3)
    out = object_audit.poll_object_audit(
        arr, {}, cursor_args(tmp_path, replay_objects=5)
    )
    assert out[-1]["purefb_audit_health"]["log_key_prefix"] == ""
    assert sorted(e["event_id"] for e in only(out, "purefb_s3_event")) == [1, 2]
    # without a prefix the floor key is not used: it would sort among the ids
    s3.add(f"{other}2026/11/12/201000.A7.1.log", ndjson(record(event_id=3)))
    clock["ms"] += 60_000
    out = object_audit.poll_object_audit(arr, {}, cursor_args(tmp_path))
    assert [e["event_id"] for e in only(out, "purefb_s3_event")] == [3]
    assert all("StartAfter" not in kw for n, kw in s3.calls if n == "list_objects_v2")
    # an empty bucket: nothing to discover, nothing seen, not stale
    empty = make_array(object_audit, monkeypatch, FakeS3(object_audit))
    (health,) = only(
        object_audit.poll_object_audit(empty, {}, cursor_args(tmp_path / "e")),
        "purefb_audit_health",
    )
    assert health["log_key_prefix"] == "" and health["newest_log_object"] == ""
    assert health["newest_log_age_s"] == -1 and health["stale"] is False


# --- the derived booleans ----------------------------------------------------
def classify(object_audit, monkeypatch, tmp_path, *records, **args):
    """Emit one log object holding `records` and return its events by id."""
    s3 = FakeS3(object_audit)
    s3.add(log_key("200500", 1), ndjson(*records))
    arr = make_array(object_audit, monkeypatch, s3, log_key_prefix=PREFIX)
    opts = cursor_args(
        tmp_path, replay_objects=1, automation_keys=[TAIL_KEY, STATE_KEY]
    )
    opts.update(args)
    out = object_audit.poll_object_audit(arr, {}, opts)
    events = {e["event_id"]: e for e in only(out, "purefb_s3_event")}
    (rollup,) = only(out, "purefb_s3_rollup")
    return events, rollup


def test_canary_fields(object_audit, monkeypatch, tmp_path, clock):
    reader = "PSFBSAZRTESTKEY0009"
    events, rollup = classify(
        object_audit,
        monkeypatch,
        tmp_path,
        record(name="PutObject", event_id=1),
        record(name="GetObject", event_id=2),
        record(name="GetObject", event_id=3, access_key=reader, user="auditor"),
        record(name="GetObject", event_id=4, error="NoSuchKey"),
        record(name="HeadObject", event_id=5),
        record(name="GetObjectTagging", event_id=6),
        record(name="GetObject", event_id=7, key="canary2/x", access_key=reader),
        record(name="GetObject", event_id=8, key="other/do-not-read.txt"),
        record(name="GetObject", event_id=9, access_key=TAIL_KEY, user="eda-tail"),
        canary_readers=[reader],
        canary_prefixes=["canary/", "canary2/"],
    )
    assert events[1]["canary"] is False  # the write that created it
    assert events[2]["canary"] is True and events[2]["canary_allowed"] is False
    assert events[2]["by_automation"] is False
    assert events[3]["canary"] is True and events[3]["canary_allowed"] is True
    assert events[4]["canary"] is False and events[4]["ok"] is False
    assert events[4]["error_code"] == "NoSuchKey"
    assert events[4]["error_message"] == ERRORS["NoSuchKey"]
    assert events[4]["denied"] is False and events[4]["version_id"] == ""
    assert events[5]["canary"] is True and events[6]["canary"] is True
    assert events[7]["canary"] is True and events[7]["canary_allowed"] is True
    assert events[8]["canary"] is False
    # the plugin's own key reading the canary is the automation
    assert events[9]["canary"] is True and events[9]["by_automation"] is True
    assert rollup["canary_hits"] == 6
    assert rollup["by_access_key"] == {APP_KEY: 6, reader: 2, TAIL_KEY: 1}
    assert rollup["top_access_key"] == APP_KEY and rollup["top_access_key_count"] == 6


def test_landing_evidence_and_honey_fields(object_audit, monkeypatch, tmp_path, clock):
    events, rollup = classify(
        object_audit,
        monkeypatch,
        tmp_path,
        record(name="PutObject", key="landing/ds1/part-20261112.csv", event_id=1),
        record(name="PutObject", key="landing/ds1/_SUCCESS", event_id=2),
        record(
            name="CompleteMultipartUpload", key="landing/a/b/manifest.json", event_id=3
        ),
        record(name="PutObject", key="landing/_SUCCESS", event_id=4),
        record(
            name="PutObject",
            key="landing/ds2/_SUCCESS",
            event_id=5,
            error="AccessDenied",
        ),
        record(name="GetObject", key="landing/ds1/_SUCCESS", event_id=6),
        record(name="PutObject", key="evidence/case-42/img.bin", event_id=7),
        record(name="GetObject", key="evidence/case-42/img.bin", event_id=8),
        record(
            name="CompleteMultipartUpload", key="evidence/case-42/big.bin", event_id=9
        ),
        record(name="GetObject", key="secrets/id.pem", event_id=10, error="NoSuchKey"),
        record(name="PutObject", key="backup/db.key", event_id=11),
        record(name="HeadObject", key="notes/readme.secret", event_id=12),
        record(name="GetObject", key="notes/readme.txt", event_id=13),
    )
    assert events[1]["landing"] is False and events[1]["dataset"] == ""
    assert events[2]["landing"] is True and events[2]["dataset"] == "ds1"
    assert events[3]["landing"] is True and events[3]["dataset"] == "a/b"
    assert events[4]["landing"] is True and events[4]["dataset"] == ""
    assert events[5]["landing"] is False and events[5]["denied"] is True
    assert events[6]["landing"] is False
    assert events[7]["evidence"] is True and events[8]["evidence"] is False
    assert events[9]["evidence"] is True
    assert events[10]["honey"] is True and events[10]["ok"] is False
    assert events[11]["honey"] is True and events[12]["honey"] is True
    assert events[13]["honey"] is False
    assert rollup["honey_hits"] == 3 and rollup["canary_hits"] == 0
    assert rollup["denied_count"] == 1 and rollup["error_count"] == 2
    # the markers and prefixes are arguments
    events, _ = classify(
        object_audit,
        monkeypatch,
        tmp_path / "b",
        record(name="PutObject", key="inbox/ds3/DONE", event_id=1),
        record(name="PutObject", key="landing/ds1/_SUCCESS", event_id=2),
        landing_prefixes=["inbox/"],
        landing_markers="DONE",
    )
    assert events[1]["landing"] is True and events[1]["dataset"] == "ds3"
    assert events[2]["landing"] is False


def test_approval_request_denied_delete_and_bucket_operations(
    object_audit, monkeypatch, tmp_path, clock
):
    events, rollup = classify(
        object_audit,
        monkeypatch,
        tmp_path,
        record(
            name="PutObject",
            bucket="eda-log",
            key="approvals/probe-20261112T150849.json",
            access_key=STATE_KEY,
            user="eda-state",
            event_id=1,
        ),
        record(name="PutObject", bucket="eda-data", key="approvals/x.json", event_id=2),
        record(
            name="PutObject", bucket="eda-log", key="requests/req-7.json", event_id=3
        ),
        record(
            name="GetObject", bucket="eda-log", key="approvals/probe.json", event_id=4
        ),
        record(
            name="PutObject",
            bucket="eda-log",
            key="approvals/a.json",
            event_id=5,
            error="AccessDenied",
        ),
        record(name="PutObject", bucket="eda-log", key="receipts/req-7", event_id=6),
        record(
            name="ListObjectsV2", key=None, event_id=7, error="SignatureDoesNotMatch"
        ),
        record(name="ListObjectsV2", key=None, event_id=8),
        record(name="DeleteObject", key="landing/ds1/part.csv", event_id=9),
        record(name="DeleteMultipleObjects", key=None, event_id=10),
        record(name="DeleteBucket", key=None, event_id=11, error="AccessDenied"),
        record(name="GetObject", key="x", event_id=12, error="InvalidAccessKeyId"),
        record(name="GetObject", key="x", event_id=13, error="ExpiredToken"),
    )
    assert events[1]["approval"] is True and events[1]["request"] is False
    assert events[1]["request_id"] == "probe-20261112T150849"
    assert events[1]["by_automation"] is True and events[1]["user"] == "eda-state"
    assert events[2]["approval"] is False and events[2]["request_id"] == ""
    assert events[3]["request"] is True and events[3]["approval"] is False
    assert events[3]["request_id"] == "req-7"
    assert events[4]["approval"] is False
    assert events[5]["approval"] is False and events[5]["denied"] is True
    assert events[6]["approval"] is False and events[6]["request"] is False
    # bucket operations carry no key; the denied codes are the four listed
    assert events[7]["key"] == "" and events[7]["denied"] is True
    assert events[7]["ok"] is False and events[7]["read_only"] is True
    assert events[8]["key"] == "" and events[8]["denied"] is False and events[8]["ok"]
    assert events[9]["delete"] is True and events[10]["delete"] is True
    assert events[11]["delete"] is True and events[11]["denied"] is True
    assert events[12]["denied"] is True and events[13]["denied"] is True
    assert rollup["denied_count"] == 5 and rollup["delete_count"] == 3
    assert rollup["denied_storm"] is True and rollup["mass_delete"] is False
    assert rollup["by_error_code"] == {
        "AccessDenied": 2,
        "ExpiredToken": 1,
        "InvalidAccessKeyId": 1,
        "SignatureDoesNotMatch": 1,
    }
    assert rollup["buckets"] == ["eda-data", "eda-log"]
    # the prefixes follow the arguments, and the log bucket is the array's
    events, _ = classify(
        object_audit,
        monkeypatch,
        tmp_path / "b",
        record(name="PutObject", bucket="eda-log", key="ok/1.json", event_id=1),
        record(name="PutObject", bucket="eda-log", key="approvals/1.json", event_id=2),
        approval_prefix="ok/",
    )
    assert events[1]["approval"] is True and events[1]["request_id"] == "1"
    assert events[2]["approval"] is False


def test_version_id_comes_from_the_last_response_elements(
    object_audit, monkeypatch, tmp_path, clock
):
    """The lab writes responseElements twice on a GetObject; json.loads
    keeps the last, which carries the version id and the ETag."""
    line = (
        '{"eventId": 21, "eventTime": "2026-11-12T20:08:50.692Z", "eventName": '
        '"GetObject", "readOnly": true, "bucketName": "eda-data", '
        '"recipientAccountId": "eda", "sourceIPAddress": "10.21.22.12", '
        '"userIdentity": {"accessKeyId": "' + APP_KEY + '", "userName": "eda-app"}, '
        '"requestParameters": {"bucketName": "eda-data", "key": "canary/c.txt"}, '
        '"responseElements": {"x-amz-version-id": "FIRST"}, '
        '"responseElements": {"x-amz-version-id": "LAST", "ETag": "\\"34\\""}}'
    )
    events, _ = classify(object_audit, monkeypatch, tmp_path, line)
    assert events[21]["version_id"] == "LAST" and events[21]["canary"] is True
    assert events[21]["user_agent"] == "" and events[21]["account"] == "eda"


def test_automation_keys_default_to_the_tail_and_state_keys(
    object_audit, monkeypatch, tmp_path, clock
):
    s3 = FakeS3(object_audit)
    s3.add(
        log_key("200500", 1),
        ndjson(
            record(event_id=1, access_key=TAIL_KEY),
            record(event_id=2, access_key=STATE_KEY),
            record(event_id=3, access_key=APP_KEY),
        ),
    )
    arr = make_array(
        object_audit,
        monkeypatch,
        s3,
        state_s3=FakeS3(object_audit),
        log_key_prefix=PREFIX,
    )
    args = cursor_args(tmp_path, replay_objects=1)
    events = {
        e["event_id"]: e
        for e in only(object_audit.poll_object_audit(arr, {}, args), "purefb_s3_event")
    }
    assert [events[i]["by_automation"] for i in (1, 2, 3)] == [True, True, False]
    # an explicit list is added to the default, never replaces it (a fresh
    # start: the first poll left its cursor in the state bucket, which would
    # otherwise win)
    arr._state_s3.objects.clear()
    args = cursor_args(tmp_path / "b", replay_objects=1, automation_keys=APP_KEY)
    events = {
        e["event_id"]: e
        for e in only(object_audit.poll_object_audit(arr, {}, args), "purefb_s3_event")
    }
    assert [events[i]["by_automation"] for i in (1, 2, 3)] == [True, True, True]
    # the loop seeds every array's keys into the shared state
    state = {}
    other = make_array(object_audit, monkeypatch, FakeS3(object_audit), name="fb-b")
    other._access = "PSFBSAZRTESTKEY0004"
    assert object_audit._automation_keys(arr, state, {}) == {TAIL_KEY, STATE_KEY}
    state["automation_keys"] = {other.access_key_id}
    assert object_audit._automation_keys(arr, state, {}) == {
        TAIL_KEY,
        STATE_KEY,
        "PSFBSAZRTESTKEY0004",
    }
    # the argument joins the set; the plugin's own keys stay in it
    explicit = {"automation_keys": APP_KEY}
    assert object_audit._automation_keys(arr, state, explicit) == {
        TAIL_KEY,
        STATE_KEY,
        "PSFBSAZRTESTKEY0004",
        APP_KEY,
    }


# --- rollup counts and storms ----------------------------------------------
def test_rollup_counts_and_the_error_storm(object_audit, monkeypatch, tmp_path, clock):
    misses = [
        record(
            key=f"missing/{i}",
            error="NoSuchKey",
            event_id=i,
            time=f"2026-11-12T20:08:5{i}.000Z",
            ip="10.21.22.12" if i < 5 else "10.21.22.13",
        )
        for i in range(1, 6)
    ]
    events, rollup = classify(
        object_audit,
        monkeypatch,
        tmp_path,
        record(
            name="PutObject",
            key="landing/ds1/_SUCCESS",
            event_id=6,
            time="2026-11-12T20:08:50.100Z",
        ),
        *misses,
    )
    assert rollup["record_count"] == 6 and rollup["error_count"] == 5
    assert rollup["error_pct"] == 83.3
    assert rollup["first_time"] == "2026-11-12T20:08:50.100Z"
    assert rollup["last_time"] == "2026-11-12T20:08:55.000Z"
    assert rollup["by_event_name"] == {"GetObject": 5, "PutObject": 1}
    assert rollup["by_error_code"] == {"NoSuchKey": 5}
    assert rollup["by_source_ip"] == {"10.21.22.12": 5, "10.21.22.13": 1}
    assert rollup["top_source_ip"] == "10.21.22.12"
    assert rollup["error_storm"] is True and rollup["denied_storm"] is False
    assert rollup["mass_delete"] is False and rollup["bad_lines"] == 0
    assert rollup["canary_hits"] == 0 and rollup["honey_hits"] == 0
    # the events of one object are emitted oldest first
    assert list(events) == [6, 1, 2, 3, 4, 5]
    # thresholds are arguments: count and rate both have to be met
    _, rollup = classify(
        object_audit, monkeypatch, tmp_path / "b", *misses, error_min_count=6
    )
    assert rollup["error_storm"] is False and rollup["error_pct"] == 100.0
    _, rollup = classify(
        object_audit,
        monkeypatch,
        tmp_path / "c",
        *misses,
        *[record(name="PutObject", event_id=10 + i) for i in range(6)],
        error_rate_pct=50,
    )
    assert rollup["error_count"] == 5 and rollup["error_pct"] == 45.5
    assert rollup["error_storm"] is False


def test_rollup_denied_and_mass_delete_storms(
    object_audit, monkeypatch, tmp_path, clock
):
    denied = [
        record(
            name="ListObjectsV2", key=None, event_id=i, error="SignatureDoesNotMatch"
        )
        for i in range(1, 6)
    ]
    deletes = [
        record(name="DeleteObject", key=f"landing/ds1/p{i}.csv", event_id=10 + i)
        for i in range(3)
    ]
    _, rollup = classify(object_audit, monkeypatch, tmp_path, *denied, *deletes)
    assert rollup["denied_count"] == 5 and rollup["denied_storm"] is True
    assert rollup["error_storm"] is True  # 5 of 8 in error
    assert rollup["delete_count"] == 3 and rollup["mass_delete"] is False
    _, rollup = classify(
        object_audit,
        monkeypatch,
        tmp_path / "b",
        *denied,
        *deletes,
        denied_storm_count=6,
        mass_delete_count=3,
    )
    assert rollup["denied_storm"] is False and rollup["mass_delete"] is True
    # an empty object (every line bad) still yields a rollup
    _, rollup = classify(object_audit, monkeypatch, tmp_path / "c", "not json")
    assert rollup["record_count"] == 0 and rollup["error_pct"] == 0.0
    assert rollup["first_time"] == "" and rollup["top_access_key"] == ""
    assert rollup["bad_lines"] == 1 and rollup["error_storm"] is False


def test_bad_line_is_counted_and_skipped(
    object_audit, monkeypatch, tmp_path, clock, caplog
):
    caplog.set_level(logging.WARNING)
    s3 = FakeS3(object_audit)
    s3.add(
        log_key("200500", 1),
        ndjson(
            record(event_id=1), "this is not json", "", "[1, 2]", record(event_id=2)
        ),
    )
    arr = make_array(object_audit, monkeypatch, s3, log_key_prefix=PREFIX)
    out = object_audit.poll_object_audit(
        arr, {}, cursor_args(tmp_path, replay_objects=1)
    )
    assert kinds(out) == [
        "purefb_s3_event",
        "purefb_s3_event",
        "purefb_s3_rollup",
        "purefb_audit_health",
    ]
    assert [e["event_id"] for e in only(out, "purefb_s3_event")] == [1, 2]
    assert out[2]["purefb_s3_rollup"]["record_count"] == 2
    assert out[2]["purefb_s3_rollup"]["bad_lines"] == 2
    health = out[3]["purefb_audit_health"]
    assert health["records_this_poll"] == 2 and health["bad_lines_this_poll"] == 2
    assert caplog.text.count("skipped") == 2 and "line 2" in caplog.text


# --- health with and without REST -------------------------------------------
def test_health_with_rest_reads_the_audit_setup(
    object_audit, monkeypatch, tmp_path, clock
):
    client = FakeClient(
        targets=[
            {
                "name": "other-target",
                "bucket": {"name": "other-log"},
                "log_name_prefix": {"prefix": "x_"},
                "log_rotate": {"duration": 900_000},
            },
            {
                "name": "eda-audit",
                "bucket": {"name": "eda-log"},
                "log_name_prefix": {"prefix": "audit_"},
                "log_rotate": {"duration": 600_000},
            },
        ],
        buckets=[
            bucket_row("eda-data"),
            bucket_row("eda-log", virtual=900, quota=1000, count=12),
            bucket_row("eda-state"),
            bucket_row("other"),
            bucket_row("gone", destroyed=True),
        ],
    )
    s3 = sample_bucket(object_audit)
    arr = make_array(object_audit, monkeypatch, s3, client=client)
    out = object_audit.poll_object_audit(arr, {}, cursor_args(tmp_path))
    (health,) = only(out, "purefb_audit_health")
    assert health["rest_available"] is True
    # the prefix from the array id and the log target of the log bucket,
    # whose rotation is the default rotation
    assert health["log_key_prefix"] == PREFIX
    assert health["rotation_ms"] == 600_000
    assert health["log_bucket_used_bytes"] == 900
    assert health["log_bucket_quota_bytes"] == 1000
    assert health["log_bucket_used_pct"] == 90.0
    assert health["log_bucket_quota_known"] is True
    assert health["log_bucket_object_count"] == 12
    assert health["policies"] == [
        {
            "name": "eda-audit",
            "enabled": True,
            "log_targets": ["eda-audit"],
            "members": ["eda-data", "eda-log"],
        }
    ]
    assert health["policies_enabled"] == 1 and health["policies_disabled"] == 0
    # "other" has no policy; eda-state is excluded by default; "gone" is destroyed
    assert health["uncovered_buckets"] == ["other"] and health["uncovered_count"] == 1
    assert health["members_without_filters"] == []
    assert health["members_without_filters_count"] == 0
    assert health["healthy"] is False  # the quota warning and the uncovered bucket
    # the floor followed the longer rotation: two windows of ten minutes
    listing = [kw for n, kw in s3.calls if "StartAfter" in kw]
    assert listing[0]["StartAfter"] == PREFIX + "2026/11/12/195000"
    # the filter reads: one per live member
    filter_reads = [kw for n, kw in client.calls if n == "get_buckets_audit_filters"]
    assert filter_reads == [
        {"bucket_names": ["eda-data"]},
        {"bucket_names": ["eda-log"]},
    ]
    members = [
        kw for n, kw in client.calls if n == "get_audit_object_store_policies_members"
    ]
    assert members == [{"policy_names": ["eda-audit"]}]
    # the thresholds and the exclusions are arguments
    out = object_audit.poll_object_audit(
        arr,
        {},
        cursor_args(
            tmp_path / "b",
            log_bucket_quota_warn_pct=95,
            coverage_exclude="eda-state, other",
            rotation_ms=ROTATION,
        ),
    )
    (health,) = only(out, "purefb_audit_health")
    assert health["uncovered_buckets"] == [] and health["healthy"] is True
    assert health["rotation_ms"] == ROTATION


def test_coverage_exclude_empty_list_excludes_nothing(
    object_audit, monkeypatch, tmp_path, clock
):
    """None is the default (eda-state); an explicit [] is a choice, as it is
    for purefb_events, and the globs match case-sensitively."""
    client = FakeClient(buckets=[bucket_row("eda-data"), bucket_row("eda-state")])
    arr = make_array(
        object_audit, monkeypatch, sample_bucket(object_audit), client=client
    )
    (health,) = only(
        object_audit.poll_object_audit(arr, {}, cursor_args(tmp_path)),
        "purefb_audit_health",
    )
    assert health["uncovered_buckets"] == [] and health["healthy"] is True
    (health,) = only(
        object_audit.poll_object_audit(
            arr, {}, cursor_args(tmp_path / "b", coverage_exclude=[])
        ),
        "purefb_audit_health",
    )
    assert health["uncovered_buckets"] == ["eda-state"]
    assert health["uncovered_count"] == 1 and health["healthy"] is False
    (health,) = only(
        object_audit.poll_object_audit(
            arr, {}, cursor_args(tmp_path / "c", coverage_exclude=["EDA-*"])
        ),
        "purefb_audit_health",
    )
    assert health["uncovered_buckets"] == ["eda-state"]


def test_health_flags_a_disabled_policy_and_a_member_without_filters(
    object_audit, monkeypatch, tmp_path, clock
):
    client = FakeClient(filters={"eda-data": ["edaall"], "eda-log": []})
    arr = make_array(
        object_audit, monkeypatch, sample_bucket(object_audit), client=client
    )
    args = cursor_args(tmp_path, log_bucket_quota_warn_pct=95)
    (health,) = only(
        object_audit.poll_object_audit(arr, {}, args), "purefb_audit_health"
    )
    assert health["members_without_filters"] == ["eda-log"]
    assert health["members_without_filters_count"] == 1
    assert health["uncovered_buckets"] == [] and health["healthy"] is False
    client.filters = {"eda-data": ["edaall"], "eda-log": ["edaapprovals"]}
    (health,) = only(
        object_audit.poll_object_audit(arr, {}, args), "purefb_audit_health"
    )
    assert health["healthy"] is True
    # a disabled policy covers nothing
    client.policies = [policy_row(enabled=False)]
    (health,) = only(
        object_audit.poll_object_audit(arr, {}, args), "purefb_audit_health"
    )
    assert health["policies_disabled"] == 1 and health["policies_enabled"] == 0
    assert health["uncovered_buckets"] == ["eda-data", "eda-log"]
    assert health["healthy"] is False
    # no policy at all: no members read, every live bucket uncovered
    client.policies = []
    client.calls.clear()
    (health,) = only(
        object_audit.poll_object_audit(arr, {}, args), "purefb_audit_health"
    )
    assert health["policies"] == [] and health["uncovered_count"] == 2
    assert not any(
        n == "get_audit_object_store_policies_members" for n, _ in client.calls
    )


def test_health_without_rest_leaves_the_rest_fields_empty(
    object_audit, monkeypatch, tmp_path, clock
):
    arr = make_array(object_audit, monkeypatch, sample_bucket(object_audit))
    (health,) = only(
        object_audit.poll_object_audit(arr, {}, cursor_args(tmp_path)),
        "purefb_audit_health",
    )
    assert health["rest_available"] is False
    assert health["policies"] == [] and health["policies_enabled"] == 0
    assert health["policies_disabled"] == 0
    assert health["uncovered_buckets"] == [] and health["uncovered_count"] == 0
    assert health["members_without_filters"] == []
    assert health["members_without_filters_count"] == 0
    assert (
        health["log_bucket_used_bytes"] == 0 and health["log_bucket_quota_bytes"] == 0
    )
    assert health["log_bucket_used_pct"] == 0.0
    assert health["log_bucket_quota_known"] is False
    assert health["log_bucket_object_count"] == 0
    assert health["rotation_ms"] == ROTATION and health["healthy"] is True


def test_stale_only_after_a_log_object_was_seen(
    object_audit, monkeypatch, tmp_path, clock
):
    # an idle array: the only object is an hour old and behind the floor,
    # found once by the fallback listing so the age is known, not replayed
    s3 = FakeS3(object_audit)
    s3.add(log_key("190000", 133), ndjson(record(event_id=1)), "2026-11-12T19:05:01Z")
    arr = make_array(object_audit, monkeypatch, s3, log_key_prefix=PREFIX)
    state, args = {}, cursor_args(tmp_path)
    out = object_audit.poll_object_audit(arr, state, args)
    assert kinds(out) == ["purefb_audit_health"]
    health = out[0]["purefb_audit_health"]
    assert health["newest_log_object"] == log_key("190000", 133)
    assert health["newest_log_age_s"] == 3929 and health["stale"] is True
    assert health["healthy"] is False
    assert state["object_audit"]["fb-a"]["seen"] == set()  # behind the floor
    # the threshold is an argument
    (health,) = only(
        object_audit.poll_object_audit(
            arr, state, cursor_args(tmp_path, stale_rotations=100)
        ),
        "purefb_audit_health",
    )
    assert health["stale"] is False and health["healthy"] is True
    # a fresh object clears it; its own window is where the age comes from
    s3.add(log_key("200500", 133), ndjson(record(event_id=2)), "2026-11-12T20:10:03Z")
    clock["ms"] += 60_000
    out = object_audit.poll_object_audit(arr, state, args)
    assert kinds(out) == ["purefb_s3_event", "purefb_s3_rollup", "purefb_audit_health"]
    assert out[2]["purefb_audit_health"]["stale"] is False
    assert out[2]["purefb_audit_health"]["newest_log_age_s"] == 87
    # quiet again for three rotations: stale once more
    clock["ms"] += 15 * 60_000
    (health,) = only(
        object_audit.poll_object_audit(arr, state, args), "purefb_audit_health"
    )
    assert health["newest_log_age_s"] == 987 and health["stale"] is True


def test_a_listing_without_last_modified_falls_back_to_the_window_end(
    object_audit, monkeypatch, tmp_path, clock
):
    s3 = FakeS3(object_audit)
    s3.add(log_key("200500", 133), ndjson(record(event_id=1)))
    s3.objects[log_key("200500", 133)] = (s3.objects[log_key("200500", 133)][0], None)
    arr = make_array(object_audit, monkeypatch, s3, log_key_prefix=PREFIX)
    (health,) = only(
        object_audit.poll_object_audit(arr, {}, cursor_args(tmp_path)),
        "purefb_audit_health",
    )
    assert health["newest_log_age_s"] == 30  # the window ends at 20:10:00


# --- failures are poll errors -----------------------------------------------
def test_rest_failure_is_a_poll_error_not_a_crash(
    object_audit, monkeypatch, tmp_path, clock
):
    client = FakeClient()
    client.get_buckets = lambda **kw: FakeError("Service temporarily unavailable", 503)
    arr = make_array(
        object_audit, monkeypatch, sample_bucket(object_audit), client=client
    )
    with pytest.raises(RuntimeError, match="buckets: Service temporarily unavailable"):
        object_audit.poll_object_audit(arr, {}, cursor_args(tmp_path))
    # a failed fetch of one object fails the poll before the cursor moves
    arr = make_array(object_audit, monkeypatch, sample_bucket(object_audit))
    arr._s3.fail_get = RuntimeError("read timed out")
    state = {}
    with pytest.raises(RuntimeError, match="read timed out"):
        object_audit.poll_object_audit(
            arr, state, cursor_args(tmp_path, replay_objects=1)
        )
    assert not (tmp_path / "cursors" / "fb-a.json").exists()


def _run_loop(plugin, arrays, args, done_when, on_event=None, timeout=20):
    got = []

    async def go():
        done = asyncio.Event()

        class Q:
            async def put(self, event):
                # emitted after run() returned, never while an array lock is held
                assert all(not a.lock.locked() for a in arrays)
                got.append(event)
                if on_event:
                    on_event(event)
                if done_when(got):
                    done.set()

        with ThreadPoolExecutor(max_workers=len(arrays) + 1) as ex:
            task = asyncio.create_task(plugin._poll_loop(Q(), arrays, {}, args, ex))
            await asyncio.wait_for(done.wait(), timeout)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(go())
    return got


def test_listing_failure_yields_one_poll_error_and_the_next_poll_works(
    object_audit, monkeypatch, tmp_path, clock, caplog
):
    caplog.set_level(logging.DEBUG)
    s3 = sample_bucket(object_audit)
    s3.fail_list = RuntimeError("endpoint unreachable")
    arr = make_array(object_audit, monkeypatch, s3)
    resets = []
    arr.reset = lambda: resets.append(1)  # keep the fakes in place

    def recover(event):
        if "purefb_poll_error" in event:
            s3.fail_list = None

    got = _run_loop(
        object_audit,
        [arr],
        cursor_args(tmp_path, interval=1, replay_objects=1),
        lambda got: any("purefb_audit_health" in e for e in got),
        on_event=recover,
    )
    assert kinds(got) == [
        "purefb_poll_error",
        "purefb_s3_event",
        "purefb_s3_event",
        "purefb_s3_rollup",
        "purefb_audit_health",
    ]
    assert got[0]["purefb_poll_error"] == {
        "signal": "object_audit",
        "array": "fb-a",
        "error": "endpoint unreachable",
        "consecutive": 1,
        "escalate": False,
    }
    assert resets == [1]
    # the automation set was seeded from the array's own keys
    assert all(e["by_automation"] is False for e in only(got, "purefb_s3_event"))
    assert "poll failed (1 in a row)" in caplog.text
    assert SECRET not in caplog.text and TAIL_KEY not in caplog.text


def test_poll_loop_counts_a_streak_and_escalates_at_the_threshold(
    object_audit, monkeypatch, tmp_path, clock
):
    s3 = sample_bucket(object_audit)
    # an explicit prefix: one listing per poll, so the script below is
    # one outcome per poll
    arr = make_array(object_audit, monkeypatch, s3, log_key_prefix=PREFIX)
    arr.reset = lambda: None
    outcomes = iter([False, False, True, False])
    real = s3.list_objects_v2

    def flaky(**kw):
        if not next(outcomes, True):
            raise RuntimeError("nope")
        return real(**kw)

    s3.list_objects_v2 = flaky
    got = _run_loop(
        object_audit,
        [arr],
        cursor_args(tmp_path, interval=1, poll_error_escalate=2),
        lambda got: len(got) >= 4,  # error, error, health, error
    )
    errors = only(got, "purefb_poll_error")
    assert [(e["consecutive"], e["escalate"]) for e in errors] == [
        (1, False),
        (2, True),
        (1, False),
    ]
    assert kinds(got)[2] == "purefb_audit_health"


def test_poll_loop_polls_each_array_under_its_lock(
    object_audit, monkeypatch, tmp_path, clock
):
    good = make_array(object_audit, monkeypatch, sample_bucket(object_audit))
    bad_s3 = FakeS3(object_audit)
    bad_s3.fail_list = RuntimeError("array down")
    bad = make_array(object_audit, monkeypatch, bad_s3, name="fb-b")
    bad.reset = lambda: None
    got = _run_loop(
        object_audit,
        [bad, good],
        cursor_args(tmp_path, interval=1),
        lambda got: len(got) >= 2,
    )
    assert got[0]["purefb_poll_error"]["array"] == "fb-b"
    assert got[1]["purefb_audit_health"]["array"] == "fb-a"
    assert sorted(p.name for p in (tmp_path / "cursors").iterdir()) == ["fb-a.json"]
