# Hardware notes

Facts about the array, the SDK, the collection, ansible-rulebook and the
state model that shape the code, grouped by topic; [README.md](../README.md)
carries the short form. Array facts are from a read-only probe of a
FlashBlade//S200 on Purity//FB 4.6.4 (REST 2.21) unless stated; a legacy
FlashBlade on 4.5.6 (REST 2.17) is the replication peer of part 3
("Replication" below).

## REST API

- The lab array negotiates REST 2.21 and py-pure-client 1.98.0 covers
  FB_2_0 to FB_2_30. The plugin must not call a method the negotiated
  client lacks: `get_telemetry_*` (SDK FB_2_29, although the array serves
  `/telemetry-targets` on 2.21), `get_software_installations` (absent on
  2.21), `get_workloads` (2.23).
- There is no `/alerts/events` on FlashBlade. The alerts signal fetches
  `get_alerts(filter="state!='closed'")`, falls back to an unfiltered
  `get_alerts(limit=...)` if the filter answers 400 (logged once), and
  diffs the result locally. An alert is `open`, then `closing` once the
  condition has cleared (1101 went there within a minute of the grow on the
  lab and still answers the `state!='closed'` filter), then `closed`; the
  plugin emits `closing` as its own transition and the rulebook logs it.
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
  absent. `purefb_maintenance` (part 3) wraps the post and the delete,
  takes the timeout in hours and, with `extend`, deletes and re-posts an
  existing window with the new timeout.
- `/arrays`: `banner`, `default_inbound_tls_policy`,
  `encryption.data_at_rest`, `eradication_config {eradication_delay (ms),
  manual_eradication}`, `idle_timeout`, `network_access_policy`,
  `ntp_servers`, `time_zone`, `version`, `smb_mode`, `product_type`, `os`.
  There is no eradication-config endpoint; it is a field here and on each
  file system, array-wide and one-way for retention. The write is `PATCH
  /arrays {"eradication_config": {...}}` (unverified; "Containment and
  holds" below).
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
- Telemetry on 2.21: `/telemetry-targets`, `/telemetry-metrics-policies`
  and `/telemetry-metrics-policies/rules` answer; `/telemetry-metrics`
  answers 400. The SDK has these methods from FB_2_29 only, so the two
  telemetry modules, `telemetry_check.yml` and the rule generator speak
  REST ("Telemetry" below).
- `/software-check`: `checks[]`, `details`, `software_upgrade_hops`,
  `software_version`, `status`; `/software/installations` is absent on
  2.21.
- `/log-targets/file-systems` and `/audit-file-systems-policies` answer
  400 "Operation not permitted" on the lab: file log targets are a
  directed-availability feature in 4.6.x.
- `/bucket-audit-filter-actions` lists the 43 S3 actions (for example
  `s3:PutObjectLegalHold`, `s3:All`) a bucket audit filter may select; a
  filter takes actions and optional `s3_prefixes`, nothing else
  ("Object audit" below).
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
  `ObjectStoreAccount.public_access_config`; the 2.21 bucket item carries
  both bucket fields, which is what the posture signal reads.
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
- Object audit is not syslog: a per-bucket policy writes its records as
  objects into another bucket on the same array ("Object audit" below).
  Object notifications to Kafka arrive with 4.10.a (REST 2.32).

## Object audit

Verified in part 2 on the lab array (Purity//FB 4.6.4, REST 2.21,
py-pure-client 1.98.0); everything below exists from REST 2.20
(Purity//FB 4.6.3). The lab object store, which the vars and env examples
assume:

| Object | Value |
| --- | --- |
| account | `eda` |
| users | `eda/eda-tail` (policies `pure:policy/object-list`, `object-read`, `bucket-list`, `bucket-info`: the plugin's read-only identity), `eda/eda-state` (full access: the cursor copy, request, receipt and legal-hold writes), `eda/eda-app` (full access: demo traffic, the "attacker" of the tests) |
| buckets | `eda-log` (versioning enabled, Object Lock enabled, governance mode, default retention 1 day on the lab and 90 days in production, `quota_limit` 1073741824 soft); `eda-state` (versioning enabled); `eda-data` (versioning enabled, Object Lock enabled, no default retention: legal holds per object) |
| log target | `eda-audit`: bucket `eda-log`, `log_name_prefix {"prefix": "audit_"}`, `log_rotate {"duration": 300000}` |
| audit policy | `eda-audit`, enabled, `log_targets [{name eda-audit, resource_type log-targets/object-store}]`, members `eda-data` and `eda-log` |
| filters | `eda-data`: `edaall` actions `[s3:All]`; `eda-log`: `edaapprovals` actions `[s3:PutObject]`, `s3_prefixes [approvals/, requests/]` |
| S3 endpoint | the data VIP over HTTPS, path-style addressing, the array's self-signed certificate (`verify_ssl` false) |
| env (`~/.flashblade-eda.env`, chmod 600) | `FB_A_S3_ENDPOINT`, `FB_A_S3_ACCESS_KEY` / `FB_A_S3_SECRET_KEY` (eda-tail), `FB_A_S3_STATE_ACCESS_KEY` / `FB_A_S3_STATE_SECRET_KEY` (eda-state), `FB_A_S3_APP_ACCESS_KEY` / `FB_A_S3_APP_SECRET_KEY` (eda-app, tests only) |

REST, all under `/api/2.21/`:

- Log targets: `GET/POST/PATCH/DELETE log-targets/object-store?names=<n>`.
  POST body `{"bucket": {"name": "<bucket>"}, "log_name_prefix": {"prefix":
  "<p>"}, "log_rotate": {"duration": <ms>}}`; `duration` 300000 to 86400000
  ms (5 minutes to 24 hours; the array's own defaults, verified by a POST
  with only the bucket set, are a `log_rotate` of 900000 ms, 15 minutes,
  and the prefix `audit-logs`); a plain string for `log_name_prefix` is rejected
  with "invalid value for log_name_prefix". Item: `{name, id, bucket{name,
  id}, log_rotate{duration}, log_name_prefix{prefix}}`. A target in use by
  a policy cannot be deleted; the module surfaces the array's message.
- Audit policies: `GET/POST/PATCH/DELETE audit-object-store-policies?names=<n>`.
  POST body `{"enabled": true, "log_targets": [{"name": "<lt>",
  "resource_type": "log-targets/object-store"}]}`; PATCH takes `enabled`,
  `log_targets` (the full list) or `add_log_targets` / `remove_log_targets`.
  Item: `{name, id, enabled, is_local, location, log_targets[{name, id,
  resource_type}], policy_type "audit"}`.
- Members: `GET audit-object-store-policies/members?policy_names=<p>` items
  `{policy{name, id, resource_type}, member{name, id, resource_type
  "buckets"}}`; `POST` and `DELETE
  audit-object-store-policies/members?policy_names=<p>&member_names=<bucket>`
  with no body.
- Bucket audit filters: `GET buckets/audit-filters?bucket_names=<b>` items
  `{name, actions[], s3_prefixes[] or null, bucket{name, id}}`; `POST
  buckets/audit-filters?bucket_names=<b>&names=<f>` body `{"actions":
  [...], "s3_prefixes": [...]}` (`s3_prefixes` optional); `DELETE` with the
  same query; no PATCH, so a change is delete plus create. Filter names
  are alphanumeric only, 1 to 63 characters: `eda-all` was rejected,
  `edaall` works. The 43 actions come from `GET
  bucket-audit-filter-actions` and include `s3:All`, `s3:ReadOnly`,
  `s3:WriteOnly`, `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`,
  `s3:DeleteMultipleObjects`, `s3:PutObjectLegalHold` and
  `s3:PutObjectRetention`.
- Buckets: `POST buckets?names=<b>` body `{"account": {"name": ..},
  "quota_limit": "<bytes>", "hard_limit_enabled": false}`; `quota_limit`
  must be a string. Object Lock cannot be set in the POST that creates the
  bucket (versioning is `none` then): three steps, POST the bucket, `PATCH
  {"versioning": "enabled"}`, `PATCH {"object_lock_config": {"enabled":
  true}}`; `default_retention_mode` and `default_retention` go together,
  the retention in ms as a string. Item: `space{virtual, total_physical,
  ...}`, `object_count`, `quota_limit` (int bytes or null),
  `hard_limit_enabled`, `object_lock_config`, `versioning`,
  `public_status`, `public_access_config`, `account{name}`. The health
  event's used bytes are `space.virtual`.
- Access keys: `GET object-store-access-keys` items `{name (the access
  key id), user{name "<account>/<user>"}, created (ms), enabled}`; `PATCH
  object-store-access-keys?names=<key>` body `{"enabled": false}` disables
  one key (SDK: `patch_object_store_access_keys(names=,
  object_store_access_key=ObjectStoreAccessKey(enabled=False))`; the
  collection's `purefb_s3user state=keystate key_name= enable_key=` does
  the same). The canary and anomaly playbooks use the PATCH through
  `tasks/fb_rest.yml`.
- Other reads the posture signal and the plugin use: `certificates`
  (`valid_to` ms), `nfs-export-policies/rules` (`client`, `permission`,
  `access`), `object-store-accounts`, `object-store-users`, `arrays` (its
  `id` is the array id that prefixes every log key).

SDK (the negotiated FB_2_21 client), for the modules:
`get/post/patch/delete_log_targets_object_store` (keyword
`log_target_object_store`; models `flashblade.LogTargetsObjectStorePost`
and `flashblade.LogTargetsObjectStore` with `bucket=flashblade.Reference(name=)`,
`log_name_prefix=flashblade.AuditLogNamePrefix(prefix=)`,
`log_rotate=flashblade.AuditLogRotate(duration=)`);
`get/post/patch/delete_audit_object_store_policies` (keyword `policy`;
`AuditObjectStorePoliciesPost(enabled=, log_targets=[flashblade.ReferenceWritable(name=,
resource_type="log-targets/object-store")])` and
`AuditObjectStorePoliciesPatch(enabled=, log_targets= | add_log_targets= |
remove_log_targets=)`); `get/post/delete_audit_object_store_policies_members(policy_names=[..],
member_names=[..])`; `get_buckets_audit_filters(bucket_names=[..])`,
`post_buckets_audit_filters(bucket_names=[b], names=[f],
audit_filter=flashblade.BucketAuditFilterPost(actions=, s3_prefixes=))`,
`delete_buckets_audit_filters(bucket_names=[b], names=[f])`;
`get_bucket_audit_filter_actions()`. Model classes are lazy attributes of
`pypureclient.flashblade`: `getattr(flashblade, "Name")` raises
`ImportError` for a name that does not exist, and there is no `LogRotate`,
`LegalHoldPost` or `ObjectStoreAccessKeyPatch`. The collection's
module_utils is `ansible_collections.everpure.flashblade.plugins.module_utils.purefb`
with `get_system(module)` (the negotiated client from `fb_url`,
`api_token`, `disable_warnings`; fails without pypureclient) and
`purefb_argument_spec()`.

The log objects, read back from `eda-log` after traffic from `eda-app`:

- Key layout `<array id>/<log_name_prefix>/<yyyy>/<mm>/<dd>/<HHMMSS>.A<authority>.<sequence>.log`,
  for example `<array id>/audit_/2026/11/12/200500.A198.1.log`. `HHMMSS`
  is the start of the rotation window, not the write time; one object per
  authority (blade partition) that had records in the window, so the
  objects of one window share the timestamp and differ in the authority
  (12 objects for 14 records in the lab's one busy window); written within
  seconds of the window closing (records from 20:08:50 appeared at
  20:10:00 to 20:10:04 under `200500`). Lexical order of the keys under
  the prefix is time order. An idle window produces no object at all, so a
  missing object is not a dropped record; `stale` in the health event is
  only meaningful where traffic is continuous.
- Content: one JSON object per line (NDJSON), plain text, no gzip, no
  `Records` wrapper. Fields: `eventId` (int, up to 2^56 range: keep it an
  integer, never a float), `eventTime` (ISO 8601 with milliseconds, `Z`),
  `eventVersion` "1.0", `eventName` (`GetObject`, `PutObject`,
  `HeadObject`, `ListObjectsV2`, `DeleteObject`, ...), `readOnly`,
  `eventCategory` "Data", `managementEvent` false, `eventSource` "onPrem",
  `sourceIPAddress`, `bucketName`, `recipientAccountId` (the account),
  `resources` `[{type Pure::S3::Object, ARN prn::s3:::<bucket>/<key>},
  {accountId, type Pure::S3::Bucket, ARN prn::s3:::<bucket>}]` (a
  `ListObjectsV2` record carries `ARNPrefix prn::s3:::<bucket>/` on the
  object resource instead of `ARN`), `userIdentity {type IAMUser, arn
  prn::iam:array-id/local:obj-account-id/<n>:user/<user>, accountId,
  accessKeyId, userName (without the account)}`, `userAgent` (the
  client's own string), `requestParameters {bucketName, key, Host, ...}`
  (`key` absent on bucket operations; `Content-Encoding aws-chunked` on a
  put, `x-amz-checksum-mode` on a get, `list-type` and `encoding-type` on
  a list), `responseElements {x-amz-version-id, ETag}` or `null`. A
  successful `GetObject` record carried `responseElements` twice;
  `json.loads` keeps the last. Error records add `errorCode` and
  `errorMessage` (`NoSuchKey` "The specified key does not exist.",
  `SignatureDoesNotMatch`) with `responseElements` null; there is no
  separate error record type.
- Latency: one rotation (5 minutes on the lab, the minimum) plus the
  seconds the write takes, plus the poll interval; the plugin keeps
  re-listing the last `late_windows` windows for authorities that write
  late.

- Bucket `public_status` reads `bucket-and-objects-not-public` on a bucket with no
  public policy (not `none`), with `public_access_config` `{block_public_access:
  true, block_new_public_policies: true}` inherited from the account on the lab;
  the posture signal counts a bucket public only when the status names a public
  side without `not-public`, or when `block_public_access` is false.

## Telemetry

Probed in part 3 on the lab array (Purity//FB 4.6.4, REST 2.21). The
FB_2_21 client has no telemetry methods, so `purefb_telemetry_target`,
`purefb_telemetry_policy`, `telemetry_check.yml` and
`tools/telemetry_rules.py` speak REST.

- Verified against a live receiver on 2026-11-13: the array POSTs to the
  endpoint path exactly as given, so a Prometheus 3.x OTLP receiver needs
  `<host>:9090/api/v1/otlp/v1/metrics` (`/api/v1/otlp` alone answers 404 and
  nothing is stored); the first 60 s policies showed up within two minutes.
  `GET /telemetry-targets/test` answered 400 with no body even while the
  pushes were landing, so a 400 there does not mean the path is broken;
  `telemetry_check.yml` therefore weighs the receiver's own answer too. The
  resource attributes arrive as labels `device_name` and `device_id`; the
  series carry `storage_type` (all, file, object), `space_type` (capacity,
  total_physical, total_provisioned, available_provisioned, unique, virtual,
  snapshots, shared, destroyed, destroyed_virtual), `op`, `proto`,
  `resource_type`, `entity_name`, `err` and `transfer`. Two things the
  live stream showed about rules: the per-client policies
  (`device_client_perf_metrics`, `bucket_client_perf_metrics`) push
  nothing while no client is active, so an `absent_over_time` on them
  fires on an idle array and the generator gives them no silence rule; and
  `storage_device_connector_err_ps` reports a steady `other` and `total`
  count on the lab's uplinks (16/s on CH1.FM1.ETH1 and ETH4) that is not a
  fault, so the connector rule counts only the named error kinds. The
  array names itself in telemetry (`device_name` slc6-s200-n2-a19-33), not
  by its inventory name, so `alertmanager.yml` maps the label through
  `telemetry_array_names`; without the map a playbook addressed to the
  device name finds no host and runs against nothing.
- Targets: `GET /telemetry-targets` (none on the lab before part 3).
  `POST /telemetry-targets?names=<n>` body `{"endpoint":
  "<host>:<port>[/<path>]", "transport": "otlp-http-protobuf" |
  "otlp-http-protobuf-without-tls", "source": {"name": "<network
  interface>"}}`. The endpoint carries no scheme ("Endpoint must be in the
  form '<hostname>:<port>' or '<hostname>:<port>/<path>' and reachable
  over the network"); the array opens a TCP connection at creation and
  refuses an endpoint nothing listens on, so a listener must exist before
  the module can create the target (the module strips a scheme with a
  warning). `source` is required and names one of the array's network
  interfaces: a data VIP works; the string `management` is "invalid value
  for source" and `{"name": "management"}` is "Network does not exist".
  `vendor` is "Invalid body parameter: vendor" on 2.21 (a later field).
  `enabled` is patchable (`PATCH /telemetry-targets?names=<n> {"enabled":
  false}`); `DELETE /telemetry-targets?names=<n>`. Item: `name`, `id`,
  `endpoint`, `transport`, `source{name}`, `enabled`, `api_token`,
  `ca_certificate`, `ca_certificate_group`, `client_certificate`,
  `resource_attributes`.
- `GET /telemetry-targets/test?names=<n>` answered 400 with an empty body
  against a port that is not an OTLP receiver; the success shape (the
  design says the array delivers `dummy_metric_milliseconds`) is to be
  recorded once a receiver exists. The module's `test` option returns
  `{status, ok, body}` and `telemetry_check.yml` treats anything but 200
  as a failed test.
- Policies: `GET /telemetry-metrics-policies` lists 12 built-in policies
  (`bucket_client_perf_metrics`, `bucket_perf_metrics`,
  `bucket_space_metrics`, `device_client_perf_metrics`,
  `device_connector_metrics`, `device_perf_metrics`,
  `device_replication_perf_metrics`, `device_space_metrics`,
  `device_state_metrics`, `fs_perf_metrics`, `fs_space_metrics`,
  `obj_store_account_space_metrics`), each `{name, enabled,
  targets[{name, id, resource_type}], rules[{name}], version, policy_type
  "telemetry-metrics"}`. `PATCH /telemetry-metrics-policies?names=<p>
  {"targets": [{"name": "<t>"}]}` sets the full target list (`[]`
  detaches); `enabled` is not patchable on 2.21 ("Invalid body parameter:
  enabled"), which `purefb_telemetry_policy` documents.
  `purefb_telemetry_target` adds itself to each named policy's list
  without removing other targets; `purefb_telemetry_policy` enforces the
  full list.
- Rules: `GET /telemetry-metrics-policies/rules` lists 35 rules `{name
  "<policy>.<n>", policy{name}, index, metric_name, resolution}`;
  `resolution` is 60000 ms for `device_perf_metrics`,
  `device_space_metrics` and `device_connector_metrics` and 600000 ms for
  the rest. Metric names are OTLP dotted: `storage.device.bw_bpo`,
  `.bw_bps`, `.iops`, `.latency_us`, `.space_bytes`, `.space_reduction`,
  `.alerts`, `.health`, `.info`; `storage.device.connector.bw_bps`,
  `.bw_pps`, `.err_ps`; `storage.device.repl.bw_bps`;
  `storage.device.client.bw_bpo`, `.bw_bps`, `.iops`, `.latency_us`;
  `storage.device.fs.bw_bpo`, `.bw_bps`, `.iops`, `.latency_us`,
  `.space_bytes`, `.space_reduction`; `storage.device.bucket.bw_bpo`,
  `.bw_bps`, `.iops`, `.latency_us`, `.obj_count`, `.space_bytes`,
  `.space_reduction`; `storage.device.bucket.client.latency_us`,
  `.op_count`; `storage.device.obj_store_account.obj_count`,
  `.space_bytes`, `.space_reduction`.
- In Prometheus (the OTLP receiver with `translation_strategy:
  UnderscoreEscapingWithSuffixes`) a dotted name becomes underscores,
  `storage_device_iops`, and the resource attributes (the device name
  among them) become labels when `otlp.promote_all_resource_attributes`
  is true. Confirmed on a Prometheus 3.16 receiver on 2026-11-13: the array
  name arrives as `device_name` (with `device_id`), which is the generator's
  default `--array-label`; every generated rule copies it into a fixed
  `array_name` label (a rulebook condition cannot index a label by a
  variable name, so `alertmanager.yml` reads `event.alert.labels.array_name`).
  `storage_device_health` is one gauge per component (`component_name`,
  `component_type`) reading 5 for every healthy component; the enum is not
  published, so the rule alerts on any other value. `storage_device_alerts`
  is one series per open alert valued with its code and labelled with
  severity, summary, component and knowledge base link; `storage_device_info`
  carries `device_os`, `device_type` and `device_vers`.
- Alertmanager payload (`ansible.eda.alertmanager` with `data_alerts_path
  alerts`, `data_host_path ""`, `data_path_separator "."`): one event per
  alert, `event.alert.status` firing or resolved,
  `event.alert.labels.<name>`, `event.alert.annotations.<name>`,
  `event.alert.startsAt`.

## Replication

The lab pair: `fb-a`, the FlashBlade//S200 on Purity//FB 4.6.4 (REST
2.21), and `fb-b`, a legacy FlashBlade on 4.5.6 (REST 2.17).

- An array connection `fb-a` to `fb-b` was created in part 3 with
  `everpure.flashblade.purefb_connect` (`target_repl` the peer's
  replication address). `GET /array-connections` item `{id,
  management_address, replication_addresses[], version, status,
  remote{name, id}, type "async-replication", os, encrypted,
  throttle{default_limit, window_limit, window{start, end}},
  ca_certificate_group}`; `status` reads `connecting` from creation and
  `connected` once up; the peer shows the same `id` with
  `management_address` null and `replication_addresses` `[]` while
  connecting. A connection created with `replication_addresses` (the
  module's `target_repl`, the CLI's `--replication-address`) between two
  arrays with no NAT between them stayed `connecting` for hours on the lab
  with "Internal error." on the object path, in both directions, although
  the replication VIPs ping each other at 9000 bytes and the peer's expired
  certificate had been replaced; created again with the management address
  and the connection key alone it was `partially_connected` in 15 s and
  `connected` in a minute (knowledge base KB0034025 describes exactly
  this). A replica link appeared `idle` on both sides within 15 s of the
  POST and the peer's copy reads `promotion_status demoted, writable
  false`; a promote (`PATCH /file-systems requested_promotion_state
  promoted`) completes within a minute, and the demote on the 4.5.6 peer
  needs `discard_non_snapshotted_data=true` ("Demoting a file system
  will restore the file system from the latest snapshot because creating
  an undo snapshot is not supported"), which is what `dr_drill_discard`
  switches on. The `replication` signal reports a connecting one in
  `connections_down` and as `purefb_array_connection` with `connected
  false`; `throttled` is true when `default_limit` or `window_limit` is
  set.
- `GET /file-system-replica-links` item (SDK `FileSystemReplicaLink`)
  `{id, direction (inbound | outbound), lag (ms), status (replicating,
  idle, unhealthy, paused, ...), status_details, link_type,
  local_file_system{name}, remote{name}, remote_file_system{name},
  policies[], recovery_point (ms)}`. `GET /bucket-replica-links` item
  `{id, direction, lag, status, status_details, paused,
  object_backlog{bytes_count, delete_ops_count, other_ops_count,
  put_ops_count}, local_bucket{name}, remote{name}, remote_bucket{name},
  remote_credentials{name}, recovery_point, cascading_enabled}`. `GET
  /targets` (S3 replication targets) item `{name, address, status,
  status_details}`. Every read is optional in the plugin, so an array
  without an endpoint yields an empty list; `replication_ok_statuses`
  (default replicating, idle, connected, healthy) decides what counts as
  unhealthy and `replication_lag_warn_s` what counts as lagging.
- A scratch file system `eda-dr-demo` (1 GiB, NFSv3, the legacy rule for
  the client VM) exists on `fb-a`; its replica link (`POST
  /file-system-replica-links?local_file_system_names=eda-dr-demo&remote_names=<peer>&remote_file_system_names=eda-dr-demo`)
  waits for the connection.
- Promotion: `PATCH /file-systems?names=<fs> {"requested_promotion_state":
  "promoted" | "demoted"}` (`FileSystemPatch.requested_promotion_state`;
  `purefb_fs` has `promote` and `discard_snaps`); `promotion_status` reads
  the result back; a demote is only allowed on a file system in a replica
  link. `tasks/dr_drill_one.yml` promotes on the peer, waits for
  `promotion_status`, reads the file system's space and export, and
  demotes in an always block, with `discard_non_snapshotted_data` only
  when `dr_drill_discard` is true.
- A paused bucket link is resumed with
  `everpure.flashblade.purefb_bucket_replica paused: false`, which needs
  the link's `name`, `target`, `target_bucket` and `credential`;
  `replication_resume.yml` reads them from `GET
  /bucket-replica-links?local_bucket_names=<bucket>` first.
- Alert 1112 (object replication connection requires attention) is the
  array's own view of the same state; four were open on the lab before
  part 3.

## Containment and holds

QoS, sessions and locks, export rules, legal holds, the alert flag, the
eradication config and certificates, as part 3 reads and writes them.

- QoS (REST 2.21; SDK `post/patch/delete/get_qos_policies` with keyword
  `policy` and model `flashblade.QosPolicy(max_total_ops_per_sec=,
  max_total_bytes_per_sec=, enabled=)`; there is no `QosPolicyPost`):
  `POST /qos-policies?names=<n> {"max_total_ops_per_sec": 100}` returns
  `{name, id, enabled true, max_total_bytes_per_sec null,
  max_total_ops_per_sec 100, policy_type "qos", location{name}}`; `PATCH
  {"max_total_ops_per_sec": 200, "enabled": true}` and `DELETE
  /qos-policies?names=<n>` work; 100 ops/s is the floor. Audit words:
  command `purepolicy`, subcommand `qos create`, `qos setattr`, `qos
  delete`. Assignment is `PATCH /file-systems?names=<fs> {"qos_policy":
  {"name": "<p>"}}` (`FileSystemPatch.qos_policy`, the `qos_cap.yml`
  path of part 1) and the same on `/buckets`. Clearing is unverified:
  `purefb_qos` tries `{"qos_policy": {"name": ""}}` and then
  `{"qos_policy": null}`, and the lab run records which one the array
  accepted. `state: absent` clears the policy from every object that
  carries it (read from `/file-systems` and `/buckets` `qos_policy.name`)
  before deleting it.
- Sessions and locks: `GET /file-systems/sessions` (optional
  `protocols=nfs|smb`), `GET /file-systems/locks` and `GET
  /file-systems/open-files?protocols=smb` exist on 2.21 (`nfs` is
  "Unsupported protocols" for open files); both lists were empty on the
  idle lab, so the item fields are unverified and `purefb_fs_session`
  reads them with `.get`. Documented fields (Purity//FB REST reference):
  session `{name, client, user, protocol, port, opens, time_idle,
  authentication, connection_time}`; lock `{name, client,
  file_system{name}, path, inode, protocol, access_type, created,
  range{start, end}, source, status}`. SDK
  `get/delete_file_systems_sessions(names=, client_names=, user_names=,
  protocols=, disruptive=)` and `get/delete_file_systems_locks(names=,
  file_system_names=, client_names=, paths=, inodes=, recursive=)`. The
  lab's NFS client (`10.21.200.129`) mounts `eda-demo-quota` over NFSv3,
  so there are no NFSv4 sessions to list; an NLM lock taken with `flock`
  on the mount is what `/file-systems/locks` should show. The module
  lists first, removes only what matched, reports the names and changes
  nothing in check mode.
- NFS exports take two forms. An export policy (`nfs.export_policy{name}`)
  has ordered rules under `/nfs-export-policies/rules`: item `{name
  "<policy>.<n>", policy{name}, index, client, permission rw|ro, access
  root-squash|no-squash|all-squash, anonuid, anongid, atime, secure,
  security[], fileid_32bit}`; SDK
  `post_nfs_export_policies_rules(policy_names=[p],
  rule=flashblade.NfsExportPolicyRuleInPolicy(client=, permission=,
  access=), before_rule_name=<rule>)` and
  `delete_nfs_export_policies_rules(names=[rule])`. The legacy form is
  the `nfs.rules` string, `"10.21.200.129(rw,no_root_squash)"`, several
  entries separated by a space, written with `PATCH
  /file-systems?names=<fs> {"nfs": {"rules": "..."}}` or `purefb_fs
  nfs_rules`. `quarantine_client.yml` handles both: a rule `client <ip>
  permission ro access root-squash` posted before the policy's first rule
  (found again by `client == <ip>` and `permission ro` for the release),
  or the client's legacy entry rewritten from `rw` to `ro` after
  re-reading the string; the rule is the state. The lab's scratch file
  systems `eda-demo-quota` and `eda-dr-demo` use the legacy string with
  the one client, which is what the quarantine test exercises; the
  default export policy `all` has rule `all.1` `* rw no-squash`.
- Legal holds (REST 2.17 and later): `GET /legal-holds` (one hold existed
  on the lab before part 3), item `{name, id, description, realms}`; SDK
  `post_legal_holds(names=, hold=flashblade.LegalHold(description=))`,
  `delete_legal_holds(names=)`; held entities `GET
  /legal-holds/held-entities`, item `{legal_hold{name},
  file_system{name}, path, status}`, SDK
  `post_legal_holds_held_entities(names=[hold], file_system_names=[fs],
  paths=[path], recursive=)`. A held entity is released with `PATCH
  /legal-holds/held-entities?names=<hold>&file_system_names=<fs>&paths=<path>&released=true`
  (SDK `patch_legal_holds_held_entities(..., released=True)`; verified on
  the lab: a DELETE answers 405, a PATCH without `released` says 'Patch
  on held entities requires "released"=true', a directory needs
  `recursive=true` on the release as on the hold ("Can't apply or release
  legal holds to directories without the recursive flag"), a hold
  still in use cannot be deleted, and a hold applied or released moments
  ago stays in use for several seconds, so the module retries the delete
  for up to 30 s). The object-side hold of part 2
  (`put_object_legal_hold` over S3) is a different mechanism.
- The alert flag (verified in part 1, and the module on 2026-11-13: flag,
  flag again unchanged, unflag on the open 1140 alert): `PATCH
  /alerts?names=<n> {"flagged": true | false}`; `purefb_alert_flag` wraps
  it and returns `{name, code, flagged}`.
- Eradication config: `/arrays` carries `eradication_config
  {eradication_delay (ms), manual_eradication}` (`all-enabled` on the
  lab; the other values are `all-disabled` and `partially-enabled`); the
  write is `PATCH /arrays {"eradication_config": {...}}` (SDK
  `patch_arrays(array=flashblade.Array(eradication_config=flashblade.ArrayEradicationConfig(...)))`),
  unverified (the module ran in check mode on the lab on 2026-11-13: no
  change at the array's own values, a longer delay reported as a change;
  nothing was written). `purefb_eradication` takes the delay in whole days
  (1 to 30) and treats the setting as a ratchet: it refuses to shorten the
  delay or re-enable manual eradication unless `force` is true.
- Certificates: `/certificates` item `{name, certificate_type
  array|external, common_name, issued_by, issued_to, valid_from, valid_to
  (ms), status, key_algorithm, key_size, subject_alternative_names}`.
  `renew_certificate.yml` renews only an `array` certificate whose
  `issued_by` equals `issued_to` (self-signed) with
  `everpure.flashblade.purefb_certs` (`generate: true`, `days`, the
  current common name and SANs read back first); a CA-issued or external
  certificate is recorded as needing a human.

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
- No module exists in 1.29.0 for maintenance windows, QoS policies, alert
  flagging, session or lock termination, audit policies, log targets,
  telemetry targets or legal holds ([DESIGN.md](DESIGN.md), "Collection
  gaps"); parts 2 and 3 add the eleven under `plugins/modules/` of this
  repository, `playbooks/tasks/fb_rest.yml` covers the raw calls that
  remain (the user quota reads, the export string rewrite, the promotion
  state, the telemetry test), and `tasks/s3_object.yml` does the object
  legal hold over S3 (`put_object_legal_hold`). Only `purefb_realm` has
  a QoS option in 1.29.0.
- Part 2 adds `purefb_log_target`, `purefb_audit_policy` and
  `purefb_bucket_audit_filter` under `plugins/modules/` of this
  repository, in the collection's style (DOCUMENTATION, EXAMPLES, RETURN,
  check mode, idempotent, the collection's `module_utils.purefb` for
  `get_system` and `purefb_argument_spec`), on the SDK models
  `LogTargetsObjectStorePost` / `LogTargetsObjectStore` (`Reference`,
  `AuditLogNamePrefix`, `AuditLogRotate`), `AuditObjectStorePoliciesPost` /
  `AuditObjectStorePoliciesPatch` (`ReferenceWritable` with
  `resource_type log-targets/object-store`) and `BucketAuditFilterPost`
  ("Object audit" above). `purefb_log_target` takes `log_rotate` as
  `5m` to `24h` and converts to ms; `purefb_bucket_audit_filter`
  recreates a filter that differs, because the array has no PATCH for
  them; `purefb_audit_policy` makes the member set exactly `buckets` and
  removes the members before an absent policy.
- Part 3 adds eight more in the same style, all with check mode and
  idempotent: `purefb_telemetry_target` (REST: `name`, `endpoint` as
  `host:port[/path]`, `transport`, `source`, `enabled`, `policies` to
  attach to, `test`, `state`; returns `target` and, with `test`, the
  array's test result), `purefb_telemetry_policy` (REST: `name` one of
  the built-ins, `targets` as the full list; returns `policy` with the
  policy's metric names and resolution; `enabled` is read-only on 2.21),
  `purefb_maintenance` (`state`, `timeout` in hours 1 to 48, `extend`;
  returns `window`), `purefb_qos` (`name`, `max_total_ops_per_sec` of
  100 or more, `max_total_bytes_per_sec`, `enabled`, `file_systems` and
  `buckets` to assign, `state`; absent clears before deleting; returns
  `policy`), `purefb_fs_session` (an action module: `kind` sessions,
  locks or both, `client`, `user`, `protocols`, `disruptive`,
  `file_system`, `path`, `recursive`, `inode`; one of client, user or
  file system required; returns `removed` with counts and names),
  `purefb_legal_hold` (`name`, `description`, `state`, and `file_system`
  with `paths` and `recursive` for held entities under `entities_state`;
  returns `hold` with its entities), `purefb_alert_flag` (REST: `alert`
  the numeric name, `flagged`; returns `alert`) and `purefb_eradication`
  (`eradication_delay_days` 1 to 30, `manual_eradication` all-enabled or
  all-disabled, `force`; returns `eradication_config`). The REST-only
  modules carry a small helper that logs in with the token, uses the
  session header and logs out in a `finally`, and never puts the token in
  a message.
- Already sufficient for part 2: `purefb_s3user state=keystate key_name=
  enable_key=` (the re-enable after a canary test), `purefb_bucket`
  (quota, hard limit, Object Lock options, `block_public_access`,
  `block_new_public_policies`), `purefb_s3acc`; and for part 3:
  `purefb_bucket_replica paused` (the resume), `purefb_certs generate`
  (the renewal), `purefb_connect` (the lab's array connection),
  `purefb_fs promote` (the drill uses the REST field directly so that it
  can poll `promotion_status`).
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
- The stock `ansible.eda` sources (webhook, generic, tick, alertmanager)
  resolve only
  from `ANSIBLE_COLLECTIONS_PATH`; `env.sh` puts `~/.ansible/collections`
  on it, with `./collections` in front when that directory exists.
- `ansible.eda.tick` carries nothing a condition can read the clock from,
  so `housekeeping.yml` runs its playbooks on every tick and the
  playbooks decide by the date: `showback.yml` skips when today's ledger
  object exists, `dr_drill.yml` runs only at `dr_drill_hour` and keeps a
  marker object per day and file system. On the platform `housekeeping.yml`
  has no activation; three controller schedules launch the selftest, the
  showback and the DR drill templates instead.

## Containerized platform

Facts about the containerized Ansible Automation Platform 2.7 topology the
seed, the two images and the activations are written around;
[aap/README.md](../aap/README.md) has the longer form.

- The controller's task container keeps a container image store of its
  own, and the EDA activation workers start activations through the host
  user's podman service, whose storage the installer configures
  separately; neither is the host user's command-line store. An image that
  exists only there (`localhost/flashblade-eda-ee` built on the same
  machine) fails at job or activation start with "pinging container
  registry localhost", because a `localhost/` name the store lacks is
  pulled from a registry called localhost. Either push both images to a
  registry the platform can pull from (the private automation hub counts)
  or load each into the store that needs it: the execution environment
  with `podman save localhost/flashblade-eda-ee:latest | podman exec -i
  automation-controller-task podman load`, the decision environment with
  `podman save localhost/flashblade-eda-de:latest | podman --remote load`
  (the socket the activation workers use).
- A decision environment created through the API defaults to the pull
  policy `always`; an activation on a locally loaded image then fails to
  start with "x509: certificate is valid for <platform host>, not
  localhost", the worker's pull of `localhost/...` meeting the gateway's
  certificate. `aap_eda_de_pull_policy` is `missing` by default and `never`
  for an image loaded as above, so that the worker never asks a registry
  for the `localhost/` name; `aap_ee_pull: missing` does the same for the
  execution environment.
- A job's execution environment reaches a service on the platform host
  only as `host.containers.internal`; the host's own address and loopback
  point back at the container. The orchestrator credential's
  `orchestrator_url` carries that name when the orchestrator runs on the
  platform host (with `orchestrator_validate_certs: false` while it
  carries a self-signed certificate), and the seed leaves
  `orchestrator_url` out of an activation's extra vars when that
  credential is attached, so the credential's value is the one the post
  uses.
- `run_job_template` needs a controller to talk to: every activation
  carries the `Platform user - flashblade-eda` credential (the built-in
  `Red Hat Ansible Automation Platform` type, host
  `<gateway>/api/controller/`), whose user needs the execute role on the
  templates of `aap_organization` and nothing more. The action holds the
  ruleset until the job ends, so three events three seconds apart become
  three jobs about a minute apart.
- One credential per type. The controller refuses a second credential of
  a type a template already carries, and the EDA controller refuses two
  credentials on one activation whose injectors name the same variable.
  Every `FlashBlade - <array>` credential injects `fb_url`, `api_token`
  and `credential_array`, which is why one `FlashBlade fleet` credential
  carries every array (`purefb_fleet_json` on the controller,
  `purefb_arrays` on the EDA side, the JSON list the source plugins
  accept) and the per-array credentials serve launches that pin an array.
  The `FlashBlade object store` type's `FB_OBJ_*` names belong to that
  type alone, so its credential sits beside the fleet, platform-user and
  orchestrator credentials; a second object-store array is a second
  `FlashBlade object store - <array>` credential, a second object-audit
  activation and a credential chosen at launch for the S3 templates,
  never two such credentials on one template or activation.
  `tools/activation_audit.py` leaves the injected keys (`purefb_arrays`,
  `config_arrays`) out of its comparison for the same reason.
- Activations run with host networking: a listener inside an activation
  (the syslog and Alertmanager sources) listens on the host's port, so the
  arrays' syslog servers and Alertmanager address the platform host
  itself, the host firewall admits `syslog_port` over TCP and UDP and
  `alertmanager_port` over TCP, and a sibling project's activations on the
  same host take ports of their own. The webhook rulesets have no platform
  variant, so those two are the only ports the FlashBlade activations
  bind; the example vars keep the standalone ports.
- An activation created while several others are still starting can fail
  its readiness check ("Activation is unresponsive. Readiness check for
  ansible-rulebook timed out") a few times before it runs; the seed
  enables them ten seconds apart and the on-failure restart policy carries
  the rest.
- The activations listing (`GET /api/eda/v1/activations/`) is the EDA
  controller's slowest call: with twenty activations and their monitors
  busy, its worker exceeds the gunicorn timeout and the gateway answers
  500 or 503, while a single activation's detail still answers. The seed
  asks for pages of 50 and retries the call; `tools/activation_audit.py`
  pages by 50 and waits ten seconds between its eight attempts; a hand
  check reads one activation at a time.
- A restarted activation whose websocket handshake the gateway answers
  502 (the EDA websocket service busy with other starts) fails at once
  and, past its restart limit, stays failed: restart it again until it
  reports running, one activation at a time.
- `ansible.eda.rulebook_activation` cannot modify an activation; the
  enabled flag is the one thing a seed can still set, so the seed enforces
  it on every run and a site keeps it true once the activations run. An
  activation keeps the rulebook and the extra vars it was created with,
  and a project is synced when it is created, not on later seed runs: a
  changed playbook reaches a job template after a project sync, a changed
  rule or a new vars key reaches an activation after it is recreated
  (`tools/activation_audit.py --recreate`). After an activation is deleted
  the EDA controller keeps its name for a while after the id has left the
  list, and a create with the same name inside that window answers 400
  "activation with this name already exists"; the gateway also answers an
  occasional 5xx to a create or a delete the controller has in fact
  carried out, so the tool looks the name up after every failed create,
  judges a delete by the id leaving the list and retries the list itself.
  The controller hands `extra_var` back re-serialised with sorted keys and
  without the credential-injected vars, so the tool compares parsed vars,
  never the text.
- The jobs' paths are the execution environment's: `aap_job_extra_vars`
  puts `alert_log`, `request_dir` and `baseline_dir` under `/tmp`, which
  lives and dies with the job's container, so on the platform the job
  output, the Slack post and the orchestrator's execution record are the
  record, and a request file written by one job is not seen by the next
  (the reclaim rebuilds its request from the artifacts for that reason).
- A rootless container's non-root user cannot read a 600-mode file
  bind-mounted from the host (the host user maps to the container's root,
  not to the image's user); a podman secret is mounted readable under
  `/run/secrets`, which is how the approval bot gets its tokens
  (`slack/README.md`).

## Ansible templating

- Inside a Jinja expression Ansible doubles backslashes before Jinja
  parses the string, so `'\n'` written in `{{ }}` is a backslash and an
  `n`, not a line break; a real one belongs to the YAML. Regex arguments
  are the exception. A unit test refuses such literals outside `regex_*`
  filters.
- A JSON text handed from one task to another through `vars:` can arrive
  as a dict: `lookup('file', ...)` of a request file, passed as
  `s3_body` and templated again in a task's `environment:`, reached the
  S3 helper as a Python repr with single quotes (seen on the lab: the
  mirrored request object was not JSON). `tasks/s3_object.yml` therefore
  carries bodies base64-encoded (`S3_BODY_B64`, a string or a dict
  turned back into JSON first) and decodes them in the helper, and
  `s3_approval.yml` treats an object that parses to an empty dict as
  missing.

## State

- FlashBlade has no tags on file systems or buckets through REST 2.30
  (only workloads carry tags, 2.23+), so state that must survive a
  rulebook restart lives on the array as a name or a bit, or in a file:
  the QoS cap is a policy named `eda-cap-<fs>` (read back as
  `eda_capped`), the tarpit is the one policy `eda-tarpit` (read back as
  `eda_tarpit`), a quarantined client is its `ro` export rule or legacy
  entry, a handled alert is `flagged`, the approval request is a
  file under `request_dir`, the maintenance window is itself the state.
- Part 3 adds two kinds of object in the state bucket, both written with
  the state key through `tasks/s3_object.yml`: the showback ledger
  `<showback_prefix><yyyy-mm-dd>/<array>.json` (skipped when it exists,
  so one run a day) and the DR drill marker
  `<showback_prefix>dr/<date>-<file system>.json` (a second drill the
  same day finds it and stops).
- The object-audit cursor (`last_key`, the seen keys of the retained
  windows, the time) is a file `<cursor_dir>/<array>.json` written
  atomically and, when the state key is configured, an object
  `cursors/object_audit/<array>.json` in `eda-state` (best effort, a
  warning on failure); a request also written as
  `<log_bucket>/requests/<id>.json` is answered by
  `approvals/<id>.json` and closed by `receipts/<id>.json`, and a disabled
  key reads back as `enabled false`.

## What the lab cannot show

- SMB file audit (no Active Directory join), NFS file audit (4.8.0), file
  log targets (directed availability), Kafka notifications (4.10.a).
- A real hardware fault: the LED path runs live on a healthy component,
  the fault itself is replayed from `tests/fixtures/` (a blade, a LAG).
- A hot array: last-week baselines are idle, so the client and QoS rules
  fire on lowered thresholds under a fio load from the client VM, which
  also hosts the OTLP receiver.
- Replication end to end beyond one link: the array connection to the
  4.5.6 peer came up once created without replication addresses
  ("Replication" above) and carries the `eda-dr-link` file system link;
  a file system that has ever had a legal hold applied cannot be linked
  ("File system with legal holds applied does not support replication",
  which is why the drill uses a second scratch file system); bucket links
  and a lagging or paused link are replayed only; the peer has no LLDP,
  CSR or object audit.
- The OTLP path beyond one receiver: a Prometheus 3.16 receiver ran as
  plain processes on the client VM and the rulebook host reached its
  Alertmanager over an ssh reverse tunnel ("Telemetry" above has what it
  showed); a TLS receiver (`otlp-http-protobuf` with a CA certificate
  group), a Datadog or Dynatrace target, and a success body from
  `GET /telemetry-targets/test` (it answered 400 even while pushes landed)
  were not seen.
- Object-audit latency below one rotation: 5 minutes is the floor the
  array accepts for `log_rotate`, and a disabled key is seen by its next
  request, not by the one that tripped the canary.
