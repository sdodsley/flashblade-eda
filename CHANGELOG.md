# Changelog

## 0.1.0 (unreleased)

The first two parts of flashblade-eda: Event-Driven Ansible for Everpure
FlashBlade, a prototype and the sibling of flasharray-eda. Written against
Purity//FB 4.6.4 (REST 2.21) on a FlashBlade//S200 with py-pure-client
1.98.0 and everpure.flashblade 1.29.0; part 1 (REST pull and management
syslog) and part 2 (the object side) have both passed their lab exit
tests. The tests are listed in
[docs/VERIFICATION.md](docs/VERIFICATION.md), the array facts in
[docs/HARDWARE_NOTES.md](docs/HARDWARE_NOTES.md) and the design in
[docs/DESIGN.md](docs/DESIGN.md).

### Event sources

- `purefb_events` polls the REST API for alerts, audits, sessions, health,
  telemetry and config signals, reading each array's URL and token from
  the environment variables named by `fb_url_env` and `api_token_env`. It
  keeps the FlashArray plugin's skeleton (one asyncio task per signal, SDK
  calls in a thread pool, `request_timeout`, poll-error streaks with
  `poll_error_escalate`, the cached maintenance read) and emits FlashBlade
  events: alerts diffed locally with the transitions initial, opened,
  updated and closed (FlashBlade alerts are open or closed, nothing else)
  and the `flagged` bit; audits with `user_agent`, `ip_address`,
  `by_automation`, `automation_agent` and `destructive`; sessions with
  location and method; health with blades, DFM drives, LAG speed against
  its member ports and the LLDP neighbour of every faulted port; capacity
  with days-to-full from the array's seven-day history split by file
  system and object store, pending-eradication counts and top growers;
  per-file-system quota usage; array, per-client, per-file-system and
  per-bucket performance with ops shares and streaks; and the config
  fields the drift rulebook compares. The `replication` signal of the
  design is refused by name until a later part.
- Part 2 adds the `posture` signal to `purefb_events`: one `purefb_posture`
  per poll with the public buckets (`public_status`,
  `public_access_config`), the buckets outside every enabled audit policy
  (minus `coverage_exclude`), world-open NFS export rules and legacy
  `nfs.rules` (minus `posture_exclude_file_systems`), access keys older
  than `key_max_age_days`, certificates inside `cert_warn_days`, share
  policies that grant everyone full control, a count for each and
  `issue_count`; every read is optional, so an array without an endpoint
  reports an empty list.
- `purefb_syslog` receives what a FlashBlade syslog server sends: the
  `purity.alert` and `purity.audit` layouts of the management service,
  parsed into fields, and the one-JSON-object-per-line records of the
  `data-audit` service (`pf: fb`, and `pf: fa` from a FlashArray, one
  parser for both), with the TCP line limit raised to the 128 KB a
  data-audit record may need.
- `purefb_object_audit` (part 2) tails the log bucket an object-audit
  policy writes into, with boto3 against the data VIP and a read-only key
  named by `s3_access_key_env` / `s3_secret_key_env`. The log objects are
  NDJSON, one per authority per rotation window, keyed
  `<array id>/<prefix>/<yyyy>/<mm>/<dd>/<HHMMSS>.A<authority>.<sequence>.log`;
  the plugin lists with `StartAfter` from a floor `late_windows` rotations
  back, skips seen keys and emits one `purefb_s3_event` per record with
  the derived booleans (`canary`, `canary_allowed`, `landing` and
  `dataset`, `evidence`, `honey`, `approval` and `request` with
  `request_id`, `denied`, `delete`, `by_automation` from
  `automation_keys`), one `purefb_s3_rollup` per log object (counts by
  event name, access key, source address and error code; `error_storm`,
  `denied_storm`, `mass_delete`) and one `purefb_audit_health` per poll
  (newest log age and `stale`, the log bucket against its quota, the
  policies with log targets and members, `uncovered_buckets`,
  `members_without_filters`, `healthy`; the REST fields need a token).
  The cursor is a file under `cursor_dir` and, with a state key, a copy in
  the state bucket; `replay_objects` re-emits the newest N objects for
  tests; poll failures are `purefb_poll_error` with signal `object_audit`.

### Modules

- Three modules under `plugins/modules/` for the object-audit
  configuration the collection has no module for, in the collection's
  style and on its `module_utils`: `purefb_log_target` (bucket,
  `log_name_prefix`, `log_rotate` as `5m` to `24h`; create, update,
  delete, the array's refusal when in use surfaced), `purefb_audit_policy`
  (`enabled`, the full `log_targets` list, the member `buckets` made
  exactly the given set; absent removes the members first) and
  `purefb_bucket_audit_filter` (alphanumeric name of 1 to 63 characters,
  `actions` validated against the array's list when it answers, optional
  `s3_prefixes`; a filter that differs is deleted and recreated because
  the array has no PATCH). Check mode, idempotent, unit-tested with a fake
  AnsibleModule and client.

### Rulebooks

- Ten rulebooks: alerts (the runbook map keyed by FlashBlade alert codes,
  then the `flagged` bit as the handled marker), change control (the
  governed and destructive command lists of a FlashBlade: `purefs`,
  `purebucket`, `purepolicy`, `purelog`, ...), config drift (NTP and DNS
  reapplied; directory service, eradication, admin settings, SSO, Active
  Directory join and a missing data-audit syslog target notified), health
  (identify LED with the LLDP line, LAG degradation naming the switch
  ports, blade and drive issues), capacity (request and approved reclaim
  of file systems, buckets and snapshots; the hard-quota grow behind
  `fs_auto_grow`), user quota (alerts 1106 and 1107, a raise behind
  `auto_raise_user_quota`), client (a client IP as an inventory host, the
  QoS cap `eda-cap-<fs>` with hysteresis), maintenance (the array's one
  window opened, extended and closed through a bearer-token webhook),
  sessions (logins from outside `admin_cidrs`, failed attempts, alerts
  1140 and 1123) and syslog.
- Part 2 adds `object_audit.yml`: a canary read by a key not in
  `canary_readers` disables the key behind `canary_auto_disable_key`, else
  notifies critical; a honey suffix and a mass delete notify critical; a
  landing marker runs `landing.yml`; an evidence object gets a legal hold
  behind `legal_hold_on_arrival`; an approval object runs
  `s3_approval.yml`; an access-denied or error storm runs
  `s3_anomaly.yml` (the top key disabled behind
  `anomaly_auto_disable_key`); the log bucket at
  `log_bucket_quota_warn_pct`, a stale log where
  `audit_log_expected_continuous`, and a bucket without an enabled policy,
  a disabled policy or a member without a filter run or notify through
  `auditor_guard.yml`; a second ruleset on the audits signal catches a
  human `purepolicy`, `purelog` or `purebucket` change to the audit
  configuration and re-attaches behind `audit_auto_reattach`, paging in
  either case. And `posture.yml`: a public bucket reverted behind
  `posture_auto_revert`, world-open exports, old keys, expiring
  certificates, uncovered buckets and an SMB share policy granting
  Everyone full control notified once a day per array.
- `capacity.yml` passes `s3_requests_enabled` and `object_store` to
  `grow_fs.yml` and `capacity_request.yml`, which then also write the
  request object to the log bucket.
- The gates of the FlashArray project carry over: `maintenance_active`
  (false where the endpoint is absent, REST < 2.16), `automation_users`
  and, on S3 events, `automation_keys`, literal throttles, per-site
  switches that fall back to notify, approval on every eradication.

### Playbooks

- Act on the arrays through `everpure.flashblade` 1.29.0 or later:
  `purefb_ntp`, `purefb_dns`, `purefb_hardware`, `purefb_fs`,
  `purefb_bucket`, `purefb_snap`, `purefb_userquota`, `purefb_groupquota`.
  Where the collection has no module (maintenance windows, QoS policies,
  user quota reads, the alert `flagged` bit, an access key's `enabled`
  bit, a bucket's quota) `playbooks/tasks/fb_rest.yml` makes one raw REST
  call against `/api/<fb_api_version>/` with the inventory token exchanged
  for a session at `POST /api/login`.
- `notify.yml`, `alert_runbook.yml`, `capacity_request.yml`,
  `capacity_reclaim.yml` and `orchestrator_post.yml` are the FlashArray
  playbooks with FlashBlade objects; `flag_alert.yml`, `grow_fs.yml`,
  `user_quota.yml`, `client_diagnose.yml`, `qos_cap.yml`, `qos_lift.yml`
  and `maintenance_window.yml` are new.
- Part 2 adds seven: `canary.yml` and `s3_anomaly.yml` (disable one access
  key, refusing a key in `canary_readers` or `automation_keys`),
  `landing.yml` (run `landing_command` with `LANDING_ARRAY`, `LANDING_BUCKET`,
  `LANDING_KEY`, `LANDING_DATASET`, `LANDING_USER`, `LANDING_ACCESS_KEY_ID`
  and `LANDING_SOURCE_IP` in its environment; a failure is recorded, not
  fatal),
  `lock_on_arrival.yml` (`put_object_legal_hold` with the state key),
  `s3_approval.yml` (reads `approvals/<id>.json` and `requests/<id>.json`
  with the tail key, refuses a writer outside `approval_keys` or inside
  `automation_keys`, an unknown request or an unproposed action, grows the
  file system through `fb_rest.yml` after re-reading it, records
  `eradicate_pending` as approved for `capacity_reclaim.yml`, writes
  `receipts/<id>.json`), `auditor_guard.yml` (mode `quota`: raise the log
  bucket quota by `log_bucket_quota_step_pct` within
  `log_bucket_quota_max_bytes`; mode `reattach`: the three modules from
  `audit_baseline` behind `audit_auto_reattach`; always a critical record
  naming the reason) and `posture_revert.yml` (`purefb_bucket
  block_public_access` and `block_new_public_policies`). `tasks/s3_object.yml`
  is the S3 twin of `fb_rest.yml`: one get, head, put or legal hold with
  the keys of the array's `object_store` entry read from the environment,
  leaving `s3_status`, `s3_json` and `s3_error`. `grow_fs.yml` and
  `capacity_request.yml` also put the request to
  `<log_bucket>/requests/<id>.json` when `s3_requests_enabled`.

### Repository

- The repository is the collection `sdodsley.flashblade_eda` (`galaxy.yml`,
  `meta/runtime.yml`, `make collection`); `run.sh`, `env.sh` and
  `env.example` read `FBEDA_*` and `FB_A_*` / `FB_B_*`; `make check` runs
  black, pyflakes, yamllint, the unit tests and a playbook syntax check,
  and GitHub Actions runs the same.
- Part 2: `boto3>=1.26` in `requirements.txt`; `env.example` documents the
  `FB_A_S3_*` variables (endpoint, tail key, state key, app key for the
  tests); `vars/example.yml` gains `object_audit_arrays`, `object_store`,
  `audit_baseline` and the object-side thresholds and switches
  (`canary_auto_disable_key`, `anomaly_auto_disable_key`,
  `legal_hold_on_arrival`, `audit_auto_reattach`, `posture_auto_revert`,
  `s3_requests_enabled`, among others); the Makefile's lint covers
  `plugins/modules`; the unit tests cover the tail against a fake boto3
  client, the modules and the posture signal, and stub `boto3` when it is
  absent; `tests/replay_object_audit.yml` replays a canary hit, a landing
  marker, an evidence put, a denied-storm rollup and a quota health event,
  and `tests/replay_posture.yml` one posture event with every finding kind
  (`tests/fixtures/posture.yml`).
- Apache-2.0; SPDX headers on every Python, YAML and shell file. The
  modules re-license under the collection's GPL-3.0-or-later when they
  move there ([docs/PACKAGING.md](docs/PACKAGING.md)).
