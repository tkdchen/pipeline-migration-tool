
REQUIREMENTS_FILES = requirements.in requirements-tests.in

.PHONY: requirements
requirements:
	pip-compile --generate-hashes requirements.in
	pip-compile --generate-hashes requirements-tests.in
