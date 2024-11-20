from textwrap import dedent

import responses
import pytest

from typing import Final

from pipeline_migration.migrate import (
    determine_task_bundle_upgrades_range,
    TaskBundleUpgrade,
    InvalidRenovateUpgradesData,
    resolve_pipeline,
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


class TestFetchMigrationFile:
    """Test method fetch_migration_file"""


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
        with pytest.raises(ValueError, match="not a YAML file"):
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
