import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import responses
import pytest
from oras.types import container_type

from pipeline_migration.cli import entry_point
from pipeline_migration.migrate import (
    ANNOTATION_HAS_MIGRATION,
    ANNOTATION_PREVIOUS_MIGRATION_BUNDLE,
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

from tests.test_migrate import APP_IMAGE_REPO, PIPELINE_DEFINITION, TASK_BUNDLE_CLONE
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
    manifests: dict[str, dict]  # manifest digest => image manifest
    referrers: dict[str, dict]  # manifest digest => image index
    blobs: dict[str, bytes]  # layer digest => artifact content


task_bundle_clone_test_data = ImageTestData(
    image=TASK_BUNDLE_CLONE,
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
            "annotations": {ANNOTATION_HAS_MIGRATION: "true"},
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
            "annotations": {ANNOTATION_HAS_MIGRATION: "true"},
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
                        "dev.konflux-ci.task.migration": "true",
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
                        "dev.konflux-ci.task.migration": "true",
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


class TestMigrateSingleTaskBundleUpgrade:

    def _mock_quay_list_tags(self):
        for image_data in MockRegistry.test_data:
            tags = [
                {"name": f"0.1-{generate_git_sha()}", "manifest_digest": digest}
                for digest, manifest_json in image_data.manifests.items()
                if manifest_json["config"]["mediaType"] == MEDIA_TYPE_OCI_IMAGE_CONFIG_V1
            ]
            c = Container(image_data.image)
            api_url = f"https://quay.io/api/v1/repository/{c.api_prefix}/tag/"
            responses.get(
                f"{api_url}?page=1&onlyActiveTags=true",
                json={"tags": tags, "page": 1, "has_additional": False},
            )

    def _mock_pipeline_file(self, tmp_path: Path) -> Path:
        tekton_dir = tmp_path / ".tekton"
        tekton_dir.mkdir()
        pipeline_file = tekton_dir / "component-pipeline.yaml"
        pipeline_file.write_text(PIPELINE_DEFINITION)
        return pipeline_file

    @responses.activate
    @pytest.mark.parametrize("use_linked_migrations", [True, False])
    def test_apply_migrations(self, use_linked_migrations, monkeypatch, tmp_path):
        monkeypatch.setattr("pipeline_migration.migrate.Registry", MockRegistry)
        self._mock_quay_list_tags()

        pipeline_file = self._mock_pipeline_file(tmp_path)

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
        ]

        # Renovate runs migration tool from the root of the git repository.
        # This change simulates that behavior.
        monkeypatch.chdir(tmp_path)

        cli_cmd = ["pmt", "-u", json.dumps(tb_upgrades)]

        if use_linked_migrations:
            test_data = MockRegistry.test_data[0]
            # Set annotation to link bundles that have migration
            annotations = test_data.manifests["sha256:c4bb69a3a08f"]["annotations"]
            annotations[ANNOTATION_PREVIOUS_MIGRATION_BUNDLE] = "sha256:f23dc7cd74ba"
            annotations = test_data.manifests["sha256:f23dc7cd74ba"]["annotations"]
            annotations[ANNOTATION_PREVIOUS_MIGRATION_BUNDLE] = ""
            # Nothing change to the CLI command. Linked migrations are used by default.
        else:
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
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("subprocess.run", _subprocess_run)

        assert entry_point() is None


def test_entry_point_should_catch_error(monkeypatch, caplog):
    cli_cmd = ["pmt", "--use-legacy-resolver", "-u", json.dumps(UPGRADES)]
    monkeypatch.setattr("sys.argv", cli_cmd)
    assert entry_point() == 1
    assert "Cannot do migration for pipeline." in caplog.text
    assert "Traceback (most recent call last)" in caplog.text


@pytest.mark.parametrize(
    "upgrades,expected_err_msgs",
    [
        ["renovate upgrades which is not a encoded JSON string", ["Expecting value:"]],
        ["{}", ["does not pass schema validation:"]],
        ["[{}]", ["does not pass schema validation:"]],
        [f'[{{"depName": "{TASK_BUNDLE_CLONE}"}}]', ["does not pass schema validation:"]],
        [
            json.dumps(
                [
                    {
                        "depName": TASK_BUNDLE_CLONE,
                        "currentValue": "0.1",
                        "currentDigest": "sha256:digest",  # invalid
                        "newValue": "0.1",
                        "newDigest": generate_digest(),
                        "depTypes": ["tekton-bundle"],
                        "packageFile": "path/to/pipeline-run.yaml",
                        "parentDir": "path/to",
                    },
                ],
            ),
            ["does not pass schema validation:"],
        ],
        [
            json.dumps(
                [
                    {
                        "depName": TASK_BUNDLE_CLONE,
                        "currentValue": "0.1",
                        "currentDigest": generate_digest(),
                        "newValue": "0.1",
                        "newDigest": "sha256:digest",  # invalid
                        "depTypes": ["tekton-bundle"],
                        "packageFile": "path/to/pipeline-run.yaml",
                        "parentDir": "path/to",
                    },
                ],
            ),
            ["does not pass schema validation:"],
        ],
    ],
)
def test_cli_stops_if_input_upgrades_is_invalid(upgrades, expected_err_msgs, monkeypatch, caplog):
    cli_cmd = ["pmt", "-u", upgrades]
    monkeypatch.setattr("sys.argv", cli_cmd)
    assert entry_point() == 1
    for err_msg in expected_err_msgs:
        assert err_msg in caplog.text


class TestBundleUpgradeByLinkedMigration:
    """Test applying migration by checking linked migration"""
