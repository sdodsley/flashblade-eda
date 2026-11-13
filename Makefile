# Developer entry points. `make check` is what CI runs.
PY ?= python3
PLUGINS = extensions/eda/plugins/event_source plugins/modules
# the developer tools (the Prometheus rule generator) are linted, not shipped
TOOLS = tools
# playbooks only: tests/replay_*.yml are rulebooks, not playbooks
PLAYBOOKS = $(wildcard playbooks/*.yml) $(filter-out $(wildcard tests/replay_*.yml),$(wildcard tests/*.yml))
# the collection the repository root builds into (galaxy.yml)
COLLECTION = sdodsley-flashblade_eda

.PHONY: check lint test syntax deps collection

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
	black --check $(PLUGINS) $(TOOLS) $(wildcard tests/*.py) tests/unit
	pyflakes $(PLUGINS) $(TOOLS) $(wildcard tests/*.py) tests/unit
	yamllint -s .

test:
	$(PY) -m pytest -q tests/unit

syntax:
	# the playbooks take their host from the rulebook (hosts: "{{ target_array }}")
	# the collection this tree builds (make collection) must resolve, since the playbooks name its sdodsley.flashblade_eda.* modules; env.sh does the same for run.sh
	ANSIBLE_COLLECTIONS_PATH=$(CURDIR)/collections:$$HOME/.ansible/collections ANSIBLE_LOCALHOST_WARNING=False ansible-playbook -i localhost, -e target_array=localhost --syntax-check $(PLAYBOOKS)

check: lint test syntax
