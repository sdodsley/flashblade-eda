# Copyright 2026 Everpure, Inc.
# SPDX-License-Identifier: Apache-2.0
"""The committed platform rulebooks under extensions/eda/rulebooks/platform/
are exactly what tools/platform_rulebooks.py generates from the standalone
rulebooks beside them, housekeeping.yml (tick-only: the controller's
schedules replace it) has no platform file, every job template they name is
listed in orchestrator/job_templates.txt, and the generated form keeps the
contract the generator documents (FQCN sources, no webhook-only or tick-only
ruleset, no run_playbook, no template-carried extra var, one template per
playbook)."""

import importlib.util
import pathlib
import shutil

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "tools" / "platform_rulebooks.py"
RULEBOOKS = ROOT / "extensions" / "eda" / "rulebooks"
PLATFORM = RULEBOOKS / "platform"
PLAYBOOKS = ROOT / "playbooks"
TEMPLATES = ROOT / "orchestrator" / "job_templates.txt"
STANDALONE = sorted(p.name for p in RULEBOOKS.glob("*.yml"))
# the standalone rulebooks whose every ruleset is tick-only or webhook-only,
# so that nothing of them runs as an activation
NO_PLATFORM_FILE = ["housekeeping.yml"]
PLATFORM_NAMES = [name for name in STANDALONE if name not in NO_PLATFORM_FILE]
HEADER_LINES = [
    "---",
    "# Copyright 2026 Everpure, Inc.",
    "# SPDX-License-Identifier: Apache-2.0",
]


def _load_generator():
    spec = importlib.util.spec_from_file_location("platform_rulebooks", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator():
    return _load_generator()


@pytest.fixture(scope="module")
def generated(generator):
    return generator.generate(RULEBOOKS)


def _allowed_templates():
    names = set()
    for line in TEMPLATES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.add(line)
    return names


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _actions(doc):
    for ruleset in doc:
        for rule in ruleset.get("rules", []):
            yield from rule.get("actions") or [rule.get("action", {})]


def test_every_standalone_rulebook_but_housekeeping_has_one_platform_file(generated):
    assert sorted(generated) == PLATFORM_NAMES
    assert sorted(p.name for p in PLATFORM.glob("*.yml")) == PLATFORM_NAMES
    assert len(PLATFORM_NAMES) == 15


@pytest.mark.parametrize("name", NO_PLATFORM_FILE)
def test_a_tick_only_rulebook_produces_no_platform_file(name, generator, generated):
    doc = _load(RULEBOOKS / name)
    assert all(generator.tick_only(ruleset) for ruleset in doc)
    assert generator.convert(doc) == []
    assert name not in generated
    assert not (PLATFORM / name).exists()


@pytest.mark.parametrize("name", PLATFORM_NAMES)
def test_committed_platform_file_equals_the_generators_output(name, generated):
    committed = (PLATFORM / name).read_text(encoding="utf-8")
    assert committed == generated[name], (
        f"platform/{name} differs from the generator's output; "
        "run python tools/platform_rulebooks.py"
    )


@pytest.mark.parametrize("name", PLATFORM_NAMES)
def test_platform_file_starts_with_the_licence_and_names_its_source(name):
    lines = (PLATFORM / name).read_text(encoding="utf-8").splitlines()
    assert lines[:3] == HEADER_LINES
    header = "\n".join(lines[3:20])
    assert "tools/platform_rulebooks.py" in header
    assert f"extensions/eda/rulebooks/{name}" in header


@pytest.mark.parametrize("name", PLATFORM_NAMES)
def test_platform_file_keeps_the_generators_contract(name, generator):
    doc = _load(PLATFORM / name)
    standalone = _load(RULEBOOKS / name)
    kept = [rs for rs in standalone if not generator.platform_omits(rs)]
    assert [rs["name"] for rs in doc] == [rs["name"] for rs in kept]
    for ruleset in doc:
        for entry in ruleset["sources"]:
            source = generator.source_type(entry)
            assert source != generator.WEBHOOK_SOURCE, f"{name}: webhook source kept"
            assert source != generator.TICK_SOURCE, f"{name}: tick source kept"
            assert source not in generator.BARE_SOURCES, f"{name}: bare {source}"
            if source.startswith("sdodsley."):
                assert source in generator.SOURCE_FQCN.values(), f"{name}: {source}"
    for action in _actions(doc):
        assert "run_playbook" not in action, f"{name}: run_playbook left"
        job = action.get("run_job_template")
        if job is None:
            continue
        assert job["name"] in generator.PLAYBOOK_TEMPLATES.values(), job["name"]
        assert job["organization"] == generator.ORGANIZATION
        extra_vars = job.get("job_args", {}).get("extra_vars", {})
        carried = set(extra_vars) & set(generator.TEMPLATE_VARS)
        assert not carried, f"{name}: {job['name']} passes {sorted(carried)}"


def test_every_run_job_template_name_is_an_allowed_template():
    assert TEMPLATES.exists(), f"{TEMPLATES.relative_to(ROOT)} is missing"
    allowed = _allowed_templates()
    used = set()
    for path in sorted(PLATFORM.glob("*.yml")):
        for action in _actions(_load(path)):
            job = action.get("run_job_template")
            if job:
                used.add(job["name"])
    assert used, "no run_job_template action in the platform rulebooks"
    assert used <= allowed, f"not in job_templates.txt: {sorted(used - allowed)}"


def test_one_template_per_playbook_and_every_run_playbook_is_mapped(generator):
    playbooks = {f"playbooks/{p.name}" for p in PLAYBOOKS.glob("*.yml")}
    assert set(generator.PLAYBOOK_TEMPLATES) == playbooks
    names = list(generator.PLAYBOOK_TEMPLATES.values())
    assert len(set(names)) == len(names), "two playbooks share a template name"
    for template in names:
        assert template.startswith("FlashBlade - "), template
    for name in STANDALONE:
        for action in _actions(_load(RULEBOOKS / name)):
            run = action.get("run_playbook")
            if run:
                assert run["name"] in generator.PLAYBOOK_TEMPLATES, run["name"]


def test_rules_survive_the_conversion_unchanged_but_for_the_action(generator):
    """Everything on a rule except its action (name, condition, throttle) is
    the standalone rule's, an action that is not run_playbook is untouched,
    and the extra_vars that are passed are the same minus the template's."""
    for name in PLATFORM_NAMES:
        standalone = _load(RULEBOOKS / name)
        platform = _load(PLATFORM / name)
        kept = [rs for rs in standalone if not generator.platform_omits(rs)]
        assert len(kept) == len(platform)
        for before, after in zip(kept, platform):
            assert len(before["rules"]) == len(after["rules"])
            for rule_before, rule_after in zip(before["rules"], after["rules"]):
                for key, value in rule_before.items():
                    if key not in ("action", "actions"):
                        assert (
                            rule_after[key] == value
                        ), f"{name}: {rule_before['name']}"
                befores = rule_before.get("actions") or [rule_before["action"]]
                afters = rule_after.get("actions") or [rule_after["action"]]
                assert len(befores) == len(afters), f"{name}: {rule_before['name']}"
                for act_before, act_after in zip(befores, afters):
                    run = act_before.get("run_playbook")
                    if run is None:
                        assert act_after == act_before, f"{name}: {rule_before['name']}"
                        continue
                    job = act_after["run_job_template"]
                    expected = {
                        k: v
                        for k, v in (run.get("extra_vars") or {}).items()
                        if k not in generator.TEMPLATE_VARS
                    }
                    assert job.get("job_args", {}).get("extra_vars", {}) == expected
                    assert job["name"] == generator.PLAYBOOK_TEMPLATES[run["name"]]


def test_a_bare_source_name_is_refused_and_an_fqcn_passes_through(generator):
    assert set(generator.BARE_SOURCES) == {
        "purefb_events",
        "purefb_syslog",
        "purefb_object_audit",
    }
    assert all(key == value for key, value in generator.SOURCE_FQCN.items())
    fqcn = "sdodsley.flashblade_eda.purefb_events"
    entry = {fqcn: {"arrays": "{{ purefb_arrays }}"}, "filters": []}
    assert generator.convert_source(entry) == entry
    webhook = {generator.WEBHOOK_SOURCE: {"port": 5000}}
    assert generator.convert_source(webhook) == webhook
    with pytest.raises(ValueError, match="purefb_events must be named by its FQCN"):
        generator.convert_source({"purefb_events": {"arrays": []}})


def test_the_action_conversion_refuses_what_has_no_platform_form(generator):
    with pytest.raises(ValueError, match="no job template mapped"):
        generator.convert_action({"run_playbook": {"name": "playbooks/other.yml"}})
    with pytest.raises(ValueError, match="no platform form"):
        generator.convert_action(
            {"run_playbook": {"name": "playbooks/notify.yml", "verbosity": 1}}
        )
    # the template-carried vars are dropped; nothing left means no job_args
    only_template_vars = {
        "run_playbook": {
            "name": "playbooks/notify.yml",
            "extra_vars": {k: "/x" for k in generator.TEMPLATE_VARS},
        }
    }
    assert generator.convert_action(only_template_vars) == {
        "run_job_template": {
            "name": "FlashBlade - notify",
            "organization": "{{ aap_organization }}",
        }
    }
    debug = {"debug": {"msg": "x"}}
    assert generator.convert_action(debug) is debug


def test_check_mode_is_clean_on_the_committed_tree_and_flags_drift(
    generator, tmp_path, capsys
):
    assert generator.main(["--check", "--rulebooks", str(RULEBOOKS)]) == 0
    assert capsys.readouterr().out == ""
    work = tmp_path / "rulebooks"
    shutil.copytree(RULEBOOKS, work)
    (work / "platform" / "alerts.yml").write_text("---\n[]\n", encoding="utf-8")
    (work / "platform" / "housekeeping.yml").write_text("---\n[]\n", encoding="utf-8")
    assert generator.main(["--check", "--rulebooks", str(work)]) == 1
    out = capsys.readouterr().out
    assert "alerts.yml: differs from the generator's output" in out
    assert "housekeeping.yml: no standalone rulebook generates it" in out
    # without --check the stale file is rewritten and the orphan is reported
    assert generator.main(["--rulebooks", str(work)]) == 1
    out = capsys.readouterr().out
    assert "wrote" in out and "alerts.yml" in out
    assert (work / "platform" / "alerts.yml").read_text(encoding="utf-8") == (
        PLATFORM / "alerts.yml"
    ).read_text(encoding="utf-8")
    assert "housekeeping.yml: no standalone rulebook generates it; remove it" in out
