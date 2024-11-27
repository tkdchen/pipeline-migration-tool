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
source ./venv/bin/activate
python3 -m pip install -e .

# build and push sample task bundles
bash ./hack/local-test.sh build-and-push

# Sample Renovate upgrades data is written into file /tmp/pmt-upgrades-data.txt

# Run the migration tool
pipeline-migration-tool -u "$(cat /tmp/pmt-upgrades-data.txt)"

# If needed, run command to remove all task bundles from registry
bash ./hack/local-test.sh remove-task-bundles
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
