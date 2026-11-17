# Verification

What is exercised against FlashBlade hardware and the platform, what is
replayed from fixtures, and what is not verified. The design is in
[DESIGN.md](DESIGN.md); array and tool facts are in
[HARDWARE_NOTES.md](HARDWARE_NOTES.md). This is a prototype; it is not a
supported product.

## Environment

- `fb-a`: a FlashBlade//S200 on Purity//FB 4.6.4 (REST 2.21) for the
  tables below and 4.8.8 (REST 2.30) for "On Purity//FB 4.8.8": 10 blades,
  40 DFM drives, one LAG of 8 x 100 GbE to a Cisco Nexus switch with LLDP
  visible, a Manila share the tests never touch, two scratch file systems
  (`eda-demo-quota` for the quota, grow, client, tarpit, quarantine and
  lock tests; `eda-dr-link` for the DR drill), and the object store
  account `eda` with its three users, three buckets, audit policy, log
  target and filters ([HARDWARE_NOTES.md](HARDWARE_NOTES.md), "Object
  audit").
- `fb-b`: a legacy FlashBlade on Purity//FB 4.5.6 (REST 2.17), the
  replication peer, joined to `fb-a` by an array connection and the
  replica link of `eda-dr-link`.
- An NFS client VM that provides the load, the quota writes, the S3
  traffic and the NLM lock and hosts a Prometheus 3.16 OTLP receiver with
  Alertmanager.
- A containerized Ansible Automation Platform 2.7 on a host of its own
  (podman; the controller, its execution environment and the EDA
  activation workers), the automation orchestrator beside it (the
  upstream code at the tag Red Hat ships as the 2026.8 release, API
  1.3.1) and a Slack workspace. The client VM's Alertmanager and the
  arrays' syslog servers point at the activations on the platform host;
  the sibling project's objects on the same platform are left alone.
- Tooling: ansible-rulebook 1.3.x, ansible-core 2.17, py-pure-client
  1.98.0, everpure.flashblade 1.29.0, ansible.eda 2.13.0, boto3 1.26 or
  newer.

## How the tests run

`tests/e2e_sources.sh` runs each rulebook against the array with
`--print-events` for a fixed time and counts the event names, opens and
closes a one-hour maintenance window, replays the hardware fixture to
light and clear an identify LED, and runs the audits signal directly; it
creates, destroys and resizes nothing, and everything it writes goes under
`tests/out/`. The tests that need a scratch file system, a load generator
or S3 traffic are run by hand with the rulebook, playbook or module the
row names, started through `run.sh`; the platform tests run through the
seeded objects. What cannot be induced (a blade fault, a degraded LAG, a
full array, a hot client) is replayed from `tests/fixtures/` through
`ansible.eda.generic`, and the replayed events drive the real actions on
the array. Every test restores what it changes: the window closed, the
LED cleared, the key re-enabled, quotas and sizes put back, the policy
lifted, the hold released, the replica demoted, the NTP servers restored.
The eradication module is never run for real on the shared array; its
ratchet is unit-tested and the lab run is check mode only.

## Polling, syslog and the first actions

| # | Test | Expected | Result |
| --- | --- | --- | --- |
| 1 | `purefb_events` against the lab array, all six signals | `purefb_alert` with transition `initial` for the open alerts; `purefb_health` with `blade_count` 10 and `drive_count` 40; `purefb_capacity` with the seven daily space points read (`days_to_full_known` stays false while the array holds no data); `purefb_config` with the array's real NTP servers and DNS; no `purefb_poll_error` | passed: six `initial` alerts (1123, 1140, four 1112), 10 blades, 40 DFMs, the array's NTP, DNS and LDAP in `purefb_config`, no poll error in an hour (one transient 503 on `clients/performance`, logged and skipped) |
| 2 | a scratch 1 GiB hard-limit file system `eda-demo-quota` (NFS rule for the client VM), written to 950 MiB from the client VM | alert 1101 opens and `purefb_fs_quota` reports `used_pct` above `fs_quota_grow_pct` (90); `grow_fs.yml` grows it to 1.25 GiB (`fs_grow_step_pct` 25) and the record carries the old and new size | passed: 1101 `opened` was ticketed through the runbook map and flagged on the array; `purefb_fs_quota` at 92.8 % drove `grow_fs.yml` from 1073741824 to 1342177280 bytes (`PATCH /file-systems provisioned`); the array moved 1101 to `closing` within a minute, which the plugin emits as its own transition |
| 3 | a 100 MiB quota for uid 1001 on that file system, 90 MiB written as that uid from the client VM | alert 1106 opens; `user_quota.yml` reads the file system's quotas, records quota and usage for every principal at or above `user_quota_warn_pct` and raises each by `user_quota_step_pct` up to `user_quota_max_bytes` | passed: 1106 `opened` ("One user has reached or exceeded 90%") reached `user_quota.yml`, which lists `/quotas/users` for the file system because the alert names the file system and per-band counts, not the user; uid 1001 recorded at 90.0 % and raised from 104857600 to 131072000 bytes (`purefb_userquota`) |
| 4 | a governed and a destructive change made by a human account (`tests/as_human.yml`: an NTP server added, a scratch file system destroyed and eradicated) | `purefb_audit` arrives with `by_automation false`; the NTP change is reconciled from `baseline_dir/fb-a.yml` and recorded with user, address, interface, command and arguments; each destructive `purefs` subcommand is notified | passed: `purearray setattr --ntpserver` arrived within the 10 s audit interval and `reconcile.yml` put the baseline servers back ("reverted to baseline"; without a baseline file it records "no baseline for this array" and changes nothing); `purefs remove`, `destroy` and `eradicate` each produced a warning naming the user, the address and REST |
| 5 | `identify_led.yml` against `CH1.FB1`; then the replayed 1000 fixture through `tests/replay_health.yml` | the LED lights on the real component, the record carries the LLDP neighbour line (switch name and port); the replayed fixture takes the same path; the LED is cleared afterwards | passed (`tests/e2e_sources.sh` step 3): the replayed blade fault lit the LED on `CH1.FB1` (`purehw setattr --identify 'on'` in the audit log), the recovered fixture cleared it, the LAG fixture notified with the switch port list |
| 6 | 75 s of dd writes from the client VM against the scratch file system | `purefb_client_performance` names the client's address with `ops_share_pct` near 100; the client rule stays quiet while `baseline_known` is false (an array idle last week has no baseline to compare with) | passed: the client row named the VM's address (the port split off) at 100 % of the ops and 201 MB/s with `high_share_polls` climbing to 2; `purefb_fs_performance` put `eda-demo-quota` at 100 % for two polls and the "file system hogging the array" notify fired (`qos_auto_cap` off); the per-client rule stayed quiet because last week's baseline is zero on an idle array, so `array_write_latency_ratio` is unknown; a one-second burst is too short for the array's lagged client sample |
| 7 | a one-hour maintenance window opened through `maintenance_window.yml` (`window_action: open, window_hours: 1`), then closed | `maintenance_active` reads true on the next health, alert and audit events and false again after the close; the human-window rule does not fire for the automation account | passed: `POST /maintenance-windows?names=array` with `{"timeout": 3600000}` opened it, the next health poll carried `maintenance_active: true` with the window, the close deleted it |

## The object side

Run with `object_audit.yml` (and `capacity.yml` for test 2); the S3
traffic comes from the client VM with boto3 and the `eda-app` key. The
array's 5-minute rotation sets the pace: each test waits one window plus
the poll interval for its event. The request objects stay in place
afterwards (the log bucket is Object-Locked).

| # | Test | Expected | Result |
| --- | --- | --- | --- |
| 1 | a boto3 `GetObject` of `eda-data/canary/do-not-read.txt` with the `eda-app` key, `canary_auto_disable_key` true, `canary_readers` empty | within one rotation plus `object_audit_interval` a `purefb_s3_event` with `canary true`, `canary_allowed false`, `by_automation false`, the key id, `user eda-app` and the source address; `canary.yml` sends `PATCH object-store-access-keys?names=<key>` `{"enabled": false}`, the key reads back `enabled false` and the next request with it is refused; one record of kind `canary`, severity critical, with the key, user, object key, source address and log object; `set_stats` carries `access_key_id`, `user`, `disabled true`; a second read in the same window by a key in `canary_readers` produces an event with `canary_allowed true` and no action | passed: the `GetObject` fell into a rotation window whose log object was written at the window's close, and the key read back disabled 33 s after that (record kind `canary`, critical, `outcome "key disabled"`, `disabled true`), three minutes after the read; the `canary_readers` allow-list case is unit-tested only |
| 2 | `grow_fs.yml` on the scratch file system with `s3_requests_enabled` true and `fs_grow_max_bytes` below the target, so a request is written; then a `PutObject` of `approvals/<id>.json` (`{"approve": true, "approver": "<name>", "action": "grow_fs"}`) into `eda-log` by a key listed in `approval_keys` (not the tail or state key) | the request appears both under `request_dir` and as `eda-log/requests/<id>.json`; the approval write arrives as a `purefb_s3_event` with `approval true` and `request_id <id>` (the `edaapprovals` filter audits that prefix); `s3_approval.yml` reads both objects with the tail key, re-reads the file system, grows it to the requested size through `PATCH /file-systems provisioned`, writes `eda-log/receipts/<id>.json` and marks the local request done; one record of kind `s3_approval` with the request id, action `grow_fs` and outcome; the same approval written by the `eda-app` key when it is not in `approval_keys`, or by the state key, is refused and recorded, nothing grown | passed: with `fs_grow_max_bytes` lowered to the current size and the file system at 93 %, `grow_fs.yml` wrote the request file and `eda-log/requests/<id>.json`; the approval object arrived at the window's end and `s3_approval.yml` grew the file system to 1677721600 bytes (`request_source object`, `performed true`), wrote the receipt and the record; the refusal paths (key not listed, state key) are unit-tested only |
| 3 | as a human account (`tests/as_human.yml` style), `eda-data` removed from the members of the `eda-audit` policy on the CLI or in the GUI, `audit_auto_reattach` true | `purefb_audit` with `command purepolicy`, a `remove` subcommand, `audit` in the arguments and `by_automation false` arrives within `audits_interval`; the change-control ruleset of `object_audit.yml` runs `auditor_guard.yml` in mode `reattach` with the human in `reason`; the three modules put the baseline back (`eda-data` a member again, log target and filters unchanged, every module idempotent on the second run); one record of kind `auditor_guard`, severity critical, naming the user and address; the next `purefb_audit_health` reads `uncovered_count 0`, `members_without_filters []`, `healthy true`; with the switch off the record says what would have been done and the member stays detached until the plugin's own coverage rule (throttled one hour) reports it again | passed: `purepolicy` / `audit object remove --bucket 'eda-data' eda-audit` by a human over REST (python-requests, so neither `by_automation` nor `automation_agent`) arrived within the 10 s audit interval and `auditor_guard.yml` had `eda-data` back as a member 13 s after the detach (record critical, `reason` naming the user, address, REST and the audit id; `changed_objects ["policy eda-audit"]`); the array's `subcommand` is the multi-word string `audit object remove`, which is why the rule uses regex operators; the switch-off path is not run on the array |
| 4 | `quota_limit` of `eda-log` lowered so that `space.virtual` is at or above 80 % of it; first with `log_bucket_quota_max_bytes` 0, then with a ceiling above the current quota | `purefb_audit_health` with `log_bucket_quota_known true` and `log_bucket_used_pct` at or above `log_bucket_quota_warn_pct` on the next poll; `auditor_guard.yml` in mode `quota` records critical and changes nothing with the ceiling at 0; with a ceiling it sends `PATCH buckets?names=eda-log` `{"quota_limit": "<bytes>"}` raised by `log_bucket_quota_step_pct` (25) and capped at the ceiling, the record carries the old and new quota, and the rule stays quiet for six hours by its throttle; the quota is put back to 1073741824 afterwards | passed: with the quota lowered to 209715200 and a 170 MB object written, the next health poll read 85.2 % and `auditor_guard.yml` raised the quota to 262144000 (step 25 %, ceiling 2 GiB) within a minute, the record carrying the old and new quota; the ceiling-0 notify-only path is not run on the array |
| 5 | two windows of traffic from the `eda-app` key, the rulebook killed after the first window's objects were emitted and restarted before the second window's objects were written | the cursor file `<cursor_dir>/<array>.json` (and `eda-state/cursors/object_audit/<array>.json`) holds the last key and the seen keys of the retained windows at the kill; after the restart no record of the first window is emitted again and every record of the second window arrives exactly once (`event_id` compared with the log objects' `eventId`s); a late authority of the first window written after the restart is still picked up (`late_windows` 2); `purefb_audit_health` reads `cursor_persisted true` | passed: nine records of the first window were emitted before the stop; the cursor file held the last key, the nine seen keys of that window plus the newest of the previous one, and the floor; three records written while the rulebook was down arrived in the next window and after the restart exactly three more `purefb_s3_event`s were emitted (12 in total), every log object appeared once (one event and one rollup each) and no `event_id` repeated; `cursor_persisted true` on the health event; a late authority is unit-tested only |

## Telemetry, containment and replication

Run with the rulebook named in each row started through `run.sh`, or with
the playbook or module run directly where the row says so. The load, the
NLM lock and the read-only check come from the client VM; the OTLP
receiver and Alertmanager run on it as plain processes, and Alertmanager
reaches a rulebook host behind NAT over an ssh reverse tunnel
([../prometheus/README.md](../prometheus/README.md)).

| # | Test | Expected | Result |
| --- | --- | --- | --- |
| 1 | `telemetry_setup.yml` against `fb-a` with `telemetry_target_name` `eda-prom`, `telemetry_endpoint` `<receiver>:9090/api/v1/otlp/v1/metrics`, `telemetry_transport` `otlp-http-protobuf-without-tls`, `telemetry_source` a data VIP and `telemetry_policies` the twelve built-ins; then the target disabled (`purefb_telemetry_target enabled: false`) for longer than the silence window | `purefb_telemetry_target` creates the target (`POST /telemetry-targets?names=eda-prom`; a scheme in the endpoint is stripped with a warning) and attaches it to every listed policy, `purefb_telemetry_policy` reads each policy back with `eda-prom` in `targets`, its metric names and resolution; the array's own `GET /telemetry-targets/test` runs and its status is recorded (`set_stats` `target`, `policies_attached` 12, `test_ok`); a second run changes nothing; `selftest.yml`'s telemetry check reads ok. On the receiver, `storage_device_iops` and its siblings appear with the promoted device attribute; disabling the target fires `FlashBladeTelemetrySilent` after `absent_over_time` of twice the policy's resolution or `telemetry_silence_minutes`, Alertmanager delivers it to `alertmanager.yml`, `telemetry_check.yml` records kind `telemetry_check` with verdict `pipeline` (array and receiver answer, the array's test fails) and `set_stats` `verdict`, `test_status`, `array_reachable`, `receiver_reachable`; re-enabling the target resolves the alert and the resolved notification follows at info | passed: `telemetry_setup.yml` created `eda-prom` (the array POSTs to the path as given; `/api/v1/otlp` alone is 404 and stores nothing), attached all twelve policies, and the 60 s policies showed up within two minutes, the 10 min ones within ten; the array's own test answers 400 even while the pushes land, so it is not a delivery check; the rule file generated from the live listing matches the committed one; with the target disabled, `FlashBladeTelemetrySilent` fired for ten policies from eight minutes on and travelled Prometheus to Alertmanager to the rulebook, and `telemetry_check.yml` ran against `fb-a` through `telemetry_array_names` with verdict `pipeline`; re-enabling the target resolved every alert within two minutes and the resolutions were logged; the per-client policies get no silence rule and the connector rule counts named error kinds only for what the live stream showed ([HARDWARE_NOTES.md](HARDWARE_NOTES.md), "Telemetry") |
| 2 | `tarpit.yml` with `action apply` on `eda-demo-quota` and `tarpit_ops_per_sec` 100 while dd runs on the client VM; then `action lift` | `purefb_qos` creates `eda-tarpit` with `max_total_ops_per_sec` 100 (`purepolicy` / `qos create` in the audit log) and assigns it (`PATCH /file-systems qos_policy`); dd's rate on the VM drops to about 100 ops/s; the next `purefb_fs_performance` reads `eda_tarpit true` and `eda_capped false`; the record is kind `tarpit`, `set_stats` `file_system`, `action apply`, `policy eda-tarpit`, `changed true`; `lift` clears the policy from the file system, keeps the policy object, the rate recovers and `eda_tarpit` reads false; a file system that already carries a policy of its own is recorded and left alone; the two containment rules fire the same playbook from `tests/replay_contain.yml` | passed: 3000 direct 4 KiB writes from the VM took 3.0 s before (about 1000 ops/s), 32.4 s with `eda-tarpit` at 100 ops/s (379 kB/s) and 3.8 s after the lift; `purefb_qos` created the policy (`purepolicy` / `qos create`), assigned the file system and cleared it with the empty-name form; records kind `tarpit` at warning (apply) and info (lift) |
| 3 | `quarantine_client.yml` with `action quarantine`, `client` the VM's address and `file_system eda-demo-quota`; a write and a read attempted on the VM's mount; then `action release` | the legacy rule `<ip>(rw,no_root_squash)` is re-read and rewritten `<ip>(ro,no_root_squash)` through `PATCH /file-systems nfs.rules`; on the VM a write fails read-only while a read succeeds; the record is kind `quarantine`, severity critical, naming the file system touched, `set_stats` `client`, `action`, `file_systems`, `changed_count 1`; `release` puts `rw` back and the write succeeds; a client listed in `contain_exclude_clients`, or the array's own management or replication address, is refused with nothing changed; with `file_system` empty the Manila share is skipped by `contain_exclude_file_systems`; the same two actions arrive through the `locks_port` webhook under the bearer token | passed: the legacy rule went from `<ip>(rw,no_root_squash)` to `(ro,...)`, a write on the VM's mount failed with "Read-only file system" while a read worked, the release put `rw` back and the next write succeeded; records critical (quarantine) and warning (release); `tests/replay_contain.yml` quarantines and tarpits the scratch file systems from its fixture, so the replay drives the real playbooks; the refusals and the webhook path are unit-tested only |
| 4 | `tests/replay_replication.yml` with `tests/fixtures/replication.yml` (a connection down, a paused bucket link, a lagging link that escalates, a recovery); then `replication.yml` live against `fb-a` | replayed: the connection down notifies at critical (throttled one hour by array and remote), the paused link notifies (or runs `replication_resume.yml` with `replication_auto_resume` true), the lagging link notifies at warning while `escalate` is false and at critical once it is true, the recovery is logged; live: `purefb_array_connection` for the peer with its `status`, `connected` and no `previous_status` on the first poll, `purefb_replication` with `connection_count 1`, the link counts, `issue_count` and `maintenance_active`, the "Array connection not connected" rule notifying once an hour while the connection is down and the summary logged at debug; when the connection comes up, `previous_status` and the "back" rule logs it | passed: replayed, the connection down notified at critical, the paused bucket link at warning, the lagging link escalated to critical after five polls and the recovery was logged at info; live, `replication.yml` read the real connection to the peer and paged a `connecting` state at critical on its first poll |
| 5 | `dr_drill.yml` with `dr_drills` `[{array: fb-a, file_system: eda-dr-link, peer: fb-b, peer_file_system: eda-dr-link, max_minutes: 10}]`, run by hand with `dr_drill_force` | `tasks/dr_drill_one.yml` resolves `fb-b` through `resolve_array`, sends `PATCH /file-systems?names=eda-dr-link requested_promotion_state promoted` to the peer, polls `promotion_status` until `promoted` within `max_minutes`, reads the file system's space and export, then demotes (`requested_promotion_state demoted`, with `discard_non_snapshotted_data` when `dr_drill_discard` is true) and waits for `demoted`; the record is kind `dr_drill` at info with the durations; the marker `showback/dr/<date>-eda-dr-link.json` appears in `eda-state` and a second run the same day records that it already ran; a timeout or a refusal records critical and the always block still demotes | passed: the drill promoted the peer's copy (`writable true`), read it and demoted it again with `dr_drill_discard` true, which the 4.5.6 peer requires ("creating an undo snapshot is not supported"), wrote the day's marker to `eda-state` and recorded kind `dr_drill` at info; a drill whose peer is missing from the inventory, and a demote refused without the discard, are recorded as critical, as a broken drill should be. Two array facts came out of it, both in [HARDWARE_NOTES.md](HARDWARE_NOTES.md), "Replication": a connection created with replication addresses stays `connecting` on this pair while one created with the management address and the connection key alone connects, and a file system that has ever carried a legal hold cannot be linked, which is why the drill has its own scratch file system |
| 6 | `purefb_maintenance` against `fb-a`: `state present timeout 1`, the same again, `extend true timeout 2`, then `state absent`; `health.yml` running alongside | the first run posts `/maintenance-windows?names=array` `{"timeout": 3600000}` and returns `window` `{name array, created, expires}` with `changed true`; the second changes nothing; `extend` deletes and re-posts and `expires` moves out by an hour; `maintenance_active true` on the next `purefb_health`; `absent` deletes the window and the flag drops; check mode reports each change without posting | passed: `state present timeout 1` posted the window (`puremaintenance schedule --timeout '1h' array` in the audit log), the same again changed nothing, `state absent` closed it; `extend` is not exercised on the array |
| 7 | `flock` held on a file in the VM's NFSv3 mount of `eda-demo-quota`; `purefb_fs_session kind locks client <ip>` first in check mode, then for real; the same through the `locks_port` webhook with `action release_locks` (`release_locks.yml`) | `GET /file-systems/locks` lists the NLM lock with the VM's address, `file_system eda-demo-quota`, `protocol nfs` and the path; check mode returns `removed.items` naming it with `changed true` and makes no delete call; the real run calls `delete_file_systems_locks` by client, returns `removed.locks 1`, `changed true`, and the `flock` holder on the VM loses or has to re-acquire the lock; `kind sessions` reports zero because an NFSv3 mount has no NFSv4 session; `release_locks.yml` writes a record of kind `release_locks` at warning naming the approver, `set_stats` `client`, `removed_sessions`, `removed_locks` | passed: an `flock` from the VM appeared as one NFSv3 lock under `/file-systems/locks` (the client's address, `source eda-demo-quota`, the path; an NFSv3 client has no session rows), `release_locks.yml` removed it through `purefb_fs_session` (0 sessions, 1 lock) and the listing read 0 afterwards; check mode and the webhook path are not exercised |
| 8 | `purefb_legal_hold` against `fb-a`: `name eda-ir-demo`, a description, `file_system eda-dr-demo`, `paths ["/"]`, `recursive true`; the same again; `entities_state absent`; `state absent` | `POST /legal-holds` creates the hold and `post_legal_holds_held_entities` the entity; `GET /legal-holds/held-entities` lists `{legal_hold eda-ir-demo, file_system eda-dr-demo, path /}` and the module returns `hold` with `held_entities`; the second run changes nothing; `entities_state absent` releases the entity; `state absent` deletes the hold; check mode reports without writing | passed: the first run created the hold and held the path, the second changed nothing, `state absent` released the entity and deleted the hold; the array releases a held entity with `PATCH /legal-holds/held-entities ... released=true` (DELETE is 405), a directory needs `recursive=true` on the release too, and a hold applied or released moments ago stays "in use" for several seconds, so the module retries the delete; `entities_state absent` alone is not run on the array |
| 9 | `housekeeping.yml` with `housekeeping_interval` lowered to a few minutes, `selftest_checks` the five defaults, `prometheus_url` empty, `showback_bucket eda-state` | on the first tick `selftest.yml` records kind `selftest` at info with per-check results: `rest` (GET `/arrays`) ok, `s3` (a get of `cursors/object_audit/fb-a.json` in `eda-state`, ok on 200 or 404) ok, `audit` (the `eda-audit` policy enabled with members and filters) ok, `telemetry` ok while the test-1 target exists or skipped with `telemetry_endpoint` empty, `prometheus` skipped; `showback.yml` writes `showback/<yyyy-mm-dd>/fb-a.json` to `eda-state` with `date`, `array`, `file_systems`, `buckets`, `accounts` and `totals`, records kind `showback` at info and `set_stats` `date`, `object_key`, `file_systems`, `buckets`; on the second tick showback skips because the object exists and selftest runs again; `dr_drill.yml` runs and does nothing outside `dr_drill_hour`; a check made to fail turns the selftest record critical with `failed` naming it | passed (both playbooks run by hand, not from the tick): `showback.yml` wrote the day's `fb-a.json` to `eda-state` (3 file systems, 3 buckets, 1 account) and left it alone on the second run; `selftest.yml` recorded rest, s3 and audit ok and telemetry and prometheus failed (target disabled, no receiver) at critical, the intended verdict |

## The platform

Run against the containerized platform from a clone of this repository on
its host: the two images built there and loaded into the stores the
platform uses, `aap/seed.yml` run with the site vars (`FBEDA_VARS` for the
activations, `-e @vars/local-<site>.yml` for the seed), then the objects
exercised through the platform and the orchestrator. The S3 traffic, the
human changes and the Alertmanager posts come from the client VM.

| # | Test | Expected | Result |
| --- | --- | --- | --- |
| 1 | `make check` on a clean checkout, with `lint-ao` and the platform unit tests | black, pyflakes and yamllint pass over the plugins, the modules, `tools/`, `slack/` and the tests; `tools/ao_lint.py` passes every definition against `orchestrator/job_templates.txt`; the unit tests pass; the playbook syntax check passes | passed: black, pyflakes and yamllint clean, `ao_lint` passes the seven definitions, 707 unit tests, every playbook syntax-checks |
| 2 | `aap/seed.yml` against an empty `FlashBlade` organization with the site vars and the credentials in the environment; then the same run again | every object of the seed exists: the organization, the five credential types, the credentials (`FlashBlade - <array>` per array, `FlashBlade fleet - FlashBlade`, `FlashBlade object store - <array>`, `Orchestrator service account - flashblade-eda`, `Slack webhook - flashblade-eda`, `Platform user - flashblade-eda`, `Source control - flashblade-eda`), the inventory `FlashBlades` with localhost and one host per array, the project `FlashBlade EDA` on both sides, `FlashBlade EDA EE` and `FlashBlade EDA DE`, the 35 job templates with their site keys and credentials, the workflow `FlashBlade - capacity reclaim (controller tier)`, the three schedules, the notification template `Slack - flashblade-eda` and fifteen activations; the second run changes nothing but the EDA credentials, which the platform does not read back | passed: every object exists, the fifteen activations included; the second run changed only the EDA credentials, which the platform does not read back. A busy gateway answers 503 to single tasks and times out the activations listing; the seed retries the listing in pages of 50, and a rerun finishes what a 503 stopped |
| 3 | `FlashBlade - notify` and `FlashBlade - telemetry setup` launched by hand against one array | the jobs run green in `FlashBlade EDA EE`: py-pure-client and this collection's `purefb_*` modules import, `tasks/resolve_array.yml` picks the array from the fleet credential and the record is written | passed: `FlashBlade - notify` runs green (the record kept out of Slack by the warning floor) and `FlashBlade - telemetry setup` runs green through `purefb_telemetry_target` and `purefb_telemetry_policy` (target present, 12 of 12 policies attached, the test endpoint's 400 recorded) |
| 4 | `aap_eda_activations_enabled: true` and the seed run again; then `tools/activation_audit.py` against the platform | fifteen activations `FlashBlade rulebook - <name>` running from `FlashBlade EDA DE` (pull policy `never` on the loaded image), enabled ten seconds apart; the audit reports every one `ok` and exits 0 | passed in part: the fifteen activations exist with the extra vars the seed renders (the audit finds no drift), the decision environment image loaded through the workers' socket runs them, and each of the seven exercised below ran when enabled; the lab controller's running cap (12) is shared with the sibling project's ten activations, so the FlashBlade activations ran two at a time, in rotation, and the audit's exit status reports the disabled ones, as it should |
| 5 | the polling path on the platform: an NTP server added on `fb-a` by hand (`config_drift.yml`, `change_control.yml`) | the config-drift activation launches `FlashBlade - remediate NTP` through the fleet credential and the baseline servers are back on the array; the change-control activation launches `FlashBlade - reconcile` | passed, both paths: the change-control activation launched `FlashBlade - reconcile` forty seconds after the human's NTP change (recorded `no baseline for this array`: a platform job has no baseline checkout, the documented limit), and the config-drift activation launched `FlashBlade - remediate NTP` on its next poll, after which the array's NTP servers read back as the baseline |
| 6 | object audit on the platform: a boto3 `GetObject` of the canary with the `eda-app` key from the client VM | within one rotation plus the poll interval the object-audit activation, with `FlashBlade object store - fb-a` injecting the `FB_OBJ_*` variables, launches `FlashBlade - canary`; the key reads back `enabled false` (re-enabled afterwards) and the cursor copy in `eda-state` moves | passed: the canary read as the `eda-app` key was followed within two minutes by `FlashBlade - canary` launched by the object-audit activation (the `FlashBlade object store` credential injecting the `FB_OBJ_*` variables) and the key read back disabled (`outcome: key disabled`) |
| 7 | the Alertmanager path: the client VM's Alertmanager pointed at `/alerts` on the platform host's `alertmanager_port`; the telemetry target disabled for longer than the silence window, then re-enabled | `FlashBladeTelemetrySilent` reaches the alertmanager activation and launches `FlashBlade - telemetry check`, whose record carries verdict `pipeline`; the resolved alert launches `FlashBlade - notify` at info | passed: with the target disabled, `FlashBladeTelemetrySilent` reached the alertmanager activation through the client VM's Alertmanager nine minutes later and launched `FlashBlade - telemetry check` (verdict `pipeline`, array and receiver reachable, target disabled); with the target re-enabled the resolved alert launched `FlashBlade - notify` at info; Alertmanager re-sends the group as further policies go silent, one check job per notification |
| 8 | the syslog path: a syslog server on `fb-a` with the URI `tcp://<platform host>:<syslog_port>` and the management service; a human audit line (a command on the CLI) | the array holds a TCP connection to the syslog activation, the `purity.audit` line is parsed into fields and the human-action rule launches `FlashBlade - notify` with the user, the array and the command | passed: with the array's management syslog pointed at the platform host's `syslog_port` (the array's own test message delivered), the syslog activation parsed each `purity.audit` line of a human's `purefs` commands (user, controller, command and arguments, source address, audit id, time) and launched `FlashBlade - notify` within three seconds of the command |
| 9 | `FlashBlade - capacity reclaim (controller tier)` launched by hand | the request job publishes its artifacts, the workflow pauses at the approval node, the approval is given from the Slack card (the bot) or in the platform's UI, the reclaim node rebuilds the request from the artifacts and records each refusal or that nothing is pending, and the notify node runs | passed: the request job published `request_id`, `candidate_count` and `candidates`, the workflow paused at the approval node, the Slack bot's card carried the approval back as its platform user, the reclaim node rebuilt the request from the artifacts (`rebuilt_from: job artifacts`, nothing pending, `result: done`) and the workflow completed |
| 10 | the orchestrator: the capacity route (a fixture event posted through `FlashBlade - orchestrator post`); `day2_grow_fs` started from the API against `eda-demo-quota`; `dr_drill_schedule.json` published | the capacity execution runs the request, pauses at its approval and on approval runs `FlashBlade - capacity reclaim` to completion; the grow definition notifies, pauses at its approval, grows the file system through `FlashBlade - grow file system` and notifies with `old_size`, `new_size` and the job link; publishing the drill definition creates its schedule | passed: `tools/ao_sync.py` validated, created and published the seven definitions (a second run reports them unchanged); the capacity event posted through the template started an execution that ran the request with the event array's credential and, with nothing pending, took the no-candidates notify to completion; posted again with a destroyed file system it paused at the approval, the Slack card decided it and the reclaim eradicated that file system (`result: done`); `day2_grow_fs` started from the API paused at its approval, the Slack decision ran the grow (1.25 to 1.5 GiB, `outcome: grown`) and the closing notify; publishing the drill definition created its Temporal schedule (next run 03:00 UTC) |

The four schedules fire on their own: `FlashBlade - showback (daily)` and
`FlashBlade - selftest (daily)` launch their templates at their hours, and
at 03:00 UTC both drill schedulers launch `FlashBlade - DR drill`. The
orchestrator's execution starts first and promotes, reads and demotes the
replica in about six minutes; the controller schedule's copy waits for it
(the template does not allow simultaneous jobs), finds the day's marker
and records the drill as skipped, so a site keeps one of the two
schedulers.

## Replayed only

Exercised from fixtures under `tests/fixtures/`, not induced on the array:
a blade fault and a degraded LAG (`tests/replay_health.yml`), the alert
routes (`tests/replay_alerts.yml`), days to full (`tests/replay_capacity.yml`),
a client's share, the object-side fixture (a canary hit, a landing marker,
an evidence put, a denied-storm rollup and a quota health event through
`tests/replay_object_audit.yml`), a posture event with every finding kind
(`tests/replay_posture.yml` from `tests/fixtures/posture.yml`), a
connection down, a paused bucket link, a lagging link that escalates and a
recovery (`tests/replay_replication.yml` from
`tests/fixtures/replication.yml`), three Alertmanager payloads in the shape
`ansible.eda.alertmanager` emits (a `FlashBladeTelemetrySilent` firing, a
`FlashBladeCapacityHigh` firing and a resolved alert,
`tests/replay_alertmanager.yml` from `tests/fixtures/alertmanager.yml`),
and a client hog, a hogging file system and a calm capped file system
(`tests/replay_contain.yml`). The rule generator is exercised against a
fixture listing (`tests/fixtures/telemetry_rules.json`) and compared with
the golden `tests/fixtures/flashblade.rules.yml` by the unit tests. The
replay rulebooks are kept rule-for-rule identical to the live ones by the
unit test, so a replayed event takes the same path as a live one.

## On Purity//FB 4.8.8

The same array after its upgrade, REST up to 2.30, through
`tests/e2e_sources.sh`, the source plugin run directly and the playbooks
run from a checkout.

| # | Test | Expected | Result |
| --- | --- | --- | --- |
| 1 | the eight REST signals (`tests/e2e_sources.sh`, then the telemetry, posture, replication, config, sessions and health signals run directly for two minutes) | every signal answers at 2.30 with the event names of the tables above and no `purefb_poll_error` | passed: alert, health, capacity, array, client, file-system and bucket performance, config, session, posture, replication and array-connection events; no poll error |
| 2 | a maintenance window opened and closed, the identify LED lit and cleared on `CH1.FB1`, the audits signal with a ten-minute lookback | `maintenance_active` true on the health event; the LED changes; the audit entries name the window and the LED | passed |
| 3 | `purity.alert` lines captured during the upgrade | parsed, the multi-word `Waiting To Downgrade` state included | passed after the layout fix; the lines are fixtures of `tests/unit/test_syslog_parse.py` |
| 4 | the telemetry metric policies at 2.30 | `tools/telemetry_rules.py --check --array-name fb-a` agrees with the committed rule file | passed: twelve rules naming collections at 30 s or 5 min; `prometheus/flashblade.rules.yml` is generated from that listing and loaded by the lab's Prometheus |
| 5 | `telemetry_check.yml` and the selftest's `rest` and `telemetry` checks against the array | the target is read at the REST version `tasks/fb_rest.yml` settles (2.30) and the delivery test answers | passed: verdict `healthy`, the test answers 200 with a reachability result per controller; selftest `rest` Purity//FB 4.8.8, `telemetry` target enabled |
| 6 | the older REST versions the playbooks pin (2.16 maintenance windows, 2.17 QoS policies, 2.20 object audit and log targets) | still served | passed |

## Not verified

- `tools/activation_restart.py` through a boot: its tests run against a
  fake controller, and a run on the platform with every FlashBlade
  activation already running touched nothing; no reboot has exercised it.
- Of the management syslog, `purity.test` lines from the array: the
  `purity.audit` and `purity.alert` layouts are confirmed from live lines;
  `purity.test` is parsed as the Admin Guide documents.
- The data-audit JSON branch of the listener: SMB audit needs an Active
  Directory join the lab lacks; NFS audit needs Purity//FB 4.8.0, which the
  lab array now serves and no test has used yet.
- A real hardware fault, a hot array, a full array: replayed or induced on
  lowered thresholds only. The health rule on a real fault (the gauge's
  enum is unpublished; 5 is observed for every healthy component),
  `FlashBladeCapacityHigh` and the other hand-written Prometheus rules
  firing on real series, and the containment rules firing from live
  telemetry (the lab is idle, so `baseline_known` is false and the rules
  are replayed).
- The QoS cap and lift (`qos_cap.yml`, `qos_lift.yml`): written against
  `/qos-policies` and `patch_file_systems qos_policy` through the raw REST
  task and not run on an array; the tarpit test exercises the same
  endpoints through `purefb_qos`.
- A maintenance window older than REST 2.16: the gate reads false there by
  design and no such array is in the lab. `purefb_maintenance extend`.
- Scale beyond one array; the 4.5.6 peer is reached only for the array
  connection and the DR drill.
- Of the object side: the landing command, the legal hold on arrival, the
  honey, error-storm and mass-delete rules and the `posture.yml` actions,
  unit-tested and replayed only. The `posture` signal itself runs against
  the lab: one `purefb_posture` with the default export policy `all` as a
  world-open export, `_smb_share_allow_everyone` as an Everyone-full-control
  share policy, no public bucket (the array reports
  `bucket-and-objects-not-public`, which is not public), no uncovered
  bucket, no old key, no expiring certificate.
- Kafka object notifications (Purity//FB 4.10.a): the bucket tail is the
  only object transport the lab can show.
- `housekeeping.yml` itself (its three playbooks run by hand, the drill
  with `dr_drill_force`); `purefb_fs_session` in check mode and the
  `locks_port` webhook; `purefb_eradication` beyond check mode (no change
  at the array's own values, a longer delay reported as a change; never
  written on the lab); `replication_resume.yml` and a paused bucket link
  (no bucket link exists; replayed only); the certificate renewal (no
  expiring self-signed array certificate; the peer's expired CA-issued one
  was replaced through an import, the path the playbook refuses by
  design); a TLS telemetry target and a success body from `GET
  /telemetry-targets/test` (400 even while pushes land); the export-policy
  form of the quarantine (the scratch file systems use the legacy
  `nfs.rules` string, so the `before_rule_name` insert and the rule delete
  are unit-tested only); an SMB session termination (no SMB client in the
  lab); `renew_certificate.yml` (replayed from the posture fixture).
- On the platform: this repository's own bot instance
  (`slack/approval_bot.py` with its `flashblade_eda_` action prefix; on a
  platform shared with the sibling, one bot pointed at both systems
  carries the cards, because one Slack app has one bot); a second
  object-store array and its second object-audit activation; the Source
  Control credential's removal once the repository is public (the seed
  creates none when the token variable is unset and never deletes one);
  the three other day-2 definitions
  (`day2_identify_led`, `day2_qos_cap`, `day2_quarantine_client`) and
  `replication_paused_approval.json` from a live paused link, linted and
  unit-tested only; all fifteen activations running at once (the lab
  controller's running cap is shared with the sibling, so they run two at
  a time); and why the alertmanager activation holds a core busy on the
  platform while the same rulebook idles at a few percent standalone, an
  open question.
