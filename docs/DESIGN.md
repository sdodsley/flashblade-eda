# Design

Why a FlashBlade is an event source, what it emits, how events reach
ansible-rulebook, the ideas each rulebook implements, and what waits for a
later part or a newer Purity//FB.

Versions this design assumes: py-pure-client 1.98.0 (FB_2_0 to FB_2_30; the
lab array serves REST 2.21), Purity//FB 4.6.4 on a FlashBlade//S200,
everpure.flashblade 1.29.0, ansible-rulebook 1.3.x and ansible.eda 2.13.
Anything said about 4.8.x or 4.10 comes from release documentation and is
unverified on hardware.

## 1. Thesis

The FlashArray project turned the array's alert log, audit log and
performance history into rulebook events and acted through the collection.
FlashBlade has all three, and four things FlashArray does not:

- **Data-plane events.** File-access audit records (user, client IP, path,
  operation, result) over syslog, and CloudTrail-style S3 records (access
  key, source IP, event name, error) written as objects into a bucket on
  the array. From 4.10 the same S3 records go to Kafka, which
  `ansible.eda.kafka` already consumes.
- **Identity-shaped actuators.** An access key can be disabled, an SMB
  session terminated, a lock broken, one client downgraded to read-only in
  an ordered export rule, a file system or bucket throttled by a QoS
  policy, a path put under legal hold. FlashArray could only snapshot and
  page.
- **Push without an exporter.** Native OTLP telemetry push means no scrape
  credential and no exporter container; management and data-audit syslog
  arrive in seconds.
- **Tenants with numbers.** User, group, file-system, bucket and account
  quotas, with alert codes (1101, 1106, 1107) the array already e-mails
  about, plus realms and per-client performance.

What FlashBlade lacks shapes the design too: no tags on file systems or
buckets (state cannot live on the object), no `/alerts/rules` thresholds,
no `/alerts/events` (alerts are diffed locally), one array-wide
maintenance window instead of two kinds, and alert state is open or
closed.

## 2. What the lab shows

A FlashBlade//S200 on Purity//FB 4.6.4 (REST 2.21).
"Now" is demonstrable on that array; "later" needs a newer Purity//FB or a
lab change; "blocked" is not possible there.

| Capability | Status | Evidence |
| --- | --- | --- |
| Alerts with action text and KB link | now | 185 alerts, 6 open (four 1112 object-replication connections, one 1140 superuser password login); fields `action`, `knowledge_base_url`, `flagged`, `state`, `variables` |
| Attributable audits and sessions | now | 6683 audits with `user`, `user_agent` (`pure/py-pure-client/1.96.0`), `ip_address`, `command`, `subcommand`; 116 sessions with `location` and `method` |
| Maintenance windows | now | `/maintenance-windows` answers (none active); one array window, 1 to 48 h, since 4.5.2 |
| Hardware identify LED, blades, DFMs, LAG | now | 95 hardware items with `identify_enabled`; 10 blades; 40 DFMs; LAG `uplink`, 8 x 100 GbE, healthy |
| LLDP neighbours | now | `CH1.FM1.ETH1` to a Cisco Nexus switch port `Ethernet1/13`, switch management address exposed |
| Space and performance history | now | 7 daily points with `resolution=86400000` by type; 6 half-hour performance points; per-protocol NFS metadata rates |
| Per-client performance | now | `/arrays/clients/performance` answers (empty while idle; a load from the client VM fills it) |
| Object audit to a log bucket | now | policy, log-target and filter endpoints answer; 43 S3 actions available for filters; with the lab account, buckets, policy, log target and filters in place the records read back as NDJSON log objects, one per authority per rotation window, written seconds after the window closes ([HARDWARE_NOTES.md](HARDWARE_NOTES.md), "Object audit") |
| OTLP telemetry push | now | REST 2.21 serves `/telemetry-targets` and 12 built-in metric policies with 35 rules; the SDK names these methods from FB_2_29, so the two telemetry modules (`purefb_telemetry_target`, `purefb_telemetry_policy`) speak REST; the array opens a TCP connection to the endpoint when the target is created and refuses one nothing listens on |
| Management syslog push | now | a syslog server created with `purefb_syslog` delivers `purity.audit` lines to the listener within seconds |
| Legal holds, QoS policies, Object Lock, public-access config | now | endpoints and models present on 2.21 |
| SMB file-access audit | blocked | needs an AD-joined SMB file system with SACLs; the lab has no Active Directory join |
| NFS file-access audit | later | Purity//FB 4.8.0 (control type `policy`); 4.6.4 cannot audit NFS and blocks NFS mounts on an SMB-audited file system |
| File log targets (audit written into a file system) | blocked | 400 "Operation not permitted": directed availability on 4.6.x |
| Kafka object notifications | later | Purity//FB 4.10.a (REST 2.32), above the installed SDK ceiling of 2.30 |
| Per-rule telemetry resolution | later | 4.8.6 |

The production file system on the lab array is a Manila share on legacy
NFS rules with no export policy, and the NFS client VM also hosts the
Prometheus OTLP receiver. Every export-policy, quarantine and lock demo
uses a throwaway file system; nothing isolates the VM.

## 3. Transports

| Transport | Min Purity//FB | Latency | Customer configures | Consumer |
| --- | --- | --- | --- | --- |
| REST pull | any 4.x; maintenance gate 4.5.2; LLDP and object audit 4.6.3 (REST 2.20) | poll interval | a read-only API token | `purefb_events` |
| Management syslog | 3.1.0 | sub-second | a syslog server with services `[management]` to the EDA host on 5140 | `purefb_syslog` |
| Data-audit syslog | SMB 4.4/4.5 (SACL, AD); NFS 4.8.0 | seconds | a syslog server with services `[data-audit]`; an audit policy per file system (no module yet); rsyslog tap with 128 KB messages | `purefb_syslog` JSON branch |
| Object-audit log bucket | 4.6.3 (REST 2.20) | one rotation (5 min on the lab) plus seconds | an account, a log bucket with Object Lock and quota headroom, a policy, a log target, prefix filters (the three modules under `plugins/modules/`), a read-only key | `purefb_object_audit` (boto3) |
| Kafka notifications | 4.10.a (REST 2.32) | under 5 s normally | a Kafka log target and routing info | `ansible.eda.kafka` (needs aiokafka) |
| OTLP to Prometheus to Alertmanager | 4.6.4; per-rule resolution 4.8.6 | 60 s for the device performance, space and connector policies, 10 min for the rest, plus rule evaluation | a telemetry target attached to the metric policies (`purefb_telemetry_target`, `purefb_telemetry_policy`, `telemetry_setup.yml`), Prometheus 3.x with the OTLP receiver and the resource attributes promoted, Alertmanager routed to the EDA listener (`prometheus/`) | `ansible.eda.alertmanager` (`alertmanager.yml`) |
| SNMP | any | seconds | `purefb_snmp_mgr` | none in ansible.eda; adds nothing over syslog, not built |

The diagram at the top of [README.md](../README.md) shows the paths;
[The source plugins](../README.md#the-source-plugins) lists the signals,
endpoints and events of the three plugins.

### Source plugins

| Plugin | Signals and events | Reuse from flasharray-eda |
| --- | --- | --- |
| `purefb_events` | alerts (local diff, transitions initial / opened / updated / closed); audits (time cursor, `by_automation` from `automation_users` plus the `user_agent` prefix, `destructive` from the subcommand); sessions; health (hardware, blade progress and target, DFM drives, LAG speed against its ports, an LLDP neighbour cache refreshed only while healthy); telemetry (space by type with 7-day history, performance against the same half hour last week, per client, per file system and bucket, per-file-system quota usage); config; posture (public buckets, buckets outside every enabled audit policy, world-open export rules and legacy `nfs.rules`, keys older than `key_max_age_days`, certificates inside `cert_warn_days`, everyone-full-control share policies, each read optional so an array without the endpoint reports an empty list); replication (array connections, S3 targets, file-system and bucket replica links in one `purefb_replication` summary per poll, a `purefb_replica_link` per link with an issue and once on recovery with the streak and `escalate`, a `purefb_array_connection` on every status change). Every event but the telemetry ones carries `maintenance_active`, false where the endpoint is absent | the `purefa_events` skeleton as is: one asyncio task per signal, an Array class with a lock and a thread pool, poll-error streaks, the cached maintenance read, `request_timeout`, credentials from the environment |
| `purefb_syslog` | the FlashBlade layouts `purity.alert: <SEVERITY> <State> - <array>: <subject> [<code>]` and `purity.audit: ... User=, Location=, UI=, Command=, Subcommand=, Arguments=`; a JSON branch for data-audit records keyed on `pf` `fb` or `fa` (one parser for both platforms); sliding windows per (fs, user, client_ip) emitting `purefb_file_activity` on a mass delete or a rename storm (SMB audit now, NFS audit from 4.8.0) | transport, header regexes, allow-list and emitter; the parsers rewritten; the TCP line limit raised from 16 KB to 128 KB |
| `purefb_object_audit` | boto3 against the data VIP, `list_objects_v2` with `Prefix` and `StartAfter` on the log keys, whose lexical order is time order (`<array id>/<prefix>/<yyyy>/<mm>/<dd>/<HHMMSS>.A<authority>.<sequence>.log`), re-listing the last `late_windows` windows for late authorities; one `purefb_s3_event` per NDJSON record with the derived booleans (canary, landing, evidence, honey, approval, request, denied, delete, `by_automation`), one `purefb_s3_rollup` per log object (counts by event name, access key, source address and error code; the storm and mass-delete flags), one `purefb_audit_health` per poll (log age, quota, policy coverage); suffix, canary and honey matching in the plugin because bucket audit filters only take actions and prefixes; the cursor in a file and, optionally, in the state bucket | new; shares one event filter with `ansible.eda.kafka` so the rulebooks do not change at 4.10 |

## 4. Ideas by domain

Each bullet names the rulebook or playbook that implements it, or marks it
design intent.

### 4.1 Change control from the audit record

- The rule fires on the audit record itself, not on the next poll
  (`change_control.yml`, audits signal). A FlashBlade audit record carries
  `user_agent`, so `pure/py-pure-client` is reported as `automation_agent`
  beside the `automation_users` list; the list stays, because the Manila
  driver and a QA harness use the same client library.
- Governed objects and an automation service account: a governed command
  (`governed_commands`: `puredns`, `pureds`, `purearray`, `pureadmin`,
  `puresmtp`, `puresyslog`, `puresnmp`, `purealert`, `purepolicy`,
  `purelog`, `puremaintenance`) by any identity not in `automation_users`
  is reconciled from the baseline files for NTP and DNS
  (`playbooks/reconcile.yml`) and reported with user, address, interface
  and command for everything else; a destructive subcommand
  (`destructive_subcommands`: destroy, eradicate, delete, remove,
  disconnect, demote) by a human is reported; the automation's own changes
  are logged.
- Git webhook source: a signed push applies the baseline forward
  (`apply_baseline.yml`); the audit stream reconciles backward.
- Poll-based drift as the fallback (`config_drift.yml`): NTP and DNS
  reapplied through `purefb_ntp` and `purefb_dns`; the management
  directory service, eradication (`manual_eradication`, an
  `eradication_delay_ms` shorter than the baseline minimum), the admin
  lockout and password settings, SSO, an unexpected Active Directory join
  and a missing `data-audit` syslog target (`require_data_audit_syslog`)
  notified. The wider posture score (export, share and client rules,
  public buckets, key age, certificate expiry) is `posture.yml` on the
  `posture` signal: a public bucket has its public access
  blocked behind `posture_auto_revert` (`posture_revert.yml`), everything
  else notifies once a day per array.

### 4.2 Let the array hold the baseline

- Performance abnormality is the current sample against the same half hour
  last week from `/arrays/performance` with `resolution=1800000`
  (`client.yml`, array write latency). No TSDB needed.
- Top-N clients, file systems and buckets via REST `sort` and `limit`, not
  pagination; a file system whose QoS policy starts with `eda-cap-` is
  always included whatever its rank.
- Capacity: days-to-full from the array's own 7-day space history
  (`resolution=86400000`, no `limit`) with the same call split by
  `type=file-system` and `type=object-store` to say which side is growing;
  top growers from per-object space deltas across polls; `capacity.yml`
  compares used % with `capacity_risk_pct`.
- There are no `/alerts/rules` on FlashBlade, so every threshold lives in
  the vars file or in `prometheus/flashblade.rules.yml`; the
  array's own thresholds are the quota alerts (1101 at 80, 90 and 100 %).

### 4.3 Hardware events get hands and a switch port

- A hardware component whose status is an issue lights the identify LED
  (`purefb_hardware`, `identify_led.yml`) and notifies, logged only inside
  a maintenance window; the record carries the component's LLDP neighbour
  (`neighbor_switch`, `neighbor_port`), read from
  `/network-interfaces/neighbors` into a cache that is refreshed only
  while the port is healthy, so a down link still names the switch port
  it was on. Blade issues carry evacuation `progress` and `target`; drive
  issues are DFMs.
- A LAG is degraded when its status is not healthy or its `lag_speed` is
  under the sum of its healthy member ports (`/hardware-connectors`
  `port_speed`, or `port_speed * len(ports)`); `purefb_lag` carries the
  member ports with their switch ports and the health rulebook pages at
  critical. Read-only `show` commands on the switch through a network
  collection are design intent.
- Opened alerts (`alerts.yml`) route through the alert-code runbook map
  (`alert_runbooks`, keys are FlashBlade codes as strings) to notify,
  ticket, or ticket plus per-code tasks under
  `playbooks/runbooks/<code>.yml`, carrying the array's own action text
  and knowledge base link; unknown codes take the default entry. The
  lifecycle is opened, updated (routed again when `severity_raised`),
  closing (the array parks a resolved alert there first; logged) and
  closed (notified at info with `resolved_after_s`). The handled marker is
  the alert's own `flagged`
  bit (`flag_alert.yml`, behind `alert_flag_handled`); a human flagging an
  alert in the GUI reads as handled, which is the intent.
- Time-boxed remote assist (`purefb_ra`): design intent; `health.yml`
  logs remote-assist state.

### 4.4 Capacity turns into trend, approval and a quota ladder

- Days-to-full from the space history; top growers from file system and
  bucket space deltas; what is pending eradication (file systems, buckets,
  snapshots, from `destroyed=True` queries) as the reclaim candidates.
- Every destructive action sits behind an approval. Standalone, a capacity
  breach writes a request file (`capacity_request.yml`: request id
  `<array>-<timestamp>`, the days-to-full estimate, the top growers, what
  is pending eradication) and a webhook ruleset takes `{request_id,
  approve, approver, action}` under a bearer token; only an approved
  request whose action was proposed runs, and `eradicate_pending`
  (`capacity_reclaim.yml`: `purefb_fs`, `purefb_bucket` and `purefb_snap`
  with `state=absent eradicate=true`) eradicates destroyed objects,
  reporting anything the array refuses. `orchestrator_enabled` hands the
  event to the automation orchestrator instead (`orchestrator_post.yml`,
  unchanged from the FlashArray project).
- The quota ladder the array advertises. Alert 1101 (a file system at 80,
  90 or 100 % of its quota) is routed by the runbook map; the
  deterministic path is `purefb_fs_quota` from the telemetry signal: a
  hard-limited file system at `fs_quota_grow_pct` of `provisioned` grows
  by `fs_grow_step_pct` (`grow_fs.yml`, `purefb_fs size=`) up to
  `fs_grow_max_bytes` behind `fs_auto_grow`, and above the cap raises a
  capacity request; names matching `fs_grow_exclude` (the Manila shares
  by default) only notify. Alerts 1106 and 1107 (a user or group quota)
  already e-mail the user; `quota_user.yml` reads that file system's quota
  and usage (`/quotas/users` and `/usage/users` need a file system
  identifier, so they are read on the alert, never polled) and, behind
  `auto_raise_user_quota`, raises the quota by `user_quota_step_pct` up to
  `user_quota_max_bytes` (`purefb_userquota`, `purefb_groupquota`).
  Pointing `quotas/settings` `contact` at a self-service queue so the
  array's own e-mail carries the link is design intent.
- Snapshot sprawl and versioned buckets without a lifecycle rule as
  reclaim candidates: roadmap. The ledger is capacity's record:
  `showback.yml` writes one object a day per array
  (`<showback_prefix><date>/<array>.json`: every file system's virtual
  and provisioned space, every bucket's space, object count and quota,
  the accounts, the totals) to the state bucket, so a month of usage by
  tenant is a listing, not a query.

### 4.5 A client IP is an Ansible host

- `/arrays/clients/performance` names the consumer: an NFS or SMB client
  address with its ops, bandwidth and latency. When array write latency is
  `baseline_latency_ratio` times last week's same half hour and one client
  holds `client_min_share_pct` of the array's ops for
  `client_consecutive_polls`, `client_diagnose.yml` maps the address to an
  inventory host by `ansible_host` and, behind `client_actions_enabled`,
  runs read-only diagnostics there (mounts, `nconnect`, the top I/O
  processes); an unknown address notifies.
- The QoS cap with hysteresis, on a file system this time: a file system
  holding `qos_cap_min_share_pct` under the same array latency condition
  gets a policy `eda-cap-<fs>` with `max_total_ops_per_sec` at
  `qos_cap_ops_per_sec` (`qos_cap.yml`: `post_qos_policies` through the
  raw REST task, assigned with `patch_file_systems qos_policy` or the
  module's option where one exists), behind `qos_auto_cap`; `qos_lift.yml`
  removes it after `qos_lift_normal_polls` normal polls. The policy name
  is the state, read back as `eda_capped`.
- The ordered export rule that downgrades one client to read-only, the
  `eda-tarpit` policy and the session and lock termination are
  `contain.yml`. A client at `contain_min_share_pct` of the
  array's ops for `contain_consecutive_polls` polls under the same
  latency condition is quarantined behind `ir_auto_contain`
  (`quarantine_client.yml`): on an export policy a rule `client <ip>
  permission ro access root-squash` is posted before the policy's first
  rule (`post_nfs_export_policies_rules` with `before_rule_name`), on
  the legacy `nfs.rules` string the client's `rw` entry is rewritten
  `ro`; the rule is the state and `release` removes or rewrites it. A
  client in `contain_exclude_clients` or at one of the array's own
  addresses is never quarantined and `contain_exclude_file_systems` (the
  Manila shares) are never touched. A file system hogging the array with
  no cap on it gets the one policy `eda-tarpit` at `tarpit_ops_per_sec`
  behind `ir_auto_tarpit` (`tarpit.yml`, `purefb_qos`), lifted after
  `contain_release_normal_polls` calm polls; a file system that already
  carries a policy of its own is recorded, not re-assigned. A tarpit
  slows every client of that file system, so it is the fallback, not the
  first step. Breaking a dead client's sessions and locks
  (`release_locks.yml`, `purefb_fs_session`) has no switch: the webhook
  ruleset of `contain.yml` on `locks_port` is its only entry, and the
  same webhook quarantines, releases, tarpits and lifts by hand. There is
  no separate `locks.yml`.

### 4.6 Sessions and identity

- `sessions.yml`: a login whose `location` is outside `admin_cidrs`
  notifies at warning; repeated session events other than login and logout
  (failed attempts) notify; alerts 1140 (superuser login with a password,
  the network-access policy's concern) and 1123 (the default `pureuser`
  password) are routed as they open or update. A 14-day learned location
  baseline, API-token rotation and `clear_lock` behind approval are design
  intent; humans are never auto-unlocked.

### 4.7 The data path

What the lab array shows: the log objects are NDJSON, one
object per authority (blade partition) per rotation window, written
seconds after the window closes, nothing written for an idle window, and
the keys sort in time order; 5 minutes is the shortest rotation the array
accepts. The details are in [HARDWARE_NOTES.md](HARDWARE_NOTES.md),
"Object audit". The rules below are `object_audit.yml`; the plugin emits
the booleans and the rulebook decides.

- One GET disables the key: a canary prefix with an audit filter on
  `s3:GetObject` (the lab filter is `s3:All` on the data bucket and the
  plugin does the prefix match) means the array records the hit; any
  reader not in `canary_readers` and not an automation key gets its key
  disabled (`canary.yml`, `PATCH /object-store-access-keys enabled false`)
  behind `canary_auto_disable_key`, else a critical notification: a
  reversible brake on the identity, not the data. Latency is one log
  rotation (5 min) plus seconds until Kafka at 4.10, so this is a tripwire
  and a record, not a race. A key that touches a honey suffix (`.secret`,
  `.key`, `.pem`) notifies at critical.
- Approve by PutObject: with `s3_requests_enabled` the request is also an
  object under `requests/` in the Object-Locked log bucket and the
  approval is a PutObject under `approvals/`; the approver's PutObject is
  itself audited (key, address, time), so the array's own record is the
  provenance, and `s3_approval.yml` refuses a writer key outside
  `approval_keys` or inside `automation_keys`, a request not awaiting
  approval and an action the request did not propose, then leaves a
  receipt under `receipts/`. The bearer token and the file approval stay.
- The rule that protects the rule (`auditor_guard.yml`): a bucket without
  an enabled audit policy, a disabled policy or a member without a filter
  (`purefb_audit_health`), the log bucket at `log_bucket_quota_warn_pct`,
  no log object for `stale_rotations` rotations where traffic is expected
  (`audit_log_expected_continuous`), alert 1139 (the runbook map), or a
  human `purepolicy`, `purelog` or `purebucket` change to the audit
  configuration (audits signal): re-attach from `audit_baseline` with the
  three modules behind `audit_auto_reattach`, raise the quota by
  `log_bucket_quota_step_pct` within `log_bucket_quota_max_bytes`, and
  always a critical record naming the admin when there is one. Dropped
  audit records are unrecoverable, so this ships with the first data-path
  rule.
- Storms from the rollup: `denied_storm_count` access-denied records, or
  an error rate at `error_rate_pct` over `error_min_count` records, in one
  log object notify and, behind `anomaly_auto_disable_key`, disable the
  top key (`s3_anomaly.yml`); `mass_delete_count` deletes notify.
- Landing manifest starts the job: a PutObject of `_SUCCESS` or
  `manifest.json` under a dataset prefix runs `landing_command` with the
  dataset, writer and source address in its environment (`landing.yml`;
  empty = record only); a PutObject under `evidence/` gets a legal hold
  the array audits back (`lock_on_arrival.yml`, `put_object_legal_hold`
  with the state key, behind `legal_hold_on_arrival`, on by default
  because a hold only adds protection).
- Honeyfiles, an inbox folder and access storms over NFS file audit wait
  for 4.8.0.

### 4.8 Suppression and a dead man's switch

- The array's one maintenance window is stamped on every alert, audit,
  session, health, hardware and config event as `maintenance_active`,
  from one cached `/maintenance-windows` read per array; the gate reads
  false where the endpoint is absent (REST < 2.16). `maintenance.yml`
  opens, extends (a second POST) and closes it from a bearer-token webhook
  carrying `{array, action, hours}` (`maintenance_window.yml`, raw REST,
  timeout in milliseconds), and logs a window a human opened. The array's
  exemption list is fixed (1016, 1026, 1101 to 1104, 1106 to 1110, 1123,
  1130), so those codes route inside a window; 1139 and 1140 are
  suppressed by the array during one.
- Poll failures arrive as `purefb_poll_error` with a consecutive count and
  `escalate` at `poll_error_escalate` (default 10); the health rulebook
  pages at warning on the first failure of a streak, the others record
  theirs at info, and every rulebook pages at critical once a streak
  escalates.
- Silence is the signal: with OTLP push there is no `up == 0`.
  `tools/telemetry_rules.py` writes one `FlashBladeTelemetrySilent` rule
  per metric policy, `absent_over_time` of the policy's first metric over
  twice its resolution (or `telemetry_silence_minutes`, whichever is
  longer), and `alertmanager.yml` answers it with `telemetry_check.yml`,
  which asks the array for its own delivery test (`GET
  /telemetry-targets/test`), the management VIP for `/api/api_version`
  and Prometheus for `up`, and records a `verdict`: `pipeline` (array and
  receiver answer, the test failed), `array` (the management VIP is
  silent, critical), `receiver` (Prometheus is silent) or `healthy` (the
  test delivered, the silence was transient). `selftest.yml` repeats the
  cheap half of that every hour from `housekeeping.yml`.
- Housekeeping on a tick: `housekeeping.yml` runs
  `selftest.yml` every `housekeeping_interval` (REST, the state bucket,
  the audit policy, the telemetry target, Prometheus; one record, critical
  when a check fails), `showback.yml` once a day (the ledger decides by
  the date) and `dr_drill.yml` at `dr_drill_hour` (a marker object in the
  state bucket stops a second run the same day).

### 4.9 The record, the pager and the approver

- The alert log is the record: every playbook appends one JSON line with
  `time`, `kind`, `severity` and `array`, whatever else happens.
- Slack is the pager. `notify.yml` posts the same record to an incoming
  webhook when `SLACK_WEBHOOK_URL` is set, and a failed post never fails
  the notification.
- A pager is not an approver. The three approval paths of the FlashArray
  project (request file plus bearer webhook, a platform workflow approval
  node, the automation orchestrator) carry over unchanged; the S3 inbox
  (`s3_approval.yml`, behind `s3_requests_enabled`) is the fourth.

## 5. State without tags

- On the array first. The alert `flagged` bit as the handled marker (raw
  `PATCH /alerts`); a key's `enabled` state; QoS policy names
  `eda-cap-<fs>` and `eda-tarpit`; an export rule `client == <ip>,
  permission ro` meaning quarantined; a file system's `provisioned`;
  legal holds named `eda-ir-*`; the maintenance window itself.
- Two buckets, not one. `eda-state` (versioning only, lifecycle
  pruning) for the tail's cursor copy at `cursors/object_audit/<array>.json`,
  the showback ledger under `showback/<date>/<array>.json`
  and the DR drill markers under `showback/dr/<date>-<file system>.json`;
  `eda-log` (versioning plus Object Lock, governance mode and 90-day
  retention for the prototype, 1 day on the lab, `retention_lock`
  unlocked, a soft quota) for the audit log objects, requests, approvals
  and receipts and, later, the alert log and the ledger. A cursor
  rewritten every poll inside an Object-Locked bucket would pile up
  unprunable versions, which is why the stores are split. The third
  bucket, `eda-data`, is the demo's data: Object-Locked without a default
  retention so single objects can be held.
- The audit log is the restart-safe record of what the automation did
  last. Streaks stay in plugin memory and may be lost; the JSONL alert
  log, `request_dir` and the webhooks remain the standalone path.

## 6. Gates and safety

- `maintenance_active` on the event: carried by alert, audit, session,
  health, hardware and config events, not by telemetry events, so the file
  system grow, the client diagnostics and the QoS cap run inside a window.
- `automation_users` and `automation_agent`, so the audit-driven rules
  never react to the automation's own changes.
- Every data-path write defaults to notify: `fs_auto_grow`,
  `auto_raise_user_quota`, `client_actions_enabled`, `qos_auto_cap`,
  `canary_auto_disable_key`, `anomaly_auto_disable_key`,
  `audit_auto_reattach`, `posture_auto_revert` (`legal_hold_on_arrival`
  defaults to on: a hold only adds protection and the array audits it),
  `ir_auto_contain`, `ir_auto_tarpit`, `replication_auto_resume` and
  `certs_auto_renew`; `replication_auto_create` and `telemetry_adaptive`
  are roadmap. Lock breaking
  has no switch, only approval (the webhook on `locks_port`); the
  eradication module refuses to shorten the delay or re-enable manual
  eradication without `force`, and the lab only runs it in check mode.
  On S3 events `automation_keys` (the tail and state keys by default) sets
  `by_automation`, as `automation_users` does on audits. Approvals are always
  required for eradication, key deletion, lock breaking, promote and
  demote, retention or SafeMode ratchets, the eradication delay,
  production export changes and software windows.
- False-positive controls on the data path: allow-lists for automation
  keys, replication credentials, the plugin's own key, backup, antivirus
  and indexer identities and hosts, and scratch file systems that may stay
  world-open; two-window hysteresis and streaks; quarantine is read-only,
  never a block, with a concurrent-quarantine cap. Nothing is ever
  shortened; the Manila shares and the `eda` buckets are never touched.
- Two credentials: a read-only token for the plugins and a write
  credential for the playbooks; daily operations never run as
  `array_admin` ([SECURITY.md](../SECURITY.md)).

## 7. Collection gaps

Modules `everpure.flashblade` needs, each a pull request in its own right.
Already sufficient: `purefb_s3user keystate`, `purefb_bucket` Object Lock
options, `purefb_syslog services`, `purefb_bucket_replica paused`,
`purefb_user clear_lock` and `api`, `purefb_hardware enabled`,
`purefb_pingtrace`, `purefb_certs state=sign`, `purefb_s3acc` quota and
public-access options, `purefb_fs promote`. To verify on an array:
`purefb_policy` rule ordering with `before_rule_name`. The first eight
rows of the table are built in this repository under `plugins/modules/`
(Apache-2.0 here, the collection's licence on the move:
[PACKAGING.md](PACKAGING.md)) until the collection takes them; the file
audit policies of 4.8, the quota settings, the software check and the
lifecycle additions are not. The telemetry pair and the alert flag speak
REST because the FB_2_21 client lacks the methods; `purefb_qos` and
`purefb_legal_hold` fall back to REST for the one call each the SDK has
no method for (clearing a policy from an object, removing a held entity).

| Module | Endpoints | Why EDA needs it |
| --- | --- | --- |
| `purefb_maintenance` | `post/delete_maintenance_windows` (`names=['array']`, timeout in ms) | the universal gate; ticket-driven windows; NDU-style extension |
| `purefb_audit_policy`, `purefb_log_target`, `purefb_bucket_audit_filter` | `post/patch_audit_object_store_policies` and members; `post_log_targets_object_store` `{bucket, log_name_prefix, log_rotate.duration}`; `post_buckets_audit_filters` `{actions, s3_prefixes}`; `post_audit_file_systems_policies` `{control_type, rules[{op_status, operations}], log_targets}` and members | every object-audit idea, the coverage re-attach, file audit at 4.8 |
| `purefb_telemetry_target`, `purefb_telemetry_policy` | `post/patch_telemetry_targets`, `get_telemetry_targets_test`, `patch_telemetry_metrics_policies` and rules | the zero-code path; REST inside the modules on 2.21 until the FB_2_29 client is the floor |
| `purefb_qos` plus `qos_policy` on `purefb_fs` and `purefb_bucket` | `post/patch/delete_qos_policies` (`max_total_ops_per_sec` 100 and up), members, `patch_file_systems qos_policy` | cap and tarpit; today only `purefb_realm` has a QoS option |
| `purefb_fs_session` | `delete_file_systems_sessions` (client, user, protocols, disruptive), `delete_file_systems_locks` (client, file system, paths) | quarantine kill, lock reaper |
| `purefb_legal_hold` | `post_legal_holds`, `post_legal_holds_held_entities` (file systems, paths, recursive) | the immutability ladder, lock-on-arrival for files |
| `purefb_alert_flag` | `patch_alerts flagged` | the handled marker that replaces the `eda/*` tags |
| `purefb_eradication` | `patch_arrays eradication_config` (`eradication_delay` whole days to 30, `manual_eradication`) | protect-now; only `purefb_info` reads it today |
| `purefb_quota_settings` | `patch_quotas_settings` `{contact, direct_notifications_enabled}` | self-service advertised by the array's own e-mail |
| `purefb_software_check` | `post_software_check` (names, versions), `get_software_check` | pre-upgrade gate |
| `purefb_lifecycle` additions | delete-marker cleanup; tag-filtered rules and `keep_previous_versions_count` (2.29) | lifecycle as the reclaim; tag-on-arrival |

## 8. Scope

| Area | Purity//FB | Built | Verified by |
| --- | --- | --- | --- |
| Polling and management syslog | 4.6.4 | `purefb_events` with alerts, audits, sessions, health, telemetry and config; `purefb_syslog`'s management path; the alerts, change_control, config_drift, health, capacity, quota_user, client, maintenance, sessions and syslog rulebooks; notify, the approval paths and the orchestrator post reused from the sibling | seven lab exit tests in [VERIFICATION.md](VERIFICATION.md): the six signals live; a scratch file system filled past its quota raises 1101 and is grown; a uid quota raises 1106 and is raised; a human's NTP change is reconciled and a destroy notified from the audit log; the identify LED with the LLDP line; a client burst attributed to its address; a maintenance window flipping `maintenance_active` |
| The object side | 4.6.4 (object audit from 4.6.3) | `purefb_object_audit`; the `posture` signal; the three audit modules; the two `eda` buckets; `object_audit.yml` (canary, honey, landing, lock on arrival, the S3 approval inbox, storms, the auditor guard) and `posture.yml` | five tests: one boto3 GET disables the key within one rotation; an approval PutObject runs a proposed grow; a policy member removed as a human is re-attached and paged; the log bucket at its warning threshold has its quota raised; the cursor survives a restart |
| Telemetry, containment and replication | 4.6.4 and the 4.5.6 peer | the telemetry, maintenance, QoS, session and lock, legal hold, alert flag and eradication modules; the `replication` signal; OTLP into a Prometheus receiver with the rules generated by `tools/telemetry_rules.py` and the Alertmanager rulebook; `contain.yml` (quarantine, tarpit, the lock-release webhook); `replication.yml`; `housekeeping.yml` (selftest, showback, DR drills); the certificate renewal in `posture.yml` | nine tests: a telemetry target created, attached to the twelve policies and the silence rule firing on a live receiver; `eda-tarpit` throttling dd on the client VM, then lifted; the client VM read-only, then released; the lag fixture escalating while the live signal reports the real connection; a promote and demote on the peer; a window opened and closed by the module; an NLM lock listed and released; a legal hold placed and removed; showback and selftest |
| The platform port | any; a containerized Ansible Automation Platform 2.7 with the automation orchestrator | `aap/seed.yml` with the `FlashBlade API`, `FlashBlade fleet` and `FlashBlade object store` credential types, 35 job templates, the controller-tier capacity workflow and three schedules in place of the housekeeping tick; the execution and decision environments; the fifteen platform rulebooks from `tools/platform_rulebooks.py`; `tools/activation_audit.py`; the seven orchestrator definitions with `tools/ao_lint.py` and `tools/ao_sync.py`; the Slack approval bot | ten tests: `make check`; the seed from an empty organization, run twice; a job in the execution environment; the activations from the decision environment; a config drift remediated through the fleet credential; a canary read disabling a key from the object-audit activation; the silence alert through the alertmanager activation; a human audit line through the syslog activation; the controller-tier workflow through its approval; the capacity route, a day-2 grow and the drill schedule on the orchestrator |
| Roadmap | 4.8.x | NFS file audit, honeyfiles, inbox, storm windows, 128 KB frames; adaptive telemetry; tag-driven lifecycle | a honeyfile read from the NFS client snapshots the file system and kills the session within seconds |
| Roadmap | 4.10.a | `ansible.eda.kafka` behind the shared CloudTrail event filter; the bucket tail stays as the record | canary latency under 5 s; `eventId` dedupe while both sources run |

Making the riskiest demos safe: the canary key disable defaults to notify,
with a 5-minute rotation, the plugin's reader key in a separate account
from the victim key and the re-enable playbook ready; the 1101 grow never
runs on the Manila share (a scratch `eda-demo-quota` file system with its
own export policy, capped at 2 GiB); the OTLP silence rule is written
against series names read from Prometheus the day before, with a fixture
replay as fallback.

### Deployment

`decision-environment/` holds a Containerfile and an ansible-builder v3
definition, both on `quay.io/ansible/ansible-rulebook:v1.3.2`, that produce
the same tree: the collection `sdodsley.flashblade_eda` built from the
checkout and installed with its dependencies, py-pure-client 1.98 or newer
(the FB_2_30 client the telemetry and object-audit code assume), boto3,
and the rulebooks and playbooks as plain files under `/opt/flashblade-eda`.
Standalone, one container runs one rulebook with the vars and inventory
mounted and the credentials in its environment, and the webhook rulesets
listen on their ports. On Ansible Automation Platform the same image is
the decision environment: activations resolve the sources from the
collection in the image and run the generated variants under
`extensions/eda/rulebooks/platform/`, where `run_playbook` becomes
`run_job_template` against the templates `aap/seed.yml` creates in the
execution environment `aap/Containerfile.ee` builds, the webhook rulesets
are left out (approvals arrive through the controller-tier workflow, the
orchestrator and the S3 inbox) and the housekeeping tick gives way to
three controller schedules (the DR drill, the showback, the selftest). Every
activation carries one array's `FlashBlade object store` credential (the
object-audit source reads it), whose injectors are fixed environment
variable names, so there is one object-audit activation per array. The orchestrator takes the approvals a
rulebook hands it (`orchestrator_enabled`: the capacity reclaim and the
paused replica link) and offers the day-2 forms (grow a file system,
identify LED, QoS cap, quarantine a client) and a scheduled drill of its
own; the Slack approval bot runs as one more container beside the
listeners, opens an outbound Socket Mode websocket and needs no inbound
port (`aap/README.md`, [ORCHESTRATOR.md](ORCHESTRATOR.md),
`slack/README.md`, [PACKAGING.md](PACKAGING.md)).

## 9. Risks and what not to build

- Object-audit latency is one rotation until Kafka, and records drop
  silently when the log bucket hits its quota.
- Data-audit syslog from every blade and OTLP both leave by the management
  VIP; 1139 only covers data-audit drops and is itself suppressed in a
  window.
- The FB_2_21 client lacks the telemetry methods and Kafka sits above the
  SDK ceiling, so raw REST appears wherever versions gap; the decision
  environment must pin py-pure-client 1.98 or newer.
- The lab is shared: Manila owns the production file system, the
  eradication delay and the maintenance window are array-wide, the client
  VM hosts the OTLP receiver, and the 4.5.6 peer has no LLDP, CSR or
  object audit. An array connection made with replication addresses stays
  `connecting` on this pair; the one the drill uses was made with the
  management address and the connection key alone
  ([HARDWARE_NOTES.md](HARDWARE_NOTES.md), "Replication"). A rulebook host
  behind NAT needs the receiver on a host the array reaches and
  Alertmanager posting back through an ssh tunnel.
- Compliance retention, a ratcheted retention lock and SafeMode need
  Support to undo; the eradication delay caps at 30 days.
- Last-week baselines are idle on the lab and 14-day location baselines
  cannot exist on demo day; fixtures stand in and say so.
- Already-open alerts arrive as `initial`; a human flagging an alert in the
  GUI reads as handled.

Not built: a file-audit SIEM (EDA takes a filtered rsyslog tap); data-path
denials without approval or canary confirmation; SMB audit on a
multi-protocol file system before 4.8.0; Kafka or S3-notification
emulation before 4.10; quota e-mails (the array sends them); file log
targets (directed availability); an own TSDB; an SNMP source; software
installation; realm-scoped credentials; fleet mode for alerts, health or
client performance (no `context_names` on those endpoints); NLM
reclamation as a per-client tool; and everything FlashArray-only from the
sibling project: stretched pods, preferred arrays, protection-group
replicate-now, `/alerts/rules` and volume tags.

## 10. References

- Rulebook conditions: https://docs.ansible.com/projects/rulebook/en/latest/conditions.html
- ansible.eda sources: https://github.com/ansible/event-driven-ansible/tree/main/extensions/eda/plugins/event_source
- FlashBlade collection: https://github.com/Everpure-Ansible/FlashBlade-Collection
- py-pure-client: https://github.com/PureStorage-OpenConnect/py-pure-client
- Sibling design: https://github.com/sdodsley/flasharray-eda/blob/main/docs/DESIGN.md
