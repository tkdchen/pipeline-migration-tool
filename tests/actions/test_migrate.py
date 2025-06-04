import os
import itertools
import logging
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any, Final
from unittest.mock import patch

from pipeline_migration.utils import YAMLStyle, dump_yaml, load_yaml
import responses
import pytest

from pipeline_migration.actions import migrate
from pipeline_migration.actions.migrate import (
    ANNOTATION_HAS_MIGRATION,
    ANNOTATION_IS_MIGRATION,
    ANNOTATION_TRUTH_VALUE,
    determine_task_bundle_upgrades_range,
    fetch_migration_file,
    IncorrectMigrationAttachment,
    LinkedMigrationsResolver,
    SimpleIterationResolver,
    TaskBundleMigration,
    TaskBundleUpgrade,
    TaskBundleUpgradesManager,
    MigrationFileOperation,
    PackageFile,
)
from pipeline_migration.quay import QuayTagInfo
from pipeline_migration.registry import Container
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
            "pipeline_migration.actions.migrate.fetch_migration_file", _fetch_migration_file
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


class TestMigrationFileOperationHandlePipelineFile:
    """Test MigrationFileOperation"""

    def prepare(self, tmp_path, pipeline_content):
        tb_upgrade = TaskBundleUpgrade(
            dep_name=TASK_BUNDLE_CLONE,
            current_value="0.1",
            current_digest="sha256:cff6b68a194a",
            new_value="0.2",
            new_digest="sha256:96e797480ac5",
        )

        self.package_file = PackageFile(file_path=".tekton/pipeline.yaml", parent_dir=".tekton")
        self.package_file.task_bundle_upgrades.append(tb_upgrade)

        m = TaskBundleMigration(
            task_bundle=f"{tb_upgrade.dep_name}:{tb_upgrade.new_value}@{generate_digest()}",
            migration_script="echo add a new task",
        )
        tb_upgrade.migrations.append(m)

        # Less content of the migration script than previous one, which covers file truncate.
        m = TaskBundleMigration(
            task_bundle=f"{tb_upgrade.dep_name}:{tb_upgrade.new_value}@{generate_digest()}",
            migration_script="echo hello",
        )
        tb_upgrade.migrations.append(m)

        m = TaskBundleMigration(
            task_bundle=f"{tb_upgrade.dep_name}:{tb_upgrade.new_value}@{tb_upgrade.new_digest}",
            migration_script="echo remove task param",
        )
        tb_upgrade.migrations.append(m)

        tekton_dir = tmp_path / ".tekton"
        tekton_dir.mkdir()
        (tekton_dir / "pipeline.yaml").write_text(pipeline_content)

    def test_apply_migrations(self, pipeline_and_run_yaml, tmp_path, monkeypatch):
        """Ensure migrations are applied to given pipeline"""
        self.prepare(tmp_path, pipeline_and_run_yaml)

        counter = itertools.count()

        def _mkstemp(*args, **kwargs):
            tmp_file_path = tmp_path / f"temp_file-{next(counter)}"
            tmp_file_path.write_text("")
            fd = os.open(tmp_file_path, os.O_RDWR)
            return fd, tmp_file_path

        def subprocess_run(*args, **kwargs):
            # Modify the pipeline
            cmd = args[0]
            pipeline_file = cmd[-1]
            style = YAMLStyle.detect(pipeline_file)
            doc = load_yaml(pipeline_file, style)
            doc["spec"]["tasks"].append({"name": "test"})
            dump_yaml(pipeline_file, doc, style)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("tempfile.mkstemp", _mkstemp)
        monkeypatch.setattr("subprocess.run", subprocess_run)

        monkeypatch.chdir(tmp_path)
        op = MigrationFileOperation(self.package_file.task_bundle_upgrades)
        op.handle(self.package_file.file_path)

        doc = load_yaml(self.package_file.file_path)
        if "kind: PipelineRun" in pipeline_and_run_yaml:
            tasks = doc["spec"]["pipelineSpec"]["tasks"]
        else:
            tasks = doc["spec"]["tasks"]

        assert tasks[-1] == {"name": "test"}

        # Verify the original formatting is preserved
        assert pipeline_and_run_yaml in Path(self.package_file.file_path).read_text()

    def test_do_not_save_if_no_changes(self, pipeline_and_run_yaml, monkeypatch, tmp_path):
        self.prepare(tmp_path, pipeline_and_run_yaml)

        counter = itertools.count()

        expected_dump_yaml_calls = 0  # for Pipeline
        if "kind: PipelineRun" in pipeline_and_run_yaml:
            expected_dump_yaml_calls = 1

        def _mkstemp(*args, **kwargs):
            tmp_file_path = tmp_path / f"temp_file-{next(counter)}"
            tmp_file_path.write_text("")
            fd = os.open(tmp_file_path, os.O_RDWR)
            return fd, tmp_file_path

        monkeypatch.setattr("tempfile.mkstemp", _mkstemp)
        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
        )

        monkeypatch.chdir(tmp_path)
        op = MigrationFileOperation(self.package_file.task_bundle_upgrades)
        with patch.object(migrate, "dump_yaml", wraps=migrate.dump_yaml) as mock_dump_yaml:
            op.handle(self.package_file.file_path)
            assert mock_dump_yaml.call_count == expected_dump_yaml_calls

    def test_raise_error_if_migration_process_fails(
        self, pipeline_and_run_yaml, caplog, monkeypatch, tmp_path
    ):
        self.prepare(tmp_path, pipeline_and_run_yaml)

        caplog.set_level(logging.DEBUG, logger="migrate")
        counter = itertools.count()

        def _mkstemp(*args, **kwargs):
            tmp_file_path = tmp_path / f"temp-file-{next(counter)}"
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

        monkeypatch.chdir(tmp_path)
        op = MigrationFileOperation(self.package_file.task_bundle_upgrades)
        with pytest.raises(subprocess.CalledProcessError):
            op.handle(self.package_file.file_path)

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
