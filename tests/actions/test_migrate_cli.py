import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import responses
import pytest
from oras.types import container_type

from pipeline_migration.cli import entry_point
from pipeline_migration.actions.migrate import (
    ANNOTATION_HAS_MIGRATION,
    ANNOTATION_IS_MIGRATION,
    ANNOTATION_PREVIOUS_MIGRATION_BUNDLE,
    clean_upgrades,
    InvalidRenovateUpgradesData,
)
from pipeline_migration.registry import (
    Container,
    MEDIA_TYPE_OCI_EMTPY_V1,
    MEDIA_TYPE_OCI_IMAGE_CONFIG_V1,
    MEDIA_TYPE_OCI_IMAGE_INDEX_V1,
    MEDIA_TYPE_OCI_IMAGE_MANIFEST_V1,
    Registry,
    ensure_container,
)

from pipeline_migration.utils import YAMLStyle, load_yaml, dump_yaml
from tests.actions.test_migrate import (
    APP_IMAGE_REPO,
    TASK_BUNDLE_CLONE,
    TASK_BUNDLE_SIGNATURE_SCAN,
    mock_list_repo_tags_with_filter_tag_name,
)
from tests.utils import generate_digest, generate_git_sha


UPGRADES: Final = [
    {
        "depName": TASK_BUNDLE_CLONE,
        "currentValue": "0.1",
        "currentDigest": generate_digest(),
        "newValue": "0.1",
        "newDigest": generate_digest(),
        "depTypes": ["tekton-bundle"],
        "packageFile": ".tekton/component-a-pr.yaml",
        "parentDir": ".tekton",
    },
]


@dataclass
class ImageTestData:
    image: str
    # manifest digest => image manifest
    manifests: dict[str, dict] = field(default_factory=dict)
    # manifest digest => image index
    referrers: dict[str, dict] = field(default_factory=dict)
    # layer digest => artifact content
    blobs: dict[str, bytes] = field(default_factory=dict)
    # list of tags info
    tags: list[dict[str, str | int]] = field(default_factory=list)
    # list of tag names for mocking listRepoTags endpoint to return []
    nonexistent_tags: list[str] = field(default_factory=list)


task_bundle_clone_test_data = ImageTestData(
    image=TASK_BUNDLE_CLONE,
    # For mocking listRepoTags endpoint. They are mapped to the following bundle manifests.
    tags=[
        {
            "name": f"0.1-{generate_git_sha()}",
            "manifest_digest": "sha256:c4bb69a3a08f",
            "start_ts": 3,
        },
        {
            "name": f"0.1-{generate_git_sha()}",
            "manifest_digest": "sha256:f23dc7cd74ba",
            "start_ts": 2,
        },
        {
            "name": f"0.1-{generate_git_sha()}",
            "manifest_digest": "sha256:492fb9ae4e7e",
            "start_ts": 1,
        },
    ],
    manifests={
        # Task bundles, which are listed from newer one to older one.
        "sha256:c4bb69a3a08f": {
            "schemaVersion": 2,
            "mediaType": MEDIA_TYPE_OCI_IMAGE_MANIFEST_V1,
            "config": {
                "mediaType": MEDIA_TYPE_OCI_IMAGE_CONFIG_V1,
                "digest": generate_digest(),
                "size": 10,
            },
            "layers": [],
            "annotations": {
                ANNOTATION_HAS_MIGRATION: "true",
                ANNOTATION_PREVIOUS_MIGRATION_BUNDLE: "sha256:f23dc7cd74ba",
            },
        },
        "sha256:f23dc7cd74ba": {
            "schemaVersion": 2,
            "mediaType": MEDIA_TYPE_OCI_IMAGE_MANIFEST_V1,
            "config": {
                "mediaType": MEDIA_TYPE_OCI_IMAGE_CONFIG_V1,
                "digest": generate_digest(),
                "size": 11,
            },
            "layers": [],
            "annotations": {
                ANNOTATION_HAS_MIGRATION: "true",
                ANNOTATION_PREVIOUS_MIGRATION_BUNDLE: "",
            },
        },
        "sha256:492fb9ae4e7e": {
            "schemaVersion": 2,
            "mediaType": MEDIA_TYPE_OCI_IMAGE_MANIFEST_V1,
            "config": {
                "mediaType": MEDIA_TYPE_OCI_IMAGE_CONFIG_V1,
                "digest": generate_digest(),
                "size": 12,
            },
            "layers": [],
            "annotations": {},
        },
        # Artifacts
        "sha256:524f99ec6cde": {
            "schemaVersion": 2,
            "mediaType": MEDIA_TYPE_OCI_IMAGE_MANIFEST_V1,
            "config": {
                "mediaType": MEDIA_TYPE_OCI_EMTPY_V1,
                "digest": "sha256:44136fa",
                "size": 2,
            },
            "layers": [
                {
                    "mediaType": "text/x-shellscript",
                    "digest": "sha256:2fed5ba",
                    "size": 120,
                }
            ],
        },
        "sha256:3ee08ef47114": {
            "schemaVersion": 2,
            "mediaType": MEDIA_TYPE_OCI_IMAGE_MANIFEST_V1,
            "config": {
                "mediaType": MEDIA_TYPE_OCI_EMTPY_V1,
                "digest": "sha256:44136ff",
                "size": 2,
            },
            "layers": [
                {
                    "mediaType": "text/x-shellscript",
                    "digest": "sha256:cf505b9",
                    "size": 120,
                }
            ],
        },
    },
    referrers={
        "sha256:c4bb69a3a08f": {
            "schemaVersion": 2,
            "mediaType": MEDIA_TYPE_OCI_IMAGE_INDEX_V1,
            "manifests": [
                {
                    "mediaType": MEDIA_TYPE_OCI_IMAGE_MANIFEST_V1,
                    "digest": "sha256:123",
                    "size": 1409,
                    "artifactType": "application/pdf",
                    "annotations": {},
                },
                {
                    "mediaType": MEDIA_TYPE_OCI_IMAGE_MANIFEST_V1,
                    "digest": "sha256:524f99ec6cde",
                    "size": 300,
                    "artifactType": "text/x-shellscript",
                    "annotations": {
                        ANNOTATION_IS_MIGRATION: "true",
                    },
                },
            ],
        },
        "sha256:f23dc7cd74ba": {
            "schemaVersion": 2,
            "mediaType": MEDIA_TYPE_OCI_IMAGE_INDEX_V1,
            "manifests": [
                {
                    "mediaType": MEDIA_TYPE_OCI_IMAGE_MANIFEST_V1,
                    "digest": "sha256:3ee08ef47114",
                    "size": 2048,
                    "artifactType": "text/x-shellscript",
                    "annotations": {
                        ANNOTATION_IS_MIGRATION: "true",
                    },
                },
            ],
        },
    },
    blobs={
        "sha256:2fed5ba": b"echo add a new task",
        "sha256:cf505b9": b"echo remove params from task",
    },
)


task_bundle_signature_scan_test_data = ImageTestData(
    image=TASK_BUNDLE_SIGNATURE_SCAN,
    nonexistent_tags=["0.2"],
)


# TODO: make this fixture to be reusable
def mock_quay_list_tags(image_repo: str, tags: list[dict]) -> None:
    assert image_repo != ""
    api_url = f"https://quay.io/api/v1/repository/{image_repo}/tag/"
    responses.get(
        f"{api_url}?page=1&onlyActiveTags=true",
        json={"tags": tags, "page": 1, "has_additional": False},
    )


class MockRegistry(Registry):

    test_data = [task_bundle_clone_test_data]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for image_data in self.test_data:
            container = Container(image_data.image)

            # Mock requests for Registry.list_referrers
            for digest, image_index in image_data.referrers.items():
                referrer_c = Container(image_data.image)
                referrer_c.digest = digest
                responses.get(
                    f"https://{referrer_c.referrers_url}?artifactType=text/x-shellscript",
                    json=image_index,
                )

            # Mock requests for Registry.get_blob
            for digest, content in image_data.blobs.items():
                responses.get(f"https://{container.get_blob_url(digest)}", body=content)

    @ensure_container
    def get_manifest(
        self, container: container_type, allowed_media_type: list | None = None
    ) -> dict:
        """Override Registry.get_manifest to get manifest from test data"""
        for image_data in self.test_data:
            manifest = image_data.manifests.get(container.digest)
            if manifest is None:
                raise ValueError(f"Digest {container.digest} does not present in the test data.")
            return manifest
        raise ValueError("No test data.")


class TestMigrateTaskBundleUpgrade:

    def _mock_quay_list_tags(self):
        for image_data in MockRegistry.test_data:
            mock_list_repo_tags_with_filter_tag_name(image_data.image, image_data.tags)

        # Mock new tag scheme in bundle image repository, so no tag is retrieved for the version.
        mock_list_repo_tags_with_filter_tag_name(
            task_bundle_signature_scan_test_data.image,
            [],
            empty_for_versions=task_bundle_signature_scan_test_data.nonexistent_tags,
        )

    def _mock_pipeline_file(self, repo_path: Path, content: str) -> Path:
        tekton_dir = repo_path / ".tekton"
        tekton_dir.mkdir()
        pipeline_file = tekton_dir / "component-pipeline.yaml"
        pipeline_file.write_text(content)
        return pipeline_file

    @responses.activate
    @pytest.mark.parametrize("use_linked_migrations", [True, False])
    @pytest.mark.parametrize("use_upgrades_file", [True, False])
    def test_apply_migrations(
        self,
        use_linked_migrations,
        use_upgrades_file,
        pipeline_yaml_with_various_indent_styles,
        monkeypatch,
        tmp_path,
        caplog,
    ):
        caplog.set_level(level=logging.INFO, logger="migrate")

        monkeypatch.setattr("pipeline_migration.actions.migrate.Registry", MockRegistry)
        self._mock_quay_list_tags()

        pipeline_file = self._mock_pipeline_file(tmp_path, pipeline_yaml_with_various_indent_styles)

        # Verified later
        origin_style = YAMLStyle.detect(pipeline_file)

        tb_upgrades = [
            {
                "depName": TASK_BUNDLE_CLONE,
                "currentValue": "0.1",
                "currentDigest": "sha256:492fb9ae4e7e",
                "newValue": "0.1",
                "newDigest": "sha256:c4bb69a3a08f",
                "depTypes": ["tekton-bundle"],
                "packageFile": str(pipeline_file.relative_to(tmp_path)),
                "parentDir": pipeline_file.parent.name,
            },
            # Following two should be excluded due to the depTypes and do not affect the migration.
            {
                "depName": APP_IMAGE_REPO,
                "currentValue": "0.2",
                "currentDigest": generate_digest(),
                "newValue": "0.7",
                "newDigest": generate_digest(),
                "depTypes": ["tekton-bundle"],
                "packageFile": str(pipeline_file.relative_to(tmp_path)),
                "parentDir": pipeline_file.parent.name,
            },
            {
                "depName": "registry.access.redhat.com/ubi9/ubi",
                "currentValue": "9.3-1",
                "currentDigest": "",
                "newValue": "9.3-2",
                "newDigest": "",
                "depTypes": ["tekton-step-image"],
                "packageFile": "path/to/build-file.yaml",
                "parentDir": "path/to",
            },
            # Empty upgrade range is empty for this bundle upgrade.
            {
                "depName": TASK_BUNDLE_SIGNATURE_SCAN,
                "currentValue": "0.2",
                "currentDigest": "sha256:ab2fb9ae4e7e",
                "newValue": "0.2",
                "newDigest": "sha256:cdbb69a3a08f",
                "depTypes": ["tekton-bundle"],
                "packageFile": str(pipeline_file.relative_to(tmp_path)),
                "parentDir": pipeline_file.parent.name,
            },
        ]

        # Renovate runs migration tool from the root of the git repository.
        # This change simulates that behavior.
        monkeypatch.chdir(tmp_path)

        if use_upgrades_file:
            upgrades_file_path = tmp_path / "upgrades-file.json"
            upgrades_file_path.write_text(json.dumps(tb_upgrades))
            cli_cmd = ["pmt", "migrate", "-f", str(upgrades_file_path)]
        else:
            cli_cmd = ["pmt", "migrate", "-u", json.dumps(tb_upgrades)]

        # Nothing change to the CLI command if using linked migrations.
        # Linked migrations are used by default.
        if not use_linked_migrations:
            cli_cmd.append("--use-legacy-resolver")

        monkeypatch.setattr("sys.argv", cli_cmd)

        migration_steps = [
            content
            for image_data in MockRegistry.test_data
            for _, content in image_data.blobs.items()
        ]

        def _subprocess_run(cmd, *args, **kwargs):
            pipeline_file = cmd[-1]
            assert pipeline_file == tb_upgrades[0]["packageFile"]
            migration_file = cmd[-2]
            assert Path(migration_file).read_bytes() in migration_steps

            # Modify the pipeline as if a migration is applied
            doc = load_yaml(pipeline_file)
            doc["spec"]["tasks"] += {"name": "summary"}
            dump_yaml(pipeline_file, doc)

            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("subprocess.run", _subprocess_run)

        assert entry_point() is None

        if use_linked_migrations:
            log_text = f"Upgrade range is empty for {TASK_BUNDLE_SIGNATURE_SCAN}."
            assert log_text in caplog.text

        # Verify result formatting
        cur_style = YAMLStyle.detect(pipeline_file)
        assert cur_style.indentation.is_consistent
        if origin_style.indentation.is_consistent:
            assert origin_style.indentation.levels == cur_style.indentation.levels
        else:
            assert cur_style.indentation.levels == [0]

    @responses.activate
    def test_non_existing_package_file(self, monkeypatch, tmp_path, caplog):
        """Migrate should stop if package file included in an upgrade does not exist"""

        some_dir = tmp_path / "some_dir"
        some_dir.mkdir()
        monkeypatch.chdir(some_dir)

        upgrades = [
            {
                "depName": TASK_BUNDLE_CLONE,
                "currentValue": "0.1",
                "currentDigest": "sha256:492fb9ae4e7e",
                "newValue": "0.1",
                "newDigest": "sha256:c4bb69a3a08f",
                "depTypes": ["tekton-bundle"],
                "packageFile": ".tekton/pull.yaml",
                "parentDir": ".tekton",
            },
        ]

        monkeypatch.setattr("pipeline_migration.actions.migrate.Registry", MockRegistry)
        self._mock_quay_list_tags()

        cli_cmd = ["pmt", "migrate", "-u", json.dumps(upgrades)]
        monkeypatch.setattr("sys.argv", cli_cmd)

        assert entry_point() == 1
        package_file = upgrades[0]["packageFile"]
        log_msg = f"Pipeline file does not exist: {package_file}"
        assert log_msg in caplog.text


def test_entry_point_should_catch_error(monkeypatch, caplog):
    cli_cmd = ["pmt", "migrate", "--use-legacy-resolver", "-u", json.dumps(UPGRADES)]
    monkeypatch.setattr("sys.argv", cli_cmd)
    assert entry_point() == 1
    assert "Cannot do migration for pipeline." in caplog.text
    assert "Traceback (most recent call last)" in caplog.text


@pytest.mark.parametrize(
    "upgrades,expected_err_msgs",
    [
        ["renovate upgrades which is not a encoded JSON string", ["Expecting value:"]],
        [f'[{{"depName": "{TASK_BUNDLE_CLONE}"}}]', ["does not pass schema validation:"]],
        pytest.param(
            json.dumps(
                [
                    {
                        "depName": TASK_BUNDLE_CLONE,
                        "currentValue": "0.1",
                        "currentDigest": "sha256:digest",
                        "newValue": "0.1",
                        "newDigest": generate_digest(),
                        "depTypes": ["tekton-bundle"],
                        "packageFile": "path/to/pipeline-run.yaml",
                        "parentDir": "path/to",
                    },
                ],
            ),
            ["does not pass schema validation:"],
            id="invalid-digest-for-currentDigest",
        ),
        pytest.param(
            json.dumps(
                [
                    {
                        "depName": TASK_BUNDLE_CLONE,
                        "currentValue": "0.1",
                        "currentDigest": generate_digest(),
                        "newValue": "0.1",
                        "newDigest": "sha256:digest",
                        "depTypes": ["tekton-bundle"],
                        "packageFile": "path/to/pipeline-run.yaml",
                        "parentDir": "path/to",
                    },
                ],
            ),
            ["does not pass schema validation:"],
            id="invalid-digest-for-newDigest",
        ),
    ],
)
def test_cli_stops_if_input_upgrades_is_invalid(upgrades, expected_err_msgs, monkeypatch, caplog):
    cli_cmd = ["pmt", "migrate", "-u", upgrades]
    monkeypatch.setattr("sys.argv", cli_cmd)
    assert entry_point() == 1
    for err_msg in expected_err_msgs:
        assert err_msg in caplog.text


@pytest.mark.parametrize("upgrades", ["", "[]", "[{}]"])
@pytest.mark.parametrize("use_upgrades_file", [True, False])
def test_do_nothing_if_input_upgrades_is_empty(upgrades, use_upgrades_file, monkeypatch, tmp_path):
    if use_upgrades_file:
        upgrades_file_path = tmp_path / "upgrades.json"
        upgrades_file_path.write_text(upgrades)
        cli_cmd = ["pmt", "migrate", "-f", str(upgrades_file_path)]
    else:
        cli_cmd = ["pmt", "migrate", "-u", upgrades]

    monkeypatch.setattr("sys.argv", cli_cmd)

    called = [False]

    def _migrate(*args, **kwargs):
        called[0] = True

    monkeypatch.setattr("pipeline_migration.actions.migrate", _migrate)

    assert entry_point() is None
    assert not called[0]


class TestBundleUpgradeByLinkedMigration:
    """Test applying migration by checking linked migration"""


mock_image_digest: Final[str] = generate_digest()
mock_image_digest_2: Final[str] = generate_digest()


@pytest.mark.parametrize(
    "upgrades_json_s,expected",
    [
        ["", "not a valid encoded JSON string"],
        [" ", "not a valid encoded JSON string"],
        ["depName", "not a valid encoded JSON string"],
        pytest.param("{}", "is not a list", id="ignore-unexpected-mapping"),
        pytest.param("100", "is not a list", id="skip-handling-malformed-input-upgrades"),
        pytest.param("[]", [], id="empty-upgrades-list-results-in-empty-result"),
        pytest.param("[{}]", [], id="ignore-falsy-objects"),
        pytest.param(
            json.dumps([{"currentValue": "0.2"}]),
            "does not have value of field depName",
            id="depName-is-not-included",
        ),
        pytest.param(
            json.dumps(
                [
                    {
                        "depName": "",
                        "currentValue": "0.1",
                        "currentDigest": mock_image_digest,
                        "newValue": "0.1",
                        "newDigest": mock_image_digest,
                        "packageFile": ".tekton/pipeline.yaml",
                        "parentDir": ".tekton",
                        "depTypes": ["tekton-bundle", "some-manager"],
                    },
                ],
            ),
            "does not have value of field depName",
            id="depName-is-included-but-empty",
        ),
        pytest.param(
            json.dumps(
                [
                    {
                        "depName": TASK_BUNDLE_CLONE,
                        "currentValue": "",
                        "currentDigest": generate_digest(),
                        "newValue": "0.1",
                        "newDigest": generate_digest(),
                        "packageFile": ".tekton/pipeline.yaml",
                        "parentDir": ".tekton",
                    },
                ],
            ),
            "Property currentValue is empty",
            id="empty-property-digest",
        ),
        pytest.param(
            json.dumps(
                [
                    {
                        "depName": TASK_BUNDLE_CLONE,
                        "currentValue": "0.1",
                        "currentDigest": generate_digest(),
                        "newValue": "0.1",
                        "newDigest": generate_digest(),
                        "packageFile": ".tekton/pipeline.yaml",
                        "parentDir": ".tekton",
                    },
                ],
            ),
            "depTypes.+is a required property",
            id="missing-depTypes-property",
        ),
        pytest.param(
            json.dumps([{"depName": TASK_BUNDLE_CLONE}]),
            "is a required property",
            id="missing-multiple-properties",
        ),
        pytest.param(
            json.dumps(
                [
                    {
                        "depName": TASK_BUNDLE_CLONE,
                        "currentValue": "0.1",
                        "currentDigest": generate_digest(),
                        "newValue": "0.1",
                        "newDigest": generate_digest(),
                        "packageFile": ".tekton/pipeline.yaml",
                        "parentDir": ".tekton",
                        "depTypes": ["some-manager"],
                    },
                ],
            ),
            [],
            id="missing-tekton-bundle-in-depTypes",
        ),
        pytest.param(
            json.dumps(
                [
                    {
                        "depName": APP_IMAGE_REPO,
                        "currentValue": "0.1",
                        "currentDigest": generate_digest(),
                        "newValue": "0.1",
                        "newDigest": generate_digest(),
                        "packageFile": ".tekton/pipeline.yaml",
                        "parentDir": ".tekton",
                        "depTypes": ["tekton-bundle"],
                    },
                ],
            ),
            [],
            id="cleanup-image-not-from-known-image-repo",
        ),
        pytest.param(
            json.dumps(
                [
                    {
                        "depName": TASK_BUNDLE_CLONE,
                        "currentValue": "0.1",
                        "currentDigest": mock_image_digest,
                        "newValue": "0.1",
                        "newDigest": mock_image_digest,
                        "packageFile": ".tekton/pipeline.yaml",
                        "parentDir": ".tekton",
                        "depTypes": ["tekton-bundle", "some-manager"],
                    },
                ],
            ),
            [
                {
                    "depName": TASK_BUNDLE_CLONE,
                    "currentValue": "0.1",
                    "currentDigest": mock_image_digest,
                    "newValue": "0.1",
                    "newDigest": mock_image_digest,
                    "packageFile": ".tekton/pipeline.yaml",
                    "parentDir": ".tekton",
                    "depTypes": ["tekton-bundle", "some-manager"],
                },
            ],
            id="normal-work",
        ),
        pytest.param(
            json.dumps(
                [
                    {},
                    {
                        "depName": TASK_BUNDLE_CLONE,
                        "currentValue": "0.1",
                        "currentDigest": mock_image_digest,
                        "newValue": "0.1",
                        "newDigest": mock_image_digest,
                        "packageFile": ".tekton/pipeline.yaml",
                        "parentDir": ".tekton",
                        "depTypes": ["tekton-bundle", "some-manager"],
                    },
                    {
                        "depName": "registry.access.redhat.com/ubi9/ubi",
                        "currentValue": "9.2",
                        "currentDigest": "",
                        "newValue": "9.3",
                        "newDigest": "",
                        "packageFile": ".tekton/pipeline.yaml",
                        "parentDir": ".tekton",
                        "depTypes": ["tekton-image-step"],
                    },
                ],
            ),
            [
                {
                    "depName": TASK_BUNDLE_CLONE,
                    "currentValue": "0.1",
                    "currentDigest": mock_image_digest,
                    "newValue": "0.1",
                    "newDigest": mock_image_digest,
                    "packageFile": ".tekton/pipeline.yaml",
                    "parentDir": ".tekton",
                    "depTypes": ["tekton-bundle", "some-manager"],
                },
            ],
            id="normal-work-by-cleaning-up-the-unexpected-upgrade",
        ),
        pytest.param(
            json.dumps(
                [
                    "set_local_test",
                    {
                        "depName": TASK_BUNDLE_CLONE,
                        "currentValue": "0.1",
                        "currentDigest": mock_image_digest,
                        "newValue": "0.1",
                        "newDigest": mock_image_digest,
                        "packageFile": ".tekton/pipeline.yaml",
                        "parentDir": ".tekton",
                        "depTypes": ["tekton-bundle"],
                    },
                    {
                        "depName": APP_IMAGE_REPO,
                        "currentValue": "0.1",
                        "currentDigest": mock_image_digest_2,
                        "newValue": "0.1",
                        "newDigest": mock_image_digest_2,
                        "packageFile": ".tekton/pipeline.yaml",
                        "parentDir": ".tekton",
                        "depTypes": ["tekton-bundle"],
                    },
                ],
            ),
            [
                {
                    "depName": TASK_BUNDLE_CLONE,
                    "currentValue": "0.1",
                    "currentDigest": mock_image_digest,
                    "newValue": "0.1",
                    "newDigest": mock_image_digest,
                    "packageFile": ".tekton/pipeline.yaml",
                    "parentDir": ".tekton",
                    "depTypes": ["tekton-bundle"],
                },
                {
                    "depName": APP_IMAGE_REPO,
                    "currentValue": "0.1",
                    "currentDigest": mock_image_digest_2,
                    "newValue": "0.1",
                    "newDigest": mock_image_digest_2,
                    "packageFile": ".tekton/pipeline.yaml",
                    "parentDir": ".tekton",
                    "depTypes": ["tekton-bundle"],
                },
            ],
            id="normal-work-with-local-test-set",
        ),
    ],
)
def test_clean_upgrades(upgrades_json_s, expected, monkeypatch):
    if isinstance(expected, str):
        with pytest.raises(InvalidRenovateUpgradesData, match=expected):
            clean_upgrades(upgrades_json_s)
    else:
        if '"set_local_test",' in upgrades_json_s:
            upgrades_json_s = upgrades_json_s.replace('"set_local_test",', "")
            monkeypatch.setenv("PMT_LOCAL_TEST", "1")
        assert clean_upgrades(upgrades_json_s) == expected


def test_missing_both_upgrades_args(caplog, monkeypatch):
    cli_cmd = ["pmt", "migrate"]
    monkeypatch.setattr("sys.argv", cli_cmd)
    with caplog.at_level(logging.INFO):
        entry_point()
        assert "Empty input upgrades" in caplog.text


def test_nonexisting_upgrades_file(capsys, monkeypatch, tmp_path):
    upgrades_file = str(tmp_path / "upgrades.json")
    cli_cmd = ["pmt", "migrate", "-f", upgrades_file]
    monkeypatch.setattr("sys.argv", cli_cmd)
    monkeypatch.setattr("sys.exit", lambda *args, **kwargs: 0)
    entry_point()
    assert f"Upgrades file {upgrades_file} does not exist" in capsys.readouterr().err
