import socket
import subprocess
from typing import Final, Type

import pytest
import responses

from argparse import ArgumentTypeError
from responses.matchers import query_param_matcher

from requests.exceptions import HTTPError

from pipeline_migration.actions.add_task import git_add
from pipeline_migration.actions.add_task import InconsistentBundleBuild
from pipeline_migration.actions.add_task import konflux_task_bundle_reference
from pipeline_migration.actions.add_task import KonfluxBuildDefinitions
from pipeline_migration.actions.add_task import KonfluxTaskFileNotExist
from pipeline_migration.actions.add_task import KonfluxTaskNotExist
from pipeline_migration.actions.add_task import search_pipeline_files
from tests.utils import generate_digest


IMAGE_DIGEST: Final = generate_digest()


@responses.activate
@pytest.mark.parametrize(
    "bundle_ref,responded_tags,expected_error",
    [
        ["", None, pytest.raises(ArgumentTypeError, match="is not a valid image reference")],
        [
            "some-registry.io/app@sha256:1234",
            None,
            pytest.raises(ArgumentTypeError, match="only support adding Konflux tasks"),
        ],
        [
            "quay.io/org/app@sha256:1234",
            None,
            pytest.raises(ArgumentTypeError, match="missing tag"),
        ],
        ["quay.io/org/app:0.1", None, pytest.raises(ArgumentTypeError, match="missing digest")],
        pytest.param(
            f"quay.io/org/app:0.1@{IMAGE_DIGEST}",
            [],
            pytest.raises(ArgumentTypeError, match="tag 0.1 does not exist"),
            id="tag-does-not-exist-in-image-repo",
        ),
        pytest.param(
            f"quay.io/org/app:0.1@{IMAGE_DIGEST}",
            [{"name": "0.1", "manifest_digest": generate_digest()}],
            pytest.raises(ArgumentTypeError, match="Mismatch digest"),
            id="mismatch-digest",
        ),
        pytest.param(
            f"quay.io/org/app:0.1@{IMAGE_DIGEST}",
            [{"name": "0.1", "manifest_digest": IMAGE_DIGEST}],
            None,
            id="valid-image-ref",
        ),
        pytest.param(
            f"quay.io/org/app:latest@{IMAGE_DIGEST}",
            [{"name": "latest", "manifest_digest": IMAGE_DIGEST}],
            None,
            id="valid-image-ref-with-latest-tag",
        ),
        pytest.param(
            f"quay.io/org/app:latest@{IMAGE_DIGEST}",
            [{"name": "latest", "manifest_digest": generate_digest()}],
            pytest.raises(ArgumentTypeError, match="Mismatch digest"),
            id="input-image-ref-with-mismatch-digest-by-latest-tag",
        ),
        pytest.param(
            f"quay.io/org/app:latest@{IMAGE_DIGEST}",
            [],
            pytest.raises(ArgumentTypeError, match="tag latest does not exist"),
            id="latest-tag-does-not-exist-in-image-repo",
        ),
    ],
)
def test_konflux_task_bundle_reference(bundle_ref, responded_tags, expected_error) -> None:
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
            konflux_task_bundle_reference(bundle_ref)
    else:
        assert bundle_ref == konflux_task_bundle_reference(bundle_ref)


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


class TestSearchPipelineFiles:

    def setup_method(self, method):
        self.sock = None

    def teardown_method(self, method):
        if self.sock:
            self.sock.close()

    @pytest.mark.parametrize("data", [[], [""]])
    def test_empty_input_files_or_dirs(self, data):
        assert list(search_pipeline_files(data)) == []

    def _create_noisy_files(self, component_a_repo, component_b_repo):
        text_file = component_b_repo.tekton_dir / "test.txt"
        text_file.write_text("hello world")

        invalid_yaml_file = component_b_repo.tekton_dir / "invalid.yaml"
        invalid_yaml_file.write_bytes(b"\x00")

        common_yaml_file = component_a_repo.tekton_dir / "common.yaml"
        common_yaml_file.write_text("book: Python programming")

        yaml_files = component_b_repo.tekton_dir.glob("*.yaml")
        symlink_to_pr_yaml = component_b_repo.tekton_dir / "link-to.yaml"
        symlink_to_pr_yaml.symlink_to(next(yaml_files))

        # Create a special socket file for satisfying the code path coverage
        sock_file = component_b_repo.tekton_dir / "app.sock"
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(sock_file))

    def test_pipeline_files_from_given_files(
        self, component_a_repo, component_b_repo, caplog, tmp_path, monkeypatch
    ):
        self._create_noisy_files(component_a_repo, component_b_repo)

        files = map(
            str,
            [
                *[p.name for p in component_a_repo.tekton_dir.iterdir()],
                *component_b_repo.tekton_dir.iterdir(),
            ],
        )

        # for testing converting relative path to absolute path
        monkeypatch.chdir(component_a_repo.tekton_dir)

        found = list(search_pipeline_files(files))
        assert len(found) == 3

        expected = [
            str(component_a_repo.tekton_dir / "pr.yaml"),
            str(component_a_repo.tekton_dir / "push.yaml"),
            str(component_b_repo.tekton_dir / "build-pipeline.yaml"),
        ]
        assert sorted([item[0] for item in found]) == sorted(expected)

    def test_search_pipeline_files_from_directory(
        self, component_a_repo, component_b_repo, tmp_path, caplog
    ):
        """Ensure the search without recursive walk through directories"""

        self._create_noisy_files(component_a_repo, component_b_repo)

        sub_dir = component_b_repo.tekton_dir / "sub_dir"
        sub_dir.mkdir()
        (sub_dir / "another.file").touch()

        found = list(search_pipeline_files([str(component_b_repo.tekton_dir)]))

        assert len(found) == 1
        original_pipeline_file = found[0][0]
        assert original_pipeline_file == str(component_b_repo.tekton_dir / "build-pipeline.yaml")


class TestDetermineTaskLatestVersion:

    TASK_NAME: Final = "clone"

    def setup_method(self, method):
        self.build_def = KonfluxBuildDefinitions()
        repo = KonfluxBuildDefinitions.DEFINITIONS_REPO
        self.api_url = f"https://api.github.com/repos/{repo}/contents/task/{self.TASK_NAME}"

    @responses.activate
    @pytest.mark.parametrize(
        "http_status,body,exception,msg",
        [
            [404, "", KonfluxTaskNotExist, r"Task .+ is not found"],
            [500, "", HTTPError, r"Server.+"],
            [200, "[]", ValueError, r"No version is found"],
            [200, '[{"name": "invalid-version"}]', ValueError, r"Malformed version"],
        ],
    )
    def test_various_failures(
        self, http_status: int, body: str, exception: Type[Exception], msg: str
    ):
        responses.get(self.api_url, body=body, status=http_status)
        with pytest.raises(exception, match=msg):
            self.build_def.determine_latest_version(self.TASK_NAME)

    @responses.activate
    @pytest.mark.parametrize(
        "versions,expected_version",
        [
            [[{"name": "0.1"}], "0.1"],
            [[{"name": "0.2"}, {"name": "0.1"}, {"name": "0.3"}, {"name": "0.10"}], "0.10"],
        ],
    )
    def test_determine_latest_version(self, versions, expected_version):
        responses.get(self.api_url, json=versions)
        assert expected_version == self.build_def.determine_latest_version(self.TASK_NAME)


class TestGetTaskLatestCommitSha:

    def setup_method(self, method):
        self.build_def = KonfluxBuildDefinitions()
        repo = KonfluxBuildDefinitions.DEFINITIONS_REPO
        self.api_url = f"https://api.github.com/repos/{repo}/commits"
        self.task_name = "clone"
        self.task_version = "0.3"
        self.http_get_params = {
            "path": f"task/{self.task_name}/{self.task_version}/{self.task_name}.yaml",
            "per_page": "1",
        }

    @responses.activate
    @pytest.mark.parametrize(
        "http_status,body,exception,msg",
        [
            [400, "", HTTPError, "Client Error"],
            [500, "", HTTPError, "Server Error"],
            [200, "[]", KonfluxTaskFileNotExist, r"Task file .+ does not exist"],
            [200, '[{"commit_hash": "1234"}]', ValueError, "response does not include field"],
        ],
    )
    def test_failures(self, http_status: int, body: str, exception: Type[Exception], msg: str):
        responses.get(
            self.api_url,
            body=body,
            status=http_status,
            match=[query_param_matcher(self.http_get_params)],
        )
        with pytest.raises(exception, match=msg):
            self.build_def.get_task_latest_commit_sha(self.task_name, self.task_version)

    @responses.activate
    def test_get_the_commit_sha(self):
        responses.get(
            self.api_url,
            json=[{"sha": "1234567"}],
            match=[query_param_matcher(self.http_get_params)],
        )
        commit_sha = self.build_def.get_task_latest_commit_sha(self.task_name, self.task_version)
        assert commit_sha == "1234567"


@responses.activate
@pytest.mark.parametrize(
    "tags,expected",
    [
        [[], None],
        [[{"name": "0.2", "manifest_digest": "sha256:908070"}], "sha256:908070"],
    ],
)
def test_konflux_build_definitions_get_digest(tags, expected):
    task_name = "clone"
    task_version = "0.2"
    build_def = KonfluxBuildDefinitions()

    api_url = (
        f"https://quay.io/api/v1/repository/{build_def.KONFLUX_IMAGE_ORG}/task-{task_name}/tag/"
    )
    api_params = {
        "page": "1",
        "onlyActiveTags": "true",
        "specificTag": task_version,
    }
    responses.get(
        api_url,
        json={"tags": tags, "has_additional": False},
        match=[query_param_matcher(api_params)],
    )

    assert build_def.get_digest("clone", task_version) == expected


@pytest.mark.parametrize("found_digest", [True, False])
def test_query_latest_bundle(found_digest, monkeypatch):
    tag = "0.1"
    image_digest = generate_digest()
    build_def = KonfluxBuildDefinitions()

    monkeypatch.setattr(
        build_def,
        "determine_latest_version",
        lambda *args, **kwargs: tag,
    )
    monkeypatch.setattr(
        build_def,
        "get_task_latest_commit_sha",
        lambda *args, **kwargs: 1234567,
    )
    monkeypatch.setattr(
        build_def,
        "get_digest",
        lambda *args, **kwargs: image_digest if found_digest else None,
    )

    if found_digest:
        bundle = build_def.query_latest_bundle("clone")
        expected = f"quay.io/konflux-ci/tekton-catalog/task-clone:{tag}@{image_digest}"
        assert bundle == expected
    else:
        msg = "does not have a task bundle built from"
        with pytest.raises(InconsistentBundleBuild, match=msg):
            build_def.query_latest_bundle("clone")


@pytest.mark.parametrize(
    "tasks,expected_result,expected_log",
    [
        pytest.param([], [], None, id="empty-tasks"),
        pytest.param([{}], [], "Cannot get pipeline task name", id="no-pipeline-task-name"),
        pytest.param(
            [{"name": "clone"}],
            [],
            "Task clone does not have taskRef",
            id="task-is-referenced-by-taskRef",
        ),
        pytest.param(
            [{"name": "clone", "taskRef": {"resolver": "git"}}],
            [],
            "Task clone does not use tekton bundle",
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
            [],
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
            [("build-container", "buildah-oci-ta")],
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
            [
                ("init", "init"),
                ("build-container", "buildah-oci-ta"),
                ("sast-coverity-check", "sast-coverity-check-oci-ta"),
            ],
            None,
            id="get-names-from-a-number-of-tasks",
        ),
    ],
)
def test_extract_task_names(tasks, expected_result, expected_log, caplog):
    result = list(KonfluxBuildDefinitions.extract_task_names(tasks))
    assert result == expected_result
    if expected_log is not None:
        assert expected_log in caplog.text
