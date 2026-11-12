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
make check     # black, pyflakes, yamllint, unit tests, playbook syntax
```

`make check` is what CI runs; black and pyflakes cover the plugins and the
tests. The unit tests need no array: the plugins are exercised with a fake
SDK client, and the rulebook tests validate every rulebook against
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
  health, hardware and config events; telemetry events do not),
  `automation_users` on audit events so the automation never reacts to
  itself, and a per-site switch that falls back to notify on the file
  system grow, the user quota raise, the client diagnostics and the QoS
  cap. A new action states which of the three it has, and README.md's
  gating paragraph says so.
- Every record a playbook appends to the alert log carries `time`, `kind`,
  `severity` and `array`.
- State that must survive a rulebook restart lives on the array or in a
  file, never in a rulebook fact. FlashBlade has no tags on file systems
  or buckets, so the array-side state is a name or a bit: QoS policies
  `eda-cap-<object>` and `eda-tarpit`, the alert `flagged` bit, an export
  rule `client == <ip>, permission ro`
  ([docs/DESIGN.md](docs/DESIGN.md), "State without tags").
- Use the endpoint, field, module and option names the array and the
  collection actually have (`docs/HARDWARE_NOTES.md` lists the verified
  ones); where something still has to be confirmed on an array, say so in
  a comment that starts `verify:`.
- Keep lab specifics out of committed files. `vars/local*.yml`,
  `inventory/local*.yml`, `baseline/*.yml` (except `example.yml`) and
  `*.env` are gitignored for that; no host names, addresses or
  home-directory paths from a lab belong anywhere in the tree.
- Changes that touch an array should be validated against one; say in the
  pull request what ran and what the array did.
- Every `.py`, `.yml` and `.sh` file starts (after the shebang or the
  `---`, if any) with `# Copyright 2026 Everpure, Inc.` and
  `# SPDX-License-Identifier: Apache-2.0`.
