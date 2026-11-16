#!/usr/bin/env python3
# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Restart the FlashBlade activations that are not running, one at a time,
once the EDA controller answers again.

    export AAP_TOKEN=...
    python tools/activation_restart.py --url https://aap.example.com [--wait-minutes 15]

A host reboot brings a containerized platform back on its own, but not
every activation: the activation worker marks an instance that had reached
its restart policy's limit as failed and leaves it there. This tool waits
for the EDA API (/api/eda/v1/status/) for up to --wait-minutes, then reads
the activation of every platform rulebook by its full name ("FlashBlade
rulebook - <file stem>", never the listing), leaves the disabled and the
running ones alone, waits for one that is starting, and restarts each
other one through its restart endpoint, waiting for it to run before the
next, up to three restarts each (an early start can meet the websocket
service before it is ready). Nothing but these names is touched. The exit
status is 1 when the API never answered or an activation does not run at
the end, so a oneshot unit's ExecMainStatus tells the result; a rulebook
file whose activation is missing is reported and left to the audit. The
gateway, the token and the TLS setting come from the same flags and
environment as tools/activation_audit.py (AAP_HOSTNAME, AAP_TOKEN,
AAP_VALIDATE_CERTS); the token is never printed. Python 3.9+, standard
library plus PyYAML (through the audit module).
"""

import argparse
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import activation_audit as audit  # noqa: E402

API_STATUS = "/api/eda/v1/status/"
POLL_SECONDS = 10  # between reads while waiting for the API or for a start
START_WAIT_SECONDS = 300  # for one start to reach running
RESTARTS = 3  # restarts of one activation before giving up
GAP_SECONDS = 10  # after a restart, before the next activation
# statuses that end by themselves: the start is under way or being undone
IN_FLIGHT = ("starting", "pending", "stopping", "workers offline")


def wait_for_api(client, minutes, say):
    """True once the EDA API answers its status call with 200; False when
    the minutes run out first."""
    started = time.monotonic()
    deadline = started + minutes * 60
    last = "no answer"
    while True:
        try:
            status, _ = client.request("GET", API_STATUS, retries=1)
            if status == 200:
                say(f"the EDA API answered after {int(time.monotonic() - started)} s")
                return True
            last = f"HTTP {status}"
        except audit.AuditError as exc:
            last = str(exc)
        if time.monotonic() >= deadline:
            say(
                f"activation_restart: the EDA API did not answer within "
                f"{minutes:g} minute(s): {last}"
            )
            return False
        time.sleep(POLL_SECONDS)


def read(client, aid):
    status, detail = client.request("GET", f"/api/eda/v1/activations/{aid}/")
    if not isinstance(detail, dict):
        raise audit.AuditError(
            f"GET /api/eda/v1/activations/{aid}/: the answer is not an object"
        )
    return detail


def settle(client, aid, before, deadline):
    """Read the activation until it runs, until a start begun since `before`
    (the restart count then) has ended in anything else, or until the
    deadline; the last detail read."""
    while True:
        detail = read(client, aid)
        status = detail.get("status")
        if status == "running":
            return detail
        ended = status not in IN_FLIGHT and detail.get("restart_count") != before
        if ended or time.monotonic() >= deadline:
            return detail
        time.sleep(POLL_SECONDS)


def tend(client, aid, name, say):
    """Bring one activation to running if it is enabled and not there yet;
    the final detail and the number of restarts issued."""
    detail = read(client, aid)
    label = f"{name} (id {aid})"
    if not detail.get("is_enabled"):
        say(f"{label}: disabled, left alone")
        return detail, 0
    status = detail.get("status")
    if status == "running":
        say(f"{label}: running")
        return detail, 0
    if status in IN_FLIGHT:
        say(f"{label}: {status}, waiting for it")
        detail = settle(
            client,
            aid,
            detail.get("restart_count"),
            time.monotonic() + START_WAIT_SECONDS,
        )
    restarts = 0
    while detail.get("status") != "running" and restarts < RESTARTS:
        restarts += 1
        say(f"{label}: {detail.get('status')}, restart {restarts} of {RESTARTS}")
        before = detail.get("restart_count")
        client.request("POST", f"/api/eda/v1/activations/{aid}/restart/", body={})
        time.sleep(POLL_SECONDS)
        detail = settle(client, aid, before, time.monotonic() + START_WAIT_SECONDS)
    if detail.get("status") == "running":
        say(f"{label}: running after {restarts} restart(s)")
    else:
        say(f"{label}: still {detail.get('status')} after {restarts} restart(s)")
    return detail, restarts


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="activation_restart",
        description="Restart, one at a time, every FlashBlade activation that is "
        "enabled and not running, once the EDA API answers.",
    )
    parser.add_argument(
        "--url",
        default="",
        help="the platform gateway, e.g. https://aap.example.com (default: "
        f"{audit.ENV_URL[0]} or {audit.ENV_URL[1]} in the environment)",
    )
    parser.add_argument(
        "--rulebooks",
        default=str(audit.RULEBOOK_DIR),
        metavar="DIR",
        help="the platform rulebook directory whose files name the activations "
        "(default extensions/eda/rulebooks/platform)",
    )
    parser.add_argument(
        "--wait-minutes",
        type=float,
        default=15,
        metavar="N",
        help="how long to wait for the EDA API to answer (default 15)",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="do not verify the TLS certificate (default: AAP_VALIDATE_CERTS or "
        "CONTROLLER_VERIFY_SSL false in the environment)",
    )
    args = parser.parse_args(argv)

    token = os.environ.get(audit.ENV_TOKEN, "")
    if not token:
        parser.error(f"set {audit.ENV_TOKEN} in the environment")
    url = args.url or audit.env_url()
    if not url:
        parser.error(f"give --url or set {audit.ENV_URL[0]} in the environment")
    names = audit.expected_activation_names(args.rulebooks)
    if not names:
        parser.error(f"no platform rulebook under {args.rulebooks}")

    def say(text):
        print(text, flush=True)

    client = audit.Client(url, token, insecure=args.insecure or audit.env_insecure())
    if not wait_for_api(client, args.wait_minutes, say):
        return 1

    wrong = 0
    restarted = 0
    for name in names:
        try:
            found = client.activations_named(name)
        except audit.AuditError as exc:
            say(f"{name}: {exc}")
            wrong += 1
            continue
        if not found:
            say(f"{name}: not on the controller")
            continue
        for summary in sorted(found, key=lambda a: a["id"]):
            try:
                detail, restarts = tend(client, summary["id"], name, say)
            except audit.AuditError as exc:
                say(f"{name} (id {summary['id']}): {exc}")
                wrong += 1
                continue
            if restarts:
                restarted += 1
                time.sleep(GAP_SECONDS)
            if detail.get("is_enabled") and detail.get("status") != "running":
                wrong += 1
    say(
        f"activation_restart: {len(names)} name(s), {restarted} restarted, "
        f"{wrong} not running"
    )
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
