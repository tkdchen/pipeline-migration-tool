import subprocess
from typing import Final

import pytest
import responses

from argparse import ArgumentTypeError
from responses.matchers import query_param_matcher

from pipeline_migration.actions.add_task import extract_task_names, git_add
from pipeline_migration.actions.add_task import get_task_bundle_reference
from tests.utils import generate_digest


IMAGE_DIGEST: Final = generate_digest()


@responses.activate
@pytest.mark.parametrize(
    "bundle_ref, responded_tags, expected_error, expected_output",
    [
        pytest.param(
            "",
            None,
            pytest.raises(ArgumentTypeError, match="is not a valid image reference"),
            None,
            id="empty-string",
        ),
        pytest.param(
            f"some-registry.io/app@{IMAGE_DIGEST}",
            None,
            pytest.raises(ArgumentTypeError, match="missing tag"),
            None,
            id="other-registry-missing-tag",
        ),
        pytest.param(
            "some-registry.io/app:0.1",
            None,
            pytest.raises(ArgumentTypeError, match="missing digest"),
            None,
            id="other-registry-missing-digest",
        ),
        pytest.param(
            f"some-registry.io/app:0.1@{IMAGE_DIGEST}",
            None,
            None,
            None,  # input == output
            id="other-registry-valid-full-ref",
        ),
        pytest.param(
            f"quay.io/org/app@{IMAGE_DIGEST}",
            None,
            pytest.raises(ArgumentTypeError, match="missing tag"),
            None,
            id="quay-missing-tag",
        ),
        pytest.param(
            "quay.io/org/app:0.1",
            [{"name": "0.1", "manifest_digest": IMAGE_DIGEST}],
            None,
            f"quay.io/org/app:0.1@{IMAGE_DIGEST}",  # input != output (Auto-resolution)
            id="quay-valid-ref-without-digest-resolves",
        ),
        pytest.param(
            f"quay.io/org/app:0.1@{IMAGE_DIGEST}",
            [],
            pytest.raises(ArgumentTypeError, match="tag 0.1 does not exist"),
            None,
            id="quay-tag-does-not-exist",
        ),
        pytest.param(
            f"quay.io/org/app:0.1@{IMAGE_DIGEST}",
            [{"name": "0.1", "manifest_digest": generate_digest()}],
            pytest.raises(ArgumentTypeError, match="Mismatch digest"),
            None,
            id="quay-mismatch-digest",
        ),
        pytest.param(
            f"quay.io/org/app:0.1@{IMAGE_DIGEST}",
            [{"name": "0.1", "manifest_digest": IMAGE_DIGEST}],
            None,
            None,  # input == output
            id="quay-valid-full-ref",
        ),
        pytest.param(
            f"quay.io/org/app:latest@{IMAGE_DIGEST}",
            [{"name": "latest", "manifest_digest": IMAGE_DIGEST}],
            None,
            None,  # input == output
            id="quay-valid-latest-tag",
        ),
        pytest.param(
            f"quay.io/org/app:latest@{IMAGE_DIGEST}",
            [{"name": "latest", "manifest_digest": generate_digest()}],
            pytest.raises(ArgumentTypeError, match="Mismatch digest"),
            None,
            id="quay-mismatch-digest-latest",
        ),
    ],
)
def test_task_bundle_reference(bundle_ref, responded_tags, expected_error, expected_output) -> None:
    if responded_tags is not None:
        tag = bundle_ref.split("@")[0].split(":")[-1]
        params = {"page": "1", "onlyActiveTags": "true", "specificTag": tag}
        responses.get(
            "https://quay.io/api/v1/repository/org/app/tag/",
            json={"tags": responded_tags, "has_additional": False},
            match=[query_param_matcher(params)],
        )
    if expected_error:
        with expected_error:
            get_task_bundle_reference(bundle_ref)
    else:
        # if expected_output is provided, we compare against that.
        # otherwise, we assume the input string remains unchanged.
        expected = expected_output if expected_output else bundle_ref
        assert get_task_bundle_reference(bundle_ref) == expected


class TestGitAdd:

    def test_failure_if_given_path_is_not_absolute(self):
        with pytest.raises(ValueError, match="is not an absolute path"):
            git_add(".tekton/pull.yaml")

    def test_given_file_is_added(self, tmp_path, monkeypatch):
        file_to_add = tmp_path / "pr-pipeline.yaml"
        file_to_add.write_text("")

        git_index = []

        def _run(*args, **kwargs):
            cwd = kwargs.get("cwd")
            assert cwd == tmp_path
            git_index.append(file_to_add)

        monkeypatch.setattr("subprocess.run", _run)

        git_add(file_to_add)
        assert git_index[0] == file_to_add

    def test_git_command_failure(self, tmp_path, caplog, monkeypatch):
        file_to_add = tmp_path / "pr-pipeline.yaml"
        file_to_add.write_text("")

        def _run(*args, **kwargs):
            check = kwargs.get("check")
            assert check
            capture_output = kwargs.get("capture_output")
            assert capture_output
            raise subprocess.CalledProcessError(128, cmd=args[0], stderr="git failure")

        monkeypatch.setattr("subprocess.run", _run)

        git_add(file_to_add)

        assert f"{str(file_to_add)} is not added to git index: git failure" in caplog.text


@pytest.mark.parametrize(
    "tasks,expected_result,expected_log",
    [
        pytest.param([], (set(), set()), None, id="empty-tasks"),
        pytest.param(
            [{}], (set(), set()), "Cannot get pipeline task name", id="no-pipeline-task-name"
        ),
        pytest.param(
            [{"name": "clone"}],
            ({"clone"}, set()),
            "Task clone does not have taskRef",
            id="task-is-referenced-by-taskRef",
        ),
        pytest.param(
            [{"name": "clone", "taskRef": {"resolver": "git"}}],
            ({"clone"}, set()),
            None,
            id="task-is-not-referenced-by-bundle-resolver",
        ),
        pytest.param(
            [
                {
                    "name": "clone",
                    "taskRef": {
                        "resolver": "bundles",
                        "params": [
                            {"name": "kind", "value": "task"},
                        ],
                    },
                },
            ],
            ({"clone"}, set()),
            "Task clone uses tekton bundle resolver but no actual task name is specified",
            id="missing-actual-task-name",
        ),
        pytest.param(
            [
                {
                    "name": "build-container",
                    "taskRef": {
                        "resolver": "bundles",
                        "params": [
                            {"name": "kind", "value": "task"},
                            {"name": "name", "value": "buildah-oci-ta"},
                            {
                                "name": "bundle",
                                "value": "quay.io/org/buildah-oci-ta:0.1@sha256:12345",
                            },
                        ],
                    },
                },
            ],
            ({"build-container"}, {"buildah-oci-ta"}),
            None,
            id="get-names",
        ),
        pytest.param(
            [
                {
                    "name": "init",
                    "taskRef": {
                        "resolver": "bundles",
                        "params": [
                            {"name": "kind", "value": "task"},
                            {"name": "name", "value": "init"},
                            {"name": "bundle", "value": "bundle-ref"},
                        ],
                    },
                },
                {
                    "name": "build-container",
                    "taskRef": {
                        "resolver": "bundles",
                        "params": [
                            {"name": "kind", "value": "task"},
                            {"name": "name", "value": "buildah-oci-ta"},
                            {"name": "bundle", "value": "bundle-ref"},
                        ],
                    },
                },
                {
                    "name": "sast-coverity-check",
                    "taskRef": {
                        "resolver": "bundles",
                        "params": [
                            {"name": "kind", "value": "task"},
                            {"name": "name", "value": "sast-coverity-check-oci-ta"},
                            {"name": "bundle", "value": "bundle-ref"},
                        ],
                    },
                },
            ],
            (
                {"init", "build-container", "sast-coverity-check"},
                {"init", "buildah-oci-ta", "sast-coverity-check-oci-ta"},
            ),
            None,
            id="get-names-from-a-number-of-tasks",
        ),
    ],
)
def test_extract_task_names(tasks, expected_result, expected_log, caplog):
    result = extract_task_names(tasks)
    assert result == expected_result
    if expected_log is not None:
        assert expected_log in caplog.text
