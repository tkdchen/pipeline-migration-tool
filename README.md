# Pipeline Migration Tool

A migration tool does migrations for Konflux pipelines.

## Run tests

```bash
python3 -m venv venv
source ./venv/bin/activate
python3 -m pip install -r requirements-test.txt
tox
```

## Local test by running the migration tool

```bash
python3 -m venv venv
source ./venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m pip install -e .

# Log into a registry
podman login quay.io

# Push sample task bundles to your image repositories
# For example, the following set will result in bundles like quay.io/account_name/task-clone
export IMAGE_NS=quay.io/account_name

# Enable local test in order to work with images from arbitrary image organization.
export PMT_LOCAL_TEST=on

# Specify an alternative registry authentication file
export REGISTRY_AUTH_JSON=path/to/auth.json

# build and push sample task bundles task-clone and task-tests.
bash ./hack/local-test/run.sh build-and-push

# Make sure the image repositories are public, especially if you did not create them before running the above command.
# Sample Renovate upgrades data is written into file /tmp/pmt-upgrades-data.txt

# Run the migration tool
pipeline-migration-tool -u "$(cat /tmp/pmt-upgrades-data.txt)"

# To remove all pushed task bundles from the remote registry
# bash ./hack/local-test/run.sh remove-task-bundles
```

## License

Copyright 2024.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
