# Pipeline Migration Tool

Pipeline migration tool is command line tool applying migrations for Konflux pipelines. It also
allows to modify Konflux build pipelines locally.

## Installation

Install with pipx from the main branch:

```bash
pipx install git+https://github.com/konflux-ci/pipeline-migration-tool
```

Or, choose a version from [releases], for example:

```bash
pipx install https://github.com/konflux-ci/pipeline-migration-tool/archive/refs/tags/v0.5.0.tar.gz
```


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

### Manual task bundles updates

Use `--new-bundle` argument to do manual bundles updates:

```bash
pmt migrate \
--new-bundle quay.io/konflux-ci/tekton-catalog/task-push-dockerfile-oci-ta:0.1@sha256:08bba4a659ecd48f871bef00b80af58954e5a09fcbb28a1783ddd640c4f6535e \
--new-bundle quay.io/konflux-ci/tekton-catalog/task-init@sha256:4072de81ade0a75ad1eaa5449a7ff02bba84757064549a81b48c28fab3aeca59
```

`--new-bundle` accepts a full bundle reference with both tag and digest.

By default, `migrate` searches Pipeline/PipelineRun YAML files from directory `.tekton/`. Alternatively, use `--pipeline-file` to specify a specific one.

### Add a task to build pipeline with `add-task`

Sub-command `add-task` provides rich options to add a task via bundle reference to build pipelines in local
Component repositories. Let's take the task `sast-coverity-check` as an example to see a few
command usages:

> [!NOTE]
> Tasks are added via bundle reference. For `quay.io`, you can provide just a tag, and the tool will automatically resolve the digest.
> For all other registries, a full reference (tag + digest) is required.

* Add task using a tag (digest is resolved automatically for quay.io):

  ```bash
  pmt add-task quay.io/konflux-ci/tekton-catalog/task-sast-coverity-check:0.1
  ```

* Add task with latest bundle to pipelines from inside a repository:

  ```bash
  pmt add-task quay.io/konflux-ci/tekton-catalog/task-sast-coverity-check:0.1@sha256:...
  ```

  where `./.tekton/` is the default location to search pipelines.

* Add task to multiple locations:

  ```bash
  pmt add-task quay.io/konflux-ci/tekton-catalog/task-sast-coverity-check:0.1@sha256:... \
    /path/to/repo1/pipeline.yaml /path/to/repo2/pipeline-run.yaml ...
  ```

* Specify an alternative name for the task configured in the pipeline:

  ```bash
  pmt add-task quay.io/konflux-ci/tekton-catalog/task-sast-coverity-check:0.1@sha256:... \
    /path/to/repo1/pipeline.yaml /path/to/repo2/pipeline-run.yaml ... \
    --pipeline-task-name sast-coverity-check
  ```

Get more information by `pmt add-task -h`

### To modify Konflux pipelines with `modify`

Sub-command `modify` provides rich options to modify existing pipeline/pipeline runs YAML files,
mainly for automatic migrations.
This command is designed to do as minimal as possible changes to the file,
making the minimal git diff output, compared for example with `yq -i` command that may change
the structure of the whole YAML file.

pipeline-migration-tool (`pmt` command) is configured in Konflux Mintmaker to allow usage in migrations.

* Example of adding new parameter:

with yq (discouraged):

```bash
  yq -i "(.spec.tasks[] | select(.name == \"sast-coverity-check\")).params += \
    [{\"name\": \"image-url\", \"value\": \"$image_url_value\"}]" "$pipeline_file"
```

the same with `pmt modify`:

```bash
  pmt modify -f "$pipeline_file" task sast-coverity-check add-param image-url "$image_url_value"
```

Get more information about supported resources by `pmt modify -h`, and supported commands
for the given resource by `pmt modify RESOURCE -h` (for example `pmt modify task -h`).

#### Unsupported resource?

When resource you need is not supported by `pmt modify` you can use `generic` subcommand
which processes raw YAML text without semantic validation but tries to keep minimal changes
done to the YAML file.

It's not recommended to use this subcommand if specific resource subcommand exists.

Sub-command `generic` supports following operations: `insert`, `replace`, and `remove`.

Each operation requires a YAML path to the target item.
Yaml path is sequence of indexes (compatible with `yq`'s `path` function):

```yaml
- spec
- tasks
- 5
```

Also singleline notation using YAML flow style can be used:`["spec", "tasks", 5]`.
Item to be updated must be sequence or map type.

Example using yq:
```bash
    pmt modify \\
      -f .tekton/pr.yaml \\
      generic remove \\
      "$(yq '.spec.pipelineSpec.tasks[] | select(.name == "prefetch-dependencies") | \\
         path' .tekton/pr.yaml)"
```

#### Known issues

Subcommand `modify` has following known issues

* Indentation of inline comment may not be preserved when value on the same line us updated
Example when value change:

```yaml
key: value  # comment
```

```yaml
key: replaced-value      # comment
```

* YAML Flow style has limited support, to ensure safe modification,
 flow style will be regenerated to block style on affected keys.

For example, adding item `{"item": "test"}` into flow style list:

```yaml
---
start:
  flow: [{item: one, ...}, {item: two, ...}]
```

will result into:

```yaml
---
start:
  flow:
  - {item: one, ...}
  - {item: two, ...}
  - item: test
```

## Development environment management

* Create a virtual environment: `make venv/create`
* Re-create the virtual environment: `make venv/recreate`
* Update requirements after adding dependencies:
  ```bash
  source .venv/bin/activate
  make deps/compile
  ```
* Upgrade dependencies:
  ```bash
  source .venv/bin/activate
  make deps/upgrade
  ```

> [!NOTE]
> If you create a virtual environment by yourself, please ensure create it with
> python3.12 explicitly: `python3.12 -m venv .venv`
>
> When contributing dependency changes, open pull requests for adding and
> upgrading dependencies separately.

## Run tests

```bash
make venv/create
source .venv/bin/activate
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
./hack/make-release.sh make_release_for_review version issue-key
# Once pull request gets approved, it can be either merged via GitHub web UI or the following step
./hack/make-release.sh merge_and_publish_release version
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


[releases]: https://github.com/konflux-ci/pipeline-migration-tool/releases
