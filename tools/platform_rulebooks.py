#!/usr/bin/env python3
# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""Generate the platform rulebooks under extensions/eda/rulebooks/platform/
from the standalone rulebooks in extensions/eda/rulebooks/.

    python tools/platform_rulebooks.py            # write the files
    python tools/platform_rulebooks.py --check    # exit 1 when a file differs

A platform rulebook is the standalone rulebook as an Event-Driven Ansible
controller activation runs it: the same rulesets, sources and rules, except
that (a) every run_playbook action becomes run_job_template naming the job
template aap/seed.yml creates for that playbook (PLAYBOOK_TEMPLATES), in the
organization aap_organization, with the same extra_vars minus alert_log,
request_dir and baseline_dir, which the job templates carry
(TEMPLATE_VARS); (b) rulesets whose only source is ansible.eda.webhook are
left out, because the capacity, containment and maintenance-window
approvals and the git-push baseline apply are event streams or the
controller workflow on the platform, and so are rulesets whose only source
is ansible.eda.tick, because fixed-hour work is a controller schedule there
(aap/seed.yml creates the DR drill, showback and selftest schedules), so
housekeeping.yml produces no platform file; (c) the project's event sources
are named by their collection FQCN (SOURCE_FQCN): the standalone rulebooks
already write sdodsley.flashblade_eda.purefb_events, purefb_syslog and
purefb_object_audit in full, and a bare name is refused, since nothing
resolves it in the decision environment. Comments inside a standalone
rulebook do not survive the round trip; the header of each generated file
says where it comes from. Standard library plus PyYAML; the output is
deterministic, so the committed files are checked against it by
tests/unit/test_platform_rulebooks.py.
"""

import argparse
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
RULEBOOKS = ROOT / "extensions" / "eda" / "rulebooks"
PLATFORM_DIR_NAME = "platform"
GENERATOR = "tools/platform_rulebooks.py"

# every playbook under playbooks/ -> the job template that runs it on the
# platform (aap/seed.yml creates one per playbook, with these names;
# orchestrator/job_templates.txt lists the ones the orchestrator definitions
# and these rulebooks launch). The table mirrors the seed in full; a platform
# rulebook names only the templates the kept rulesets run.
PLAYBOOK_TEMPLATES = {
    "playbooks/alert_runbook.yml": "FlashBlade - alert runbook",
    "playbooks/apply_baseline.yml": "FlashBlade - apply baseline",
    "playbooks/auditor_guard.yml": "FlashBlade - auditor guard",
    "playbooks/canary.yml": "FlashBlade - canary",
    "playbooks/capacity_reclaim.yml": "FlashBlade - capacity reclaim",
    "playbooks/capacity_request.yml": "FlashBlade - capacity request",
    "playbooks/client_diagnose.yml": "FlashBlade - client diagnose",
    "playbooks/dr_drill.yml": "FlashBlade - DR drill",
    "playbooks/flag_alert.yml": "FlashBlade - flag alert",
    "playbooks/grow_fs.yml": "FlashBlade - grow file system",
    "playbooks/identify_led.yml": "FlashBlade - identify LED",
    "playbooks/landing.yml": "FlashBlade - landing",
    "playbooks/lock_on_arrival.yml": "FlashBlade - lock on arrival",
    "playbooks/maintenance_window.yml": "FlashBlade - maintenance window",
    "playbooks/notify.yml": "FlashBlade - notify",
    "playbooks/orchestrator_post.yml": "FlashBlade - orchestrator post",
    "playbooks/posture_revert.yml": "FlashBlade - posture revert",
    "playbooks/qos_cap.yml": "FlashBlade - QoS cap",
    "playbooks/qos_lift.yml": "FlashBlade - QoS lift",
    "playbooks/quarantine_client.yml": "FlashBlade - quarantine client",
    "playbooks/reconcile.yml": "FlashBlade - reconcile",
    "playbooks/release_locks.yml": "FlashBlade - release locks",
    "playbooks/remediate_dns.yml": "FlashBlade - remediate DNS",
    "playbooks/remediate_ntp.yml": "FlashBlade - remediate NTP",
    "playbooks/renew_certificate.yml": "FlashBlade - renew certificate",
    "playbooks/replication_resume.yml": "FlashBlade - replication resume",
    "playbooks/s3_anomaly.yml": "FlashBlade - S3 anomaly",
    "playbooks/s3_approval.yml": "FlashBlade - S3 approval",
    "playbooks/selftest.yml": "FlashBlade - selftest",
    "playbooks/session_check.yml": "FlashBlade - session check",
    "playbooks/showback.yml": "FlashBlade - showback",
    "playbooks/tarpit.yml": "FlashBlade - tarpit",
    "playbooks/telemetry_check.yml": "FlashBlade - telemetry check",
    "playbooks/telemetry_setup.yml": "FlashBlade - telemetry setup",
    "playbooks/user_quota.yml": "FlashBlade - user quota",
}

# this project's event sources, by FQCN in the standalone rulebooks (the
# collection this tree builds, installed by make collection) and by the same
# FQCN on the platform (the collection built into the decision environment):
# the mapping is the identity, and names a source in the platform form only
# through it. A bare name (the key without its collection) is refused.
SOURCE_FQCN = {
    "sdodsley.flashblade_eda.purefb_events": "sdodsley.flashblade_eda.purefb_events",
    "sdodsley.flashblade_eda.purefb_syslog": "sdodsley.flashblade_eda.purefb_syslog",
    "sdodsley.flashblade_eda.purefb_object_audit": (
        "sdodsley.flashblade_eda.purefb_object_audit"
    ),
}
BARE_SOURCES = tuple(fqcn.rsplit(".", 1)[-1] for fqcn in SOURCE_FQCN)

# extra_vars the job templates carry themselves (aap_job_extra_vars)
TEMPLATE_VARS = ("alert_log", "request_dir", "baseline_dir")

WEBHOOK_SOURCE = "ansible.eda.webhook"
TICK_SOURCE = "ansible.eda.tick"
ORGANIZATION = "{{ aap_organization }}"

# keys of a source entry that are not the source's name
SOURCE_ENTRY_KEYS = ("name", "filters")

HEADER = """\
---
# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
#
# Generated by {generator} from
# extensions/eda/rulebooks/{name}; edit that file and regenerate with
#   python {generator}
# The rulesets, sources and rules are the standalone rulebook's, with every
# run_playbook turned into run_job_template against the job template
# aap/seed.yml creates for that playbook (organization aap_organization;
# alert_log, request_dir and baseline_dir come from the templates), the
# project's event sources named by their collection FQCN, and the
# webhook-only and tick-only rulesets left out (event streams, the
# controller workflow and the controller's schedules for the DR drill, the
# showback and the selftest take their place on the platform).
"""


class _Dumper(yaml.SafeDumper):
    """PyYAML with the repository's YAML style: sequences indented under
    their key, no anchors."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)

    def ignore_aliases(self, data):
        return True


def source_type(entry):
    """The source name of one `sources` entry (the key that is not the
    entry's own name or filters)."""
    for key in entry:
        if key not in SOURCE_ENTRY_KEYS:
            return key
    raise ValueError(f"source entry without a source: {entry!r}")


def convert_source(entry):
    """One source entry with the project's sources named by their FQCN; a
    bare name of one of them is refused."""
    out = {}
    for key, value in entry.items():
        if key in BARE_SOURCES:
            raise ValueError(
                f"source {key} must be named by its FQCN "
                f"(sdodsley.flashblade_eda.{key}); nothing resolves a bare name "
                "in the decision environment"
            )
        out[SOURCE_FQCN.get(key, key)] = value
    return out


def convert_action(action):
    """One action with run_playbook turned into run_job_template; any other
    action is returned unchanged."""
    run = action.get("run_playbook")
    if run is None:
        return action
    unknown = set(run) - {"name", "extra_vars"}
    if unknown:
        raise ValueError(
            f"run_playbook {run['name']}: no platform form for {sorted(unknown)}"
        )
    try:
        template = PLAYBOOK_TEMPLATES[run["name"]]
    except KeyError:
        raise ValueError(f"no job template mapped for {run['name']}") from None
    extra_vars = {
        key: value
        for key, value in (run.get("extra_vars") or {}).items()
        if key not in TEMPLATE_VARS
    }
    job = {"name": template, "organization": ORGANIZATION}
    if extra_vars:
        job["job_args"] = {"extra_vars": extra_vars}
    out = {key: value for key, value in action.items() if key != "run_playbook"}
    out["run_job_template"] = job
    return out


def convert_rule(rule):
    out = {}
    for key, value in rule.items():
        if key == "action":
            out[key] = convert_action(value)
        elif key == "actions":
            out[key] = [convert_action(action) for action in value]
        else:
            out[key] = value
    return out


def convert_ruleset(ruleset):
    out = {}
    for key, value in ruleset.items():
        if key == "sources":
            out[key] = [convert_source(entry) for entry in value]
        elif key == "rules":
            out[key] = [convert_rule(rule) for rule in value]
        else:
            out[key] = value
    return out


def _only_source(ruleset, source):
    sources = ruleset.get("sources") or []
    return bool(sources) and all(source_type(entry) == source for entry in sources)


def webhook_only(ruleset):
    return _only_source(ruleset, WEBHOOK_SOURCE)


def tick_only(ruleset):
    return _only_source(ruleset, TICK_SOURCE)


def platform_omits(ruleset):
    """True for a ruleset the platform form leaves out: webhook-only (an
    event stream or the controller workflow there) or tick-only (a
    controller schedule there)."""
    return webhook_only(ruleset) or tick_only(ruleset)


def convert(doc):
    """The platform form of one standalone rulebook document; empty when
    every ruleset is left out (housekeeping.yml)."""
    return [convert_ruleset(rs) for rs in doc if not platform_omits(rs)]


def render(name, doc):
    """The text of the platform rulebook generated from the standalone
    rulebook `name` (its file name)."""
    body = yaml.dump(
        convert(doc),
        Dumper=_Dumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=False,
        width=100000,
    )
    return HEADER.format(generator=GENERATOR, name=name) + body


def generate(rulebooks_dir=RULEBOOKS):
    """{file name: platform rulebook text} for every standalone rulebook in
    the directory that keeps at least one ruleset (its platform/
    subdirectory is not read)."""
    out = {}
    for path in sorted(pathlib.Path(rulebooks_dir).glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        converted = render(path.name, doc)
        if convert(doc):
            out[path.name] = converted
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="platform_rulebooks",
        description="Generate the platform rulebooks from the standalone ones.",
    )
    parser.add_argument(
        "--rulebooks",
        default=str(RULEBOOKS),
        metavar="DIR",
        help="directory of the standalone rulebooks; the platform files go "
        "to its platform/ subdirectory",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="write nothing; exit 1 when a committed file differs or is missing",
    )
    args = parser.parse_args(argv)

    rulebooks = pathlib.Path(args.rulebooks)
    platform = rulebooks / PLATFORM_DIR_NAME
    generated = generate(rulebooks)
    stale = []
    for name, text in generated.items():
        target = platform / name
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == text:
            continue
        stale.append(name)
        if not args.check:
            platform.mkdir(parents=True, exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
    extra = sorted(
        path.name for path in platform.glob("*.yml") if path.name not in generated
    )
    if args.check:
        for name in stale:
            print(f"{platform / name}: differs from the generator's output")
        for name in extra:
            print(f"{platform / name}: no standalone rulebook generates it")
        return 1 if stale or extra else 0
    for name in stale:
        print(f"wrote {platform / name}")
    for name in extra:
        print(f"{platform / name}: no standalone rulebook generates it; remove it")
    return 1 if extra else 0


if __name__ == "__main__":
    sys.exit(main())
