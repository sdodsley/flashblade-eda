# Orchestrator workflow definitions

Workflow definitions for Red Hat's automation orchestrator (the Ansible
Automation Platform 2.7 add-on; upstream project Syntara), in the JSON the
orchestrator imports and exports (`schema_version` 2.0.0: `triggers`,
`nodes`, `edges`). They do not replace the rulebooks: `ansible-rulebook`
still decides what an array event means. What the orchestrator adds is the
part a rulebook cannot do well, a human approval that pauses the run, a
schedule with a missed-run policy, and an audit record of who decided what.
Every node that touches an array is still one of this project's playbooks,
launched as an AAP job template.

Every definition follows the node schemas of the upstream orchestrator code
at the tag Red Hat ships as the 2026.8 release (API 1.3.1), and
`tools/ao_lint.py` checks them offline. The capacity route and the day-2
file system growth are what the lab exit tests of
[docs/VERIFICATION.md](../docs/VERIFICATION.md) ("Lab exit tests for part
4") run on a live instance, from the EDA trigger or the API through the
approval to the job; that document holds the results. The product add-on is
generally available for OpenShift-based Ansible Automation Platform 2.7
only; no timeline is published for the containerized topology, and its API
(1.3.1) is described as a technology preview that may change
([docs/ORCHESTRATOR.md](../docs/ORCHESTRATOR.md)).

## The definitions

| File | Starts from | What it does |
| --- | --- | --- |
| `capacity_reclaim_approval.json` | `eda_trigger` on webhook path `fb_capacity` | A `purefb_capacity` event forwarded by the capacity rulebook runs **FlashBlade - capacity request** (the request file and alert-log record of `playbooks/capacity_request.yml`). If the request lists reclaim candidates (file systems, buckets and file system snapshots already pending eradication), **FlashBlade - notify** announces it, an approval node waits up to 24 hours, and an approval runs **FlashBlade - capacity reclaim** on exactly the candidates the request listed; a rejection runs a separate notify node. No candidates: a notify node says so. Nothing is freed without the approval. |
| `replication_paused_approval.json` | `eda_trigger` on webhook path `fb_replication` | A `purefb_replica_link` event forwarded by the replication rulebook (a paused bucket replica link, with `orchestrator_enabled` on): **FlashBlade - notify** announces it, an approval node waits up to one hour, and an approval runs **FlashBlade - replication resume** (`paused` cleared on the link itself; its target and credential are untouched), then a notify with the job URL. A rejection notifies and leaves the link paused. |
| `day2_grow_fs.json` | `manual_trigger` with an input form (array, file system, new size) | Notify, approval (24 hours), then **FlashBlade - grow file system** with the three inputs and the requested array's credential, then notify with the sizes before and after and the AAP job URL. A rejection notifies and changes nothing. |
| `day2_identify_led.json` | `manual_trigger` with an input form (array, component, LED on or off) | Approval (24 hours), then **FlashBlade - identify LED** with the requested array's credential sets the component's identify LED, then an info notify reports the state with the AAP job URL; the hardware record in the alert log says whether the LED changed and names a component without an identify LED. A rejection notifies and changes nothing. |
| `day2_qos_cap.json` | `manual_trigger` with an input form (array, file system or bucket, name, operations per second) | Notify, approval (24 hours), then **FlashBlade - QoS cap** with the requested array's credential creates the policy `eda-cap-<object>` when needed and assigns it, then a warning notify with the policy and the AAP job URL. The client rulebook lifts the cap once the object has been normal for `qos_lift_normal_polls` polls (`playbooks/qos_lift.yml` removes only `eda-cap-` policies). A rejection notifies and changes nothing. |
| `day2_quarantine_client.json` | `manual_trigger` with an input form (array, client address, file system or empty, quarantine or release) | Approval (24 hours), then **FlashBlade - quarantine client** with the requested array's credential rewrites the client's NFS export entry `rw -> ro` or inserts a read-only export policy rule (a release reverses it), then a warning notify with the file systems changed and the AAP job URL. Never a block: the client keeps reading. A rejection notifies and changes nothing. |
| `dr_drill_schedule.json` | `scheduled_trigger`, cron `0 3 * * *` (UTC), missed runs skipped | Every night **FlashBlade - DR drill** promotes the replica of each configured file system on its peer, reads it and demotes it again (`dr_drill_force: true`, so the playbook does not wait for `dr_drill_hour`); a condition on `failed_count` sends a critical **FlashBlade - notify** when any drill failed and a one-line summary otherwise. Publishing the definition creates the schedule. Without the orchestrator, the seed's controller schedule `FlashBlade - DR drill (nightly)` runs the same drill ([aap/README.md](../aap/README.md), "Schedules"); a site keeps one of the two. |

Approval nodes always have two distinct successors: `approved` and
`rejected` never share a node (the linter enforces it), because a shared
successor would run on either decision.

The day-2 definitions have one rule in common, which
`tests/unit/test_ao_lint.py` checks: the form declares and requires exactly
the inputs the job node reads, every input is passed whole (`${trigger.x}`
on its own keeps an integer's type), the node carries `credential_names`
for the requested array, the job runs only on the approval's `approved`
port, and nothing site-specific (`alert_log`, `request_dir`,
`fb_api_version`, `object_store`) is in the node.

## The event envelope

The capacity and replication definitions expect what the EDA bridge
(`playbooks/orchestrator_post.yml`, run by the capacity and replication
rulebooks when `orchestrator_enabled` is true; the paths are
`orchestrator_paths.capacity` and `orchestrator_paths.replication` in the
vars file and the contract is in `docs/ORCHESTRATOR.md`) sends to
`POST {orchestrator_url}/api/v1/webhooks/eda/<path>` with a service
account bearer token; for the capacity route:

```json
{"event": {"schema": "flashblade-eda/1", "event_type": "purefb_capacity",
           "array": "fb-a", "maintenance_active": false,
           "observed_at": "2026-01-05T02:00:00Z",
           "data": {"used_pct": 91.2, "used_bytes": 1, "capacity_bytes": 1,
                    "growth_bytes_per_day": 1, "days_to_full": 12,
                    "days_to_full_known": true, "top_growers": [], "others": [],
                    "pending_eradication_file_systems": 1,
                    "pending_eradication_buckets": 0,
                    "pending_eradication_snapshots": 3}}}
```

The orchestrator passes the body through unwrapped (the trigger's output is
the request body itself, and the trigger's id is an alias of `trigger`), so
the nodes read `${trigger.event.array}` and `${trigger.event.data.<field>}`;
`data` carries the `purefb_capacity` event fields unchanged. The trigger's
`input_schema` (JSON Schema draft-07) requires the envelope keys, the
route's event type and the array-name pattern the playbooks assert on. The
replication route carries a `purefb_replica_link` event the same way; its
nodes read `data.local` (the bucket), `data.remote`, `data.remote_object`,
`data.status` and `data.recovery_point_age_s`.

## Job templates and what they must carry

`job_templates.txt` lists every template name the definitions and the
platform rulebooks (`extensions/eda/rulebooks/platform/`, generated by
`tools/platform_rulebooks.py`) launch; `make lint-ao` refuses a definition
that names anything else, and the unit tests check that every listed name
is used by one or the other and is one of the fixed template names (one per
playbook, the table in aap/README.md). The nodes pass only what comes from
the event, the form or an earlier node; everything site-specific is set on
the job template itself (extra vars or a survey), not in these files:

| Template | Playbook | Passed by the node | Set on the template |
| --- | --- | --- | --- |
| FlashBlade - capacity request | `playbooks/capacity_request.yml` | `at_risk_array`, `used_pct`, `used_bytes`, `capacity_bytes`, `days_to_full`, `growth_bytes_per_day`, `top_growers`, `pending_eradication_file_systems`, `pending_eradication_buckets`, `pending_eradication_snapshots`, `candidate_targets` (the event's `others`) | `request_dir`, `alert_log`; `object_store`, `s3_requests_enabled` and `request_prefix` when the request should also go to the log bucket |
| FlashBlade - capacity reclaim | `playbooks/capacity_reclaim.yml` | `request_id`, `array`, `candidates`, `rebuild_from_artifacts` (`true`), `reclaim_action` (`eradicate_pending`), `approver` | `request_dir` (persistent, same path as the request template), `alert_log` |
| FlashBlade - notify | `playbooks/notify.yml` | `severity`, `subject`, `detail`, `target_array` | `alert_log`; the Slack webhook credential when notifications should reach Slack |
| FlashBlade - replication resume | `playbooks/replication_resume.yml` | `target_array`, `bucket` (the link's local bucket), `reason` | `fb_api_version`, `alert_log` |
| FlashBlade - grow file system | `playbooks/grow_fs.yml` | `target_array`, `file_system`, `new_size` | `alert_log`; `fs_grow_exclude` for the names the site never grows by automation (the rulebook's step, ceiling and request keys do not apply to a launch with `new_size`) |
| FlashBlade - identify LED | `playbooks/identify_led.yml` | `target_array`, `component`, `led_state` (`on` or `off`), `component_details`, `severity` (`info`) | `alert_log` |
| FlashBlade - QoS cap | `playbooks/qos_cap.yml` | `target_array`, `object_kind`, `object_name`, `cap_ops_per_sec`, `reason` | `alert_log` (the QoS requests ask for REST 2.17 themselves) |
| FlashBlade - quarantine client | `playbooks/quarantine_client.yml` | `target_array`, `client`, `file_system` (may be empty), `action`, `reason` | `contain_exclude_clients`, `contain_exclude_file_systems`, `fb_api_version`, `alert_log` |
| FlashBlade - DR drill | `playbooks/dr_drill.yml` | `dr_drill_force` (`true`) | `dr_drills`, `dr_drill_hour`, `dr_drill_discard`, `showback_prefix`, `fb_api_version`, `alert_log`; `object_store` for the once-a-day marker in the source array's state bucket |

`request_dir` should be a path that outlives a job and is shared by the
request and reclaim templates (a host directory exposed to isolated jobs
and the same instance group, see aap/README.md, "Paths inside the
execution environment"); the execution environment's /tmp that
`aap/vars/example.yml` defaults to is discarded when the request job ends,
up to 24 hours before the reclaim job starts. The reclaim node therefore
also passes `array` and `candidates` (the request job's artifacts), and
`capacity_reclaim.yml` rebuilds the request from them when the file is not
there, so the approval still eradicates exactly what the request listed; a
shared `request_dir` keeps the request and its outcome in one file. The
rebuild happens only when the launch also carries `rebuild_from_artifacts:
true`, as the node does; a launch without it is refused when the request
file is absent. Because the rebuild lets a launch name its own candidates,
execute on **FlashBlade - capacity reclaim** must be held only by the
controller-tier workflow and the orchestrator's AAP credential, never by
people: a direct launch of that template with execute rights is the right
to eradicate any object already pending eradication.

The templates need the inventory of this project (one host per array) and
array credentials. `aap/seed.yml` attaches the **FlashBlade fleet**
credential to every template (one JSON list of arrays, from which
`playbooks/tasks/resolve_array.yml` picks the target array's `fb_url` and
`api_token` at run time), and also creates one **FlashBlade API**
credential per array (`FlashBlade - <array>`, injecting `fb_url`,
`api_token` and `credential_array`). The two capacity nodes and the resume
node set `credential_names` to `FlashBlade - ${trigger.event.array}`, the
event's own array, and the day-2 job nodes to `FlashBlade -
${trigger.array}`, the array the form asked for (the node resolves the
names in the organization at launch): a per-array credential on the launch
takes precedence over the fleet, and the playbooks refuse to run when
`credential_array` is not the array they were asked to act on. Only the
DR-drill node, which acts on every drill's peer, uses what its template
carries. Pick a specific array's credential on a node the same way.

The condition nodes route on job artifacts, which the playbooks publish
with `set_stats` so the orchestrator sees them as
`${<node_id>.artifacts.*}`. The definitions read only the keys
`tests/unit/test_playbook_artifacts.py` documents (`ARTIFACT_KEYS`, the
set every workflow may rely on), and `tests/unit/test_ao_lint.py` checks
every reference against that list and against the playbook's `set_stats`:

- **FlashBlade - capacity request** publishes `request_id`,
  `at_risk_array`, `candidate_count` (file systems, buckets and snapshots
  pending eradication) and `candidates`, the request file's own
  `{file_systems, buckets, snapshots}` dict in full. The reclaim playbook
  reads the request file by `request_id` when it is there and rebuilds it
  from `array` and `candidates` when it is not (see above).
- **FlashBlade - capacity reclaim** publishes `request_id`, `array`,
  `rebuilt_from`, `eradicated`, `refused` and `result` (`done`, `partial`
  or `refused`).
- **FlashBlade - DR drill** publishes `drills` (one record per drill),
  `failed_count`, `due` and `date`.
- **FlashBlade - grow file system** publishes `array`, `file_system`,
  `old_size` and `new_size` (bytes), `outcome` and `changed`.
- **FlashBlade - QoS cap** publishes `array`, `object_kind`, `object_name`,
  `policy`, `capped`, `previous_policy` and `changed`.
- **FlashBlade - quarantine client** publishes `array`, `client`, `action`,
  `file_systems`, `changed_count`, `outcome` and `changed`.
- **FlashBlade - identify LED** publishes `array`, `component`, `led_state`
  (`on` or `off`), `led_changed`, `led_error`, `neighbor_switch` and
  `neighbor_port`.
- **FlashBlade - replication resume** publishes `array`, `bucket`,
  `remote`, `resumed`, `outcome` and `changed`.
- **FlashBlade - notify** publishes `record_kind`, `severity` and `array`.

`${<node_id>.job_url}` is the orchestrator's own output for an AAP node
(the job's URL in automation controller) and is what the notify nodes
link to.

## Placeholders

Per-environment identifiers never appear in this directory. The string
`REPLACE_AT_SYNC` marks every one of them and is substituted when the
definition is imported:

| Placeholder | Where | Replaced by |
| --- | --- | --- |
| `credential_id` | every `aap_job_template` (and `aap_workflow_job_template`) node | the orchestrator's AAP credential (the integration's token or user) |
| `authorized_service_account_ids` | the `eda_trigger` | the service account the EDA bridge authenticates as |

`organization_name` is `FlashBlade`, the organization `aap/seed.yml`
creates (`aap_organization`); change both together if the templates live
elsewhere. Approvers are named, not identified: `approver_groups` (and
`approver_users`) take group and user names, so they are not placeholders.
The shipped definitions name two groups: `admins`, the built-in group every
instance has, and `approvers`, a group to create for the people who decide
and for the Slack bot's orchestrator user; the orchestrator resolves the
names when it creates an approval, so a named group must exist by then,
and a service account cannot be an approver, only a user. Change the names
to the groups that should decide. `decision_window` is 86400 seconds (24
hours), 3600 for the paused-link route; an approval nobody decides within
it expires. `fallback_decision` (`approve` or `reject`) names the port an
expired approval takes; the shipped definitions leave it unset, so an
approval nobody decides within the window is recorded as expired (decided
by the orchestrator's worker, note "Approval decision window expired") and
the execution fails at the approval node with neither port taken, which is
the loud outcome wanted for an action nobody asked for. `fallback_decision`
is accepted by the schema and ignored by the engine of this release (an
expired approval takes neither port whatever it says), so the field stays
out of the shipped definitions.

## Importing

The orchestrator stores a workflow as numbered versions; webhook, EDA and
scheduled runs use the published one, so an import is a create followed by
a publish, and a change is a publish that carries the new definition, which
the orchestrator stores as the next version (a PATCH on a version changes
only its name and description). `tools/ao_sync.py` does all of that for
every definition in this directory (standard library only, Python 3.9+):

```
export AO_CLIENT_ID=nx_sa_... AO_CLIENT_SECRET=...
python tools/ao_sync.py --url https://orchestrator.example.com \
    --aap-credential "AAP controller" --aap-integration aap-example \
    --service-account eda-bridge [--project default] [--dry-run | --check] [--insecure] \
    [--only FILE]... [orchestrator/*.json]
```

It authenticates as the service account in `AO_CLIENT_ID` and
`AO_CLIENT_SECRET` (`POST /api/v1/auth/token`, `grant_type=client_credentials`,
15 minute lifetime, not refreshable; read from the environment only, never
from the command line, never printed or written to disk), resolves the
project (`--project`, default `default`) and the names that fill the
placeholders (`--aap-credential` and `--aap-integration` go on every
`aap_job_template` and `aap_workflow_job_template` node whose `credential_id`
is `REPLACE_AT_SYNC`, as `credential_id` and `integration_id`;
`--service-account` fills the EDA trigger's `authorized_service_account_ids`),
and stops before writing anything when the token, the project or a name does
not resolve; a placeholder still left after substitution fails its
definition. Each definition is then validated by the orchestrator
(`POST /api/v1/workflows/validate`; an invalid one is reported with its
findings and not imported), created when the project has no workflow of its
name (`POST /api/v1/workflows`), updated when the exported current version
differs (`POST /api/v1/workflows/{id}/versions/{version}/publish` with the
new definition and `expected_version` in the body, which the orchestrator
stores and publishes as the next version; no PATCH is sent), left alone
when it is equal, and published (the same call with an empty body) after a
create or when the current version has never been published. It prints
one line per definition (`created`, `updated` or `unchanged`, and the
published version) and `extra: <name>` for each workflow of the project that
no file defines; `--dry-run` validates and reports the decisions without
writing; `--check` does the same and exits 1 when any definition would be
created, updated or published, so a CI job or a schedule can say that the
live orchestrator has drifted from the files; `--only FILE`, with every file
still on the command line, writes one definition without making the others
extras; the exit status is 1 when anything failed. Publishing a scheduled
trigger is what creates its schedule; unpublishing deletes it.
`GET /api/v1/workflows/{id}/versions/{version}/export` and the UI's export
button return the same JSON this directory holds, so a change made on the
canvas can be exported and committed here.

A sync from this directory **never deletes**: it creates the workflows
that are missing, updates by name the ones that exist, substitutes the
placeholders from per-environment variables that are not committed, and
leaves anything else in the orchestrator alone (reporting it, not
removing it). Removing a workflow is a deliberate action in the
orchestrator by a human, not a side effect of a file disappearing.

Before every import run the linter; it is what `make check` runs:

```
python tools/ao_lint.py orchestrator/*.json --allowed-templates orchestrator/job_templates.txt
```

It needs nothing but the standard library and prints one line per problem
as `file:node:rule:message`. The rules: `schema_version` is `2.0.0`; the
top-level keys are present; ids match `^[a-zA-Z_][a-zA-Z0-9_]*$` and are
unique across triggers and nodes; only the known trigger and node types;
every edge has `from`/`to` (plus `from_port`, `to_port`) naming existing
ids and never ending at a trigger; every trigger and node of a type with a
known parameter schema carries only the parameters that schema lists
(`parameter_keys`: the orchestrator refuses an unknown key), a scheduled
trigger has `schedule_type` and the matching `cron` or `interval`, a webhook
or EDA trigger its `webhook_path` and `authorized_service_account_ids`; an
approval node lists approvers and has `approved` and `rejected` edges to
different nodes; a condition node has an expression and `true` and `false`
edges; a switch has a `default_port` and complete cases; a loop node has
`iterate` and `complete` edges and its body returns to it over an edge with
`to_port: iterate`, the one back edge allowed (`loop_ports`); a converge
node has at least two incoming edges and any other node at most one, a
loop's return aside; no other cycle; at most 200 nodes, 500 edges and 1.5
MB; every `aap_job_template` or `aap_workflow_job_template` node has
`organization_name`, a `credential_id` and its template
(`job_template_name` or `job_template_id`; `workflow_job_template_name` or
`workflow_job_template_id`), a name being one from the allowed list when
that is given; every agentic node has `credential_id` and `prompt`; no
`http_request` to a bare IP address; every `${name...}` expression names a
node, a trigger id (an alias of `trigger`) or one of the `trigger`,
`workflow_context` and `loop` namespaces, and a node it names runs before
the referencing node (not on a parallel branch or later); every
`${trigger...}` reads what the trigger exposes (`trigger_ref`: a top-level
key of the body for a webhook or EDA trigger and a property of the
`input_schema` for a manual trigger, anything when the trigger declares no
`input_schema`; `scheduled_at` or `triggered_at` for a scheduled trigger);
every node has an incoming edge (`unreachable`); `from_port` appears only
on edges leaving an approval, condition, switch or loop node; and every
condition and switch-case expression, with each `${...}` replaced by a
name, parses into the safe evaluator's operators only (`==`, `!=`, `>`,
`<`, `>=`, `<=`, `and`, `or`, `not`, `in`, names and literals; no `&&`,
arithmetic or function calls). `tests/fixtures/orchestrator/bad_<rule>.json`
holds one failing fixture per rule.

## The orchestrator's contract

Contract facts read from the node schemas and the engine of the upstream
code at the tag Red Hat ships as the 2026.8 release (API 1.3.1); the
sections above follow them:

- A webhook or EDA body is passed through unwrapped: the trigger's output
  is the request body, read as `${trigger.<top-level key>}`, and the
  trigger's id is registered as a second name for the same output.
- Approval nodes take `approver_groups` and `approver_users` (names),
  `prompt`, `decision_window` (seconds) and `fallback_decision`; their
  ports are `approved` and `rejected`, and the decision recorded is one of
  approved, rejected, expired and cancelled.
- Condition ports are `true` and `false`; loop ports `iterate` and
  `complete`.
- An `aap_job_template` node names its job credentials with
  `credential_names` (or `job_credentials` ids); every node schema has
  `additionalProperties: false`, so a key outside the list is refused at
  import (the `parameter_keys` rule). `credential_names` is interpolated
  before the names are resolved, so `FlashBlade - ${trigger.event.array}`
  selects the event array's credential. A full-span `${...}` expression
  keeps its type: a list reaches the playbook as a list, a number as a
  number, a boolean as a boolean.
- A scheduled trigger takes `schedule_type` (`cron` or `interval`), the
  matching `cron` or `interval`, `timezone` (UTC when unset) and
  `missed_schedule_policy`; its output is `scheduled_at` and
  `triggered_at`.
- A workflow's versions are numbered; `PATCH
  /workflows/{id}/versions/{v}` changes a version's name and description
  only, and `POST /workflows/{id}/versions/{v}/publish` with
  `workflow_definition` (and `expected_version`) in the body stores that
  definition as version v+1 and publishes it; with an empty body it
  publishes version v as it stands. The export of a version carries no
  `credential_names`: the orchestrator resolves them when it publishes and
  keeps no copy, and an empty `extra_vars` is dropped as well, so a
  comparison of a file with its export leaves both out (`tools/ao_sync.py`
  does). The export carries a `position` on every node and trigger once
  someone has dragged it on the canvas; the comparison ignores that too, so
  a moved box is not a new version. A node of a published definition that
  reads `${<node>.artifacts.<key>}` fails at run time when the job did not
  publish that key, which validation cannot see.
- An AAP node's result carries `job_id`, `job_url`, `job_status`,
  `artifacts` (the job's `set_stats`), `created`, `started` and `finished`.
- Approver names are resolved to users and groups when the approval is
  created, not at validation (a definition naming an unknown group
  validates and fails at that node); a service account can hold
  `approval:decide` and still cannot decide, so the Slack bot's
  orchestrator identity is a user in a group the nodes name.
- `GET /api/v1/approvals?status=pending` lists the approvals waiting
  (`id`, `execution_id`, `node_name`, `prompt`); `PATCH
  /api/v1/approvals/{id}` with `status` (`approved` or `rejected`) and
  `decision_notes` decides one as the caller's identity and keeps the
  notes, which is how `slack/approval_bot.py` carries a Slack click back.
  An approval nobody decides within `decision_window` is recorded as
  expired by the worker service and fails the execution at that node,
  whatever `fallback_decision` says.
- A launch the platform answers with a 503 may still have created the
  job: the gateway timed out after the controller had accepted one, the
  node retried and launched the template again, and the execution
  recorded the second job. A playbook behind an AAP node should therefore
  be safe to run twice. In this tree the QoS cap (`already capped`), the
  quarantine (`already as wanted`), the identify LED (the module changes
  nothing when the LED is already set), the replication resume (`already
  running`, or `nothing to resume`) and the DR drill (a drill whose marker
  for the day exists is `skipped`) are: a second launch with the same extra
  vars leaves the array as it is and reports `changed` false. The capacity
  request raises a second request file and record for the same event; the
  capacity reclaim refuses a second launch of a request already done, and
  the file system growth refuses a size that would not grow the file
  system, so a retried launch of either fails its node with the work
  already done. Check the job before retrying such an execution.

Loop wiring, read from the validator of the same code (no shipped
definition uses a loop): the body hangs off the loop node's `iterate` port
and returns to it over an edge with `to_port: iterate`, which the
orchestrator treats as the feedback edge; `complete` continues after the
last iteration. A `for_each` loop takes `items`, a `do_while` loop
`condition`, both `max_iterations`.

## On a live instance

The lab exit tests of [docs/VERIFICATION.md](../docs/VERIFICATION.md)
("Lab exit tests for part 4") run three of the definitions on a live
orchestrator beside a containerized platform, and that document records
the outcome: the capacity route from an event posted through **FlashBlade -
orchestrator post** (or from the capacity activation with
`orchestrator_enabled` true) through the request job, the approval and the
reclaim job to a completed execution; `day2_grow_fs` started from the API
through its approval to the grow job and the closing notify; and the DR
drill definition's publish creating its schedule. What those runs do not
cover (the paused-link route, the LED, QoS cap and quarantine forms, and an
approval decided from Slack by a bot instance of this project) stands on
the unit tests and the linter.
