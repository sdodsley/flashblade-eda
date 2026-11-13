# Contributing

This is a prototype maintained in the open. Issues and pull requests are
welcome, especially rules you wish existed and reports of what a real
FlashBlade did that the code did not expect. Bugs in the
`everpure.flashblade` modules themselves go to
https://github.com/Everpure-Ansible/FlashBlade-Collection, not here.

## Before you push

```
python3 -m venv venv && . venv/bin/activate
make deps      # Python requirements and the two collections
make check     # black, pyflakes, yamllint, ao_lint, unit tests, playbook syntax
```

`make check` is what CI runs; black and pyflakes cover the plugins, the
modules, `tools/`, `slack/` and the tests, and `make lint-ao` runs
`tools/ao_lint.py` over `orchestrator/*.json` against
`orchestrator/job_templates.txt`. The unit tests need no array: the
plugins are exercised with a fake SDK client and a fake boto3 client
(`boto3` is stubbed when it is not installed), the modules with a fake
AnsibleModule and client, the platform rulebook generator against the
committed files, the activation audit, the orchestrator sync tool and the
Slack bot against in-process fakes, the handoff playbook against
`tests/mock_orchestrator.py`, and the rulebook tests validate every rulebook against
ansible-rulebook's own schema, check that every lowercase variable a
rulebook references is documented in `vars/example.yml` (uppercase names
such as `APPROVAL_TOKEN` are environment variables passed with
`--env-vars`), that every playbook a rule runs exists, and that the replay
copies under `tests/` are rule-for-rule identical to the real rulebooks,
throttles included.

## Conventions

- Rulebooks decide, plugins observe. A source plugin emits facts (including
  the booleans and counters rules need, because rulebook conditions cannot
  do arithmetic or use filters); policy lives in the rulebook.
- ansible-rulebook facts to respect, not to fix (the full list is in
  [docs/HARDWARE_NOTES.md](docs/HARDWARE_NOTES.md)): conditions allow no
  Jinja filters or arithmetic (actions do); `throttle.once_within` must be a
  literal and cannot sit on a multi-event condition; `--vars` is one
  untemplated file; `localhost` must be in the inventory; `run_playbook`
  copies the playbook's whole directory, so relative includes within
  `playbooks/` work and files outside it do not.
- An action that changes an array is gated by what its event carries:
  `maintenance_active` where the event has it (alert, audit, session,
  health, hardware, config, posture and replication events; telemetry
  events do not),
  `automation_users` on audit events and `automation_keys` on S3 events so
  the automation never reacts to itself, and a per-site switch that falls
  back to notify on the file system grow, the user quota raise, the client
  diagnostics, the QoS cap, the canary and anomaly key disable, the audit
  re-attach, the public-bucket revert, the client quarantine
  (`ir_auto_contain`), the tarpit (`ir_auto_tarpit`), the paused-link
  resume (`replication_auto_resume`) and the certificate renewal
  (`certs_auto_renew`); the lock and session release has no switch, only
  the approval webhook on `locks_port`. A new action states which of the
  three it has, and README.md's gating paragraph says so.
- Every record a playbook appends to the alert log carries `time`, `kind`,
  `severity` and `array`.
- State that must survive a rulebook restart lives on the array or in a
  file, never in a rulebook fact. FlashBlade has no tags on file systems
  or buckets, so the array-side state is a name or a bit: QoS policies
  `eda-cap-<object>` and `eda-tarpit`, the alert `flagged` bit, an export
  rule `client == <ip>, permission ro`, a key's `enabled` state; the
  object-audit cursor is a file under `cursor_dir` with a copy in the
  state bucket, and requests, approvals and receipts are objects in the
  log bucket ([docs/DESIGN.md](docs/DESIGN.md), "State without tags").
- S3 credentials are named, never carried: the plugin and the playbooks
  take the environment variable names (`s3_access_key_env`,
  `state_secret_key_env`, ...) and read the values from the environment;
  `tasks/s3_object.yml` hands them to its Python snippet through the
  environment, not the command line, and every task with a secret in
  scope is `no_log`. A fixture or a test uses a made-up access key id such
  as `PSFBSAZRTESTKEY0001`; no real key id or secret goes into the tree.
- A module under `plugins/modules/` follows the collection's style
  (DOCUMENTATION, EXAMPLES and RETURN blocks, check mode, idempotent, the
  collection's `module_utils` imported inside a guarded try) so that it
  can move to `everpure.flashblade` unchanged, and has unit tests in
  `tests/unit/test_modules.py` that drive `main()` with a fake
  AnsibleModule and a fake client ([docs/PACKAGING.md](docs/PACKAGING.md)
  has the licence note for the move).
- Use the endpoint, field, module and option names the array and the
  collection actually have (`docs/HARDWARE_NOTES.md` lists the verified
  ones); where something still has to be confirmed on an array, say so in
  a comment that starts `verify:`.
- Keep lab specifics out of committed files. `vars/local*.yml`,
  `inventory/local*.yml`, `baseline/*.yml` (except `example.yml`) and
  `*.env` are gitignored for that; no host names, addresses or
  home-directory paths from a lab belong anywhere in the tree.
- The platform rulebooks are generated, never edited. After a change to a
  rulebook under `extensions/eda/rulebooks/`, run
  `python tools/platform_rulebooks.py` and commit
  `extensions/eda/rulebooks/platform/`; the unit test fails when the
  committed files differ from the generator's output. A playbook a rule
  runs for the first time needs its job template name (`FlashBlade -
  <playbook words>`) in the generator's table and in `aap/seed.yml`, and
  in `orchestrator/job_templates.txt` when a platform rulebook or a
  definition launches it; a webhook-only or tick-only ruleset has no
  platform variant.
- An orchestrator definition follows the contract in
  [docs/ORCHESTRATOR.md](docs/ORCHESTRATOR.md): `schema_version` 2.0.0,
  unwrapped trigger bodies (`${trigger.event...}`), `credential_names`
  rather than ids, the approver groups `admins` and `approvers`,
  `REPLACE_AT_SYNC` for the per-environment ids, every node passing only
  event, form or earlier-node data (site-specific keys stay on the job
  templates), and every `${node.artifacts.key}` it reads published by that
  template's playbook with `set_stats` (`ARTIFACT_KEYS` in
  `tests/unit/test_playbook_artifacts.py`). Every rule of `tools/ao_lint.py`
  has a bad fixture under `tests/fixtures/orchestrator/`; `make lint-ao`
  runs before a push.
- `tools/` and `slack/` run on Python 3.9 (a platform host's system
  Python): no `match` statements, no `X | Y` type hints, and `tools/` uses
  the standard library and PyYAML only.
- Changes that touch an array should be validated against one; say in the
  pull request what ran and what the array did.
- Every `.py`, `.yml` and `.sh` file starts (after the shebang or the
  `---`, if any) with `# Copyright 2026 Everpure, Inc.` and
  `# SPDX-License-Identifier: Apache-2.0`; a JSON file (`aap/files/`,
  `orchestrator/`, the fixtures) carries no header.
