# Pipeline Migration Tool

Pipeline migration tool is command line tool applying migrations for Konflux pipelines. It also
allows to modify Konflux build pipelines locally.

## Commands

### To apply migrations with `migrate`

Applying migrations is the major feature of pipeline-migration-tool. It auto-discovers migrations
for given task bundle upgrades and applies found migrations to build pipelines. The pipeline can be
either included in a `PipelineRun` definition as `spec.pipelineSpec` or a single `Pipeline`
defintion.

To make it work, migrations must be attached to corresponding task bundles as OCI artifacts,
typically with `oras attach`. Let's go deep dive a bit.

* If a task bundle has a migration, it must be annotated with `dev.konflux-ci.task.has-migration: true`
* A migration must be attached to the bundle with artifact type `text/x-shellscript` and annotated
  with `dev.konflux-ci.task.is-migration: true`.
* A task bundle has only one migration.
* A migration is written as a normal Bash script. Generally, it invokes `yq` to modify the pipelines.

Document [Task Migration]
(https://github.com/konflux-ci/build-definitions/?tab=readme-ov-file#task-migration) of
konflux-ci/build-definitions describes the migrations in detail. build-definitions provides a rich
tool chain and CI for creation, validation and ensuring migrations are available to
pipeline-migration-tool.

pipeline-migration-tool is configured in Konflux Mintmaker as a Renovate post-upgrade command and
Renovate is responsible for invoke migration tool properly. Then, in general, it is unnecessary for
Konflux users to run `migrate` by themselves.

To apply migrations, run command:

```bash
cd path/to/repo
pmt migrate -u '<upgrades>'
```

`upgrades` is a JSON string encoded from a list of mappings. Each mapping includes data for a
single task bundle upgrade, for example:

```jsonc
[
  {
    "depName": "quay.io/konflux-ci/tekton-catalog/task-init",
    "currentValue": "0.1",
    "currentDigest": "sha256:...",
    "newValue": "0.1",
    "newDigest": "sha256:...",
    "packageFile": ".tekton/component-name-pull.yaml",
    "parentDir": ".tekton",
    "depTypes": ["tekton-bundle"]
  },
  // ...
]
```

The field names map to the [Renovate template fields]
(https://docs.renovatebot.com/templates/#other-available-fields) directly:

* `depName`: `{{depName}}`
* `currentValue`: `{{currentValue}}`
* `currentDigest`: `{{currentDigest}}`
* `newValue`: `{{newValue}}`
* `newDigest`: `{{newDigest}}`
* `packageFile`: `{{packageFile}}`
* `parentDir`: `{{parentDir}}`
* `depTypes`: `{{depTypes}}`

To generate the list, handlebars built-in `each` helper of Renovate is used.

### Add a Konflux task to build pipeline with `add-task`

Sub-command `add-task` provides rich options to add a Konflux task to build pipelines in local
Component repositories. Let's take the task `sast-coverity-check` as an example to see a few
command usages:

* Add task with latest bundle to pipelines from inside a repository:

  ```bash
  pmt add-task sast-coverity-check
  ```

  where `./.tekton/` is the default location to search pipelines.

* Add task to multiple locations:

  ```bash
  pmt add-task sast-coverity-check \
    /path/to/repo1/pipeline.yaml /path/to/repo2/pipeline-run.yaml ...
  ```

* Specify alternative task bundle explicitly:

  ```bash
  pmt add-task --bundle-ref quay.io/konflux-ci/tekton-catalog/task-sast-coverity-check:0.1@sha256:... \
    sast-coverity-check \
    /path/to/repo1/pipeline.yaml /path/to/repo2/pipeline-run.yaml ...
  ```

Get more information by `pmt add-task -h`

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
PMT_LOCAL_TEST=1 pmt migrate -u "$(cat /tmp/pmt-test-upgrades.txt)"

# Check if the tool works as expected.
```

## Make a release

### Versioning

Releases are versioned in the form of `major.minor.patch`.

- Major version remains 0.
- Minor version is incremented when new feature and backward incompatible changes are introduced.
- Patch version is incremented when backward compatible changes are introduced.

For example:

- Code refactor: `++patch`
- Non-code change: `++patch`
- Add a new command line argument: `++minor`
- A bug fix that does not introduce changes to the original behavior and command line interface:
  `++patch`, otherwise `++minor`

### Steps

Follow these steps to make and publish a release. Here, version `0.4.2` is used as an example.

- Synchronize with `main` branch
- Checkout to a release branch `release-0.4.2`
- Update version in `src/pipeline_migration/__init__.py`
- Commit the change with title `Release 0.4.2` and open a pull request.
- Review and merge the pull request
- Go to https://github.com/konflux-ci/pipeline-migration-tool/releases/new
- Create a new tag `v0.4.2`
- Ensure the target is `main` branch
- Click button `Generate release notes` and refine the release notes if necessary
- Check `Set as the latest release`
- Click button `Publish release`

Done 🎉

Then, Renovate will send an update pull request to [konflux-ci/mintmaker-renovate-image]
(https://github.com/konflux-ci/mintmaker-renovate-image) automatically in order to upgrade
pipeline-migration-tool to the new version.

Post-release steps:

- It is highly recommend to link the Renovate update pull request to the release story.
- If there is no release story for the new release, it can be optionally linked to the major feature
  or bugfix JIRA issue.
- Open a pull request to MintMaker [Renovate configuration]
  (https://github.com/konflux-ci/mintmaker/blob/main/config/renovate/renovate.json) when necessary
  to use new command line interface.

### Run script alternatively

Executes commands in the following order to help making a release:

```bash
./hack/make-release make_release_for_review version issue-key
# Once pull request gets approved, it can be either merged via GitHub web UI or the following step
./hack/make-release merge_and_publish_release version
```

> [!NOTE]
> If customization is required based on the auto-generated notes, please do manual release creation
  via repository Releases page.

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
