# Security

This project talks to storage arrays with administrative credentials and
can change their configuration. Read this before running it anywhere that
matters.

- **Two credentials, two roles.** The source plugins only read; the
  remediation playbooks write. The shipped examples hand one token per
  array to both (`FB_A_TOKEN` in `vars/example.yml` and
  `inventory/example.yml`), so that token needs a role that can write. To
  split them, put a read-only token in its own environment variable and
  name that variable in `api_token_env`; give the playbooks a dedicated
  service account, name it in `automation_users`, and name its variable in
  the inventory's `lookup('env', ...)`. Credentials reach the plugins
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
  1101 runbook fire inside one. Four switches turn actions into
  notifications: `fs_auto_grow`, `auto_raise_user_quota`,
  `client_actions_enabled` and `qos_auto_cap`; the NTP and DNS reapply,
  the reconcile and the identify LED have none. The reclaim of
  pending-eradication objects sits behind an approval on every path.
  Start with the four switches off and `orchestrator_enabled` off, and
  read the rules you enable.
- **The approvals, maintenance and containment webhooks (`approval_port`,
  `maintenance_port`, `locks_port`) need a bearer token.** The capacity
  approval ruleset, the maintenance-window ruleset and the containment
  ruleset accept a POST only with `Authorization: Bearer <APPROVAL_TOKEN>`.
  Generate a long random value and put it in the env file; `run.sh`
  passes it to ansible-rulebook with `--env-vars`, and the rulebooks read
  `{{ APPROVAL_TOKEN }}` from the environment, never from a vars file. A
  maintenance window silences every alert the array does not exempt, so
  the token that opens one is a token that silences the array.
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
  (`orchestrator_validate_certs`), unlike the array calls below.
- **The syslog listener filters, it does not authenticate.** Bind it with
  `syslog_host` and list the arrays' addresses or prefixes in
  `syslog_allowed_sources` (empty accepts any sender). UDP source addresses
  can be forged, so syslog events are a fast hint: `syslog.yml` only
  notifies, and the REST plugin owns every action that changes an array.
  Data-audit records arrive from every blade over the management VIP; a
  syslog server with the `data-audit` service is the audit pipeline, and
  alert 1139 is the array saying it dropped some of it.
- **The raw REST tasks log in with the API token.** `tasks/fb_rest.yml`
  exchanges the inventory token for a session at `POST /api/login` for the
  endpoints the collection has no module for (QoS policies, maintenance
  windows, user quota reads), keeps the session token in a `no_log` fact
  and logs out at the end of the play. Nothing it does is outside what the
  same credential can do through the modules.
- **The Slack webhook URL is a secret.** Anyone holding it can post to the
  channel. It lives in the env file, never in a vars file, and the notify
  playbook posts with `no_log`.
- **TLS verification is off by default** in the plugins and the playbooks
  (`verify_ssl: false`, `disable_warnings: true`) because arrays in a lab
  usually carry self-signed certificates. Turn it on where the arrays have
  real ones.
- **Reporting.** Please report a vulnerability privately to the maintainer
  through GitHub's security advisory form for this repository rather than in
  a public issue.
