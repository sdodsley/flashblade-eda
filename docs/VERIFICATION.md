# Verification

What is to be exercised against FlashBlade hardware for this part, what is
replayed from fixtures, and what is not verified. The design is in
[DESIGN.md](DESIGN.md); array and tool facts are in
[HARDWARE_NOTES.md](HARDWARE_NOTES.md).

## Environment

A FlashBlade//S200 on Purity//FB 4.6.4 (REST 2.21): 10 blades, 40 DFM
drives, one LAG of 8 x 100 GbE to a Cisco Nexus switch with LLDP visible,
one file system (a Manila share, never touched by the tests), six open
alerts, and no buckets before part 1. Part 2 added the object store
account `eda` with its three users, three buckets, audit policy, log
target and filters ([HARDWARE_NOTES.md](HARDWARE_NOTES.md), "Object
audit"). Tooling: ansible-rulebook 1.3.x, ansible-core 2.17,
py-pure-client 1.98.0, everpure.flashblade 1.29.0, ansible.eda 2.13.0,
boto3 1.26 or newer for part 2. An NFS client VM provides the load, the
quota writes and the S3 traffic. This is a prototype; it is not a
supported product.

## How the tests run

`tests/e2e_sources.sh` runs each rulebook against the array with
`--print-events` for a fixed time and counts the event names, opens and
closes a one-hour maintenance window, replays the hardware fixture to
light and clear an identify LED, and runs the audits signal directly; it
creates, destroys and resizes nothing. The quota, grow and client tests
below need a scratch file system and a load generator and are run by hand
with the playbooks named. Everything the script writes goes under
`tests/out/`. What cannot be induced (a blade fault, a degraded LAG, a
full array, a hot client) is replayed from `tests/fixtures/` through
`ansible.eda.generic`; the replayed events still drive the real actions
on the array.

## Lab exit tests for part 1

Every test restores what it changes: the window is closed, the LED
cleared, the scratch file system and its quota eradicated.

| # | Test | Expected | Status |
| --- | --- | --- | --- |
| 1 | `purefb_events` against the lab array, all six signals | `purefb_alert` with transition `initial` for the six open alerts; `purefb_health` with `blade_count` 10 and `drive_count` 40; `purefb_capacity` with the seven daily space points read (`days_to_full_known` stays false while the array holds no data); `purefb_config` with the array's real NTP servers and DNS; no `purefb_poll_error` | passed 2026-11-12: six `initial` alerts (1123, 1140, four 1112), 10 blades, 40 DFMs, real NTP/DNS/LDAP in `purefb_config`; the run found the 20 empty bays reported as `unclaimed` and the client rows named `address:port`, both now handled; one transient 503 on `clients/performance` in an hour, now logged and skipped |
| 2 | a scratch 1 GiB hard-limit file system `eda-demo-quota` (NFS rule for the client VM), written to 950 MiB from the client VM | alert 1101 opens and `purefb_fs_quota` reports `used_pct` above `fs_quota_grow_pct` (90); `grow_fs.yml` grows it to 1.25 GiB (`fs_grow_step_pct` 25) and the record carries the old and new size | passed 2026-11-12: 1101 `opened` was ticketed through the runbook map and flagged on the array; `purefb_fs_quota` at 92.77 % drove `grow_fs.yml` (1073741824 to 1342177280 bytes, `PATCH /file-systems provisioned`); within a minute the array moved 1101 to `closing`, which the plugin now emits as its own transition |
| 3 | a 100 MiB quota for uid 1001 on that file system, 90 MiB written as that uid from the client VM | alert 1106 opens; `user_quota.yml` reads the file system's quotas, records quota and usage for every principal at or above `user_quota_warn_pct` and raises each by `user_quota_step_pct` up to `user_quota_max_bytes` | passed 2026-11-12: 1106 `opened` ("One user has reached or exceeded 90%") reached `user_quota.yml`; the alert carries only `File System Name` and per-band counts, so the first version could not name the user and was rewritten to list `/quotas/users` for the file system; the rerun recorded uid 1001 at 90.0 % and raised 104857600 to 131072000 bytes (`purefb_userquota`) |
| 4 | a governed and a destructive change made by a human account (`tests/as_human.yml`: an NTP server added, a scratch file system destroyed and eradicated) | `purefb_audit` arrives with `by_automation false`; the NTP change is reconciled from `baseline_dir/fb-a.yml` and recorded with user, address, interface, command and arguments; each destructive `purefs` subcommand is notified | passed 2026-11-12: `purearray setattr --ntpserver` arrived within the 10 s audit interval and `reconcile.yml` put the baseline servers back ("reverted to baseline"; the first run, without a baseline file, recorded "no baseline for this array" and changed nothing); `purefs remove`, `destroy` and `eradicate` each produced a warning naming pureuser, 10.21.22.12 and REST |
| 5 | `identify_led.yml` against `CH1.FB1`; then the replayed 1000 fixture through `tests/replay_health.yml` | the LED lights on the real component, the record carries the LLDP neighbour line (switch name and port); the replayed fixture takes the same path; the LED is cleared afterwards | passed 2026-11-12 (`tests/e2e_sources.sh` step 3): the replayed blade fault lit the LED on `CH1.FB1` (`purehw setattr --identify 'on'` in the audit log), the recovered fixture cleared it, the LAG fixture notified with the switch port list |
| 6 | 75 s of dd writes from the client VM against the scratch file system | `purefb_client_performance` names the client's address with `ops_share_pct` near 100; the client rule stays quiet while `baseline_known` is false (an array idle last week has no baseline to compare with) | passed 2026-11-12: the client row read `10.21.200.129` (port 844 split off) at 100 % of the ops and 201 MB/s with `high_share_polls` climbing to 2; `purefb_fs_performance` put `eda-demo-quota` at 100 % for two polls and the "file system hogging the array" notify fired (`qos_auto_cap` off); the per-client rule stayed quiet because last week's baseline is zero on this array, so `array_write_latency_ratio` is unknown; three one-second bursts in an earlier run were too short for the array's lagged client sample |
| 7 | a one-hour maintenance window opened through `maintenance_window.yml` (`window_action: open, window_hours: 1`), then closed | `maintenance_active` reads true on the next health, alert and audit events and false again after the close; the human-window rule does not fire for the automation account | passed 2026-11-12: `POST /maintenance-windows?names=array` with `{"timeout": 3600000}` opened it, the next health poll carried `maintenance_active: true` with the window, the close deleted it; the first attempt exposed two playbook bugs (a bare IP as the REST base, `.items` read as a dict method), both fixed |

## Lab exit tests for part 2

Run by hand against the lab object store with `object_audit.yml` (and
`capacity.yml` for test 2) started through `run.sh`; the S3 traffic comes
from the client VM with boto3 and the `eda-app` key. Every test restores
what it changes: the disabled key is re-enabled (`purefb_s3user
state=keystate enable_key=true`), the policy member re-attached, the
quota put back, the request objects left in place (the log bucket is
Object-Locked). The array's 5-minute rotation sets the pace: each test
waits one window plus the poll interval for its event.

| # | Test | Expected | Status |
| --- | --- | --- | --- |
| 1 | a boto3 `GetObject` of `eda-data/canary/do-not-read.txt` with the `eda-app` key, `canary_auto_disable_key` true, `canary_readers` empty | within one rotation plus `object_audit_interval` a `purefb_s3_event` with `canary true`, `canary_allowed false`, `by_automation false`, the key id, `user eda-app` and the source address; `canary.yml` sends `PATCH object-store-access-keys?names=<key>` `{"enabled": false}`, the key reads back `enabled false` and the next request with it is refused; one record of kind `canary`, severity critical, with the key, user, object key, source address and log object; `set_stats` carries `access_key_id`, `user`, `disabled true`; a second read in the same window by a key in `canary_readers` produces an event with `canary_allowed true` and no action | passed 2026-11-12: the `GetObject` at 21:17:38Z fell into the 21:15 window; its log object (`.../211500.A198.2.log`) was written at 21:20:00 and the key read back disabled at 21:20:33Z (record kind `canary`, critical, `outcome "key disabled"`, `disabled true`), three minutes after the read; the key was re-enabled afterwards; the `canary_readers` allow-list case is unit-tested only |
| 2 | `grow_fs.yml` on the scratch file system with `s3_requests_enabled` true and `fs_grow_max_bytes` below the target, so a request is written; then a `PutObject` of `approvals/<id>.json` (`{"approve": true, "approver": "<name>", "action": "grow_fs"}`) into `eda-log` by a key listed in `approval_keys` (not the tail or state key) | the request appears both under `request_dir` and as `eda-log/requests/<id>.json`; the approval write arrives as a `purefb_s3_event` with `approval true` and `request_id <id>` (the `edaapprovals` filter audits that prefix); `s3_approval.yml` reads both objects with the tail key, re-reads the file system, grows it to the requested size through `PATCH /file-systems provisioned`, writes `eda-log/receipts/<id>.json` and marks the local request done; one record of kind `s3_approval` with the request id, action `grow_fs` and outcome; the same approval written by the `eda-app` key when it is not in `approval_keys`, or by the state key, is refused and recorded, nothing grown | passed 2026-11-12 (third run): with `fs_grow_max_bytes` lowered to the current 1342177280 and the scratch file system filled to 93 %, `grow_fs.yml` wrote the request file and `eda-log/requests/<id>.json`; the approval object written by the `eda-app` key at 21:56:06Z arrived at the 22:00 window end and `s3_approval.yml` grew the file system to 1677721600 bytes at 22:00:35Z (`request_source object`, `performed true`), wrote the receipt and the record; the first two runs found the lab mount stale and the mirrored request written as a Python repr instead of JSON (fixed: the S3 helper carries bodies base64-encoded, an empty object falls back to the request file); the refusal paths (key not listed, state key) are unit-tested only; the file system was shrunk back afterwards |
| 3 | as a human account (`tests/as_human.yml` style), `eda-data` removed from the members of the `eda-audit` policy on the CLI or in the GUI, `audit_auto_reattach` true | `purefb_audit` with `command purepolicy`, a `remove` subcommand, `audit` in the arguments and `by_automation false` arrives within `audits_interval`; the change-control ruleset of `object_audit.yml` runs `auditor_guard.yml` in mode `reattach` with the human in `reason`; the three modules put the baseline back (`eda-data` a member again, log target and filters unchanged, every module idempotent on the second run); one record of kind `auditor_guard`, severity critical, naming the user and address; the next `purefb_audit_health` reads `uncovered_count 0`, `members_without_filters []`, `healthy true`; with the switch off the record says what would have been done and the member stays detached until the plugin's own coverage rule (throttled one hour) reports it again | passed 2026-11-12: `purepolicy` / `audit object remove --bucket 'eda-data' eda-audit` by `pureuser` over REST (python-requests, so neither `by_automation` nor `automation_agent`) arrived within the 10 s audit interval and `auditor_guard.yml` had `eda-data` back as a member 13 s after the detach (record critical, `reason` naming pureuser, 10.21.22.11, REST and the audit id; `changed_objects ["policy eda-audit"]`); the array's `subcommand` is the multi-word string `audit object remove`, which is why the rule uses regex operators; the switch-off path was not run on the array |
| 4 | `quota_limit` of `eda-log` lowered so that `space.virtual` is at or above 80 % of it; first with `log_bucket_quota_max_bytes` 0, then with a ceiling above the current quota | `purefb_audit_health` with `log_bucket_quota_known true` and `log_bucket_used_pct` at or above `log_bucket_quota_warn_pct` on the next poll; `auditor_guard.yml` in mode `quota` records critical and changes nothing with the ceiling at 0; with a ceiling it sends `PATCH buckets?names=eda-log` `{"quota_limit": "<bytes>"}` raised by `log_bucket_quota_step_pct` (25) and capped at the ceiling, the record carries the old and new quota, and the rule stays quiet for six hours by its throttle; the quota is put back to 1073741824 afterwards | passed 2026-11-12: with the quota lowered to 209715200 and a 170 MB object written, the next health poll read 85.2 % and `auditor_guard.yml` raised the quota to 262144000 (step 25 %, ceiling 2 GiB) within a minute (record critical with the old and new quota); the quota was put back to 1073741824 afterwards; the ceiling-0 notify-only path was not run on the array |
| 5 | two windows of traffic from the `eda-app` key, the rulebook killed after the first window's objects were emitted and restarted before the second window's objects were written | the cursor file `<cursor_dir>/<array>.json` (and `eda-state/cursors/object_audit/<array>.json`) holds the last key and the seen keys of the retained windows at the kill; after the restart no record of the first window is emitted again and every record of the second window arrives exactly once (`event_id` compared with the log objects' `eventId`s); a late authority of the first window written after the restart is still picked up (`late_windows` 2); `purefb_audit_health` reads `cursor_persisted true` | passed 2026-11-12: nine records of the 22:00 window were emitted before the stop at 22:06:22Z; the cursor file then held `last_key .../220000.A247.1.log`, the nine seen keys of that window plus the newest of the previous one and `floor 2026/11/12/215500`; three records were written while the rulebook was down and arrived in the 22:05 window; after the restart at 22:10:21Z exactly three more `purefb_s3_event`s were emitted (12 in total), every log object appeared once in the output (one event and one rollup each) and no `event_id` repeated; `cursor_persisted true` on the health event; a late authority was not provoked on the array (unit-tested) |

## Replayed only

Exercised from fixtures under `tests/fixtures/`, not induced on the array:
a blade fault and a degraded LAG (`tests/replay_health.yml`), the alert
routes (`tests/replay_alerts.yml`), days to full (`tests/replay_capacity.yml`),
a client's share, the object-side fixture (a canary hit, a landing
marker, an evidence put, a denied-storm rollup and a quota health event
through `tests/replay_object_audit.yml`) and a posture event with every
finding kind (`tests/replay_posture.yml` from `tests/fixtures/posture.yml`).
The replay rulebooks are kept
rule-for-rule identical to the live ones by the unit test, so a replayed
event takes the same path as a live one.

## Not verified

- The management syslog layouts: `purefb_syslog` parses the lines the
  FlashBlade Admin Guide documents; no syslog server points at the
  listener yet, so the array's actual lines are unconfirmed.
- The data-audit JSON branch of the listener: SMB audit needs an Active
  Directory join the lab lacks and NFS audit needs Purity//FB 4.8.0.
- A real hardware fault, a hot array, a full array: replayed or induced on
  lowered thresholds only.
- The poll-based config drift reapply (`config_drift.yml`): the
  audit-driven reconcile is verified (test 4) and the polled path shares
  its playbooks, but it was not run on a drifted array separately.
- The QoS cap and lift (`qos_cap.yml`, `qos_lift.yml`): written against
  `/qos-policies` and `patch_file_systems qos_policy` through the raw REST
  task; not yet run on an array.
- A maintenance window older than REST 2.16: the gate reads false there by
  design and no such array is in the lab.
- Scale beyond one array; the 4.5.6 peer is read only by later parts.
- Of the object side, what the five tests above do not touch: the landing
  command, the legal hold on arrival, the honey, error-storm and
  mass-delete rules, and the `posture.yml` rules; unit-tested and replayed
  only until they run on the array. The `posture` signal itself was run by
  hand against the lab (2026-11-12): one `purefb_posture` with the default
  export policy `all` as a world-open export, `_smb_share_allow_everyone`
  as an Everyone-full-control share policy, no public bucket (the array
  reports `bucket-and-objects-not-public`, which the first version counted
  as public), no uncovered bucket, no old key, no expiring certificate.
- Kafka object notifications (Purity//FB 4.10.a): the bucket tail is the
  only object transport the lab can show.
- The telemetry, containment and replication work of the later parts
  ([DESIGN.md](DESIGN.md), "Roadmap").
