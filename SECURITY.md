# Security

This project talks to storage arrays with administrative credentials and
can change their configuration. Read this before running it anywhere that
matters.

- **Two credentials, two roles.** The source plugins only read; the
  remediation playbooks write. The shipped examples hand one token per
  array to both (`FB_A_TOKEN` in `vars/example.yml`,
  `inventory/example.yml` and `aap/vars/example.yml`), so that token needs
  a role that can write. To
  split them, put a read-only token in its own environment variable and
  name that variable in `api_token_env`; give the playbooks a dedicated
  service account, name it in `automation_users`, and name its variable in
  the inventory's `lookup('env', ...)` (on the platform, in the fleet
  credential). Credentials reach the plugins
  through environment variables (`api_token_env`, `fb_url_env`) and the
  playbooks through the inventory, never through a vars file. `run.sh`
  loads them from `~/.flashblade-eda.env` (template: `env.example`); keep
  that file mode 600. `*.env` is gitignored. A FlashBlade API token
  belongs to a local or directory-service user whose role (`readonly`,
  `ops_admin`, `storage_admin`, `array_admin`) is what the token can do;
  the plugin's user needs `readonly` and nothing more.
- **Not every write is gated the same way.** The array's one maintenance
  window suppresses the actions whose events carry `maintenance_active`
  (alerts, audits, sessions, health, hardware and config): the alert
  runbooks, the reconcile, the identify LED and the NTP and DNS reapply;
  the telemetry events carry no flag, so the file system grow, the QoS cap
  and the client diagnostics run inside a window too. The array itself
  keeps routing the quota and certificate codes (1016, 1026, 1101 to 1104,
  1106 to 1110, 1123, 1130) during a window, so `quota_user.yml` and the
  1101 runbook fire inside one. Twelve switches turn actions into
  notifications (`fs_auto_grow`, `auto_raise_user_quota`,
  `client_actions_enabled`, `qos_auto_cap`, `canary_auto_disable_key`,
  `anomaly_auto_disable_key`, `audit_auto_reattach`, `posture_auto_revert`,
  `ir_auto_contain`, `ir_auto_tarpit`, `replication_auto_resume`,
  `certs_auto_renew`); `landing_enabled` and `legal_hold_on_arrival` gate
  the landing ingest and the legal hold the same way and are off in the
  example file; the NTP and DNS reapply, the reconcile and the identify
  LED have none, and lock breaking has only the approval webhook on
  `locks_port`. The reclaim of pending-eradication objects sits behind
  an approval on every path. Start with the switches off and
  `orchestrator_enabled` off, and read the rules you enable.
- **The approvals, maintenance and containment webhooks (`approval_port`,
  `maintenance_port`, `locks_port`) need a bearer token.** The capacity
  approval ruleset, the maintenance-window ruleset and the containment
  ruleset accept a POST only with `Authorization: Bearer <APPROVAL_TOKEN>`.
  Generate a long random value and put it in the env file; `run.sh`
  passes it to ansible-rulebook with `--env-vars`, and the rulebooks read
  `{{ APPROVAL_TOKEN }}` from the environment, never from a vars file. A
  maintenance window silences every alert the array does not exempt, so
  the token that opens one is a token that silences the array.
  `ansible.eda.webhook` serves TLS when its `certfile` and `keyfile`
  options name a certificate and key, and each webhook should carry its
  own token, one secret per port, so that a token which approves a reclaim
  cannot open a window or break a lock.
- **The git-push webhook needs an HMAC signature.** The change control
  ruleset verifies GitHub's `X-Hub-Signature-256: sha256=<hex>` over the
  exact request body with `GIT_WEBHOOK_SECRET` (same env file, same
  `--env-vars` route). Set the same secret on the GitHub webhook; an
  unsigned or wrongly signed push is dropped.
- **The orchestrator handoff uses a service-account client secret.** With
  `orchestrator_enabled: true`, `playbooks/orchestrator_post.yml` reads
  `AO_CLIENT_ID` and `AO_CLIENT_SECRET` from the environment (same env
  file), exchanges them for a 15 minute bearer token held in a `no_log`
  fact for one playbook run, and never writes the token or the secret
  anywhere; the alert-log record carries status and execution id only. TLS
  verification for the orchestrator calls is on by default
  (`orchestrator_validate_certs`), unlike the array calls below. On the
  platform the `Orchestrator service account - flashblade-eda` credential
  injects the same two variables into the orchestrator post template and
  the activations and carries `orchestrator_url` as the job's execution
  environment reaches the orchestrator (`host.containers.internal` when it
  runs on the platform host). Give the service account the EDA triggers of
  the two routes and, for `tools/ao_sync.py`, the project-admin role on its
  project, and nothing else; approvals are decided by users in `admins` or
  `approvers`, never by the service account
  ([docs/ORCHESTRATOR.md](docs/ORCHESTRATOR.md)). On the platform, launch
  permission on the `FlashBlade - capacity reclaim` job template is
  permission to eradicate destroyed file systems, buckets and snapshots
  (the reclaim rebuilds its request from the `candidates` it is given when
  no request file exists, provided the launch also sets
  `rebuild_from_artifacts: true`); grant it to the workflow's and the
  orchestrator's identity only.
- **On the platform the credential store holds the tokens.** `aap/seed.yml`
  reads every secret from the environment of the shell that runs it
  (`aap/vars/example.yml` names the variables), writes it into a platform
  credential under `no_log` and never into a file, a log or an
  activation's extra vars; the platform encrypts it and reads it back to
  nobody, the seed included. The inventory `FlashBlades` holds names only.
  The `FlashBlade fleet` credential carries every array's token in one
  secret and is attached to every template and activation, so the execute
  role on any of those templates is a write credential for every array:
  grant it to the platform user the activations launch as (`Platform user
  - flashblade-eda`), the workflow's approvers and the orchestrator's
  identity, and to nobody else. The per-array `FlashBlade - <array>`
  credentials exist for launches that pin an array, and every array-facing
  playbook refuses a `credential_array` that is not the array it was asked
  to act on.
- **The object store credential is two S3 identities.** `FlashBlade object
  store - <array>` carries the tail key and the state key beside the
  array's REST pair and injects them as the `FB_OBJ_*` environment
  variables into every activation (the object-audit source reads them)
  and the S3-side templates. The state key writes the cursor copy, the request objects, the receipts and
  the legal holds, so its user's policies on the array, not the
  credential, bound what it can touch: scope that user to the `eda`
  buckets and keep the demo key of the tests out of the credential.
- **A private repository needs a Source Control token.** The seed creates
  `Source control - flashblade-eda` (controller and EDA) from the variable
  named by `aap_scm_token_env` (default `GITHUB_TOKEN`) with
  `aap_scm_username` and attaches it to both projects. Use a fine-grained
  token with read-only access to this one repository's contents, and leave
  the variable unset once the repository is public: the seed then creates
  no such credential, and never deletes one.
- **The Alertmanager listener cannot authenticate.** `ansible.eda.alertmanager`
  has no token option. Standalone, bind it with `alertmanager_host` to an
  interface only Alertmanager reaches, or keep it on loopback behind the
  reverse ssh tunnel; on the platform the activation runs with host
  networking and `alertmanager_port` is the platform host's port, which
  the host firewall should admit from Alertmanager's address alone, or put
  an event stream that authenticates in front.
- **The syslog listener filters, it does not authenticate.** Bind it with
  `syslog_host` and list the arrays' addresses or prefixes in
  `syslog_allowed_sources` (empty accepts any sender). UDP source addresses
  can be forged, so syslog events are a fast hint: `syslog.yml` only
  notifies, and the REST plugin owns every action that changes an array.
  Data-audit records arrive from every blade over the management VIP; a
  syslog server with the `data-audit` service is the audit pipeline, and
  alert 1139 is the array saying it dropped some of it.
- **Event text is data.** Every source in every rulebook runs the
  `sdodsley.flashblade_eda.jinja_safe` event filter, which keeps template
  delimiters in an event's strings inert, so a playbook renders event text
  (an alert summary, a syslog line, an object key, a webhook body, an LLDP
  neighbour name) as text. The webhook, Alertmanager and syslog rules
  accept only well-formed array names, addresses and ids in the fields
  they hand to a playbook; a webhook request or an alert that does not
  fit is logged by the ruleset's last rule, and a syslog line whose array
  name does not fit starts nothing.
- **The raw REST tasks log in with the API token.** `tasks/fb_rest.yml`
  exchanges the inventory token for a session at `POST /api/login` for the
  endpoints the collection has no module for (QoS policies, maintenance
  windows, user quota reads), keeps the session token in a `no_log` fact
  and logs out at the end of the play. Nothing it does is outside what the
  same credential can do through the modules.
- **The Slack webhook URL is a secret.** Anyone holding it can post to the
  channel. It lives in the env file or the `Slack webhook - flashblade-eda`
  credential, never in a vars file, a definition or an activation's extra
  vars, and the notify playbook posts with `no_log`. The bot token behind
  the platform's notification template needs `chat:write` only.
- **The approval bot decides on behalf of its identities.** `slack/approval_bot.py`
  approves or rejects with the orchestrator user and the platform user it
  holds (a user in the `approvers` group the definitions name; a platform
  user with the approve role on the workflow); restrict who may click with
  `SLACK_APPROVERS` (Slack user ids), or anyone in the channel can decide.
  Its tokens (`SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` and the two identities)
  reach it through its environment or, preferably, through files and
  podman secrets (the `*_FILE` variables), never on the command line, and
  it logs no secret. One Slack app has one bot: a site running the
  FlashArray sibling too runs one bot for both systems, and its identities
  then hold the approve role on both organizations' workflows
  ([slack/README.md](slack/README.md)).
- **TLS verification is off by default** in the plugins and the playbooks
  (`verify_ssl: false`, `disable_warnings: true`) because arrays in a lab
  usually carry self-signed certificates. Turn it on where the arrays have
  real ones.
- **Reporting.** Please report a vulnerability privately to the maintainer
  through GitHub's security advisory form for this repository rather than in
  a public issue.
