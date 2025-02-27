import os
import itertools
import logging
import subprocess
from copy import deepcopy
from textwrap import dedent
from typing import Any, Final

import responses
import pytest

from pipeline_migration.migrate import (
    ANNOTATION_HAS_MIGRATION,
    ANNOTATION_IS_MIGRATION,
    ANNOTATION_TRUTH_VALUE,
    LinkedMigrationsResolver,
    determine_task_bundle_upgrades_range,
    fetch_migration_file,
    IncorrectMigrationAttachment,
    InvalidRenovateUpgradesData,
    resolve_pipeline,
    TaskBundleMigration,
    TaskBundleUpgrade,
    TaskBundleUpgradesManager,
    SimpleIterationResolver,
)
from pipeline_migration.quay import QuayTagInfo
from pipeline_migration.registry import Container
from pipeline_migration.utils import load_yaml, dump_yaml
from tests.utils import generate_digest


# Tags are listed from the latest to the oldest one.
SAMPLE_TAGS_OF_NS_APP: Final = [
    {"name": "0.3-0c9b02c", "manifest_digest": "sha256:bfc0c3c"},
    {"name": "0.3", "manifest_digest": "sha256:bfc0c3c"},
    {"name": "0.2-23d463f", "manifest_digest": "sha256:2a2c2b7"},
    {"name": "0.1-d4eab53", "manifest_digest": "sha256:52f8b96"},
    {"name": "0.1-b486c47", "manifest_digest": "sha256:9bfc6b9"},
    {"name": "0.1-9dffe5f", "manifest_digest": "sha256:7f8b549"},
    {"name": "0.1-3778abd", "manifest_digest": "sha256:bb6de65"},
    {"name": "0.1-833463f", "manifest_digest": "sha256:69edfd6"},
]

APP_IMAGE_REPO: Final = "reg.io/ns/app"
TASK_BUNDLE_CLONE: Final = "quay.io/konflux-ci/catalog/task-clone"
TASK_BUNDLE_TESTS: Final = "quay.io/konflux-ci/catalog/task-tests"
TASK_BUNDLE_LINT: Final = "quay.io/konflux-ci/catalog/task-lint"


class TestDetermineTaskBundleUpdatesRange:

    @responses.activate
    @pytest.mark.parametrize(
        "task_bundle_upgrade,tags,expected",
        [
            # No tag is found from repository
            [
                TaskBundleUpgrade(
                    dep_name=APP_IMAGE_REPO,
                    current_value="0.1",
                    current_digest="sha256:69edfd6",
                    new_value="0.1",
                    new_digest="sha256:6789012",
                ),
                [],
                [],
            ],
            # The from_task_bundle is not included in the responded tags
            [
                TaskBundleUpgrade(
                    dep_name=APP_IMAGE_REPO,
                    current_value="0.1",
                    current_digest="sha256:1234",
                    new_value="0.1",
                    new_digest="sha256:52f8b96",
                ),
                SAMPLE_TAGS_OF_NS_APP,
                ValueError,
            ],
            # The to_task_bundle is not included in the responded tags
            [
                TaskBundleUpgrade(
                    dep_name=APP_IMAGE_REPO,
                    current_value="0.1",
                    current_digest="sha256:69edfd6",
                    new_value="0.1",
                    new_digest="sha256:6789012",
                ),
                SAMPLE_TAGS_OF_NS_APP,
                ValueError,
            ],
            # Both from_task_bundle and to_task_bundle are not included in the responded tags
            [
                TaskBundleUpgrade(
                    dep_name=APP_IMAGE_REPO,
                    current_value="0.1",
                    current_digest="sha256:1234567",
                    new_value="0.1",
                    new_digest="sha256:9087654",
                ),
                SAMPLE_TAGS_OF_NS_APP,
                ValueError,
            ],
            # range is found
            [
                TaskBundleUpgrade(
                    dep_name=APP_IMAGE_REPO,
                    current_value="0.1",
                    current_digest="sha256:7f8b549",
                    new_value="0.1",
                    new_digest="sha256:52f8b96",
                ),
                SAMPLE_TAGS_OF_NS_APP,
                [
                    QuayTagInfo(name="0.1-d4eab53", manifest_digest="sha256:52f8b96"),
                    QuayTagInfo(name="0.1-b486c47", manifest_digest="sha256:9bfc6b9"),
                ],
            ],
            # range is found across versions
            [
                TaskBundleUpgrade(
                    dep_name=APP_IMAGE_REPO,
                    current_value="0.1",
                    current_digest="sha256:7f8b549",
                    new_value="0.3",
                    new_digest="sha256:bfc0c3c",
                ),
                SAMPLE_TAGS_OF_NS_APP,
                [
                    QuayTagInfo(name="0.3-0c9b02c", manifest_digest="sha256:bfc0c3c"),
                    QuayTagInfo(name="0.2-23d463f", manifest_digest="sha256:2a2c2b7"),
                    QuayTagInfo(name="0.1-d4eab53", manifest_digest="sha256:52f8b96"),
                    QuayTagInfo(name="0.1-b486c47", manifest_digest="sha256:9bfc6b9"),
                ],
            ],
        ],
    )
    def test_determine_the_range(self, task_bundle_upgrade: TaskBundleUpgrade, tags, expected):
        c = Container(task_bundle_upgrade.dep_name)
        responses.add(
            responses.GET,
            f"https://{c.registry}/api/v1/repository/{c.namespace}/{c.repository}/tag/?"
            "page=1&onlyActiveTags=true",
            json={"tags": tags, "page": 1, "has_additional": False},
        )

        if isinstance(expected, list):
            tags_range = determine_task_bundle_upgrades_range(task_bundle_upgrade)
            assert tags_range == expected
        else:
            with pytest.raises(expected):
                determine_task_bundle_upgrades_range(task_bundle_upgrade)


class TestTaskBundleUpgrade:

    def test_get_bundle_strings(self):
        current_digest = generate_digest()
        new_digest = generate_digest()
        upgrade = TaskBundleUpgrade(
            dep_name=APP_IMAGE_REPO,
            current_value="0.1",
            current_digest=current_digest,
            new_value="0.2",
            new_digest=new_digest,
        )
        assert upgrade.current_bundle == f"{APP_IMAGE_REPO}:0.1@{current_digest}"
        assert upgrade.new_bundle == f"{APP_IMAGE_REPO}:0.2@{new_digest}"

    @pytest.mark.parametrize(
        "upgrade,expected",
        [
            [
                TaskBundleUpgrade(
                    dep_name=APP_IMAGE_REPO,
                    current_value="0.1",
                    current_digest=generate_digest(),
                    new_value="0.2",
                    new_digest=generate_digest(),
                ),
                False,
            ],
            [
                TaskBundleUpgrade(
                    dep_name="quay.io/konflux-ci/tester",
                    current_value="0.1",
                    current_digest=generate_digest(),
                    new_value="0.3",
                    new_digest=generate_digest(),
                ),
                True,
            ],
        ],
    )
    def test_if_upgrade_comes_from_a_specific_org(self, upgrade: TaskBundleUpgrade, expected):
        assert upgrade.comes_from_konflux == expected

    @pytest.mark.parametrize(
        "data,expected",
        [
            [
                {
                    "dep_name": "",
                    "current_value": "",
                    "current_digest": "",
                    "new_value": "",
                    "new_digest": "",
                },
                "Image name is empty",
            ],
            [
                {
                    "dep_name": APP_IMAGE_REPO,
                    "current_value": "",
                    "current_digest": "",
                    "new_value": "",
                    "new_digest": "",
                },
                "Both currentValue and currentDigest are empty.",
            ],
            [
                {
                    "dep_name": APP_IMAGE_REPO,
                    "current_value": "0.1",
                    "current_digest": generate_digest(),
                    "new_value": "",
                    "new_digest": "",
                },
                "Both newValue and newDigest are empty.",
            ],
            [
                {
                    "dep_name": APP_IMAGE_REPO,
                    "current_value": "0.1",
                    "current_digest": "sha256:cff6b68a194a",
                    "new_value": "0.1",
                    "new_digest": "sha256:cff6b68a194a",
                },
                "Current and new task bundle are same",
            ],
            [
                {
                    "dep_name": APP_IMAGE_REPO,
                    "current_value": "0.1",
                    "current_digest": "cff6b68a194a",
                    "new_value": "0.2",
                    "new_digest": "sha256:96e797480ac5",
                },
                "Current digest is not a valid digest string",
            ],
            [
                {
                    "dep_name": APP_IMAGE_REPO,
                    "current_value": "0.1",
                    "current_digest": "sha256:cff6b68a194a",
                    "new_value": "0.2",
                    "new_digest": "96e797480ac5",
                },
                "New digest is not a valid digest string",
            ],
        ],
    )
    def test_invalid_upgrade(self, data, expected):
        with pytest.raises(InvalidRenovateUpgradesData, match=expected):
            TaskBundleUpgrade(**data)


class TestResolvePipeline:

    def test_resolve_from_a_pipeline_definition(self, tmp_path):
        pipeline_file = tmp_path / "pl.yaml"
        content = dedent(
            """\
            apiVersion: tekton.dev/v1
            kind: Pipeline
            metadata:
                name: pl
            spec:
                params:
                tasks:
            """
        )
        pipeline_file.write_text(content)
        with resolve_pipeline(pipeline_file):
            pass

    def test_resolve_from_a_pipeline_run_definition(self, tmp_path):
        pipeline_file = tmp_path / "plr.yaml"
        content = dedent(
            """\
            apiVersion: tekton.dev/v1
            kind: PipelineRun
            metadata:
                name: pl
            spec:
                pipelineSpec:
                    params:
                    tasks:
            """
        )
        pipeline_file.write_text(content)
        with resolve_pipeline(pipeline_file):
            pass

    def test_ensure_updates_to_pipeline_are_saved(self, tmp_path):
        pipeline_file = tmp_path / "plr.yaml"
        content = dedent(
            """\
            apiVersion: tekton.dev/v1
            kind: PipelineRun
            metadata:
                name: pl
            spec:
                pipelineSpec:
                    params:
                    tasks:
            """
        )
        pipeline_file.write_text(content)
        with resolve_pipeline(pipeline_file) as f:
            pl = load_yaml(f)
            pl["spec"]["tasks"] = [{"name": "init"}]
            dump_yaml(f, pl)

        plr = load_yaml(pipeline_file)
        assert plr["spec"]["pipelineSpec"]["tasks"] == [{"name": "init"}]

    def test_do_not_handle_pipelineref(self, tmp_path):
        pipeline_file = tmp_path / "plr.yaml"
        content = dedent(
            """\
            apiVersion: tekton.dev/v1
            kind: PipelineRun
            metadata:
                name: plr
            spec:
                pipelineRef:
                    name: pipeline
            """
        )
        pipeline_file.write_text(content)
        with pytest.raises(ValueError, match="PipelineRun definition seems not embedded"):
            with resolve_pipeline(pipeline_file):
                pass

    def test_given_file_is_not_yaml_file(self, tmp_path):
        pipeline_file = tmp_path / "invalid.file"
        pipeline_file.write_text("hello world")
        with pytest.raises(ValueError, match="not a YAML mapping"):
            with resolve_pipeline(pipeline_file):
                pass

    def test_empty_pipeline_run(self, tmp_path):
        pipeline_file = tmp_path / "plr.yaml"
        content = dedent(
            """\
            apiVersion: tekton.dev/v1
            kind: PipelineRun
            metadata:
                name: plr
            spec:
            """
        )
        pipeline_file.write_text(content)
        with pytest.raises(ValueError, match="neither .pipelineSpec nor .pipelineRef field"):
            with resolve_pipeline(pipeline_file):
                pass

    def test_given_file_does_not_have_known_kind(self, tmp_path):
        pipeline_file = tmp_path / "plr.yaml"
        content = dedent(
            """\
            apiVersion: tekton.dev/v1
            spec:
            """
        )
        pipeline_file.write_text(content)
        with pytest.raises(ValueError, match="does not have knownn kind Pipeline or PipelineRun"):
            with resolve_pipeline(pipeline_file):
                pass


RENOVATE_UPGRADES: list[dict[str, Any]] = [
    # for pull request
    {
        "depName": TASK_BUNDLE_CLONE,
        "currentValue": "0.1",
        "currentDigest": "sha256:3a30d8fce9ce",
        "newValue": "0.1",
        "newDigest": "sha256:3356f7c38aea",
        "packageFile": ".tekton/component-a-pull-request.yaml",
        "parentDir": ".tekton/",
        "depTypes": ["tekton-bundle"],
    },
    {
        "depName": TASK_BUNDLE_TESTS,
        "currentValue": "0.1",
        "currentDigest": "sha256:492fb9ae4e7a",
        "newValue": "0.2",
        "newDigest": "sha256:96e797480ac5",
        "packageFile": ".tekton/component-a-pull-request.yaml",
        "parentDir": ".tekton/",
        "depTypes": ["tekton-bundle"],
    },
    {
        "depName": TASK_BUNDLE_LINT,
        "currentValue": "0.1",
        "currentDigest": "sha256:193c17d08e13",
        "newValue": "0.1",
        "newDigest": "sha256:47c9dac9c222",
        "packageFile": ".tekton/component-a-pull-request.yaml",
        "parentDir": ".tekton/",
        "depTypes": ["tekton-bundle"],
    },
    # for push
    {
        "depName": TASK_BUNDLE_CLONE,
        "currentValue": "0.1",
        "currentDigest": "sha256:3a30d8fce9ce",
        "newValue": "0.1",
        "newDigest": "sha256:3356f7c38aea",
        "packageFile": ".tekton/component-a-push.yaml",
        "parentDir": ".tekton/",
        "depTypes": ["tekton-bundle"],
    },
    {
        "depName": TASK_BUNDLE_TESTS,
        "currentValue": "0.1",
        "currentDigest": "sha256:492fb9ae4e7a",
        "newValue": "0.2",
        "newDigest": "sha256:96e797480ac5",
        "packageFile": ".tekton/component-a-push.yaml",
        "parentDir": ".tekton/",
        "depTypes": ["tekton-bundle"],
    },
]


class TestTaskBundleUpgradesManagerCollectUpgrades:

    def setup_method(self, method):
        self.test_upgrades = deepcopy(RENOVATE_UPGRADES)

    def test_collect_upgrades(self):
        manager = TaskBundleUpgradesManager(self.test_upgrades, SimpleIterationResolver)
        assert len(manager._task_bundle_upgrades) == 3

    def test_skip_non_konflux_bundles(self):
        """
        Modify a task bundle for push package file, then check the number of collected upgrades,
        which is less than the number of upgrades of push in the APP_IMAGE_REPO.
        """
        upgrade = [
            upgrade
            for upgrade in self.test_upgrades
            if upgrade["depName"].endswith("-tests")
            and upgrade["packageFile"].endswith("push.yaml")
        ]
        upgrade[0]["depName"] = APP_IMAGE_REPO

        manager = TaskBundleUpgradesManager(self.test_upgrades, SimpleIterationResolver)
        package_files = [
            package_file
            for package_file in manager.package_files
            if package_file.file_path.endswith("push.yaml")
        ]
        assert len(package_files[0].task_bundle_upgrades) == 1

    def test_do_not_include_upgrade_by_tekton_bundle_manager(self):
        for upgrade in self.test_upgrades:
            if upgrade["packageFile"].endswith("push.yaml"):
                upgrade["depTypes"] = ["some-other-renovate-manager"]

        manager = TaskBundleUpgradesManager(self.test_upgrades, SimpleIterationResolver)
        package_files = set([upgrade["packageFile"] for upgrade in self.test_upgrades])
        assert len(package_files) > len(manager._package_file_updates)


class TestFetchMigrationFile:

    def setup_method(self, method):
        self.image_digest = generate_digest()

    def test_fail_if_image_has_tag_or_digest(self):
        with pytest.raises(ValueError, match="should not include digest"):
            fetch_migration_file(f"{APP_IMAGE_REPO}@{self.image_digest}", self.image_digest)

    @responses.activate
    def test_no_referrer_with_expected_artifact_type(self, image_manifest):
        c = Container(APP_IMAGE_REPO)
        c.digest = self.image_digest
        image_manifest["annotations"] = {ANNOTATION_HAS_MIGRATION: ANNOTATION_TRUTH_VALUE}
        responses.get(f"https://{c.manifest_url()}", json=image_manifest)

        referrers = []  # No referrer
        responses.get(
            f"https://{c.referrers_url}?artifactType=text/x-shellscript",
            json={"schemaVersion": 2, "manifests": referrers, "annotations": {}},
        )

        r = fetch_migration_file(APP_IMAGE_REPO, self.image_digest)
        assert r is None

    @responses.activate
    def test_no_referrer_with_migration_annotation(self, oci_referrer_descriptor, image_manifest):
        c = Container(APP_IMAGE_REPO)
        c.digest = self.image_digest
        image_manifest["annotations"] = {ANNOTATION_HAS_MIGRATION: ANNOTATION_TRUTH_VALUE}
        responses.get(f"https://{c.manifest_url()}", json=image_manifest)

        oci_referrer_descriptor["annotations"] = {}
        responses.get(
            f"https://{c.referrers_url}?artifactType=text/x-shellscript",
            json={"schemaVersion": 2, "manifests": [oci_referrer_descriptor], "annotations": {}},
        )

        r = fetch_migration_file(APP_IMAGE_REPO, self.image_digest)
        assert r is None

    @responses.activate
    def test_fail_if_no_single_migration_per_task_bundle(
        self, image_manifest, oci_referrer_descriptor
    ):
        c = Container(APP_IMAGE_REPO)
        c.digest = self.image_digest
        image_manifest["annotations"] = {ANNOTATION_HAS_MIGRATION: ANNOTATION_TRUTH_VALUE}
        responses.get(f"https://{c.manifest_url()}", json=image_manifest)

        referrers = []
        for _ in range(3):
            referrer = deepcopy(oci_referrer_descriptor)
            referrer["annotations"] = {ANNOTATION_IS_MIGRATION: ANNOTATION_TRUTH_VALUE}
            referrers.append(referrer)

        responses.get(
            f"https://{c.referrers_url}?artifactType=text/x-shellscript",
            json={"schemaVersion": 2, "manifests": referrers, "annotations": {}},
        )

        with pytest.raises(IncorrectMigrationAttachment):
            fetch_migration_file(APP_IMAGE_REPO, self.image_digest)

    @responses.activate
    def test_migration_file_is_fetched(
        self, mock_fetch_migration, oci_referrer_descriptor, image_manifest
    ) -> None:
        c = Container(APP_IMAGE_REPO)
        c.digest = self.image_digest
        bundle_manifest = deepcopy(image_manifest)
        bundle_manifest["annotations"] = {ANNOTATION_HAS_MIGRATION: ANNOTATION_TRUTH_VALUE}
        responses.get(f"https://{c.manifest_url()}", json=bundle_manifest)

        mock_fetch_migration(c, b"echo hello world")

        r = fetch_migration_file(APP_IMAGE_REPO, self.image_digest)
        assert r == "echo hello world"


class TestResolveMigrations:

    @responses.activate
    def test_no_tag_is_listed_by_registry(self) -> None:
        renovate_upgrades = deepcopy(RENOVATE_UPGRADES)[:1]
        manager = TaskBundleUpgradesManager(renovate_upgrades, SimpleIterationResolver)
        tb_upgrade = list(manager._task_bundle_upgrades.items())[0][1]

        c = Container(tb_upgrade.dep_name)
        responses.get(
            f"https://quay.io/api/v1/repository/{c.api_prefix}/tag/?page=1&onlyActiveTags=true",
            json={"tags": [], "page": 1, "has_additional": False},
        )

        manager.resolve_migrations()
        assert len(tb_upgrade.migrations) == 0

    @responses.activate
    def test_migrations_are_resolved(self, mock_get_manifest, monkeypatch) -> None:
        renovate_upgrades = deepcopy(RENOVATE_UPGRADES)[:1]
        manager = TaskBundleUpgradesManager(renovate_upgrades, SimpleIterationResolver)

        # THIS. Fetch migrations for this upgrade
        tb_upgrade: Final = list(manager._task_bundle_upgrades.items())[0][1]

        digests_of_images_having_migration = [generate_digest(), generate_digest()]

        tags_info = [
            {
                "name": f"{tb_upgrade.new_value}-837e2cd",
                "manifest_digest": tb_upgrade.new_digest,
            },
            # Make this one have a migration
            {
                "name": f"{tb_upgrade.new_value}-5678abc",
                "manifest_digest": digests_of_images_having_migration[0],
            },
            # Make this one have a migration
            {
                "name": f"{tb_upgrade.new_value}-238f2a7",
                "manifest_digest": digests_of_images_having_migration[1],
            },
            {
                "name": f"{tb_upgrade.current_value}-127a2be",
                "manifest_digest": tb_upgrade.current_digest,
            },
        ]

        c = Container(tb_upgrade.dep_name)
        responses.get(
            f"https://quay.io/api/v1/repository/{c.api_prefix}/tag/?page=1&onlyActiveTags=true",
            json={"tags": tags_info, "page": 1, "has_additional": False},
        )

        for tag in tags_info:
            c = Container(tb_upgrade.dep_name)
            c.digest = tag["manifest_digest"]
            has_migration = c.digest in digests_of_images_having_migration
            mock_get_manifest(c, has_migration=has_migration)

        script_content: Final = "echo add a new task to pipeline"

        def _fetch_migration_file(image: str, digest: str) -> str | None:
            assert digest in digests_of_images_having_migration, (
                f"Bundle with digest {digest} does not have a migration, "
                "fetch_migration_file should not be called."
            )
            return script_content

        monkeypatch.setattr(
            "pipeline_migration.migrate.fetch_migration_file", _fetch_migration_file
        )

        manager.resolve_migrations()
        migrations = list(tb_upgrade.migrations)
        assert len(migrations) == 2

        # Verify the correct order.
        c.tag = tags_info[1]["name"]
        c.digest = tags_info[1]["manifest_digest"]
        assert c.uri_with_tag == migrations[1].task_bundle
        assert script_content == migrations[1].migration_script

        c.tag = tags_info[2]["name"]
        c.digest = tags_info[2]["manifest_digest"]
        assert c.uri_with_tag == migrations[0].task_bundle
        assert script_content == migrations[0].migration_script


PIPELINE_DEFINITION: Final = dedent(
    """\
apiVersion: tekton.dev/v1
kind: Pipeline
metadata:
  name: pl
spec:
  tasks:
  - name: clone
    image: debian:latest
    script: |
      git clone https://git.host/project
"""
)


class TestApplyMigrations:

    @pytest.mark.parametrize("chdir", [True, False])
    def test_apply_single_migration(self, chdir, monkeypatch, tmp_path) -> None:
        """Test applying a single migration to a pipeline file

        The migration tool aims to run inside a component repository, from
        where the packageFile is accessed by a relative path. Test parameter
        ``chdir`` indicates to change the working directory for this test to
        test the different behaviors.
        """
        from dataclasses import dataclass, field

        @dataclass
        class TestContext:
            bash_run: bool = False
            temp_files: list[tuple[int, str]] = field(default_factory=list)

        test_context = TestContext()
        counter = itertools.count()
        migration_script: Final = "echo hello world"

        def _mkstemp(*args, **kwargs):
            tmp_file_path = tmp_path / f"temp_file-{next(counter)}"
            tmp_file_path.write_text("")
            fd = os.open(tmp_file_path, os.O_RDWR)
            test_context.temp_files.append((fd, tmp_file_path))
            return fd, tmp_file_path

        def subprocess_run(*args, **kwargs):
            cmd = args[0]
            with open(cmd[-1], "r") as f:
                assert f.read() == PIPELINE_DEFINITION
            with open(cmd[-2], "r") as f:
                assert f.read() == migration_script
            assert not kwargs.get("check")
            test_context.bash_run = True
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("tempfile.mkstemp", _mkstemp)
        monkeypatch.setattr("subprocess.run", subprocess_run)

        pipeline_file: Final = tmp_path / "pipeline.yaml"
        pipeline_file.write_text(PIPELINE_DEFINITION)

        renovate_upgrades = deepcopy(RENOVATE_UPGRADES)
        manager = TaskBundleUpgradesManager(renovate_upgrades, SimpleIterationResolver)

        tb_migration = TaskBundleMigration("task-bundle:0.3@sha256:1234", migration_script)
        if chdir:
            monkeypatch.chdir(tmp_path)
            manager._apply_migration("pipeline.yaml", tb_migration)
        else:
            with pytest.raises(ValueError, match="Pipeline file does not exist: pipeline.yaml"):
                manager._apply_migration("pipeline.yaml", tb_migration)
            return

        assert test_context.bash_run
        assert len(test_context.temp_files) > 0
        for _, file_path in test_context.temp_files:
            assert not os.path.exists(file_path)

    def test_apply_migrations(self, tmp_path, monkeypatch):
        """Ensure applying all resolved migrations"""

        renovate_upgrades = [
            {
                "depName": TASK_BUNDLE_CLONE,
                "currentValue": "0.1",
                "currentDigest": "sha256:cff6b68a194a",
                "newValue": "0.2",
                "newDigest": "sha256:96e797480ac5",
                "depTypes": ["tekton-bundle"],
                "packageFile": ".tekton/pipeline.yaml",
                "parentDir": ".tekton",
            },
        ]
        manager = TaskBundleUpgradesManager(renovate_upgrades, SimpleIterationResolver)

        # Not really resolve migrations. Mock them instead, then apply.
        tb_upgrade = list(manager._task_bundle_upgrades.values())[0]
        c = Container(tb_upgrade.dep_name)

        c.tag = tb_upgrade.new_value
        c.digest = generate_digest()
        tb_upgrade.migrations.append(
            TaskBundleMigration(task_bundle=c.uri_with_tag, migration_script="echo add a new task")
        )

        c.tag = tb_upgrade.new_value
        c.digest = tb_upgrade.new_digest
        tb_upgrade.migrations.append(
            TaskBundleMigration(
                task_bundle=c.uri_with_tag, migration_script="echo remove task param"
            )
        )

        tekton_dir = tmp_path / ".tekton"
        tekton_dir.mkdir()
        package_file = tekton_dir / "pipeline.yaml"
        package_file.write_text(PIPELINE_DEFINITION)

        test_context = {"executed_scripts": []}
        counter = itertools.count()

        def _mkstemp(*args, **kwargs):
            tmp_file_path = tmp_path / f"temp_file-{next(counter)}"
            tmp_file_path.write_text("")
            fd = os.open(tmp_file_path, os.O_RDWR)
            return fd, tmp_file_path

        def subprocess_run(*args, **kwargs):
            cmd = args[0]
            with open(cmd[-2], "r") as f:
                test_context["executed_scripts"].append(f.read())
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("tempfile.mkstemp", _mkstemp)
        monkeypatch.setattr("subprocess.run", subprocess_run)

        monkeypatch.chdir(tmp_path)
        manager.apply_migrations()

        assert test_context["executed_scripts"] == ["echo add a new task", "echo remove task param"]

    def test_raise_error_if_migration_process_fails(self, caplog, monkeypatch, tmp_path):
        caplog.set_level(logging.DEBUG, logger="migrate")

        def _mkstemp(*args, **kwargs):
            tmp_file_path = tmp_path / "migration_file"
            tmp_file_path.write_text("")
            fd = os.open(tmp_file_path, os.O_RDWR)
            return fd, tmp_file_path

        def subprocess_run(cmd, *args, **kwargs):
            assert not kwargs.get("check")
            return subprocess.CompletedProcess(
                cmd, 1, stdout="normal output\nerror: something is wrong"
            )

        monkeypatch.setattr("tempfile.mkstemp", _mkstemp)
        monkeypatch.setattr("subprocess.run", subprocess_run)

        pipeline_file: Final = tmp_path / "pipeline.yaml"
        pipeline_file.write_text("kind: Pipeline")

        manager = TaskBundleUpgradesManager(deepcopy(RENOVATE_UPGRADES), SimpleIterationResolver)
        tb_migration = TaskBundleMigration("task-bundle:0.3@sha256:1234", "echo remove a param")
        with pytest.raises(subprocess.CalledProcessError):
            manager._apply_migration(pipeline_file, tb_migration)
        assert "something is wrong" in caplog.text


class TestLinkedMigrationsResolver:

    @responses.activate
    @pytest.mark.parametrize(
        "case_",
        [
            "bundle_doesnt_have_migration_link_info",
            "no_previous_migration_yet",
            "prevous_migrate_is_outside_of_upgrade",
        ],
    )
    def test_upgrade_doesnt_include_migration(
        self, case_, image_manifest, mock_get_manifest, tmp_path
    ):
        tb_upgrade = TaskBundleUpgrade(
            dep_name=APP_IMAGE_REPO,
            current_value="0.1",
            current_digest="sha256:bb6de65",
            new_value="0.2",
            new_digest="sha256:2a2c2b7",
        )

        c = Container(tb_upgrade.dep_name)
        responses.add(
            responses.GET,
            f"https://{c.registry}/api/v1/repository/{c.namespace}/{c.repository}/tag/?"
            "page=1&onlyActiveTags=true",
            json={"tags": SAMPLE_TAGS_OF_NS_APP, "page": 1, "has_additional": False},
        )

        c = Container(f"{tb_upgrade.dep_name}@{tb_upgrade.new_digest}")
        match case_:
            case "bundle_doesnt_have_migration_link_info":
                mock_get_manifest(c, has_migration=False, previous_migration_bundle=None)
            case "no_previous_migration_yet":
                mock_get_manifest(c, has_migration=False, previous_migration_bundle="")
            case "prevous_migrate_is_outside_of_upgrade":
                bundle_digeset = "sha256:69edfd6"  # The bundle digest is outside of the upgrade
                mock_get_manifest(c, has_migration=False, previous_migration_bundle=bundle_digeset)

        resolver = LinkedMigrationsResolver()
        resolver.resolve([tb_upgrade])

        assert len(tb_upgrade.migrations) == 0

    @responses.activate
    @pytest.mark.parametrize("case_", ["single", "multiple"])
    def test_migration_is_resolved(
        self, case_, image_manifest, mock_fetch_migration, mock_get_manifest, tmp_path
    ):
        """Test an upgrade includes a single migration

        Test data: upgrade has bundles: bundle1, bundle2 (M), bundle3.
        bundle2 has a migration, and bundle3 points to bundle2 by annotation.
        """

        tb_upgrade = TaskBundleUpgrade(
            dep_name=APP_IMAGE_REPO,
            current_value="0.1",
            current_digest="sha256:bb6de65",
            new_value="0.2",
            new_digest="sha256:2a2c2b7",
        )

        c = Container(tb_upgrade.dep_name)
        responses.add(
            responses.GET,
            f"https://{c.registry}/api/v1/repository/{c.namespace}/{c.repository}/tag/?"
            "page=1&onlyActiveTags=true",
            json={"tags": SAMPLE_TAGS_OF_NS_APP, "page": 1, "has_additional": False},
        )

        expected_migrations_count = 0

        match case_:
            case "single":
                # bundle@new_digest --> bundle@sha256:9bfc6b9 (M)

                migration_bundle_digest: Final = "sha256:9bfc6b9"

                c = Container(f"{tb_upgrade.dep_name}@{tb_upgrade.new_digest}")
                mock_get_manifest(
                    c, has_migration=False, previous_migration_bundle=migration_bundle_digest
                )

                c = Container(f"{tb_upgrade.dep_name}@{migration_bundle_digest}")
                # No more migration
                mock_get_manifest(c, has_migration=True, previous_migration_bundle="")
                mock_fetch_migration(c)

                expected_migrations_count = 1

            case "multiple":
                # bundle@new_digest --> bundle@sha256:52f8b96 (M) --> bundle@sha256:7f8b549 (M)

                c = Container(f"{tb_upgrade.dep_name}@{tb_upgrade.new_digest}")
                mock_get_manifest(
                    c, has_migration=False, previous_migration_bundle="sha256:52f8b96"
                )

                c = Container(f"{tb_upgrade.dep_name}@sha256:52f8b96")
                mock_get_manifest(c, has_migration=True, previous_migration_bundle="sha256:7f8b549")
                mock_fetch_migration(c)

                c = Container(f"{tb_upgrade.dep_name}@sha256:7f8b549")
                prev_bundle = ""  # no more migration
                mock_get_manifest(c, has_migration=True, previous_migration_bundle=prev_bundle)
                mock_fetch_migration(c)

                expected_migrations_count = 2

        resolver = LinkedMigrationsResolver()
        resolver.resolve([tb_upgrade])

        assert len(tb_upgrade.migrations) == expected_migrations_count
