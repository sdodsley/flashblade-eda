# Changelog

## 0.1.0 (unreleased)

First part of flashblade-eda: Event-Driven Ansible for Everpure
FlashBlade, a prototype and the sibling of flasharray-eda. Written against
Purity//FB 4.6.4 (REST 2.21) on a FlashBlade//S200 with py-pure-client
1.98.0 and everpure.flashblade 1.29.0; the lab exit tests are listed in
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
  fields the drift rulebook compares. The `replication` and `posture`
  signals of the design are refused by name until a later part.
- `purefb_syslog` receives what a FlashBlade syslog server sends: the
  `purity.alert` and `purity.audit` layouts of the management service,
  parsed into fields, and the one-JSON-object-per-line records of the
  `data-audit` service (`pf: fb`, and `pf: fa` from a FlashArray, one
  parser for both), with the TCP line limit raised to the 128 KB a
  data-audit record may need.

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
- The gates of the FlashArray project carry over: `maintenance_active`
  (false where the endpoint is absent, REST < 2.16), `automation_users`,
  literal throttles, per-site switches that fall back to notify, approval
  on every eradication.

### Playbooks

- Act on the arrays through `everpure.flashblade` 1.29.0 or later:
  `purefb_ntp`, `purefb_dns`, `purefb_hardware`, `purefb_fs`,
  `purefb_bucket`, `purefb_snap`, `purefb_userquota`, `purefb_groupquota`.
  Where the collection has no module (maintenance windows, QoS policies,
  user quota reads, the alert `flagged` bit) `playbooks/tasks/fb_rest.yml`
  makes one raw REST call against `/api/<fb_api_version>/` with the
  inventory token exchanged for a session at `POST /api/login`.
- `notify.yml`, `alert_runbook.yml`, `capacity_request.yml`,
  `capacity_reclaim.yml` and `orchestrator_post.yml` are the FlashArray
  playbooks with FlashBlade objects; `flag_alert.yml`, `grow_fs.yml`,
  `user_quota.yml`, `client_diagnose.yml`, `qos_cap.yml`, `qos_lift.yml`
  and `maintenance_window.yml` are new.

### Repository

- The repository is the collection `sdodsley.flashblade_eda` (`galaxy.yml`,
  `meta/runtime.yml`, `make collection`); `run.sh`, `env.sh` and
  `env.example` read `FBEDA_*` and `FB_A_*` / `FB_B_*`; `make check` runs
  black, pyflakes, yamllint, the unit tests and a playbook syntax check,
  and GitHub Actions runs the same.
- Apache-2.0; SPDX headers on every Python, YAML and shell file.
