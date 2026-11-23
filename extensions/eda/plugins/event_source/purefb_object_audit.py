# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""
purefb_object_audit.py - ansible-rulebook event source that tails the S3
audit log of an Everpure FlashBlade.

The array writes its object-store audit records into a bucket (a log
target): one object per rotation window and blade authority, named
<array id>/<log name prefix>/<yyyy>/<mm>/<dd>/<HHMMSS>.A<authority>.<n>.log,
one JSON record per line, written within seconds after the window closes;
an idle window writes nothing. This source lists that bucket with boto3,
reads each new object once and emits one event per record, one rollup per
object and one health event per poll. Thresholds and policy live in the
rulebook, not here.

Every string in an emitted event is plain text: a template delimiter in it
is broken by a space before the event is queued.

Written against Purity//FB 4.6.4 (REST 2.21) with boto3 1.44 and
py-pure-client 1.98.0. The REST reads (array id, log target, audit policies
and members, bucket space, audit filters) exist from REST 2.20 and run only
when the array entry carries an API token.

Arguments
---------
arrays            list of arrays (required). Each entry:
                    name              event `array` field and inventory host
                    s3_endpoint       https://<data address>, or
                    s3_endpoint_env   environment variable holding it
                    s3_access_key_env, s3_secret_key_env
                                      environment variables holding the
                                      read-only tail user's key (a literal
                                      s3_access_key / s3_secret_key is
                                      accepted but discouraged)
                    log_bucket        the log target's bucket (default eda-log)
                    log_key_prefix    key prefix of the log objects (default:
                                      <array id>/<log name prefix>/ from the
                                      REST reads with a token, else the single
                                      UUID-looking top-level common prefix of
                                      the bucket and its single sub-prefix)
                    fb_url / fb_url_env, api_token / api_token_env
                                      optional; enable the REST reads
                    state_bucket, state_access_key_env, state_secret_key_env
                                      optional; with all three the cursor is
                                      also kept at cursors/object_audit/
                                      <array>.json in that bucket and read
                                      back when the local file is missing
                  A JSON string (or a YAML string) that decodes to that list
                  is accepted too, so a platform credential can inject it as
                  one variable; it is decoded before validation and never
                  echoed into an error or a log, and neither is a key.
interval          seconds between polls (default 60)
verify_ssl        verify the S3 endpoint and the array certificate (default
                  false)
request_timeout   seconds to wait for each S3 or REST response (default 30;
                  the connect timeout is fixed at 10)
cursor_dir        directory of the cursor files <array>.json: newest key,
                  seen keys of the retained windows, floor and time; written
                  atomically (default ~/.flashblade-eda/object_audit)
rotation_ms       the log target's rotation (default: read from the log
                  target with a token, else 300000)
late_windows      windows before the newest still re-listed for a blade
                  authority that writes its object late (default 2)
replay_objects    newest log objects replayed at start-up (default 0: start
                  at the newest key, nothing historical; N is for tests)
canary_prefixes   key prefixes of canary objects (default canary/)
canary_readers    access key ids allowed to read canaries (default none)
landing_prefixes  key prefixes of landing zones (default landing/)
landing_markers   basenames that complete a dataset (default _SUCCESS,
                  manifest.json)
evidence_prefixes key prefixes of evidence (default evidence/)
honey_suffixes    key suffixes of honey objects (default .secret, .key, .pem)
approval_prefix   approval objects in the log bucket (default approvals/)
request_prefix    request objects in the log bucket (default requests/)
automation_keys   access key ids of the automation itself, whose records
                  carry by_automation; a union with the tail and state keys
                  of every array, which always count (default: none extra)
error_min_count, error_rate_pct
                  rollup: errors at or above the count and the share of
                  records set error_storm (default 5, 50)
denied_storm_count
                  rollup: denied records at or above which denied_storm is
                  set (default 5)
mass_delete_count rollup: deletes at or above which mass_delete is set
                  (default 50)
log_bucket_quota_warn_pct
                  health: the log bucket at or above this share of its quota
                  makes healthy false (default 80)
stale_rotations   health: no new log object for more than this many
                  rotations sets stale (default 2)
coverage_exclude  bucket name globs that may stay without an audit policy
                  (default eda-state; an explicit empty list excludes
                  nothing)
poll_error_escalate
                  consecutive failed polls of one array after which
                  purefb_poll_error carries escalate: true (default 10)

Events (one dict per event, exactly one top-level key, always with `array`)
-------------------------------------------------------------------------
purefb_s3_event         one per record: bucket, key (requestParameters.key,
                        "" for a bucket operation such as ListObjectsV2),
                        event_name, event_time (as logged), event_time_ms,
                        event_id, read_only, access_key_id, user, account,
                        source_ip, user_agent, error_code ("" when none),
                        error_message, ok, version_id, log_object (the log
                        key), by_automation, canary (key under a canary
                        prefix, GetObject/HeadObject/CopyObject/
                        GetObjectTagging, ok), canary_allowed (access_key_id
                        in canary_readers), landing (PutObject or
                        CompleteMultipartUpload, ok, key under a landing
                        prefix with a basename in landing_markers), dataset
                        (the path between the landing prefix and the marker,
                        "" otherwise), evidence (PutObject or
                        CompleteMultipartUpload, ok, under an evidence
                        prefix), honey (key ends with a honey suffix, any
                        event), approval (PutObject, ok, bucket is the
                        array's log_bucket, key under approval_prefix),
                        request (the same under request_prefix), request_id
                        (the key basename without extension when approval
                        or request, else ""), denied (error_code
                        AccessDenied, SignatureDoesNotMatch,
                        InvalidAccessKeyId or ExpiredToken), delete
                        (DeleteObject, DeleteMultipleObjects, DeleteBucket)
purefb_s3_rollup        one per log object, after its records: log_object,
                        window (the HHMMSS start as ISO time), record_count,
                        first_time, last_time, buckets (sorted),
                        by_event_name, by_access_key, by_source_ip,
                        by_error_code, error_count, error_pct, denied_count,
                        delete_count, top_access_key, top_access_key_count,
                        top_source_ip, error_storm (error_count >=
                        error_min_count and error_pct >= error_rate_pct),
                        denied_storm (denied_count >= denied_storm_count),
                        mass_delete (delete_count >= mass_delete_count),
                        canary_hits, honey_hits, bad_lines (lines that were
                        not JSON, skipped)
purefb_audit_health     one per poll per array: log_bucket, log_key_prefix,
                        objects_this_poll, records_this_poll,
                        bad_lines_this_poll, newest_log_object,
                        newest_log_age_s (-1 before any is seen), rotation_ms,
                        stale (newest_log_age_s above stale_rotations
                        rotations, only once a log object has been seen),
                        log_bucket_used_bytes (space.virtual),
                        log_bucket_quota_bytes (0 when none),
                        log_bucket_used_pct (0.0 without a quota),
                        log_bucket_quota_known, log_bucket_object_count,
                        policies [{name, enabled, log_targets, members}],
                        policies_enabled, policies_disabled,
                        uncovered_buckets (live buckets in no enabled
                        policy, minus coverage_exclude), uncovered_count,
                        members_without_filters (member buckets with no
                        audit filter: they audit nothing),
                        members_without_filters_count, rest_available
                        (false without a token; the REST-derived fields are
                        then empty, 0 or false), cursor_persisted (the
                        cursor reached the file or the state bucket),
                        healthy (not stale, no quota warning, no uncovered
                        bucket, no disabled policy, no member without filter)
purefb_poll_error       signal (object_audit), error, consecutive (failed
                        polls in a row on that array), escalate
"""

DOCUMENTATION = r"""
---
name: purefb_object_audit
short_description: Tail the S3 audit log bucket of an Everpure FlashBlade.
description:
  - Lists the audit log bucket of one or more FlashBlades with boto3, reads
    each new log object once and emits C(purefb_s3_event) per record,
    C(purefb_s3_rollup) per log object and C(purefb_audit_health) per poll.
    With an API token it also reads the log target, the audit policies and
    the log bucket's space over REST. Thresholds and policy live in the
    rulebook. The module docstring lists every event and its fields.
options:
  arrays:
    description:
      - Arrays to tail. Each entry has C(name), C(s3_endpoint) or
        C(s3_endpoint_env), C(s3_access_key_env) and C(s3_secret_key_env)
        (names of environment variables), optionally C(log_bucket),
        C(log_key_prefix), C(fb_url)/C(fb_url_env) with
        C(api_token)/C(api_token_env) for the REST reads, and C(state_bucket)
        with C(state_access_key_env) and C(state_secret_key_env) for the
        cursor copy in the state bucket.
      - A JSON string is accepted so a platform credential can inject the
        list as one extra var (a YAML string decodes the same way); the
        string is decoded before validation and is never written to a log.
    type: list
    elements: dict
    required: true
  interval:
    description: Seconds between polls.
    type: int
    default: 60
  verify_ssl:
    description: Verify the S3 endpoint and the array certificate.
    type: bool
    default: false
  request_timeout:
    description: Seconds to wait for each S3 or REST response; the connect timeout is fixed at 10.
    type: int
    default: 30
  cursor_dir:
    description: Directory of the per-array cursor files, written atomically.
    type: str
    default: ~/.flashblade-eda/object_audit
  rotation_ms:
    description: The log target's rotation in milliseconds; read from the array when a token is given, else 300000.
    type: int
  late_windows:
    description: Windows before the newest that are still re-listed for late blade authorities.
    type: int
    default: 2
  replay_objects:
    description: Newest log objects replayed at start-up; 0 starts at the newest key and emits nothing historical.
    type: int
    default: 0
  canary_prefixes:
    description: Key prefixes of canary objects.
    type: list
    elements: str
    default: [canary/]
  canary_readers:
    description: Access key ids allowed to read canaries.
    type: list
    elements: str
    default: []
  landing_prefixes:
    description: Key prefixes of landing zones.
    type: list
    elements: str
    default: [landing/]
  landing_markers:
    description: Object basenames that mark a dataset as complete.
    type: list
    elements: str
    default: [_SUCCESS, manifest.json]
  evidence_prefixes:
    description: Key prefixes of evidence objects.
    type: list
    elements: str
    default: [evidence/]
  honey_suffixes:
    description: Key suffixes of honey objects.
    type: list
    elements: str
    default: [.secret, .key, .pem]
  approval_prefix:
    description: Key prefix of approval objects in the log bucket.
    type: str
    default: approvals/
  request_prefix:
    description: Key prefix of request objects in the log bucket.
    type: str
    default: requests/
  automation_keys:
    description: Access key ids of the automation itself, a union with the tail and state keys of every array, which always count.
    type: list
    elements: str
    default: []
  error_min_count:
    description: Errors in one log object at or above which, with error_rate_pct, error_storm is set.
    type: int
    default: 5
  error_rate_pct:
    description: Share of records in error at or above which error_storm is set.
    type: float
    default: 50
  denied_storm_count:
    description: Denied records in one log object at or above which denied_storm is set.
    type: int
    default: 5
  mass_delete_count:
    description: Deletes in one log object at or above which mass_delete is set.
    type: int
    default: 50
  log_bucket_quota_warn_pct:
    description: The log bucket at or above this share of its quota makes healthy false.
    type: float
    default: 80
  stale_rotations:
    description: No new log object for more than this many rotations sets stale.
    type: int
    default: 2
  coverage_exclude:
    description: Bucket name globs that may stay without an audit policy; an explicit empty list excludes nothing.
    type: list
    elements: str
    default: [eda-state]
  poll_error_escalate:
    description: Consecutive failed polls of one array after which purefb_poll_error carries escalate true.
    type: int
    default: 10
"""

EXAMPLES = r"""
- name: FlashBlade object audit
  hosts: all
  sources:
    - sdodsley.flashblade_eda.purefb_object_audit:
        arrays: "{{ object_audit_arrays }}"
        interval: "{{ object_audit_interval }}"
        canary_readers: "{{ canary_readers }}"
  rules:
    - name: Canary object read by a key not on the allow-list
      condition: >-
        event.purefb_s3_event.canary == true and
        event.purefb_s3_event.canary_allowed == false
      action:
        debug:
          msg: >-
            {{ event.purefb_s3_event.array }}:
            {{ event.purefb_s3_event.access_key_id }} read
            {{ event.purefb_s3_event.key }}
"""

import asyncio
import fnmatch
import inspect
import json
import logging
import os
import re
import time
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import urllib3

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
except ImportError as exc:  # the one hard dependency this source adds
    raise ImportError(
        "purefb_object_audit needs boto3 to tail the log bucket: pip install boto3"
    ) from exc

try:
    from pypureclient import flashblade
except ImportError:  # the REST reads are optional; Array says so when a token is set
    flashblade = None

try:
    import yaml
except ImportError:  # ansible-rulebook ships PyYAML; a bare interpreter may not
    yaml = None

logger = logging.getLogger(__name__)

# The plain-text helpers of the jinja_safe event filter, copied because
# ansible-rulebook loads each plugin from its own file; the copy is kept
# identical to the filter's and a unit test compares the four.

DELIMITER_BRACE = re.compile(r"\{(?=[{%#])")
# the override header, with or without its colon ("#jinja2" + ":" joined
# later would rebuild it)
HEADER_HASH = re.compile(r"#(?=jinja2)")
# a brace or hash left at the very end could complete a delimiter or the
# header when two strings are joined
TRAILING = re.compile(r"[{#]$")


def clean_text(value: str) -> str:
    """The string with every template delimiter broken by a space: each
    brace that starts one gets a space after it (so a run of braces becomes
    "{ { {", with nothing left to open a template), the "#" of the override
    header is split from "jinja2", and a brace or hash at the very end gets
    a space too."""
    if "{" in value:
        value = DELIMITER_BRACE.sub("{ ", value)
    if "#" in value:
        value = HEADER_HASH.sub("# ", value)
    if value and value[-1] in "{#":
        value = TRAILING.sub(lambda m: m.group(0) + " ", value)
    return value


def clean(value: Any) -> Any:
    """`value` with every string in it, however deep, passed through
    clean_text; other types unchanged."""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return {clean(k): clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, tuple):
        return tuple(clean(v) for v in value)
    return value


PLUGIN = "purefb_object_audit"
SIGNAL = "object_audit"
CONNECT_TIMEOUT = 10  # seconds to open a connection; the read timeout is an argument
DEFAULT_LOG_BUCKET = "eda-log"
DEFAULT_ROTATION_MS = 300_000
DEFAULT_CURSOR_DIR = "~/.flashblade-eda/object_audit"
USER_AGENT = "flashblade-eda/purefb_object_audit"
# <prefix><yyyy>/<mm>/<dd>/<HHMMSS>.A<authority>.<sequence>.log
LOG_KEY = re.compile(r"(\d{4})/(\d{2})/(\d{2})/(\d{6})\.A(\d+)\.(\d+)\.log$")
UUID_PREFIX = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}/$", re.I)
EVENT_TIME = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?Z$"
)
READ_EVENTS = ("GetObject", "HeadObject", "CopyObject", "GetObjectTagging")
WRITE_EVENTS = ("PutObject", "CompleteMultipartUpload")
DELETE_EVENTS = ("DeleteObject", "DeleteMultipleObjects", "DeleteBucket")
DENIED_CODES = (
    "AccessDenied",
    "SignatureDoesNotMatch",
    "InvalidAccessKeyId",
    "ExpiredToken",
)

LogObject = namedtuple("LogObject", "key modified_ms window")


# --------------------------------------------------------------------------
# helpers (the same shapes as purefb_events)
# --------------------------------------------------------------------------
def _as_list(value):
    if not value:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return list(value)


def _decode_arrays(value, plugin):
    """The arrays argument as the structure the plugin documents.

    A platform credential injects the list (or mapping) as one string
    variable, so a JSON string, or a YAML string, is decoded here before the
    argument is validated. The text is never echoed into an error message or
    a log record: it carries access keys and API tokens.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    try:
        decoded = json.loads(text)
    except ValueError:
        decoded = None
    if not isinstance(decoded, (list, dict)) and yaml is not None:
        try:
            decoded = yaml.safe_load(text)
        except yaml.YAMLError:
            decoded = None
    if isinstance(decoded, (list, dict)):
        return decoded
    raise ValueError(
        f"{plugin}: arrays is a string that does not decode as JSON (or YAML) "
        "into the documented structure; the text is not shown because it may "
        "hold access keys"
    )


def _truthy(value, default=False):
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _items(resp, what):
    """Return the response items as dicts, or raise with the array's message."""
    if resp is None or not hasattr(resp, "items"):
        errs = getattr(resp, "errors", None) or []
        msg = "; ".join(getattr(e, "message", str(e)) for e in errs) or str(resp)
        raise RuntimeError(f"{what}: {msg}")
    out = []
    for item in resp.items:
        out.append(item.to_dict() if hasattr(item, "to_dict") else dict(item))
    return out


def _first(items, default=None):
    return items[0] if items else (default if default is not None else {})


def _name(ref):
    if isinstance(ref, dict):
        return ref.get("name")
    return ref


def _now_ms():
    return int(time.time() * 1000)


def _accepts(fn, param):
    """Whether a callable takes `param`: the SDK adds parameters by REST
    version, so the client is built with what the installed release offers.
    Anything that cannot be inspected is taken to accept it."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return True
    return param in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


def _poll_error(signal, array, error, consecutive=1, escalate=False):
    return {
        "purefb_poll_error": {
            "signal": signal,
            "array": array,
            "error": str(error),
            "consecutive": consecutive,
            "escalate": escalate,
        }
    }


def _poll_streak(state, signal, array, failed):
    """Consecutive failed polls on one array: the count after this poll, 0
    after a success."""
    streaks = state.setdefault("poll_errors", {}).setdefault(signal, {})
    streaks[array] = streaks.get(array, 0) + 1 if failed else 0
    return streaks[array]


# --------------------------------------------------------------------------
# time and key helpers
# --------------------------------------------------------------------------
def _event_time_ms(text):
    """Milliseconds since the epoch for a time as the array logs it
    (2026-11-12T20:08:51.805Z); None for anything else."""
    m = EVENT_TIME.match(str(text or ""))
    if not m:
        return None
    try:
        dt = datetime(*(int(g) for g in m.groups()[:6]), tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(dt.timestamp()) * 1000 + int((m.group(7) or "0").ljust(3, "0")[:3])


def _utc(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _window_path(ms, rotation_ms):
    """<yyyy>/<mm>/<dd>/<HHMMSS> of the rotation window that holds `ms`: the
    part of a log key that orders it in time."""
    start = (int(ms) // int(rotation_ms)) * int(rotation_ms)
    return _utc(start).strftime("%Y/%m/%d/%H%M%S")


def _window_ms(path):
    dt = datetime.strptime(path, "%Y/%m/%d/%H%M%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _window_iso(path):
    y, mo, d, hms = path.split("/")
    return f"{y}-{mo}-{d}T{hms[:2]}:{hms[2:4]}:{hms[4:]}Z"


def _log_window(key):
    """The window path of a log key, None for any other object in the bucket."""
    m = LOG_KEY.search(str(key or ""))
    return "/".join(m.group(1, 2, 3, 4)) if m else None


# --------------------------------------------------------------------------
# array handle: one S3 client (two with a state bucket) and, with a token,
# one SDK client per array, rebuilt after a failure
# --------------------------------------------------------------------------
def _value(spec, field):
    """A literal field, or the environment variable its `_env` twin names."""
    value = spec.get(field)
    if not value and spec.get(field + "_env"):
        value = os.environ.get(spec[field + "_env"])
    return value


class Array:
    def __init__(self, spec, verify_ssl, request_timeout=30):
        self.name = spec["name"]
        endpoint = _value(spec, "s3_endpoint")
        if not endpoint:
            raise ValueError(
                f"array {self.name}: s3_endpoint or s3_endpoint_env is required"
            )
        self.endpoint = endpoint.rstrip("/")
        self._access = _value(spec, "s3_access_key")
        self._secret = _value(spec, "s3_secret_key")
        if not self._access or not self._secret:
            raise ValueError(
                f"array {self.name}: s3_access_key_env and s3_secret_key_env must "
                "name environment variables that are set"
            )
        self.log_bucket = spec.get("log_bucket") or DEFAULT_LOG_BUCKET
        prefix = str(spec.get("log_key_prefix") or "").strip()
        self.log_key_prefix = prefix.rstrip("/") + "/" if prefix else ""

        url = _value(spec, "fb_url") or ""
        token = _value(spec, "api_token")
        if bool(url) != bool(token):
            raise ValueError(
                f"array {self.name}: fb_url and api_token (or their _env forms) go "
                "together; give both for the REST reads or neither"
            )
        if token and flashblade is None:
            raise ValueError(
                f"array {self.name}: py-pure-client is not installed; "
                "pip install py-pure-client or drop api_token"
            )
        self.host = url.replace("https://", "").replace("http://", "").strip("/")
        self._token = token
        self.has_rest = bool(self.host and token)

        self.state_bucket = spec.get("state_bucket") or ""
        self._state_access = _value(spec, "state_access_key") or ""
        self._state_secret = _value(spec, "state_secret_key") or ""
        if self.state_bucket and not (self._state_access and self._state_secret):
            logger.warning(
                "%s: %s: state_bucket needs state_access_key_env and "
                "state_secret_key_env; the cursor stays local",
                PLUGIN,
                self.name,
            )
            self.state_bucket = ""

        self._verify = verify_ssl
        self._timeout = float(request_timeout)
        self._s3 = None
        self._state_s3 = None
        self._client = None
        self.lock = asyncio.Lock()

    @property
    def access_key_id(self):
        return self._access

    @property
    def state_access_key_id(self):
        return self._state_access if self.state_bucket else ""

    def _s3_client(self, access, secret):
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=access,
            aws_secret_access_key=secret,
            verify=self._verify,
            region_name="us-east-1",
            config=Config(
                s3={"addressing_style": "path"},
                connect_timeout=CONNECT_TIMEOUT,
                read_timeout=self._timeout,
                retries={"max_attempts": 2},
            ),
        )

    @property
    def s3(self):
        if self._s3 is None:
            self._s3 = self._s3_client(self._access, self._secret)
        return self._s3

    @property
    def state_s3(self):
        if self._state_s3 is None:
            self._state_s3 = self._s3_client(self._state_access, self._state_secret)
        return self._state_s3

    @property
    def client(self):
        if self._client is None:
            # built as purefb_events builds it: the timeout also bounds the
            # version probe; older releases take neither it nor user_agent
            kwargs = {"api_token": self._token, "verify_ssl": self._verify}
            if _accepts(flashblade.Client, "timeout"):
                kwargs["timeout"] = (CONNECT_TIMEOUT, self._timeout)
            if _accepts(flashblade.Client, "user_agent"):
                kwargs["user_agent"] = USER_AGENT
            self._client = flashblade.Client(target=self.host, **kwargs)
        return self._client

    def reset(self):
        self._s3 = None
        self._state_s3 = None
        self._client = None


# --------------------------------------------------------------------------
# the cursor: a local file, written atomically, mirrored to the state bucket
# --------------------------------------------------------------------------
def _cursor_path(arr, args):
    base = os.path.expanduser(str(args.get("cursor_dir") or DEFAULT_CURSOR_DIR))
    return os.path.join(base, f"{arr.name}.json")


def _cursor_key(arr):
    return f"cursors/object_audit/{arr.name}.json"


def _bucket_cursor(arr):
    """The copy in the state bucket, for a host whose local file is gone;
    None when there is none or the read fails (best effort)."""
    if not arr.state_bucket:
        return None
    try:
        obj = arr.state_s3.get_object(Bucket=arr.state_bucket, Key=_cursor_key(arr))
        return json.loads(obj["Body"].read())
    except Exception as exc:  # noqa: BLE001 - best effort
        code = ((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code")
        missing = isinstance(exc, ClientError) and code in ("NoSuchKey", "404")
        (logger.debug if missing else logger.warning)(
            "%s: %s: cursor copy not read: %s", PLUGIN, arr.name, exc
        )
        return None


def _load_cursor(arr, args):
    st = {
        "last_key": None,
        "last_key_time_ms": None,
        "floor": None,
        "seen": set(),
        "prefix": "",
        "primed": False,
    }
    data = None
    try:
        with open(_cursor_path(arr, args), encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        data = _bucket_cursor(arr)
    except (OSError, ValueError) as exc:
        logger.warning("%s: %s: cursor file unreadable: %s", PLUGIN, arr.name, exc)
    if isinstance(data, dict):
        st.update(
            last_key=data.get("last_key") or None,
            last_key_time_ms=data.get("last_key_time_ms"),
            floor=data.get("floor") or None,
            seen=set(data.get("seen") or []),
            primed=True,
        )
        logger.info(
            "%s: %s: cursor restored, newest %s", PLUGIN, arr.name, st["last_key"]
        )
    return st


def _save_cursor(arr, st, args):
    """Write the cursor: the local file through a temporary name and
    os.replace, so a crash leaves the old cursor rather than half a new one;
    then the state bucket copy, best effort. True when either landed."""
    data = {
        "array": arr.name,
        "last_key": st["last_key"],
        "last_key_time_ms": st["last_key_time_ms"],
        "floor": st["floor"],
        "seen": sorted(st["seen"]),
        "time": _utc(_now_ms()).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    text = json.dumps(data, indent=1, sort_keys=True)
    path = _cursor_path(arr, args)
    persisted = False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(path + ".tmp", path)
        persisted = True
    except OSError as exc:
        logger.warning("%s: %s: cursor file not written: %s", PLUGIN, arr.name, exc)
    if arr.state_bucket:
        try:
            arr.state_s3.put_object(
                Bucket=arr.state_bucket,
                Key=_cursor_key(arr),
                Body=text.encode("utf-8"),
                ContentType="application/json",
            )
            persisted = True
        except Exception as exc:  # noqa: BLE001 - the local file is the primary
            logger.warning("%s: %s: cursor copy not written: %s", PLUGIN, arr.name, exc)
    return persisted


def _array_state(state, arr, args):
    per_array = state.setdefault(SIGNAL, {})
    if arr.name not in per_array:
        per_array[arr.name] = _load_cursor(arr, args)
    return per_array[arr.name]


# --------------------------------------------------------------------------
# the bucket: listing, prefix discovery, reading one log object
# --------------------------------------------------------------------------
def _list_log_objects(s3, bucket, prefix, start_after):
    """Every log object under the prefix after `start_after`, oldest first,
    across pages. Other objects in the bucket (approvals, requests,
    receipts) are left out here."""
    out = []
    kw = {"Bucket": bucket, "Prefix": prefix}
    if start_after:
        kw["StartAfter"] = start_after
    while True:
        resp = s3.list_objects_v2(**kw)
        for obj in resp.get("Contents") or []:
            key = obj.get("Key") or ""
            window = _log_window(key)
            if window is None:
                continue
            modified = obj.get("LastModified")
            ms = (
                int(modified.timestamp() * 1000)
                if hasattr(modified, "timestamp")
                else None
            )
            out.append(LogObject(key, ms, window))
        token = resp.get("NextContinuationToken") if resp.get("IsTruncated") else None
        if not token:
            break
        kw["ContinuationToken"] = token
    out.sort()
    return out


def _discover_prefix(s3, bucket):
    """Without a token the array id is not known, so the prefix is read off
    the bucket: the one top-level common prefix that looks like a UUID, and
    the one log-name prefix under it when there is exactly one."""
    resp = s3.list_objects_v2(Bucket=bucket, Delimiter="/")
    tops = [p.get("Prefix") or "" for p in resp.get("CommonPrefixes") or []]
    uuids = [p for p in tops if UUID_PREFIX.match(p)]
    if len(uuids) != 1:
        return ""
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=uuids[0], Delimiter="/")
    subs = [p.get("Prefix") or "" for p in resp.get("CommonPrefixes") or []]
    return subs[0] if len(subs) == 1 else uuids[0]


def _log_prefix(arr, st, rest):
    if arr.log_key_prefix:
        return arr.log_key_prefix
    if rest and rest.get("array_id"):
        parts = [rest["array_id"], rest.get("log_name_prefix") or ""]
        st["prefix"] = "/".join(p for p in parts if p) + "/"
    elif not st.get("prefix"):
        st["prefix"] = _discover_prefix(arr.s3, arr.log_bucket)
    return st["prefix"]


def _read_log_object(s3, bucket, key):
    """The records of one log object (NDJSON) and the count of lines that
    were not JSON; a bad line is logged and skipped, never fatal."""
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    records, bad = [], 0
    for number, line in enumerate(body.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            rec = None
        if isinstance(rec, dict):
            records.append(rec)
        else:
            bad += 1
            logger.warning(
                "%s: %s line %d is not a record; skipped", PLUGIN, key, number
            )
    return records, bad


# --------------------------------------------------------------------------
# records -> events, one log object -> rollup
# --------------------------------------------------------------------------
class Rules:
    """The classification arguments, read once per poll."""

    def __init__(self, args, automation):
        self.canary_prefixes = tuple(
            _as_list(args.get("canary_prefixes")) or ["canary/"]
        )
        self.canary_readers = set(_as_list(args.get("canary_readers")))
        self.landing_prefixes = tuple(
            _as_list(args.get("landing_prefixes")) or ["landing/"]
        )
        self.landing_markers = set(
            _as_list(args.get("landing_markers")) or ["_SUCCESS", "manifest.json"]
        )
        self.evidence_prefixes = tuple(
            _as_list(args.get("evidence_prefixes")) or ["evidence/"]
        )
        self.honey_suffixes = tuple(
            _as_list(args.get("honey_suffixes")) or [".secret", ".key", ".pem"]
        )
        self.approval_prefix = str(args.get("approval_prefix") or "approvals/")
        self.request_prefix = str(args.get("request_prefix") or "requests/")
        self.automation = set(automation)
        self.error_min_count = int(args.get("error_min_count", 5))
        self.error_rate_pct = float(args.get("error_rate_pct", 50))
        self.denied_storm_count = int(args.get("denied_storm_count", 5))
        self.mass_delete_count = int(args.get("mass_delete_count", 50))


def _automation_keys(arr, state, args):
    """The access key ids whose records are the automation's own: the
    argument united with the tail and state keys of every array (seeded by
    the poll loop) and at least this array's, so naming an extra key never
    un-marks the plugin's own."""
    keys = set(_as_list(args.get("automation_keys")))
    keys.update(state.get("automation_keys") or ())
    keys.update(k for k in (arr.access_key_id, arr.state_access_key_id) if k)
    return keys


def _under(key, prefixes):
    """The prefix the key is under, "" when none."""
    for p in prefixes:
        if p and key.startswith(p):
            return p
    return ""


def _s3_event(arr, rec, log_key, rules):
    params = rec.get("requestParameters") or {}
    identity = rec.get("userIdentity") or {}
    # responseElements is null on an error and may appear twice on a
    # success; json.loads keeps the last, which carries the version id
    response = rec.get("responseElements") or {}
    bucket = rec.get("bucketName") or params.get("bucketName") or ""
    key = str(params.get("key") or "")
    name = str(rec.get("eventName") or "")
    error_code = str(rec.get("errorCode") or "")
    ok = not error_code
    access_key = str(identity.get("accessKeyId") or "")
    base = key.rsplit("/", 1)[-1]
    landing_prefix = _under(key, rules.landing_prefixes)
    landing = bool(
        landing_prefix and name in WRITE_EVENTS and ok and base in rules.landing_markers
    )
    dataset = (
        key[len(landing_prefix) : len(key) - len(base)].strip("/") if landing else ""
    )
    in_log_bucket = bucket == arr.log_bucket
    approval = bool(
        name == "PutObject"
        and ok
        and in_log_bucket
        and key.startswith(rules.approval_prefix)
    )
    request = bool(
        name == "PutObject"
        and ok
        and in_log_bucket
        and key.startswith(rules.request_prefix)
    )
    event_time = str(rec.get("eventTime") or "")
    return {
        "purefb_s3_event": {
            "array": arr.name,
            "bucket": bucket,
            "key": key,
            "event_name": name,
            "event_time": event_time,
            "event_time_ms": _event_time_ms(event_time),
            "event_id": rec.get("eventId"),
            "read_only": rec.get("readOnly"),
            "access_key_id": access_key,
            "user": identity.get("userName") or "",
            "account": rec.get("recipientAccountId") or "",
            "source_ip": rec.get("sourceIPAddress") or "",
            "user_agent": rec.get("userAgent") or "",
            "error_code": error_code,
            "error_message": str(rec.get("errorMessage") or ""),
            "ok": ok,
            "version_id": str(response.get("x-amz-version-id") or ""),
            "log_object": log_key,
            "by_automation": access_key in rules.automation,
            "canary": bool(
                ok and name in READ_EVENTS and _under(key, rules.canary_prefixes)
            ),
            "canary_allowed": access_key in rules.canary_readers,
            "landing": landing,
            "dataset": dataset,
            "evidence": bool(
                ok and name in WRITE_EVENTS and _under(key, rules.evidence_prefixes)
            ),
            "honey": bool(key) and key.endswith(rules.honey_suffixes),
            "approval": approval,
            "request": request,
            "request_id": base.rsplit(".", 1)[0] if (approval or request) else "",
            "denied": error_code in DENIED_CODES,
            "delete": name in DELETE_EVENTS,
        }
    }


def _counts(rows, field):
    out = {}
    for r in rows:
        v = r.get(field)
        if v:
            out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


def _top(counter):
    """The most frequent key and its count, the lexically first on a tie."""
    if not counter:
        return "", 0
    best = max(sorted(counter), key=counter.get)
    return best, counter[best]


def _rollup(arr, log_key, window, rows, bad_lines, rules):
    timed = [r for r in rows if r.get("event_time_ms") is not None]
    first = min(timed, key=lambda r: r["event_time_ms"]) if timed else None
    last = max(timed, key=lambda r: r["event_time_ms"]) if timed else None
    count = len(rows)
    error_count = sum(1 for r in rows if not r["ok"])
    error_pct = round(100.0 * error_count / count, 1) if count else 0.0
    denied_count = sum(1 for r in rows if r["denied"])
    delete_count = sum(1 for r in rows if r["delete"])
    by_access_key = _counts(rows, "access_key_id")
    by_source_ip = _counts(rows, "source_ip")
    top_key, top_key_count = _top(by_access_key)
    return {
        "purefb_s3_rollup": {
            "array": arr.name,
            "log_object": log_key,
            "window": _window_iso(window),
            "record_count": count,
            "first_time": first["event_time"] if first else "",
            "last_time": last["event_time"] if last else "",
            "buckets": sorted({r["bucket"] for r in rows if r["bucket"]}),
            "by_event_name": _counts(rows, "event_name"),
            "by_access_key": by_access_key,
            "by_source_ip": by_source_ip,
            "by_error_code": _counts(rows, "error_code"),
            "error_count": error_count,
            "error_pct": error_pct,
            "denied_count": denied_count,
            "delete_count": delete_count,
            "top_access_key": top_key,
            "top_access_key_count": top_key_count,
            "top_source_ip": _top(by_source_ip)[0],
            "error_storm": bool(
                error_count >= rules.error_min_count
                and error_pct >= rules.error_rate_pct
            ),
            "denied_storm": denied_count >= rules.denied_storm_count,
            "mass_delete": delete_count >= rules.mass_delete_count,
            "canary_hits": sum(1 for r in rows if r["canary"]),
            "honey_hits": sum(1 for r in rows if r["honey"]),
            "bad_lines": bad_lines,
        }
    }


# --------------------------------------------------------------------------
# REST reads (with a token) and the health event
# --------------------------------------------------------------------------
def _rest_facts(arr):
    """What the array says about its own audit setup: the id that prefixes
    the log keys, the log target's prefix and rotation, the audit policies
    with their members, the live buckets (space, quota, object count) and
    the audit filters of each member bucket."""
    c = arr.client
    array = _first(_items(c.get_arrays(), "arrays"))
    targets = _items(c.get_log_targets_object_store(), "log-targets/object-store")
    target = _first(
        [t for t in targets if _name(t.get("bucket")) == arr.log_bucket] or targets
    )
    policies = _items(
        c.get_audit_object_store_policies(), "audit-object-store-policies"
    )
    members = []
    if policies:
        members = _items(
            c.get_audit_object_store_policies_members(
                policy_names=[p.get("name") for p in policies]
            ),
            "audit-object-store-policies/members",
        )
    by_policy = {}
    for m in members:
        by_policy.setdefault(_name(m.get("policy")), set()).add(_name(m.get("member")))
    buckets = _items(c.get_buckets(), "buckets")
    live = {b.get("name"): b for b in buckets if not b.get("destroyed")}
    # a filter read per member: one destroyed or renamed member would fail
    # a bulk call, and the members are few
    filters = {}
    for bucket in sorted(set().union(*by_policy.values()) if by_policy else ()):
        if bucket in live:
            rows = _items(
                c.get_buckets_audit_filters(bucket_names=[bucket]),
                f"buckets/audit-filters {bucket}",
            )
            filters[bucket] = sorted(f.get("name") or "" for f in rows)
    return {
        "array_id": array.get("id") or "",
        "log_name_prefix": (target.get("log_name_prefix") or {}).get("prefix") or "",
        "rotation_ms": (target.get("log_rotate") or {}).get("duration"),
        "policies": [
            {
                "name": p.get("name"),
                "enabled": bool(p.get("enabled")),
                "log_targets": sorted(
                    _name(t) or "" for t in (p.get("log_targets") or [])
                ),
                "members": sorted(by_policy.get(p.get("name"), ())),
            }
            for p in policies
        ],
        "buckets": live,
        "filters": filters,
    }


def _health_event(arr, st, rest, args, prefix, rotation, counts, persisted, now):
    objects, records, bad = counts
    log_bucket = (rest["buckets"].get(arr.log_bucket) if rest else None) or {}
    used = (log_bucket.get("space") or {}).get("virtual") or 0
    quota = log_bucket.get("quota_limit") or 0
    used_pct = round(100.0 * used / quota, 1) if quota else 0.0
    warn = float(args.get("log_bucket_quota_warn_pct", 80))
    policies = rest["policies"] if rest else []
    disabled = [p for p in policies if not p["enabled"]]
    covered = {m for p in policies if p["enabled"] for m in p["members"]}
    # as purefb_events: None means the default, an explicit [] excludes nothing
    exclude = (
        ["eda-state"]
        if args.get("coverage_exclude") is None
        else _as_list(args.get("coverage_exclude"))
    )
    uncovered = sorted(
        b
        for b in (rest["buckets"] if rest else {})
        if b not in covered and not any(fnmatch.fnmatchcase(b, g) for g in exclude)
    )
    without_filters = sorted(
        b for b, names in (rest["filters"] if rest else {}).items() if not names
    )
    newest_ms = st.get("last_key_time_ms")
    age = round((now - newest_ms) / 1000) if st.get("last_key") and newest_ms else -1
    stale = age > int(args.get("stale_rotations", 2)) * rotation / 1000
    return {
        "purefb_audit_health": {
            "array": arr.name,
            "log_bucket": arr.log_bucket,
            "log_key_prefix": prefix,
            "objects_this_poll": objects,
            "records_this_poll": records,
            "bad_lines_this_poll": bad,
            "newest_log_object": st.get("last_key") or "",
            "newest_log_age_s": age,
            "rotation_ms": rotation,
            "stale": stale,
            "log_bucket_used_bytes": used,
            "log_bucket_quota_bytes": quota,
            "log_bucket_used_pct": used_pct,
            "log_bucket_quota_known": bool(quota),
            "log_bucket_object_count": log_bucket.get("object_count") or 0,
            "policies": policies,
            "policies_enabled": len(policies) - len(disabled),
            "policies_disabled": len(disabled),
            "uncovered_buckets": uncovered,
            "uncovered_count": len(uncovered),
            "members_without_filters": without_filters,
            "members_without_filters_count": len(without_filters),
            "rest_available": rest is not None,
            "cursor_persisted": persisted,
            "healthy": bool(
                not stale
                and not (quota and used_pct >= warn)
                and not uncovered
                and not disabled
                and not without_filters
            ),
        }
    }


# --------------------------------------------------------------------------
# one poll of one array: synchronous, run in a worker thread
# --------------------------------------------------------------------------
def poll_object_audit(arr, state, args):
    st = _array_state(state, arr, args)
    now = _now_ms()
    rest = _rest_facts(arr) if arr.has_rest else None
    rotation = int(
        args.get("rotation_ms")
        or (rest or {}).get("rotation_ms")
        or DEFAULT_ROTATION_MS
    )
    prefix = _log_prefix(arr, st, rest)
    late = int(args.get("late_windows", 2))
    replay = int(args.get("replay_objects", 0) or 0)
    first = not st["primed"]
    # the floor: the oldest window still re-listed for late authorities, or
    # the floor the last run persisted when that is older (a restart after
    # a gap lists everything the gap holds)
    time_floor = _window_path(now - late * rotation, rotation)
    floor = min(time_floor, st["floor"]) if st.get("floor") else time_floor
    s3 = arr.s3
    if first and replay > 0:
        listed = _list_log_objects(s3, arr.log_bucket, prefix, None)
    else:
        # without a prefix the floor key would sort among the array ids, so
        # the window check alone decides and the whole bucket is listed
        floor_key = prefix + floor if prefix else None
        listed = _list_log_objects(s3, arr.log_bucket, prefix, floor_key)
        if first and not listed:
            # an idle array: find the newest object once so the health event
            # can say how old the log is; nothing before the floor is read
            listed = _list_log_objects(s3, arr.log_bucket, prefix, None)[-1:]
    fresh = [
        o
        for o in listed
        if o.key not in st["seen"] and (o.window >= floor or (first and replay > 0))
    ]
    if first:
        fresh = fresh[-replay:] if replay > 0 else []

    rules = Rules(args, _automation_keys(arr, state, args))
    events, records, bad_lines = [], 0, 0
    for obj in fresh:
        rows, bad = _read_log_object(s3, arr.log_bucket, obj.key)
        parsed = [_s3_event(arr, r, obj.key, rules) for r in rows]
        parsed.sort(key=lambda e: e["purefb_s3_event"]["event_time_ms"] or 0)
        events.extend(parsed)
        flat = [e["purefb_s3_event"] for e in parsed]
        events.append(_rollup(arr, obj.key, obj.window, flat, bad, rules))
        records += len(rows)
        bad_lines += bad

    st["seen"].update(o.key for o in listed)
    if listed:
        newest = max(listed, key=lambda o: o.key)
        if st["last_key"] is None or newest.key > st["last_key"]:
            st["last_key"] = newest.key
            st["last_key_time_ms"] = newest.modified_ms or (
                _window_ms(newest.window) + rotation
            )
    st["floor"] = time_floor
    st["seen"] = {k for k in st["seen"] if (_log_window(k) or "") >= time_floor}
    st["primed"] = True
    persisted = _save_cursor(arr, st, args)
    counts = (len(fresh), records, bad_lines)
    events.append(
        _health_event(arr, st, rest, args, prefix, rotation, counts, persisted, now)
    )
    return events


# --------------------------------------------------------------------------
# asyncio driver
# --------------------------------------------------------------------------
async def _poll_loop(queue, arrays, state, args, executor):
    loop = asyncio.get_running_loop()
    interval = float(args.get("interval") or 60)
    escalate_after = int(args.get("poll_error_escalate", 10) or 10)
    # the default automation set: every array's tail and state keys
    state.setdefault("automation_keys", set()).update(
        k for a in arrays for k in (a.access_key_id, a.state_access_key_id) if k
    )

    def run_sync(arr):
        try:
            return poll_object_audit(arr, state, args)
        except Exception:
            arr.reset()
            raise

    async def run(arr):
        # one poll of an array at a time under its lock, so reset() never
        # races a request in flight
        async with arr.lock:
            return await loop.run_in_executor(executor, run_sync, arr)

    def failed(arr, exc):
        count = _poll_streak(state, SIGNAL, arr.name, True)
        logger.warning(
            "%s: %s poll failed (%d in a row): %s", PLUGIN, arr.name, count, exc
        )
        return [_poll_error(SIGNAL, arr.name, exc, count, count >= escalate_after)]

    logger.info("%s: tailing %s every %ss", PLUGIN, [a.name for a in arrays], interval)
    while True:
        started = time.monotonic()
        for arr in arrays:
            try:
                events = await run(arr)
                _poll_streak(state, SIGNAL, arr.name, False)
            except Exception as exc:  # noqa: BLE001 - report and keep polling
                events = failed(arr, exc)
            for event in events:
                await queue.put(clean(event))
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(1.0, interval - elapsed))


async def main(queue: asyncio.Queue, args: dict) -> None:
    verify = _truthy(args.get("verify_ssl"), False)
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    specs = _decode_arrays(args.get("arrays"), PLUGIN) or []
    if isinstance(specs, dict):
        specs = [dict(name=k, **v) for k, v in specs.items()]
    request_timeout = float(args.get("request_timeout", 30) or 30)
    arrays = [Array(spec, verify, request_timeout) for spec in specs]
    if not arrays:
        raise ValueError(f"{PLUGIN}: at least one array is required")
    state: dict = {}
    # one worker per array lock plus one, so no poll waits on a thread
    executor = ThreadPoolExecutor(max_workers=len(arrays) + 1)
    task = asyncio.create_task(_poll_loop(queue, arrays, state, args, executor))
    try:
        await task
    finally:
        task.cancel()
        executor.shutdown(wait=False)


if __name__ == "__main__":
    # Manual test: python purefb_object_audit.py <name> <endpoint>
    #   <ACCESS_KEY_ENV> <SECRET_KEY_ENV> [log_bucket] [interval]
    import sys

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    class _Q:
        async def put(self, event):
            print(json.dumps(event, default=str), flush=True)

    asyncio.run(
        main(
            _Q(),
            {
                "arrays": [
                    {
                        "name": sys.argv[1],
                        "s3_endpoint": sys.argv[2],
                        "s3_access_key_env": sys.argv[3],
                        "s3_secret_key_env": sys.argv[4],
                        "log_bucket": sys.argv[5] if len(sys.argv) > 5 else None,
                    }
                ],
                "interval": float(sys.argv[6]) if len(sys.argv) > 6 else 30,
                # a hand run shows the last window straight away
                "replay_objects": 12,
            },
        )
    )
