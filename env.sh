# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
# shellcheck shell=bash
#
# Shared environment for run.sh and tests/e2e_*.sh. Sourced from the
# repo root, never executed:
#   . ./env.sh
# In order:
#   1. sources the env file FBEDA_ENV (default $HOME/.flashblade-eda.env) when
#      it exists; env.example lists what it exports. The file is read once per
#      process tree (FBEDA_ENV_LOADED marks that), so a value a caller already
#      exported, such as the FBEDA_VARS the test scripts derive before they
#      call run.sh, is not overwritten when run.sh sources this file again.
#      Unset FBEDA_ENV_LOADED to force a re-read in a shell that sourced this.
#   2. activates the virtualenv FBEDA_VENV (default <repo>/venv) when it has
#      bin/activate; otherwise ansible-rulebook must already be on PATH.
#   3. ANSIBLE_COLLECTIONS_PATH defaults to $HOME/.ansible/collections, with
#      <repo>/collections in front when that directory exists (ansible.eda
#      and everpure.flashblade must both be reachable), and the localhost
#      warning is silenced.
#   4. exports FBEDA_VARS (default vars/local-lab.yml) and FBEDA_INVENTORY
#      (default inventory/local-lab.yml), paths relative to the repo root.

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

ENV_FILE=${FBEDA_ENV:-$HOME/.flashblade-eda.env}
if [ -z "${FBEDA_ENV_LOADED:-}" ] && [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  export FBEDA_ENV_LOADED=1
fi

VENV=${FBEDA_VENV:-$ROOT/venv}
if [ -f "$VENV/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
elif ! command -v ansible-rulebook >/dev/null 2>&1; then
  echo "env.sh: no virtualenv at $VENV and ansible-rulebook is not on PATH;" \
    "create venv/ (README, Quick start) or set FBEDA_VENV" >&2
  return 1 2>/dev/null || exit 1
fi

COLL_DEFAULT=$HOME/.ansible/collections
if [ -d "$ROOT/collections" ]; then
  COLL_DEFAULT="$ROOT/collections:$COLL_DEFAULT"
fi
export ANSIBLE_COLLECTIONS_PATH=${ANSIBLE_COLLECTIONS_PATH:-$COLL_DEFAULT}
export ANSIBLE_LOCALHOST_WARNING=False
export FBEDA_VARS=${FBEDA_VARS:-vars/local-lab.yml}
export FBEDA_INVENTORY=${FBEDA_INVENTORY:-inventory/local-lab.yml}
