# Hardware notes

Facts about the array, the SDK, the collection, ansible-rulebook and the
state model that shape the code, grouped by topic; [README.md](../README.md)
carries the short form. Array facts are from a read-only probe of a
FlashBlade//S200 on Purity//FB 4.6.4 (REST 2.21) unless stated; a legacy
FlashBlade on 4.5.6 (REST 2.17) is the replication peer of later parts.

## REST API

- The lab array negotiates REST 2.21 and py-pure-client 1.98.0 covers
  FB_2_0 to FB_2_30. The plugin must not call a method the negotiated
  client lacks: `get_telemetry_*` (SDK FB_2_29, although the array serves
  `/telemetry-targets` on 2.21), `get_software_installations` (absent on
  2.21), `get_workloads` (2.23).
- There is no `/alerts/events` on FlashBlade. The alerts signal fetches
  `get_alerts(filter="state!='closed'")`, falls back to an unfiltered
  `get_alerts(limit=...)` if the filter answers 400 (logged once), and
  diffs the result locally. An alert is `open` or `closed`, nothing else;
  there is no `closing` state to wait a day for.
- Alert fields: `action`, `code`, `component_name`, `component_type`,
  `created`, `description`, `duration`, `flagged`, `id`, `index`,
  `knowledge_base_url`, `name` (a numeric string, the plugin's
  `alert_id`), `notified`, `severity`, `state`, `summary`, `updated`,
  `variables`. Every alert carries `action` text and
  `knowledge_base_url` (`https://support.purestorage.com/?cid=Alert_<code>`).
- Alert codes the rules name: 1000 blade unhealthy, 1001 blade removed,
  1016 evacuation stuck (no space), 1019 NTP no qualified server, 1101
  file system space quota (80, 90, 100 %), 1102 directory-service user
  misconfigured, 1103 and 1104 snapshot count limits, 1106 user quota,
  1107 group quota, 1108 certificate expiry, 1109 and 1110 quota e-mail
  failures, 1112 object replication connection requires attention, 1123
  default `pureuser` password, 1130 daily snapshot limit, 1139 syslog
  message drop (audit), 1140 superuser login with a password. Codes 1000
  to 1099 are system, 1100 to 1199 customer controlled. The lab's six
  open alerts are four 1112 and one 1140.
- Audit fields: `arguments`, `command`, `id`, `ip_address`, `name`,
  `origin`, `scopes`, `subcommand`, `time`, `user`, `user_agent`,
  `user_interface`; the SDK model also has `system_event` and
  `correlation_id`. `user_agent` reads `pure/py-pure-client/<version>` for
  SDK callers, which is how `automation_agent` is derived without a user
  list; `automation_users` stays because the Manila driver and a QA
  harness use the same client. The audits signal reads newest first in
  pages of `audit_batch` and applies the time cursor locally, as on
  FlashArray.
- Session fields: `event` (login, logout, failed attempts), `event_count`,
  `location`, `method` (`API token`, password), `start_time`, `end_time`,
  `user`, `user_interface`, `identifying_details`. 116 on the lab.
- Hardware: `identify_enabled`, `index`, `model`, `part_number`, `serial`,
  `slot`, `status`, `type` (`ch`, `fm`, `fb`, `eth`, `psu`, ...),
  `details`, `speed`, `temperature`; 95 items on the lab, names like
  `CH1.FB1`, `CH1.FM1.ETH1`. Blade: `raw_capacity`, `status`, `progress`,
  `target`, `details` (evacuation progress); 10 blades. Drive:
  `raw_capacity`, `status`, `type` (`DFM`), `progress`, `details`; 40
  drives. Hardware connector: `connector_type`, `lane_speed`,
  `port_speed`, `transceiver_type`. Link aggregation group: `lag_speed`,
  `mac_address`, `port_speed`, `ports[]`, `status` (`healthy`); the lab's
  `uplink` is 8 x 100 GbE.
- LLDP (`/network-interfaces/neighbors`, REST 2.20): `local_port.name`
  (`CH1.FM1.ETH1`), `neighbor_chassis` `{name, description, addresses[],
  id}` (a Cisco Nexus with its management address), `neighbor_port`
  `{description "Ethernet1/13", id {type ifname, value}}`. The plugin
  caches it and refreshes the cache only while the port is healthy, so a
  down link still names the switch port.
- Maintenance windows (REST 2.16, Purity//FB 4.5.2): `GET/POST/DELETE
  /maintenance-windows`, one window of type `array`, `names=array`,
  `timeout` in milliseconds, 1 to 48 hours (default 4); a second POST
  extends it. The window suppresses every alert except 1016, 1026, 1101,
  1102, 1103, 1104, 1106, 1107, 1108, 1109, 1110, 1123 and 1130. There is
  no "environment" window. The gate reads false where the endpoint is
  absent.
- `/arrays`: `banner`, `default_inbound_tls_policy`,
  `encryption.data_at_rest`, `eradication_config {eradication_delay (ms),
  manual_eradication}`, `idle_timeout`, `network_access_policy`,
  `ntp_servers`, `time_zone`, `version`, `smb_mode`, `product_type`, `os`.
  There is no eradication-config endpoint; it is a field here and on each
  file system, array-wide and one-way for retention.
- `/arrays/space`: `capacity`, `parity`, `space {available_provisioned,
  available_ratio, data_reduction, destroyed, destroyed_virtual, shared,
  snapshots, total_physical, total_provisioned, total_used, unique,
  virtual}`, `time`. History: `resolution=86400000&start_time=<7 d ago>`
  returned 7 daily points; `limit` is not accepted with `start_time`;
  `type=file-system` and `type=object-store` split it.
- `/arrays/performance`: `bytes_per_op`, `bytes_per_read`,
  `bytes_per_write`, `others_per_sec`, `read_bytes_per_sec`,
  `write_bytes_per_sec`, `reads_per_sec`, `writes_per_sec`,
  `usec_per_other_op`, `usec_per_read_op`, `usec_per_write_op`, `time`;
  `resolution=1800000&start_time=-3h` returned 6 half-hour points;
  `protocol=nfs` works; `/arrays/nfs-specific-performance` adds
  per-op-class metadata rates and latencies.
- `/arrays/clients/performance` is per client connection: the row `name` is
  `<address>:<port>` (lab: `10.21.200.129:893`), so the plugin splits it into
  `client` and `client_port`; the figures are a lagged sample (a 4 s dd burst
  showed on `/file-systems/performance` first) and the list is empty while
  the array is idle. `sort=writes_per_sec-` and `limit` give the top N;
  `/file-systems/performance` and `/buckets/performance` take the same.
- `/hardware` reports an empty DFM bay on a //S as status `unclaimed` (the lab
  has two of four bays populated per blade: 20 such rows) and `/drives` lists
  the same bays as DFMs with status `unclaimed`, so the defaults of both
  `hardware_ignore_status` and `drive_ignore_status` include it.
- `/arrays/clients/performance` answered "Service temporarily unavailable" once
  in an hour of polling while every other read succeeded; the plugin logs it
  and skips the client rows for that poll instead of failing the signal.
- Some alerts (1112 on the lab) bump `updated` every minute without any other
  change; the alert diff keys on state, severity, flag and summary, not on
  `updated`.
- `/quotas/users` and `/usage/users` (and the group forms) require a file
  system identifier (`file_system_names`), so they are read when alert
  1106 or 1107 arrives, never polled across every file system.
  `/quotas/settings` has `direct_notifications_enabled` and `contact`: the
  array e-mails quota users itself.
- `/file-systems/open-files` requires `protocols`; `/remote-arrays`
  requires `current_fleet_only`.
- Telemetry on 2.21: `/telemetry-metrics-policies` lists 12 built-in
  policies (`bucket_client_perf_metrics`, `bucket_perf_metrics`,
  `bucket_space_metrics`, `device_client_perf_metrics`,
  `device_connector_metrics`, `device_perf_metrics`,
  `device_replication_perf_metrics`, `device_space_metrics`,
  `device_state_metrics`, `fs_perf_metrics`, `fs_space_metrics`,
  `obj_store_account_space_metrics`) with 35 rules `{metric_name,
  resolution}`; `/telemetry-targets` answers 200 (none configured);
  `/telemetry-metrics` answers 400. The SDK has these methods from
  FB_2_29 only, so a later part reaches them through `tasks/fb_rest.yml`.
- `/software-check`: `checks[]`, `details`, `software_upgrade_hops`,
  `software_version`, `status`; `/software/installations` is absent on
  2.21.
- `/log-targets/file-systems` and `/audit-file-systems-policies` answer
  400 "Operation not permitted" on the lab: file log targets are a
  directed-availability feature in 4.6.x.
- `/buckets/audit-filter-actions` lists 43 S3 actions (for example
  `s3:PutObjectLegalHold`, `s3:All`) a bucket audit filter may select; a
  filter takes actions and a prefix or suffix, not both.
- Defaults that posture rules will flag: the NFS export policy `all` has
  one rule, client `*`, permission `rw`, access `no-squash`; the SMB share
  policy `_smb_share_allow_everyone` grants everyone read, change and full
  control; the SMB client policy `_smb_client_allow_everyone` is client
  `*`, `rw`, encryption optional. A `default-network-access-policy` with
  six rules exists.
- `/admins/settings`: `lockout_duration`, `max_login_attempts`,
  `min_password_length`; `/support`: `phonehome_enabled`,
  `remote_assist_status`, `remote_assist_paths`, `edge_management_enabled`.
- Public access is a set of fields, not an endpoint:
  `Bucket.public_access_config {block_new_public_policies,
  block_public_access}`, `Bucket.public_status`,
  `ObjectStoreAccount.public_access_config` (FB_2_30 models; verify: the
  version they first appear on).
- Syslog servers carry `services` (`management`, `data-audit`); there is
  no separate `log-targets/syslog` resource. `/syslog-servers/settings`
  has `ca_certificate` and `ca_certificate_group` per service.

## Syslog

- Management syslog (Purity//FB 3.1.0+): up to three servers over UDP,
  TCP or TLS, facility local6 or local7, programs `purity.alert`,
  `purity.audit` and `purity.test`; sessions are logged too. The layouts
  differ from FlashArray's:
  `<ts> <fm-host> purity.alert: <SEVERITY> <State> - <array>: <subject>
  [<code>] #012<details ... Severity: Warning#012State: Open#012First Seen
  UTC Time: ...#012Array Name: ...#012Array ID: ...#012Purity//FB Version:
  ...#012Suggested Action: ...#012Knowledge Base Article: <url>` and
  `<ts> <fm-host> purity.audit: - Adding audit entry. GUID=<guid>, ID=<n>,
  Time=<yyyy-mm-dd hh:mm:ss.ffffff>, User=<user>, Location=<ip>,
  UI=CLI|GUI|REST, Command=<cmd>, Subcommand=<sub>, Arguments=<args>`.
  RFC 3164 by default; RFC 5424 is a tunable. verify: the exact lines the
  lab array sends once a syslog server exists; the layouts above are from
  the FlashBlade Admin Guide 4.8.1.
- Alert 1139 "Message Drop for Remote Syslog (Audit Related)" fires when a
  target drops data-audit messages; it is suppressed during a maintenance
  window, as is 1140.
- Data-audit syslog: one JSON object per line, RFC 3164 header
  `<PRI>DATE ARRAY_NAME : MESSAGE`, sent from each blade over the
  management VIP, up to 128 KB per message (rsyslog `MaxMessageSize`).
  Fields: `pf` (`fb`; a FlashArray sends `fa`), `v`, `eid`, `time`,
  `u_sid`, `user`, `client_ip`, `proto` (`smb`, `nfs`), `inode`, `fs`,
  `path`, `op_code`, `op_name`, `f_type`, `result` (`success`,
  `err_access`), `create_disposition`, `tgt_path` on a rename. SMB audit
  needs Purity//FB 4.4/4.5, an AD join and SACLs set by the data owners
  (control type `sacl`); NFS audit needs 4.8.0 (control type `policy`
  with success and failure operations); HTTP access is never audited;
  before 4.8 an SMB-audited file system refuses NFS mounts. Disabling
  audit drops pending records.
- Object audit (Purity//FB 4.6.3, REST 2.20): a per-bucket policy writes
  CloudTrail-style JSON records as log objects named
  `[array_identifier][prefix][yyyymmdd][HH-MM-SS]A[authority-id].[sequence].log`
  into another bucket on the same array, rotated every 5 minutes to 24
  hours (default 15 minutes) or by size. Record fields: `eventVersion`,
  `eventId`, `eventTime`, `eventName`, `readOnly`, `eventCategory`,
  `eventSource "onPrem"`, `sourceIPAddress`, `userAgent`, `bucketName`,
  `recipientAccountId`, `resources[{type, ARN}]`, `userIdentity {type,
  accountId, accessKeyId, userName, sessionContext}`,
  `requestParameters`, `errorCode`, `errorMessage`. Records are dropped
  when the log bucket hits its quota, and an admin can detach the policy.
  The lab has the endpoints and no accounts or buckets yet. Object
  notifications to Kafka arrive with 4.10.a (REST 2.32).

## py-pure-client SDK

- `pypureclient.flashblade.Client(target, api_token=..., verify_ssl=...)`;
  the plugin passes `timeout=(10, request_timeout)` when the installed
  client's signature accepts it (checked with `inspect`), else nothing.
- Responses carry `.items`, `.status_code`, `.total_item_count`; the fake
  SDK in `tests/unit/conftest.py` mirrors that shape.
- SDK models raise `AttributeError` for fields the array did not return;
  read them through `to_dict()`.
- `get_file_systems(total_only=True, filter=...)` sums `provisioned` in
  one call; snapshots are per file system (no consistency group); a
  destroyed name is reserved until eradication; a file system is
  destroyed only with its protocols off.

## everpure.flashblade modules

- `purefb_fs` creates `nfs rule *(rw,no_root_squash)` plus NFS and SMB
  exports when `default_exports` is omitted; a rename keeps the export
  name; overwrite-restore keeps the id and exports.
- `purefb_hardware` takes `name` and `enabled` (the identify LED) and
  `speed` for connectors.
- `purefb_snap` restores and destroys per file system; `purefb_bucket`
  and `purefb_fs` take `state: absent` with `eradicate: true`.
- `purefb_userquota` and `purefb_groupquota` set per-file-system quotas by
  user or group name or numeric id.
- No module exists for maintenance windows, QoS policies, alert flagging,
  session or lock termination, audit policies, log targets, telemetry
  targets or legal holds ([DESIGN.md](DESIGN.md), "Collection gaps");
  `playbooks/tasks/fb_rest.yml` covers the ones this part needs. Only
  `purefb_realm` has a QoS option in 1.29.0.
- Known array behaviours from the collection's QA suite: snapshot policies
  cannot be renamed; SMB share rules are one permission ladder keyed by
  principal; bucket access rules store only `s3:GetObject`/allow; SNMP
  passphrases read back `****`; realms need `without_default_access_list`;
  cloning a snapshot into a new file system needs 4.8.0.

## Identity and audit attribution

- Any other automation that shares the array account appears in the audit
  log exactly as an operator would; change control cannot tell them
  apart. Name the automation account in `automation_users` so its records
  are logged and not acted on; `automation_agent` from `user_agent` is a
  second hint, not a replacement. Running the automation as a dedicated
  service account is not optional. Fleet operations need a
  directory-service user; a local admin gets 403 on `context_names`
  calls. [SECURITY.md](../SECURITY.md) states the two-credential policy.

## ansible-rulebook 1.3.x

- Conditions cannot use Jinja filters or arithmetic; actions can. The
  plugin therefore emits the booleans and counts the rules need:
  `days_to_full_known`, `baseline_known`, `lag_degraded`,
  `hardware_issue_count`, `high_share_polls`, `eda_capped`.
- `--vars` takes one file and values in it are not templated; the e2e
  script concatenates the vars file and a fixture into one document and
  substitutes the fixture placeholders with `sed`.
- `throttle.once_within` must be a literal and is rejected on a
  multi-event (`all`) condition.
- `run_playbook` copies the playbook's directory into a temporary project,
  not the repository: files outside `playbooks/` are absent, everything
  arrives as `extra_vars`, and relative includes under `playbooks/`
  resolve (`tasks/resolve_array.yml`, `tasks/fb_rest.yml`,
  `runbooks/<code>.yml`).
- `localhost` must be in the inventory.
- The stock `ansible.eda` sources (webhook, generic, tick) resolve only
  from `ANSIBLE_COLLECTIONS_PATH`; `env.sh` puts `~/.ansible/collections`
  on it, with `./collections` in front when that directory exists.

## Ansible templating

- Inside a Jinja expression Ansible doubles backslashes before Jinja
  parses the string, so `'\n'` written in `{{ }}` is a backslash and an
  `n`, not a line break; a real one belongs to the YAML. Regex arguments
  are the exception. A unit test refuses such literals outside `regex_*`
  filters.

## State

- FlashBlade has no tags on file systems or buckets through REST 2.30
  (only workloads carry tags, 2.23+), so state that must survive a
  rulebook restart lives on the array as a name or a bit, or in a file:
  the QoS cap is a policy named `eda-cap-<fs>` (read back as
  `eda_capped`), a handled alert is `flagged`, the approval request is a
  file under `request_dir`, the maintenance window is itself the state.

## What the lab cannot show

- SMB file audit (no Active Directory join), NFS file audit (4.8.0), file
  log targets (directed availability), Kafka notifications (4.10.a).
- A real hardware fault: the LED path runs live on a healthy component,
  the fault itself is replayed from `tests/fixtures/` (a blade, a LAG).
- A hot array: last-week baselines are idle, so the client and QoS rules
  fire on lowered thresholds under a fio load from the client VM, which
  is also the platform host.
- Replication: no links or connections exist yet; the 4.5.6 peer has no
  LLDP, CSR or object audit.
