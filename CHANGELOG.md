# Changelog

## Unreleased

- `tools/activation_restart.py` makes at least two passes, ninety seconds
  apart: the first reads after the EDA API comes up can show an activation
  as running from before the reboot, which the worker marks failed seconds
  later. An activation that used its restarts is not restarted again.
- The syslog rulebook notifies an open alert once a day instead of once
  every ten minutes: the array repeats the alert's line every quarter hour
  while it stays open, so the throttle now keys on the alert's first-seen
  time, which a re-opened alert changes.

## 0.1.2 (2026-11-17)

Hardening on a shared, containerized platform and the lab array's move to
Purity//FB 4.8.8: the platform tools stop listing activations and survive
a reboot, every REST call picks its version with a floor, and the syslog
alert path parses what a live array writes and grades it by state.

- The seed and `tools/activation_audit.py` read each activation by its full
  name and never list; the audit rides out a busy gateway and reports a
  rulebook whose activation is missing.
- `tools/activation_restart.py` restarts the activations a host reboot
  leaves failed, one at a time, from a user unit after the platform's own.
- `purefb_syslog` parses the alert and audit lines a live array writes,
  including a state of several words and the alert id (`alert_id`).
- The syslog rulebook grades an alert by its state: open at its severity,
  closed at info, closing and waiting to downgrade logged.
- Purity//FB 4.8.8: the telemetry endpoints answer from REST 2.29 up and
  name metrics collections with a resolution each; the telemetry modules,
  playbooks, `tools/telemetry_rules.py` and the rule file follow.
- `tasks/fb_rest.yml` asks for the newest REST version the array serves
  above the endpoint's and the site's floor and says why when none fits;
  `purefb_alert_flag` and `purefb_qos` negotiate the same way.
- The documents state what was verified on 4.6.4 and on 4.8.8.

## 0.1.1 (2026-11-14)

- The documentation describes the tree as it stands: the verification
  record lists each lab test with its result, the design and hardware
  notes no longer refer to build parts or dates, and CONTRIBUTING sets the
  shape of release notes and documents.
- `aap/seed.yml` enforces `aap_eda_activations_enabled` on the FlashBlade
  activations only. The EDA controller ignores the listing's name filter,
  so the previous loop disabled another project's activations on a shared
  controller.

## 0.1.0 (2026-11-13)

Event-Driven Ansible for Everpure FlashBlade: the first release, a
prototype, written against Purity//FB 4.6.4 (REST 2.21) with py-pure-client
1.98.0 and everpure.flashblade 1.29.0 and verified on a FlashBlade//S200,
its Purity//FB 4.5.6 replication peer and a containerized Ansible
Automation Platform 2.7 with the automation orchestrator beside it
([docs/VERIFICATION.md](docs/VERIFICATION.md)).

- Event sources: `purefb_events` (the alerts, audits, sessions, health,
  telemetry, config, posture and replication signals over REST),
  `purefb_syslog` (the management and data-audit syslog layouts) and
  `purefb_object_audit` (the object-audit log bucket, tailed with boto3).
- Eleven modules under `plugins/modules/` for what the collection lacks:
  log targets, audit policies and bucket audit filters, telemetry targets
  and policies, maintenance windows, QoS policies, sessions and locks,
  legal holds, the alert flag and the eradication config.
- Sixteen rulebooks (alerts, change control, config drift, health,
  capacity, user quota, client, maintenance, sessions, syslog, object
  audit, posture, Alertmanager, replication, containment and
  housekeeping) with the FlashArray project's gates: the maintenance
  window, the automation identities, literal throttles, per-site switches
  that fall back to notify, and an approval on every eradication.
- Thirty-five playbooks acting through `everpure.flashblade`, raw REST for
  the endpoints without a module, and S3 for the request, approval and
  receipt objects, the showback ledger and the DR drill markers.
- The Prometheus path: `tools/telemetry_rules.py` generates the alert
  rules from the array's own metric policies, with example receiver and
  Alertmanager configurations under `prometheus/`.
- The platform port: `aap/seed.yml` (the organization, credential types,
  35 job templates, the controller-tier reclaim workflow, three schedules
  and fifteen activations), the execution and decision environment
  images, the generated platform rulebooks, `tools/activation_audit.py`,
  seven orchestrator definitions with `tools/ao_lint.py` and
  `tools/ao_sync.py`, and the Slack approval bot.
- The repository is the collection `sdodsley.flashblade_eda`; `make check`
  runs the linters, the unit tests and a playbook syntax check, as CI
  does. Apache-2.0.
