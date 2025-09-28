define compile_deps
	pip-compile --generate-hashes $(1) --output-file=requirements.txt pyproject.toml
	pip-compile --extra=test --generate-hashes $(1) --output-file=requirements-test.txt pyproject.toml
	# Period is converted to dash during pip-compile. This is a workaround by reverting it back
	# so that Renovate can include the updates correctly for ruamel.yaml package.
	for req_file in requirements.txt requirements-test.txt; do \
		sed -i "s/ruamel-yaml-clib/ruamel.yaml.clib/" $$req_file; \
		sed -i "s/ruamel-yaml/ruamel.yaml/" $$req_file; \
	done
endef

.PHONY: deps/compile deps/upgrade

deps/compile:
	$(call compile_deps)

deps/upgrade:
	$(call compile_deps,--upgrade)


.PHONY: venv/create venv/remove venv/recreate

venv/create:
	python3.12 -m venv --upgrade-deps .venv
	.venv/bin/python3 -m pip install -r requirements-test.txt
	.venv/bin/python3 -m pip install pip-tools

venv/remove:
	rm -rf .venv

venv/recreate: venv/remove venv/create
