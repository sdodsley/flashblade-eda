# Packaging

The repository is the Ansible collection `sdodsley.flashblade_eda`. This
page describes how the collection is laid out, built and installed, and
what changes when the content moves into `everpure.flashblade`.

## The repository is the collection

`galaxy.yml` sits at the repository root, so the root directory is the
collection source. The collection content is what ansible-rulebook and the
EDA controller need to resolve the event sources and run the playbooks:

| Path | Content |
| --- | --- |
| `extensions/eda/plugins/event_source/` | `purefb_events.py`, `purefb_object_audit.py` and `purefb_syslog.py` |
| `extensions/eda/rulebooks/` | the rulebooks, which name their sources by FQCN |
| `plugins/modules/` | `purefb_log_target.py`, `purefb_audit_policy.py`, `purefb_bucket_audit_filter.py`, `purefb_telemetry_target.py`, `purefb_telemetry_policy.py`, `purefb_maintenance.py`, `purefb_qos.py`, `purefb_fs_session.py`, `purefb_legal_hold.py`, `purefb_alert_flag.py` and `purefb_eradication.py` |
| `playbooks/` | the playbooks, `runbooks/` and `tasks/` |
| `meta/runtime.yml` | `requires_ansible: ">=2.15.0"` |
| `README.md`, `LICENSE` | the collection readme and licence |

Everything else in the tree (tests, docs, the local launcher and the
environment files, `venv/`, `collections/` and `build/`) is development
material and is listed in `build_ignore`, so the built artifact carries the
collection content only. `build_ignore` also lists `aap/`,
`orchestrator/`, `prometheus/`, `decision-environment/`, `slack/` and
`tools/`, so the platform material stays out of the artifact.

The event sources are `sdodsley.flashblade_eda.purefb_events`,
`sdodsley.flashblade_eda.purefb_object_audit` and
`sdodsley.flashblade_eda.purefb_syslog`, and every rulebook declares one of
them under those names. ansible-rulebook resolves a FQCN source from
`ANSIBLE_COLLECTIONS_PATH`, which is why the collection has to be
installed before a rulebook starts; `run.sh` still passes `--source-dir`,
which ansible-rulebook consults first for bare names and which is harmless
for a FQCN. The dependencies are declared in `galaxy.yml`: `ansible.eda`
>= 2.13.0 (the stock webhook, tick and generic sources) and
`everpure.flashblade` >= 1.29.0 (the modules the playbooks run).
`requirements.yml` lists the same two for `ansible-galaxy collection
install -r`.

`plugins/modules/` is collection content too: the eleven modules the
collection has no equivalent for yet (`purefb_log_target`,
`purefb_audit_policy` and `purefb_bucket_audit_filter` for the
object-audit configuration; `purefb_telemetry_target`,
`purefb_telemetry_policy`, `purefb_maintenance`, `purefb_qos`,
`purefb_fs_session`, `purefb_legal_hold`, `purefb_alert_flag` and
`purefb_eradication`). A playbook names them `sdodsley.flashblade_eda.<module>`, with the same
`fb_url`, `api_token` and `disable_warnings` options as the collection's
own modules, because they import `get_system` and `purefb_argument_spec`
from the `everpure.flashblade` module_utils; that collection has to be
installed for them to run, which the `galaxy.yml` dependency already
requires. They are written in the collection's style so that they can move
upstream as they are; note that everything in `everpure.flashblade` is
GPL-3.0-or-later, so upstreaming re-licenses them from this repository's
Apache-2.0 to that licence.

## Build and install

    make collection

builds the tarball into `build/` with `ansible-galaxy collection build` and
installs it, with its dependencies when they are not already installed,
under `./collections` with `ansible-galaxy collection install --force -p
collections`. `env.sh` puts `./collections` first on
`ANSIBLE_COLLECTIONS_PATH` when that directory exists, so `run.sh` and the
end-to-end script resolve the FQCN sources from it. `make deps` installs
the Python requirements and the two collections and then runs `make
collection`, so a fresh checkout is ready after `make deps`. CI runs `make
collection` after the lint (so that the installed collections are not
linted) and before the unit tests. `build/` and `collections/` are
gitignored and in `build_ignore`, so building from a tree that already
holds an installed copy does not package it twice.

## The decision environment

`decision-environment/Containerfile` and
`decision-environment/execution-environment.yml` (ansible-builder v3) build
the same image on `quay.io/ansible/ansible-rulebook:v1.3.2`;
`aap/decision-environment.yml` builds the same content on the supported
`de-supported-rhel9` base for Ansible Automation Platform, and
`aap/Containerfile.ee` with `aap/execution-environment.yml` build the
execution environment the job templates run in on `ee-supported-rhel9`.
`make de` runs the Containerfile build. Each image contains:

- py-pure-client, boto3 and requests (`decision-environment/requirements.txt`,
  or the inline Python list of the ansible-builder definitions);
- the collection `sdodsley.flashblade_eda`, built from the checkout at
  image build time and installed under `/usr/share/ansible/collections`
  together with `everpure.flashblade` and, where the base image does not
  already carry it, `ansible.eda`. `plugins/` is part of the copied
  content: the playbooks call the collection's own `purefb_*` modules, and
  a project checkout is not a collections path, so the modules have to be
  in the execution environment as well as the event sources in the
  decision environment. The Containerfiles copy the collection content in,
  run `ansible-galaxy collection build` and install the tarball; the
  ansible-builder definitions copy the content into the build context with
  `additional_build_files` and install it through a galaxy requirement of
  type `dir`, which ansible-galaxy builds on the fly in the galaxy stage
  (the form ansible-builder v3 supports for content that is not on a Galaxy
  server; a `dir` requirement honours `build_ignore`);
- in the two decision environments, the rulebooks and playbooks as plain
  files under `/opt/flashblade-eda`, the working directory, so that a
  standalone `podman run` names a rulebook by path and
  `run_playbook: playbooks/<name>.yml` resolves;
- in the two decision environments, `/var/log/flashblade-eda`,
  `/var/lib/flashblade-eda/requests` and `/var/lib/flashblade-eda/object_audit`,
  the default `alert_log` and `request_dir` and a place for the
  object-audit source's cursor files, writable by the runtime user.

The upstream base image ships `ansible.eda` under `/app/.ansible/collections`
and `ansible-galaxy collection install` leaves a dependency that is already
installed there alone, so `ANSIBLE_COLLECTIONS_PATH` in those two images is
`/usr/share/ansible/collections:/app/.ansible/collections`; with the first
directory alone ansible-rulebook cannot find `ansible.eda.alertmanager` and
the other stock sources. The supported bases already keep `ansible.eda`
under `/usr/share/ansible/collections`, so the `aap/` definitions need no
override.

Three things about ansible-builder 3 that the definitions are written
around: `additional_build_files` copies a directory's *contents* into
`_build/<dest>`, so each collection directory names its own `dest`
(`flashblade_eda/extensions`, `flashblade_eda/playbooks`,
`flashblade_eda/plugins`, `flashblade_eda/meta`) or they merge into one;
the base image's `/usr/bin/python3` is 3.9 while ansible-rulebook and the
`PYTHONPATH` it sets are 3.12, so `python_interpreter` pins
`/usr/bin/python3.12` for the build stages; and `ansible.eda`'s
requirements name `systemd-python`, which has no wheel and needs Python
headers, systemd headers and a compiler the supported images lack, so the
`aap/` definitions exclude it (`dependencies.exclude.python`; nothing in
this project reads the journal). On a rootless host with native overlay,
buildah can fail every multi-stage `COPY --from` with "copier: stat ... no
such file or directory" although the stage image holds the path; a
separate image store with the `vfs` driver (`podman --root <dir>
--storage-driver vfs`, put first on the PATH as `podman` for the build)
builds the image. A container from a builder image names its rulebook,
inventory and vars by absolute path or sets `-w /opt/flashblade-eda`: the
final stage keeps that working directory, but a relative path is the first
thing to check when a run says a file does not exist.

Because the sources and modules resolve from the installed collection, one
image serves two uses: a standalone `podman run ... ansible-rulebook
--rulebook extensions/eda/rulebooks/<name>.yml` without `--source-dir` (the
Containerfile header has the full line), and EDA controller activations,
which pass no `--source-dir` and resolve custom sources from the
collections in the decision environment only. On a containerized platform
the images are loaded into the stores the controller and the activation
workers use, not the host user's ([aap/README.md](../aap/README.md),
"Images on a containerized platform").

## Version floors

The plugin negotiates the REST version the array serves (2.21 on
Purity//FB 4.6.4, 2.30 on 4.8.8) and must not call methods the negotiated
client lacks: `get_telemetry_*` exist in the SDK from FB_2_29, while a
4.6.4 array serves `/telemetry-targets` on 2.21 and a 4.8.8 array from
2.29 only, so the REST callers (the two telemetry modules, the telemetry
playbooks and `tools/telemetry_rules.py`) ask for the newest version the
array serves; `get_software_installations` and `get_workloads` arrive
later still. Where a playbook needs an endpoint the
collection has no module for, `playbooks/tasks/fb_rest.yml` calls it with
`ansible.builtin.uri` against `/api/<fb_api_version>/`, default 2.16, the
floor for maintenance windows. Raising `fb_api_version` is a site choice,
not a code change.

## Moving the content into everpure.flashblade

The content is already in collection layout, so the move is a rename plus
the collection's own review steps:

1. **Source names.** `sdodsley.flashblade_eda.purefb_events`,
   `sdodsley.flashblade_eda.purefb_object_audit` and
   `sdodsley.flashblade_eda.purefb_syslog` become
   `everpure.flashblade.purefb_events`,
   `everpure.flashblade.purefb_object_audit` and
   `everpure.flashblade.purefb_syslog` in the rulebooks that name them, in
   the plugins' `EXAMPLES` blocks and in the unit tests that assert the
   names. The `ansible.eda` dependency moves into the collection's
   `galaxy.yml`.
2. **Playbook references.** `run_playbook: name: playbooks/notify.yml`
   becomes the collection playbook FQCN (`everpure.flashblade.notify`),
   one per playbook. Re-check `alert_runbook.yml`'s relative include of
   `runbooks/<code>.yml` from a collection playbook, where `playbook_dir`
   points into the installed collection, and the shared
   `playbooks/tasks/resolve_array.yml` and `playbooks/tasks/fb_rest.yml`
   includes for the same reason.
3. **Modules first.** Several playbooks reach the array through
   `tasks/fb_rest.yml` because the collection has no module yet
   (maintenance windows, QoS policies, user quota reads; the list is in
   [DESIGN.md](DESIGN.md), "Collection gaps"). Each of those is a module
   pull request on the collection before the playbook moves, and the raw
   REST task goes away with it.
4. **Variables and secrets.** The `--vars` file (`vars/example.yml`) is the
   activation's extra vars. `APPROVAL_TOKEN` and `GIT_WEBHOOK_SECRET` are
   credentials injected as environment variables in a standalone run
   (`--env-vars`); on a platform the webhook rulesets are event streams,
   which authenticate at the platform.
5. **Documentation.** The three event sources carry `DOCUMENTATION` and
   `EXAMPLES` blocks (a unit test asserts every argument the code reads is
   an option). On the move, re-check them with the collection's doc
   tooling, add a README section for the EDA content, and a changelog
   fragment (quote entries that contain `key: value` text or the changelog
   sanity test fails).
6. **Tests.** `tests/unit/test_events_plugin.py` with the `conftest.py` SDK
   stub moves to `tests/unit/plugins/event_source/`; `ansible-test sanity`
   and `ansible-lint` run as the collection's CI does. The replay rulebooks
   and `tests/unit/test_rulebooks.py` stay in this repository.
7. **Certification.** Red Hat's review of certified EDA content looks for
   asyncio sources with `main(queue, args)` that never block the event loop
   (the pollers run SDK calls in a thread pool), clean shutdown on
   cancellation, documented arguments, rulebook examples under
   `extensions/eda/rulebooks`, unit tests, and a published decision
   environment definition. Confirm the list against the partner guide
   before submitting. Automation Hub judges the built artifact, so check
   that `build_ignore` keeps `extensions/`.
