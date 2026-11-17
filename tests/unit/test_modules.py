# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the modules, which need no array. Each module file is loaded
against stubs for the everpure.flashblade module_utils, for pypureclient and,
for the modules that reach the array over REST, for requests; then main()
runs through the real AnsibleModule with its arguments injected the way
ansible-core does it, and the printed JSON result is read back. A fake SDK
client plays the array's SDK side and a fake REST array its REST side."""

import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import types
from types import SimpleNamespace

import pytest
import yaml

# the whole file needs ansible-core; skip it, not fail, where it is absent
basic = pytest.importorskip("ansible.module_utils.basic")
from ansible.module_utils.common import warnings as module_warnings

ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULES = ROOT / "plugins" / "modules"
LOG_TARGET = "purefb_log_target"
POLICY = "purefb_audit_policy"
FILTER = "purefb_bucket_audit_filter"
TELEMETRY_TARGET = "purefb_telemetry_target"
TELEMETRY_POLICY = "purefb_telemetry_policy"
MAINTENANCE = "purefb_maintenance"
QOS = "purefb_qos"
SESSION = "purefb_fs_session"
LEGAL_HOLD = "purefb_legal_hold"
ALERT_FLAG = "purefb_alert_flag"
ERADICATION = "purefb_eradication"
# the top-level keys each module's RETURN documents
RETURN_KEYS = {
    LOG_TARGET: {"log_target"},
    POLICY: {"policy"},
    FILTER: {"filter"},
    TELEMETRY_TARGET: {"target", "test"},
    TELEMETRY_POLICY: {"policy"},
    MAINTENANCE: {"window"},
    QOS: {"policy"},
    SESSION: {"removed"},
    LEGAL_HOLD: {"hold"},
    ALERT_FLAG: {"alert"},
    ERADICATION: {"eradication_config"},
}
ACTIONS = ["s3:All", "s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
# the token run() hands every module and the session token the fake array
# answers a login with; neither may ever appear in a result
API_TOKEN = "not-a-real-token"
SESSION_TOKEN = "fake-session-token"
DAY_MS = 86400000
HOUR_MS = 3600000
STUBS = [
    "ansible_collections",
    "ansible_collections.everpure",
    "ansible_collections.everpure.flashblade",
    "ansible_collections.everpure.flashblade.plugins",
    "ansible_collections.everpure.flashblade.plugins.module_utils",
    "ansible_collections.everpure.flashblade.plugins.module_utils.purefb",
    "pypureclient",
    "pypureclient.flashblade",
]


def _ns(value):
    """Dicts to attribute objects, recursively, as the SDK items read."""
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_ns(v) for v in value]
    return value


class FakeResponse:
    def __init__(self, items=None, status=200, message=None):
        self.status_code = status
        if status == 200:
            self.items = _ns(items or [])
        else:
            self.errors = [SimpleNamespace(message=message)]


class FakeModels(types.ModuleType):
    """Stands in for pypureclient.flashblade: any model name yields a record
    class that keeps its keyword arguments, and every record built is listed
    in `built` as (model name, kwargs) so a test can see what was sent. Names
    in `missing` raise ImportError, as the SDK's lazy attributes do."""

    def __init__(self):
        super().__init__("pypureclient.flashblade")
        self.built = []
        self.missing = set()

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self.missing:
            raise ImportError(f"module pypureclient.flashblade has no attribute {name}")
        built = self.built

        class Record:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
                built.append((name, kwargs))

            def __repr__(self):
                return f"{name}({self.__dict__!r})"

        Record.__name__ = name
        return Record

    def sent(self, name):
        return [kwargs for model, kwargs in self.built if model == name]


class FakeClient:
    """The array's SDK side as the modules see it: log targets, audit
    policies with their members, buckets with their audit filters, the
    maintenance window, QoS policies, file systems and buckets with the
    policy they carry, sessions, locks, legal holds with their held entities
    and the array record. Every call is recorded in `calls`; a method named
    in `fail` answers with that array-style error."""

    def __init__(
        self,
        log_targets=(),
        policies=(),
        members=None,
        filters=None,
        windows=(),
        qos=(),
        file_systems=(),
        buckets=(),
        sessions=(),
        locks=(),
        holds=(),
        held=(),
        array=None,
    ):
        self.calls = []
        self.fail = {}
        self.log_targets = {t["name"]: t for t in log_targets}
        self.policies = {p["name"]: p for p in policies}
        self.members = {k: set(v) for k, v in (members or {}).items()}
        self.filters = {k: dict(v) for k, v in (filters or {}).items()}
        self.actions = list(ACTIONS)
        self.windows = [dict(w) for w in windows]
        self.qos = {q["name"]: dict(q) for q in qos}
        self.file_systems = {f["name"]: dict(f) for f in file_systems}
        self.buckets = {b["name"]: dict(b) for b in buckets}
        self.sessions = {s["name"]: dict(s) for s in sessions}
        self.locks = {k["name"]: dict(k) for k in locks}
        self.holds = {h["name"]: dict(h) for h in holds}
        self.held = [dict(e) for e in held]
        self.array = dict(
            array
            or {
                "name": "fb-a",
                "eradication_config": {
                    "eradication_delay": DAY_MS,
                    "manual_eradication": "all-enabled",
                },
            }
        )

    def _record(self, name, kwargs):
        self.calls.append((name, kwargs))
        if name in self.fail:
            return FakeResponse(status=400, message=self.fail[name])
        return None

    def called(self, prefix=("post_", "patch_", "delete_")):
        return [name for name, _ in self.calls if name.startswith(prefix)]

    # log targets
    def get_log_targets_object_store(self, **kw):
        err = self._record("get_log_targets_object_store", kw)
        if err:
            return err
        missing = [n for n in kw["names"] if n not in self.log_targets]
        if missing:
            return FakeResponse(status=400, message="Not found")
        return FakeResponse([self.log_targets[n] for n in kw["names"]])

    def post_log_targets_object_store(self, **kw):
        err = self._record("post_log_targets_object_store", kw)
        if err:
            return err
        rec = kw["log_target_object_store"]
        for name in kw["names"]:
            if name in self.log_targets:
                return FakeResponse(status=400, message="Already exists")
            self.log_targets[name] = {
                "name": name,
                "bucket": {"name": rec.bucket.name},
                "log_name_prefix": {"prefix": rec.log_name_prefix.prefix},
                "log_rotate": {"duration": rec.log_rotate.duration},
            }
        return FakeResponse([self.log_targets[n] for n in kw["names"]])

    def patch_log_targets_object_store(self, **kw):
        err = self._record("patch_log_targets_object_store", kw)
        if err:
            return err
        rec = kw["log_target_object_store"]
        for name in kw["names"]:
            target = self.log_targets[name]
            if getattr(rec, "bucket", None) is not None:
                target["bucket"] = {"name": rec.bucket.name}
            if getattr(rec, "log_name_prefix", None) is not None:
                target["log_name_prefix"] = {"prefix": rec.log_name_prefix.prefix}
            if getattr(rec, "log_rotate", None) is not None:
                target["log_rotate"] = {"duration": rec.log_rotate.duration}
        return FakeResponse([self.log_targets[n] for n in kw["names"]])

    def delete_log_targets_object_store(self, **kw):
        err = self._record("delete_log_targets_object_store", kw)
        if err:
            return err
        for name in kw["names"]:
            del self.log_targets[name]
        return FakeResponse([])

    # audit policies
    def get_audit_object_store_policies(self, **kw):
        err = self._record("get_audit_object_store_policies", kw)
        if err:
            return err
        missing = [n for n in kw["names"] if n not in self.policies]
        if missing:
            return FakeResponse(status=400, message="Not found")
        return FakeResponse([self.policies[n] for n in kw["names"]])

    def post_audit_object_store_policies(self, **kw):
        err = self._record("post_audit_object_store_policies", kw)
        if err:
            return err
        rec = kw["policy"]
        for name in kw["names"]:
            self.policies[name] = {
                "name": name,
                "enabled": rec.enabled,
                "log_targets": [
                    {"name": t.name, "resource_type": t.resource_type}
                    for t in getattr(rec, "log_targets", None) or []
                ],
            }
            self.members.setdefault(name, set())
        return FakeResponse([self.policies[n] for n in kw["names"]])

    def patch_audit_object_store_policies(self, **kw):
        err = self._record("patch_audit_object_store_policies", kw)
        if err:
            return err
        rec = kw["policy"]
        for name in kw["names"]:
            policy = self.policies[name]
            if getattr(rec, "enabled", None) is not None:
                policy["enabled"] = rec.enabled
            if getattr(rec, "log_targets", None) is not None:
                policy["log_targets"] = [
                    {"name": t.name, "resource_type": t.resource_type}
                    for t in rec.log_targets
                ]
        return FakeResponse([self.policies[n] for n in kw["names"]])

    def delete_audit_object_store_policies(self, **kw):
        err = self._record("delete_audit_object_store_policies", kw)
        if err:
            return err
        for name in kw["names"]:
            if self.members.get(name):
                return FakeResponse(status=400, message="Policy has members")
            del self.policies[name]
            self.members.pop(name, None)
        return FakeResponse([])

    def get_audit_object_store_policies_members(self, **kw):
        err = self._record("get_audit_object_store_policies_members", kw)
        if err:
            return err
        rows = [
            {
                "policy": {"name": policy, "resource_type": "audit-policies"},
                "member": {"name": bucket, "resource_type": "buckets"},
            }
            for policy in kw["policy_names"]
            for bucket in sorted(self.members.get(policy, ()))
        ]
        return FakeResponse(rows)

    def post_audit_object_store_policies_members(self, **kw):
        err = self._record("post_audit_object_store_policies_members", kw)
        if err:
            return err
        for policy in kw["policy_names"]:
            self.members.setdefault(policy, set()).update(kw["member_names"])
        return FakeResponse([])

    def delete_audit_object_store_policies_members(self, **kw):
        err = self._record("delete_audit_object_store_policies_members", kw)
        if err:
            return err
        for policy in kw["policy_names"]:
            self.members[policy].difference_update(kw["member_names"])
        return FakeResponse([])

    # bucket audit filters
    def get_bucket_audit_filter_actions(self, **kw):
        err = self._record("get_bucket_audit_filter_actions", kw)
        if err:
            return err
        return FakeResponse([{"name": a} for a in self.actions])

    def get_buckets_audit_filters(self, **kw):
        err = self._record("get_buckets_audit_filters", kw)
        if err:
            return err
        rows = []
        for bucket in kw["bucket_names"]:
            if bucket not in self.filters:
                return FakeResponse(status=400, message="Bucket does not exist")
            for name, spec in sorted(self.filters[bucket].items()):
                rows.append(dict(spec, name=name, bucket={"name": bucket}))
        return FakeResponse(rows)

    def post_buckets_audit_filters(self, **kw):
        err = self._record("post_buckets_audit_filters", kw)
        if err:
            return err
        rec = kw["audit_filter"]
        for bucket in kw["bucket_names"]:
            for name in kw["names"]:
                if name in self.filters.setdefault(bucket, {}):
                    return FakeResponse(status=400, message="Already exists")
                self.filters[bucket][name] = {
                    "actions": list(rec.actions),
                    "s3_prefixes": getattr(rec, "s3_prefixes", None),
                }
        return FakeResponse([])

    def delete_buckets_audit_filters(self, **kw):
        err = self._record("delete_buckets_audit_filters", kw)
        if err:
            return err
        for bucket in kw["bucket_names"]:
            for name in kw["names"]:
                del self.filters[bucket][name]
        return FakeResponse([])

    # the maintenance window: one, named array
    def get_maintenance_windows(self, **kw):
        err = self._record("get_maintenance_windows", kw)
        if err:
            return err
        return FakeResponse(list(self.windows))

    def post_maintenance_windows(self, **kw):
        err = self._record("post_maintenance_windows", kw)
        if err:
            return err
        window = {
            "name": "array",
            "created": 1000,
            "expires": 1000 + kw["maintenance_window"].timeout,
        }
        self.windows = [window]
        return FakeResponse([window])

    def delete_maintenance_windows(self, **kw):
        err = self._record("delete_maintenance_windows", kw)
        if err:
            return err
        self.windows = []
        return FakeResponse([])

    # QoS policies
    def get_qos_policies(self, **kw):
        err = self._record("get_qos_policies", kw)
        if err:
            return err
        missing = [n for n in kw["names"] if n not in self.qos]
        if missing:
            return FakeResponse(status=400, message="Not found")
        return FakeResponse([self.qos[n] for n in kw["names"]])

    def post_qos_policies(self, **kw):
        err = self._record("post_qos_policies", kw)
        if err:
            return err
        rec = kw["policy"]
        for name in kw["names"]:
            self.qos[name] = {
                "name": name,
                "enabled": getattr(rec, "enabled", True),
                "max_total_ops_per_sec": getattr(rec, "max_total_ops_per_sec", None),
                "max_total_bytes_per_sec": getattr(
                    rec, "max_total_bytes_per_sec", None
                ),
            }
        return FakeResponse([self.qos[n] for n in kw["names"]])

    def patch_qos_policies(self, **kw):
        err = self._record("patch_qos_policies", kw)
        if err:
            return err
        rec = kw["policy"]
        for name in kw["names"]:
            for field in (
                "enabled",
                "max_total_ops_per_sec",
                "max_total_bytes_per_sec",
            ):
                if hasattr(rec, field):
                    self.qos[name][field] = getattr(rec, field)
        return FakeResponse([self.qos[n] for n in kw["names"]])

    def delete_qos_policies(self, **kw):
        err = self._record("delete_qos_policies", kw)
        if err:
            return err
        for name in kw["names"]:
            del self.qos[name]
        return FakeResponse([])

    # file systems and buckets, as far as the QoS module reads them
    def _objects(self, call, table, kw):
        err = self._record(call, kw)
        if err:
            return err
        names = kw.get("names")
        if names:
            if any(n not in table for n in names):
                return FakeResponse(status=400, message="Not found")
            return FakeResponse([table[n] for n in names])
        return FakeResponse([table[n] for n in sorted(table)])

    def get_file_systems(self, **kw):
        return self._objects("get_file_systems", self.file_systems, kw)

    def get_buckets(self, **kw):
        return self._objects("get_buckets", self.buckets, kw)

    # sessions and locks
    def get_file_systems_sessions(self, **kw):
        err = self._record("get_file_systems_sessions", kw)
        if err:
            return err
        rows = [self.sessions[n] for n in sorted(self.sessions)]
        if kw.get("protocols"):
            rows = [s for s in rows if s.get("protocol") in kw["protocols"]]
        return FakeResponse(rows)

    def delete_file_systems_sessions(self, **kw):
        err = self._record("delete_file_systems_sessions", kw)
        if err:
            return err
        for name in kw["names"]:
            del self.sessions[name]
        return FakeResponse([])

    def get_file_systems_locks(self, **kw):
        err = self._record("get_file_systems_locks", kw)
        if err:
            return err
        return FakeResponse([self.locks[n] for n in sorted(self.locks)])

    def delete_file_systems_locks(self, **kw):
        err = self._record("delete_file_systems_locks", kw)
        if err:
            return err
        for name in kw["names"]:
            del self.locks[name]
        return FakeResponse([])

    # legal holds and what they hold
    def get_legal_holds(self, **kw):
        err = self._record("get_legal_holds", kw)
        if err:
            return err
        missing = [n for n in kw["names"] if n not in self.holds]
        if missing:
            return FakeResponse(status=400, message="Not found")
        return FakeResponse([self.holds[n] for n in kw["names"]])

    def post_legal_holds(self, **kw):
        err = self._record("post_legal_holds", kw)
        if err:
            return err
        rec = kw["hold"]
        for name in kw["names"]:
            self.holds[name] = {
                "name": name,
                "description": getattr(rec, "description", None),
                "realms": [],
            }
        return FakeResponse([self.holds[n] for n in kw["names"]])

    def delete_legal_holds(self, **kw):
        err = self._record("delete_legal_holds", kw)
        if err:
            return err
        for name in kw["names"]:
            if any(e["legal_hold"]["name"] == name for e in self.held):
                return FakeResponse(status=400, message="Legal hold has held entities")
            del self.holds[name]
        return FakeResponse([])

    def get_legal_holds_held_entities(self, **kw):
        err = self._record("get_legal_holds_held_entities", kw)
        if err:
            return err
        return FakeResponse(
            [e for e in self.held if e["legal_hold"]["name"] in kw["names"]]
        )

    def patch_legal_holds_held_entities(self, **kw):
        # the array releases a held entity with released=true, nothing else
        err = self._record("patch_legal_holds_held_entities", kw)
        if err:
            return err
        if kw.get("released") is not True:
            return FakeResponse(
                status=400, message='Patch on held entities requires "released"=true'
            )
        keep = [
            e
            for e in self.held
            if not (
                e["legal_hold"]["name"] in kw["names"]
                and e["file_system"]["name"] in kw["file_system_names"]
                and e["path"] in kw["paths"]
            )
        ]
        if len(keep) == len(self.held):
            return FakeResponse(status=400, message="Held entity does not exist")
        self.held = keep
        return FakeResponse([])

    def post_legal_holds_held_entities(self, **kw):
        err = self._record("post_legal_holds_held_entities", kw)
        if err:
            return err
        for hold in kw["names"]:
            for fs in kw["file_system_names"]:
                for path in kw["paths"]:
                    self.held.append(
                        {
                            "legal_hold": {"name": hold},
                            "file_system": {"name": fs},
                            "path": path,
                            "status": "held",
                        }
                    )
        return FakeResponse([])

    # the array record
    def get_arrays(self, **kw):
        err = self._record("get_arrays", kw)
        if err:
            return err
        return FakeResponse([self.array])

    def patch_arrays(self, **kw):
        err = self._record("patch_arrays", kw)
        if err:
            return err
        config = kw["array"].eradication_config
        for field in ("eradication_delay", "manual_eradication"):
            if hasattr(config, field):
                self.array["eradication_config"][field] = getattr(config, field)
        return FakeResponse([self.array])


class FakeHTTPResponse:
    """What requests hands back: status, headers, and a body that json()
    parses or, when there is none, refuses as requests does."""

    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.text = "" if body is None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeSession:
    """requests.Session as the REST helper uses it: headers kept across
    requests, verify, request() and close()."""

    def __init__(self, rest):
        self.rest = rest
        self.headers = {}
        self.verify = True
        self.closed = False

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        sent = dict(self.headers)
        sent.update(headers or {})
        return self.rest.request(method, url, params or {}, json, sent)

    def close(self):
        self.closed = True


class FakeRest:
    """The array's REST side for what the SDK client cannot serve: telemetry
    targets, telemetry metrics policies with their rules, alerts, and the
    object PATCHes and held-entity DELETEs the SDK modules fall back to,
    which read and write the FakeClient they are given. A login needs the
    api-token header and hands out SESSION_TOKEN, which every later request
    must carry. Each request under /api/<version>/ is recorded in `calls` as
    (method, resource, params, body); a (method, resource) in `fail` answers
    with that array-style error."""

    def __init__(self, targets=(), policies=(), rules=(), alerts=(), client=None):
        self.calls = []
        self.fail = {}
        self.versions = set()
        self.api_versions = ["2.19", "2.20", "2.21"]  # what GET /api/api_version lists
        self.logins = 0
        self.logouts = 0
        self.sessions = []
        self.targets = {t["name"]: dict(t) for t in targets}
        self.policies = {p["name"]: dict(p) for p in policies}
        self.rules = [dict(r) for r in rules]
        self.alerts = {a["name"]: dict(a) for a in alerts}
        self.client = client
        # what GET telemetry-targets/test answers: (status, body)
        self.test_answer = (200, {"items": [{"name": "eda-prom"}]})
        # clear bodies (json text, sorted keys) the object PATCH refuses
        self.clear_refused = set()

    def open_session(self):
        session = FakeSession(self)
        self.sessions.append(session)
        return session

    def called(self, methods=("POST", "PATCH", "DELETE")):
        return [(m, path) for m, path, _, _ in self.calls if m in methods]

    @staticmethod
    def _error(message, status=400):
        return FakeHTTPResponse(
            status, {"errors": [{"message": message, "context": None}]}
        )

    def request(self, method, url, params, body, headers):
        path = url.split("/api/", 1)[1]
        if path == "api_version":
            return FakeHTTPResponse(200, {"versions": list(self.api_versions)})
        if path == "login":
            if headers.get("api-token") != API_TOKEN:
                return self._error("invalid credentials", 401)
            self.logins += 1
            return FakeHTTPResponse(200, headers={"x-auth-token": SESSION_TOKEN})
        if headers.get("x-auth-token") != SESSION_TOKEN:
            return self._error("no session", 401)
        if path == "logout":
            self.logouts += 1
            return FakeHTTPResponse(200)
        version, resource = path.split("/", 1)
        self.versions.add(version)
        self.calls.append((method, resource, dict(params), body))
        if (method, resource) in self.fail:
            return self._error(self.fail[(method, resource)])
        handler = getattr(
            self,
            "_{0}_{1}".format(
                method.lower(), resource.replace("-", "_").replace("/", "__")
            ),
            None,
        )
        if handler is None:
            return self._error("no such endpoint {0} {1}".format(method, resource), 404)
        return handler(params, body)

    # telemetry targets
    def _get_telemetry_targets(self, params, body):
        name = params.get("names")
        if name is None:
            return FakeHTTPResponse(200, {"items": list(self.targets.values())})
        if name not in self.targets:
            return self._error("Telemetry target {0} does not exist".format(name))
        return FakeHTTPResponse(200, {"items": [self.targets[name]]})

    def _post_telemetry_targets(self, params, body):
        name = params["names"]
        if name in self.targets:
            return self._error("Telemetry target already exists")
        if "vendor" in body:
            return self._error("Invalid body parameter: vendor")
        self.targets[name] = {
            "name": name,
            "endpoint": body["endpoint"],
            "transport": body["transport"],
            "source": dict(body["source"]),
            "enabled": True,
        }
        return FakeHTTPResponse(200, {"items": [self.targets[name]]})

    def _patch_telemetry_targets(self, params, body):
        self.targets[params["names"]].update(body)
        return FakeHTTPResponse(200, {"items": [self.targets[params["names"]]]})

    def _delete_telemetry_targets(self, params, body):
        name = params["names"]
        for policy in self.policies.values():
            if name in [t["name"] for t in policy.get("targets") or []]:
                return self._error("Telemetry target is in use by a policy")
        del self.targets[name]
        return FakeHTTPResponse(200, {"items": []})

    def _get_telemetry_targets__test(self, params, body):
        status, answer = self.test_answer
        return FakeHTTPResponse(status, answer)

    # telemetry metrics policies and their rules
    def _get_telemetry_metrics_policies(self, params, body):
        name = params.get("names")
        if name is None:
            rows = [self.policies[n] for n in sorted(self.policies)]
            return FakeHTTPResponse(200, {"items": rows})
        if name not in self.policies:
            return self._error("Policy {0} does not exist".format(name))
        return FakeHTTPResponse(200, {"items": [self.policies[name]]})

    def _patch_telemetry_metrics_policies(self, params, body):
        if "enabled" in body:
            return self._error("Invalid body parameter: enabled")
        unknown = [t["name"] for t in body["targets"] if t["name"] not in self.targets]
        if unknown:
            return self._error("Telemetry target {0} does not exist".format(unknown[0]))
        policy = self.policies[params["names"]]
        policy["targets"] = [
            {"name": t["name"], "resource_type": "telemetry-targets"}
            for t in body["targets"]
        ]
        return FakeHTTPResponse(200, {"items": [policy]})

    def _get_telemetry_metrics_policies__rules(self, params, body):
        return FakeHTTPResponse(200, {"items": list(self.rules)})

    # alerts
    def _get_alerts(self, params, body):
        name = params.get("names")
        if name not in self.alerts:
            return self._error("Alert {0} does not exist".format(name))
        return FakeHTTPResponse(200, {"items": [self.alerts[name]]})

    def _patch_alerts(self, params, body):
        self.alerts[params["names"]]["flagged"] = body["flagged"]
        return FakeHTTPResponse(200, {"items": [self.alerts[params["names"]]]})

    # file systems and buckets: the QoS assignment, on the SDK fake's tables
    def _assign(self, table, params, body):
        obj = table.get(params["names"])
        if obj is None:
            return self._error("Not found")
        if json.dumps(body, sort_keys=True) in self.clear_refused:
            return self._error("Invalid body parameter: qos_policy")
        ref = body.get("qos_policy")
        name = ref.get("name") if isinstance(ref, dict) else None
        obj["qos_policy"] = {"name": name} if name else None
        return FakeHTTPResponse(200, {"items": [obj]})

    def _patch_file_systems(self, params, body):
        return self._assign(self.client.file_systems, params, body)

    def _patch_buckets(self, params, body):
        return self._assign(self.client.buckets, params, body)

    # held entities: the release the SDK has no method for
    def _delete_legal_holds__held_entities(self, params, body):
        keep = [
            e
            for e in self.client.held
            if not (
                e["legal_hold"]["name"] == params["names"]
                and e["file_system"]["name"] == params["file_system_names"]
                and e["path"] == params["paths"]
            )
        ]
        if len(keep) == len(self.client.held):
            return self._error("Held entity does not exist")
        self.client.held = keep
        return FakeHTTPResponse(200, {"items": []})


def requests_stub(rest):
    """A requests module whose Session talks to the FakeRest; the modules use
    Session(), session.request(...) and exceptions.RequestException."""
    stub = types.ModuleType("requests")

    class RequestException(Exception):
        pass

    stub.exceptions = types.SimpleNamespace(RequestException=RequestException)
    stub.Session = rest.open_session
    return stub


def _connection_spec():
    return dict(
        fb_url=dict(type="str"),
        api_token=dict(type="str", no_log=True),
        disable_warnings=dict(type="bool", default=False),
    )


@contextlib.contextmanager
def _stubbed(sdk_client, models, rest=None):
    """sys.modules entries for the collection module_utils, the SDK and, when
    a FakeRest is given, requests; put back afterwards so that the other test
    files see what they expect."""
    names = STUBS + (["requests"] if rest is not None else [])
    saved = {name: sys.modules.get(name) for name in names}
    for name in STUBS[:5]:
        sys.modules[name] = types.ModuleType(name)
    purefb = types.ModuleType(STUBS[5])
    purefb.get_system = lambda module: sdk_client
    purefb.purefb_argument_spec = _connection_spec
    sys.modules[STUBS[5]] = purefb
    sdk = types.ModuleType("pypureclient")
    sdk.__version__ = "1.98.0"
    sdk.flashblade = models
    sys.modules["pypureclient"] = sdk
    sys.modules["pypureclient.flashblade"] = models
    if rest is not None:
        sys.modules["requests"] = requests_stub(rest)
    try:
        yield
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def _load(name):
    path = MODULES / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(module_name, sdk_client, models=None, check_mode=False, rest=None, **args):
    """Run a module's main() against the fakes and return its parsed result
    with the exit code under `rc`. `rest` is the FakeRest a module that
    reaches the array over REST talks to. The fake SDK client is not called
    `client` here because that is an option of the session module."""
    models = models if models is not None else FakeModels()
    args.setdefault("fb_url", "fb-a.example.net")
    args.setdefault("api_token", API_TOKEN)
    if check_mode:
        args["_ansible_check_mode"] = True
    basic._ANSIBLE_ARGS = json.dumps({"ANSIBLE_MODULE_ARGS": args}).encode()
    # ansible-core 2.19 and later also want the serialization profile
    if hasattr(basic, "_ANSIBLE_PROFILE"):
        basic._ANSIBLE_PROFILE = "legacy"
    # module.warn collects into process-wide state there too; start each
    # run clean so a warning never leaks from one test into the next
    if hasattr(module_warnings, "_global_warnings"):
        module_warnings._global_warnings.clear()
    out = io.StringIO()
    with _stubbed(sdk_client, models, rest):
        module = _load(module_name)
        with contextlib.redirect_stdout(out), pytest.raises(SystemExit) as exc:
            module.main()
    basic._ANSIBLE_ARGS = None
    result = json.loads(out.getvalue().strip().splitlines()[-1])
    result["rc"] = exc.value.code
    return result


def warnings_of(result):
    """The warning texts of a result. ansible-core 2.19 and later wrap each
    one in a summary record; earlier releases list the plain strings."""
    texts = []
    for item in result.get("warnings", []):
        if isinstance(item, dict):
            item = (item.get("event") or {}).get("msg") or json.dumps(item)
        texts.append(item)
    return texts


def target_row(name="eda-audit", bucket="eda-log", prefix="audit_", rotate=300000):
    return {
        "name": name,
        "bucket": {"name": bucket},
        "log_name_prefix": {"prefix": prefix},
        "log_rotate": {"duration": rotate},
    }


def policy_row(name="eda-audit", enabled=True, targets=("eda-audit",)):
    return {
        "name": name,
        "enabled": enabled,
        "log_targets": [
            {"name": t, "resource_type": "log-targets/object-store"} for t in targets
        ],
    }


# ---------------------------------------------------------------- documentation


@pytest.mark.parametrize(
    "name, options",
    [
        (LOG_TARGET, {"name", "bucket", "log_name_prefix", "log_rotate", "state"}),
        (POLICY, {"name", "enabled", "log_targets", "buckets", "state"}),
        (FILTER, {"bucket", "name", "actions", "s3_prefixes", "state"}),
        (
            TELEMETRY_TARGET,
            {
                "name",
                "endpoint",
                "transport",
                "source",
                "enabled",
                "policies",
                "test",
                "api_version",
                "state",
            },
        ),
        (TELEMETRY_POLICY, {"name", "targets", "api_version", "state"}),
        (MAINTENANCE, {"state", "timeout", "extend"}),
        (
            QOS,
            {
                "name",
                "max_total_ops_per_sec",
                "max_total_bytes_per_sec",
                "enabled",
                "file_systems",
                "buckets",
                "api_version",
                "state",
            },
        ),
        (
            SESSION,
            {
                "kind",
                "client",
                "user",
                "protocols",
                "disruptive",
                "file_system",
                "path",
                "recursive",
                "inode",
            },
        ),
        (
            LEGAL_HOLD,
            {
                "name",
                "description",
                "state",
                "file_system",
                "paths",
                "recursive",
                "entities_state",
            },
        ),
        (ALERT_FLAG, {"alert", "flagged", "api_version"}),
        (ERADICATION, {"eradication_delay_days", "manual_eradication", "force"}),
    ],
)
def test_documentation_names_every_option(name, options):
    with _stubbed(FakeClient(), FakeModels(), FakeRest()):
        module = _load(name)
    doc = yaml.safe_load(module.DOCUMENTATION)
    assert doc["module"] == name
    assert doc["version_added"] == "0.1.0"
    assert set(doc["options"]) == options
    assert doc["extends_documentation_fragment"] == ["everpure.flashblade.everpure.fb"]
    examples = yaml.safe_load(module.EXAMPLES)
    assert all(f"sdodsley.flashblade_eda.{name}" in task for task in examples)
    assert "api_token" not in module.EXAMPLES.replace(
        'api_token: "{{ api_token }}"', ""
    )
    returned = yaml.safe_load(module.RETURN)
    assert set(returned) == RETURN_KEYS[name]


def test_every_module_file_is_documented_and_tested():
    assert {p.stem for p in MODULES.glob("purefb_*.py")} == set(RETURN_KEYS)


def test_headers_and_vocabulary():
    for path in sorted(MODULES.glob("*.py")):
        lines = path.read_text().splitlines()
        start = 1 if lines[0].startswith("#!") else 0
        assert lines[start] == "# Copyright 2026 Everpure, Inc.", path.name
        assert lines[start + 1] == "# SPDX-License-Identifier: Apache-2.0", path.name


# ------------------------------------------------------------------ log target


def test_log_target_create_sends_the_three_sub_models():
    client, models = FakeClient(), FakeModels()
    result = run(
        LOG_TARGET,
        client,
        models,
        name="eda-audit",
        bucket="eda-log",
        log_name_prefix="audit_",
        log_rotate="5m",
    )
    assert result["rc"] == 0 and result["changed"] is True
    assert result["log_target"] == {
        "name": "eda-audit",
        "bucket": "eda-log",
        "log_name_prefix": "audit_",
        "log_rotate": 300000,
    }
    assert client.called() == ["post_log_targets_object_store"]
    post = client.calls[-1][1]
    assert post["names"] == ["eda-audit"]
    body = post["log_target_object_store"]
    assert type(body).__name__ == "LogTargetsObjectStorePost"
    assert models.sent("Reference") == [{"name": "eda-log"}]
    assert models.sent("AuditLogNamePrefix") == [{"prefix": "audit_"}]
    assert models.sent("AuditLogRotate") == [{"duration": 300000}]
    assert client.log_targets["eda-audit"] == target_row()


def test_log_target_defaults_are_the_arrays_own():
    client = FakeClient()
    result = run(LOG_TARGET, client, name="eda-audit", bucket="eda-log")
    assert result["log_target"]["log_name_prefix"] == "audit-logs"
    assert result["log_target"]["log_rotate"] == 900000


def test_log_target_no_change_makes_no_write():
    client = FakeClient(log_targets=[target_row()])
    result = run(
        LOG_TARGET,
        client,
        name="eda-audit",
        bucket="eda-log",
        log_name_prefix="audit_",
        log_rotate="300000",
    )
    assert result["changed"] is False
    assert result["log_target"]["log_rotate"] == 300000
    assert client.called() == []


def test_log_target_update_patches_only_what_differs():
    client, models = FakeClient(log_targets=[target_row()]), FakeModels()
    result = run(
        LOG_TARGET,
        client,
        models,
        name="eda-audit",
        bucket="eda-log",
        log_name_prefix="audit_",
        log_rotate="15m",
    )
    assert result["changed"] is True
    assert client.called() == ["patch_log_targets_object_store"]
    body = client.calls[-1][1]["log_target_object_store"]
    assert type(body).__name__ == "LogTargetsObjectStore"
    assert not hasattr(body, "log_name_prefix") and not hasattr(body, "bucket")
    assert body.log_rotate.duration == 900000
    assert models.sent("AuditLogNamePrefix") == []
    assert client.log_targets["eda-audit"]["log_rotate"] == {"duration": 900000}
    assert result["log_target"]["log_rotate"] == 900000


def test_log_target_delete_and_absent_when_missing():
    client = FakeClient(log_targets=[target_row()])
    result = run(LOG_TARGET, client, name="eda-audit", state="absent")
    assert result["changed"] is True and result["log_target"] == {}
    assert client.called() == ["delete_log_targets_object_store"]
    assert client.log_targets == {}
    again = run(LOG_TARGET, client, name="eda-audit", state="absent")
    assert again["changed"] is False and again["log_target"] == {}
    assert client.called() == ["delete_log_targets_object_store"]


def test_log_target_check_mode_reports_without_writing():
    client = FakeClient()
    result = run(
        LOG_TARGET, client, check_mode=True, name="eda-audit", bucket="eda-log"
    )
    assert result["changed"] is True and client.log_targets == {}
    assert client.called() == []
    client = FakeClient(log_targets=[target_row()])
    result = run(LOG_TARGET, client, check_mode=True, name="eda-audit", state="absent")
    assert result["changed"] is True and "eda-audit" in client.log_targets
    assert client.called() == []


@pytest.mark.parametrize("rotate", ["2m", "299999", "25h", "86400001", "soon", "m"])
def test_log_target_rejects_a_bad_rotation_before_any_call(rotate):
    client = FakeClient()
    result = run(
        LOG_TARGET, client, name="eda-audit", bucket="eda-log", log_rotate=rotate
    )
    assert result["rc"] == 1 and result["failed"] is True
    assert "log_rotate" in result["msg"]
    assert client.calls == []


def test_log_target_accepts_the_range_ends_and_bare_milliseconds():
    for rotate, ms in [("5m", 300000), ("24h", 86400000), ("86400000", 86400000)]:
        client = FakeClient()
        result = run(
            LOG_TARGET, client, name="eda-audit", bucket="eda-log", log_rotate=rotate
        )
        assert result["changed"] is True
        assert result["log_target"]["log_rotate"] == ms


def test_log_target_needs_a_bucket_to_create():
    result = run(LOG_TARGET, FakeClient(), name="eda-audit")
    assert result["failed"] is True and "bucket" in result["msg"]


def test_log_target_delete_refusal_carries_the_arrays_message():
    client = FakeClient(log_targets=[target_row()])
    client.fail["delete_log_targets_object_store"] = (
        "Log target eda-audit is in use by policy eda-audit"
    )
    result = run(LOG_TARGET, client, name="eda-audit", state="absent")
    assert result["failed"] is True
    assert "in use by policy eda-audit" in result["msg"]
    assert "eda-audit" in client.log_targets


def test_log_target_missing_model_names_the_sdk_floor():
    models = FakeModels()
    models.missing.add("LogTargetsObjectStorePost")
    result = run(LOG_TARGET, FakeClient(), models, name="eda-audit", bucket="eda-log")
    assert result["failed"] is True
    assert "LogTargetsObjectStorePost" in result["msg"] and "1.98.0" in result["msg"]


# ---------------------------------------------------------------- audit policy


def test_policy_create_posts_the_policy_then_the_members():
    client, models = FakeClient(), FakeModels()
    result = run(
        POLICY,
        client,
        models,
        name="eda-audit",
        log_targets=["eda-audit"],
        buckets=["eda-log", "eda-data"],
    )
    assert result["rc"] == 0 and result["changed"] is True
    assert result["policy"] == {
        "name": "eda-audit",
        "enabled": True,
        "log_targets": ["eda-audit"],
        "buckets": ["eda-data", "eda-log"],
    }
    assert client.called() == [
        "post_audit_object_store_policies",
        "post_audit_object_store_policies_members",
    ]
    post = client.calls[-2][1]
    assert post["names"] == ["eda-audit"]
    assert type(post["policy"]).__name__ == "AuditObjectStorePoliciesPost"
    assert post["policy"].enabled is True
    assert models.sent("ReferenceWritable") == [
        {"name": "eda-audit", "resource_type": "log-targets/object-store"}
    ]
    members = client.calls[-1][1]
    assert members == {
        "policy_names": ["eda-audit"],
        "member_names": ["eda-data", "eda-log"],
    }
    assert client.members["eda-audit"] == {"eda-data", "eda-log"}


def test_policy_create_without_targets_leaves_the_field_to_the_array():
    client = FakeClient()
    result = run(POLICY, client, name="eda-audit")
    assert result["changed"] is True
    post = client.calls[-1][1]["policy"]
    assert not hasattr(post, "log_targets")
    assert result["policy"]["log_targets"] == [] and result["policy"]["buckets"] == []


def test_policy_no_change_makes_no_write():
    client = FakeClient(
        policies=[policy_row()], members={"eda-audit": {"eda-data", "eda-log"}}
    )
    result = run(
        POLICY,
        client,
        name="eda-audit",
        log_targets=["eda-audit"],
        buckets=["eda-data", "eda-log"],
    )
    assert result["changed"] is False
    assert result["policy"]["buckets"] == ["eda-data", "eda-log"]
    assert client.called() == []


def test_policy_update_patches_enabled_alone():
    client = FakeClient(policies=[policy_row()], members={"eda-audit": {"eda-data"}})
    result = run(POLICY, client, name="eda-audit", enabled=False)
    assert result["changed"] is True
    assert client.called() == ["patch_audit_object_store_policies"]
    body = client.calls[-1][1]["policy"]
    assert type(body).__name__ == "AuditObjectStorePoliciesPatch"
    assert body.enabled is False and not hasattr(body, "log_targets")
    assert client.policies["eda-audit"]["enabled"] is False
    # buckets were not given: membership untouched, still reported
    assert result["policy"]["buckets"] == ["eda-data"]
    assert result["policy"]["enabled"] is False


def test_policy_update_replaces_the_target_list_and_reconciles_members():
    client, models = (
        FakeClient(policies=[policy_row()], members={"eda-audit": {"eda-data", "old"}}),
        FakeModels(),
    )
    result = run(
        POLICY,
        client,
        models,
        name="eda-audit",
        log_targets=["eda-audit", "second"],
        buckets=["eda-data", "eda-log"],
    )
    assert result["changed"] is True
    assert client.called() == [
        "patch_audit_object_store_policies",
        "post_audit_object_store_policies_members",
        "delete_audit_object_store_policies_members",
    ]
    patch = client.calls[-3][1]["policy"]
    assert [t.name for t in patch.log_targets] == ["eda-audit", "second"]
    assert not hasattr(patch, "enabled")
    assert client.calls[-2][1]["member_names"] == ["eda-log"]
    assert client.calls[-1][1]["member_names"] == ["old"]
    assert client.members["eda-audit"] == {"eda-data", "eda-log"}
    assert result["policy"]["log_targets"] == ["eda-audit", "second"]
    assert result["policy"]["buckets"] == ["eda-data", "eda-log"]


def test_policy_delete_removes_members_first_then_the_policy():
    client = FakeClient(
        policies=[policy_row()], members={"eda-audit": {"eda-data", "eda-log"}}
    )
    result = run(POLICY, client, name="eda-audit", state="absent")
    assert result["changed"] is True and result["policy"] == {}
    assert client.called() == [
        "delete_audit_object_store_policies_members",
        "delete_audit_object_store_policies",
    ]
    assert client.calls[-2][1]["member_names"] == ["eda-data", "eda-log"]
    assert client.policies == {}
    again = run(POLICY, client, name="eda-audit", state="absent")
    assert again["changed"] is False and again["policy"] == {}


def test_policy_check_mode_reports_without_writing():
    client = FakeClient()
    result = run(POLICY, client, check_mode=True, name="eda-audit", buckets=["b"])
    assert result["changed"] is True and client.policies == {}
    assert client.called() == []
    client = FakeClient(policies=[policy_row()], members={"eda-audit": {"eda-data"}})
    result = run(POLICY, client, check_mode=True, name="eda-audit", state="absent")
    assert result["changed"] is True and "eda-audit" in client.policies
    assert client.called() == []
    result = run(POLICY, client, check_mode=True, name="eda-audit", buckets=["x"])
    assert result["changed"] is True and client.members["eda-audit"] == {"eda-data"}
    assert result["policy"]["buckets"] == ["x"]


def test_policy_rejects_an_unknown_state():
    result = run(POLICY, FakeClient(), name="eda-audit", state="paused")
    assert result["failed"] is True and "state" in result["msg"]


def test_policy_array_errors_carry_the_message():
    client = FakeClient()
    client.fail["post_audit_object_store_policies"] = "Log target eda-audit not found"
    result = run(POLICY, client, name="eda-audit", log_targets=["eda-audit"])
    assert result["failed"] is True
    assert "Log target eda-audit not found" in result["msg"]
    client = FakeClient(policies=[policy_row()], members={"eda-audit": set()})
    client.fail["post_audit_object_store_policies_members"] = "Bucket nope not found"
    result = run(POLICY, client, name="eda-audit", buckets=["nope"])
    assert result["failed"] is True and "Bucket nope not found" in result["msg"]
    assert "nope" in result["msg"]


# --------------------------------------------------------- bucket audit filter


def test_filter_create_checks_the_actions_and_posts():
    client, models = FakeClient(filters={"eda-data": {}}), FakeModels()
    result = run(
        FILTER, client, models, bucket="eda-data", name="edaall", actions=["s3:All"]
    )
    assert result["rc"] == 0 and result["changed"] is True
    assert result["filter"] == {
        "bucket": "eda-data",
        "name": "edaall",
        "actions": ["s3:All"],
        "s3_prefixes": [],
    }
    assert client.called(("get_",)) == [
        "get_bucket_audit_filter_actions",
        "get_buckets_audit_filters",
    ]
    assert client.called() == ["post_buckets_audit_filters"]
    post = client.calls[-1][1]
    assert post["bucket_names"] == ["eda-data"] and post["names"] == ["edaall"]
    assert type(post["audit_filter"]).__name__ == "BucketAuditFilterPost"
    assert post["audit_filter"].actions == ["s3:All"]
    assert not hasattr(post["audit_filter"], "s3_prefixes")
    assert client.filters["eda-data"]["edaall"]["actions"] == ["s3:All"]


def test_filter_create_with_prefixes():
    client = FakeClient(filters={"eda-log": {}})
    result = run(
        FILTER,
        client,
        bucket="eda-log",
        name="edaapprovals",
        actions=["s3:PutObject"],
        s3_prefixes=["requests/", "approvals/"],
    )
    assert result["changed"] is True
    assert result["filter"]["s3_prefixes"] == ["approvals/", "requests/"]
    body = client.calls[-1][1]["audit_filter"]
    assert body.s3_prefixes == ["approvals/", "requests/"]


def test_filter_no_change_ignores_order():
    client = FakeClient(
        filters={
            "eda-log": {
                "edaapprovals": {
                    "actions": ["s3:PutObject"],
                    "s3_prefixes": ["requests/", "approvals/"],
                }
            }
        }
    )
    result = run(
        FILTER,
        client,
        bucket="eda-log",
        name="edaapprovals",
        actions=["s3:PutObject"],
        s3_prefixes=["approvals/", "requests/"],
    )
    assert result["changed"] is False
    assert client.called() == []
    assert result["filter"]["s3_prefixes"] == ["approvals/", "requests/"]


def test_filter_change_is_delete_then_create():
    client = FakeClient(
        filters={
            "eda-data": {"edaall": {"actions": ["s3:GetObject"], "s3_prefixes": None}}
        }
    )
    result = run(FILTER, client, bucket="eda-data", name="edaall", actions=["s3:All"])
    assert result["changed"] is True
    assert client.called() == [
        "delete_buckets_audit_filters",
        "post_buckets_audit_filters",
    ]
    assert client.filters["eda-data"]["edaall"]["actions"] == ["s3:All"]


def test_filter_delete_and_absent_when_missing():
    client = FakeClient(
        filters={"eda-data": {"edaall": {"actions": ["s3:All"], "s3_prefixes": None}}}
    )
    result = run(FILTER, client, bucket="eda-data", name="edaall", state="absent")
    assert result["changed"] is True and result["filter"] == {}
    assert client.called() == ["delete_buckets_audit_filters"]
    assert client.filters["eda-data"] == {}
    again = run(FILTER, client, bucket="eda-data", name="edaall", state="absent")
    assert again["changed"] is False and again["filter"] == {}
    # absent needs no actions and does not read the action list
    assert "get_bucket_audit_filter_actions" not in client.called(("get_",))


def test_filter_check_mode_reports_without_writing():
    client = FakeClient(filters={"eda-data": {}})
    result = run(
        FILTER,
        client,
        check_mode=True,
        bucket="eda-data",
        name="edaall",
        actions=["s3:All"],
    )
    assert result["changed"] is True and client.filters["eda-data"] == {}
    assert client.called() == []
    client = FakeClient(
        filters={
            "eda-data": {"edaall": {"actions": ["s3:GetObject"], "s3_prefixes": None}}
        }
    )
    result = run(
        FILTER,
        client,
        check_mode=True,
        bucket="eda-data",
        name="edaall",
        actions=["s3:All"],
    )
    assert result["changed"] is True
    assert client.filters["eda-data"]["edaall"]["actions"] == ["s3:GetObject"]
    assert client.called() == []


@pytest.mark.parametrize(
    "name", ["eda-all", "eda_all", "12345", "a" * 64, "", "eda all"]
)
def test_filter_rejects_a_bad_name_before_any_call(name):
    client = FakeClient(filters={"eda-data": {}})
    result = run(FILTER, client, bucket="eda-data", name=name, actions=["s3:All"])
    assert result["failed"] is True
    assert "letters and digits" in result["msg"]
    assert client.calls == []


def test_filter_accepts_a_long_alphanumeric_name():
    client = FakeClient(filters={"eda-data": {}})
    result = run(
        FILTER, client, bucket="eda-data", name="a" * 62 + "1", actions=["s3:All"]
    )
    assert result["changed"] is True


def test_filter_rejects_an_unknown_action_before_any_write():
    client = FakeClient(filters={"eda-data": {}})
    result = run(
        FILTER, client, bucket="eda-data", name="edaall", actions=["s3:All", "s3:Fly"]
    )
    assert result["failed"] is True
    assert "s3:Fly" in result["msg"] and "s3:All" in result["msg"]
    assert client.called() == []


def test_filter_skips_the_action_check_when_the_array_cannot_list_them():
    client = FakeClient(filters={"eda-data": {}})
    client.fail["get_bucket_audit_filter_actions"] = "Not supported"
    result = run(FILTER, client, bucket="eda-data", name="edaall", actions=["s3:Fly"])
    assert result["changed"] is True
    assert client.called() == ["post_buckets_audit_filters"]


def test_filter_needs_actions_to_create():
    result = run(
        FILTER, FakeClient(filters={"eda-data": {}}), bucket="eda-data", name="edaall"
    )
    assert result["failed"] is True and "actions" in result["msg"]
    result = run(
        FILTER,
        FakeClient(filters={"eda-data": {}}),
        bucket="eda-data",
        name="edaall",
        actions=[],
    )
    assert result["failed"] is True and "actions" in result["msg"]


def test_filter_array_error_carries_the_message():
    client = FakeClient(filters={"eda-data": {}})
    client.fail["post_buckets_audit_filters"] = "Bucket eda-data is destroyed"
    result = run(FILTER, client, bucket="eda-data", name="edaall", actions=["s3:All"])
    assert result["failed"] is True
    assert "Bucket eda-data is destroyed" in result["msg"] and "edaall" in result["msg"]
    client = FakeClient(
        filters={"eda-data": {"edaall": {"actions": ["s3:All"], "s3_prefixes": None}}}
    )
    client.fail["delete_buckets_audit_filters"] = "Filter is in use"
    result = run(FILTER, client, bucket="eda-data", name="edaall", state="absent")
    assert result["failed"] is True and "Filter is in use" in result["msg"]
    assert "edaall" in client.filters["eda-data"]


# ------------------------------------------------------------ telemetry target

TELEMETRY_POLICIES = ["device_perf_metrics", "device_space_metrics", "fs_perf_metrics"]
ENDPOINT = "prom.example.net:9090/api/v1/otlp"


def telemetry_target_row(
    name="eda-prom",
    endpoint=ENDPOINT,
    transport="otlp-http-protobuf-without-tls",
    source="2200-datavip",
    enabled=True,
):
    return {
        "name": name,
        "endpoint": endpoint,
        "transport": transport,
        "source": {"name": source},
        "enabled": enabled,
    }


def telemetry_policy_row(name, targets=()):
    return {
        "name": name,
        "enabled": True,
        "targets": [{"name": t, "resource_type": "telemetry-targets"} for t in targets],
        "rules": [{"name": f"{name}.1"}],
        "version": 1,
        "policy_type": "telemetry-metrics",
    }


def telemetry_rule(policy, index, metric, resolution):
    return {
        "name": f"{policy}.{index}",
        "policy": {"name": policy},
        "index": index,
        "metric_name": metric,
        "resolution": resolution,
    }


def telemetry_rest(targets=(), attached=None):
    """A REST array with three of the built-in policies; `attached` maps a
    policy to the targets it already carries."""
    attached = attached or {}
    return FakeRest(
        targets=targets,
        policies=[
            telemetry_policy_row(p, attached.get(p, ())) for p in TELEMETRY_POLICIES
        ],
        rules=[
            telemetry_rule("device_perf_metrics", 3, "storage.device.iops", 60000),
            telemetry_rule("device_perf_metrics", 1, "storage.device.bw_bpo", 60000),
            telemetry_rule("device_perf_metrics", 2, "storage.device.bw_bps", 60000),
            telemetry_rule("fs_perf_metrics", 1, "storage.device.fs.iops", 600000),
        ],
    )


def target_args(**over):
    args = dict(
        name="eda-prom",
        endpoint=ENDPOINT,
        transport="otlp-http-protobuf-without-tls",
        source="2200-datavip",
    )
    args.update(over)
    return args


def test_telemetry_target_create_posts_then_attaches_keeping_other_targets():
    rest = telemetry_rest(
        targets=[telemetry_target_row("other")],
        attached={"device_perf_metrics": ("other",)},
    )
    result = run(
        TELEMETRY_TARGET,
        FakeClient(),
        rest=rest,
        **target_args(policies=["fs_perf_metrics", "device_perf_metrics"]),
    )
    assert result["rc"] == 0 and result["changed"] is True
    assert result["target"] == {
        "name": "eda-prom",
        "endpoint": ENDPOINT,
        "transport": "otlp-http-protobuf-without-tls",
        "source": "2200-datavip",
        "enabled": True,
        "policies": ["device_perf_metrics", "fs_perf_metrics"],
    }
    assert "test" not in result
    assert rest.called() == [
        ("POST", "telemetry-targets"),
        ("PATCH", "telemetry-metrics-policies"),
        ("PATCH", "telemetry-metrics-policies"),
    ]
    post = [c for c in rest.calls if c[0] == "POST"][0]
    assert post[2] == {"names": "eda-prom"}
    assert post[3] == {
        "endpoint": ENDPOINT,
        "transport": "otlp-http-protobuf-without-tls",
        "source": {"name": "2200-datavip"},
    }
    patches = [c for c in rest.calls if c[0] == "PATCH"]
    assert patches[0][2] == {"names": "device_perf_metrics"}
    assert patches[0][3] == {"targets": [{"name": "other"}, {"name": "eda-prom"}]}
    assert patches[1][2] == {"names": "fs_perf_metrics"}
    assert patches[1][3] == {"targets": [{"name": "eda-prom"}]}
    assert rest.targets["eda-prom"] == telemetry_target_row()
    assert rest.logins == 1 and rest.logouts == 1 and rest.sessions[0].closed
    assert rest.versions == {"2.21"}


def test_telemetry_target_strips_a_scheme_with_a_warning():
    rest = telemetry_rest()
    result = run(
        TELEMETRY_TARGET,
        FakeClient(),
        rest=rest,
        name="eda-prom",
        endpoint="http://" + ENDPOINT,
        source="2200-datavip",
    )
    assert result["changed"] is True
    assert result["target"]["endpoint"] == ENDPOINT
    assert result["target"]["transport"] == "otlp-http-protobuf"
    assert any("scheme" in w for w in warnings_of(result))
    assert rest.targets["eda-prom"]["endpoint"] == ENDPOINT


def test_telemetry_target_rejects_an_endpoint_without_a_port_before_any_write():
    rest = telemetry_rest()
    result = run(
        TELEMETRY_TARGET,
        FakeClient(),
        rest=rest,
        **target_args(endpoint="prom.example.net"),
    )
    assert result["failed"] is True and "host:port" in result["msg"]
    assert rest.called() == []


def test_telemetry_target_no_change_when_everything_matches():
    rest = telemetry_rest(
        targets=[telemetry_target_row()],
        attached={"device_perf_metrics": ("eda-prom",)},
    )
    result = run(
        TELEMETRY_TARGET,
        FakeClient(),
        rest=rest,
        **target_args(policies=["device_perf_metrics"]),
    )
    assert result["changed"] is False and rest.called() == []
    assert result["target"]["policies"] == ["device_perf_metrics"]
    # policies omitted: the attachment is reported, not touched
    result = run(TELEMETRY_TARGET, FakeClient(), rest=rest, **target_args())
    assert result["changed"] is False and rest.called() == []
    assert result["target"]["policies"] == ["device_perf_metrics"]


def test_telemetry_target_update_patches_only_what_differs():
    rest = telemetry_rest(targets=[telemetry_target_row()])
    result = run(
        TELEMETRY_TARGET, FakeClient(), rest=rest, **target_args(enabled=False)
    )
    assert result["changed"] is True
    assert rest.called() == [("PATCH", "telemetry-targets")]
    assert rest.calls[-1][2] == {"names": "eda-prom"}
    assert rest.calls[-1][3] == {"enabled": False}
    assert rest.targets["eda-prom"]["enabled"] is False
    assert result["target"]["enabled"] is False
    result = run(
        TELEMETRY_TARGET,
        FakeClient(),
        rest=rest,
        **target_args(
            enabled=False, source="2201-datavip", endpoint="x.example.net:4318"
        ),
    )
    assert rest.calls[-1][3] == {
        "endpoint": "x.example.net:4318",
        "source": {"name": "2201-datavip"},
    }


def test_telemetry_target_create_disabled_posts_then_patches_enabled():
    rest = telemetry_rest()
    result = run(
        TELEMETRY_TARGET, FakeClient(), rest=rest, **target_args(enabled=False)
    )
    assert result["changed"] is True and result["target"]["enabled"] is False
    assert rest.called() == [
        ("POST", "telemetry-targets"),
        ("PATCH", "telemetry-targets"),
    ]
    writes = [c for c in rest.calls if c[0] != "GET"]
    assert "enabled" not in writes[0][3]
    assert writes[1][3] == {"enabled": False}
    assert rest.targets["eda-prom"]["enabled"] is False


def test_telemetry_target_delete_detaches_from_every_policy_first():
    rest = telemetry_rest(
        targets=[telemetry_target_row(), telemetry_target_row("other")],
        attached={
            "device_perf_metrics": ("eda-prom", "other"),
            "fs_perf_metrics": ("eda-prom",),
        },
    )
    result = run(
        TELEMETRY_TARGET, FakeClient(), rest=rest, name="eda-prom", state="absent"
    )
    assert result["changed"] is True and result["target"] == {}
    assert rest.called() == [
        ("PATCH", "telemetry-metrics-policies"),
        ("PATCH", "telemetry-metrics-policies"),
        ("DELETE", "telemetry-targets"),
    ]
    assert rest.calls[-3][2] == {"names": "device_perf_metrics"}
    assert rest.calls[-3][3] == {"targets": [{"name": "other"}]}
    assert rest.calls[-2][2] == {"names": "fs_perf_metrics"}
    assert rest.calls[-2][3] == {"targets": []}
    assert "eda-prom" not in rest.targets and "other" in rest.targets
    again = run(
        TELEMETRY_TARGET, FakeClient(), rest=rest, name="eda-prom", state="absent"
    )
    assert again["changed"] is False and again["target"] == {}
    assert len(rest.called()) == 3


def test_telemetry_target_check_mode_reports_without_writing():
    rest = telemetry_rest()
    result = run(
        TELEMETRY_TARGET,
        FakeClient(),
        rest=rest,
        check_mode=True,
        **target_args(policies=["fs_perf_metrics"], test=True),
    )
    assert result["changed"] is True and rest.called() == []
    assert result["target"]["policies"] == ["fs_perf_metrics"]
    assert "test" not in result and rest.targets == {}
    rest = telemetry_rest(
        targets=[telemetry_target_row()], attached={"fs_perf_metrics": ("eda-prom",)}
    )
    result = run(
        TELEMETRY_TARGET,
        FakeClient(),
        rest=rest,
        check_mode=True,
        name="eda-prom",
        state="absent",
    )
    assert result["changed"] is True and rest.called() == []
    assert "eda-prom" in rest.targets


def test_telemetry_target_test_returns_the_arrays_answer():
    rest = telemetry_rest(targets=[telemetry_target_row()])
    rest.test_answer = (400, None)
    result = run(TELEMETRY_TARGET, FakeClient(), rest=rest, **target_args(test=True))
    assert result["changed"] is False
    assert result["test"] == {"status": 400, "ok": False, "body": {}}
    assert rest.calls[-1][:3] == (
        "GET",
        "telemetry-targets/test",
        {"names": "eda-prom"},
    )
    rest.test_answer = (200, {"items": [{"name": "eda-prom"}]})
    result = run(TELEMETRY_TARGET, FakeClient(), rest=rest, **target_args(test=True))
    assert result["test"] == {
        "status": 200,
        "ok": True,
        "body": {"items": [{"name": "eda-prom"}]},
    }
    assert rest.called() == []


def test_telemetry_target_unknown_policy_fails_before_any_write():
    rest = telemetry_rest()
    result = run(
        TELEMETRY_TARGET, FakeClient(), rest=rest, **target_args(policies=["nope"])
    )
    assert result["failed"] is True
    assert "nope" in result["msg"] and "device_perf_metrics" in result["msg"]
    assert rest.called() == []


def test_telemetry_target_array_refusal_carries_the_message():
    rest = telemetry_rest()
    rest.fail[("POST", "telemetry-targets")] = (
        "Endpoint must be in the form '<hostname>:<port>' or "
        "'<hostname>:<port>/<path>' and reachable over the network"
    )
    result = run(TELEMETRY_TARGET, FakeClient(), rest=rest, **target_args())
    assert result["failed"] is True
    assert "reachable over the network" in result["msg"]
    # the finally ran: the session was closed on the array too
    assert rest.logouts == 1 and rest.sessions[0].closed


def test_telemetry_target_needs_endpoint_and_source_to_create():
    result = run(TELEMETRY_TARGET, FakeClient(), rest=telemetry_rest(), name="eda-prom")
    assert result["failed"] is True and "endpoint" in result["msg"]


def test_telemetry_target_bad_token_fails_without_leaking_it():
    rest = telemetry_rest()
    result = run(
        TELEMETRY_TARGET,
        FakeClient(),
        rest=rest,
        **target_args(api_token="wrong-token"),
    )
    assert result["failed"] is True and "Login" in result["msg"]
    assert "wrong-token" not in json.dumps(result)
    assert rest.logouts == 0 and rest.calls == []


def test_telemetry_target_api_version_and_verify_follow_the_options():
    rest = telemetry_rest(targets=[telemetry_target_row()])
    run(TELEMETRY_TARGET, FakeClient(), rest=rest, **target_args(api_version="2.22"))
    assert rest.versions == {"2.22"}
    assert rest.sessions[0].verify is True
    run(TELEMETRY_TARGET, FakeClient(), rest=rest, **target_args(disable_warnings=True))
    assert rest.sessions[1].verify is False
    assert SESSION_TOKEN not in json.dumps(
        run(TELEMETRY_TARGET, FakeClient(), rest=rest, **target_args())
    )


def test_telemetry_modules_ask_for_the_newest_version_the_array_serves():
    rest = telemetry_rest(targets=[telemetry_target_row()])
    rest.api_versions = ["2.21", "2.29", "2.30"]
    run(TELEMETRY_TARGET, FakeClient(), rest=rest, **target_args())
    run(
        TELEMETRY_POLICY,
        FakeClient(),
        rest=rest,
        name="device_perf_metrics",
        targets=["eda-prom"],
    )
    assert rest.versions == {"2.30"}
    # an array too old for the telemetry endpoints is refused before any login
    rest = telemetry_rest(targets=[telemetry_target_row()])
    rest.api_versions = ["2.16", "2.17"]
    result = run(TELEMETRY_TARGET, FakeClient(), rest=rest, **target_args())
    assert result["failed"] is True
    assert "2.21" in result["msg"] and "2.17" in result["msg"]
    assert rest.logins == 0 and rest.calls == []


def test_alert_flag_and_qos_ask_for_the_newest_version_the_array_serves():
    rest = FakeRest(alerts=[alert_row()])
    rest.api_versions = ["2.16", "2.21", "2.30"]
    run(ALERT_FLAG, FakeClient(), rest=rest, alert="1042", flagged=False)
    assert rest.versions == {"2.30"}
    client = FakeClient(qos=[qos_row()], buckets=[object_row("eda-data")])
    rest = FakeRest(client=client)
    rest.api_versions = ["2.17", "2.21", "2.30"]
    run(QOS, client, rest=rest, name="eda-tarpit", buckets=["eda-data"])
    assert rest.versions == {"2.30"}
    # an array below the floor is refused before any login
    client = FakeClient(qos=[qos_row()], buckets=[object_row("eda-data")])
    rest = FakeRest(client=client)
    rest.api_versions = ["2.15", "2.16"]
    result = run(QOS, client, rest=rest, name="eda-tarpit", buckets=["eda-data"])
    assert result["failed"] is True and "2.17" in result["msg"]
    assert rest.logins == 0


# ------------------------------------------------------------ telemetry policy


def test_telemetry_policy_sets_the_full_target_list_and_reports_the_rules():
    rest = telemetry_rest(
        targets=[telemetry_target_row(), telemetry_target_row("other")],
        attached={"device_perf_metrics": ("other",)},
    )
    result = run(
        TELEMETRY_POLICY,
        FakeClient(),
        rest=rest,
        name="device_perf_metrics",
        targets=["eda-prom"],
    )
    assert result["rc"] == 0 and result["changed"] is True
    assert result["policy"] == {
        "name": "device_perf_metrics",
        "enabled": True,
        "targets": ["eda-prom"],
        "metric_names": [
            "storage.device.bw_bpo",
            "storage.device.bw_bps",
            "storage.device.iops",
        ],
        "resolution_ms": 60000,
    }
    assert rest.called() == [("PATCH", "telemetry-metrics-policies")]
    patch = [c for c in rest.calls if c[0] == "PATCH"][0]
    assert patch[2] == {"names": "device_perf_metrics"}
    assert patch[3] == {"targets": [{"name": "eda-prom"}]}
    assert [t["name"] for t in rest.policies["device_perf_metrics"]["targets"]] == [
        "eda-prom"
    ]
    assert rest.logins == 1 and rest.logouts == 1


def test_telemetry_policy_no_change_ignores_order():
    rest = telemetry_rest(
        targets=[telemetry_target_row(), telemetry_target_row("other")],
        attached={"device_perf_metrics": ("other", "eda-prom")},
    )
    result = run(
        TELEMETRY_POLICY,
        FakeClient(),
        rest=rest,
        name="device_perf_metrics",
        targets=["eda-prom", "other"],
    )
    assert result["changed"] is False and rest.called() == []
    assert result["policy"]["targets"] == ["eda-prom", "other"]


def test_telemetry_policy_empty_list_detaches_and_a_policy_without_rules_reads_empty():
    rest = telemetry_rest(
        targets=[telemetry_target_row()],
        attached={"device_space_metrics": ("eda-prom",)},
    )
    result = run(
        TELEMETRY_POLICY,
        FakeClient(),
        rest=rest,
        name="device_space_metrics",
        targets=[],
    )
    assert result["changed"] is True
    assert [c[3] for c in rest.calls if c[0] == "PATCH"] == [{"targets": []}]
    assert rest.policies["device_space_metrics"]["targets"] == []
    assert result["policy"]["targets"] == []
    assert result["policy"]["metric_names"] == []
    assert result["policy"]["resolution_ms"] is None


def test_telemetry_policy_unknown_name_lists_the_arrays_policies():
    rest = telemetry_rest()
    result = run(TELEMETRY_POLICY, FakeClient(), rest=rest, name="nope", targets=[])
    assert result["failed"] is True
    assert "nope" in result["msg"] and "device_perf_metrics" in result["msg"]
    assert rest.called() == []


def test_telemetry_policy_check_mode_reports_without_writing():
    rest = telemetry_rest(targets=[telemetry_target_row()])
    result = run(
        TELEMETRY_POLICY,
        FakeClient(),
        rest=rest,
        check_mode=True,
        name="fs_perf_metrics",
        targets=["eda-prom"],
    )
    assert result["changed"] is True and rest.called() == []
    assert result["policy"]["targets"] == ["eda-prom"]
    assert result["policy"]["metric_names"] == ["storage.device.fs.iops"]
    assert result["policy"]["resolution_ms"] == 600000
    assert rest.policies["fs_perf_metrics"]["targets"] == []


def test_telemetry_policy_rules_refusal_is_a_warning_not_a_failure():
    rest = telemetry_rest(targets=[telemetry_target_row()])
    rest.fail[("GET", "telemetry-metrics-policies/rules")] = "Not supported"
    result = run(
        TELEMETRY_POLICY,
        FakeClient(),
        rest=rest,
        name="fs_perf_metrics",
        targets=["eda-prom"],
    )
    assert result["rc"] == 0 and result["changed"] is True
    assert result["policy"]["metric_names"] == []
    assert any("rules" in w for w in warnings_of(result))


def test_telemetry_policy_refuses_other_states_and_documents_enabled_read_only():
    result = run(
        TELEMETRY_POLICY,
        FakeClient(),
        rest=telemetry_rest(),
        name="fs_perf_metrics",
        targets=[],
        state="absent",
    )
    assert result["failed"] is True and "state" in result["msg"]
    with _stubbed(FakeClient(), FakeModels(), FakeRest()):
        module = _load(TELEMETRY_POLICY)
    assert "read-only" in module.DOCUMENTATION


# ---------------------------------------------------------- maintenance window


def test_maintenance_opens_a_window_in_milliseconds():
    client, models = FakeClient(), FakeModels()
    result = run(MAINTENANCE, client, models)
    assert result["rc"] == 0 and result["changed"] is True
    assert result["window"] == {
        "name": "array",
        "created": 1000,
        "expires": 1000 + 4 * HOUR_MS,
    }
    assert client.called() == ["post_maintenance_windows"]
    post = client.calls[-1][1]
    assert post["names"] == ["array"]
    assert type(post["maintenance_window"]).__name__ == "MaintenanceWindowPost"
    assert models.sent("MaintenanceWindowPost") == [{"timeout": 4 * HOUR_MS}]


def test_maintenance_open_window_is_left_alone_unless_extended():
    client = FakeClient(windows=[{"name": "array", "created": 5, "expires": 6}])
    result = run(MAINTENANCE, client, timeout=8)
    assert result["changed"] is False
    assert result["window"] == {"name": "array", "created": 5, "expires": 6}
    assert client.called() == []
    result = run(MAINTENANCE, client, timeout=8, extend=True)
    assert result["changed"] is True
    assert client.called() == ["delete_maintenance_windows", "post_maintenance_windows"]
    assert client.calls[-1][1]["maintenance_window"].timeout == 8 * HOUR_MS
    assert result["window"]["expires"] == 1000 + 8 * HOUR_MS


def test_maintenance_close_and_absent_when_none():
    client = FakeClient(windows=[{"name": "array", "created": 5, "expires": 6}])
    result = run(MAINTENANCE, client, state="absent")
    assert result["changed"] is True and result["window"] == {}
    assert client.called() == ["delete_maintenance_windows"]
    assert client.calls[-1][1] == {"names": ["array"]}
    assert client.windows == []
    again = run(MAINTENANCE, client, state="absent")
    assert again["changed"] is False and again["window"] == {}
    assert client.called() == ["delete_maintenance_windows"]


@pytest.mark.parametrize("hours", [0, 49, -4])
def test_maintenance_rejects_a_timeout_out_of_range_before_any_call(hours):
    client = FakeClient()
    result = run(MAINTENANCE, client, timeout=hours)
    assert result["failed"] is True and "timeout" in result["msg"]
    assert client.calls == []


def test_maintenance_check_mode_reports_without_writing():
    client = FakeClient()
    result = run(MAINTENANCE, client, check_mode=True)
    assert result["changed"] is True and client.windows == []
    assert result["window"] == {"name": "array", "created": None, "expires": None}
    assert client.called() == []
    client = FakeClient(windows=[{"name": "array", "created": 5, "expires": 6}])
    result = run(MAINTENANCE, client, check_mode=True, state="absent")
    assert result["changed"] is True and client.windows and client.called() == []


def test_maintenance_array_error_carries_the_message():
    client = FakeClient()
    client.fail["post_maintenance_windows"] = "timeout must be at most 48 hours"
    result = run(MAINTENANCE, client, timeout=48)
    assert result["failed"] is True and "at most 48 hours" in result["msg"]


# ------------------------------------------------------------------------- QoS


def qos_row(name="eda-tarpit", ops=100, bytes_per_sec=None, enabled=True):
    return {
        "name": name,
        "enabled": enabled,
        "max_total_ops_per_sec": ops,
        "max_total_bytes_per_sec": bytes_per_sec,
    }


def object_row(name, policy=None, destroyed=False):
    return {
        "name": name,
        "destroyed": destroyed,
        "qos_policy": {"name": policy} if policy else None,
    }


CLEAR_EMPTY = json.dumps({"qos_policy": {"name": ""}}, sort_keys=True)
CLEAR_NULL = json.dumps({"qos_policy": None}, sort_keys=True)


def test_qos_create_posts_the_policy_and_assigns_over_rest():
    client = FakeClient(
        file_systems=[object_row("scratch-01"), object_row("scratch-02", "other")],
        buckets=[object_row("eda-data")],
    )
    models, rest = FakeModels(), FakeRest(client=client)
    result = run(
        QOS,
        client,
        models,
        rest=rest,
        name="eda-tarpit",
        max_total_ops_per_sec=100,
        file_systems=["scratch-02", "scratch-01"],
        buckets=["eda-data"],
    )
    assert result["rc"] == 0 and result["changed"] is True
    assert result["policy"] == {
        "name": "eda-tarpit",
        "enabled": True,
        "max_total_ops_per_sec": 100,
        "max_total_bytes_per_sec": None,
        "file_systems": ["scratch-01", "scratch-02"],
        "buckets": ["eda-data"],
    }
    assert client.called() == ["post_qos_policies"]
    post = client.calls[[n for n, _ in client.calls].index("post_qos_policies")][1]
    assert post["names"] == ["eda-tarpit"]
    assert type(post["policy"]).__name__ == "QosPolicy"
    assert models.sent("QosPolicy") == [{"max_total_ops_per_sec": 100, "enabled": True}]
    assert rest.called() == [
        ("PATCH", "file-systems"),
        ("PATCH", "file-systems"),
        ("PATCH", "buckets"),
    ]
    assert rest.calls[0][2:] == (
        {"names": "scratch-01"},
        {"qos_policy": {"name": "eda-tarpit"}},
    )
    assert rest.calls[2][2:] == (
        {"names": "eda-data"},
        {"qos_policy": {"name": "eda-tarpit"}},
    )
    assert client.file_systems["scratch-02"]["qos_policy"] == {"name": "eda-tarpit"}
    assert client.buckets["eda-data"]["qos_policy"] == {"name": "eda-tarpit"}
    assert client.qos["eda-tarpit"] == qos_row()
    assert rest.logins == 1 and rest.logouts == 1


def test_qos_no_change_when_policy_and_assignments_match_and_needs_no_session():
    client = FakeClient(
        qos=[qos_row()], file_systems=[object_row("scratch-01", "eda-tarpit")]
    )
    rest = FakeRest(client=client)
    result = run(
        QOS,
        client,
        rest=rest,
        name="eda-tarpit",
        max_total_ops_per_sec=100,
        file_systems=["scratch-01"],
    )
    assert result["changed"] is False
    assert client.called() == [] and rest.called() == []
    assert rest.logins == 0 and rest.sessions[0].closed


def test_qos_update_patches_only_the_differing_cap():
    client, models = FakeClient(qos=[qos_row()]), FakeModels()
    result = run(
        QOS,
        client,
        models,
        rest=FakeRest(client=client),
        name="eda-tarpit",
        max_total_ops_per_sec=200,
        max_total_bytes_per_sec=1048576,
    )
    assert result["changed"] is True
    assert client.called() == ["patch_qos_policies"]
    assert models.sent("QosPolicy") == [
        {"max_total_ops_per_sec": 200, "max_total_bytes_per_sec": 1048576}
    ]
    assert result["policy"]["max_total_ops_per_sec"] == 200
    assert client.qos["eda-tarpit"]["max_total_bytes_per_sec"] == 1048576
    result = run(
        QOS, client, rest=FakeRest(client=client), name="eda-tarpit", enabled=False
    )
    assert client.calls[-1][1]["policy"].enabled is False
    assert not hasattr(client.calls[-1][1]["policy"], "max_total_ops_per_sec")


def test_qos_existing_policy_keeps_its_caps_when_none_given():
    client = FakeClient(qos=[qos_row()], buckets=[object_row("eda-data")])
    rest = FakeRest(client=client)
    result = run(QOS, client, rest=rest, name="eda-tarpit", buckets=["eda-data"])
    assert result["changed"] is True and client.called() == []
    assert rest.called() == [("PATCH", "buckets")]
    assert result["policy"]["max_total_ops_per_sec"] == 100
    assert result["policy"]["buckets"] == ["eda-data"]


def test_qos_create_needs_a_cap_and_ops_has_a_floor():
    client = FakeClient()
    result = run(QOS, client, rest=FakeRest(client=client), name="eda-tarpit")
    assert result["failed"] is True and "max_total_ops_per_sec" in result["msg"]
    assert client.called() == []
    client = FakeClient()
    result = run(
        QOS,
        client,
        rest=FakeRest(client=client),
        name="eda-tarpit",
        max_total_ops_per_sec=99,
    )
    assert result["failed"] is True and "100" in result["msg"]
    assert client.calls == []


def test_qos_delete_clears_every_live_carrier_then_deletes():
    client = FakeClient(
        qos=[qos_row()],
        file_systems=[
            object_row("scratch-01", "eda-tarpit"),
            object_row("gone", "eda-tarpit", destroyed=True),
            object_row("other-fs", "other"),
        ],
        buckets=[object_row("eda-data", "eda-tarpit"), object_row("plain")],
    )
    rest = FakeRest(client=client)
    result = run(QOS, client, rest=rest, name="eda-tarpit", state="absent")
    assert result["changed"] is True and result["policy"] == {}
    assert rest.called() == [("PATCH", "file-systems"), ("PATCH", "buckets")]
    assert rest.calls[0][2:] == ({"names": "scratch-01"}, {"qos_policy": {"name": ""}})
    assert rest.calls[1][2:] == ({"names": "eda-data"}, {"qos_policy": {"name": ""}})
    assert client.called() == ["delete_qos_policies"]
    assert client.file_systems["scratch-01"]["qos_policy"] is None
    assert client.buckets["eda-data"]["qos_policy"] is None
    # destroyed objects and other policies are left alone
    assert client.file_systems["gone"]["qos_policy"] == {"name": "eda-tarpit"}
    assert client.file_systems["other-fs"]["qos_policy"] == {"name": "other"}
    assert "eda-tarpit" not in client.qos
    assert not any("refused" in w for w in warnings_of(result))
    again = run(QOS, client, rest=rest, name="eda-tarpit", state="absent")
    assert again["changed"] is False and again["policy"] == {}


def test_qos_clear_falls_back_to_a_null_policy_with_a_warning():
    client = FakeClient(
        qos=[qos_row()], file_systems=[object_row("scratch-01", "eda-tarpit")]
    )
    rest = FakeRest(client=client)
    rest.clear_refused = {CLEAR_EMPTY}
    result = run(QOS, client, rest=rest, name="eda-tarpit", state="absent")
    assert result["rc"] == 0 and result["changed"] is True
    assert [c[3] for c in rest.calls] == [
        {"qos_policy": {"name": ""}},
        {"qos_policy": None},
    ]
    assert any("refused the empty-name form" in w for w in warnings_of(result))
    assert client.file_systems["scratch-01"]["qos_policy"] is None
    assert "eda-tarpit" not in client.qos
    client = FakeClient(
        qos=[qos_row()], file_systems=[object_row("scratch-01", "eda-tarpit")]
    )
    rest = FakeRest(client=client)
    rest.clear_refused = {CLEAR_EMPTY, CLEAR_NULL}
    result = run(QOS, client, rest=rest, name="eda-tarpit", state="absent")
    assert result["failed"] is True
    assert "Invalid body parameter: qos_policy" in result["msg"]
    assert "scratch-01" in result["msg"]
    assert client.called() == [] and "eda-tarpit" in client.qos


def test_qos_check_mode_reports_without_writing():
    client = FakeClient(file_systems=[object_row("scratch-01")])
    rest = FakeRest(client=client)
    result = run(
        QOS,
        client,
        rest=rest,
        check_mode=True,
        name="eda-tarpit",
        max_total_ops_per_sec=100,
        file_systems=["scratch-01"],
    )
    assert result["changed"] is True
    assert client.called() == [] and rest.called() == [] and client.qos == {}
    assert result["policy"]["file_systems"] == ["scratch-01"]
    client = FakeClient(
        qos=[qos_row()], file_systems=[object_row("scratch-01", "eda-tarpit")]
    )
    rest = FakeRest(client=client)
    result = run(
        QOS, client, rest=rest, check_mode=True, name="eda-tarpit", state="absent"
    )
    assert result["changed"] is True
    assert client.called() == [] and rest.called() == []
    assert "eda-tarpit" in client.qos
    assert client.file_systems["scratch-01"]["qos_policy"] == {"name": "eda-tarpit"}


def test_qos_array_errors_carry_the_message():
    client = FakeClient()
    client.fail["post_qos_policies"] = "max_total_ops_per_sec must be at least 100"
    result = run(
        QOS,
        client,
        rest=FakeRest(client=client),
        name="eda-tarpit",
        max_total_ops_per_sec=100,
    )
    assert result["failed"] is True and "at least 100" in result["msg"]
    client = FakeClient(qos=[qos_row()], file_systems=[object_row("scratch-01")])
    rest = FakeRest(client=client)
    rest.fail[("PATCH", "file-systems")] = "File system scratch-01 is destroyed"
    result = run(QOS, client, rest=rest, name="eda-tarpit", file_systems=["scratch-01"])
    assert result["failed"] is True and "is destroyed" in result["msg"]
    assert "eda-tarpit" in result["msg"] and "scratch-01" in result["msg"]
    client = FakeClient(qos=[qos_row()])
    result = run(
        QOS,
        client,
        rest=FakeRest(client=client),
        name="eda-tarpit",
        file_systems=["nope"],
    )
    assert result["failed"] is True and "nope" in result["msg"]


# -------------------------------------------------------- sessions and locks


def session_row(name, client="10.21.200.129", user="simon", protocol="nfs"):
    row = {
        "name": name,
        "client": client,
        "protocol": protocol,
        "port": 1023,
        "opens": 0,
    }
    if user is not None:
        row["user"] = user
    return row


def lock_row(
    name, client="10.21.200.129", fs="eda-demo-quota", path="/jobs/a.lock", inode=77
):
    return {
        "name": name,
        "client": client,
        "file_system": {"name": fs},
        "path": path,
        "inode": inode,
        "protocol": "nfs",
        "access_type": "exclusive",
    }


def test_session_removes_only_the_clients_sessions():
    client = FakeClient(
        sessions=[
            session_row("s1"),
            session_row("s2", client="10.21.200.7"),
            # a session the array lists without a user: read as unknown
            session_row("s3", user=None),
        ]
    )
    result = run(SESSION, client, client="10.21.200.129")
    assert result["rc"] == 0 and result["changed"] is True
    assert result["removed"] == {"sessions": 2, "locks": 0, "items": ["s1", "s3"]}
    assert client.called(("get_",)) == ["get_file_systems_sessions"]
    assert client.calls[0][1] == {}
    assert client.called() == ["delete_file_systems_sessions"]
    assert client.calls[-1][1] == {"names": ["s1", "s3"], "disruptive": False}
    assert set(client.sessions) == {"s2"}


def test_session_by_user_passes_protocols_and_disruptive():
    client = FakeClient(
        sessions=[session_row("s1", protocol="smb"), session_row("s2", protocol="nfs")]
    )
    result = run(SESSION, client, user="simon", protocols=["smb"], disruptive=True)
    assert result["removed"]["items"] == ["s1"]
    assert client.calls[0][1] == {"protocols": ["smb"]}
    assert client.calls[-1][1] == {"names": ["s1"], "disruptive": True}
    assert set(client.sessions) == {"s2"}


def test_session_nothing_matched_changes_nothing():
    client = FakeClient(sessions=[session_row("s1")])
    result = run(SESSION, client, client="10.9.9.9")
    assert result["changed"] is False
    assert result["removed"] == {"sessions": 0, "locks": 0, "items": []}
    assert client.called() == []


def test_session_locks_by_file_system_path_and_inode():
    client = FakeClient(
        locks=[
            lock_row("l1"),
            lock_row("l2", path="/jobs/sub/b.lock", inode=78),
            lock_row("l3", path="/other/c.lock", inode=79),
            lock_row("l4", fs="other-fs", path="/jobs/d.lock", inode=80),
        ]
    )
    result = run(
        SESSION,
        client,
        kind="locks",
        file_system="eda-demo-quota",
        path="/jobs",
        recursive=True,
    )
    assert result["removed"] == {"sessions": 0, "locks": 2, "items": ["l1", "l2"]}
    assert client.called() == ["delete_file_systems_locks"]
    assert client.calls[-1][1] == {"names": ["l1", "l2"]}
    assert set(client.locks) == {"l3", "l4"}
    # an exact path takes the lock on it, not what is under a longer name
    client = FakeClient(locks=[lock_row("l1"), lock_row("l2", path="/jobs/a.lock/x")])
    result = run(
        SESSION, client, kind="locks", file_system="eda-demo-quota", path="/jobs/a.lock"
    )
    assert result["removed"]["items"] == ["l1"]
    client = FakeClient(locks=[lock_row("l1"), lock_row("l2", inode=78)])
    result = run(SESSION, client, kind="locks", file_system="eda-demo-quota", inode=78)
    assert result["removed"]["items"] == ["l2"]
    # the client narrows the locks too
    client = FakeClient(locks=[lock_row("l1"), lock_row("l2", client="10.21.200.7")])
    result = run(SESSION, client, kind="locks", client="10.21.200.129")
    assert result["removed"]["items"] == ["l1"]


def test_session_both_skips_sessions_when_only_a_file_system_is_given():
    client = FakeClient(sessions=[session_row("s1")], locks=[lock_row("l1")])
    result = run(SESSION, client, kind="both", file_system="eda-demo-quota")
    assert result["removed"] == {"sessions": 0, "locks": 1, "items": ["l1"]}
    assert client.called(("get_",)) == ["get_file_systems_locks"]
    assert "s1" in client.sessions
    client = FakeClient(sessions=[session_row("s1")], locks=[lock_row("l1")])
    result = run(SESSION, client, kind="both", client="10.21.200.129")
    assert result["removed"] == {"sessions": 1, "locks": 1, "items": ["s1", "l1"]}
    assert client.sessions == {} and client.locks == {}


def test_session_check_mode_reports_what_would_go():
    client = FakeClient(sessions=[session_row("s1")], locks=[lock_row("l1")])
    result = run(SESSION, client, check_mode=True, kind="both", client="10.21.200.129")
    assert result["changed"] is True
    assert result["removed"] == {"sessions": 1, "locks": 1, "items": ["s1", "l1"]}
    assert client.called() == []
    assert "s1" in client.sessions and "l1" in client.locks


def test_session_needs_a_client_user_or_file_system():
    result = run(SESSION, FakeClient(), kind="locks", path="/x")
    assert result["failed"] is True and "client" in result["msg"]


def test_session_array_error_carries_the_message():
    client = FakeClient(sessions=[session_row("s1")])
    client.fail["delete_file_systems_sessions"] = "Session s1 has open files"
    result = run(SESSION, client, client="10.21.200.129")
    assert result["failed"] is True and "has open files" in result["msg"]
    assert "s1" in client.sessions
    client = FakeClient()
    client.fail["get_file_systems_locks"] = "Not supported"
    result = run(SESSION, client, kind="locks", file_system="x")
    assert result["failed"] is True and "Not supported" in result["msg"]


# ------------------------------------------------------------------ legal hold


def hold_row(name="case-4411", description="Litigation hold"):
    return {"name": name, "description": description, "realms": []}


def held_row(hold="case-4411", fs="eda-dr-demo", path="/cases/4411", status="held"):
    return {
        "legal_hold": {"name": hold},
        "file_system": {"name": fs},
        "path": path,
        "status": status,
    }


def test_legal_hold_create_posts_the_hold_then_holds_the_paths():
    client, models = FakeClient(), FakeModels()
    rest = FakeRest(client=client)
    result = run(
        LEGAL_HOLD,
        client,
        models,
        rest=rest,
        name="case-4411",
        description="Litigation hold",
        file_system="eda-dr-demo",
        paths=["/cases/4411/b", "/cases/4411/a"],
        recursive=True,
    )
    assert result["rc"] == 0 and result["changed"] is True
    assert result["hold"] == {
        "name": "case-4411",
        "description": "Litigation hold",
        "held_entities": [
            {"file_system": "eda-dr-demo", "path": "/cases/4411/a", "status": "held"},
            {"file_system": "eda-dr-demo", "path": "/cases/4411/b", "status": "held"},
        ],
    }
    assert client.called() == ["post_legal_holds", "post_legal_holds_held_entities"]
    assert models.sent("LegalHold") == [{"description": "Litigation hold"}]
    calls = dict(client.calls)
    assert calls["post_legal_holds"]["names"] == ["case-4411"]
    assert type(calls["post_legal_holds"]["hold"]).__name__ == "LegalHold"
    assert calls["post_legal_holds_held_entities"] == {
        "names": ["case-4411"],
        "file_system_names": ["eda-dr-demo"],
        "paths": ["/cases/4411/a", "/cases/4411/b"],
        "recursive": True,
    }


def test_legal_hold_create_without_description_or_paths():
    client, models = FakeClient(), FakeModels()
    result = run(
        LEGAL_HOLD, client, models, rest=FakeRest(client=client), name="case-4411"
    )
    assert result["changed"] is True
    assert models.sent("LegalHold") == [{}]
    assert result["hold"] == {
        "name": "case-4411",
        "description": None,
        "held_entities": [],
    }
    assert client.called() == ["post_legal_holds"]


def test_legal_hold_no_change_when_hold_and_paths_exist():
    client = FakeClient(holds=[hold_row()], held=[held_row()])
    result = run(
        LEGAL_HOLD,
        client,
        rest=FakeRest(client=client),
        name="case-4411",
        description="Litigation hold",
        file_system="eda-dr-demo",
        paths=["/cases/4411"],
    )
    assert result["changed"] is False and client.called() == []
    assert result["hold"]["held_entities"] == [
        {"file_system": "eda-dr-demo", "path": "/cases/4411", "status": "held"}
    ]
    assert client.calls[-1][0] == "get_legal_holds_held_entities"
    assert client.calls[-1][1] == {"names": ["case-4411"]}


def test_legal_hold_other_description_is_a_warning_not_a_change():
    client = FakeClient(holds=[hold_row()])
    result = run(
        LEGAL_HOLD,
        client,
        rest=FakeRest(client=client),
        name="case-4411",
        description="Something else",
    )
    assert result["changed"] is False and client.called() == []
    assert result["hold"]["description"] == "Litigation hold"
    assert any("description" in w for w in warnings_of(result))


def test_legal_hold_adds_only_the_missing_paths():
    client = FakeClient(holds=[hold_row()], held=[held_row()])
    result = run(
        LEGAL_HOLD,
        client,
        rest=FakeRest(client=client),
        name="case-4411",
        file_system="eda-dr-demo",
        paths=["/cases/4411", "/cases/4412"],
    )
    assert result["changed"] is True
    assert client.called() == ["post_legal_holds_held_entities"]
    assert dict(client.calls)["post_legal_holds_held_entities"]["paths"] == [
        "/cases/4412"
    ]
    assert [e["path"] for e in result["hold"]["held_entities"]] == [
        "/cases/4411",
        "/cases/4412",
    ]


def test_legal_hold_releases_paths_with_the_sdk_patch():
    client = FakeClient(
        holds=[hold_row()], held=[held_row(), held_row(path="/cases/4411/draft.txt")]
    )
    result = run(
        LEGAL_HOLD,
        client,
        name="case-4411",
        file_system="eda-dr-demo",
        paths=["/cases/4411/draft.txt", "/not/held"],
        entities_state="absent",
    )
    assert result["changed"] is True
    # one release per held path, and only released=true does it on the array
    assert client.called() == ["patch_legal_holds_held_entities"]
    release = next(c for c in client.calls if c[0] == "patch_legal_holds_held_entities")
    assert release[1] == {
        "names": ["case-4411"],
        "file_system_names": ["eda-dr-demo"],
        "paths": ["/cases/4411/draft.txt"],
        "released": True,
        "recursive": False,
    }
    assert result["hold"]["held_entities"] == [
        {"file_system": "eda-dr-demo", "path": "/cases/4411", "status": "held"}
    ]
    again = run(
        LEGAL_HOLD,
        client,
        name="case-4411",
        file_system="eda-dr-demo",
        paths=["/cases/4411/draft.txt"],
        entities_state="absent",
    )
    assert again["changed"] is False
    assert client.called().count("patch_legal_holds_held_entities") == 1


def test_legal_hold_delete_releases_everything_first():
    client = FakeClient(
        holds=[hold_row(), hold_row("other")],
        held=[
            held_row(),
            held_row(path="/cases/4411/x"),
            held_row(hold="other", path="/o"),
        ],
    )
    result = run(LEGAL_HOLD, client, name="case-4411", state="absent")
    assert result["changed"] is True and result["hold"] == {}
    releases = [c for c in client.calls if c[0] == "patch_legal_holds_held_entities"]
    assert [c[1]["paths"] for c in releases] == [["/cases/4411"], ["/cases/4411/x"]]
    assert all(c[1]["released"] is True for c in releases)
    assert client.called() == [
        "patch_legal_holds_held_entities",
        "patch_legal_holds_held_entities",
        "delete_legal_holds",
    ]
    assert "case-4411" not in client.holds and "other" in client.holds
    assert client.held == [held_row(hold="other", path="/o")]
    again = run(LEGAL_HOLD, client, name="case-4411", state="absent")
    assert again["changed"] is False and again["hold"] == {}


def test_legal_hold_check_mode_reports_without_writing():
    client = FakeClient()
    rest = FakeRest(client=client)
    result = run(
        LEGAL_HOLD,
        client,
        rest=rest,
        check_mode=True,
        name="case-4411",
        description="Litigation hold",
        file_system="eda-dr-demo",
        paths=["/cases/4411"],
    )
    assert result["changed"] is True and client.called() == [] and rest.calls == []
    assert result["hold"] == {
        "name": "case-4411",
        "description": "Litigation hold",
        "held_entities": [
            {"file_system": "eda-dr-demo", "path": "/cases/4411", "status": None}
        ],
    }
    assert client.holds == {} and rest.logins == 0
    client = FakeClient(holds=[hold_row()], held=[held_row()])
    rest = FakeRest(client=client)
    result = run(
        LEGAL_HOLD, client, rest=rest, check_mode=True, name="case-4411", state="absent"
    )
    assert result["changed"] is True and client.called() == [] and rest.calls == []
    assert "case-4411" in client.holds and client.held == [held_row()]


def test_legal_hold_paths_need_a_file_system_and_errors_carry_the_message():
    result = run(
        LEGAL_HOLD, FakeClient(), rest=FakeRest(), name="case-4411", paths=["/x"]
    )
    assert result["failed"] is True and "file_system" in result["msg"]
    client = FakeClient()
    client.fail["post_legal_holds"] = "Legal hold name is too long"
    result = run(LEGAL_HOLD, client, rest=FakeRest(client=client), name="case-4411")
    assert result["failed"] is True and "too long" in result["msg"]
    client = FakeClient(holds=[hold_row()], held=[held_row()])
    client.fail["patch_legal_holds_held_entities"] = "Held entity is in use"
    result = run(LEGAL_HOLD, client, name="case-4411", state="absent")
    assert result["failed"] is True and "in use" in result["msg"]
    assert "case-4411" in client.holds


# ------------------------------------------------------------------ alert flag


def alert_row(name="1042", code=1016, flagged=False):
    return {"name": name, "code": code, "flagged": flagged, "severity": "warning"}


def test_alert_flag_patches_the_flag():
    rest = FakeRest(alerts=[alert_row()])
    result = run(ALERT_FLAG, FakeClient(), rest=rest, alert="1042", flagged=True)
    assert result["rc"] == 0 and result["changed"] is True
    assert result["alert"] == {"name": "1042", "code": 1016, "flagged": True}
    assert rest.called() == [("PATCH", "alerts")]
    assert rest.calls[-1][2:] == ({"names": "1042"}, {"flagged": True})
    assert rest.alerts["1042"]["flagged"] is True
    assert rest.logins == 1 and rest.logouts == 1 and rest.sessions[0].closed
    result = run(ALERT_FLAG, FakeClient(), rest=rest, alert="1042", flagged=False)
    assert result["changed"] is True and rest.alerts["1042"]["flagged"] is False


def test_alert_flag_no_change_when_already_set():
    rest = FakeRest(alerts=[alert_row(flagged=True)])
    result = run(ALERT_FLAG, FakeClient(), rest=rest, alert="1042", flagged=True)
    assert result["changed"] is False and rest.called() == []
    assert result["alert"] == {"name": "1042", "code": 1016, "flagged": True}


def test_alert_flag_unknown_alert_fails_with_the_message():
    rest = FakeRest(alerts=[alert_row()])
    result = run(ALERT_FLAG, FakeClient(), rest=rest, alert="9999", flagged=True)
    assert result["failed"] is True and "9999" in result["msg"]
    assert rest.called() == []


def test_alert_flag_check_mode_reports_without_writing():
    rest = FakeRest(alerts=[alert_row()])
    result = run(
        ALERT_FLAG, FakeClient(), rest=rest, check_mode=True, alert="1042", flagged=True
    )
    assert result["changed"] is True and rest.called() == []
    assert rest.alerts["1042"]["flagged"] is False
    assert result["alert"]["flagged"] is True


def test_alert_flag_options_reach_the_request():
    rest = FakeRest(alerts=[alert_row()])
    run(
        ALERT_FLAG,
        FakeClient(),
        rest=rest,
        alert="1042",
        flagged=False,
        api_version="2.17",
        disable_warnings=True,
    )
    assert rest.versions == {"2.17"}
    assert rest.sessions[0].verify is False
    run(ALERT_FLAG, FakeClient(), rest=rest, alert="1042", flagged=False)
    assert rest.sessions[1].verify is True


def test_alert_flag_refusal_carries_the_message_and_never_the_token():
    rest = FakeRest(alerts=[alert_row()])
    rest.fail[("PATCH", "alerts")] = "Alert 1042 is closed"
    result = run(ALERT_FLAG, FakeClient(), rest=rest, alert="1042", flagged=True)
    assert result["failed"] is True and "is closed" in result["msg"]
    text = json.dumps(result)
    assert API_TOKEN not in text and SESSION_TOKEN not in text
    assert rest.logouts == 1


# ----------------------------------------------------------------- eradication


def eradication_array(delay_ms=DAY_MS, manual="all-enabled"):
    return {
        "name": "fb-a",
        "eradication_config": {
            "eradication_delay": delay_ms,
            "manual_eradication": manual,
        },
    }


def test_eradication_lengthens_the_delay_freely():
    client, models = FakeClient(), FakeModels()
    result = run(ERADICATION, client, models, eradication_delay_days=7)
    assert result["rc"] == 0 and result["changed"] is True
    assert result["eradication_config"] == {
        "eradication_delay_days": 7,
        "eradication_delay_ms": 7 * DAY_MS,
        "manual_eradication": "all-enabled",
    }
    assert client.called() == ["patch_arrays"]
    body = client.calls[-1][1]["array"]
    assert type(body).__name__ == "Array"
    assert type(body.eradication_config).__name__ == "ArrayEradicationConfig"
    assert models.sent("ArrayEradicationConfig") == [{"eradication_delay": 7 * DAY_MS}]
    assert client.array["eradication_config"]["eradication_delay"] == 7 * DAY_MS


def test_eradication_shortening_needs_force():
    client = FakeClient(array=eradication_array(7 * DAY_MS, "all-disabled"))
    result = run(ERADICATION, client, eradication_delay_days=1)
    assert result["failed"] is True and "force" in result["msg"]
    assert "7" in result["msg"] and "1" in result["msg"]
    assert client.called() == []
    result = run(ERADICATION, client, eradication_delay_days=1, force=True)
    assert result["changed"] is True
    assert client.array["eradication_config"]["eradication_delay"] == DAY_MS
    assert result["eradication_config"]["eradication_delay_days"] == 1


def test_eradication_disabling_manual_is_free_and_enabling_needs_force():
    client = FakeClient()
    result = run(ERADICATION, client, manual_eradication="all-disabled")
    assert result["changed"] is True
    assert client.array["eradication_config"]["manual_eradication"] == "all-disabled"
    assert not hasattr(
        client.calls[-1][1]["array"].eradication_config, "eradication_delay"
    )
    result = run(ERADICATION, client, manual_eradication="all-enabled")
    assert result["failed"] is True and "force" in result["msg"]
    assert client.array["eradication_config"]["manual_eradication"] == "all-disabled"
    result = run(ERADICATION, client, manual_eradication="all-enabled", force=True)
    assert result["changed"] is True
    assert client.array["eradication_config"]["manual_eradication"] == "all-enabled"
    # partially-enabled is still a loosening on the way to all-enabled
    client = FakeClient(array=eradication_array(DAY_MS, "partially-enabled"))
    result = run(ERADICATION, client, manual_eradication="all-enabled")
    assert result["failed"] is True and client.called() == []
    result = run(ERADICATION, client, manual_eradication="all-disabled")
    assert result["changed"] is True


def test_eradication_no_change_reports_the_settings():
    client = FakeClient()
    result = run(
        ERADICATION, client, eradication_delay_days=1, manual_eradication="all-enabled"
    )
    assert result["changed"] is False and client.called() == []
    assert result["eradication_config"] == {
        "eradication_delay_days": 1,
        "eradication_delay_ms": DAY_MS,
        "manual_eradication": "all-enabled",
    }
    # a delay that is not whole days reads back as a fraction
    client = FakeClient(array=eradication_array(DAY_MS // 2))
    result = run(ERADICATION, client, manual_eradication="all-enabled")
    assert result["eradication_config"]["eradication_delay_days"] == 0.5


@pytest.mark.parametrize("days", [0, 31])
def test_eradication_rejects_days_out_of_range_before_any_call(days):
    client = FakeClient()
    result = run(ERADICATION, client, eradication_delay_days=days)
    assert result["failed"] is True and "eradication_delay_days" in result["msg"]
    assert client.calls == []


def test_eradication_needs_one_of_the_two_settings():
    result = run(ERADICATION, FakeClient(), force=True)
    assert result["failed"] is True
    assert "eradication_delay_days" in result["msg"]


def test_eradication_check_mode_reports_without_writing():
    client = FakeClient()
    result = run(ERADICATION, client, check_mode=True, eradication_delay_days=7)
    assert result["changed"] is True and client.called() == []
    assert result["eradication_config"]["eradication_delay_days"] == 7
    assert client.array["eradication_config"]["eradication_delay"] == DAY_MS


def test_eradication_array_error_carries_the_message():
    client = FakeClient()
    client.fail["patch_arrays"] = (
        "eradication_delay cannot be changed while the array is locked"
    )
    result = run(ERADICATION, client, eradication_delay_days=7)
    assert result["failed"] is True and "cannot be changed" in result["msg"]
    assert client.array["eradication_config"]["eradication_delay"] == DAY_MS
