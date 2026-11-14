# Orchestrator handoff

How a rulebook hands an event to Red Hat's automation orchestrator (the
AAP 2.7 add-on; upstream code: Syntara) instead of acting on it locally:
the bridge playbook, its mock and its tests. The workflow definitions that
consume the events are orchestrator/capacity_reclaim_approval.json and
orchestrator/replication_paused_approval.json (orchestrator/README.md).

Verified: the bridge against `tests/mock_orchestrator.py` from the unit
test (`tests/unit/test_orchestrator_bridge.py`: token, post, 202 with an
execution id, the `flashblade-eda/1` envelope for a capacity event and for
a paused replica link, 401, 403, an unreachable orchestrator, an oversize
event, missing credentials); `tests/replay_capacity.yml` replaying one
event through the real capacity rules with `orchestrator_enabled` true
against the mock; and the capacity route end to end on a live
orchestrator, from the trigger through the approval to the reclaim job
("On a live orchestrator" below; [VERIFICATION.md](VERIFICATION.md)). The
contract comes from the upstream Syntara documentation and the 1.3.1 API;
what no run exercises is listed under "Limitations".

Availability: Red Hat ships the automation orchestrator as an add-on to
Ansible Automation Platform 2.7 for OpenShift-based deployments only. No
timeline is published for the containerized (non-OpenShift) topology,
and the one configuration-as-code collection that targets its API (1.3.1)
describes that API as a technology preview that may change.

## What moves, what stays

Two rules move: capacity at risk, and a paused bucket replica link (an
approval then gates the resume, `orchestrator/replication_paused_approval.json`).
Everything else stays in EDA: polling, the gates (`maintenance_active`,
`automation_users`, the per-site switches), the throttles, notifications,
the QoS caps, the containment actions and the file system links (a paused
file system link has no pause flag to clear from here and is notified
whatever the switches say).

`orchestrator_enabled` (vars) is the switch:

- `false` (default): the capacity rule writes a request file and the
  approvals webhook ruleset answers it (`capacity_request.yml`,
  `capacity_reclaim.yml`); a paused bucket link is resumed when
  `replication_auto_resume` is true and notified otherwise.
- `true`: the rule runs `playbooks/orchestrator_post.yml`, which posts the
  whole event to the EDA trigger named by `orchestrator_paths.capacity`
  (or `.replication`). No request file is written and the approvals
  webhook is idle for that rule. The request, the approval and the reclaim
  happen in the orchestrator workflow: an `aap_job_template` node raises
  the capacity request with the event's fields as `extra_vars`, an
  `approval` node waits, and an `aap_job_template` node runs the reclaim
  with the request job's artifacts (`request_id`, `candidates`), the
  event's `array` and the constants `rebuild_from_artifacts`,
  `reclaim_action` and `approver`. The replication workflow notifies,
  waits one hour for the approval and runs the resume with the event's
  `array` and local bucket.

The switch is a literal in the rule's condition (`vars.orchestrator_enabled
== true` / `== false`), because rulebook conditions take no filters or
defaults; a `vars/local-<site>.yml` must carry the key, as
`vars/example.yml` does.

The capacity rule has a 24 hour per-array throttle, the replication rule
one hour per array and local bucket. A handoff that fails (the
orchestrator is down, the token is refused, the service account is not
bound) is recorded as a warning and is not retried until the throttle
window has passed; nothing is written locally in that case unless
`orchestrator_fallback_local` is on. Watch the alert log for
`orchestrator_post` records with `severity: warning`. An approval the
orchestrator is waiting for is decided in its UI, or from Slack through
`slack/approval_bot.py`, which lists the pending approvals
(`GET /api/v1/approvals?status=pending`) and decides one with
`PATCH /api/v1/approvals/{id}` (`status`, and `decision_notes` naming the
Slack user); the decision is recorded under the bot's own orchestrator
identity (slack/README.md).

## The contract

### 1. Token

```
POST {orchestrator_url}/api/v1/auth/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&client_id=<AO_CLIENT_ID>&client_secret=<AO_CLIENT_SECRET>
```

Answer `200 {"access_token": "...", "token_type": "bearer", "expires_in":
900}`; `401` for bad credentials. Tokens live 15 minutes by default, cannot
be refreshed and are never cached to disk: the playbook requests one per
event and holds it in a `no_log` fact.

### 2. Event

```
POST {orchestrator_url}/api/v1/webhooks/eda/{webhook_path}
Authorization: Bearer <access_token>
Content-Type: application/json

{"event": {...}}
```

| Status | Meaning | Record |
| --- | --- | --- |
| 202 `{"execution_id": "..."}` | a workflow execution started | `severity: info`, `execution_id` |
| 401 | missing, expired or invalid token | `warning`, `status: 401` |
| 403 | the service account is not bound to this trigger (`authorized_service_account_ids`) | `warning`, `status: 403` |
| 413 | body over the orchestrator's 1 MB payload limit | `warning`, `status: 413` (the playbook refuses bodies over 1000000 bytes before sending, see below) |
| -1 | no HTTP answer (connection refused, timeout, TLS failure) | `warning`, `status: -1` |

The orchestrator passes the body through unwrapped, so a workflow node
reads `${trigger.event.array}`, `${trigger.event.data.used_pct}` and so on
(the trigger's id is an alias of `trigger`).

### 3. Envelope

```json
{
  "event": {
    "schema": "flashblade-eda/1",
    "event_type": "purefb_capacity",
    "array": "fb-a",
    "maintenance_active": false,
    "observed_at": "2026-01-05T21:40:49Z",
    "data": {
      "array": "fb-a",
      "used_pct": 97.3,
      "used_bytes": 48650000000000,
      "capacity_bytes": 50000000000000,
      "growth_bytes_per_day": 150000000000,
      "days_to_full": 9,
      "days_to_full_known": true,
      "file_system_bytes": 30000000000000,
      "object_store_bytes": 18650000000000,
      "top_growers": [{"name": "fs-db-01", "kind": "file_system", "delta_bytes": 90000000000, "total_physical": 9000000000000}],
      "pending_eradication_file_systems": 2,
      "pending_eradication_buckets": 0,
      "pending_eradication_snapshots": 5,
      "others": [{"array": "fb-b", "used_pct": 41.0}]
    }
  }
}
```

| Field | Content |
| --- | --- |
| `schema` | `flashblade-eda/1`; bump when a field changes meaning |
| `event_type` | the event's `purefb_*` key as the plugin emits it |
| `array` | inventory name of the array, as every event carries it |
| `maintenance_active` | the event's own flag; `false` when the event type does not carry one (the capacity and replica-link events do not) |
| `observed_at` | UTC, `%Y-%m-%dT%H:%M:%SZ`, when the playbook built the envelope |
| `data` | the event's fields, unchanged, without ansible-rulebook's `meta` |

The body must stay under the orchestrator's 1 MB limit. The playbook
measures `to_json` output and refuses anything over 1000000 bytes without
a request (`stage: size_check`, `status: null`). A capacity event is a few
hundred bytes; the limit matters only if a future route forwards large
lists.

The replication route sends the same envelope with `event_type`
`purefb_replica_link` and the link's fields in `data`: `kind` (`bucket`),
`local` (the bucket on the array), `remote`, `remote_object`, `direction`,
`status`, `status_details`, `paused`, `lag_ms`, `lag_s`, `lagging`,
`recovery_point_age_s`, `unhealthy`, `recovered`,
`consecutive_issue_polls` and `escalate`. The definition reads
`${trigger.event.array}`, `${trigger.event.data.local}`, `.remote`,
`.remote_object`, `.status` and `.recovery_point_age_s` for its
notifications and the approval prompt, and passes `target_array`, `bucket`
(the local bucket) and a `reason` to the resume job.

### 4. The trigger on the workflow side

The published workflow carries an `eda_trigger` whose `webhook_path` is
the value in `orchestrator_paths` and whose `authorized_service_account_ids`
lists the service account behind `AO_CLIENT_ID`. Ids differ per
environment; the definition files use the placeholder `REPLACE_AT_SYNC`:

```json
{
  "id": "fb_capacity_event",
  "type": "eda_trigger",
  "parameters": {
    "webhook_path": "fb_capacity",
    "authorized_service_account_ids": ["REPLACE_AT_SYNC"]
  }
}
```

The shipped trigger also carries an `input_schema` that requires the
envelope keys (`schema`, `event_type`, `array`, `maintenance_active`,
`observed_at`, `data`), the route's event type and the array-name pattern
the playbooks assert on.

A webhook trigger and an EDA trigger may share a path; the EDA endpoint is
`/api/v1/webhooks/eda/<path>`, the plain webhook `/api/v1/webhooks/<path>`.

## Configuration

Vars (`vars/example.yml`, copied to `vars/local-<site>.yml`):

| Key | Default | Meaning |
| --- | --- | --- |
| `orchestrator_enabled` | `false` | the switch described above |
| `orchestrator_url` | `https://orchestrator.example.com` | base URL; `/api/v1/...` is appended |
| `orchestrator_paths` | `{capacity: fb_capacity, replication: fb_replication}` | `webhook_path` per route; one key per rule that hands over |
| `orchestrator_fallback_local` | `false` | when true, a capacity handoff that produced no execution raises the local request anyway (`capacity_request.yml`), so the approvals webhook can answer it; off because on a platform the request file would live in a job's /tmp |
| `orchestrator_validate_certs` | `true` | TLS verification for both calls (the orchestrator normally has a real certificate, unlike lab arrays) |

Playbook-only knobs, overridable as extra_vars: `orchestrator_timeout`
(seconds per call, 30) and `orchestrator_max_body_bytes` (1000000).

Environment (`env.example`): `AO_CLIENT_ID` and `AO_CLIENT_SECRET`, the
client credentials of an orchestrator service account. The playbook reads
them with `lookup('env')`; `run.sh` also forwards them to ansible-rulebook
with `--env-vars` when they are set, like `APPROVAL_TOKEN`. They never sit
in a vars file. Without them the playbook fails with a message naming
both variables and sends nothing.

On the orchestrator: create the service account in the workflow's project,
give it a client-credentials credential (the secret is shown once; client
ids start with `nx_sa_`; credentials expire after 180 days by default, with
a rotation grace period), and list the account in each EDA trigger's
`authorized_service_account_ids`. A service account bound to no trigger
gets a token and then a 403.

## On the platform

As an Event-Driven Ansible controller activation the rule's `run_playbook`
becomes `run_job_template` against `FlashBlade - orchestrator post`.
`aap/seed.yml` creates that template and the `Orchestrator service
account` credential type, which injects `AO_CLIENT_ID` and
`AO_CLIENT_SECRET` as environment variables and `orchestrator_url` as an
extra var (aap/README.md). On a containerized platform a job's execution
environment reaches an orchestrator on the same host only as
`https://host.containers.internal:<port>`; the host's own address and
loopback both point back at the container. That is the URL the credential
carries. A capacity event handed over through the template runs its
workflow this way ("On a live orchestrator").

## The alert-log record

One line per handoff, whatever happened, kind `orchestrator_post`:

```json
{"time": "...", "kind": "orchestrator_post", "severity": "info", "array": "fb-a", "event_type": "purefb_capacity", "webhook_path": "fb_capacity", "stage": "post", "status": 202, "execution_id": "00000000-0000-4000-8000-000000000000", "reason": "", "body_bytes": 640}
{"time": "...", "kind": "orchestrator_post", "severity": "warning", "array": "fb-a", "event_type": "purefb_capacity", "webhook_path": "fb_capacity", "stage": "token", "status": 401, "execution_id": "", "reason": "token request failed: invalid client credentials", "body_bytes": 640}
{"time": "...", "kind": "orchestrator_post", "severity": "warning", "array": "fb-a", "event_type": "purefb_capacity", "webhook_path": "fb_other", "stage": "post", "status": 403, "execution_id": "", "reason": "service account is not authorized for this trigger", "body_bytes": 640}
{"time": "...", "kind": "orchestrator_post", "severity": "warning", "array": "fb-a", "event_type": "purefb_capacity", "webhook_path": "fb_capacity", "stage": "token", "status": -1, "execution_id": "", "reason": "token request failed: Status code was -1 and not [200]: Request failed: <urlopen error [Errno 111] Connection refused>", "body_bytes": 640}
{"time": "...", "kind": "orchestrator_post", "severity": "warning", "array": "fb-a", "event_type": "purefb_capacity", "webhook_path": "fb_capacity", "stage": "size_check", "status": null, "execution_id": "", "reason": "body of 1100433 bytes exceeds the 1000000 byte limit; not sent", "body_bytes": 1100433}
```

`stage` is where it stopped: `size_check`, `token` or `post`. `reason` is
the orchestrator's problem-details `detail` when there is one, else the
uri module's one-line message. The record never holds the token, the
client secret or the body.

## Running the mock

`tests/mock_orchestrator.py` is a standard-library HTTP server with the
two endpoints, the executions list and `GET /healthz`. From the repository
root:

```
python tests/mock_orchestrator.py --port 8800 --client-id nx_sa_demo \
    --client-secret demo --paths fb_capacity,fb_replication --record tests/out/orchestrator.jsonl
```

`--paths` is the comma-separated list of webhook paths the service account
is "bound" to (anything else answers 403); `--record` receives one JSON
line per accepted event; `--token-ttl` (default 900) ages tokens. By hand:

```
TOKEN=$(curl -s -X POST http://127.0.0.1:8800/api/v1/auth/token \
  -d grant_type=client_credentials -d client_id=nx_sa_demo -d client_secret=demo | python -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
curl -s -X POST http://127.0.0.1:8800/api/v1/webhooks/eda/fb_capacity \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"event": {"schema": "flashblade-eda/1", "event_type": "purefb_capacity", "array": "fb-a", "data": {}}}'
```

The playbook alone, against the mock:

```
AO_CLIENT_ID=nx_sa_demo AO_CLIENT_SECRET=demo ansible-playbook -i inventory/example.yml \
  -e orchestrator_url=http://127.0.0.1:8800 -e orchestrator_webhook_path=fb_capacity \
  -e '{"event": {"purefb_capacity": {"array": "fb-a", "used_pct": 97.3}}}' \
  -e array=fb-a -e alert_log=$PWD/tests/out/alerts.log playbooks/orchestrator_post.yml
```

`alert_log` must be absolute; a relative path is resolved against
`playbooks/`, not the directory the command runs from.

The whole chain, with ansible-rulebook: the header of
`tests/replay_capacity.yml` has the commands (derive a vars file from the
site vars and the capacity fixture, run the replay); start the mock first
and set `orchestrator_enabled: true` and the mock's URL in that vars file.
Expected: one `orchestrator_post` record with status 202 and the envelope
as one line in the record file.

`tests/unit/test_orchestrator_bridge.py` does the same without a shell:
it serves the mock in a thread on a free port bound to `fb_capacity` and
`fb_replication`, checks the mock's own answers (200, 401, 403, 413, 202,
the executions list), then runs the playbook with `ansible-playbook` for
the accepted capacity case, a paused replica link on the replication path,
a wrong secret, an unbound path, an unreachable orchestrator (a closed
port; recorded as `status: -1`), an oversize event, missing credentials
and an event without a `purefb_*` key, and checks that neither the secret
nor any issued token appears in the playbook output. The playbook cases
are skipped when `ansible-playbook` is not on the PATH. It runs in about
a minute.

## Security notes

- Both HTTP tasks run with `no_log`; the playbook logs only the status and
  the execution id. The credentials come from the environment and the
  token from memory; nothing is cached.
- TLS verification defaults to on for the orchestrator calls, unlike the
  array calls (`SECURITY.md`); set `orchestrator_validate_certs: false`
  only for a lab instance with a self-signed certificate.
- The envelope carries array, file system and bucket names and numbers,
  never credentials. The orchestrator keeps the payload in its audit log
  and Temporal history, so treat those stores as holding your array names.
- A 403 means the service account exists but is not bound to the trigger;
  a 401 on the token call means the credential is wrong, disabled or
  expired. Both are recorded, never retried in a loop.

## On a live orchestrator

The capacity route is verified end to end on the upstream orchestrator
code at the tag Red Hat ships as the 2026.8 release (API 1.3.1), beside
the containerized platform of aap/README.md: a service account with a
client credential, an `Ansible Automation Platform` credential and an
`ansible_automation_platform` integration; `orchestrator/*.json`
validated, created and published by `tools/ao_sync.py` (a second run
reports every definition unchanged); a `purefb_capacity` event handed
over by **FlashBlade - orchestrator post** (token, post, 202, one
`orchestrator_post` record with the execution id); **FlashBlade - capacity
request** running with `credential_names` resolved to the event array's
credential. With nothing pending eradication the request publishes
`candidate_count` 0, the condition takes its `false` port and the
no-candidates notify completes the execution; with a destroyed file system
on the array the request lists it, the execution pauses at the approval,
the Slack bot's card carries the decision back and **FlashBlade - capacity
reclaim** rebuilds the request from the artifacts and eradicates that one
file system (`result: done`, nothing refused). `day2_grow_fs` runs from
the API through its approval to the grow job and the closing notify, and
publishing the DR drill definition creates its schedule
(orchestrator/README.md, "Verified on a live instance"). The contract the
definitions rely on (the unwrapped body, typed `${...}` values, artifacts
and `job_url`, approver resolution, the publish-with-body versioning) is
in orchestrator/README.md, "The orchestrator's contract".

## Limitations

- Covered against the mock by the unit tests and not by a live run of this
  tree: whether the EDA webhook accepts a post without a bearer token
  (upstream pages disagree; the bridge always sends one), the 403 answer
  for a service account bound to no trigger, and the problem-details shape
  of error answers. The mock implements them as the upstream documentation
  describes.
- The paused-link route, the LED, QoS cap and quarantine forms and an
  approval decided from Slack by this project's own bot instance are not
  among the lab exit tests; they stand on the unit tests and the linter.
- A failed handoff leaves no local request unless
  `orchestrator_fallback_local` is true; by default the alert-log warning
  is the only trace.
- The bridge posts every event its rule lets through and does not look for
  an execution already open for the same array and object; the rule's
  throttle (24 hours per array for capacity, one hour per array and bucket
  for replication) is the only guard against a second execution.
- A retried launch of **FlashBlade - capacity request** raises a second
  request for the same event (the playbook keys requests on the array and
  the time, not on the event); a retried **FlashBlade - capacity reclaim**
  or **FlashBlade - grow file system** launch is refused with the work
  already done (orchestrator/README.md, "The orchestrator's contract").
- Two routes are wired, capacity and a paused bucket link. Another one
  (an S3 anomaly, say) follows the same pattern: a key under
  `orchestrator_paths`, a rule guarded by `vars.orchestrator_enabled ==
  true` that runs `orchestrator_post.yml`, `== false` on the local rule,
  and a definition with an `eda_trigger` on that path.
