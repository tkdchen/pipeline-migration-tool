from pathlib import Path
from textwrap import dedent

import pytest

from pipeline_migration.cli import entry_point
from pipeline_migration.utils import load_yaml


class ComponentRepo:
    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.tekton_dir = base_path / ".tekton"


@pytest.fixture
def component_pipeline_dir(tmp_path):
    """Create a temporary directory with pipeline files."""
    component_dir = tmp_path / "component_pipeline"
    tekton_dir = component_dir / ".tekton"
    tekton_dir.mkdir(parents=True)

    # Create pipeline file
    pipeline_content = dedent(
        """\
        apiVersion: tekton.dev/v1
        kind: Pipeline
        metadata:
          name: test-pipeline
        spec:
          tasks:
            - name: clone
              taskRef:
                name: git-clone
              params:
                - name: url
                  value: "https://github.com/example/repo"
                - name: revision
                  value: "main"
            - name: build
              taskRef:
                name: buildah
              params:
                - name: IMAGE
                  value: "registry.io/app:latest"
            - name: test-task
              taskRef:
                name: test-runner
        """
    )

    pipeline_file = tekton_dir / "pipeline.yaml"
    pipeline_file.write_text(pipeline_content)

    # Create pipeline run file
    pipeline_run_content = dedent(
        """\
        apiVersion: tekton.dev/v1
        kind: PipelineRun
        metadata:
          name: test-pipeline-run
        spec:
          pipelineSpec:
            tasks:
              - name: clone
                taskRef:
                  name: git-clone
                params:
                  - name: url
                    value: "https://github.com/example/repo"
              - name: build
                taskRef:
                  name: buildah
              - name: deploy
                taskRef:
                  name: kubectl-deploy
                params:
                  - name: image
                    value: "registry.io/app:latest"
                  - name: namespace
                    value: "production"
        """
    )

    pipeline_run_file = tekton_dir / "pipeline-run.yaml"
    pipeline_run_file.write_text(pipeline_run_content)

    return ComponentRepo(component_dir)


@pytest.fixture
def component_matrix_pipeline_dir(tmp_path):
    """Create a temporary directory with pipeline files."""
    component_dir = tmp_path / "component_pipeline"
    tekton_dir = component_dir / ".tekton"
    tekton_dir.mkdir(parents=True)

    # Create pipeline file
    pipeline_content = dedent(
        """\
        apiVersion: tekton.dev/v1
        kind: Pipeline
        metadata:
          name: test-pipeline
        spec:
          tasks:
            - name: clone
              taskRef:
                name: git-clone
              matrix:
                params:
                  - name: url
                    value: "https://github.com/example/repo"
                  - name: revision
                    value: "main"
            - name: build
              taskRef:
                name: buildah
              matrix:
                params:
                  - name: IMAGE
                    value: "registry.io/app:latest"
            - name: test-task
              taskRef:
                name: test-runner
        """
    )

    pipeline_file = tekton_dir / "pipeline.yaml"
    pipeline_file.write_text(pipeline_content)

    # Create pipeline run file
    pipeline_run_content = dedent(
        """\
        apiVersion: tekton.dev/v1
        kind: PipelineRun
        metadata:
          name: test-pipeline-run
        spec:
          pipelineSpec:
            tasks:
              - name: clone
                taskRef:
                  name: git-clone
                matrix:
                  params:
                  - name: url
                    value: "https://github.com/example/repo"
              - name: build
                taskRef:
                  name: buildah
              - name: deploy
                taskRef:
                  name: kubectl-deploy
                matrix:
                  params:
                  - name: image
                    value: "registry.io/app:latest"
                  - name: namespace
                    value: "production"
        """
    )

    pipeline_run_file = tekton_dir / "pipeline-run.yaml"
    pipeline_run_file.write_text(pipeline_run_content)

    return ComponentRepo(component_dir)


@pytest.fixture
def second_component_dir(tmp_path):
    """Create a second temporary directory with different pipeline files."""
    component_dir = tmp_path / "second_component"
    tekton_dir = component_dir / ".tekton"
    tekton_dir.mkdir(parents=True)

    # Create a different pipeline file
    pipeline_content = dedent(
        """\
        apiVersion: tekton.dev/v1
        kind: Pipeline
        metadata:
          name: another-pipeline
        spec:
          tasks:
            - name: fetch-source
              taskRef:
                name: git-clone
              params:
                - name: url
                  value: "https://github.com/another/repo"
            - name: compile
              taskRef:
                name: maven
              params:
                - name: GOALS
                  value: "clean compile"
                - name: MAVEN_ARGS
                  value: "-DskipTests=true"
        """
    )

    pipeline_file = tekton_dir / "build.yaml"
    pipeline_file.write_text(pipeline_content)

    return ComponentRepo(component_dir)


@pytest.fixture
def second_component_matrix_dir(tmp_path):
    """Create a second temporary directory with different pipeline files."""
    component_dir = tmp_path / "second_component"
    tekton_dir = component_dir / ".tekton"
    tekton_dir.mkdir(parents=True)

    # Create a different pipeline file
    pipeline_content = dedent(
        """\
        apiVersion: tekton.dev/v1
        kind: Pipeline
        metadata:
          name: another-pipeline
        spec:
          tasks:
            - name: fetch-source
              taskRef:
                name: git-clone
              matrix:
                params:
                  - name: url
                    value: "https://github.com/another/repo"
            - name: compile
              taskRef:
                name: maven
              matrix:
                params:
                  - name: GOALS
                    value: "clean compile"
                  - name: MAVEN_ARGS
                    value: "-DskipTests=true"
        """
    )

    pipeline_file = tekton_dir / "build.yaml"
    pipeline_file.write_text(pipeline_content)

    return ComponentRepo(component_dir)


def verify_param_added(file_path: Path, task_name: str, param_name: str, param_value: str):
    """Helper function to verify a parameter was added to a task."""
    doc = load_yaml(file_path)

    # Check both Pipeline and PipelineRun structures
    tasks = None
    if "spec" in doc and "tasks" in doc["spec"]:
        tasks = doc["spec"]["tasks"]
    elif "spec" in doc and "pipelineSpec" in doc["spec"] and "tasks" in doc["spec"]["pipelineSpec"]:
        tasks = doc["spec"]["pipelineSpec"]["tasks"]

    assert tasks is not None, f"No tasks found in {file_path}"

    task = next((t for t in tasks if t["name"] == task_name), None)
    assert task is not None, f"Task {task_name} not found in {file_path}"

    if "params" not in task:
        assert False, f"No params found in task {task_name} in {file_path}"

    param = next((p for p in task["params"] if p["name"] == param_name), None)
    assert param is not None, f"Parameter {param_name} not found in task {task_name} in {file_path}"
    assert param["value"] == param_value, f"Parameter {param_name} has wrong value in {file_path}"


def verify_param_removed(file_path: Path, task_name: str, param_name: str):
    """Helper function to verify a parameter was removed from a task."""
    doc = load_yaml(file_path)

    # Check both Pipeline and PipelineRun structures
    tasks = None
    if "spec" in doc and "tasks" in doc["spec"]:
        tasks = doc["spec"]["tasks"]
    elif "spec" in doc and "pipelineSpec" in doc["spec"] and "tasks" in doc["spec"]["pipelineSpec"]:
        tasks = doc["spec"]["pipelineSpec"]["tasks"]

    assert tasks is not None, f"No tasks found in {file_path}"

    task = next((t for t in tasks if t["name"] == task_name), None)
    assert task is not None, f"Task {task_name} not found in {file_path}"

    if task.get("params") is None:
        return  # No params section means param was definitely removed or is null

    param = next((p for p in task["params"] if p["name"] == param_name), None)
    assert param is None, f"Parameter {param_name} still exists in task {task_name} in {file_path}"


def verify_matrix_param_added(file_path: Path, task_name: str, param_name: str, param_value: str):
    """Helper function to verify a parameter was added to a task."""
    doc = load_yaml(file_path)

    # Check both Pipeline and PipelineRun structures
    tasks = None
    if "spec" in doc and "tasks" in doc["spec"]:
        tasks = doc["spec"]["tasks"]
    elif "spec" in doc and "pipelineSpec" in doc["spec"] and "tasks" in doc["spec"]["pipelineSpec"]:
        tasks = doc["spec"]["pipelineSpec"]["tasks"]

    assert tasks is not None, f"No tasks found in {file_path}"

    task = next((t for t in tasks if t["name"] == task_name), None)
    assert task is not None, f"Task {task_name} not found in {file_path}"

    if "matrix" not in task:
        assert False, f"No matrix found in task {task_name} in {file_path}"

    matrix = task["matrix"]

    if "params" not in matrix:
        assert False, f"No params found in task {task_name} matrix in {file_path}"

    param = next((p for p in matrix["params"] if p["name"] == param_name), None)
    assert param is not None, f"Parameter {param_name} not found in task {task_name} in {file_path}"
    assert param["value"] == param_value, f"Parameter {param_name} has wrong value in {file_path}"


def verify_matrix_param_removed(file_path: Path, task_name: str, param_name: str):
    """Helper function to verify a parameter was removed from a task."""
    doc = load_yaml(file_path)

    # Check both Pipeline and PipelineRun structures
    tasks = None
    if "spec" in doc and "tasks" in doc["spec"]:
        tasks = doc["spec"]["tasks"]
    elif "spec" in doc and "pipelineSpec" in doc["spec"] and "tasks" in doc["spec"]["pipelineSpec"]:
        tasks = doc["spec"]["pipelineSpec"]["tasks"]

    assert tasks is not None, f"No tasks found in {file_path}"

    task = next((t for t in tasks if t["name"] == task_name), None)
    assert task is not None, f"Task {task_name} not found in {file_path}"

    if "matrix" not in task:
        return  # No matrix section means that param was removed

    matrix = task["matrix"]
    if "params" not in matrix:
        return  # No params section means param was definitely removed or is null

    param = next((p for p in matrix["params"] if p["name"] == param_name), None)
    assert param is None, f"Parameter {param_name} still exists in task {task_name} in {file_path}"


class TestModifyTaskAddParam:
    """Test cases for the modify task add-param CLI command."""

    def test_add_param_to_existing_task(self, component_pipeline_dir, monkeypatch):
        """Test adding a parameter to an existing task."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "add-param",
            "timeout",
            "30m",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added to all pipeline files
        for yaml_file in component_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_param_added(yaml_file, "clone", "timeout", "30m")

    def test_add_param_to_existing_task_array(self, component_pipeline_dir, monkeypatch):
        """Test adding a parameter to an existing task as array."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "add-param",
            "-t",
            "array",
            "timeout",
            "30m",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added to all pipeline files
        for yaml_file in component_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_param_added(yaml_file, "clone", "timeout", ["30m"])

    def test_add_param_to_existing_task_array_multiple(self, component_pipeline_dir, monkeypatch):
        """Test adding a parameter to an existing task as array (multiple values)."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "add-param",
            "-t",
            "array",
            "timeout",
            "30m",
            "60m",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added to all pipeline files
        for yaml_file in component_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_param_added(yaml_file, "clone", "timeout", ["30m", "60m"])

    def test_add_param_to_task_without_params(self, component_pipeline_dir, monkeypatch):
        """Test adding a parameter to a task that has no existing parameters."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "task",
            "test-task",
            "add-param",
            "verbose",
            "true",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added
        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        verify_param_added(pipeline_file, "test-task", "verbose", "true")

    def test_update_existing_param_value(self, component_pipeline_dir, monkeypatch):
        """Test updating an existing parameter value."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "add-param",
            "url",
            "https://github.com/new/repo",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was updated
        for yaml_file in component_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_param_added(yaml_file, "clone", "url", "https://github.com/new/repo")

    def test_work_with_multiple_directories(
        self, component_pipeline_dir, second_component_dir, monkeypatch
    ):
        """Test adding parameters across multiple directories."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "--file-or-dir",
            str(second_component_dir.tekton_dir),
            "task",
            "fetch-source",
            "add-param",
            "depth",
            "1",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added where the task exists
        build_file = second_component_dir.tekton_dir / "build.yaml"
        verify_param_added(build_file, "fetch-source", "depth", "1")

    def test_work_with_specific_files(self, component_pipeline_dir, monkeypatch):
        """Test adding parameters to specific files."""
        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(pipeline_file),
            "task",
            "clone",
            "add-param",
            "sslVerify",
            "false",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added to the specific file
        verify_param_added(pipeline_file, "clone", "sslVerify", "false")

    def test_use_relative_tekton_dir(self, component_pipeline_dir, monkeypatch):
        """Test using the default .tekton directory."""
        # Change to the component directory
        monkeypatch.chdir(str(component_pipeline_dir.base_path))

        cmd = ["pmt", "modify", "task", "clone", "add-param", "timeout", "45m"]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added
        for yaml_file in component_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_param_added(yaml_file, "clone", "timeout", "45m")

    def test_parameter_with_special_characters(self, component_pipeline_dir, monkeypatch):
        """Test adding a parameter with special characters in the value."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "add-param",
            "userHome",
            "/home/user",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added
        for yaml_file in component_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_param_added(yaml_file, "clone", "userHome", "/home/user")

    def test_parameter_with_spaces_in_value(self, component_pipeline_dir, monkeypatch):
        """Test adding a parameter with spaces in the value."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "task",
            "build",
            "add-param",
            "BUILD_ARGS",
            "--build-arg VERSION=1.0",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added
        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        verify_param_added(pipeline_file, "build", "BUILD_ARGS", "--build-arg VERSION=1.0")


class TestModifyTaskRemoveParam:
    """Test cases for the modify task remove-param CLI command."""

    def test_remove_existing_param(self, component_pipeline_dir, monkeypatch):
        """Test removing an existing parameter."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "remove-param",
            "url",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was removed
        for yaml_file in component_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_param_removed(yaml_file, "clone", "url")

    def test_remove_param_from_pipeline_run(self, component_pipeline_dir, monkeypatch):
        """Test removing a parameter from a PipelineRun."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "task",
            "deploy",
            "remove-param",
            "namespace",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was removed
        pipeline_run_file = component_pipeline_dir.tekton_dir / "pipeline-run.yaml"
        verify_param_removed(pipeline_run_file, "deploy", "namespace")

    def test_use_relative_tekton_dir(self, component_pipeline_dir, monkeypatch):
        """Test using the default .tekton directory."""
        # Change to the component directory
        monkeypatch.chdir(str(component_pipeline_dir.base_path))

        cmd = ["pmt", "modify", "task", "clone", "remove-param", "url"]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        for yaml_file in component_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_param_removed(yaml_file, "clone", "url")

    def test_remove_nonexistent_param(self, component_pipeline_dir, monkeypatch):
        """Test removing a parameter that doesn't exist (should do nothing)."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "remove-param",
            "nonexistent",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Should complete without error, original params should remain
        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        verify_param_added(pipeline_file, "clone", "url", "https://github.com/example/repo")
        verify_param_added(pipeline_file, "clone", "revision", "main")

    def test_remove_param_from_task_without_params(self, component_pipeline_dir, monkeypatch):
        """Test removing a parameter from a task that has no parameters."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "task",
            "test-task",
            "remove-param",
            "nonexistent",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Should complete without error
        # No verification needed as task has no params

    def test_remove_param_from_nonexistent_task(self, component_pipeline_dir, monkeypatch):
        """Test removing a parameter from a task that doesn't exist."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "task",
            "nonexistent-task",
            "remove-param",
            "param",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Should complete without error
        # Original tasks should remain unchanged

    def test_remove_all_params_from_task(self, component_pipeline_dir, monkeypatch):
        """Test removing all parameters from a task."""
        # Remove first parameter
        cmd1 = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "remove-param",
            "url",
        ]

        monkeypatch.setattr("sys.argv", cmd1)
        entry_point()

        # Remove second parameter
        cmd2 = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "remove-param",
            "revision",
        ]

        monkeypatch.setattr("sys.argv", cmd2)
        entry_point()

        # Verify both parameters were removed
        for yaml_file in component_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_param_removed(yaml_file, "clone", "url")
            verify_param_removed(yaml_file, "clone", "revision")


class TestModifyTaskMatrixAddParam:
    """Test cases for the modify task matrix-add-param CLI command."""

    def test_add_param_to_existing_task(self, component_matrix_pipeline_dir, monkeypatch):
        """Test adding a parameter to an existing task."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_matrix_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "matrix-add-param",
            "timeout",
            "30m",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added to all pipeline files
        for yaml_file in component_matrix_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_matrix_param_added(yaml_file, "clone", "timeout", "30m")

    def test_add_param_to_existing_task_array(self, component_matrix_pipeline_dir, monkeypatch):
        """Test adding a parameter to an existing task as array."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_matrix_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "matrix-add-param",
            "-t",
            "array",
            "timeout",
            "30m",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added to all pipeline files
        for yaml_file in component_matrix_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_matrix_param_added(yaml_file, "clone", "timeout", ["30m"])

    def test_add_param_to_existing_task_array_multiple(
        self, component_matrix_pipeline_dir, monkeypatch
    ):
        """Test adding a parameter to an existing task as array (multiple values)."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_matrix_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "matrix-add-param",
            "-t",
            "array",
            "timeout",
            "30m",
            "60m",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added to all pipeline files
        for yaml_file in component_matrix_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_matrix_param_added(yaml_file, "clone", "timeout", ["30m", "60m"])

    def test_add_param_to_task_without_params(self, component_matrix_pipeline_dir, monkeypatch):
        """Test adding a parameter to a task that has no existing parameters."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_matrix_pipeline_dir.tekton_dir),
            "task",
            "test-task",
            "matrix-add-param",
            "verbose",
            "true",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added
        pipeline_file = component_matrix_pipeline_dir.tekton_dir / "pipeline.yaml"
        verify_matrix_param_added(pipeline_file, "test-task", "verbose", "true")

    def test_update_existing_param_value(self, component_matrix_pipeline_dir, monkeypatch):
        """Test updating an existing parameter value."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_matrix_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "matrix-add-param",
            "url",
            "https://github.com/new/repo",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was updated
        for yaml_file in component_matrix_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_matrix_param_added(yaml_file, "clone", "url", "https://github.com/new/repo")

    def test_work_with_multiple_directories(
        self, component_matrix_pipeline_dir, second_component_matrix_dir, monkeypatch
    ):
        """Test adding parameters across multiple directories."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_matrix_pipeline_dir.tekton_dir),
            "--file-or-dir",
            str(second_component_matrix_dir.tekton_dir),
            "task",
            "fetch-source",
            "matrix-add-param",
            "depth",
            "1",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added where the task exists
        build_file = second_component_matrix_dir.tekton_dir / "build.yaml"
        verify_matrix_param_added(build_file, "fetch-source", "depth", "1")

    def test_work_with_specific_files(self, component_matrix_pipeline_dir, monkeypatch):
        """Test adding parameters to specific files."""
        pipeline_file = component_matrix_pipeline_dir.tekton_dir / "pipeline.yaml"
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(pipeline_file),
            "task",
            "clone",
            "matrix-add-param",
            "sslVerify",
            "false",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added to the specific file
        verify_matrix_param_added(pipeline_file, "clone", "sslVerify", "false")

    def test_use_relative_tekton_dir(self, component_matrix_pipeline_dir, monkeypatch):
        """Test using the default .tekton directory."""
        # Change to the component directory
        monkeypatch.chdir(str(component_matrix_pipeline_dir.base_path))

        cmd = ["pmt", "modify", "task", "clone", "matrix-add-param", "timeout", "45m"]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added
        for yaml_file in component_matrix_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_matrix_param_added(yaml_file, "clone", "timeout", "45m")

    def test_parameter_with_special_characters(self, component_matrix_pipeline_dir, monkeypatch):
        """Test adding a parameter with special characters in the value."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_matrix_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "matrix-add-param",
            "userHome",
            "/home/user",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added
        for yaml_file in component_matrix_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_matrix_param_added(yaml_file, "clone", "userHome", "/home/user")

    def test_parameter_with_spaces_in_value(self, component_matrix_pipeline_dir, monkeypatch):
        """Test adding a parameter with spaces in the value."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_matrix_pipeline_dir.tekton_dir),
            "task",
            "build",
            "matrix-add-param",
            "BUILD_ARGS",
            "--build-arg VERSION=1.0",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added
        pipeline_file = component_matrix_pipeline_dir.tekton_dir / "pipeline.yaml"
        verify_matrix_param_added(pipeline_file, "build", "BUILD_ARGS", "--build-arg VERSION=1.0")


class TestModifyTaskMatrixRemoveParam:
    """Test cases for the modify task remove-param CLI command."""

    def test_remove_existing_param(self, component_matrix_pipeline_dir, monkeypatch):
        """Test removing an existing parameter."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_matrix_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "matrix-remove-param",
            "url",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was removed
        for yaml_file in component_matrix_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_matrix_param_removed(yaml_file, "clone", "url")

    def test_remove_param_from_pipeline_run(self, component_matrix_pipeline_dir, monkeypatch):
        """Test removing a parameter from a PipelineRun."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_matrix_pipeline_dir.tekton_dir),
            "task",
            "deploy",
            "matrix-remove-param",
            "namespace",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was removed
        pipeline_run_file = component_matrix_pipeline_dir.tekton_dir / "pipeline-run.yaml"
        verify_matrix_param_removed(pipeline_run_file, "deploy", "namespace")

    def test_remove_nonexistent_param(self, component_matrix_pipeline_dir, monkeypatch):
        """Test removing a parameter that doesn't exist (should do nothing)."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_matrix_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "matrix-remove-param",
            "nonexistent",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Should complete without error, original params should remain
        pipeline_file = component_matrix_pipeline_dir.tekton_dir / "pipeline.yaml"
        verify_matrix_param_added(pipeline_file, "clone", "url", "https://github.com/example/repo")
        verify_matrix_param_added(pipeline_file, "clone", "revision", "main")

    def test_remove_param_from_task_without_params(
        self, component_matrix_pipeline_dir, monkeypatch
    ):
        """Test removing a parameter from a task that has no parameters."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_matrix_pipeline_dir.tekton_dir),
            "task",
            "test-task",
            "matrix-remove-param",
            "nonexistent",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Should complete without error
        # No verification needed as task has no params

    def test_remove_param_from_nonexistent_task(self, component_matrix_pipeline_dir, monkeypatch):
        """Test removing a parameter from a task that doesn't exist."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_matrix_pipeline_dir.tekton_dir),
            "task",
            "nonexistent-task",
            "matrix-remove-param",
            "param",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Should complete without error
        # Original tasks should remain unchanged

    def test_remove_all_params_from_task(self, component_matrix_pipeline_dir, monkeypatch):
        """Test removing all parameters from a task."""
        # Remove first parameter
        cmd1 = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_matrix_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "matrix-remove-param",
            "url",
        ]

        monkeypatch.setattr("sys.argv", cmd1)
        entry_point()

        # Remove second parameter
        cmd2 = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_matrix_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "matrix-remove-param",
            "revision",
        ]

        monkeypatch.setattr("sys.argv", cmd2)
        entry_point()

        # Verify both parameters were removed
        for yaml_file in component_matrix_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_matrix_param_removed(yaml_file, "clone", "url")
            verify_matrix_param_removed(yaml_file, "clone", "revision")

    def test_use_relative_tekton_dir(self, component_matrix_pipeline_dir, monkeypatch):
        """Test using the default .tekton directory."""
        # Change to the component directory
        monkeypatch.chdir(str(component_matrix_pipeline_dir.base_path))

        cmd = ["pmt", "modify", "task", "clone", "matrix-remove-param", "url"]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added
        for yaml_file in component_matrix_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_matrix_param_removed(yaml_file, "clone", "url")


class TestModifyTaskErrorHandling:
    """Test error handling and edge cases."""

    def test_missing_task_name_argument(self, monkeypatch):
        """Test that missing task name argument is handled."""
        cmd = ["pmt", "modify", "task"]

        monkeypatch.setattr("sys.argv", cmd)

        # Should exit with error
        with pytest.raises(SystemExit):
            entry_point()

    def test_missing_param_arguments(self, monkeypatch):
        """Test that missing parameter arguments are handled."""
        cmd = ["pmt", "modify", "task", "clone", "add-param"]

        monkeypatch.setattr("sys.argv", cmd)

        # Should exit with error
        with pytest.raises(SystemExit):
            entry_point()

    def test_array_param_with_string_type(self, monkeypatch):
        """Array of param values passed when type is string."""
        cmd = [
            "pmt",
            "modify",
            "task",
            "clone",
            "add-param",
            "-t",
            "string",
            "param",
            "value1",
            "value2",
        ]

        monkeypatch.setattr("sys.argv", cmd)

        assert entry_point() != 0

    def test_invalid_subcommand(self, monkeypatch):
        """Test that invalid subcommands are handled."""
        cmd = ["pmt", "modify", "task", "clone", "invalid-command"]

        monkeypatch.setattr("sys.argv", cmd)

        # Should exit with error
        with pytest.raises(SystemExit):
            entry_point()

    def test_nonexistent_file_path(self, monkeypatch):
        """Test handling of nonexistent file paths."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            "/nonexistent/path",
            "task",
            "clone",
            "add-param",
            "timeout",
            "30m",
        ]

        monkeypatch.setattr("sys.argv", cmd)

        # Should handle gracefully (may not find any files to process)
        entry_point()  # Should not crash

    def test_empty_parameter_value(self, component_pipeline_dir, monkeypatch):
        """Test adding a parameter with an empty value."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "task",
            "clone",
            "add-param",
            "empty",
            "",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify parameter was added with empty value
        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        verify_param_added(pipeline_file, "clone", "empty", "")
