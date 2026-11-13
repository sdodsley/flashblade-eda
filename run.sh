#!/bin/bash
# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Launch one rulebook.
#   ./run.sh extensions/eda/rulebooks/alerts.yml
#   ./run.sh extensions/eda/rulebooks/config_drift.yml -v --print-events
# Environment (read by env.sh, which this script sources; see env.example):
#   FBEDA_ENV        env file with the array URLs/tokens and webhook secrets
#                    (default $HOME/.flashblade-eda.env)
#   FBEDA_VENV       virtualenv with ansible-rulebook + py-pure-client
#                    (default <repo>/venv; otherwise ansible-rulebook on PATH)
#   FBEDA_VARS       vars file (default vars/local-lab.yml)
#   FBEDA_INVENTORY  inventory file (default inventory/local-lab.yml)
#   APPROVAL_TOKEN   bearer token for the capacity.yml, maintenance.yml and
#                    contain.yml webhooks
#   GIT_WEBHOOK_SECRET
#                    HMAC secret for the change_control.yml webhook; each
#                    secret that is set is passed with --env-vars, an unset
#                    one is not (ansible-rulebook rejects a name it cannot
#                    find)
#   AO_CLIENT_ID, AO_CLIENT_SECRET
#                    automation orchestrator service account for the
#                    orchestrator handoff (playbooks/orchestrator_post.yml);
#                    same rule
set -eu
cd "$(dirname "$0")"
# shellcheck source=env.sh
source ./env.sh

RULEBOOK=${1:-extensions/eda/rulebooks/alerts.yml}
shift || true

ARGS=(
  --rulebook "$RULEBOOK"
  --inventory "$FBEDA_INVENTORY"
  --vars "$FBEDA_VARS"
  --source-dir extensions/eda/plugins/event_source
)
# ansible-rulebook exposes to the rulebook only the environment variables
# named in --env-vars; the rulebooks read the secrets as {{ APPROVAL_TOKEN }}
# and {{ GIT_WEBHOOK_SECRET }}. AO_CLIENT_ID, AO_CLIENT_SECRET and
# SLACK_WEBHOOK_URL are read by playbooks with lookup('env') and travel the
# same way.
ENV_VARS=""
for name in APPROVAL_TOKEN GIT_WEBHOOK_SECRET AO_CLIENT_ID AO_CLIENT_SECRET SLACK_WEBHOOK_URL SLACK_MIN_SEVERITY; do
  if [ -n "${!name:-}" ]; then
    ENV_VARS="${ENV_VARS:+$ENV_VARS,}$name"
  fi
done
if [ -n "$ENV_VARS" ]; then
  ARGS+=(--env-vars "$ENV_VARS")
fi

exec ansible-rulebook "${ARGS[@]}" "$@"
