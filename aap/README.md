# Ansible Automation Platform seed

`seed.yml` builds, from an empty Ansible Automation Platform 2.7, the
objects this project needs to run its playbooks as job templates, hand the
capacity reclaim to a human through a workflow approval, run the daily
work on schedules, and host the rulebooks as Event-Driven Ansible
activations. It is idempotent: every object is addressed by name, so
re-running it applies only what changed.

It creates nothing on an array and nothing in the automation orchestrator
(see "What it cannot seed").

## Prerequisites

- An Ansible Automation Platform 2.7 installation reachable at one gateway
  URL, with automation controller and the Event-Driven Ansible controller
  enabled.
- `AAP_HOST` (the gateway URL) and `AAP_TOKEN` in the environment. The
  first run creates the organization, which needs a platform
  administrator's token; after that an organization administrator's token
  is enough. The token is read from the environment only; it is never
  written to a file by the seed. `ansible.controller` 4.8.0, the build
  shipped with the 2.7 installer, declares `aap_token` but authenticates
  with HTTP basic auth only, so set `AAP_USERNAME` and `AAP_PASSWORD` for
  the same account as well; the gateway and Event-Driven Ansible modules
  use the token.
- The `FB_*_URL` / `FB_*_TOKEN` pairs for every array in `aap_arrays` and
  the `FB_*_S3_*` endpoint and keys for every entry of `aap_object_stores`
  (the same names `env.example` uses), and, if the orchestrator credential
  is wanted, `AO_URL`, `AO_CLIENT_ID` and `AO_CLIENT_SECRET`. The seed
  refuses to run with an array's or an object store's variables empty and
  skips the orchestrator credentials when `AO_CLIENT_ID` is empty. For
  Slack, optionally `SLACK_WEBHOOK_URL` (an incoming webhook, stored as a
  credential on the notify template) and `SLACK_BOT_TOKEN` with
  `SLACK_CHANNEL` (a bot token with `chat:write`, invited to the channel,
  for the platform's own Slack notification template). For a private
  repository, `GITHUB_TOKEN` (or the variable `aap_scm_token_env` names):
  the seed then stores it as a source control credential on both projects.
- The certified collections `ansible.platform`, `ansible.controller` and
  `ansible.eda` on the machine that runs the seed. The first two are
  published on Red Hat Automation Hub only, so they live in
  `aap/requirements.yml` rather than the top-level file that CI installs
  from Galaxy:

      ansible-galaxy collection install -r aap/requirements.yml   # Hub configured in ansible.cfg

- An execution environment image the platform can pull that contains
  py-pure-client, boto3, the `everpure.flashblade` collection and this
  repository built as the collection `sdodsley.flashblade_eda`. The default
  is the supported `ee-supported-rhel9` name with no registry; it lacks all
  four, and the playbooks call `sdodsley.flashblade_eda.purefb_*` modules
  that a project checkout cannot provide (a checkout is not a collections
  path). `aap/execution-environment.yml` is an ansible-builder definition
  that adds them on top of the supported base (`aap/decision-environment.yml`
  does the same for the Event-Driven Ansible side, where the collection's
  event sources are needed as well), and `aap/Containerfile.ee` builds the
  same content with `podman build` when ansible-builder is not available;
  build one of them, then set `aap_ee_image` (and `aap_ee_registry` if it
  is pushed somewhere) to the result, or expect the job templates to fail
  on the first module call. The controller does not install Python
  packages or collections for a project.

## Run

    cp aap/vars/example.yml vars/local-aap.yml      # gitignored with the other local files; edit
    export AAP_HOST=https://<gateway> AAP_TOKEN=<token> AAP_USERNAME=<user> AAP_PASSWORD=<password>
    export FB_A_URL=... FB_A_TOKEN=... FB_B_URL=... FB_B_TOKEN=...
    export FB_A_S3_ENDPOINT=... FB_A_S3_ACCESS_KEY=... FB_A_S3_SECRET_KEY=... \
           FB_A_S3_STATE_ACCESS_KEY=... FB_A_S3_STATE_SECRET_KEY=...
    export AO_URL=... AO_CLIENT_ID=... AO_CLIENT_SECRET=...              # optional: the orchestrator credential
    export SLACK_WEBHOOK_URL=... SLACK_BOT_TOKEN=... SLACK_CHANNEL=...   # optional: Slack
    export GITHUB_TOKEN=...                                             # optional: a private repository
    export FBEDA_VARS=vars/local-<site>.yml                             # the rulebook vars the activations carry
    ansible-playbook -i aap/inventory/example.yml aap/seed.yml -e @vars/local-aap.yml

`aap/vars/example.yml` is loaded as the defaults; the `-e @file` overrides
them. `aap/inventory/example.yml` is just localhost: the seed talks to the
platform API from where it runs. `FBEDA_VARS` names the rulebook vars file
(the one `run.sh` would pass with `--vars`); the activations get it as
their extra vars and the job templates take their per-template keys from
it, so a site writes one vars file for both ways of running.

## What it creates

| Object | Name | Notes |
| --- | --- | --- |
| Organization | `FlashBlade` | gateway object (`ansible.platform.organization`) |
| Setting | `TOWER_URL_BASE` | the base URL the platform writes into the links of its notifications; `aap_url_base`, by default the gateway URL (a site reachable by address only puts the address there) |
| Credential type | `FlashBlade fleet` | one input, `arrays`: a JSON list of `{name, fb_url, api_token}`, one entry per array, name as in the inventory; injected as the extra_var `purefb_fleet_json`, from which `playbooks/tasks/resolve_array.yml` picks the array a launch names |
| Credential type | `FlashBlade API` | inputs `fb_url`, `api_token` (secret), `array_name`; injected as the extra_vars `fb_url`, `api_token` and `credential_array`; the playbooks refuse to act on an array other than `credential_array` |
| Credential type | `FlashBlade object store` | inputs `array_name`, `fb_url`, `api_token` (secret), `endpoint`, `access_key`, `secret_key` (secret), `state_access_key`, `state_secret_key` (secret); injected as the environment variables `FB_OBJ_URL`, `FB_OBJ_TOKEN`, `FB_OBJ_S3_ENDPOINT`, `FB_OBJ_S3_ACCESS_KEY`, `FB_OBJ_S3_SECRET_KEY`, `FB_OBJ_S3_STATE_ACCESS_KEY` and `FB_OBJ_S3_STATE_SECRET_KEY` ("The object store credential") |
| Credential type | `Orchestrator service account` | inputs `orchestrator_url`, `client_id`, `client_secret` (secret); injected as the environment variables `AO_CLIENT_ID`, `AO_CLIENT_SECRET` and the extra_var `orchestrator_url`; the same type the FlashArray sibling's seed defines, so either seed finds the other's |
| Credential type | `Slack webhook` | one secret input, `webhook_url`; injected as the environment variable `SLACK_WEBHOOK_URL`, which `playbooks/notify.yml` posts to; shared with the FlashArray sibling the same way |
| Credential | `FlashBlade fleet - <organization>` | every entry of `aap_arrays`, values from the named environment variables; attached to every job template |
| Credentials | `FlashBlade - <array>` | one per entry in `aap_arrays`; for launches that pin one array (`credential_names` on an orchestrator node, or the credential prompt) |
| Credentials | `FlashBlade object store - <array>` | one per entry in `aap_object_stores`; the first entry's is attached to the S3 templates and to every activation, the others serve the credential prompt and an activation copy |
| Credential | `Orchestrator service account - flashblade-eda` | only when `AO_CLIENT_ID` is set |
| Credential | `Slack webhook - flashblade-eda` | only when `SLACK_WEBHOOK_URL` is set; attached to `FlashBlade - notify` |
| Credential | `Source control - flashblade-eda` | only when the variable `aap_scm_token_env` names (`GITHUB_TOKEN`) is set: the built-in `Source Control` type with `aap_scm_username` and the token as the password, attached to the controller project; its EDA twin (the EDA controller's own `Source Control` type) to the EDA project |
| Inventory | `FlashBlades` | `localhost` plus one host per array, named as in `purefb_arrays`; `ansible_connection: local` |
| Project | `FlashBlade EDA` | git, `aap_project_url` on `aap_project_branch`; synced once by the seed |
| Execution environment | `FlashBlade EDA EE` | image from `aap_ee_image` |
| Job templates | `FlashBlade - alert runbook`, `FlashBlade - apply baseline`, `FlashBlade - auditor guard`, `FlashBlade - canary`, `FlashBlade - capacity reclaim`, `FlashBlade - capacity request`, `FlashBlade - client diagnose`, `FlashBlade - DR drill`, `FlashBlade - flag alert`, `FlashBlade - grow file system`, `FlashBlade - identify LED`, `FlashBlade - landing`, `FlashBlade - lock on arrival`, `FlashBlade - maintenance window`, `FlashBlade - notify`, `FlashBlade - orchestrator post`, `FlashBlade - posture revert`, `FlashBlade - QoS cap`, `FlashBlade - QoS lift`, `FlashBlade - quarantine client`, `FlashBlade - reconcile`, `FlashBlade - release locks`, `FlashBlade - remediate DNS`, `FlashBlade - remediate NTP`, `FlashBlade - renew certificate`, `FlashBlade - replication resume`, `FlashBlade - S3 anomaly`, `FlashBlade - S3 approval`, `FlashBlade - selftest`, `FlashBlade - session check`, `FlashBlade - showback`, `FlashBlade - tarpit`, `FlashBlade - telemetry check`, `FlashBlade - telemetry setup`, `FlashBlade - user quota` | one per playbook; the fleet credential attached; prompt for extra vars, credentials and a limit; `alert_log`, `request_dir`, `baseline_dir` from `aap_job_extra_vars` and `fb_api_version` from the rulebook vars; apply baseline is limited to `localhost` plus `aap_default_array` (one launch applies one array's baseline); the S3-side templates (auditor guard, canary, capacity request, DR drill, grow file system, landing, lock on arrival, S3 anomaly, S3 approval, selftest, showback) carry `object_store` and the object store credential; the DR drill carries `aap_dr_drills` as `dr_drills` with `dr_drill_hour`, `dr_drill_discard` and `showback_prefix`; showback carries `showback_bucket`, `showback_prefix`, `showback_enabled`; selftest carries `selftest_checks`, `audit_baseline`, `prometheus_url` and the telemetry names; showback and selftest carry `target_array` as every array, comma-joined, the way the standalone tick launches them; telemetry check and setup carry the `telemetry_*` keys and `prometheus_url`; quarantine client, tarpit and release locks carry `contain_exclude_clients`, `contain_exclude_file_systems` and `tarpit_ops_per_sec`; notify also carries the Slack webhook credential when seeded; the orchestrator post the service account |
| Workflow job template | `FlashBlade - capacity reclaim (controller tier)` | request -> approval -> reclaim; notify on failure or denial |
| Notification template | `Slack - flashblade-eda` | only when `SLACK_BOT_TOKEN` and `SLACK_CHANNEL` are set: type Slack, posts every job template's failures and the workflow's success, failure and approval events (pending, approved, denied, timed out) to the channel, each with its link into the platform |
| Schedules | `FlashBlade - DR drill (nightly)`, `FlashBlade - showback (daily)`, `FlashBlade - selftest (daily)` | daily iCal rules from `aap_schedules` in `aap_schedule_timezone`; the drill schedule passes `dr_drill_force: false` ("Schedules") |
| EDA decision environment | `FlashBlade EDA DE` | image from `aap_eda_de_image`, pulled with `aap_eda_de_pull_policy`: the image `aap/decision-environment.yml` or `make de` builds, pushed to a registry or loaded into the store the activation workers use ("Images on a containerized platform") |
| EDA project | `FlashBlade EDA` | same repository and branch; the source control credential when seeded |
| EDA credential types and credentials | `FlashBlade fleet`, `FlashBlade API`, `FlashBlade object store`, `Orchestrator service account`; `FlashBlade fleet - <organization>`, `FlashBlade - <array>`, `FlashBlade object store - <array>`, `Orchestrator service account - flashblade-eda` | same inputs as the controller types; injected as variables (the fleet list as `purefb_arrays`) or, for the object store, as the same environment variables; `Platform user - flashblade-eda` (the built-in `Red Hat Ansible Automation Platform` type, the user the activations launch job templates as, host `<gateway>/api/controller/`; `aap_eda_platform_*`) |
| EDA rulebook activations | `FlashBlade rulebook - <name>` | one per file in `extensions/eda/rulebooks/platform/`, with the fleet credential, the `Platform user - flashblade-eda` credential (the one the `run_job_template` actions launch as), the first object store credential and, when it exists, the orchestrator credential attached; disabled unless the site vars say otherwise (see below) |

The names in the job template column are fixed: the orchestrator workflow
definitions and the platform rulebooks' `run_job_template` actions refer
to them, `orchestrator/job_templates.txt` lists the ones a definition or a
platform rulebook launches, and `tests/unit/test_playbook_artifacts.py`
checks that `seed.yml` carries them all. `FlashBlade - orchestrator post`
runs `playbooks/orchestrator_post.yml`; it is created only when the
orchestrator credential exists.

## How the array credentials reach the playbooks

The playbooks address an array as `fb_url` and `api_token` (host variables
in the standalone inventory, which reads them from the environment). On the
platform the seed provides those through credentials; the inventory holds
names only.

**The fleet credential.** The `FlashBlade fleet` type carries every array
in one secret input (a JSON list of `{name, fb_url, api_token}`, built by
the seed from the environment variables `aap_arrays` names) and injects it
as the extra_var `purefb_fleet_json`. Every array-facing playbook starts
with `playbooks/tasks/resolve_array.yml`, which leaves `fb_url` and
`api_token` alone when they are already set, and otherwise picks the entry
whose `name` is the array the launch names (`target_array`,
`at_risk_array`, the request's array, a drill's `peer`) and sets `fb_url`,
`api_token` and `credential_array` from it. The job template is seeded with
this credential, so one template, one workflow copy and one orchestrator
definition serve every array: the launch variables say which array, the
credential resolves it. The token list is stored encrypted in the
platform's credential store, is visible to nobody, and is rotated by
re-running the seed with `-e aap_update_secrets=true`. `apply_baseline`,
whose play spans every array, resolves each host's own entry and so
applies every host in the limit; its template is seeded with a limit of
`localhost` (the play that refreshes the checkout) plus `aap_default_array`,
and every template prompts for the limit. `selftest` and `showback` fan out
the same way: their templates carry `target_array` as every array of
`aap_arrays`, comma-joined (an Ansible host pattern), so one scheduled run
covers the fleet, and a launch may narrow it to one array.

**One array per launch.** The `FlashBlade API` type injects one array's
pair as the extra_vars `fb_url`, `api_token` and `credential_array`, which
override host variables, so a job given that credential runs against that
array whatever host it names. The seed keeps one such credential per array
for launches that pin an array: an orchestrator node's `credential_names`,
a workflow node's credential list, a person in the credential prompt. Every
array-facing playbook refuses to run when `credential_array` is not the
array it was asked to act on, so a mismatch fails the job before any array
call; outside the platform `credential_array` is undefined and the check is
skipped. With a single-array credential attached, `apply_baseline` applies
only that array and ends the other hosts instead of failing.

**Not chosen: host variables.** Putting `fb_url` and `api_token` on each
host of the `FlashBlades` inventory would also let one job span every
array, but inventory variables are stored and displayed in clear to anyone
with read access to the inventory, and a token in an inventory is outside
the credential store's rotation and audit. The fleet credential keeps the
one-job-many-arrays property inside the credential store.

## The object store credential

The object-audit source and the S3 playbooks (`playbooks/tasks/s3_object.yml`)
read an array's S3 endpoint and keys from environment variables whose
names the rulebook vars give: `object_audit_arrays` for the source,
`object_store` for the playbooks (`vars/example.yml` names the `FB_A_S3_*`
variables of `env.example`). On the platform nothing sets those variables
in a job or an activation, so the `FlashBlade object store` type carries
one array's address, token, endpoint, tail key and state key and injects
them as seven environment variables with fixed names: `FB_OBJ_URL`,
`FB_OBJ_TOKEN`, `FB_OBJ_S3_ENDPOINT`, `FB_OBJ_S3_ACCESS_KEY`,
`FB_OBJ_S3_SECRET_KEY`, `FB_OBJ_S3_STATE_ACCESS_KEY` and
`FB_OBJ_S3_STATE_SECRET_KEY`. The rulebook vars file a site hands the seed
(`FBEDA_VARS`) names those seven instead of the `FB_A_S3_*` ones:

```yaml
object_audit_arrays:
  - name: fb-a
    fb_url_env: FB_OBJ_URL
    api_token_env: FB_OBJ_TOKEN
    s3_endpoint_env: FB_OBJ_S3_ENDPOINT
    s3_access_key_env: FB_OBJ_S3_ACCESS_KEY
    s3_secret_key_env: FB_OBJ_S3_SECRET_KEY
    log_bucket: eda-log
    state_bucket: eda-state
    state_access_key_env: FB_OBJ_S3_STATE_ACCESS_KEY
    state_secret_key_env: FB_OBJ_S3_STATE_SECRET_KEY
object_store:
  fb-a:
    endpoint_env: FB_OBJ_S3_ENDPOINT
    access_key_env: FB_OBJ_S3_ACCESS_KEY
    secret_key_env: FB_OBJ_S3_SECRET_KEY
    state_access_key_env: FB_OBJ_S3_STATE_ACCESS_KEY
    state_secret_key_env: FB_OBJ_S3_STATE_SECRET_KEY
    log_bucket: eda-log
    state_bucket: eda-state
```

The seed creates one `FlashBlade object store - <array>` credential per
entry of `aap_object_stores` (controller and EDA), from the environment
variables the entry names, and attaches the first entry's to the templates
whose playbooks touch the object store and to every activation. Because the
seven names are fixed, one credential serves one array per job or
activation: a second credential of the type would set the same variables
again, and the controller refuses two credentials of one type on a job
template in any case. A site with a second object-store array keeps its
credential for the credential prompt (a launch swaps the first array's for
the second's and names that array in `target_array`) and, for its audit
log, runs one object-audit activation per array: a copy of
`FlashBlade rulebook - object_audit` made in the UI or through the API with
that array's credential and a vars file whose `object_audit_arrays` and
`object_store` name it. The remaining extra vars are the same, so the two
activations differ in one credential and one array name.

## Images on a containerized platform

On a containerized Ansible Automation Platform the controller's task
container keeps its own container image store, separate from the host
user's, and the Event-Driven Ansible activation workers start activations
through the host user's podman service (the workers' `CONTAINER_HOST` is
that user's podman socket; the worker image carries no podman of its own),
which the installer runs with a storage configuration of its own. An image
that exists only in the host user's command-line store (for example
`localhost/flashblade-eda-ee` built on the same machine) fails at job or
activation start with "pinging container registry localhost", because a
`localhost/` name that the store lacks is pulled from a registry called
localhost. Either push the image to a registry the platform can pull from
(the private automation hub counts) and reference it by that name, or load
it into the stores that need it:

    podman build -f aap/Containerfile.ee -t localhost/flashblade-eda-ee:latest .
    podman save localhost/flashblade-eda-ee:latest \
      | podman exec -i automation-controller-task podman load
    podman build -t localhost/flashblade-eda-de:latest -f decision-environment/Containerfile .
    podman save localhost/flashblade-eda-de:latest | podman --remote load

The second load goes through the socket the activation workers use. Set
`aap_eda_de_pull_policy: never` for an image loaded that way, so that the
worker never tries to pull the `localhost/` name, and `aap_ee_pull:
missing` for the execution environment.

## Paths inside the execution environment

A job runs in a fresh container. `aap_job_extra_vars` points `alert_log`,
`request_dir` and `baseline_dir` at `/tmp`, which exists, is writable and
vanishes with the job. The durable record of a platform job is its output
and its artifacts (the `set_stats` at the end of every playbook, shown on
the job and passed to the next workflow node); the object store keeps the
rest: the requests, receipts, legal holds, ledgers and drill markers the
S3 playbooks write to the array's state bucket survive the job. Two things
still need a path that outlives a job:

- the controller-tier workflow and the orchestrator's capacity workflow:
  `capacity_reclaim.yml` reads the request file `capacity_request.yml`
  wrote, in a later job. Without a shared path it rebuilds the request from
  the artifacts the launching node passes (`array` or `at_risk_array`, and
  `candidates`), so the reclaim works without one, but the request and its
  outcome then sit in two jobs' outputs rather than in one file (with
  `s3_requests_enabled` the request is also in the log bucket);
- the baseline playbooks: `baseline_dir` is a git checkout.

For those, expose a host directory to the jobs (Settings > Jobs > Paths to
expose to isolated jobs), pin the templates to the instance group whose
nodes have it, and point the three variables there.

## The controller-tier workflow

`FlashBlade - capacity reclaim (controller tier)` is the approval gate the
standalone webhook ruleset stands in for. Launch it by hand with the
variables the capacity rule passes to `capacity_request.yml`
(`at_risk_array`, `used_pct`, `used_bytes`, `capacity_bytes`,
`days_to_full`, `top_growers`, the pending-eradication lists,
`candidate_targets` and `target_array` set to the same array so the notify
records carry it); the platform capacity rulebook does not launch it (it
runs `FlashBlade - capacity request` on its own, or hands the event to the
orchestrator). The variables must have the event's shape: `candidate_targets`
is a list of `{array, used_pct}` records, not a list of names. The request
node's artifacts (`request_id`, `at_risk_array`, `candidate_count`,
`candidates`) are passed to the following nodes as extra vars, which is how
the reclaim node learns `request_id` and, when `request_dir` is not a path
shared by both jobs, rebuilds the request from `at_risk_array` and
`candidates` (see "Paths inside the execution environment"; the reclaim
node passes `rebuild_from_artifacts: true`, without which the playbook
refuses a missing request file). Launch permission on `FlashBlade -
capacity reclaim` is permission to eradicate already-destroyed file
systems, buckets and snapshots (the reclaim rebuilds its request from the
`candidates` it is given when no request file exists); grant it to the
workflow's and the orchestrator's identity only, and give people the
workflow, not the template. The approval node waits `aap_approval_timeout_s`
seconds for an approver; its denial or timeout takes the failure edge to a
warning notify, and a failed job takes it to a critical one. The reclaim
node runs with `reclaim_action: eradicate_pending` and records the approver
as `workflow-approval`; the platform's activity stream holds who approved.
The request and reclaim nodes run their templates with the fleet credential
the templates carry, and `at_risk_array` picks the array, so one copy of
the workflow serves every array.

## Schedules

The standalone housekeeping rulebook runs the selftest, the showback
ledger and the DR drills off an hourly tick; `tools/platform_rulebooks.py`
drops that tick-only ruleset, so on the platform the three are controller
schedules on their templates (`ansible.controller.schedule`; daily iCal
rules built from `aap_schedules`, times a quoted `HH:MM` in
`aap_schedule_timezone`; the seed refuses an unquoted time, which YAML
reads as a number; a schedule with `enabled: false` is created and never
fires). A schedule launches its template with the template's extra vars
plus its own extra data, and a failed job is reported by the template's
failure notification (`Slack - flashblade-eda` when the bot token is
seeded), so a failed drill or a failed check reaches the channel with its
link into the platform.

- **`FlashBlade - DR drill (nightly)`** runs `FlashBlade - DR drill` on
  `aap_dr_drills` with `dr_drill_force: false`: the playbook's own hour
  gate stays on, so with the UTC zone the schedule's hour must be the
  `dr_drill_hour` of the rulebook vars file, and the seed refuses the pair
  when they differ (another zone is the site's own arithmetic). Each drill
  promotes the replica on the peer, reads it and demotes it again; the
  marker in the source array's state bucket (`<showback_prefix>dr/<date>-<file_system>.json`,
  written with the object store credential) stops a second run the same
  day, and a drill that found no link, could not promote or could not
  demote is a critical record and counts in `failed_count`. The
  orchestrator's `dr_drill_schedule.json` runs the same drill from the
  orchestrator with `dr_drill_force: true` and a notify on failure; a site
  with both keeps one and disables the other.
- **`FlashBlade - showback (daily)`** runs `FlashBlade - showback` for
  every array (`target_array` on the template): one ledger per array to its
  state bucket, `<showback_prefix><date>/<array>.json`, with what every file
  system, bucket and object store account occupies; a ledger that already
  exists for the day is left alone. An array without an `object_store`
  entry gets the totals in its record and no object.
- **`FlashBlade - selftest (daily)`** runs `FlashBlade - selftest` for
  every array: REST answers, the state bucket answers with the state key,
  the audit baseline policy is enabled with members and filters, the
  telemetry target exists and is enabled, the Prometheus receiver is ready
  (`selftest_checks` picks them; the telemetry and Prometheus checks skip
  while `telemetry_endpoint` and `prometheus_url` are empty). One record
  per array, critical when a check failed, and the job fails with it, so a
  broken dependency is found at the scheduled hour instead of during an
  incident.

## The Event-Driven Ansible controller side

The rulebooks an activation runs are the platform rulebooks under
`extensions/eda/rulebooks/platform/`, generated from the standalone ones
by `tools/platform_rulebooks.py` (`python tools/platform_rulebooks.py`;
`tests/unit/test_platform_rulebooks.py` fails when the committed files
drift). A platform rulebook has the standalone rulebook's rulesets,
sources and rules, with three differences: every `run_playbook` is a
`run_job_template` naming the job template above for that playbook, in
the organization `aap_organization`, passing the same extra vars minus
`alert_log`, `request_dir` and `baseline_dir` (the templates carry those);
the project's event sources keep their collection FQCN
(`sdodsley.flashblade_eda.purefb_events`, `purefb_object_audit`,
`purefb_syslog`), which is how an activation resolves them from the
decision environment; and the rulesets whose only source is
`ansible.eda.webhook` (the capacity approvals, the git push, the
maintenance tickets, the containment approvals) or `ansible.eda.tick` (the
housekeeping) are left out, because on the platform those are event streams
with their own credentials, the controller-tier workflow or the schedules
above. `APPROVAL_TOKEN` and `GIT_WEBHOOK_SECRET` are therefore not seeded.
The Alertmanager and syslog listeners keep their ports (`alertmanager_port`,
`syslog_port` in the vars file), which an activation with host networking
binds on the platform host itself; a site that runs the FlashArray sibling
on the same host gives each its own numbers.

The seed creates the decision environment, the project, the four
credential types with their credentials, and one activation per platform
rulebook (`rulebook_name` is the file's path relative to
`extensions/eda/rulebooks`, `platform/<file>`, which is how the controller
lists a rulebook in a subdirectory). Each activation gets:

- as extra vars, the rulebook vars file (`FBEDA_VARS`, else
  `vars/example.yml`) laid over `vars/example.yml`, so a key the site file
  lacks keeps its default, minus `purefb_arrays` and `config_arrays` (the
  config and change-control sources fall back to the fleet list), plus
  `aap_organization`; minus `orchestrator_url` as well when the
  orchestrator credential is attached. The controller refuses an activation
  whose extra vars and credentials define the same key, so what a
  credential injects is removed from the file's content;
- the `FlashBlade fleet - <organization>` EDA credential, whose type
  injects the arrays list as the variable `purefb_arrays`, the list the
  `purefb_events` source polls (its entries carry `fb_url` and `api_token`
  literals, which the plugin accepts next to the `_env` forms), so the
  plugin needs no environment variables and one credential covers the
  fleet;
- the `Platform user - flashblade-eda` credential, which ansible-rulebook
  launches the job templates with;
- the first `FlashBlade object store - <array>` credential, whose
  environment variables the object-audit source and the S3 job templates
  read ("The object store credential");
- the `Orchestrator service account - flashblade-eda` EDA credential when
  `AO_CLIENT_ID` is set, which injects `orchestrator_url`.

The per-array EDA credentials (`FlashBlade - <array>`) are seeded but not
attached: the fleet credential covers every array, and the controller
refuses two credentials of one injecting type on an activation ("Key:
fb_url already exists in extra_vars"); they serve a rulebook copy that
polls one array only.

The activations end up **disabled** by default (`aap_eda_activations_enabled:
false`): `ansible.eda.rulebook_activation` creates an activation enabled
whatever it is told and cannot modify one afterwards, so the seed sets the
flag through the enable and disable endpoints on every activation on every
run, the one thing it can still change, ten seconds apart when enabling
(activations started in the same second contend for the EDA websocket and
some fail their first start). Set it true in the site vars file once the
decision environment image, with the `sdodsley.flashblade_eda` collection
in it, is in the activation workers' store, and keep it there: a later run
with the example default disables every running activation.

Two rules for a busy platform host. Bring the activations up one at a
time: an activation's start opens two websocket connections to the EDA
controller (the workload, then the event stream), a busy controller makes
the gateway answer the second one 502, the start fails at once, and past
the restart policy's limit the activation stays failed; several starting
or retrying together make it worse, since every attempt costs a container
start and more websocket work, so restart (or disable and enable) a failed
one until it reports running, and `tools/activation_audit.py --recreate`
creates one activation at a time for the same reason. And name, in the
rulebook vars' `automation_users`, the user the API tokens in the
credentials belong to: every job the platform runs logs in to the array
as that user, so with the name missing the sessions rulebook sees a human
login for every job and launches a job for each, and the change control
rulebook treats the platform's own writes as human changes.

## Checking the activations

An activation keeps the extra vars it was created with, and the seed
renders them from the site file laid over `vars/example.yml`, so a key the
site file has lost quietly takes the example default, and one of those
defaults is a placeholder an activation would act on (`orchestrator_url`
is `orchestrator.example.com`). Two things stop that. The seed refuses to
render extra vars whose `orchestrator_enabled` is true while
`orchestrator_url` is still the example placeholder (unless the
orchestrator credential injects it), and `tools/activation_audit.py`
compares every `FlashBlade rulebook - *` activation with what the seed
would render today:

```
export AAP_TOKEN=...            # a platform token with EDA read (and write for --recreate)
python tools/activation_audit.py --url https://<gateway> --vars vars/<site>.yml \
    [--organization FlashBlade] [--allow KEY]... [--recreate [--enable]] [--insecure] [--json]
```

It prints one line per activation: `ok`, or the keys whose values differ,
are missing or are extra, plus any value that is an example placeholder
(`example.com`, documentation IP ranges, `REPLACE_AT_`) and any activation
that is disabled or not running. `--allow KEY` accepts a placeholder a site
means (a baseline that really is `example.com`). The exit status is 1 when
anything is wrong; on a platform seeded with the default
`aap_eda_activations_enabled: false` every activation is reported disabled
and the audit exits 1 until they are enabled. `--recreate` deletes each
activation whose vars differ from the expected ones and creates it again
with those vars and the same project, rulebook, decision environment,
credentials, policies and enabled flag (`--enable` creates it enabled
whatever flag the old one carried, for the activations a sweep switched
off), waiting for it to run; the gateway's transient answers (5xx, 403, a
dropped connection on a read) are retried for two minutes, and every
activation is read by its full name (one request per platform rulebook
file; the controller's listing is never asked for, since it takes ten
seconds and a timeout takes the EDA API away from every reader); a
rulebook file without an activation is reported. A placeholder-only,
disabled or stopped
activation is reported, not recreated, and nothing is recreated while the
expected vars themselves
still carry a placeholder (fix the site file first). Each delete and create
is printed as it happens, old id and new id, because the Rule Audit rows
stay under the old id. Run it after the seed and after any change to the
site file.

## After a host reboot

The platform's own units bring it back, and the activation worker restarts
the activations the shutdown ended, but one that had already reached its
restart policy's limit is marked failed and left there.
`tools/activation_restart.py` finishes the job: it waits for the EDA API
(`--wait-minutes`, 15 by default), reads the activation of every platform
rulebook by its full name, leaves the disabled and the running ones alone,
waits for one that is starting, and restarts each other one through its
restart endpoint, one at a time, up to three times (an early start can
meet the websocket service before it is ready); the exit status is 1 when
one does not run. It takes the gateway, the token and the TLS setting from
the same flags and environment as the audit, so a oneshot user unit
ordered after the platform's units (`After=automation-gateway.service
automation-eda-api.service automation-eda-daphne.service
automation-eda-activation-worker-1.service`) runs it at boot, reading the
token from a file at run time rather than carrying it in the unit. It makes
at least two passes, ninety seconds apart: the first reads after the API
comes up can show an activation as running from before the reboot, which
the worker marks failed seconds later. The platform host keeps no user
journal, so the unit's output is read from the system journal with
`journalctl _SYSTEMD_USER_UNIT=<unit>.service` and its result with
`systemctl --user show <unit> -p ExecMainStatus`.

A start is also a load event: the worker gives it sixty seconds to become
ready and stops it otherwise, so after a storm (a reboot, a recreate of
several activations) enable or restart activations one at a time and let
the host settle between them; two activations in restart loops are enough
to keep a small host too busy for either to make it.

## Re-running safely

Run the same command again. Objects are matched by name and only
differences are applied; the two exceptions are `destroy_current_nodes`
on the workflow (off by default; `-e aap_rebuild_workflow_nodes=true`
recreates the nodes, which always reports changed) and secrets, which the
platform cannot read back: credentials are updated only with
`-e aap_update_secrets=true` (controller) and EDA credentials report
changed on every run. `ansible.eda.rulebook_activation` cannot modify an
existing activation; to change one (a new rulebook, a new variable in its
extra vars), delete it in the UI or through the API and re-run, which
recreates only that one; an activation keeps the rulebook and the extra
vars it was created with, so a changed rule or a new vars key reaches it
the same way (a rule that reads a key the activation's copy of the vars
file lacks fails at source start with "is undefined"). A change to an
event source plugin or a module travels further still: the collection is
built into the decision environment and the execution environment, so the
image is rebuilt, loaded into the store that uses it, and the activation
recreated. The run also sets `aap_eda_activations_enabled` on every
FlashBlade activation, so a site whose activations are running keeps the
key true in its vars file. The two projects are synced when the seed
creates them and not on later runs, and the job templates do not update
the project on launch, so after a push sync the controller project (and
the EDA project, before recreating an activation) or a job runs the
revision the project last fetched. A gateway answering 503 or 504 while
the platform is busy fails the one task that hit it; the seed is
idempotent, so the next run finishes the rest. The seed never deletes
anything.

## What it cannot seed

- **The automation orchestrator.** It has no certified collection, and the
  seed does not drive its API: the AAP integration, the service account
  and its client credentials, the EDA triggers with the service account
  bound to them, and the workflow definitions are set up as
  [orchestrator/README.md](../orchestrator/README.md) describes. The seed
  only stores the service account's credentials, once they exist, so that
  `FlashBlade - orchestrator post` and the activations can use them;
  `AO_URL` is the orchestrator as the jobs reach it, which for an
  orchestrator on the platform host of a containerized platform is
  `https://host.containers.internal:<port>` (a job's execution environment
  cannot reach the host's own address).
- **The images.** The execution environment and the decision environment
  are references by name and image; building and pushing them is outside
  the platform.
- **The object store identities.** The tail and state access keys, the
  audit log bucket and the state bucket exist on the array before the
  seed stores the keys (the object-audit setup in the project README).
- **The firewall.** The Alertmanager and syslog activations listen on the
  platform host; opening `alertmanager_port` and `syslog_port` to
  Alertmanager and to the arrays is a host step.
- **The Automation Hub token** the collection install needs, and the
  platform's own subscription.
- **Approvers.** The workflow approval accepts any user with the approve
  permission on the workflow; granting that role is a platform RBAC step.
- **The Slack app.** The incoming webhook, the bot token and the Socket
  Mode token are created at api.slack.com for the channel
  ([slack/README.md](../slack/README.md)); the seed stores the webhook and
  the bot token once they exist, and the approval bot runs beside the
  platform, not on it.
