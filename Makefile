.PHONY: compile/requirements
compile/requirements:
	pip-compile --generate-hashes --output-file=requirements.txt pyproject.toml
	pip-compile --extra=test --generate-hashes --output-file=requirements-test.txt pyproject.toml
	# Period is converted to dash during pip-compile. This is a workaround by reverting it back
	# so that Renovate can include the updates correctly for ruamel.yaml package.
	for req_file in requirements.txt requirements-test.txt; do \
		sed -i "s/ruamel-yaml-clib/ruamel.yaml.clib/" $$req_file; \
		sed -i "s/ruamel-yaml/ruamel.yaml/" $$req_file; \
	done
