from collections import defaultdict
import os
import itertools
import logging
import re
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
    MigrationApplyError,
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
    {"name": "0.3-0c9b02c", "manifest_digest": "sha256:bfc0c3c", "start_ts": 7},
    # {"name": "0.3", "manifest_digest": "sha256:bfc0c3c"},
    {"name": "0.2-23d463f", "manifest_digest": "sha256:2a2c2b7", "start_ts": 6},
    {"name": "0.1-d4eab53", "manifest_digest": "sha256:52f8b96", "start_ts": 5},
    {"name": "0.1-b486c47", "manifest_digest": "sha256:9bfc6b9", "start_ts": 4},
    {"name": "0.1-9dffe5f", "manifest_digest": "sha256:7f8b549", "start_ts": 3},
    {"name": "0.1-3778abd", "manifest_digest": "sha256:bb6de65", "start_ts": 2},
    {"name": "0.1-833463f", "manifest_digest": "sha256:69edfd6", "start_ts": 1},
]

APP_IMAGE_REPO: Final = "reg.io/ns/app"
TASK_BUNDLE_CLONE: Final = "quay.io/konflux-ci/catalog/task-clone"
TASK_BUNDLE_TESTS: Final = "quay.io/konflux-ci/catalog/task-tests"
TASK_BUNDLE_LINT: Final = "quay.io/konflux-ci/catalog/task-lint"
TASK_BUNDLE_SIGNATURE_SCAN: Final = "quay.io/konflux-ci/some-catalog/task-signature-scan"


def mock_list_repo_tags_with_filter_tag_name(
    image: str, tags_info: list[dict], empty_for_versions: list[str] | None = None, status=200
) -> None:
    c = Container(image)
    api_url = f"https://quay.io/api/v1/repository/{c.api_prefix}/tag/"
    tag_groups: dict[str, list[dict]] = defaultdict(list)
    for tag in tags_info:
        version = tag["name"].split("-")[0]
        tag_groups[version].append(tag)
    if empty_for_versions:
        for version in empty_for_versions:
            tag_groups[version] = []
    for version, its_tags in tag_groups.items():
        responses.get(
            f"{api_url}?page=1&onlyActiveTags=true&filter_tag_name=like:{version}-",
            json={"tags": its_tags, "page": 1, "has_additional": False},
            status=status,
        )


class TestDetermineTaskBundleUpdatesRange:
    """Test method determine_task_bundle_upgrades_range

    This test shares test data with ``test_drop_out_of_order_versions`` together.
    """

    @responses.activate
    def test_ordered_bundles(self):
        """Determine range from ordered bundles"""
        bundle_upgrade = TaskBundleUpgrade(
            dep_name=TASK_BUNDLE_CLONE,
            current_value="0.2",
            current_digest="sha256:1028",
            new_value="0.3",
            new_digest="sha256:9854",
        )
        tags_info = [
            {"name": "0.3-9854", "manifest_digest": "sha256:9854", "start_ts": 5},  # <- to
            {"name": "0.2-2834", "manifest_digest": "sha256:2834", "start_ts": 4},
            {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 3},
            {"name": "0.2-1028", "manifest_digest": "sha256:1028", "start_ts": 2},  # <- from
            {"name": "0.2-6582", "manifest_digest": "sha256:6582", "start_ts": 1},
        ]
        mock_list_repo_tags_with_filter_tag_name(bundle_upgrade.dep_name, tags_info)

        expected = [
            QuayTagInfo.from_tag_info(tag)
            for tag in [
                {"name": "0.3-9854", "manifest_digest": "sha256:9854", "start_ts": 5},
                {"name": "0.2-2834", "manifest_digest": "sha256:2834", "start_ts": 4},
                {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 3},
            ]
        ]
        range = determine_task_bundle_upgrades_range(bundle_upgrade)
        assert range == expected

    @responses.activate
    @pytest.mark.parametrize(
        "upgrade",
        [
            {
                "current_value": "0.2",
                "current_digest": "sha256:4745",
                "new_value": "0.3",
                "new_digest": "sha256:0de3",
            },
            {
                "current_value": "0.2",
                "current_digest": "sha256:8a2d",
                "new_value": "0.3",
                "new_digest": "sha256:0de3",
            },
        ],
    )
    def test_out_of_order_bundles(self, upgrade):
        """Determine range from out-of-order bundles"""
        bundle_upgrade = TaskBundleUpgrade(dep_name=TASK_BUNDLE_CLONE, **upgrade)
        tags_info = [
            {"name": "0.2-8a2d", "manifest_digest": "sha256:8a2d", "start_ts": 10},
            {"name": "0.1-e37f", "manifest_digest": "sha256:e37f", "start_ts": 9},
            {"name": "0.2-abcd", "manifest_digest": "sha256:abcd", "start_ts": 8},
            {"name": "0.3-0de3", "manifest_digest": "sha256:0de3", "start_ts": 7},
            {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 6},
            {"name": "0.1-f40f", "manifest_digest": "sha256:f40f", "start_ts": 5},
            {"name": "0.2-9fed", "manifest_digest": "sha256:9fed", "start_ts": 4},
            {"name": "0.3-6532", "manifest_digest": "sha256:6532", "start_ts": 3},
            {"name": "0.3-2834", "manifest_digest": "sha256:2834", "start_ts": 2},
            {"name": "0.2-e8f2", "manifest_digest": "sha256:e8f2", "start_ts": 1},
        ]
        mock_list_repo_tags_with_filter_tag_name(bundle_upgrade.dep_name, tags_info)

        expected = [
            QuayTagInfo.from_tag_info(tag)
            for tag in [
                {"name": "0.3-0de3", "manifest_digest": "sha256:0de3", "start_ts": 7},
                {"name": "0.3-6532", "manifest_digest": "sha256:6532", "start_ts": 3},
                {"name": "0.3-2834", "manifest_digest": "sha256:2834", "start_ts": 2},
            ]
        ]

        range = determine_task_bundle_upgrades_range(bundle_upgrade)
        assert range == expected

    @responses.activate
    @pytest.mark.parametrize(
        "upgrade",
        [
            {
                "current_value": "0.2",
                "current_digest": "sha256:9999",
                "new_value": "0.3",
                "new_digest": "sha256:2834",
            },
            {
                "current_value": "0.2",
                "current_digest": "sha256:e8f2",
                "new_value": "0.3",
                "new_digest": "sha256:0000",
            },
        ],
    )
    def test_invalid_input_digest(self, upgrade, caplog):
        caplog.set_level(logging.WARNING)

        """Test empty list is returned if input digest is invalid"""
        bundle_upgrade = TaskBundleUpgrade(dep_name=TASK_BUNDLE_CLONE, **upgrade)
        tags_info = [
            {"name": "0.3-6532", "manifest_digest": "sha256:6532", "start_ts": 3},
            {"name": "0.3-2834", "manifest_digest": "sha256:2834", "start_ts": 2},
            {"name": "0.2-e8f2", "manifest_digest": "sha256:e8f2", "start_ts": 1},
        ]
        mock_list_repo_tags_with_filter_tag_name(bundle_upgrade.dep_name, tags_info)

        range = determine_task_bundle_upgrades_range(bundle_upgrade)
        assert range == []

        bundle_regex = rf"{bundle_upgrade.dep_name}:0\.[23]@sha256:(9999|0000)"
        log_regex = re.compile(rf"Registry does not have (current|new) bundle {bundle_regex}")
        assert log_regex.search(caplog.text)

    @responses.activate
    def test_repo_is_in_pure_new_tag_scheme(self):
        """Test return empty list if image repo only has new tag scheme"""
        bundle_upgrade = TaskBundleUpgrade(
            dep_name=TASK_BUNDLE_CLONE,
            current_value="0.2",
            current_digest="sha256:1028",
            new_value="0.3",
            new_digest="sha256:9854",
        )

        # If a repo has the pure new tag scheme, no tag is retrieved from registry.
        mock_list_repo_tags_with_filter_tag_name(
            bundle_upgrade.dep_name, [], empty_for_versions=["0.2", "0.3"]
        )

        assert determine_task_bundle_upgrades_range(bundle_upgrade) == []

    @responses.activate
    def test_repo_mixes_two_tag_schemes(self):
        """
        Test return empty list if image repo mixes build-definitions style and the new schemes
        """
        bundle_upgrade = TaskBundleUpgrade(
            dep_name=TASK_BUNDLE_CLONE,
            current_value="0.2",
            current_digest="sha256:1028",
            new_value="0.3",
            new_digest="sha256:9854",
        )
        tags_info = [
            {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 6},
            {"name": "0.2-9fed", "manifest_digest": "sha256:9fed", "start_ts": 4},
            {"name": "0.2-1028", "manifest_digest": "sha256:e8f2", "start_ts": 1},
        ]

        # When mixing the tag schemes, version 0.3 bundles are pushed in a new form, which means
        # there are no longer tags like ``0.3-<commit hash>``.
        mock_list_repo_tags_with_filter_tag_name(
            bundle_upgrade.dep_name, tags_info, empty_for_versions=["0.3"]
        )

        assert determine_task_bundle_upgrades_range(bundle_upgrade) == []


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

        mock_list_repo_tags_with_filter_tag_name(
            tb_upgrade.dep_name,
            [],
            empty_for_versions=[tb_upgrade.current_value, tb_upgrade.new_value],
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
                "start_ts": 4,
            },
            # Make this one have a migration
            {
                "name": f"{tb_upgrade.new_value}-5678abc",
                "manifest_digest": digests_of_images_having_migration[0],
                "start_ts": 3,
            },
            # Make this one have a migration
            {
                "name": f"{tb_upgrade.new_value}-238f2a7",
                "manifest_digest": digests_of_images_having_migration[1],
                "start_ts": 2,
            },
            {
                "name": f"{tb_upgrade.current_value}-127a2be",
                "manifest_digest": tb_upgrade.current_digest,
                "start_ts": 1,
            },
        ]

        mock_list_repo_tags_with_filter_tag_name(tb_upgrade.dep_name, tags_info)

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
            # simulate yq to indent block sequences with 2 spaces
            style.indentation.indent(2)
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
            # At least one dump_yaml call to write pipeline definition into a temp file.
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
        with pytest.raises(ExceptionGroup) as exc_info:
            op.handle(self.package_file.file_path)

        assert exc_info.group_contains(MigrationApplyError, match="Command .+ returned non-zero")
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
            dep_name=TASK_BUNDLE_CLONE,
            current_value="0.1",
            current_digest="sha256:bb6de65",
            new_value="0.2",
            new_digest="sha256:2a2c2b7",
        )

        mock_list_repo_tags_with_filter_tag_name(tb_upgrade.dep_name, SAMPLE_TAGS_OF_NS_APP)

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
            dep_name=TASK_BUNDLE_CLONE,
            current_value="0.1",
            current_digest="sha256:bb6de65",
            new_value="0.2",
            new_digest="sha256:2a2c2b7",
        )

        mock_list_repo_tags_with_filter_tag_name(tb_upgrade.dep_name, SAMPLE_TAGS_OF_NS_APP)

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

    @responses.activate
    def test_skip_resolving_migrations_if_upgrade_range_is_empty(self, caplog):
        """Test no upgrade, no migration is resolved"""
        caplog.set_level(logging.INFO, logger="migrate")

        tb_upgrade = TaskBundleUpgrade(
            dep_name=APP_IMAGE_REPO,
            current_value="0.1",
            current_digest="sha256:bb6de65",
            new_value="0.2",
            new_digest="sha256:2a2c2b7",
        )

        c = Container(tb_upgrade.dep_name)
        for version in ["0.1", "0.2"]:
            responses.add(
                responses.GET,
                f"https://{c.registry}/api/v1/repository/{c.namespace}/{c.repository}/tag/?"
                f"page=1&onlyActiveTags=true&filter_tag_name=like:{version}-",
                json={"tags": [], "page": 1, "has_additional": False},
            )

        resolver = LinkedMigrationsResolver()
        resolver.resolve([tb_upgrade])

        log_text = f"Upgrade range is empty for {tb_upgrade.dep_name}. Skip resolving migrations"
        assert log_text in caplog.text


@responses.activate
@pytest.mark.parametrize(
    "tags_info,bundle_upgrade,expected",
    [
        pytest.param(
            [],
            TaskBundleUpgrade(
                dep_name=TASK_BUNDLE_CLONE,
                current_value="0.2",
                current_digest="sha256:4745",
                new_value="0.2",
                new_digest="sha256:6582",
            ),
            [[], None, None, False],
            id="empty-input-tags",
        ),
        pytest.param(
            [{"name": "0.2-2834", "manifest_digest": "sha256:2834", "start_ts": 1}],
            TaskBundleUpgrade(
                dep_name=TASK_BUNDLE_CLONE,
                current_value="0.2",
                current_digest="sha256:def7",
                new_value="0.2",
                new_digest="sha256:2834",
            ),
            [
                [{"name": "0.2-2834", "manifest_digest": "sha256:2834", "start_ts": 1}],
                None,
                {"name": "0.2-2834", "manifest_digest": "sha256:2834", "start_ts": 1},
                False,
            ],
            id="single-tag",
        ),
        pytest.param(
            [{"name": "0.2-2834", "manifest_digest": "sha256:2834", "start_ts": 1}],
            TaskBundleUpgrade(
                dep_name=TASK_BUNDLE_CLONE,
                current_value="0.2",
                current_digest="sha256:2834",
                new_value="0.2",
                new_digest="sha256:def7",
            ),
            [
                [{"name": "0.2-2834", "manifest_digest": "sha256:2834", "start_ts": 1}],
                {"name": "0.2-2834", "manifest_digest": "sha256:2834", "start_ts": 1},
                None,
                False,
            ],
            id="single-tag-2",
        ),
        pytest.param(
            [
                {"name": "0.2-2834", "manifest_digest": "sha256:2834", "start_ts": 4},
                {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 3},  # <- to
                {"name": "0.2-1028", "manifest_digest": "sha256:1028", "start_ts": 2},
                {"name": "0.2-6582", "manifest_digest": "sha256:6582", "start_ts": 1},  # <- from
            ],
            TaskBundleUpgrade(
                dep_name=TASK_BUNDLE_CLONE,
                current_value="0.2",
                current_digest="sha256:6582",
                new_value="0.2",
                new_digest="sha256:4745",
            ),
            [
                [
                    {"name": "0.2-2834", "manifest_digest": "sha256:2834", "start_ts": 4},
                    {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 3},
                    {"name": "0.2-1028", "manifest_digest": "sha256:1028", "start_ts": 2},
                    {"name": "0.2-6582", "manifest_digest": "sha256:6582", "start_ts": 1},
                ],
                {"name": "0.2-6582", "manifest_digest": "sha256:6582", "start_ts": 1},
                {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 3},
                False,
            ],
            id="more-tags-within-version",
        ),
        pytest.param(
            [
                {"name": "0.3-2834", "manifest_digest": "sha256:2834", "start_ts": 2},
                {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 1},
            ],
            TaskBundleUpgrade(
                dep_name=TASK_BUNDLE_CLONE,
                current_value="0.2",
                current_digest="sha256:4745",
                new_value="0.3",
                new_digest="sha256:2834",
            ),
            [
                [
                    {"name": "0.3-2834", "manifest_digest": "sha256:2834", "start_ts": 2},
                    {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 1},
                ],
                {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 1},
                {"name": "0.3-2834", "manifest_digest": "sha256:2834", "start_ts": 2},
                False,
            ],
            id="two-tags-newer-version-is-built",
        ),
        pytest.param(
            [
                {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 2},
                {"name": "0.3-2834", "manifest_digest": "sha256:2834", "start_ts": 1},
            ],
            TaskBundleUpgrade(
                dep_name=TASK_BUNDLE_CLONE,
                current_value="0.2",
                current_digest="sha256:4745",
                new_value="0.3",
                new_digest="sha256:2834",
            ),
            [
                [{"name": "0.3-2834", "manifest_digest": "sha256:2834", "start_ts": 1}],
                {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 2},
                {"name": "0.3-2834", "manifest_digest": "sha256:2834", "start_ts": 1},
                True,
            ],
            id="two-tags-older-version-is-built",
        ),
        pytest.param(
            [
                {"name": "0.2-abcd", "manifest_digest": "sha256:abcd", "start_ts": 6},
                {"name": "0.3-0de3", "manifest_digest": "sha256:0de3", "start_ts": 5},
                {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 4},  # <- from
                {"name": "0.3-6532", "manifest_digest": "sha256:6532", "start_ts": 3},  # <- to
                {"name": "0.3-2834", "manifest_digest": "sha256:2834", "start_ts": 2},
                {"name": "0.2-e8f2", "manifest_digest": "sha256:e8f2", "start_ts": 1},
            ],
            TaskBundleUpgrade(
                dep_name=TASK_BUNDLE_CLONE,
                current_value="0.2",
                current_digest="sha256:4745",
                new_value="0.3",
                new_digest="sha256:6532",
            ),
            [
                [
                    {"name": "0.3-0de3", "manifest_digest": "sha256:0de3", "start_ts": 5},
                    {"name": "0.3-6532", "manifest_digest": "sha256:6532", "start_ts": 3},
                    {"name": "0.3-2834", "manifest_digest": "sha256:2834", "start_ts": 2},
                    {"name": "0.2-e8f2", "manifest_digest": "sha256:e8f2", "start_ts": 1},
                ],
                {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 4},
                {"name": "0.3-6532", "manifest_digest": "sha256:6532", "start_ts": 3},
                True,
            ],
            id="tags-with-mixed-versions",
        ),
        pytest.param(
            [
                {"name": "0.3-fed0", "manifest_digest": "sha256:fed0", "start_ts": 12},
                {"name": "0.4-def4", "manifest_digest": "sha256:def4", "start_ts": 11},  # <- to
                {"name": "0.2-8a2d", "manifest_digest": "sha256:8a2d", "start_ts": 10},
                {"name": "0.1-e37f", "manifest_digest": "sha256:e37f", "start_ts": 9},
                {"name": "0.2-abcd", "manifest_digest": "sha256:abcd", "start_ts": 8},
                {"name": "0.3-0de3", "manifest_digest": "sha256:0de3", "start_ts": 7},
                {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 6},
                {"name": "0.1-f40f", "manifest_digest": "sha256:f40f", "start_ts": 5},
                {"name": "0.2-9fed", "manifest_digest": "sha256:9fed", "start_ts": 4},
                {"name": "0.3-6532", "manifest_digest": "sha256:6532", "start_ts": 3},
                {"name": "0.3-2834", "manifest_digest": "sha256:2834", "start_ts": 2},
                {"name": "0.2-e8f2", "manifest_digest": "sha256:e8f2", "start_ts": 1},  # <- from
            ],
            TaskBundleUpgrade(
                dep_name=TASK_BUNDLE_CLONE,
                current_value="0.2",
                current_digest="sha256:e8f2",
                new_value="0.4",
                new_digest="sha256:def4",
            ),
            [
                [
                    {"name": "0.4-def4", "manifest_digest": "sha256:def4", "start_ts": 11},
                    {"name": "0.3-0de3", "manifest_digest": "sha256:0de3", "start_ts": 7},
                    {"name": "0.3-6532", "manifest_digest": "sha256:6532", "start_ts": 3},
                    {"name": "0.3-2834", "manifest_digest": "sha256:2834", "start_ts": 2},
                    {"name": "0.2-e8f2", "manifest_digest": "sha256:e8f2", "start_ts": 1},
                ],
                {"name": "0.2-e8f2", "manifest_digest": "sha256:e8f2", "start_ts": 1},
                {"name": "0.4-def4", "manifest_digest": "sha256:def4", "start_ts": 11},
                False,
            ],
            id="tags-more-older-versions-are-built",
        ),
        pytest.param(
            [
                {"name": "0.2-8a2d", "manifest_digest": "sha256:8a2d", "start_ts": 10},
                {"name": "0.1-e37f", "manifest_digest": "sha256:e37f", "start_ts": 9},
                {"name": "0.2-abcd", "manifest_digest": "sha256:abcd", "start_ts": 8},
                {"name": "0.3-0de3", "manifest_digest": "sha256:0de3", "start_ts": 7},  # <- to
                {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 6},  # <- from
                {"name": "0.1-f40f", "manifest_digest": "sha256:f40f", "start_ts": 5},
                {"name": "0.2-9fed", "manifest_digest": "sha256:9fed", "start_ts": 4},
                {"name": "0.3-6532", "manifest_digest": "sha256:6532", "start_ts": 3},
                {"name": "0.3-2834", "manifest_digest": "sha256:2834", "start_ts": 2},
                {"name": "0.2-e8f2", "manifest_digest": "sha256:e8f2", "start_ts": 1},
            ],
            TaskBundleUpgrade(
                dep_name=TASK_BUNDLE_CLONE,
                current_value="0.2",
                current_digest="sha256:4745",
                new_value="0.3",
                new_digest="sha256:0de3",
            ),
            [
                [
                    {"name": "0.3-0de3", "start_ts": 7, "manifest_digest": "sha256:0de3"},
                    {"name": "0.3-6532", "start_ts": 3, "manifest_digest": "sha256:6532"},
                    {"name": "0.3-2834", "start_ts": 2, "manifest_digest": "sha256:2834"},
                    {"name": "0.2-e8f2", "start_ts": 1, "manifest_digest": "sha256:e8f2"},
                ],
                {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 6},
                {"name": "0.3-0de3", "start_ts": 7, "manifest_digest": "sha256:0de3"},
                True,
            ],
            id="upgrade-from-bundle-of-old-version-built-after-newer-version",
        ),
        pytest.param(
            [
                {"name": "0.2-8a2d", "manifest_digest": "sha256:8a2d", "start_ts": 10},  # <- from
                {"name": "0.1-e37f", "manifest_digest": "sha256:e37f", "start_ts": 9},
                {"name": "0.2-abcd", "manifest_digest": "sha256:abcd", "start_ts": 8},
                {"name": "0.3-0de3", "manifest_digest": "sha256:0de3", "start_ts": 7},  # <- to
                {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 6},
                {"name": "0.1-f40f", "manifest_digest": "sha256:f40f", "start_ts": 5},
                {"name": "0.2-9fed", "manifest_digest": "sha256:9fed", "start_ts": 4},
                {"name": "0.3-6532", "manifest_digest": "sha256:6532", "start_ts": 3},
                {"name": "0.3-2834", "manifest_digest": "sha256:2834", "start_ts": 2},
                {"name": "0.2-e8f2", "manifest_digest": "sha256:e8f2", "start_ts": 1},
            ],
            TaskBundleUpgrade(
                dep_name=TASK_BUNDLE_CLONE,
                current_value="0.2",
                current_digest="sha256:8a2d",
                new_value="0.3",
                new_digest="sha256:0de3",
            ),
            [
                [
                    {"name": "0.3-0de3", "start_ts": 7, "manifest_digest": "sha256:0de3"},
                    {"name": "0.3-6532", "start_ts": 3, "manifest_digest": "sha256:6532"},
                    {"name": "0.3-2834", "start_ts": 2, "manifest_digest": "sha256:2834"},
                    {"name": "0.2-e8f2", "start_ts": 1, "manifest_digest": "sha256:e8f2"},
                ],
                {"name": "0.2-8a2d", "manifest_digest": "sha256:8a2d", "start_ts": 10},
                {"name": "0.3-0de3", "start_ts": 7, "manifest_digest": "sha256:0de3"},
                True,
            ],
            id="upgrade-from-bundle-of-old-version-built-after-newer-version-2",
        ),
        pytest.param(
            [
                {"name": "0.2-abcd", "manifest_digest": "sha256:abcd", "start_ts": 6},
                {"name": "0.3-0de3", "manifest_digest": "sha256:0de3", "start_ts": 5},
                {"name": "0.2-4745", "manifest_digest": "sha256:4745", "start_ts": 4},
                {"name": "0.3-6532", "manifest_digest": "sha256:6532", "start_ts": 3},
                {"name": "0.3-2834", "manifest_digest": "sha256:2834", "start_ts": 2},
                {"name": "0.2-e8f2", "manifest_digest": "sha256:e8f2", "start_ts": 1},
            ],
            # Both of the digests are not present in the responded tags
            TaskBundleUpgrade(
                dep_name=TASK_BUNDLE_CLONE,
                current_value="0.2",
                current_digest="sha256:9999",
                new_value="0.3",
                new_digest="sha256:0000",
            ),
            [
                [
                    {"name": "0.3-0de3", "manifest_digest": "sha256:0de3", "start_ts": 5},
                    {"name": "0.3-6532", "manifest_digest": "sha256:6532", "start_ts": 3},
                    {"name": "0.3-2834", "manifest_digest": "sha256:2834", "start_ts": 2},
                    {"name": "0.2-e8f2", "manifest_digest": "sha256:e8f2", "start_ts": 1},
                ],
                None,
                None,
                False,
            ],
            id="digest-is-out-of-range",
        ),
    ],
)
def test_drop_out_of_order_versions(tags_info, bundle_upgrade, expected):
    c = Container(bundle_upgrade.dep_name)
    api_url = f"https://quay.io/api/v1/repository/{c.api_prefix}/tag/"

    if tags_info:
        mock_list_repo_tags_with_filter_tag_name(bundle_upgrade.dep_name, tags_info)
    else:
        mock_list_repo_tags_with_filter_tag_name(
            bundle_upgrade.dep_name,
            [],
            empty_for_versions=[bundle_upgrade.current_value, bundle_upgrade.new_value],
        )

    responses.get(
        f"{api_url}?page=1&onlyActiveTags=true&filter_tag_name=like:0.100-",
        json={"tags": [], "page": 1, "has_additional": False},
    )

    tags = migrate.list_bundle_tags(bundle_upgrade)
    result = migrate.drop_out_of_order_versions(tags, bundle_upgrade)
    assert result == tuple(expected)
