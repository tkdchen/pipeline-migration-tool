import itertools
import logging
import re
from pathlib import Path
from typing import Any, Final

import pytest
import responses
from responses.matchers import query_param_matcher
from ruamel.yaml.comments import CommentedSeq

from pipeline_migration.cli import entry_point
from pipeline_migration.pipeline import PipelineFileOperation
from pipeline_migration.types import FilePath
from pipeline_migration.utils import YAMLStyle
from tests.utils import generate_digest

KONFLUX_IMAGE_ORG: Final = "konflux-ci/tekton-catalog"

TASK_NAME: Final = "push"
IMAGE_DIGEST: Final = generate_digest()
BUNDLE_REF: Final = f"quay.io/{KONFLUX_IMAGE_ORG}/task-{TASK_NAME}:0.2@{IMAGE_DIGEST}"

TEST_TASK_VALUES = [f"{KONFLUX_IMAGE_ORG}/task-{TASK_NAME}", "0.2", IMAGE_DIGEST]

TASK_TEST: Final = "test"
TEST_BUNDLE_DIGEST: Final = generate_digest()
TEST_BUNDLE: Final = f"quay.io/{KONFLUX_IMAGE_ORG}/task-{TASK_TEST}:0.1@{TEST_BUNDLE_DIGEST}"


class VerifyUpdatedPipeline(PipelineFileOperation):

    def __init__(
        self,
        task_name: str,
        bundle_ref: str,
        pipeline_task_name: str = "",
        skip_checks: bool = False,
        run_after: list[str] | None = None,
        check_finally: bool = False,
        params: list[dict[str, str]] | None = None,
    ):
        self.task_name = task_name
        self.bundle_ref = bundle_ref
        self.pipeline_task_name = pipeline_task_name
        self.skip_checks = skip_checks
        self.run_after = run_after
        self.check_finally = check_finally
        self.params = params

    def handle_pipeline_file(self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle) -> None:
        if self.check_finally:
            tasks_section = loaded_doc["spec"].get("finally")
            assert tasks_section is not None, f"'{file_path}' is missing 'spec.finally' section"
        else:
            tasks_section = loaded_doc["spec"]["tasks"]
        self.verify(tasks_section)

    def handle_pipeline_run_file(
        self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle
    ) -> None:
        if self.check_finally:
            tasks_section = loaded_doc["spec"]["pipelineSpec"].get("finally")
            assert (
                tasks_section is not None
            ), f"'{file_path}' is missing 'spec.pipelineSpec.finally' section"
        else:
            tasks_section = loaded_doc["spec"]["pipelineSpec"]["tasks"]
        self.verify(tasks_section)

    def verify(self, tasks: CommentedSeq):
        expected_pipeline_task_name = self.pipeline_task_name or self.task_name
        tasks = [item for item in tasks if item["name"] == expected_pipeline_task_name]
        assert len(tasks) == 1
        expected_task_config: dict[str, Any] = {
            "name": expected_pipeline_task_name,
            "taskRef": {
                "resolver": "bundles",
                "params": [
                    {"name": "kind", "value": "task"},
                    {"name": "name", "value": self.task_name},
                    {"name": "bundle", "value": self.bundle_ref},
                ],
            },
        }
        if self.skip_checks:
            expected_task_config["when"] = [
                {"input": "$(params.skip-checks)", "operator": "in", "values": ["false"]}
            ]
        if self.run_after:
            expected_task_config["runAfter"] = self.run_after[:]
        if self.params:
            expected_task_config["params"] = self.params[:]
        assert tasks[0] == expected_task_config

    def check(self, file_path: str):
        return super().handle(file_path)


def mock_get_digest_for_specific_tag(image_repo: str, version: str, expected_digest: str) -> None:
    responses.get(
        f"https://quay.io/api/v1/repository/{image_repo}/tag/",
        json={"tags": [{"manifest_digest": expected_digest}], "has_additional": False},
        match=[
            query_param_matcher({"page": "1", "onlyActiveTags": "true", "specificTag": version}),
        ],
    )


@responses.activate
def test_use_bundle_ref(component_a_repo, monkeypatch):
    mock_get_digest_for_specific_tag(*TEST_TASK_VALUES)
    pipeline_file = component_a_repo.tekton_dir / "pr.yaml"

    cmd = ["pmt", "add-task", BUNDLE_REF, str(pipeline_file)]
    monkeypatch.setattr("sys.argv", cmd)

    entry_point()

    VerifyUpdatedPipeline(f"task-{TASK_NAME}", BUNDLE_REF).check(str(pipeline_file))


FILES_DIRS_COMBINATIONS = [
    "use_relative_tekton_dir",
    "specify_pipeline_files_explicitly",
    "search_pipelines_from_dirs",
    "mix_pipeline_files_and_dirs",
]


@pytest.fixture(params=FILES_DIRS_COMBINATIONS)
def files_dirs_combinations(request, component_a_repo, component_b_repo) -> list[str]:
    if request.param == FILES_DIRS_COMBINATIONS[0]:
        return []
    if request.param == FILES_DIRS_COMBINATIONS[1]:
        files = [
            *component_a_repo.tekton_dir.glob("*.yaml"),
            *component_b_repo.tekton_dir.glob("*.yaml"),
        ]
        return list(map(str, files))
    if request.param == FILES_DIRS_COMBINATIONS[2]:
        return [str(component_b_repo.tekton_dir)]
    if request.param == FILES_DIRS_COMBINATIONS[3]:
        return [
            *map(str, component_a_repo.tekton_dir.glob("*.yaml")),
            str(component_b_repo.tekton_dir),
        ]
    raise ValueError(f"unexpected fixture param {request.param}")


@responses.activate
def test_work_with_files_or_dirs(files_dirs_combinations, component_b_repo, monkeypatch):
    mock_get_digest_for_specific_tag(*TEST_TASK_VALUES)

    check_targets = list(map(Path, files_dirs_combinations))
    cmd = ["pmt", "add-task", BUNDLE_REF]

    if files_dirs_combinations:
        cmd.extend(files_dirs_combinations)
    else:
        # Test searching from relative .tekton/ directory
        monkeypatch.chdir(str(component_b_repo))
        check_targets.append(component_b_repo.tekton_dir)

    monkeypatch.setattr("sys.argv", cmd)

    entry_point()

    verifier = VerifyUpdatedPipeline(f"task-{TASK_NAME}", BUNDLE_REF)
    for item in check_targets:
        if item.is_dir():
            for yaml_file in item.glob("*.yaml"):
                verifier.check(str(yaml_file))
        else:
            verifier.check(str(item))


@responses.activate
@pytest.mark.parametrize(
    "depended_tasks",
    [
        pytest.param([], id="dont-depend-on-other-task"),
        pytest.param(["clone"], id="depend-on-single-task"),
        pytest.param(["clone", "build"], id="depend-on-multiple-tasks"),
        pytest.param(["error"], id="given-depended-task-is-unknown"),
    ],
)
def test_set_execution_order(request, depended_tasks, component_b_repo, caplog, monkeypatch):
    mock_get_digest_for_specific_tag(*TEST_TASK_VALUES)

    cmd = [
        "pmt",
        "add-task",
        BUNDLE_REF,
        str(component_b_repo.tekton_dir),
    ]
    for task_name in depended_tasks:
        cmd.append("--run-after")
        cmd.append(task_name)

    monkeypatch.setattr("sys.argv", cmd)

    if request.node.callspec.id == "given-depended-task-is-unknown":
        assert entry_point() == 1
        assert f"Task {depended_tasks[0]} does not exist" in caplog.text
    else:
        entry_point()

        verifier = VerifyUpdatedPipeline(f"task-{TASK_NAME}", BUNDLE_REF, run_after=depended_tasks)
        for yaml_file in component_b_repo.tekton_dir.glob("*.yaml"):
            verifier.check(str(yaml_file))


@responses.activate
@pytest.mark.parametrize(
    "params,expected_params",
    [
        pytest.param([], [], id="no-task-param-to-add"),
        pytest.param(
            ["verbose=true"], [{"name": "verbose", "value": "true"}], id="add-single-param"
        ),
        pytest.param(
            ["verbose=true", "ignore=rule1"],
            [{"name": "verbose", "value": "true"}, {"name": "ignore", "value": "rule1"}],
            id="add-multiple-params",
        ),
        pytest.param(
            ["verbose=true", "ignore=rule=1"],
            [{"name": "verbose", "value": "true"}, {"name": "ignore", "value": "rule=1"}],
            id="value-includes-equal-sign",
        ),
        pytest.param(["verbose"], [], id="malformed-param-missing-comma"),
    ],
)
def test_set_params(request, params, expected_params, component_b_repo, capsys, monkeypatch):
    mock_get_digest_for_specific_tag(*TEST_TASK_VALUES)

    cmd = [
        "pmt",
        "add-task",
        BUNDLE_REF,
        str(component_b_repo.tekton_dir),
    ]
    for param in params:
        cmd.append("--param")
        cmd.append(param)

    monkeypatch.setattr("sys.argv", cmd)
    if request.node.callspec.id == "malformed-param-missing-comma":
        with pytest.raises(SystemExit):
            assert entry_point() == 1
            assert "Missing parameter name or value" in capsys.readouterr().err
    else:
        entry_point()

        verifier = VerifyUpdatedPipeline(f"task-{TASK_NAME}", BUNDLE_REF, params=expected_params)
        for yaml_file in component_b_repo.tekton_dir.glob("*.yaml"):
            verifier.check(str(yaml_file))


@responses.activate
@pytest.mark.parametrize("skip_checks", [True, False])
def test_set_skip_checks(skip_checks, component_b_repo, monkeypatch):
    mock_get_digest_for_specific_tag(*TEST_TASK_VALUES)

    cmd = [
        "pmt",
        "add-task",
        BUNDLE_REF,
        str(component_b_repo.tekton_dir),
    ]
    if skip_checks:
        cmd.append("--skip-checks")

    monkeypatch.setattr("sys.argv", cmd)
    entry_point()

    verifier = VerifyUpdatedPipeline(f"task-{TASK_NAME}", BUNDLE_REF, skip_checks=skip_checks)
    for yaml_file in component_b_repo.tekton_dir.glob("*.yaml"):
        verifier.check(str(yaml_file))


@responses.activate
def test_add_task_with_params_and_run_after_clone(component_a_repo, component_b_repo, monkeypatch):
    mock_get_digest_for_specific_tag(*TEST_TASK_VALUES)

    pipeline_files = list(
        itertools.chain(
            component_a_repo.tekton_dir.glob("*.yaml"),
            component_b_repo.tekton_dir.glob("*.yaml"),
        )
    )

    git_index = []
    expected_yaml_files = [filename.name for filename in pipeline_files]

    def _git_add(*args, **kwargs):
        assert str(kwargs.get("cwd")) in [
            str(component_a_repo.tekton_dir),
            str(component_b_repo.tekton_dir),
        ]
        run_cmd = args[-1]
        filename = run_cmd[-1]
        git_index.append(filename)

    monkeypatch.setattr("subprocess.run", _git_add)

    cmd = [
        "pmt",
        "add-task",
        BUNDLE_REF,
        str(component_a_repo.tekton_dir),
        str(component_b_repo.tekton_dir),
        "--run-after",
        "clone",
        "--param",
        "image_url=$(build.results.image_url)",
        "--git-add",
    ]

    monkeypatch.setattr("sys.argv", cmd)
    entry_point()

    expected_params = [{"name": "image_url", "value": "$(build.results.image_url)"}]
    verifier = VerifyUpdatedPipeline(
        f"task-{TASK_NAME}", BUNDLE_REF, params=expected_params, run_after=["clone"]
    )
    for yaml_file in pipeline_files:
        verifier.check(str(yaml_file))

    assert len(git_index) == len(expected_yaml_files)


@responses.activate
@pytest.mark.parametrize(
    "pipeline_task_name,actual_task_name,bundle_digest,expected_log",
    [
        pytest.param(
            "clone",
            "git-clone-oci-ta",
            generate_digest(),
            "Task clone is included in pipeline",
            id="pipeline-task-name-exists",
        ),
        pytest.param(
            "git-clone-oci-ta",
            "git-clone-oci-ta",
            generate_digest(),
            "Task git-clone-oci-ta is being referenced in pipeline ",
            id="actual-task-name-exists",
        ),
        pytest.param(
            "git-clone-oci-ta",
            "clone",
            generate_digest(),
            "The pipeline task name and actual task name seem swapped",
            id="pipeline-and-actual-task-names-are-swapped",
        ),
    ],
)
def test_skip_adding_task_if_exists(
    pipeline_task_name,
    actual_task_name,
    bundle_digest,
    expected_log,
    component_a_repo,
    component_b_repo,
    monkeypatch,
    caplog,
) -> None:
    version: Final = "0.1"
    image_repo: Final = f"{KONFLUX_IMAGE_ORG}/{actual_task_name}"
    bundle_ref: Final = f"quay.io/{image_repo}:{version}@{bundle_digest}"

    mock_get_digest_for_specific_tag(image_repo, version, bundle_digest)

    git_index: list[str] = []

    def _git_add(*args, **kwargs):
        run_cmd = args[-1]
        filename = run_cmd[-1]
        git_index.append(filename)

    monkeypatch.setattr("subprocess.run", _git_add)

    cmd = [
        "pmt",
        "add-task",
        bundle_ref,
        str(component_b_repo.tekton_dir),
        str(component_a_repo.tekton_dir),
        "--git-add",
        "--pipeline-task-name",
        pipeline_task_name,
    ]

    monkeypatch.setattr("sys.argv", cmd)

    with caplog.at_level(logging.DEBUG):
        entry_point()
        assert expected_log in caplog.text

    assert git_index == [], (
        "Neither Pipeline or PipelineRun from component_a and component_b "
        "repositories should be handled."
    )


@responses.activate
@pytest.mark.parametrize(
    "pipeline_task_name,actual_task_name,expected_pipeline_task_name,expected_actual_task_name",
    [
        pytest.param(None, "check", "check", "check", id="use-actual-task-name"),
        pytest.param(None, "check-oci-ta", "check", "check-oci-ta", id="auto-remove-oci-ta-suffix"),
        pytest.param("do-check", "check", "do-check", "check", id="set-individually"),
        pytest.param(
            "do-check",
            "check-oci-ta",
            "do-check",
            "check-oci-ta",
            id="set-individually-with-ta-task",
        ),
    ],
)
def test_pipeline_and_actual_task_name_combinations(
    pipeline_task_name,
    actual_task_name,
    expected_pipeline_task_name,
    expected_actual_task_name,
    component_b_repo,
    monkeypatch,
):
    test_digest = generate_digest()
    image_repo = f"{KONFLUX_IMAGE_ORG}/{actual_task_name}"
    bundle_ref = f"quay.io/{image_repo}:0.1@{test_digest}"

    mock_get_digest_for_specific_tag(image_repo, "0.1", test_digest)

    cmd = [
        "pmt",
        "add-task",
        bundle_ref,
        str(component_b_repo.tekton_dir),
    ]
    if pipeline_task_name:
        cmd.append("--pipeline-task-name")
        cmd.append(pipeline_task_name)

    monkeypatch.setattr("sys.argv", cmd)
    entry_point()

    verifier = VerifyUpdatedPipeline(
        expected_actual_task_name, bundle_ref, pipeline_task_name=expected_pipeline_task_name
    )
    for yaml_file in component_b_repo.tekton_dir.glob("*.yaml"):
        verifier.check(str(yaml_file))


@responses.activate
def test_preserve_yaml_formatting(component_a_repo, component_b_repo, monkeypatch):
    # component a and b repos have Pipeline and PipelineRun definitions individually
    mock_get_digest_for_specific_tag(*TEST_TASK_VALUES)

    cmd = [
        "pmt",
        "add-task",
        BUNDLE_REF,
        str(component_a_repo.tekton_dir),
        str(component_b_repo.tekton_dir),
    ]

    monkeypatch.setattr("sys.argv", cmd)
    entry_point()

    pipeline_files = itertools.chain(
        component_a_repo.tekton_dir.glob("*.yaml"),
        component_b_repo.tekton_dir.glob("*.yaml"),
    )
    for file_path in pipeline_files:
        match = re.search(r'name: revision\n +default: "main"', file_path.read_text())
        assert (
            match
        ), "Expected text should be preserved. YAMLstyle is not set properly to preserve format."


@responses.activate
@pytest.mark.parametrize(
    "cli_flag, check_finally",
    [
        pytest.param([], False, id="default_add_to_tasks"),
        pytest.param(["-f"], True, id="add_to_finally"),
        pytest.param(["--add-to-finally"], True, id="add_to_finally_long_flag"),
    ],
)
def test_add_task_to_finally(
    cli_flag, check_finally, component_b_repo, component_a_repo, monkeypatch
):
    mock_get_digest_for_specific_tag(*TEST_TASK_VALUES)

    cmd = [
        "pmt",
        "add-task",
        BUNDLE_REF,
        str(component_b_repo.tekton_dir),
        str(component_a_repo.tekton_dir),
        *cli_flag,
    ]

    monkeypatch.setattr("sys.argv", cmd)
    entry_point()

    pipeline_files = itertools.chain(
        component_a_repo.tekton_dir.glob("*.yaml"),
        component_b_repo.tekton_dir.glob("*.yaml"),
    )
    verifier = VerifyUpdatedPipeline(f"task-{TASK_NAME}", BUNDLE_REF, check_finally=check_finally)
    for yaml_file in pipeline_files:
        verifier.check(str(yaml_file))
