# Changelog

## Unreleased

- `tools/activation_audit.py` rides out a busy gateway: transient answers
  (5xx, 403, a dropped connection on a read) are retried for two minutes,
  the activation list is read in pages of twenty, the wait for the list
  after a delete lasts the name-release window, and `--recreate --enable`
  creates the new activation enabled whatever flag the old one carried.
  The seed's activation listings retry a 403 as they do a 5xx, and the
  seed stops when a listing has a further page, since it reads one page.

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
