.PHONY: compile/requirements
compile/requirements:
	pip-compile --generate-hashes --output-file=requirements.txt pyproject.toml

.PHONY: compile/requirements-test
compile/requirements-test:
	pip-compile --extra=test --generate-hashes --output-file=requirements-test.txt pyproject.toml