# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""
purefb_events.py - ansible-rulebook event source for Everpure FlashBlade.

Polls one or more FlashBlades over REST (py-pure-client) and emits plain
fact events.  Thresholds, baselines and policy live in the rulebook, not here.
One instance can run several signals, each on its own interval.

Written against Purity//FB 4.6.4 (REST 2.21) with py-pure-client 1.98.0.
Every call it makes exists from REST 2.16 except the per-protocol client
performance (2.21), and the calls an older array lacks (maintenance windows,
SAML identity providers, Active Directory) are skipped rather than failing
the signal.

Arguments
---------
arrays            list of arrays (required). Each entry:
                    name          event `array` field and inventory host name
                    fb_url        https://<management address>, or
                    fb_url_env    name of an environment variable holding it
                    api_token     API token, or
                    api_token_env name of an environment variable holding it
                  A JSON string (or a YAML string) that decodes to that list
                  is accepted too, so a platform credential can inject the
                  list as one variable; it is decoded before validation and
                  never echoed into an error or a log.
signals           list or comma-separated string, default: all of
                  alerts | audits | sessions | health | telemetry | config.
                  replication and posture are reserved for a later release
                  and are rejected with an error that says so.
interval          default seconds between polls (default 60)
<signal>_interval per-signal override, e.g. audits_interval: 15
verify_ssl        verify the array certificate (default false)
request_timeout   seconds to wait for each REST response (default 30; the
                  connect timeout is fixed at 10). A hung connection then
                  fails that poll with purefb_poll_error instead of holding
                  the array's lock for ever. Keep it well under the shortest
                  signal interval: the signals on one array are serialised
                  and each signal polls its arrays in turn, so one cycle can
                  take arrays x (10 + request_timeout) seconds at worst.
automation_users  list or comma-separated string of user names whose audit
                  records count as automation (default: empty). Audit events
                  carry `by_automation`.
automation_user_agents
                  user-agent prefixes that mark an audit record as made by a
                  program (default: pure/py-pure-client). Audit events carry
                  `automation_agent`, which tells a script from a person even
                  when the user list is incomplete.
destructive_subcommands
                  words in an audit subcommand that make it destructive
                  (default: destroy, eradicate, delete, remove, disconnect,
                  demote). Audit events carry `destructive`.
audit_batch       audits and sessions: records fetched per page (default
                  200). Up to 10 pages are read when more than that arrived
                  between polls; beyond that the oldest are skipped and a
                  purefb_poll_error (signal audits, "audit_batch overflow")
                  says so.
audit_lookback    seconds of audit history to replay at start-up (default 0)
include_initial_alerts
                  emit alerts that are already open at start-up with
                  transition "initial" (default true)
poll_error_escalate
                  consecutive failed polls of one signal on one array after
                  which purefb_poll_error carries escalate: true (default 10)
hardware_ignore_status
                  hardware statuses that are not issues
                  (default: healthy, not_installed, unused, unclaimed; an
                  empty DFM bay on a //S reports unclaimed)
blade_ignore_status
                  blade statuses that are not issues (default: healthy,
                  unused)
drive_ignore_status
                  drive statuses that are not issues (default: healthy,
                  unused, empty, evacuated, unclaimed)
fs_quota_warn_pct telemetry: a file system whose virtual space is at or
                  above this share of its provisioned size gets a
                  purefb_fs_quota event (default 80)
top_clients       telemetry: how many clients to fetch per protocol per
                  poll, busiest writers first (default 10)
top_file_systems  telemetry: how many file systems to fetch per poll,
                  busiest writers first (default 10). File systems whose QoS
                  policy is named eda-cap-* are always reported, whatever
                  their rank, so the lift rule keeps seeing them.
top_buckets       telemetry: the same for buckets (default 10)
top_growers       telemetry: how many fastest-growing file systems and
                  buckets to report on the capacity event (default 5)
client_min_share_pct
                  telemetry: a client, file system or bucket doing at least
                  this share of the array's operations counts a high-share
                  poll; below it counts a normal poll (default 40)
baseline          telemetry: also fetch the same half hour one week ago
                  from the array's own history (default true)
drr_drop_threshold_pct
                  telemetry: data reduction this much below a week ago sets
                  drr_drop (default 10)
snapshot_growth_threshold_pct
                  telemetry: snapshot space this much above a week ago sets
                  snapshot_surge (default 25)
write_surge_ratio telemetry: write bandwidth this many times last week's
                  sets write_surge (default 3)

Events (one dict per event, exactly one top-level key, always with `array`;
alert, audit, session, health, hardware, lag and config events also carry
`maintenance_active`, true while the array has an open maintenance window
and false on an array without the endpoint, REST before 2.16)
-------------------------------------------------------------------------
purefb_alert            transition: initial|opened|updated|closing|closed
                        (closing: the array parked a resolved alert before
                        it closes; only severity, flag or summary changes
                        count as updated, `updated` alone does not);
                        alert_id, code, severity, state, component_type,
                        component_name, summary, description, action,
                        knowledge_base_url, flagged, created, updated,
                        notified, variables, age_s, resolved_after_s (closed
                        only), previous_severity and severity_raised
                        (updated only)
purefb_audit            audit_id, time, user, user_agent, ip_address,
                        user_interface, origin, command, subcommand,
                        arguments, by_automation, automation_agent,
                        destructive
purefb_session          session_id, event, event_count, user, location,
                        method, user_interface, start_time, end_time
purefb_health           hardware_issues[{component,type,status,details}],
                        hardware_issue_count,
                        blade_issues[{blade,status,progress,target,details}],
                        blade_issue_count,
                        drive_issues[{drive,status,type,details}],
                        drive_issue_count, lags[{lag,status,lag_speed,
                        expected_speed,port_count,degraded}], lag_degraded,
                        blade_count, drive_count, maintenance_active,
                        maintenance_windows[{name,created,expires}],
                        remote_assist_active, phonehome_enabled,
                        open_alert_count, purity_version
purefb_hardware         one per hardware component whose status is an issue:
                        component, type, status, details, slot, serial,
                        part_number, identify_enabled, neighbor_switch,
                        neighbor_switch_address, neighbor_port (the LLDP
                        neighbour of an Ethernet port, empty strings when
                        unknown)
purefb_hardware_recovered
                        component, type, status, previous_status: a
                        component that left the issue set since the last poll
purefb_lag              when a link aggregation group becomes degraded
                        (status not healthy, or lag_speed below the sum of
                        its member port speeds) or recovers: lag, status,
                        lag_speed, expected_speed, ports[{name,
                        neighbor_switch,neighbor_port}], degraded, recovered
purefb_capacity         time, capacity_bytes, used_bytes, used_pct,
                        data_reduction, snapshot_bytes, destroyed_bytes,
                        destroyed_virtual_bytes, total_provisioned_bytes,
                        growth_bytes_per_day, days_to_full,
                        days_to_full_known, file_system_bytes,
                        object_store_bytes, file_system_growth_bytes_per_day,
                        object_store_growth_bytes_per_day,
                        pending_eradication_file_systems,
                        pending_eradication_buckets,
                        pending_eradication_snapshots,
                        top_growers[{name,kind,delta_bytes,total_physical}],
                        drr_week_ago, drr_drop_pct, drr_drop,
                        snapshot_growth_pct, snapshot_surge,
                        others[{array,used_pct}] (the other polled arrays,
                        least used first)
purefb_fs_quota         one per file system at or above fs_quota_warn_pct of
                        its provisioned size: file_system, provisioned,
                        used_bytes, used_pct, hard_limit_enabled,
                        snapshot_bytes, qos_policy, destroyed
purefb_array_performance
                        time, usec_per_read_op, usec_per_write_op,
                        usec_per_other_op, reads_per_sec, writes_per_sec,
                        others_per_sec, read_bytes_per_sec,
                        write_bytes_per_sec, nfs_metadata_modifies_per_sec,
                        nfs_metadata_reads_per_sec; when the baseline fetch
                        succeeds also baseline_<field> for the same half
                        hour last week, read_latency_ratio,
                        write_latency_ratio, write_bw_ratio; always
                        baseline_known, write_surge
purefb_client_performance
                        one per client row (named <address>:<port> by the
                        array; the address is the inventory key): client,
                        client_port, protocol, reads_per_sec,
                        writes_per_sec, others_per_sec, read_bytes_per_sec,
                        write_bytes_per_sec, usec_per_read_op,
                        usec_per_write_op, usec_per_other_op, ops_share_pct,
                        high_share_polls, normal_polls,
                        array_usec_per_write_op, array_write_latency_ratio,
                        baseline_known
purefb_fs_performance   file_system, the same performance fields,
                        ops_share_pct, qos_policy, eda_capped (the QoS policy
                        is named eda-cap-*, set by the QoS playbook),
                        high_share_polls, normal_polls
purefb_bucket_performance
                        bucket, the same shape as purefb_fs_performance
purefb_config           purity_version, ntp_servers_csv, time_zone,
                        dns_domain, dns_nameservers_csv,
                        ds_management_enabled, ds_management_uris_csv,
                        ds_management_base_dn, ds_management_bind_user,
                        ds_nfs_enabled, ad_joined, manual_eradication,
                        eradication_delay_ms, lockout_duration_ms,
                        max_login_attempts, min_password_length,
                        sso_enabled, syslog_servers_csv,
                        syslog_data_audit_servers_csv, smtp_relay_host,
                        smtp_sender_domain, alert_watchers_enabled_csv,
                        phonehome_enabled, remote_assist_status, banner_set,
                        default_inbound_tls_policy, data_at_rest_encryption,
                        smb_mode, network_access_policy (csv fields are
                        sorted and comma-joined)
purefb_poll_error       signal, error, consecutive (failed polls in a row on
                        that array and signal; 0 for a poll that succeeded
                        but dropped records), escalate (one per failed poll;
                        the audits signal also raises it on audit_batch
                        overflow)
"""

DOCUMENTATION = r"""
---
name: purefb_events
short_description: Poll Everpure FlashBlades over REST and emit fact events.
description:
  - Polls one or more FlashBlades with py-pure-client and emits plain fact
    events (C(purefb_alert), C(purefb_audit), C(purefb_session),
    C(purefb_health), C(purefb_hardware), C(purefb_lag), C(purefb_capacity),
    C(purefb_fs_quota), C(purefb_client_performance), C(purefb_config) and
    others). Thresholds and policy live in the rulebook. One instance runs
    several signals, each on its own interval. The module docstring lists
    every event and its fields.
options:
  arrays:
    description:
      - Arrays to poll. Each entry has C(name) (the event's array field and
        inventory host name), C(fb_url) or C(fb_url_env), and C(api_token) or
        C(api_token_env); the C(_env) forms name environment variables.
      - A JSON string is accepted so a platform credential can inject the
        list as one extra var (a YAML string decodes the same way); the
        string is decoded before validation and is never written to a log.
    type: list
    elements: dict
    required: true
  signals:
    description:
      - Signals to run (list or comma-separated string). C(replication) and
        C(posture) are reserved for a later release and are rejected.
    type: list
    elements: str
    choices: [alerts, audits, sessions, health, telemetry, config]
    default: [alerts, audits, sessions, health, telemetry, config]
  interval:
    description: Default seconds between polls.
    type: int
    default: 60
  alerts_interval:
    description: Poll interval for the alerts signal; overrides I(interval).
    type: int
  audits_interval:
    description: Poll interval for the audits signal; overrides I(interval).
    type: int
  sessions_interval:
    description: Poll interval for the sessions signal; overrides I(interval).
    type: int
  health_interval:
    description: Poll interval for the health signal; overrides I(interval).
    type: int
  telemetry_interval:
    description: Poll interval for the telemetry signal; overrides I(interval).
    type: int
  config_interval:
    description: Poll interval for the config signal; overrides I(interval).
    type: int
  verify_ssl:
    description: Verify the array certificate.
    type: bool
    default: false
  request_timeout:
    description:
      - Seconds to wait for each REST response; the connect timeout is fixed
        at 10. Keep it well under the shortest signal interval.
    type: int
    default: 30
  automation_users:
    description: User names whose audit records count as automation (list or comma-separated string).
    type: list
    elements: str
    default: []
  automation_user_agents:
    description: User-agent prefixes that mark an audit record as made by a program.
    type: list
    elements: str
    default: [pure/py-pure-client]
  destructive_subcommands:
    description: Words in an audit subcommand that make the record destructive.
    type: list
    elements: str
    default: [destroy, eradicate, delete, remove, disconnect, demote]
  audit_batch:
    description:
      - Audit and session records fetched per page; up to 10 pages are read
        per poll, then a purefb_poll_error reports the overflow.
    type: int
    default: 200
  audit_lookback:
    description: Seconds of audit history to replay at start-up.
    type: int
    default: 0
  include_initial_alerts:
    description: Emit alerts already open at start-up with transition initial.
    type: bool
    default: true
  poll_error_escalate:
    description: Consecutive failed polls of one signal on one array after which purefb_poll_error carries escalate true.
    type: int
    default: 10
  hardware_ignore_status:
    description: Hardware statuses that are not issues.
    type: list
    elements: str
    default: [healthy, not_installed, unused, unclaimed]
  blade_ignore_status:
    description: Blade statuses that are not issues.
    type: list
    elements: str
    default: [healthy, unused]
  drive_ignore_status:
    description: Drive statuses that are not issues.
    type: list
    elements: str
    default: [healthy, unused, empty, evacuated, unclaimed]
  fs_quota_warn_pct:
    description: A file system at or above this share of its provisioned size gets a purefb_fs_quota event.
    type: float
    default: 80
  top_clients:
    description: Clients fetched per protocol per poll, busiest writers first.
    type: int
    default: 10
  top_file_systems:
    description: File systems fetched per poll, busiest writers first (capped ones are always reported).
    type: int
    default: 10
  top_buckets:
    description: Buckets fetched per poll, busiest writers first (capped ones are always reported).
    type: int
    default: 10
  top_growers:
    description: Fastest-growing file systems and buckets reported on the capacity event.
    type: int
    default: 5
  client_min_share_pct:
    description: Share of the array's operations at or above which a client, file system or bucket counts a high-share poll.
    type: float
    default: 40
  baseline:
    description: Also fetch the same half hour one week ago from the array's history.
    type: bool
    default: true
  drr_drop_threshold_pct:
    description: Data reduction this much below a week ago sets drr_drop.
    type: float
    default: 10
  snapshot_growth_threshold_pct:
    description: Snapshot space this much above a week ago sets snapshot_surge.
    type: float
    default: 25
  write_surge_ratio:
    description: Write bandwidth this many times last week's sets write_surge.
    type: float
    default: 3
"""

EXAMPLES = r"""
- name: FlashBlade alerts and health
  hosts: all
  sources:
    - sdodsley.flashblade_eda.purefb_events:
        arrays: "{{ purefb_arrays }}"
        signals: [alerts, health]
        alerts_interval: 60
        health_interval: 300
        automation_users: "{{ automation_users }}"
  rules:
    - name: Critical alert opened
      condition: >-
        event.purefb_alert.transition == "opened" and
        event.purefb_alert.severity == "critical"
      action:
        debug:
          msg: "{{ event.purefb_alert.array }}: {{ event.purefb_alert.summary }}"
"""

import asyncio
import inspect
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

import urllib3
from pypureclient import flashblade

try:
    import yaml
except ImportError:  # ansible-rulebook ships PyYAML; a bare interpreter may not
    yaml = None

logger = logging.getLogger(__name__)

SIGNALS = ("alerts", "audits", "sessions", "health", "telemetry", "config")
# signals a later release adds: named so the error says "later", not "unknown"
RESERVED_SIGNALS = ("replication", "posture")
DAY_MS = 86_400_000
HALF_HOUR_MS = 1_800_000
CONNECT_TIMEOUT = 10  # seconds to open the connection; the read timeout is an argument
AUDIT_MAX_PAGES = (
    10  # pages of audit_batch rows read per poll before reporting overflow
)
ALERTS_UNFILTERED_LIMIT = 1000  # newest alerts read when the state filter is rejected
GROWTH_OBJECTS = 500  # largest file systems and buckets whose growth is tracked
OBJECTS_MAX_AGE = 30.0  # seconds the capacity step's object lists serve the other steps
USER_AGENT = "flashblade-eda/purefb_events"
EDA_CAP_PREFIX = (
    "eda-cap-"  # QoS policies the QoS playbook creates; FlashBlade has no tags
)

PERF_FIELDS = (
    "usec_per_read_op",
    "usec_per_write_op",
    "usec_per_other_op",
    "reads_per_sec",
    "writes_per_sec",
    "others_per_sec",
    "read_bytes_per_sec",
    "write_bytes_per_sec",
)


# --------------------------------------------------------------------------
# helpers
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
    a log record: it carries API tokens.
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
        "hold API tokens"
    )


def _truthy(value, default=False):
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _csv(values):
    return ",".join(sorted(str(v) for v in (values or [])))


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


def _status(resp):
    return getattr(resp, "status_code", None)


def _total(resp):
    return getattr(resp, "total_item_count", None)


def _first(items, default=None):
    return items[0] if items else (default if default is not None else {})


def _name(ref):
    if isinstance(ref, dict):
        return ref.get("name")
    return ref


def _avg(values):
    vals = [v for v in values if isinstance(v, (int, float))]
    return round(sum(vals) / len(vals), 1) if vals else None


def _ratio(now, base):
    if isinstance(now, (int, float)) and isinstance(base, (int, float)) and base > 0:
        return round(now / base, 2)
    return None


def _pct(part, whole, digits=1):
    """part as a percentage of whole, clamped to 100 (two samples are never
    taken at the same instant); None without a whole."""
    if not whole:
        return None
    return min(100.0, round(100.0 * (part or 0) / whole, digits))


def _now_ms():
    return int(time.time() * 1000)


def _accepts(fn, param):
    """Whether a callable takes `param`: the SDK adds parameters by REST
    version (the client's `timeout`, the per-protocol client performance),
    so the call is shaped to what the installed client and the negotiated
    version offer. Anything that cannot be inspected is taken to accept it."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return True
    return param in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


def _optional_items(c, method, what, **kw):
    """Rows from a call the array may not offer at its REST version: an
    empty list when the negotiated client lacks the method or the call
    fails. Only for reads whose absence has a safe meaning (no identity
    provider, no domain join); the mandatory reads raise through _items."""
    fn = getattr(c, method, None)
    if fn is None:
        return []
    try:
        return _items(fn(**kw), what)
    except Exception as exc:  # noqa: BLE001 - optional read
        logger.debug("purefb_events: %s unavailable: %s", what, exc)
        return []


_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


def _severity_raised(previous, current):
    """True when an alert's severity went up between two polls; nothing is a
    rise from no previous reading or an unknown level."""
    before = _SEVERITY_RANK.get(str(previous)) if previous is not None else None
    after = _SEVERITY_RANK.get(str(current))
    return before is not None and after is not None and after > before


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
    """Consecutive failed polls of one signal on one array: the count after
    this poll, 0 after a success. Kept in the shared state so a rule can
    tell a blip from an array nobody has reached for an hour."""
    streaks = state.setdefault("poll_errors", {}).setdefault(signal, {})
    streaks[array] = streaks.get(array, 0) + 1 if failed else 0
    return streaks[array]


def _maintenance(arr, state, max_age=20.0):
    """Active maintenance windows on the array, cached briefly across signals.

    The alert, audit, session, health and config signals stamp
    `maintenance_active` on their events so a rule can suppress remediation
    while someone has declared work on the array, without cross-signal
    facts in the rulebook. FlashBlade has one array-wide window (REST 2.16,
    Purity//FB 4.5.2); an older array has no endpoint, so the client built
    for its version lacks the method and the flag is simply false.
    """
    cache = state.setdefault("maintenance", {}).setdefault(
        arr.name, {"at": 0, "windows": []}
    )
    if time.monotonic() - cache["at"] > max_age:
        c = arr.client
        if hasattr(c, "get_maintenance_windows"):
            cache["windows"] = _items(
                c.get_maintenance_windows(), "maintenance windows"
            )
        elif not cache.get("unsupported"):
            logger.info(
                "purefb_events: %s has no maintenance-windows endpoint (REST before "
                "2.16); maintenance_active stays false",
                arr.name,
            )
            cache["unsupported"] = True
        cache["at"] = time.monotonic()
    return cache["windows"]


def _rows_since(fetch, cursor, batch, time_key, what):
    """Newest-first pages of a time-ordered log, cut locally at `cursor`.

    Neither /audits nor /sessions is relied on to filter by time, so pages
    sorted by the time field descending are read until one crosses the
    cursor or the log ends. Returns the rows oldest first (ids are
    increasing integers and break timestamp ties) and whether pages were
    left unread."""
    rows, page, overflow = [], 0, False
    while True:
        chunk = _items(
            fetch(limit=batch, offset=page * batch, sort=[time_key + "-"]), what
        )
        fresh = [r for r in chunk if (r.get(time_key) or 0) >= cursor]
        rows.extend(fresh)
        page += 1
        if len(fresh) < len(chunk) or len(chunk) < batch:
            break  # crossed the cursor, or reached the end of the log
        if page >= AUDIT_MAX_PAGES:
            overflow = True  # the oldest new records stay unread
            break
    # a record written between two page fetches shifts the pages by one row;
    # keep each id once
    rows = list({r.get("name"): r for r in rows}.values())
    rows.sort(
        key=lambda r: (
            r.get(time_key) or 0,
            int(r["name"]) if str(r.get("name", "")).isdigit() else 0,
        )
    )
    return rows, overflow


def _advance_cursor(st, rows, time_key):
    """The rows not yet emitted, oldest first; moves the cursor to the newest
    time seen and remembers the ids at that time so a repeat poll skips
    them (several records can share one timestamp)."""
    out = []
    newest, ids_at_newest = st["cursor"], set(st["ids"])
    for r in rows:
        if r.get("name") in st["ids"] and r.get(time_key) == st["cursor"]:
            continue
        out.append(r)
        t = r.get(time_key) or newest
        if t > newest:
            newest, ids_at_newest = t, {r.get("name")}
        elif t == newest:
            ids_at_newest.add(r.get("name"))
    st["cursor"], st["ids"] = newest, ids_at_newest
    return out


# --------------------------------------------------------------------------
# array handle: one SDK client per array, rebuilt after a failure
# --------------------------------------------------------------------------
class Array:
    def __init__(self, spec, verify_ssl, request_timeout=30):
        self.name = spec["name"]
        url = spec.get("fb_url") or spec.get("url")
        if not url and spec.get("fb_url_env"):
            url = os.environ.get(spec["fb_url_env"])
        if not url:
            raise ValueError(f"array {self.name}: fb_url or fb_url_env is required")
        self.host = url.replace("https://", "").replace("http://", "").strip("/")
        token = spec.get("api_token")
        if not token and spec.get("api_token_env"):
            token = os.environ.get(spec["api_token_env"])
        if not token:
            raise ValueError(
                f"array {self.name}: api_token or api_token_env is required"
            )
        self._token = token
        self._verify = verify_ssl
        self._timeout = (CONNECT_TIMEOUT, float(request_timeout))
        self._client = None
        self.lock = asyncio.Lock()

    @property
    def client(self):
        if self._client is None:
            # the timeout also bounds the version probe Client() makes, so an
            # unreachable array fails fast here instead of hanging the worker;
            # older py-pure-client releases take neither it nor user_agent
            kwargs = {"api_token": self._token, "verify_ssl": self._verify}
            if _accepts(flashblade.Client, "timeout"):
                kwargs["timeout"] = self._timeout
            if _accepts(flashblade.Client, "user_agent"):
                kwargs["user_agent"] = USER_AGENT
            self._client = flashblade.Client(target=self.host, **kwargs)
        return self._client

    def reset(self):
        self._client = None


# --------------------------------------------------------------------------
# signal pollers: synchronous, run in a worker thread; return a list of events
# --------------------------------------------------------------------------
def _active_alerts(c, st):
    """Alerts not closed. FlashBlade has no /alerts/events, so the open set
    is diffed locally. The state filter is the cheap read; an array that
    rejects it (400) is read unfiltered, newest first, from then on, and
    the switch is logged once."""
    if not st.get("unfiltered"):
        resp = c.get_alerts(filter="state!='closed'")
        if _status(resp) != 400:
            return _items(resp, "alerts")
        st["unfiltered"] = True
        logger.warning(
            "purefb_events[alerts]: the alert state filter was rejected (400); "
            "reading the newest %d alerts unfiltered from now on",
            ALERTS_UNFILTERED_LIMIT,
        )
    rows = _items(
        c.get_alerts(limit=ALERTS_UNFILTERED_LIMIT, sort=["created-"]), "alerts"
    )
    return [a for a in rows if a.get("state") != "closed"]


def poll_alerts(arr, state, args):
    c = arr.client
    st = state.setdefault("alerts", {}).setdefault(arr.name, {"seen": None})
    active = _active_alerts(c, st)
    seen = st["seen"]
    first = seen is None
    current = {}
    events = []

    maintenance_active = bool(_maintenance(arr, state))

    def event(a, transition, previous=None):
        created = a.get("created")
        closed = transition == "closed"
        # how long the alert was open when it closed: the array's own
        # duration where it reports one, else updated - created
        resolved_after = None
        if closed:
            if a.get("duration"):
                resolved_after = round(a["duration"] / 1000)
            elif created and a.get("updated"):
                resolved_after = round((a["updated"] - created) / 1000)
        return {
            "purefb_alert": {
                "array": arr.name,
                "transition": transition,
                "maintenance_active": maintenance_active,
                "alert_id": a.get("name"),
                "code": a.get("code"),
                "severity": a.get("severity"),
                "state": a.get("state"),
                "component_type": a.get("component_type"),
                "component_name": a.get("component_name"),
                "summary": a.get("summary"),
                "description": a.get("description"),
                "action": a.get("action"),
                "knowledge_base_url": a.get("knowledge_base_url"),
                "flagged": a.get("flagged"),
                "created": created,
                "updated": a.get("updated"),
                "notified": a.get("notified"),
                "variables": dict(a.get("variables") or {}),
                "age_s": round((_now_ms() - created) / 1000) if created else None,
                "resolved_after_s": resolved_after,
                "previous_severity": previous,
                "severity_raised": (
                    _severity_raised(previous, a.get("severity")) if previous else False
                ),
            }
        }

    # The change key leaves `updated` out on purpose: the array re-evaluates
    # some alerts every minute (1112 on the lab) and bumps `updated` each
    # time, which would make every poll an "updated" transition. What a rule
    # can act on is the severity, the flag and the summary text.
    for a in active:
        aid = a.get("name")
        key = (
            a.get("state"),
            a.get("severity"),
            bool(a.get("flagged")),
            a.get("summary"),
        )
        current[aid] = key
        if first:
            if _truthy(args.get("include_initial_alerts"), True):
                events.append(event(a, "initial"))
        elif aid not in seen:
            events.append(event(a, "opened"))
        elif seen[aid] != key:
            previous = seen[aid][1] if len(seen[aid]) > 1 else None
            # the array parks a resolved alert in "closing" for a while
            # before it closes (1101 did on the lab after the grow)
            closing = a.get("state") == "closing" and seen[aid][0] != "closing"
            events.append(event(a, "closing" if closing else "updated", previous))
    if not first:
        for aid in set(seen) - set(current):
            final = _first(
                _items(c.get_alerts(names=[aid]), "alert"),
                {"name": aid, "state": "closed"},
            )
            events.append(event(final, "closed"))
    st["seen"] = current
    return events


def poll_audits(arr, state, args):
    c = arr.client
    st = state.setdefault("audits", {}).setdefault(
        arr.name, {"cursor": None, "ids": set()}
    )
    automation = set(_as_list(args.get("automation_users")))
    agents = tuple(
        _as_list(args.get("automation_user_agents")) or ["pure/py-pure-client"]
    )
    destructive = [
        w.lower()
        for w in (
            _as_list(args.get("destructive_subcommands"))
            or ["destroy", "eradicate", "delete", "remove", "disconnect", "demote"]
        )
    ]
    batch = int(args.get("audit_batch", 200))
    if st["cursor"] is None:
        latest = _first(_items(c.get_audits(limit=1, sort=["time-"]), "audits"))
        lookback = float(args.get("audit_lookback", 0) or 0) * 1000
        st["cursor"] = int((latest.get("time") or _now_ms()) - lookback)
        if not lookback:
            st["ids"] = {latest.get("name")}
    rows, overflow = _rows_since(c.get_audits, st["cursor"], batch, "time", "audits")
    rows = _advance_cursor(st, rows, "time")
    maintenance_active = bool(_maintenance(arr, state)) if rows else False
    events = []
    for r in rows:
        user = r.get("user")
        agent = str(r.get("user_agent") or "")
        sub = str(r.get("subcommand") or "").lower()
        events.append(
            {
                "purefb_audit": {
                    "array": arr.name,
                    "maintenance_active": maintenance_active,
                    "audit_id": r.get("name"),
                    "time": r.get("time"),
                    "user": user,
                    "user_agent": r.get("user_agent"),
                    "ip_address": r.get("ip_address"),
                    "user_interface": r.get("user_interface"),
                    "origin": _name(r.get("origin")),
                    "command": r.get("command"),
                    "subcommand": r.get("subcommand"),
                    "arguments": r.get("arguments"),
                    "by_automation": user in automation,
                    "automation_agent": agent.startswith(agents),
                    "destructive": any(w in sub for w in destructive),
                }
            }
        )
    if overflow:
        msg = (
            f"audit_batch overflow: more than {AUDIT_MAX_PAGES * batch} audit records "
            "since the last poll, the oldest were skipped; raise audit_batch"
        )
        logger.warning("purefb_events[audits]: %s: %s", arr.name, msg)
        # a successful poll that dropped records: consecutive 0, so the
        # poll-failure rules leave it to the debug rule
        events.append(_poll_error("audits", arr.name, msg, 0, False))
    return events


def poll_sessions(arr, state, args):
    """Management sessions (logins, logouts, failed attempts), cursor on
    start_time. The first poll only sets the cursor: a session log replayed
    at every restart would page on old logins."""
    c = arr.client
    st = state.setdefault("sessions", {}).setdefault(
        arr.name, {"cursor": None, "ids": set()}
    )
    batch = int(args.get("audit_batch", 200))
    if st["cursor"] is None:
        latest = _first(
            _items(c.get_sessions(limit=1, sort=["start_time-"]), "sessions")
        )
        st["cursor"] = int(latest.get("start_time") or _now_ms())
        st["ids"] = {latest.get("name")}
    rows, overflow = _rows_since(
        c.get_sessions, st["cursor"], batch, "start_time", "sessions"
    )
    rows = _advance_cursor(st, rows, "start_time")
    maintenance_active = bool(_maintenance(arr, state)) if rows else False
    events = [
        {
            "purefb_session": {
                "array": arr.name,
                "maintenance_active": maintenance_active,
                "session_id": r.get("name"),
                "event": r.get("event"),
                "event_count": r.get("event_count"),
                "user": r.get("user"),
                "location": r.get("location"),
                "method": r.get("method"),
                "user_interface": r.get("user_interface"),
                "start_time": r.get("start_time"),
                "end_time": r.get("end_time"),
            }
        }
        for r in rows
    ]
    if overflow:
        msg = (
            f"audit_batch overflow: more than {AUDIT_MAX_PAGES * batch} sessions "
            "since the last poll, the oldest were skipped; raise audit_batch"
        )
        logger.warning("purefb_events[sessions]: %s: %s", arr.name, msg)
        events.append(_poll_error("sessions", arr.name, msg, 0, False))
    return events


def _lldp_neighbors(c, st, refresh):
    """Switch name, address and port behind each Ethernet port, from the
    array's LLDP view, keyed by the local port name (CH1.FM1.ETH1, the same
    name /hardware uses). A port whose link is down has no neighbour any
    more, so the cache is only rebuilt while every LAG is healthy and the
    last good view names the switch port to check when one goes down. The
    first poll fills it whatever the LAG state; a failed read keeps the
    old view."""
    cache = st.setdefault("lldp", {})
    if not refresh and cache:
        return cache
    try:
        rows = _items(
            c.get_network_interfaces_neighbors(), "network-interfaces/neighbors"
        )
    except Exception as exc:  # noqa: BLE001 - the neighbour view is an enrichment
        logger.debug("purefb_events[health]: LLDP neighbours unread: %s", exc)
        return cache
    fresh = {}
    for n in rows:
        port = _name(n.get("local_port"))
        if not port:
            continue
        chassis = n.get("neighbor_chassis") or {}
        nport = n.get("neighbor_port") or {}
        addresses = chassis.get("addresses") or []
        fresh[port] = {
            "neighbor_switch": chassis.get("name") or "",
            "neighbor_switch_address": str(addresses[0]) if addresses else "",
            "neighbor_port": nport.get("description")
            or (nport.get("id") or {}).get("value")
            or "",
        }
    if fresh:
        st["lldp"] = fresh
        return fresh
    return cache


def _lag_expected_speed(lag, connectors):
    """What the LAG should carry: the sum of its member ports' speeds from
    /hardware/connectors, the LAG's own port_speed for a member without a
    connector row. None for a LAG with no members."""
    ports = [_name(p) for p in (lag.get("ports") or [])]
    if not ports:
        return None
    return sum(
        (connectors.get(p) or {}).get("port_speed") or lag.get("port_speed") or 0
        for p in ports
    )


def _lag_degraded(lag, expected):
    if (lag.get("status") or "unknown") != "healthy":
        return True
    speed = lag.get("lag_speed")
    return bool(expected and isinstance(speed, (int, float)) and speed < expected)


def poll_health(arr, state, args):
    c = arr.client
    st = state.setdefault("health", {}).setdefault(arr.name, {"issues": {}, "lags": {}})
    hw_ignore = set(
        _as_list(args.get("hardware_ignore_status"))
        or ["healthy", "not_installed", "unused", "unclaimed"]
    )
    blade_ignore = set(
        _as_list(args.get("blade_ignore_status")) or ["healthy", "unused"]
    )
    drive_ignore = set(
        _as_list(args.get("drive_ignore_status"))
        or ["healthy", "unused", "empty", "evacuated", "unclaimed"]
    )

    hardware = _items(c.get_hardware(), "hardware")
    issues = [h for h in hardware if (h.get("status") or "unknown") not in hw_ignore]
    blades = _items(c.get_blades(), "blades")
    blade_issues = [
        b for b in blades if (b.get("status") or "unknown") not in blade_ignore
    ]
    drives = _items(c.get_drives(), "drives")
    drive_issues = [
        d for d in drives if (d.get("status") or "unknown") not in drive_ignore
    ]
    lags = _items(c.get_link_aggregation_groups(), "link-aggregation-groups")
    connectors = {
        h.get("name"): h
        for h in _items(c.get_hardware_connectors(), "hardware/connectors")
    }
    lag_rows = []
    for lag in lags:
        expected = _lag_expected_speed(lag, connectors)
        lag_rows.append((lag, expected, _lag_degraded(lag, expected)))
    all_lags_healthy = not any(degraded for _, _, degraded in lag_rows)
    lldp = _lldp_neighbors(c, st, refresh=all_lags_healthy)

    windows = _maintenance(arr, state, max_age=0)
    support = _first(_items(c.get_support(), "support"))
    arrays = _first(_items(c.get_arrays(), "arrays"))
    open_alerts = c.get_alerts(filter="state='open'", limit=1)
    open_count = _total(open_alerts) if hasattr(open_alerts, "items") else None
    maintenance_active = bool(windows)

    def neighbour(port):
        return lldp.get(port) or {
            "neighbor_switch": "",
            "neighbor_switch_address": "",
            "neighbor_port": "",
        }

    events = [
        {
            "purefb_health": {
                "array": arr.name,
                "purity_version": arrays.get("version"),
                "hardware_issues": [
                    {
                        "component": h.get("name"),
                        "type": h.get("type"),
                        "status": h.get("status"),
                        "details": h.get("details"),
                    }
                    for h in issues
                ],
                "hardware_issue_count": len(issues),
                "blade_issues": [
                    {
                        "blade": b.get("name"),
                        "status": b.get("status"),
                        "progress": b.get("progress"),
                        "target": b.get("target"),
                        "details": b.get("details"),
                    }
                    for b in blade_issues
                ],
                "blade_issue_count": len(blade_issues),
                "drive_issues": [
                    {
                        "drive": d.get("name"),
                        "status": d.get("status"),
                        "type": d.get("type"),
                        "details": d.get("details"),
                    }
                    for d in drive_issues
                ],
                "drive_issue_count": len(drive_issues),
                "lags": [
                    {
                        "lag": lag.get("name"),
                        "status": lag.get("status"),
                        "lag_speed": lag.get("lag_speed"),
                        "expected_speed": expected,
                        "port_count": len(lag.get("ports") or []),
                        "degraded": degraded,
                    }
                    for lag, expected, degraded in lag_rows
                ],
                "lag_degraded": not all_lags_healthy,
                "blade_count": len(blades),
                "drive_count": len(drives),
                "maintenance_active": maintenance_active,
                "maintenance_windows": [
                    {
                        "name": w.get("name"),
                        "created": w.get("created"),
                        "expires": w.get("expires"),
                    }
                    for w in windows
                ],
                "remote_assist_active": support.get("remote_assist_active"),
                "phonehome_enabled": support.get("phonehome_enabled"),
                "open_alert_count": open_count,
            }
        }
    ]
    for h in issues:
        events.append(
            {
                "purefb_hardware": {
                    "array": arr.name,
                    "component": h.get("name"),
                    "type": h.get("type"),
                    "status": h.get("status"),
                    "details": h.get("details"),
                    "slot": h.get("slot"),
                    "serial": h.get("serial"),
                    "part_number": h.get("part_number"),
                    "identify_enabled": h.get("identify_enabled"),
                    **neighbour(h.get("name")),
                    "maintenance_active": maintenance_active,
                }
            }
        )
    # a component that was an issue last poll and is not one now has
    # recovered: the rulebook can clear the identify LED it lit
    previous_issues = st.get("issues", {})
    current_issues = {h.get("name"): h for h in issues}
    by_name = {h.get("name"): h for h in hardware}
    for name, was in previous_issues.items():
        if name in current_issues:
            continue
        h = by_name.get(name, {})
        events.append(
            {
                "purefb_hardware_recovered": {
                    "array": arr.name,
                    "component": name,
                    "type": h.get("type") or was.get("type"),
                    "status": h.get("status"),
                    "previous_status": was.get("status"),
                    "maintenance_active": maintenance_active,
                }
            }
        )
    st["issues"] = {
        n: {"type": h.get("type"), "status": h.get("status")}
        for n, h in current_issues.items()
    }
    # a LAG event on the change only: the poll that finds it degraded (or a
    # fresh start that does) and the poll that finds it whole again, each
    # naming the switch ports behind its members from the last good LLDP view
    previous_lags = st.get("lags", {})
    for lag, expected, degraded in lag_rows:
        name = lag.get("name")
        was = previous_lags.get(name)
        if degraded == was or (not degraded and was is None):
            continue
        events.append(
            {
                "purefb_lag": {
                    "array": arr.name,
                    "lag": name,
                    "status": lag.get("status"),
                    "lag_speed": lag.get("lag_speed"),
                    "expected_speed": expected,
                    "ports": [
                        {
                            "name": _name(p),
                            "neighbor_switch": neighbour(_name(p))["neighbor_switch"],
                            "neighbor_port": neighbour(_name(p))["neighbor_port"],
                        }
                        for p in (lag.get("ports") or [])
                    ],
                    "degraded": degraded,
                    "recovered": not degraded,
                    "maintenance_active": maintenance_active,
                }
            }
        )
    st["lags"] = {lag.get("name"): degraded for lag, _, degraded in lag_rows}
    return events


def _space_sorted(fn, what):
    """The largest live objects by physical space. verify: the array accepts
    sort on space.total_physical for file systems and buckets; until that is
    confirmed on the lab the default order is the fallback."""
    try:
        return _items(
            fn(destroyed=False, limit=GROWTH_OBJECTS, sort=["space.total_physical-"]),
            what,
        )
    except Exception as exc:  # noqa: BLE001 - retry in the array's order
        logger.debug("purefb_events: %s sorted by space unavailable: %s", what, exc)
        return _items(fn(destroyed=False, limit=GROWTH_OBJECTS), what)


def _objects(c, st, refresh=False):
    """Live file systems and buckets. The capacity step fetches and keeps
    them; the quota and performance steps of the same cycle reuse them
    while fresh, and fetch for themselves when run alone."""
    cached = st.get("objects")
    if not refresh and cached and time.monotonic() - cached[0] < OBJECTS_MAX_AGE:
        return cached[1], cached[2]
    file_systems = _space_sorted(c.get_file_systems, "file-systems")
    buckets = _space_sorted(c.get_buckets, "buckets")
    if refresh:
        st["objects"] = (time.monotonic(), file_systems, buckets)
    return file_systems, buckets


def _growth(points):
    """Bytes per day across (time, total_physical) points, and the oldest
    point; None without two points a day apart."""
    pts = sorted(p for p in points if p[0] and p[1] is not None)
    if len(pts) < 2:
        return None, (pts[0] if pts else None)
    span_days = (pts[-1][0] - pts[0][0]) / DAY_MS
    if span_days <= 0:
        return None, pts[0]
    return int((pts[-1][1] - pts[0][1]) / span_days), pts[0]


def _space_history(c, now, **kw):
    """Daily points for the last week: start_time alone, no end_time and no
    limit, which is what returns seven points on REST 2.21."""
    return _items(
        c.get_arrays_space(resolution=DAY_MS, start_time=now - 7 * DAY_MS, **kw),
        "space history",
    )


def _capacity_row(arr, state, args):
    c = arr.client
    st = state.setdefault("telemetry", {}).setdefault(arr.name, {})
    now = _now_ms()
    space = _first(_items(c.get_arrays_space(), "arrays/space"))
    sp = space.get("space") or {}
    capacity = space.get("capacity") or 0
    used = sp.get("total_physical") or 0

    growth = days_to_full = None
    first_space = None
    try:
        hist = _space_history(c, now)
        growth, first_point = _growth(
            [
                (h.get("time"), (h.get("space") or {}).get("total_physical"))
                for h in hist
            ]
        )
        if growth and growth > 0 and capacity:
            days_to_full = round((capacity - used) / growth, 1)
        oldest = min(
            (h for h in hist if h.get("time") and h.get("space")),
            key=lambda h: h["time"],
            default=None,
        )
        first_space = (oldest or {}).get("space")
    except Exception as exc:  # noqa: BLE001 - history is optional
        logger.debug("purefb_events[%s]: space history unavailable: %s", arr.name, exc)

    # the same split by object type: how much is file, how much is object,
    # and which one is growing
    by_type = {}
    for kind, key in (("file-system", "file_system"), ("object-store", "object_store")):
        by_type[key] = {"bytes": None, "growth": None}
        try:
            cur = _first(_items(c.get_arrays_space(type=kind), f"arrays/space {kind}"))
            by_type[key]["bytes"] = (cur.get("space") or {}).get("total_physical")
            by_type[key]["growth"], _ = _growth(
                [
                    (h.get("time"), (h.get("space") or {}).get("total_physical"))
                    for h in _space_history(c, now, type=kind)
                ]
            )
        except Exception as exc:  # noqa: BLE001 - the split is optional
            logger.debug(
                "purefb_events[%s]: %s space unavailable: %s", arr.name, kind, exc
            )

    # anomaly inputs: data reduction and snapshot space against the oldest
    # point of the 7-day daily history
    drr_now = sp.get("data_reduction")
    snap_now = sp.get("snapshots") or 0
    drr_week = (first_space or {}).get("data_reduction")
    snap_week = (first_space or {}).get("snapshots")
    drr_drop_pct = (
        round(100.0 * (drr_week - drr_now) / drr_week, 1)
        if drr_week and drr_now is not None
        else None
    )
    snap_growth_pct = (
        round(100.0 * (snap_now - snap_week) / snap_week, 1) if snap_week else None
    )
    drr_drop_threshold = float(args.get("drr_drop_threshold_pct", 10))
    snap_growth_threshold = float(args.get("snapshot_growth_threshold_pct", 25))

    # growth per object across polls, over the largest live file systems and
    # buckets; the first poll only records sizes
    top_n = int(args.get("top_growers", 5))
    file_systems, buckets = _objects(c, st, refresh=True)
    sizes = {}
    for kind, rows in (("file_system", file_systems), ("bucket", buckets)):
        for r in rows:
            sizes[(kind, r.get("name"))] = (r.get("space") or {}).get(
                "total_physical"
            ) or 0
    prev = st.get("sizes") or {}
    growers = sorted(
        (
            {
                "name": name,
                "kind": kind,
                "delta_bytes": size - prev[(kind, name)],
                "total_physical": size,
            }
            for (kind, name), size in sizes.items()
            if (kind, name) in prev and size - prev[(kind, name)] > 0
        ),
        key=lambda g: g["delta_bytes"],
        reverse=True,
    )[:top_n]
    st["sizes"] = sizes

    def _pending(fn):
        r = fn(destroyed=True, limit=1)
        return _total(r) if hasattr(r, "items") else None

    return {
        "array": arr.name,
        "time": space.get("time"),
        "capacity_bytes": capacity,
        "used_bytes": used,
        "used_pct": round(100.0 * used / capacity, 2) if capacity else None,
        "data_reduction": drr_now,
        "snapshot_bytes": sp.get("snapshots"),
        "destroyed_bytes": sp.get("destroyed"),
        "destroyed_virtual_bytes": sp.get("destroyed_virtual"),
        "total_provisioned_bytes": sp.get("total_provisioned"),
        "growth_bytes_per_day": growth,
        "days_to_full": days_to_full,
        "days_to_full_known": days_to_full is not None,
        "file_system_bytes": by_type["file_system"]["bytes"],
        "object_store_bytes": by_type["object_store"]["bytes"],
        "file_system_growth_bytes_per_day": by_type["file_system"]["growth"],
        "object_store_growth_bytes_per_day": by_type["object_store"]["growth"],
        "pending_eradication_file_systems": _pending(c.get_file_systems),
        "pending_eradication_buckets": _pending(c.get_buckets),
        "pending_eradication_snapshots": _pending(c.get_file_system_snapshots),
        "top_growers": growers,
        "drr_week_ago": drr_week,
        "drr_drop_pct": drr_drop_pct,
        "snapshot_growth_pct": snap_growth_pct,
        # anomaly booleans (thresholds: the *_threshold_pct arguments)
        "drr_drop": bool(
            drr_drop_pct is not None and drr_drop_pct >= drr_drop_threshold
        ),
        "snapshot_surge": bool(
            snap_growth_pct is not None and snap_growth_pct >= snap_growth_threshold
        ),
    }


def _fs_quota_events(arr, state, args):
    """One event per file system at or above fs_quota_warn_pct of its
    provisioned size: the deterministic twin of alert 1101, from the same
    numbers the array uses (virtual space against provisioned)."""
    c = arr.client
    st = state.setdefault("telemetry", {}).setdefault(arr.name, {})
    warn = float(args.get("fs_quota_warn_pct", 80))
    file_systems, _ = _objects(c, st)
    events = []
    for f in file_systems:
        provisioned = f.get("provisioned") or 0
        if provisioned <= 0:
            continue
        used = (f.get("space") or {}).get("virtual") or 0
        used_pct = round(100.0 * used / provisioned, 2)
        if used_pct < warn:
            continue
        events.append(
            {
                "purefb_fs_quota": {
                    "array": arr.name,
                    "file_system": f.get("name"),
                    "provisioned": provisioned,
                    "used_bytes": used,
                    "used_pct": used_pct,
                    "hard_limit_enabled": f.get("hard_limit_enabled"),
                    "snapshot_bytes": (f.get("space") or {}).get("snapshots"),
                    "qos_policy": _name(f.get("qos_policy")),
                    "destroyed": f.get("destroyed"),
                }
            }
        )
    return events


def _perf_fields(perf, prefix=""):
    return {prefix + k: perf.get(k) for k in PERF_FIELDS}


def _split_client(name):
    """A client row is named <address>:<port> (IPv6 as [address]:port); the
    address is what maps to an inventory host, the port rides along."""
    s = str(name or "")
    if s.startswith("["):
        addr, _, rest = s[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    elif s.count(":") == 1:
        addr, _, port = s.partition(":")
    else:
        addr, port = s, ""
    return addr, (int(port) if port.isdigit() else None)


def _ops(perf):
    return (
        (perf.get("reads_per_sec") or 0)
        + (perf.get("writes_per_sec") or 0)
        + (perf.get("others_per_sec") or 0)
    )


def _share_streaks(st, key, share, threshold, reset_normal=False):
    """Consecutive polls at or above the share threshold and consecutive
    polls below it, per object; the normal count starts again when asked
    (an object newly capped)."""
    high = st.setdefault("high_share_polls", {})
    normal = st.setdefault("normal_polls", {})
    at_or_above = share is not None and share >= threshold
    high[key] = high.get(key, 0) + 1 if at_or_above else 0
    if reset_normal:
        normal[key] = 0
    else:
        normal[key] = normal.get(key, 0) + 1 if not at_or_above else 0
    return high[key], normal[key]


def _prune_streaks(st, seen):
    for counter in ("high_share_polls", "normal_polls"):
        for key in [k for k in st.get(counter, {}) if k not in seen]:
            st[counter].pop(key, None)


def _by_name_performance(fn, names, what):
    """Current performance rows for `names`. One name that no longer exists
    fails the whole bulk call, so fall back to one call per name and drop
    only that one."""
    if not names:
        return []
    try:
        return _items(fn(names=names), what)
    except Exception:  # noqa: BLE001 - retry one by one
        out = []
        for n in names:
            try:
                out.extend(_items(fn(names=[n]), what))
            except Exception as exc:  # noqa: BLE001
                logger.debug("purefb_events: %s %s unavailable: %s", what, n, exc)
        return out


def _object_performance_events(
    kind, event_name, fn, top, objects, perf, st, threshold, seen, arr
):
    """Top-N busiest objects (file systems or buckets) plus every object
    whose QoS policy is named eda-cap-*: the policy name is the array-held
    state the lift rule reads back, so a capped object is reported whatever
    its rank. The lift's "normal for M polls" counts from the cap."""
    merged = {}
    for v in _items(fn(limit=top, sort=["writes_per_sec-"]), f"{kind}/performance"):
        merged[v.get("name")] = v
    policies = {o.get("name"): _name(o.get("qos_policy")) for o in objects}
    capped = {n for n, p in policies.items() if p and str(p).startswith(EDA_CAP_PREFIX)}
    for v in _by_name_performance(
        fn, sorted(n for n in capped if n not in merged), f"{kind}/performance"
    ):
        merged[v.get("name")] = v
    was_capped = st.get(f"{kind}_capped_last_poll")  # None on the first poll
    newly_capped = set() if was_capped is None else capped - set(was_capped)
    st[f"{kind}_capped_last_poll"] = sorted(capped)
    array_ops = _ops(perf)
    events = []
    for name, v in merged.items():
        key = f"{kind}:{name}"
        seen.add(key)
        share = _pct(_ops(v), array_ops)
        high, normal = _share_streaks(
            st, key, share, threshold, reset_normal=name in newly_capped
        )
        events.append(
            {
                event_name: {
                    "array": arr.name,
                    kind: name,
                    **_perf_fields(v),
                    "ops_share_pct": share,
                    "qos_policy": policies.get(name),
                    "eda_capped": name in capped,
                    "high_share_polls": high,
                    "normal_polls": normal,
                }
            }
        )
    return events


def _performance_events(arr, state, args):
    c = arr.client
    st = state.setdefault("telemetry", {}).setdefault(arr.name, {})
    now = _now_ms()
    perf = _first(_items(c.get_arrays_performance(), "arrays/performance"))
    ev = {"array": arr.name, "time": perf.get("time"), **_perf_fields(perf)}
    # NFS metadata rates: the aggregate fields of the NFS-specific row, which
    # the SDK offers from REST 2.0 but a quiet array may report as zeros
    nfs = _first(
        _optional_items(c, "get_arrays_nfs_specific_performance", "nfs performance")
    )
    ev["nfs_metadata_modifies_per_sec"] = nfs.get(
        "aggregate_file_metadata_modifies_per_sec"
    )
    ev["nfs_metadata_reads_per_sec"] = nfs.get("aggregate_file_metadata_reads_per_sec")
    if _truthy(args.get("baseline"), True):
        try:
            week_ago = now - 7 * DAY_MS
            hist = _items(
                c.get_arrays_performance(
                    resolution=HALF_HOUR_MS,
                    start_time=week_ago - HALF_HOUR_MS,
                    end_time=week_ago + HALF_HOUR_MS,
                ),
                "performance baseline",
            )
            for k in PERF_FIELDS:
                ev["baseline_" + k] = _avg(h.get(k) for h in hist)
            ev["read_latency_ratio"] = _ratio(
                ev["usec_per_read_op"], ev.get("baseline_usec_per_read_op")
            )
            ev["write_latency_ratio"] = _ratio(
                ev["usec_per_write_op"], ev.get("baseline_usec_per_write_op")
            )
            ev["baseline_known"] = ev["write_latency_ratio"] is not None
            ev["write_bw_ratio"] = _ratio(
                ev["write_bytes_per_sec"], ev.get("baseline_write_bytes_per_sec")
            )
            ev["write_surge"] = bool(
                ev["write_bw_ratio"] is not None
                and ev["write_bw_ratio"] >= float(args.get("write_surge_ratio", 3))
            )
        except Exception as exc:  # noqa: BLE001 - baseline is optional
            logger.debug("purefb_events[%s]: baseline unavailable: %s", arr.name, exc)
    ev.setdefault("baseline_known", False)
    ev.setdefault("write_surge", False)
    events = [{"purefb_array_performance": ev}]

    threshold = float(args.get("client_min_share_pct", 40))
    array_ops = _ops(perf)
    seen = set()

    # per-client rows: the row name is the client address, which the
    # rulebook maps to an inventory host. Without `protocol` the array
    # reports NFS clients; S3 clients are a second read where the negotiated
    # version takes the parameter (REST 2.21).
    top_clients = int(args.get("top_clients", 10))
    reads = [("nfs", {})]
    if _accepts(c.get_arrays_clients_performance, "protocol"):
        reads.append(("s3", {"protocol": "S3"}))
    for protocol, extra in reads:
        try:
            rows = _items(
                c.get_arrays_clients_performance(
                    sort=["writes_per_sec-"], limit=top_clients, **extra
                ),
                f"clients/performance {protocol}",
            )
        except Exception as exc:  # noqa: BLE001 - one protocol's clients
            # the lab answered "Service temporarily unavailable" to this call
            # once in an hour while every other read succeeded; the client
            # rows are enrichment, so a failed read skips them for one poll
            # rather than failing the whole telemetry signal
            logger.warning(
                "purefb_events[%s]: %s clients unavailable: %s", arr.name, protocol, exc
            )
            continue
        for v in rows:
            # the row is named <address>:<port> (lab: 10.21.200.129:893); the
            # address is the inventory key, the port is kept beside it
            client, client_port = _split_client(v.get("name"))
            key = f"client:{protocol}:{client}"
            seen.add(key)
            share = _pct(_ops(v), array_ops)
            high, normal = _share_streaks(st, key, share, threshold)
            events.append(
                {
                    "purefb_client_performance": {
                        "array": arr.name,
                        "client": client,
                        "client_port": client_port,
                        "protocol": protocol,
                        **_perf_fields(v),
                        "ops_share_pct": share,
                        "high_share_polls": high,
                        "normal_polls": normal,
                        "array_usec_per_write_op": perf.get("usec_per_write_op"),
                        "array_write_latency_ratio": ev.get("write_latency_ratio"),
                        "baseline_known": ev["baseline_known"],
                    }
                }
            )

    file_systems, buckets = _objects(c, st)
    events.extend(
        _object_performance_events(
            "file_system",
            "purefb_fs_performance",
            c.get_file_systems_performance,
            int(args.get("top_file_systems", 10)),
            file_systems,
            perf,
            st,
            threshold,
            seen,
            arr,
        )
    )
    events.extend(
        _object_performance_events(
            "bucket",
            "purefb_bucket_performance",
            c.get_buckets_performance,
            int(args.get("top_buckets", 10)),
            buckets,
            perf,
            st,
            threshold,
            seen,
            arr,
        )
    )
    _prune_streaks(st, seen)
    return events


def poll_config(arr, state, args):
    c = arr.client
    a = _first(_items(c.get_arrays(), "arrays"))
    # the management configuration when Purity names them, else the only one
    all_dns = _items(c.get_dns(), "dns")
    dns = _first([d for d in all_dns if d.get("name") == "management"] or all_dns)
    all_ds = _items(c.get_directory_services(), "directory-services")
    ds = _first([d for d in all_ds if d.get("name") == "management"] or all_ds)
    ds_nfs = _first([d for d in all_ds if d.get("name") == "nfs"])
    erad = a.get("eradication_config") or {}
    admin = _first(_items(c.get_admins_settings(), "admins/settings"))
    syslog = _items(c.get_syslog_servers(), "syslog-servers")
    smtp = _first(_items(c.get_smtp_servers(), "smtp-servers"))
    watchers = [
        w.get("name")
        for w in _items(c.get_alert_watchers(), "alert-watchers")
        if w.get("enabled")
    ]
    support = _first(_items(c.get_support(), "support"))
    # an array without SAML or a domain join answers with no rows, and one
    # too old for the endpoint reads the same way
    idps = _optional_items(c, "get_sso_saml2_idps", "sso/saml2-idps")
    ad = _optional_items(c, "get_active_directory", "active-directory")
    data_at_rest = (a.get("encryption") or {}).get("data_at_rest") or {}
    return [
        {
            "purefb_config": {
                "array": arr.name,
                "maintenance_active": bool(_maintenance(arr, state)),
                "purity_version": a.get("version"),
                "ntp_servers_csv": _csv(a.get("ntp_servers")),
                "time_zone": a.get("time_zone"),
                "dns_domain": dns.get("domain"),
                "dns_nameservers_csv": _csv(dns.get("nameservers")),
                "ds_management_enabled": bool(ds.get("enabled")),
                "ds_management_uris_csv": _csv(ds.get("uris")),
                "ds_management_base_dn": ds.get("base_dn"),
                "ds_management_bind_user": ds.get("bind_user"),
                "ds_nfs_enabled": bool(ds_nfs.get("enabled")),
                "ad_joined": bool(ad),
                "manual_eradication": erad.get("manual_eradication"),
                "eradication_delay_ms": erad.get("eradication_delay"),
                "lockout_duration_ms": admin.get("lockout_duration"),
                "max_login_attempts": admin.get("max_login_attempts"),
                "min_password_length": admin.get("min_password_length"),
                "sso_enabled": any(i.get("enabled") for i in idps),
                "syslog_servers_csv": _csv(s.get("uri") for s in syslog),
                "syslog_data_audit_servers_csv": _csv(
                    s.get("uri")
                    for s in syslog
                    if "data-audit" in (s.get("services") or [])
                ),
                "smtp_relay_host": smtp.get("relay_host"),
                "smtp_sender_domain": smtp.get("sender_domain"),
                "alert_watchers_enabled_csv": _csv(watchers),
                "phonehome_enabled": support.get("phonehome_enabled"),
                "remote_assist_status": support.get("remote_assist_status"),
                "banner_set": bool((a.get("banner") or "").strip()),
                "default_inbound_tls_policy": _name(
                    a.get("default_inbound_tls_policy")
                ),
                "data_at_rest_encryption": data_at_rest.get("enabled"),
                "smb_mode": a.get("smb_mode"),
                "network_access_policy": _name(a.get("network_access_policy")),
            }
        }
    ]


# telemetry is the only signal that needs every array's result before it can
# emit: the capacity event lists the other polled arrays, least used first.
def _capacity_events(rows):
    events = []
    for row in rows:
        others = sorted(
            (
                {"array": r["array"], "used_pct": r["used_pct"]}
                for r in rows
                if r is not row
            ),
            key=lambda r: (r["used_pct"] is None, r["used_pct"]),
        )
        events.append({"purefb_capacity": {**row, "others": others}})
    return events


POLLERS = {
    "alerts": poll_alerts,
    "audits": poll_audits,
    "sessions": poll_sessions,
    "health": poll_health,
    "config": poll_config,
}


# --------------------------------------------------------------------------
# asyncio driver
# --------------------------------------------------------------------------
async def _signal_loop(queue, signal, arrays, state, args, executor):
    loop = asyncio.get_running_loop()
    interval = float(args.get(f"{signal}_interval") or args.get("interval") or 60)

    def run_sync(arr, fn, st, a):
        try:
            return fn(arr, st, a)
        except Exception:
            arr.reset()
            raise

    async def run(arr, fn):
        # every poller on an array runs under its lock: one SDK client, one
        # request at a time, and reset() never races a call in flight
        async with arr.lock:
            return await loop.run_in_executor(executor, run_sync, arr, fn, state, args)

    async def emit(events):
        for event in events:
            await queue.put(event)

    escalate_after = int(args.get("poll_error_escalate", 10) or 10)

    def failed(arr, exc):
        count = _poll_streak(state, signal, arr.name, True)
        logger.warning(
            "purefb_events[%s]: %s poll failed (%d in a row): %s",
            signal,
            arr.name,
            count,
            exc,
        )
        return [_poll_error(signal, arr.name, exc, count, count >= escalate_after)]

    logger.info(
        "purefb_events[%s]: polling %s every %ss",
        signal,
        [a.name for a in arrays],
        interval,
    )
    while True:
        started = time.monotonic()
        if signal == "telemetry":
            # capacity rows are collected across the arrays and emitted after
            # the last one, because each lists the others
            rows = []
            for arr in arrays:
                try:
                    rows.append(await run(arr, _capacity_row))
                    await emit(await run(arr, _performance_events))
                    await emit(await run(arr, _fs_quota_events))
                    _poll_streak(state, signal, arr.name, False)
                except Exception as exc:  # noqa: BLE001 - report and keep polling
                    await emit(failed(arr, exc))
            await emit(_capacity_events(rows))
        else:
            for arr in arrays:
                try:
                    events = await run(arr, POLLERS[signal])
                    _poll_streak(state, signal, arr.name, False)
                except Exception as exc:  # noqa: BLE001 - report and keep polling
                    events = failed(arr, exc)
                await emit(events)
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(1.0, interval - elapsed))


async def main(queue: asyncio.Queue, args: dict) -> None:
    verify = _truthy(args.get("verify_ssl"), False)
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    specs = _decode_arrays(args.get("arrays"), "purefb_events") or []
    if isinstance(specs, dict):
        specs = [dict(name=k, **v) for k, v in specs.items()]
    request_timeout = float(args.get("request_timeout", 30) or 30)
    arrays = [Array(spec, verify, request_timeout) for spec in specs]
    if not arrays:
        raise ValueError("purefb_events: at least one array is required")
    signals = _as_list(args.get("signals")) or list(SIGNALS)
    reserved = [s for s in signals if s in RESERVED_SIGNALS]
    if reserved:
        raise ValueError(
            f"purefb_events: signals {reserved} are not supported yet by this "
            f"plugin (they are planned for a later release); supported: {SIGNALS}"
        )
    bad = [s for s in signals if s not in SIGNALS]
    if bad:
        raise ValueError(f"purefb_events: unknown signals {bad}; valid: {SIGNALS}")
    state: dict = {}
    # one worker per array lock plus one, so no signal waits on a thread
    executor = ThreadPoolExecutor(max_workers=len(arrays) + 1)
    tasks = [
        asyncio.create_task(_signal_loop(queue, s, arrays, state, args, executor))
        for s in signals
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()
        executor.shutdown(wait=False)


if __name__ == "__main__":
    # Manual test: python purefb_events.py <name> <fb_url> <TOKEN_ENV_VAR> [signals] [interval]
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
                        "fb_url": sys.argv[2],
                        "api_token_env": sys.argv[3],
                    }
                ],
                "signals": sys.argv[4] if len(sys.argv) > 4 else None,
                "interval": float(sys.argv[5]) if len(sys.argv) > 5 else 30,
                "audit_lookback": 600,
            },
        )
    )
