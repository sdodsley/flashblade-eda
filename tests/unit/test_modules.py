# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the three object-audit modules that need no array. Each module
file is loaded against stubs for the everpure.flashblade module_utils and for
pypureclient, then main() runs through the real AnsibleModule with its
arguments injected the way ansible-core does it; the printed JSON result is
read back. A fake client plays the array."""

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

ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULES = ROOT / "plugins" / "modules"
LOG_TARGET = "purefb_log_target"
POLICY = "purefb_audit_policy"
FILTER = "purefb_bucket_audit_filter"
ACTIONS = ["s3:All", "s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
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
    """The array as the modules see it: log targets, policies with their
    members, buckets with their audit filters. Every call is recorded in
    `calls`; a method named in `fail` answers with that array-style error."""

    def __init__(self, log_targets=(), policies=(), members=None, filters=None):
        self.calls = []
        self.fail = {}
        self.log_targets = {t["name"]: t for t in log_targets}
        self.policies = {p["name"]: p for p in policies}
        self.members = {k: set(v) for k, v in (members or {}).items()}
        self.filters = {k: dict(v) for k, v in (filters or {}).items()}
        self.actions = list(ACTIONS)

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


def _connection_spec():
    return dict(
        fb_url=dict(type="str"),
        api_token=dict(type="str", no_log=True),
        disable_warnings=dict(type="bool", default=False),
    )


@contextlib.contextmanager
def _stubbed(client, models):
    """sys.modules entries for the collection module_utils and the SDK, put
    back afterwards so that the other test files see what they expect."""
    saved = {name: sys.modules.get(name) for name in STUBS}
    for name in STUBS[:5]:
        sys.modules[name] = types.ModuleType(name)
    purefb = types.ModuleType(STUBS[5])
    purefb.get_system = lambda module: client
    purefb.purefb_argument_spec = _connection_spec
    sys.modules[STUBS[5]] = purefb
    sdk = types.ModuleType("pypureclient")
    sdk.__version__ = "1.98.0"
    sdk.flashblade = models
    sys.modules["pypureclient"] = sdk
    sys.modules["pypureclient.flashblade"] = models
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


def run(module_name, client, models=None, check_mode=False, **args):
    """Run a module's main() against the fakes and return its parsed result
    with the exit code under `rc`."""
    models = models if models is not None else FakeModels()
    args.setdefault("fb_url", "fb-a.example.net")
    args.setdefault("api_token", "not-a-real-token")
    if check_mode:
        args["_ansible_check_mode"] = True
    basic._ANSIBLE_ARGS = json.dumps({"ANSIBLE_MODULE_ARGS": args}).encode()
    # ansible-core 2.19 and later also want the serialization profile
    if hasattr(basic, "_ANSIBLE_PROFILE"):
        basic._ANSIBLE_PROFILE = "legacy"
    out = io.StringIO()
    with _stubbed(client, models):
        module = _load(module_name)
        with contextlib.redirect_stdout(out), pytest.raises(SystemExit) as exc:
            module.main()
    basic._ANSIBLE_ARGS = None
    result = json.loads(out.getvalue().strip().splitlines()[-1])
    result["rc"] = exc.value.code
    return result


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
    ],
)
def test_documentation_names_every_option(name, options):
    with _stubbed(FakeClient(), FakeModels()):
        module = _load(name)
    doc = yaml.safe_load(module.DOCUMENTATION)
    assert doc["module"] == name
    assert set(doc["options"]) == options
    assert doc["extends_documentation_fragment"] == ["everpure.flashblade.everpure.fb"]
    examples = yaml.safe_load(module.EXAMPLES)
    assert all(f"sdodsley.flashblade_eda.{name}" in task for task in examples)
    assert "api_token" not in module.EXAMPLES.replace(
        'api_token: "{{ api_token }}"', ""
    )
    returned = yaml.safe_load(module.RETURN)
    assert list(returned) == [
        {LOG_TARGET: "log_target", POLICY: "policy"}.get(name, "filter")
    ]


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
