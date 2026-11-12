# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""
purefb_syslog.py - ansible-rulebook event source that listens for FlashBlade
syslog messages (push path, sub-second).

Point the array at this host (GUI: Settings > System > Syslog Servers, or
everpure.flashblade.purefb_syslog with a tcp:// or udp:// URI). A FlashBlade
syslog server carries two services. The `management` service sends the
programs purity.alert, purity.audit and purity.test; the `data-audit`
service sends one JSON record per audited file operation (SMB from
Purity//FB 4.4, NFS from 4.8.0) from every blade over the management VIP,
up to 128 KB a record. Each line is turned into one event; the management
lines are parsed into fields so rules can match on user, command, code and
severity without regexes, and the data-audit records are tallied per file
system, user and client into sliding windows so a mass delete or a rename
storm is one event, not ten thousand.

Arguments
---------
host                    bind address (default 0.0.0.0; bind to the interface
                        the arrays reach)
port                    port (default 5140; 514 needs root or a capability)
protocols               list or comma-separated string, udp and/or tcp
                        (default both). Data-audit records can be 128 KB:
                        use tcp for the data-audit service, a datagram is
                        64 KB at most.
programs                list or comma-separated string of syslog programs to
                        emit (default purity.alert, purity.audit,
                        purity.test). Lines from any other program, and
                        lines with no syslog header, are dropped and
                        counted; `all` (or an empty list) passes everything
                        through as purefb_syslog_other. Data-audit records
                        carry no program and are always emitted.
hostnames               optional mapping array name or syslog hostname ->
                        inventory host name, for arrays whose own name or
                        logged hostname differs from the inventory (default
                        {}). A JSON string (or a YAML string) that decodes to
                        the mapping is accepted too, so a platform credential
                        can inject it as one variable; it is decoded before
                        validation and never echoed into an error or a log.
automation_users        list or comma-separated string of user names counted
                        as automation (default: empty)
allowed_sources         list or comma-separated string of IPv4/IPv6 addresses
                        or CIDR prefixes allowed to send; datagrams and
                        connections from any other address are dropped and
                        logged at debug level (default: empty = accept any
                        sender)
file_window_seconds     length of the sliding window a data-audit record is
                        counted in, per (fs, user, client_ip) (default 60)
mass_delete_threshold   deletes, or create+delete pairs, in one window that
                        raise purefb_file_activity (default 100)
rename_storm_threshold  extension-changing renames in one window that raise
                        purefb_file_activity (default 50)

Security
--------
Syslog carries no authentication. The array name is taken from the message
text and a UDP source address can be forged, so `allowed_sources` is hygiene
rather than authentication. Prefer `protocols: [tcp]` plus a host firewall
limited to the arrays' management addresses, and let syslog events drive
notifications only; remediation belongs to the REST plugin's audit and
alert records. Data-audit records name users and paths: keep the alert log
where the array's own audit log would be kept.

Events (one dict per line; the top-level key names the kind)
------------------------------------------------------------
Every event carries array (inventory name: the message's own array name,
else the syslog hostname, whichever is first found in `hostnames`; unmapped
names pass through), host (hostname from the header, before mapping),
syslog_time (header timestamp text), time (the record's own time when it
carries one - the audit Time= and the data-audit time - else the header
timestamp), program (purity.alert | purity.audit | purity.test | other
program | None for a data-audit record), message (the text after the
header), syslog_severity (the PRI's severity word, None when the array
sends no PRI), facility (None without a PRI), source (sender ip), protocol
(udp | tcp) and raw (the whole line).

purefb_syslog_alert   `<SEVERITY> <State> - <array>: <subject> [<code>]
                      #012<details>`, the details being lines separated by
                      #012 (the octal escape rsyslog uses for a newline):
                      free text, then `Severity:`, `State:`, `First Seen UTC
                      Time:`, `Array Name:`, `Array ID:`, `Purity//FB
                      Version:`, `Suggested Action:` and `Knowledge Base
                      Article:`. Gives severity (info | warning | critical,
                      the same words the REST purefb_alert event uses: the
                      Severity field, else the leading word, else the PRI),
                      state, subject, code (int), details (the free-text
                      lines), action, knowledge_base_url, first_seen,
                      array_id, purity_version. A purity.alert line the
                      layout does not match is emitted whole with code None.
purefb_syslog_audit   `- Adding audit entry. GUID=..., ID=..., Time=...,
                      User=..., Location=..., UI=..., Command=...,
                      Subcommand=..., Arguments=...` gives guid, audit_id,
                      time, user, location (the client ip), user_interface
                      (CLI | GUI | REST), command, subcommand, arguments and
                      by_automation (user in automation_users). The audit
                      line names no array: `array` is the mapped hostname.
purefb_syslog_test    the purity.test line; `Array Name:` and `UTC Time:`
                      trailers are read when present (array_name, utc_time).
purefb_syslog_other   any other program (only when `programs` lets it
                      through) and any line with no recognisable syslog
                      header; the latter arrives with message and raw set to
                      the line and every other field None, never named
                      after the sender.
purefb_file_event     one data-audit record (a syslog line whose message is
                      a JSON object with "pf" fb or fa; FlashArray's
                      fileaudit program writes the same schema, so one
                      listener serves both): user, u_sid, client_ip, proto
                      (smb | nfs), fs, path, inode, op_code, op_name, f_type,
                      result (success | err_...), tgt_path (renames), eid,
                      time (the record's ISO time), pf, v,
                      create_disposition.
purefb_file_activity  raised at most once per window per (fs, user,
                      client_ip) when a count reaches its threshold: fs,
                      user, client_ip, proto, window_seconds, events (records
                      in the window), deletes, renames (extension-changing
                      renames), create_delete_pairs (a delete of a path
                      created earlier in the same window: the dropper
                      pattern), mass_delete (deletes or pairs at or above
                      mass_delete_threshold), rename_storm (renames at or
                      above rename_storm_threshold), first_time, last_time,
                      sample_paths (the last five paths touched). The
                      thresholds are repeated in the event so a rule can
                      show them. A second event follows inside the window
                      only when the other threshold trips too.

Format verification: the management layouts above are the ones the
FlashBlade Admin Guide (4.8.1, remote syslog chapter) documents and the
data-audit schema is the one the file-auditing guide publishes (pf, v 1.3,
eid, time, u_sid, user, client_ip, proto, inode, fs, path, op_code, op_name,
f_type, result, create_disposition, tgt_path). verify: capture real lines
from the lab array (Purity//FB 4.6.4) over TCP and check the timestamp
form, the hostname the array logs (array name or FM name), whether a PRI is
sent, and the op_name vocabulary; the 4.6.4 lab has no syslog server
configured yet. The array sends RFC 3164 by default; RFC 5424 is a tunable
(`rfc5424_preferred`) and both headers are accepted.
"""

DOCUMENTATION = r"""
---
name: purefb_syslog
short_description: Receive Everpure FlashBlade syslog messages as events.
description:
  - Listens on UDP and/or TCP for syslog lines from FlashBlades and emits
    one event per line, keyed by kind - C(purefb_syslog_alert),
    C(purefb_syslog_audit), C(purefb_syslog_test), C(purefb_syslog_other)
    for the management service, C(purefb_file_event) for a data-audit
    record - plus C(purefb_file_activity) when a sliding window per file
    system, user and client reaches a delete or rename threshold.
  - The management lines are parsed into fields (user, command, subcommand,
    arguments; severity, state, code, action) so rules can match without
    regexes. A line from a program not in C(programs) is dropped and
    counted; data-audit records carry no program and are always emitted.
  - Syslog carries no authentication and a UDP source address can be forged.
    Bind to the interface the arrays reach, prefer TCP behind a host
    firewall, and drive notifications from these events, not remediation.
options:
  host:
    description: Bind address.
    type: str
    default: 0.0.0.0
  port:
    description: Listening port; 514 needs root or a capability.
    type: int
    default: 5140
  protocols:
    description:
      - Transports to listen on, udp and/or tcp (list or comma-separated
        string). Data-audit records can be 128 KB, so the data-audit
        service needs tcp.
    type: list
    elements: str
    default: [udp, tcp]
  programs:
    description:
      - Syslog programs to emit (list or comma-separated string). Lines from
        any other program, and lines with no syslog header, are dropped and
        counted. C(all) or an empty list passes everything through as
        C(purefb_syslog_other). Data-audit records are not filtered.
    type: list
    elements: str
    default: [purity.alert, purity.audit, purity.test]
  hostnames:
    description:
      - Mapping of an array's own name or syslog hostname to the inventory
        host name, for arrays whose names differ from the inventory.
      - A JSON string is accepted so a platform credential can inject the
        mapping as one extra var (a YAML string decodes the same way); the
        string is decoded before validation and is never written to a log.
    type: dict
    default: {}
  automation_users:
    description: User names whose audit lines count as automation (list or comma-separated string).
    type: list
    elements: str
    default: []
  allowed_sources:
    description:
      - IPv4/IPv6 addresses or CIDR prefixes allowed to send (list or
        comma-separated string). Empty accepts any sender. This is hygiene,
        not authentication.
    type: list
    elements: str
    default: []
  file_window_seconds:
    description: Sliding window, in seconds, that data-audit records are counted in per file system, user and client.
    type: int
    default: 60
  mass_delete_threshold:
    description: Deletes, or create+delete pairs, in one window that raise C(purefb_file_activity).
    type: int
    default: 100
  rename_storm_threshold:
    description: Extension-changing renames in one window that raise C(purefb_file_activity).
    type: int
    default: 50
"""

EXAMPLES = r"""
- name: FlashBlade syslog
  hosts: all
  sources:
    - sdodsley.flashblade_eda.purefb_syslog:
        host: "{{ syslog_host }}"
        port: "{{ syslog_port }}"
        protocols: [tcp]
        programs: "{{ syslog_programs }}"
        hostnames: "{{ syslog_hostnames }}"
        automation_users: "{{ automation_users }}"
        allowed_sources: "{{ syslog_allowed_sources }}"
  rules:
    - name: Human action via syslog audit - notify
      condition: event.purefb_syslog_audit.by_automation == false
      action:
        debug:
          msg: "{{ event.purefb_syslog_audit.user }} on {{ event.purefb_syslog_audit.array }}: {{ event.purefb_syslog_audit.command }} {{ event.purefb_syslog_audit.subcommand }}"
    - name: Mass delete - notify
      condition: event.purefb_file_activity.mass_delete == true
      action:
        debug:
          msg: "{{ event.purefb_file_activity.user }} from {{ event.purefb_file_activity.client_ip }} deleted {{ event.purefb_file_activity.deletes }} files on {{ event.purefb_file_activity.fs }}"
"""

import asyncio
import collections
import datetime
import ipaddress
import json
import logging
import re
import time

try:
    import yaml
except ImportError:  # ansible-rulebook ships PyYAML; a bare interpreter may not
    yaml = None

logger = logging.getLogger(__name__)

SEVERITIES = ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"]
# Purity's own alert severity words -> the words the REST purefb_alert event
# carries, so one rule condition serves both transports.
ALERT_SEVERITIES = {
    "info": "info",
    "informational": "info",
    "warning": "warning",
    "warn": "warning",
    "critical": "critical",
    "crit": "critical",
}
# A PRI severity word -> the same three grades, used only when the line
# carries no severity word of its own.
PRI_TO_ALERT = {
    "emerg": "critical",
    "alert": "critical",
    "crit": "critical",
    "err": "critical",
    "warning": "warning",
}
# The three programs the management service writes; everything else a
# syslog target receives is the array's system log.
DEFAULT_PROGRAMS = ["purity.alert", "purity.audit", "purity.test"]
# [<PRI>]Mon DD HH:MM:SS host program[pid]: message      (RFC 3164 style)
# [<PRI>]Mon DD HH:MM:SS host : {json}                    (data-audit record:
# the file-auditing guide shows `DATE ARRAY_NAME : MESSAGE` with no program)
# An ISO 8601 timestamp is accepted in the date position as well.
# verify: the timestamp form and the hostname (array or FM name) the lab
# array logs for each service.
RFC3164 = re.compile(
    r"^(?:<(?P<pri>\d{1,3})>)?"
    r"(?P<time>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}|\d{4}-\d{2}-\d{2}T[0-9:.+Z-]+)\s+"
    r"(?P<host>[^\s:]+)(?:\s+(?P<prog>[^\s:\[]+)(?:\[\d+\])?)?\s*:\s*(?P<msg>.*)$",
    re.S,
)
# <PRI>1 2026-10-20T18:00:00Z host program - - - message   (RFC 5424 style)
RFC5424 = re.compile(
    r"^<(?P<pri>\d{1,3})>1\s+(?P<time>\S+)\s+(?P<host>\S+)\s+(?P<prog>\S+)\s+\S+\s+\S+\s+(?:\[.*?\]|-)\s*(?P<msg>.*)$",
    re.S,
)
# purity.alert: "<SEVERITY> <State> - <array>: <subject> [<code>] #012<details>"
ALERT = re.compile(
    r"^(?P<level>[A-Za-z]+)\s+(?P<state>[A-Za-z]+)\s+-\s+(?P<array>[^\s:]+):\s+"
    r"(?P<subject>.+?)\s*\[(?P<code>\d+)\]\s*(?P<details>.*)$",
    re.S,
)
# Lines inside the alert details are separated by #012 (rsyslog's escape for
# a newline); a real newline is accepted for a forwarder that keeps them.
DETAIL_SEP = re.compile(r"#012|\n")
# The labelled detail lines -> event field names
ALERT_FIELDS = {
    "severity": "severity_word",
    "state": "state",
    "first seen utc time": "first_seen",
    "array name": "array_name",
    "array id": "array_id",
    "purity//fb version": "purity_version",
    "suggested action": "action",
    "knowledge base article": "knowledge_base_url",
}
DETAIL_FIELD = re.compile(r"^(?P<key>[A-Za-z][A-Za-z /]*?):\s*(?P<value>.*)$", re.S)
# purity.audit: "- Adding audit entry. GUID=..., ID=..., Time=..., User=...,
# Location=..., UI=..., Command=..., Subcommand=..., Arguments=..."
AUDIT = re.compile(r"^-?\s*Adding audit entry\.?\s*(?P<pairs>.*)$", re.S)
AUDIT_KEYS = {
    "GUID": "guid",
    "ID": "audit_id",
    "Time": "time",
    "User": "user",
    "Location": "location",
    "UI": "user_interface",
    "Command": "command",
    "Subcommand": "subcommand",
    "Arguments": "arguments",
}
# Arguments may hold commas, so the pairs are cut at the known keys only
AUDIT_KEY = re.compile(
    r"(?:^|,\s*)(GUID|ID|Time|User|Location|UI|Command|Subcommand|Arguments)="
)
# Unquoted trailers on purity.test lines
UTC_TRAILER = re.compile(r"\bUTC Time:\s*(.+?)(?=\s+Array Name:|\s*$)", re.S)
ARRAY_TRAILER = re.compile(r"\bArray [Nn]ame:\s*'?([^'\s]+)'?")
# Fields of a data-audit record copied into purefb_file_event
FILE_KEYS = (
    "pf",
    "v",
    "eid",
    "time",
    "u_sid",
    "user",
    "client_ip",
    "proto",
    "inode",
    "fs",
    "path",
    "op_code",
    "op_name",
    "f_type",
    "result",
    "create_disposition",
    "tgt_path",
)
# Every event carries these keys, parsed or not, so a rule that reads
# `array` or `program` never meets a different shape.
EVENT_KEYS = (
    "kind",
    "array",
    "host",
    "time",
    "syslog_time",
    "program",
    "message",
    "syslog_severity",
    "facility",
    "raw",
)
ALERT_KEYS = (
    "severity",
    "state",
    "subject",
    "code",
    "details",
    "action",
    "knowledge_base_url",
    "first_seen",
    "array_id",
    "purity_version",
)
AUDIT_EVENT_KEYS = tuple(AUDIT_KEYS.values()) + ("by_automation",)
EVENT_NAMES = {
    "alert": "purefb_syslog_alert",
    "audit": "purefb_syslog_audit",
    "test": "purefb_syslog_test",
    "other": "purefb_syslog_other",
    "file": "purefb_file_event",
}
# A data-audit record is 128 KB at most (the file-auditing guide's rsyslog
# MaxMessageSize); a longer "line" is not syslog and closes the TCP
# connection instead of growing the buffer.
TCP_LINE_LIMIT = 131072
# How often a dropped program is mentioned in the log after its first line
DROP_LOG_EVERY = 1000
# Sliding-window defaults for data-audit records
DEFAULT_FILE_WINDOW_SECONDS = 60
DEFAULT_MASS_DELETE_THRESHOLD = 100
DEFAULT_RENAME_STORM_THRESHOLD = 50
SAMPLE_PATHS = 5
# verify: the op_name vocabulary of data-audit records. The guide shows
# create_regular_file; the rest is classified by these words in op_name.
DELETE_WORDS = ("unlink", "delete", "remove", "rmdir")


def _as_list(value):
    # same helper as purefb_events._as_list, copied rather than imported so
    # this plugin does not pull in pypureclient
    if not value:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return list(value)


def _programs(value):
    """The programs argument as a list; [] means emit every program.

    None (argument absent) gives the Purity programs; `all` anywhere in the
    list, or an explicit empty list, gives [].
    """
    if value is None:
        return list(DEFAULT_PROGRAMS)
    programs = _as_list(value)
    if any(p.lower() == "all" for p in programs):
        return []
    return programs


def _networks(spec):
    """allowed_sources -> list of ip_network objects; [] means accept any."""
    return [
        ipaddress.ip_network(str(item).strip(), strict=False) for item in _as_list(spec)
    ]


def _source_allowed(source, networks):
    """True when `source` is inside one of `networks` (or there are none)."""
    if not networks:
        return True
    try:
        addr = ipaddress.ip_address(str(source).split("%", 1)[0])
    except ValueError:
        return False
    candidates = [addr]
    if addr.version == 6 and addr.ipv4_mapped:
        candidates.append(addr.ipv4_mapped)  # dual-stack socket: ::ffff:203.0.113.1
    return any(a in n for a in candidates for n in networks)


def _array_name(own_name, host, hostnames):
    """The event's array: the message's own array name, else the syslog
    hostname; the first of the two found in `hostnames` is mapped, otherwise
    the name passes through unchanged."""
    candidates = [c for c in (own_name, host) if c]
    for c in candidates:
        if c in hostnames:
            return hostnames[c]
    return candidates[0] if candidates else None


def _alert_fields(m):
    """Fields of the purity.alert layout from its regex match."""
    fields = dict.fromkeys(ALERT_KEYS)
    fields.update(
        state=m.group("state").lower(),
        subject=" ".join(m.group("subject").split()),
        code=int(m.group("code")),
        array_name=m.group("array"),
    )
    free = []
    for part in DETAIL_SEP.split(m.group("details")):
        part = part.strip()
        if not part:
            continue
        f = DETAIL_FIELD.match(part)
        key = ALERT_FIELDS.get(f.group("key").strip().lower()) if f else None
        if key:
            fields[key] = " ".join(f.group("value").split())
        else:
            free.append(" ".join(part.split()))
    fields["details"] = " ".join(free)
    fields["state"] = (fields["state"] or "").lower() or None
    # the Severity field grades the alert; the leading word is the fallback.
    # A labelled Array Name line has already replaced the subject-line array.
    word = fields.pop("severity_word", None) or m.group("level")
    fields["severity"] = ALERT_SEVERITIES.get(word.lower())
    return fields


def _audit_fields(m):
    """Fields of the purity.audit layout: the text is cut at the known
    `Key=` labels so an Arguments value holding commas stays whole."""
    pairs = m.group("pairs")
    fields = dict.fromkeys(AUDIT_KEYS.values())
    marks = list(AUDIT_KEY.finditer(pairs))
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(pairs)
        fields[AUDIT_KEYS[mark.group(1)]] = pairs[mark.end() : end].strip()
    return fields


def _file_record(msg):
    """The data-audit JSON object when `msg` is one (pf fb or fa), else None."""
    text = msg.strip()
    if not text.startswith("{") or not text.endswith("}"):
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if isinstance(obj, dict) and obj.get("pf") in ("fb", "fa"):
        return obj
    return None


def parse(line, hostnames, automation):
    """One syslog line -> the event payload (its `kind` picks the top-level
    key, see event_name)."""
    line = line.strip()
    event = dict.fromkeys(EVENT_KEYS)
    event.update(kind="other", message=line, raw=line)
    m = RFC3164.match(line) or RFC5424.match(line)
    if not m:
        return event
    pri = m.group("pri")
    prog = m.group("prog")
    msg = m.group("msg")
    host = m.group("host")
    record = _file_record(msg)
    if record is not None:
        kind = "file"
    else:
        kind = prog.split(".", 1)[1] if prog and prog.startswith("purity.") else ""
        if kind not in ("alert", "audit", "test"):
            kind = "other"
    own_name = None
    if kind == "file":
        event.update({k: record.get(k) for k in FILE_KEYS})
    elif kind == "alert":
        a = ALERT.match(msg)
        if a:
            fields = _alert_fields(a)
            own_name = fields.pop("array_name")
            event.update(fields)
        else:
            event.update(dict.fromkeys(ALERT_KEYS))
            event["severity"] = PRI_TO_ALERT.get(
                SEVERITIES[int(pri) % 8] if pri is not None else "", "info"
            )
    elif kind == "audit":
        a = AUDIT.match(msg)
        fields = _audit_fields(a) if a else dict.fromkeys(AUDIT_KEYS.values())
        user = fields.get("user")
        fields["by_automation"] = bool(user and user in automation)
        event.update(fields)
    elif kind == "test":
        t = ARRAY_TRAILER.search(msg)
        own_name = t.group(1) if t else None
        event["array_name"] = own_name
        t = UTC_TRAILER.search(msg)
        event["utc_time"] = t.group(1).strip() if t else None
    # `time` is the record's own time when it has one (audit Time=, data-audit
    # time); the header timestamp is always in syslog_time
    if event.get("time") is None:
        event["time"] = m.group("time")
    event.update(
        kind=kind,
        array=_array_name(own_name, host, hostnames),
        host=host,
        syslog_time=m.group("time"),
        program=prog,
        message=msg,
        syslog_severity=SEVERITIES[int(pri) % 8] if pri is not None else None,
        facility=int(pri) // 8 if pri is not None else None,
    )
    return event


def event_name(payload):
    """The top-level key an event payload is published under."""
    return EVENT_NAMES.get(payload.get("kind"), EVENT_NAMES["other"])


def _epoch(text):
    """An ISO 8601 time -> seconds since the epoch, None when unreadable."""
    if not text or not isinstance(text, str):
        return None
    t = text.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        d = datetime.datetime.fromisoformat(t)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d.timestamp()


def _extension(path):
    """The lower-cased extension of the last path component ("" when none);
    SMB paths use backslashes, NFS paths slashes."""
    name = re.split(r"[\\/]", path or "")[-1]
    if "." not in name.lstrip("."):
        return ""
    return name.rsplit(".", 1)[1].lower()


def _op_kind(op_name):
    """delete | rename | create | other from a record's op_name."""
    name = (op_name or "").lower()
    if "rename" in name:
        return "rename"
    if any(w in name for w in DELETE_WORDS):
        return "delete"
    if "create" in name:
        return "create"
    return "other"


class ActivityWindows:
    """Sliding windows of data-audit records per (fs, user, client_ip).

    observe() takes a purefb_file_event payload and returns a
    purefb_file_activity payload the first time a count in the window
    reaches its threshold, then nothing more for that key until a whole
    window has passed or a different threshold trips (a rename storm that
    turns into a mass delete is two events), so a storm is one event and a
    steady state is none. Records are placed by their own `time`; a record
    without a readable time is placed at arrival.
    """

    def __init__(
        self,
        window_seconds=DEFAULT_FILE_WINDOW_SECONDS,
        mass_delete_threshold=DEFAULT_MASS_DELETE_THRESHOLD,
        rename_storm_threshold=DEFAULT_RENAME_STORM_THRESHOLD,
    ):
        self.window = float(window_seconds)
        self.mass_delete = int(mass_delete_threshold)
        self.rename_storm = int(rename_storm_threshold)
        self.keys = {}
        self.emitted = {}

    def observe(self, record):
        key = (record.get("fs"), record.get("user"), record.get("client_ip"))
        ts = _epoch(record.get("time"))
        if ts is None:
            ts = time.time()
        window = self.keys.setdefault(key, collections.deque())
        window.append((ts, _op_kind(record.get("op_name")), record.get("path"), record))
        cutoff = ts - self.window
        while window and window[0][0] < cutoff:
            window.popleft()
        created = set()
        deletes = renames = pairs = 0
        for _, op, path, rec in window:
            if op == "create":
                created.add(path)
            elif op == "delete":
                deletes += 1
                if path in created:
                    pairs += 1
            elif op == "rename" and _extension(path) != _extension(rec.get("tgt_path")):
                renames += 1
        mass = deletes >= self.mass_delete or pairs >= self.mass_delete
        storm = renames >= self.rename_storm
        if not (mass or storm):
            return None
        flags = {f for f, on in (("mass_delete", mass), ("rename_storm", storm)) if on}
        last = self.emitted.get(key)
        if last is not None and ts - last[0] < self.window and flags <= last[1]:
            return None
        self.emitted[key] = (ts, flags)
        return {
            "array": record.get("array"),
            "fs": key[0],
            "user": key[1],
            "client_ip": key[2],
            "proto": record.get("proto"),
            "window_seconds": self.window,
            "events": len(window),
            "deletes": deletes,
            "renames": renames,
            "create_delete_pairs": pairs,
            "mass_delete": mass,
            "rename_storm": storm,
            "mass_delete_threshold": self.mass_delete,
            "rename_storm_threshold": self.rename_storm,
            "first_time": window[0][3].get("time"),
            "last_time": record.get("time"),
            "sample_paths": [p for _, _, p, _ in list(window)[-SAMPLE_PATHS:]],
        }


def _emitter(local, hostnames, automation, networks, programs=None, windows=None):
    """Build the synchronous emit(line, source, protocol) callback: filter the
    sender, parse, keep only the wanted programs, and hand the event to the
    local queue (unbounded, so put_nowait never fails and the UDP callback
    never blocks). `programs` None means the Purity programs; [] means all.
    `windows` is an ActivityWindows (None turns the tallies off)."""
    wanted = list(DEFAULT_PROGRAMS) if programs is None else list(programs)
    dropped = {}

    def emit(line, source, protocol):
        if not _source_allowed(source, networks):
            logger.debug(
                "purefb_syslog: dropped %s line from %s (allowed_sources)",
                protocol,
                source,
            )
            return False
        payload = parse(line, hostnames, automation)
        program = payload["program"] or "(no syslog header)"
        if payload["kind"] != "file" and wanted and program not in wanted:
            n = dropped[program] = dropped.get(program, 0) + 1
            if n == 1:
                logger.info(
                    "purefb_syslog: dropping lines of program %s from %s (not in programs); counting from now on",
                    program,
                    source,
                )
            elif n % DROP_LOG_EVERY == 0:
                logger.debug(
                    "purefb_syslog: %d lines of program %s dropped", n, program
                )
            return False
        payload["source"] = source
        payload["protocol"] = protocol
        local.put_nowait({event_name(payload): payload})
        if payload["kind"] == "file" and windows is not None:
            activity = windows.observe(payload)
            if activity:
                activity["source"] = source
                activity["protocol"] = protocol
                local.put_nowait({"purefb_file_activity": activity})
        return True

    return emit


class _UDP(asyncio.DatagramProtocol):
    def __init__(self, emit):
        self.emit = emit

    def datagram_received(self, data, addr):
        for line in data.decode("utf-8", "replace").splitlines():
            if line.strip():
                self.emit(line, addr[0], "udp")


async def _tcp_client(reader, writer, emit, networks):
    peer = writer.get_extra_info("peername")
    source = peer[0] if peer else None
    try:
        if not _source_allowed(source, networks):
            logger.debug(
                "purefb_syslog: refused tcp connection from %s (allowed_sources)",
                source,
            )
            return
        while not reader.at_eof():
            try:
                raw = await reader.readline()
            except (ValueError, asyncio.LimitOverrunError):
                # readline raises ValueError once a line exceeds the limit
                logger.warning(
                    "purefb_syslog: line from %s longer than %d bytes; closing",
                    source,
                    TCP_LINE_LIMIT,
                )
                return
            if not raw:
                break
            line = raw.decode("utf-8", "replace")
            if line.strip():
                emit(line, source, "tcp")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


def _decode_mapping(value, plugin):
    """The hostnames argument as the mapping the plugin documents.

    A platform credential injects the mapping as one string variable, so a
    JSON string, or a YAML string, is decoded here before the argument is
    validated. The text is never echoed into an error message or a log
    record: a credential may carry secrets alongside it.
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
        f"{plugin}: hostnames is a string that does not decode as JSON (or YAML) "
        "into a mapping; the text is not shown because it may hold secrets"
    )


async def main(queue: asyncio.Queue, args: dict) -> None:
    host = args.get("host") or "0.0.0.0"
    port = int(args.get("port", 5140))
    protocols = _as_list(args.get("protocols")) or ["udp", "tcp"]
    programs = _programs(args.get("programs"))
    hostnames = _decode_mapping(args.get("hostnames"), "purefb_syslog") or {}
    if not isinstance(hostnames, dict):
        raise ValueError(
            "purefb_syslog: hostnames must be a mapping of array name or syslog "
            "hostname -> inventory name, not the purefb_events array list"
        )
    automation = set(_as_list(args.get("automation_users")))
    networks = _networks(args.get("allowed_sources"))
    windows = ActivityWindows(
        args.get("file_window_seconds", DEFAULT_FILE_WINDOW_SECONDS),
        args.get("mass_delete_threshold", DEFAULT_MASS_DELETE_THRESHOLD),
        args.get("rename_storm_threshold", DEFAULT_RENAME_STORM_THRESHOLD),
    )
    loop = asyncio.get_running_loop()

    # One local unbounded queue and one forwarder task: events keep their
    # order, there is one task instead of one per line, and ansible-rulebook's
    # own (one-slot) queue applies back-pressure to the forwarder only.
    local: asyncio.Queue = asyncio.Queue()
    emit = _emitter(local, hostnames, automation, networks, programs, windows)

    async def forward():
        while True:
            await queue.put(await local.get())

    forwarder = asyncio.create_task(forward())
    servers = []
    if "udp" in protocols:
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _UDP(emit), local_addr=(host, port)
        )
        servers.append(transport)
    if "tcp" in protocols:
        srv = await asyncio.start_server(
            lambda r, w: _tcp_client(r, w, emit, networks),
            host,
            port,
            limit=TCP_LINE_LIMIT,
        )
        servers.append(srv)
    logger.info(
        "purefb_syslog: listening on %s:%s (%s)%s, programs %s, file window %ss "
        "(mass delete %d, rename storm %d)",
        host,
        port,
        ",".join(protocols),
        f", sources {[str(n) for n in networks]}" if networks else ", any source",
        programs or "all",
        windows.window,
        windows.mass_delete,
        windows.rename_storm,
    )
    try:
        await asyncio.Event().wait()
    finally:
        forwarder.cancel()
        for s in servers:
            s.close()


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    if len(sys.argv) > 1 and sys.argv[1] == "parse":
        for line in sys.stdin:
            payload = parse(line, {}, set())
            print(json.dumps({event_name(payload): payload}))
        sys.exit(0)

    class _Q:
        async def put(self, event):
            print(json.dumps(event), flush=True)

    asyncio.run(main(_Q(), {"port": int(sys.argv[1]) if len(sys.argv) > 1 else 5140}))
