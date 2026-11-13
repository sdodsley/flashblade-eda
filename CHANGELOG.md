# Changelog

## 0.1.0 (unreleased)

The first four parts of flashblade-eda: Event-Driven Ansible for Everpure
FlashBlade, a prototype and the sibling of flasharray-eda. Written against
Purity//FB 4.6.4 (REST 2.21) on a FlashBlade//S200 with py-pure-client
1.98.0 and everpure.flashblade 1.29.0; part 1 (REST pull and management
syslog) and part 2 (the object side) have both passed their lab exit
tests; part 3 (telemetry, containment and replication) has passed all
nine of its lab exit tests, the telemetry one against a live Prometheus
OTLP receiver and the DR drill against the 4.5.6 peer; part 4 (the platform
port of the sibling: the platform seed and its two images, the platform
rulebooks, the orchestrator definitions and tools, the Slack approval bot)
is built and unit-tested. The tests are listed in
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
  fields the drift rulebook compares.
- Part 2 adds the `posture` signal to `purefb_events`: one `purefb_posture`
  per poll with the public buckets (`public_status`,
  `public_access_config`), the buckets outside every enabled audit policy
  (minus `coverage_exclude`), world-open NFS export rules and legacy
  `nfs.rules` (minus `posture_exclude_file_systems`), access keys older
  than `key_max_age_days`, certificates inside `cert_warn_days`, share
  policies that grant everyone full control, a count for each and
  `issue_count`; every read is optional, so an array without an endpoint
  reports an empty list.
- Part 3 adds the `replication` signal to `purefb_events` (the reserved
  list it was refused from is now empty; an unknown signal is still
  refused): `get_array_connections`, `get_targets`,
  `get_file_system_replica_links` and `get_bucket_replica_links`, each
  optional, feed one `purefb_replication` per array per poll (the
  connections with `status`, `type`, `version`, `replication_addresses`,
  `management_address`, `encrypted` and `throttled`; `connections_down`;
  the S3 targets and `targets_down`; `fs_links` and `bucket_links` with
  `status`, `status_details`, `direction`, `paused`, `lag_ms`,
  `recovery_point`, `recovery_point_age_s`, `policies` and the bucket
  backlog; `links_unhealthy` against `replication_ok_statuses`,
  `links_paused`, `links_lagging` against `replication_lag_warn_s`,
  `max_lag_ms`, `issue_count`, `maintenance_active`), a
  `purefb_replica_link` per link whose state is worth a rule and once on
  recovery (`kind`, `local`, `remote`, `remote_object`, `unhealthy`,
  `lagging`, `paused`, `recovered`, `consecutive_issue_polls`, `escalate`
  at `replication_escalate_polls`) and a `purefb_array_connection` per
  connection whose status changed (`status`, `previous_status`,
  `connected`). New arguments `replication_lag_warn_s` (900),
  `replication_ok_statuses` (replicating, idle, connected, healthy),
  `replication_escalate_polls` (5) and `replication_interval`.
  `purefb_fs_performance` and `purefb_bucket_performance` gain
  `eda_tarpit`, true when the object's QoS policy is `eda-tarpit`
  (`eda_capped` keeps meaning any `eda-cap-*` policy).
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
- Part 3 adds eight more in the same style, each with check mode,
  idempotency and the collection's `module_utils` guard; the REST-only
  ones carry a small helper that logs in with the token, uses the session
  header, logs out in a `finally` and never puts the token in a message:
  `purefb_telemetry_target` (REST: `name`, `endpoint` as `host:port[/path]`
  with a scheme stripped and warned about, `transport`
  `otlp-http-protobuf` or `otlp-http-protobuf-without-tls`, `source` the
  network interface, `enabled`, `policies` to attach the target to without
  removing other targets, `test` to run the array's delivery test, `state`;
  returns `target` and `test`), `purefb_telemetry_policy` (REST: `name`
  one of the twelve built-ins, `targets` as the full list; returns
  `policy` with `enabled`, `targets`, `metric_names` and `resolution_ms`;
  `enabled` is read-only on REST 2.21), `purefb_maintenance` (`state`,
  `timeout` in hours 1 to 48, default 4, `extend` to delete and re-post;
  returns `window`), `purefb_qos` (`name`, `max_total_ops_per_sec` of 100
  or more, `max_total_bytes_per_sec`, `enabled`, `file_systems` and
  `buckets` to assign the policy to, `state`; absent clears the policy
  from the listed objects and from any the array reports carrying it,
  then deletes; returns `policy`), `purefb_fs_session` (an action module
  without `state`: `kind` sessions, locks or both, `client`, `user`,
  `protocols`, `disruptive`, `file_system`, `path`, `recursive`, `inode`;
  one of client, user or file system required; lists first, removes only
  what matched, changed only when something went; returns `removed` with
  `sessions`, `locks` and `items`), `purefb_legal_hold` (`name`,
  `description`, `state`; `file_system`, `paths` and `recursive` manage
  held entities under `entities_state`, the removal over REST because the
  SDK has no delete; returns `hold` with `held_entities`),
  `purefb_alert_flag` (REST: `alert` the numeric name, `flagged`; returns
  `alert` with `name`, `code`, `flagged`) and `purefb_eradication`
  (`eradication_delay_days` 1 to 30, `manual_eradication` all-enabled or
  all-disabled, `force`; a ratchet that refuses to shorten the delay or
  re-enable manual eradication without `force`; returns
  `eradication_config`). Unit-tested with the fake client and a `requests`
  stub.

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
- Part 3 adds four rulebooks. `alertmanager.yml` on
  `ansible.eda.alertmanager` (`alertmanager_host`, `alertmanager_port`)
  routes by `event.alert.labels.alertname`: `FlashBladeTelemetrySilent`
  firing runs `telemetry_check.yml`; `FlashBladeHealthNotOk` notifies
  critical; `FlashBladeCapacityHigh` runs `capacity_request.yml` (with
  `request_dir`, `s3_requests_enabled` and `object_store`, as the
  FlashArray one); `FlashBladeLatencyHigh`, `FlashBladeConnectorErrors`
  and `FlashBladeReplicationStalled` notify warning; any other firing
  alert notifies at its severity label (default warning); a resolved
  alert notifies info; the array is read from the fixed `array_name`
  label the rule file sets. `replication.yml` on the `replication`
  signal: an array connection not connected notifies (throttled an hour
  by array and remote) and its return is logged; a paused replica link
  runs `replication_resume.yml` behind `replication_auto_resume`, else
  notifies; an unhealthy or lagging link notifies at warning and, once
  `escalate` is true, at critical with a six-hour throttle; a recovered
  link and the per-poll summary are logged. `contain.yml` on the
  telemetry signal: a client at `contain_min_share_pct` of the array's
  ops for `contain_consecutive_polls` polls, with the array's write
  latency at `baseline_latency_ratio` and `baseline_known`, runs
  `quarantine_client.yml` behind `ir_auto_contain`, else notifies
  critical; a file system hogging the array with no cap runs
  `tarpit.yml apply` behind `ir_auto_tarpit`; a tarpitted file system
  normal for `contain_release_normal_polls` polls runs `tarpit.yml
  lift`; a second ruleset, "FlashBlade containment approvals", is an
  `ansible.eda.webhook` on `locks_port` under `APPROVAL_TOKEN` taking
  `{array, action, client, file_system}` for `quarantine`, `release`,
  `release_locks` (`release_locks.yml`), `tarpit` and `lift` (there is no
  separate `locks.yml`). `housekeeping.yml` on `ansible.eda.tick` every
  `housekeeping_interval` seconds: `selftest.yml` on every tick,
  `showback.yml` (the playbook decides by the date), `dr_drill.yml` with
  `dr_drills` (the playbook runs at `dr_drill_hour` and once a day per
  file system). `posture.yml` gains "Expiring certificate - renew":
  `expiring_cert_count > 0` with `certs_auto_renew` runs
  `renew_certificate.yml`.
- The gates of the FlashArray project carry over: `maintenance_active`
  (false where the endpoint is absent, REST < 2.16), `automation_users`
  and, on S3 events, `automation_keys`, literal throttles, per-site
  switches that fall back to notify (twelve after part 3), approval on
  every eradication, and no switch at all for lock breaking, which only
  the `locks_port` webhook reaches.

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
- Part 3 adds ten playbooks and one task file, the array-facing ones in
  the shape of `grow_fs.yml` (`resolve_array` first, one alert-log record
  with `time`, `kind`, `severity` and `array`, one `set_stats` last):
  `telemetry_setup.yml` (the two telemetry modules create the target,
  attach it to `telemetry_policies` and run the array's test; `set_stats`
  `target`, `policies_attached`, `test_ok`), `telemetry_check.yml` (the
  target and its test through `fb_rest.yml`, a GET of the management
  VIP's `/api/api_version` and, with `prometheus_url`, of
  `/api/v1/query?query=up`; `verdict` pipeline, array, receiver or
  healthy, critical for array; `set_stats` `verdict`, `test_status`,
  `array_reachable`, `receiver_reachable`), `quarantine_client.yml`
  (`action` quarantine or release on one file system or on every live
  file system naming the client, minus `contain_exclude_file_systems`;
  refuses `contain_exclude_clients` and the array's own addresses; the
  legacy `nfs.rules` entry rewritten `rw` to `ro` after a re-read, or an
  export-policy rule `client <ip> permission ro access root-squash`
  posted before the first rule and deleted on release; record kind
  `quarantine` critical; `set_stats` `client`, `action`, `file_systems`,
  `changed_count`), `tarpit.yml` (`action` apply or lift: the `eda-tarpit`
  policy at `tarpit_ops_per_sec` through `purefb_qos`, assigned unless the
  file system already carries a policy, cleared on lift with the policy
  object kept; record kind `tarpit`; `set_stats` `file_system`, `action`,
  `policy`, `changed`), `release_locks.yml` (`purefb_fs_session` for a
  client's sessions, locks or both; record kind `release_locks` warning
  naming `approver`; `set_stats` `client`, `removed_sessions`,
  `removed_locks`), `replication_resume.yml` (the paused bucket link's
  target and credential read from `/bucket-replica-links`, then
  `purefb_bucket_replica paused: false`; record kind `replication`;
  `set_stats` `bucket`, `resumed`), `dr_drill.yml` (localhost; loops
  `dr_drills` into `tasks/dr_drill_one.yml`: resolve the peer, promote
  the replica file system, wait for `promotion_status`, read its space
  and export, demote with `discard_non_snapshotted_data` only when
  `dr_drill_discard`, always demoting in the always block; record kind
  `dr_drill` info on success and critical on failure; the marker
  `<showback_prefix>dr/<date>-<file system>.json` in the state bucket
  stops a second run the same day), `showback.yml` (file systems,
  buckets and accounts read from the array and written as
  `<showback_prefix><yyyy-mm-dd>/<array>.json` to `showback_bucket`,
  skipped when the object exists; record kind `showback`; `set_stats`
  `date`, `object_key`, `file_systems`, `buckets`), `selftest.yml`
  (array-facing, run for every array in `purefb_arrays` at once by
  `housekeeping.yml`; `selftest_checks` rest, s3, audit, telemetry and
  prometheus, the last two skipped when `telemetry_endpoint` or
  `prometheus_url` is empty; one record kind `selftest`, critical when
  any check failed; `set_stats` `checks`, `failed`) and
  `renew_certificate.yml` (only an `array` certificate whose `issued_by`
  equals `issued_to` is renewed with `purefb_certs generate` for
  `certs_renew_days`, keeping its common name and SANs; anything else is
  recorded as needing a human; record kind `certificate`; `set_stats`
  `certificate`, `renewed`, `valid_to`).

### Prometheus

- `tools/telemetry_rules.py` reads an array's
  `/telemetry-metrics-policies/rules` (`telemetry_rules.py <fb_url>
  <TOKEN_ENV> [--out prometheus/flashblade.rules.yml] [--silence-minutes
  10] [--array-label device_name]`, the token read from the named
  environment variable) and writes a Prometheus rule file: one
  `FlashBladeTelemetrySilent` alert per policy on `absent_over_time` of
  the policy's first metric over twice its resolution or
  `--silence-minutes`, whichever is longer (labels `severity critical`,
  `domain telemetry`, `policy`), plus the hand-written
  `FlashBladeHealthNotOk`, `FlashBladeCapacityHigh` (with
  `annotations.value` as the FlashArray one), `FlashBladeLatencyHigh`,
  `FlashBladeConnectorErrors` and `FlashBladeReplicationStalled`; every
  alert sets a fixed `array_name` label from the `--array-label` resource
  attribute, and the expressions whose metric shape is unverified carry a
  `verify:` comment. A unit test runs it against
  `tests/fixtures/telemetry_rules.json` and compares with the golden
  `tests/fixtures/flashblade.rules.yml`.
- `prometheus/flashblade.rules.yml` is the generator's output for the lab
  array's twelve policies; `prometheus/prometheus.example.yml` configures
  the OTLP receiver (no scrape jobs; `promote_all_resource_attributes`,
  `translation_strategy: UnderscoreEscapingWithSuffixes`, the rule file,
  Alertmanager on 127.0.0.1:9093; the `--web.enable-otlp-receiver` and
  `--web.enable-lifecycle` flags in a comment);
  `prometheus/alertmanager.example.yml` routes to the EDA listener
  (`http://127.0.0.1:5002/alerts`, `send_resolved`, grouped by
  `alertname` and `array_name`); `prometheus/README.md` explains how the
  array pushes (the endpoint form `<host>:9090/api/v1/otlp`, the
  transport without TLS for a plain receiver, the source VIP), how to run
  Prometheus 3.x and Alertmanager under systemd, the ssh reverse tunnel
  for a rulebook host behind NAT, the generator and what the silence rule
  means.

### Platform

- `aap/seed.yml` builds the Ansible Automation Platform 2.7 objects from
  the environment and `aap/vars/example.yml`, idempotently and never
  deleting: the organization `FlashBlade`; the credential types
  `FlashBlade API` (one `FlashBlade - <array>` credential per array,
  injecting `fb_url`, `api_token` and `credential_array`), `FlashBlade
  fleet` (`FlashBlade fleet - <organization>`, every array as a JSON list:
  `purefb_fleet_json` on the controller, `purefb_arrays` on the EDA side),
  `FlashBlade object store` (`FlashBlade object store - <array>` per entry
  of `aap_object_stores`: the REST pair, the S3 endpoint and the tail and
  state keys as the `FB_OBJ_*` environment variables), `Orchestrator
  service account` and `Slack webhook` (defined as the sibling defines
  them, so either seed is idempotent against the other), the `Platform
  user - flashblade-eda` credential the `run_job_template` actions launch
  as, and `Source control - flashblade-eda` from the variable
  `aap_scm_token_env` names while the repository is private; the inventory
  `FlashBlades`; the project `FlashBlade EDA` on both sides; the execution
  environment `FlashBlade EDA EE` and the decision environment `FlashBlade
  EDA DE` (`aap_ee_pull`, `aap_eda_de_pull_policy`); 35 job templates
  `FlashBlade - <playbook words>`, one per playbook, with the fleet
  credential, `aap_job_extra_vars` and the per-template site keys
  (`object_store` and the first object store credential on the S3 templates,
  `dr_drills` on the DR drill, the showback, selftest, telemetry and
  containment keys on theirs, the Slack webhook on notify, the
  orchestrator credential on orchestrator post); the controller-tier
  workflow `FlashBlade - capacity reclaim (controller tier)` (request,
  approval, reclaim with `rebuild_from_artifacts` and `reclaim_action
  eradicate_pending`, notify on rejection or failure); the schedules
  `FlashBlade - DR drill (nightly)`, `FlashBlade - showback (daily)` and
  `FlashBlade - selftest (daily)` from `aap_schedules` in
  `aap_schedule_timezone`, which replace the housekeeping tick; the
  notification template `Slack - flashblade-eda`; and one activation
  `FlashBlade rulebook - <name>` per platform rulebook with the site vars
  laid over `vars/example.yml` minus the injected keys, enabled ten
  seconds apart when `aap_eda_activations_enabled`. `aap/README.md`
  documents it.
- Two images. `aap/Containerfile.ee` builds the execution environment on
  the supported base with py-pure-client 1.98 or newer, boto3, requests,
  `everpure.flashblade` and this repository installed as the collection
  (the playbooks call `sdodsley.flashblade_eda.purefb_*` modules, which a
  project checkout cannot provide); `decision-environment/Containerfile`
  builds the decision environment on `quay.io/ansible/ansible-rulebook:v1.3.2`
  with the collection, `/opt/flashblade-eda` and the `/var` directories of
  the defaults (`make de`); `aap/execution-environment.yml`,
  `aap/decision-environment.yml` and
  `decision-environment/execution-environment.yml` are the ansible-builder
  v3 definitions. `docs/PACKAGING.md` describes the decision environment.
- Platform rulebooks under `extensions/eda/rulebooks/platform/`, fifteen
  files generated by `tools/platform_rulebooks.py` and compared with the
  committed ones by a unit test: every `run_playbook` becomes
  `run_job_template` in `aap_organization`, the webhook rulesets (capacity
  approvals, git push, maintenance windows, containment approvals) are
  left out, and `housekeeping.yml` has no platform file because the
  controller schedules take over.
- `tools/activation_audit.py` compares every `FlashBlade rulebook - *`
  activation's extra vars with what the seed would render, flags example
  placeholders and disabled activations, and with `--recreate` recreates
  the drifted ones.
- The orchestrator: `playbooks/orchestrator_post.yml` posts a
  `flashblade-eda/1` envelope to `orchestrator_paths.capacity`
  (`fb_capacity`) and, from the rule "Replica link paused - hand the event
  to the orchestrator" in `replication.yml`, to
  `orchestrator_paths.replication` (`fb_replication`); the local resume
  and notify rules for a paused link run only when `orchestrator_enabled`
  is false. Seven definitions under `orchestrator/` (schema 2.0.0,
  unwrapped trigger bodies, `credential_names`, approver groups `admins`
  and `approvers`, `REPLACE_AT_SYNC` placeholders, site keys left on the
  templates): `capacity_reclaim_approval.json`,
  `replication_paused_approval.json` (a one-hour decision window),
  `day2_grow_fs.json`, `day2_identify_led.json`, `day2_qos_cap.json`,
  `day2_quarantine_client.json` and `dr_drill_schedule.json`
  (`dr_drill_force: true` from a daily cron); `orchestrator/job_templates.txt`;
  `tools/ao_lint.py` (`make lint-ao`, with a bad fixture per rule under
  `tests/fixtures/orchestrator/` and a cross-check of every
  `${node.artifacts.key}` against the playbooks' `set_stats`);
  `tools/ao_sync.py`; `tests/mock_orchestrator.py`; `docs/ORCHESTRATOR.md`
  and `orchestrator/README.md`.
- Slack: `slack/approval_bot.py` (action prefix `flashblade_eda_`) posts
  pending orchestrator and controller approvals as cards and carries the
  decision back with the Slack user's name; the card follows its approval
  to the end (reminders in its thread at half and ninety percent of the
  window, the final status read when it leaves the queue, an expired
  orchestrator approval paged in the channel and recorded through
  `NOTIFY_TEMPLATE`, default `FlashBlade - notify`), the posted cards are
  kept under `STATE_DIR` across a restart, and `SLACK_ROUTES` and
  `NOTIFY_ROUTES` send another project's cards (by workflow name prefix)
  to its own channel and notify template, so one bot instance serves two
  projects on one Slack app; `slack/README.md` states the one-bot-per-app
  rule for a site that runs the sibling too.
- The Makefile's `lint` covers `tools/` and `slack/`, `lint-ao` lints the
  definitions and `check` runs both; CI gains the `lint-ao` step; the unit
  tests gain `test_platform_rulebooks.py`, `test_activation_audit.py`,
  `test_orchestrator_bridge.py`, `test_ao_lint.py`, `test_ao_sync.py`,
  `test_approval_bot.py` and the seed and credential-type checks in
  `test_playbook_artifacts.py`.

### Repository

- The repository is the collection `sdodsley.flashblade_eda` (`galaxy.yml`,
  `meta/runtime.yml`, `make collection`); `run.sh`, `env.sh` and
  `env.example` read `FBEDA_*` and `FB_A_*` / `FB_B_*`; `make check` runs
  black, pyflakes, yamllint, `lint-ao`, the unit tests and a playbook
  syntax check, and GitHub Actions runs the same.
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
- Part 3: `vars/example.yml` gains the replication keys
  (`replication_interval`, `replication_lag_warn_s`,
  `replication_ok_statuses`, `replication_escalate_polls`,
  `replication_auto_resume`, `dr_drills`, `dr_drill_hour`,
  `dr_drill_discard`), the containment keys (`ir_auto_contain`,
  `ir_auto_tarpit`, `tarpit_ops_per_sec`, `contain_consecutive_polls`,
  `contain_min_share_pct`, `contain_release_normal_polls`,
  `contain_exclude_clients`, `contain_exclude_file_systems`, `locks_port`),
  the telemetry keys (`telemetry_target_name`, `telemetry_endpoint`,
  `telemetry_source`, `telemetry_transport`, `telemetry_policies`,
  `telemetry_silence_minutes`, `telemetry_array_label`, `prometheus_url`),
  the housekeeping keys (`housekeeping_interval`, `showback_bucket`,
  `showback_prefix`, `selftest_checks`) and the certificate keys
  (`certs_auto_renew`, `certs_renew_days`; `cert_warn_days` is reused);
  the unit tests gain `ansible.eda.tick` among the known sources, the new
  playbooks' required extra vars and artifact keys, the polling-trio check
  for `replication.yml` and the first ruleset of `contain.yml`, and
  `tests/replay_replication.yml` (`tests/fixtures/replication.yml`: a
  connection down, a paused bucket link, a lagging link that escalates, a
  recovery), `tests/replay_alertmanager.yml` (`tests/fixtures/alertmanager.yml`:
  a `FlashBladeTelemetrySilent` firing, a `FlashBladeCapacityHigh` firing,
  a resolved alert) and `tests/replay_contain.yml` (a client hog, a hogging
  file system, a calm capped file system); `prometheus/` and `tools/` are
  development material already in `build_ignore`.
- Apache-2.0; SPDX headers on every Python, YAML and shell file. The
  modules re-license under the collection's GPL-3.0-or-later when they
  move there ([docs/PACKAGING.md](docs/PACKAGING.md)).
