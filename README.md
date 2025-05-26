# Pipeline Migration Tool

A migration tool does migrations for Konflux pipelines.

## Run tests

```bash
python3 -m venv venv
source ./venv/bin/activate
python3 -m pip install -r requirements-test.txt
tox
```

## Integration test

pipeline-migration-tool relies on task bundles are annotated and migrations are attached properly.
This integration test sets up a testing environment, inside which tasks are built and pushed by the
`build-and-push.sh` script.

Prerequisite:

- A local clone of [konflux-ci/build-definitions](https://github.com/konflux-ci/build-definitions) 
  and checkout to `main` branch.
- Create public image repositories `task-clone` and `task-lint` under specified `QUAY_NAMESPACE`.
- Log into Quay.io in order to make `tkn-bundle-push` work.

For different test scenarios, the task bundles can be customized by setting `RECIPE` environment
variable for `hack/integration-test/setup.sh`. A recipe is a multi-lines text and each line consists
of three fields in order, task name, task version, and marker indicating whether the task bundle
build should have a migration. For example:

```bash
RECIPE="
clone 0.3 M
lint 0.3 -
"
```

Example steps to run the test:

```bash
python3 -m venv venv
source ./venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m pip install -e .

git checkout -b <test branch>  # setup.sh commits changes to the repo

# Empty the image repositories of task-clone and task-lint

BUILD_DEFS_REPO="<absolute path to build-definitions>" \
QUAY_NAMESPACE="<quay namespace passed to build-definitions/hack/build-and-push.sh>" \
./hack/integration-test/setup.sh

cd ./hack/integration-test/app
PMT_LOCAL_TEST=1 pipeline-migration-tool migrate -u "$(cat /tmp/pmt-test-upgrades.txt)"

# Check if the tool works as expected.
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
