# Developer entry points. `make check` is what CI runs.
PY ?= python3
PLUGINS = extensions/eda/plugins/event_source plugins/modules extensions/eda/plugins/event_filter
# the developer tools under tools/ (the Prometheus rule generator, the
# platform rulebook generator, the activation audit, the orchestrator
# linter and sync) are linted, not shipped
TOOLS = tools
# playbooks only: tests/replay_*.yml are rulebooks, not playbooks
PLAYBOOKS = $(wildcard playbooks/*.yml) $(filter-out $(wildcard tests/replay_*.yml),$(wildcard tests/*.yml))
# the collection the repository root builds into (galaxy.yml)
COLLECTION = sdodsley-flashblade_eda

.PHONY: check lint lint-ao test syntax deps collection

# Python requirements, the two collections the rulebooks and playbooks use,
# then this repository as the collection sdodsley.flashblade_eda, so that a
# fresh checkout resolves the rulebooks' FQCN sources
deps:
	$(PY) -m pip install -r requirements.txt
	ansible-galaxy collection install -r requirements.yml
	$(MAKE) collection

# build the collection into build/ and install the tarball (with its
# dependencies, when they are not already installed) under ./collections,
# which env.sh puts first on ANSIBLE_COLLECTIONS_PATH
collection:
	rm -f build/$(COLLECTION)-*.tar.gz
	mkdir -p build collections
	ansible-galaxy collection build --output-path build .
	ansible-galaxy collection install --force -p collections build/$(COLLECTION)-*.tar.gz

lint:
	black --check $(PLUGINS) $(TOOLS) slack $(wildcard tests/*.py) tests/unit
	pyflakes $(PLUGINS) $(TOOLS) slack $(wildcard tests/*.py) tests/unit
	yamllint -s .

# the automation orchestrator workflow definitions (orchestrator/README.md)
# and the tools that generate, audit, lint and sync the platform side;
# standard library plus PyYAML, so it needs no collections
lint-ao:
	black --check $(TOOLS)
	pyflakes $(TOOLS)
	$(PY) tools/ao_lint.py orchestrator/*.json --allowed-templates orchestrator/job_templates.txt

test:
	$(PY) -m pytest -q tests/unit

syntax:
	# the playbooks take their host from the rulebook (hosts: "{{ target_array }}")
	# the collection this tree builds (make collection) must resolve, since the playbooks name its sdodsley.flashblade_eda.* modules; env.sh does the same for run.sh
	ANSIBLE_COLLECTIONS_PATH=$(CURDIR)/collections:$$HOME/.ansible/collections ANSIBLE_LOCALHOST_WARNING=False ansible-playbook -i localhost, -e target_array=localhost --syntax-check $(PLAYBOOKS)

check: lint lint-ao test syntax

# container image for standalone ansible-rulebook runs and EDA controller
# activations (decision-environment/Containerfile); builds and installs
# the collection; not built in CI
.PHONY: de
de:
	podman build -t flashblade-eda-de -f decision-environment/Containerfile .
