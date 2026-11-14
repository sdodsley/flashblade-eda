#!/bin/bash
# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
#
# End-to-end test of the REST event source and its rulebooks against one
# FlashBlade. Each step starts a rulebook with --print-events for a fixed
# number of seconds and greps the event names the plugin must emit; two
# steps change the array (a maintenance window, the identify LED) and
# restore it. Output goes to tests/out/*.log and the alert log is printed
# at the end.
#
#   A=<array> LED_COMPONENT=<component, e.g. CH1.FB1> ./tests/e2e_sources.sh
#
# Needs env.sh working (env file, venv) and the vars and inventory files
# named by FBEDA_VARS / FBEDA_INVENTORY (defaults vars/local-lab.yml and
# inventory/local-lab.yml). Credentials for A come from the environment
# variables named by A_URL_ENV / A_TOKEN_ENV (defaults FB_A_URL /
# FB_A_TOKEN), exported as FB_URL / FB_TOKEN for the direct plugin run.
# Every step runs from tests/out/vars_test.yml, a copy of the vars file with
# alert_log and request_dir under tests/out, so the configured alert log is
# never touched.
# Changes made on A: a one-hour maintenance window opened and closed through
# playbooks/maintenance_window.yml, the identify LED on LED_COMPONENT lit by
# the health replay and cleared. An interrupted run still undoes whatever
# was still in place. Nothing is created, destroyed or resized: the quota,
# grow and client steps of the lab exit tests (docs/VERIFICATION.md) need a
# scratch file system and a load generator and are run by hand.
set -u
USAGE="usage: A=<array> LED_COMPONENT=<component, e.g. CH1.FB1> $0"
for v in A LED_COMPONENT; do
  [ -n "${!v:-}" ] || { echo "$USAGE" >&2; echo "$v is not set" >&2; exit 2; }
done
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=../env.sh
. ./env.sh || exit 1
A_URL_ENV=${A_URL_ENV:-FB_A_URL}; A_TOKEN_ENV=${A_TOKEN_ENV:-FB_A_TOKEN}
export FB_URL=${!A_URL_ENV:?$A_URL_ENV is not set (export it, put it in the env file, or name another variable with A_URL_ENV)}
export FB_TOKEN=${!A_TOKEN_ENV:?$A_TOKEN_ENV is not set (export it, put it in the env file, or name another variable with A_TOKEN_ENV)}
[ -f "$FBEDA_VARS" ] && [ -f "$FBEDA_INVENTORY" ] || { echo "need $FBEDA_VARS and $FBEDA_INVENTORY (copy vars/example.yml and inventory/example.yml, or set FBEDA_VARS / FBEDA_INVENTORY)" >&2; exit 1; }
OUT=tests/out
mkdir -p "$OUT" "$OUT/requests"
# every step starts from a copy of the vars file that keeps the alert log and
# the request files under tests/out
ALERT_LOG=$PWD/$OUT/alerts.log
{ grep -v -E '^(alert_log|request_dir):' "$FBEDA_VARS"; echo "alert_log: $ALERT_LOG"; echo "request_dir: $PWD/$OUT/requests"; } > "$OUT/vars_test.yml"
export FBEDA_VARS=$OUT/vars_test.yml
rm -f "$ALERT_LOG"
PB() { ansible-playbook -i "$FBEDA_INVENTORY" "$@" 2>&1 | grep -E "changed=|fatal|FAILED" | cut -c1-160; }
RB() { local secs=$1 log=$2; shift 2; timeout "$secs" ./run.sh "$@" > "$OUT/$log" 2>&1; }
summary() { grep -E "polling|Ruleset:|Rule:|Action:|PLAY RECAP|ok=|failed=|msg|poll failed|Traceback|ERROR" "$OUT/$1" | grep -v "^$" | cut -c1-220 | head -n "${2:-25}"; }
# --print-events writes every event as it arrives; count the event names a
# step must have produced and say which are missing
expect() { local log=$1; shift; for name in "$@"; do
  n=$(grep -c "'$name'" "$OUT/$log"); if [ "$n" -gt 0 ]; then echo "  $name: $n"; else echo "  $name: MISSING"; fi; done
  n=$(grep -c "'purefb_poll_error'" "$OUT/$log"); [ "$n" = 0 ] || echo "  purefb_poll_error: $n (see $OUT/$log)"; }

# Each induced condition stays flagged until it is undone. restore runs after
# the step and again from the EXIT trap, so Ctrl-C, SIGTERM or a dropped
# session leaves A as the script found it (the helpers are idempotent).
WINDOW_OPEN=0; LED_ON=0
restore() {
  if [ "$WINDOW_OPEN" = 1 ]; then PB playbooks/maintenance_window.yml -e target_array="$A" -e window_action=close -e alert_log="$ALERT_LOG"; WINDOW_OPEN=0; fi
  if [ "$LED_ON" = 1 ]; then PB playbooks/identify_led.yml -e target_array="$A" -e component="$LED_COMPONENT" -e led_state=false -e severity=info -e alert_log="$ALERT_LOG"; LED_ON=0; fi
}
trap restore EXIT
trap 'exit 130' INT TERM

echo "===== 1. alerts, health, capacity, client, config_drift, sessions: 45s each, steady state, event names counted"
for rb in alerts health capacity client config_drift sessions; do
  RB 45 "$rb.log" "extensions/eda/rulebooks/$rb.yml" -v --print-events
  echo "--- $rb"; summary "$rb.log" 8
done
echo "--- events"
expect alerts.log purefb_alert
expect health.log purefb_health
expect capacity.log purefb_capacity purefb_array_performance
expect client.log purefb_client_performance
expect config_drift.log purefb_config
# the sessions signal sets its cursor on the first poll and emits nothing
# until someone logs in, so a steady-state run shows the alerts it also polls
expect sessions.log purefb_alert
n=$(grep -c "'purefb_session'" "$OUT/sessions.log"); echo "  purefb_session: $n (0 unless a login happened during the run)"

echo "===== 2. maintenance: open a one-hour window, expect maintenance_active true on the health event, then close it (45s)"
WINDOW_OPEN=1
PB playbooks/maintenance_window.yml -e target_array="$A" -e window_action=open -e window_hours=1 -e alert_log="$ALERT_LOG"
RB 45 health_window.log extensions/eda/rulebooks/health.yml -v --print-events
restore
summary health_window.log 12
n=$(grep -c "'maintenance_active': True" "$OUT/health_window.log"); echo "  maintenance_active true: $n"

echo "===== 3. health replay: fixture hardware events drive the identify LED on $LED_COMPONENT, then clear it (40s)"
{ cat "$FBEDA_VARS"; sed "/^---$/d; s/REPLAY_ARRAY/$A/g; s/REPLAY_COMPONENT/$LED_COMPONENT/g" tests/fixtures/hardware.yml; } > "$OUT/vars_replay.yml"
LED_ON=1
FBEDA_VARS="$OUT/vars_replay.yml" RB 40 replay_health.log tests/replay_health.yml -v
restore
summary replay_health.log 25

echo "===== 4. audits: run the audits signal directly with a 10 minute lookback; the window above is in it (30s; plugin log in $OUT/audits_cli.log)"
# the plugin's __main__ takes <name> <fb_url> <TOKEN_ENV> [signals] [interval]
timeout 30 python3 extensions/eda/plugins/event_source/purefb_events.py "$A" "$FB_URL" FB_TOKEN audits 60 2> "$OUT/audits_cli.log" \
  | python3 -c 'import json, sys
for line in sys.stdin:
    e = json.loads(line)
    if "purefb_poll_error" in e:
        print("poll error:", e["purefb_poll_error"].get("error")); continue
    a = e.get("purefb_audit")
    if a:
        print(a["time"], a["user"], a["user_interface"], a["command"], a["subcommand"], (a["arguments"] or "")[:60])' | tail -8

echo "===== alert log"
[ -f "$ALERT_LOG" ] && cut -c1-300 "$ALERT_LOG"
