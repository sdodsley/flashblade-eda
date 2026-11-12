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
| `plugins/modules/` | `purefb_log_target.py`, `purefb_audit_policy.py` and `purefb_bucket_audit_filter.py` |
| `playbooks/` | the playbooks, `runbooks/` and `tasks/` |
| `meta/runtime.yml` | `requires_ansible: ">=2.15.0"` |
| `README.md`, `LICENSE` | the collection readme and licence |

Everything else in the tree (tests, docs, the local launcher and the
environment files, `venv/`, `collections/` and `build/`) is development
material and is listed in `build_ignore`, so the built artifact carries the
collection content only. The `build_ignore` list already names the
directories the FlashArray sibling grew later (`aap/`, `orchestrator/`,
`prometheus/`, `decision-environment/`, `slack/`, `tools/`), so adding them
here needs no packaging change.

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

`plugins/modules/` is collection content too: the three object-audit
modules the collection has no equivalent for yet (log targets, audit
policies and their members, bucket audit filters). A playbook names them
`sdodsley.flashblade_eda.purefb_log_target`,
`sdodsley.flashblade_eda.purefb_audit_policy` and
`sdodsley.flashblade_eda.purefb_bucket_audit_filter`, with the same
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

There is no image definition in this repository yet. The FlashArray
sibling's `decision-environment/Containerfile` and ansible-builder
definition (on `quay.io/ansible/ansible-rulebook:v1.3.2`) build the
collection from the checkout and install it with its dependencies; the
same two files with `flasharray` replaced by `flashblade` and
py-pure-client pinned at 1.98.0 or newer (the FB_2_30 client, see below)
produce the image for this project. The facts that definition is written
around (the upstream base image keeps `ansible.eda` under
`/app/.ansible/collections`, `additional_build_files` needs one `dest` per
directory, `python_interpreter` must pin the base's 3.12, the supported
base lacks what `systemd-python` needs) hold here unchanged.

## Version floors

The plugin negotiates the REST version the array serves (2.21 on
Purity//FB 4.6.4) and must not call methods the negotiated client lacks:
`get_telemetry_*` exist in the SDK from FB_2_29 although the array serves
`/telemetry-targets` on 2.21, and `get_software_installations` and
`get_workloads` arrive later still. Where a playbook needs an endpoint the
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
