import pytest
from textwrap import dedent

from pipeline_migration.actions.modify.task import (
    ModTaskAddParamOperation,
    ModTaskRemoveParamOperation,
    TaskNotFoundError,
)
from pipeline_migration.utils import load_yaml, YAMLStyle


def read_file_content(file_path: str) -> str:
    """Helper function to read file content."""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def pipeline_yaml_file(create_yaml_file):
    """Create a temporary YAML file with a pipeline structure."""
    content = dedent(
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
    return create_yaml_file(content)


@pytest.fixture
def pipeline_finally_yaml_file(create_yaml_file):
    """Create a temporary YAML file with a pipeline structure."""
    content = dedent(
        """\
        apiVersion: tekton.dev/v1
        kind: Pipeline
        metadata:
          name: test-pipeline
        spec:
          finally:
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
    return create_yaml_file(content)


@pytest.fixture
def pipeline_run_yaml_file(create_yaml_file):
    """Create a temporary YAML file with a PipelineRun structure."""
    content = dedent(
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
    return create_yaml_file(content)


@pytest.fixture
def pipeline_run_finally_yaml_file(create_yaml_file):
    """Create a temporary YAML file with a PipelineRun structure."""
    content = dedent(
        """\
        apiVersion: tekton.dev/v1
        kind: PipelineRun
        metadata:
          name: test-pipeline-run
        spec:
          pipelineSpec:
            finally:
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
    return create_yaml_file(content)


class TestModTaskAddParamOperation:
    """Test cases for ModTaskAddParamOperation class."""

    def test_initialization(self):
        """Test operation initialization."""
        op = ModTaskAddParamOperation("clone", "timeout", "30m")
        assert op.task_name == "clone"
        assert op.param_name == "timeout"
        assert op.param_value == "30m"

    def test_add_param_to_existing_params_list(self, pipeline_yaml_file):
        """Test adding a parameter to a task that already has parameters."""
        op = ModTaskAddParamOperation("clone", "timeout", "30m")

        # Load initial data
        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)
        tasks = loaded_doc["spec"]["tasks"]

        # Execute operation
        result = op._add_param(tasks, ["spec", "tasks"], pipeline_yaml_file, style)
        assert result is True

        expected = dedent(
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
                    - name: timeout
                      value: 30m
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

        assert read_file_content(pipeline_yaml_file) == expected

    def test_add_param_to_task_without_params(self, pipeline_yaml_file):
        """Test adding a parameter to a task that has no existing parameters."""
        op = ModTaskAddParamOperation("test-task", "verbose", "true")

        # Load initial data
        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)
        tasks = loaded_doc["spec"]["tasks"]

        # Execute operation
        result = op._add_param(tasks, ["spec", "tasks"], pipeline_yaml_file, style)
        assert result is True

        expected = dedent(
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
                  params:
                    - name: verbose
                      value: 'true'
            """
        )

        assert read_file_content(pipeline_yaml_file) == expected

    def test_update_existing_param_value(self, pipeline_yaml_file):
        """Test updating an existing parameter value."""
        op = ModTaskAddParamOperation("clone", "url", "https://github.com/new/repo")

        # Load initial data
        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)
        tasks = loaded_doc["spec"]["tasks"]

        # Execute operation
        result = op._add_param(tasks, ["spec", "tasks"], pipeline_yaml_file, style)
        assert result is True

        expected = dedent(
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
                      value: https://github.com/new/repo
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

        assert read_file_content(pipeline_yaml_file) == expected

    def test_update_existing_param_value_with_array(self, pipeline_yaml_file):
        """Test updating an existing parameter value."""
        op = ModTaskAddParamOperation(
            "clone", "url", ["https://github.com/new/repo", "another_url"]
        )

        # Load initial data
        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)
        tasks = loaded_doc["spec"]["tasks"]

        # Execute operation
        result = op._add_param(tasks, ["spec", "tasks"], pipeline_yaml_file, style)
        assert result is True

        expected = dedent(
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
                      value:
                        - https://github.com/new/repo
                        - another_url
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

        assert read_file_content(pipeline_yaml_file) == expected

    def test_no_change_when_param_value_same(self, pipeline_yaml_file):
        """Test that no change is made when parameter value is already the same."""
        op = ModTaskAddParamOperation("clone", "url", "https://github.com/example/repo")

        # Load initial data
        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)
        tasks = loaded_doc["spec"]["tasks"]

        # Execute operation
        result = op._add_param(tasks, ["spec", "tasks"], pipeline_yaml_file, style)
        assert result is False  # No change needed

        expected = dedent(
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

        assert read_file_content(pipeline_yaml_file) == expected

    def test_task_not_found(self, pipeline_yaml_file):
        """Test behavior when specified task doesn't exist."""
        op = ModTaskAddParamOperation("nonexistent-task", "param", "value")

        # Load initial data
        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)
        tasks = loaded_doc["spec"]["tasks"]

        # Execute operation
        with pytest.raises(TaskNotFoundError):
            op._add_param(tasks, ["spec", "tasks"], pipeline_yaml_file, style)

        expected = dedent(
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

        assert read_file_content(pipeline_yaml_file) == expected

    def test_handle_pipeline_file(self, pipeline_yaml_file):
        """Test handle_pipeline_file method."""
        op = ModTaskAddParamOperation("clone", "timeout", "30m")

        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)

        # This should not raise an exception
        op.handle_pipeline_file(pipeline_yaml_file, loaded_doc, style)

        expected = dedent(
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
                    - name: timeout
                      value: 30m
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

        assert read_file_content(pipeline_yaml_file) == expected

    def test_handle_pipeline_run_file(self, pipeline_run_yaml_file):
        """Test handle_pipeline_run_file method."""
        op = ModTaskAddParamOperation("clone", "timeout", "30m")

        loaded_doc = load_yaml(pipeline_run_yaml_file)
        style = YAMLStyle.detect(pipeline_run_yaml_file)

        # This should not raise an exception
        op.handle_pipeline_run_file(pipeline_run_yaml_file, loaded_doc, style)
        expected = dedent(
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
                      - name: timeout
                        value: 30m
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

        assert read_file_content(pipeline_run_yaml_file) == expected

    def test_handle_pipeline_file_finally(self, pipeline_finally_yaml_file):
        """Test handle_pipeline_file method (with tasks in finally section)."""
        op = ModTaskAddParamOperation("clone", "timeout", "30m")

        loaded_doc = load_yaml(pipeline_finally_yaml_file)
        style = YAMLStyle.detect(pipeline_finally_yaml_file)

        # This should not raise an exception
        op.handle_pipeline_file(pipeline_finally_yaml_file, loaded_doc, style)

        expected = dedent(
            """\
            apiVersion: tekton.dev/v1
            kind: Pipeline
            metadata:
              name: test-pipeline
            spec:
              finally:
                - name: clone
                  taskRef:
                    name: git-clone
                  params:
                    - name: url
                      value: "https://github.com/example/repo"
                    - name: revision
                      value: "main"
                    - name: timeout
                      value: 30m
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

        assert read_file_content(pipeline_finally_yaml_file) == expected

    def test_handle_pipeline_run_file_finally(self, pipeline_run_finally_yaml_file):
        """Test handle_pipeline_run_file method (with tasks in finally section)."""
        op = ModTaskAddParamOperation("clone", "timeout", "30m")

        loaded_doc = load_yaml(pipeline_run_finally_yaml_file)
        style = YAMLStyle.detect(pipeline_run_finally_yaml_file)

        # This should not raise an exception
        op.handle_pipeline_run_file(pipeline_run_finally_yaml_file, loaded_doc, style)

        expected = dedent(
            """\
            apiVersion: tekton.dev/v1
            kind: PipelineRun
            metadata:
              name: test-pipeline-run
            spec:
              pipelineSpec:
                finally:
                  - name: clone
                    taskRef:
                      name: git-clone
                    params:
                      - name: url
                        value: "https://github.com/example/repo"
                      - name: timeout
                        value: 30m
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

        assert read_file_content(pipeline_run_finally_yaml_file) == expected


class TestModTaskRemoveParamOperation:
    """Test cases for ModTaskRemoveParamOperation class."""

    def test_initialization(self):
        """Test operation initialization."""
        op = ModTaskRemoveParamOperation("clone", "timeout")
        assert op.task_name == "clone"
        assert op.param_name == "timeout"

    def test_remove_existing_param(self, pipeline_yaml_file):
        """Test removing an existing parameter."""
        op = ModTaskRemoveParamOperation("clone", "url")

        # Load initial data
        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)
        tasks = loaded_doc["spec"]["tasks"]

        # Execute operation
        result = op._remove_param(tasks, ["spec", "tasks"], pipeline_yaml_file, style)
        assert result is True

        expected = dedent(
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

        assert read_file_content(pipeline_yaml_file) == expected

    def test_remove_param_from_task_without_params(self, pipeline_yaml_file):
        """Test removing a parameter from a task that has no parameters."""
        op = ModTaskRemoveParamOperation("test-task", "nonexistent")

        # Load initial data
        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)
        tasks = loaded_doc["spec"]["tasks"]

        # Execute operation
        result = op._remove_param(tasks, ["spec", "tasks"], pipeline_yaml_file, style)
        assert result is False

        expected = dedent(
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

        assert read_file_content(pipeline_yaml_file) == expected

    def test_remove_nonexistent_param(self, pipeline_yaml_file):
        """Test removing a parameter that doesn't exist."""
        op = ModTaskRemoveParamOperation("clone", "nonexistent-param")

        # Load initial data
        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)
        tasks = loaded_doc["spec"]["tasks"]

        # Execute operation
        result = op._remove_param(tasks, ["spec", "tasks"], pipeline_yaml_file, style)
        assert result is False

        expected = dedent(
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

        assert read_file_content(pipeline_yaml_file) == expected

    def test_task_not_found(self, pipeline_yaml_file):
        """Test behavior when specified task doesn't exist."""
        op = ModTaskRemoveParamOperation("nonexistent-task", "param")

        # Load initial data
        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)
        tasks = loaded_doc["spec"]["tasks"]

        # Execute operation
        with pytest.raises(TaskNotFoundError):
            op._remove_param(tasks, ["spec", "tasks"], pipeline_yaml_file, style)

        expected = dedent(
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

        assert read_file_content(pipeline_yaml_file) == expected

    def test_handle_pipeline_file(self, pipeline_yaml_file):
        """Test handle_pipeline_file method."""
        op = ModTaskRemoveParamOperation("clone", "url")

        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)

        # This should not raise an exception
        op.handle_pipeline_file(pipeline_yaml_file, loaded_doc, style)

        expected = dedent(
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

        assert read_file_content(pipeline_yaml_file) == expected

    def test_handle_pipeline_run_file(self, pipeline_run_yaml_file):
        """Test handle_pipeline_run_file method."""
        op = ModTaskRemoveParamOperation("deploy", "namespace")

        loaded_doc = load_yaml(pipeline_run_yaml_file)
        style = YAMLStyle.detect(pipeline_run_yaml_file)

        # This should not raise an exception
        op.handle_pipeline_run_file(pipeline_run_yaml_file, loaded_doc, style)

        expected = dedent(
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
            """
        )

        assert read_file_content(pipeline_run_yaml_file) == expected

    def test_handle_pipeline_file_finally(self, pipeline_finally_yaml_file):
        """Test handle_pipeline_file method (with tasks in finally section).."""
        op = ModTaskRemoveParamOperation("clone", "url")

        loaded_doc = load_yaml(pipeline_finally_yaml_file)
        style = YAMLStyle.detect(pipeline_finally_yaml_file)

        # This should not raise an exception
        op.handle_pipeline_file(pipeline_finally_yaml_file, loaded_doc, style)

        expected = dedent(
            """\
            apiVersion: tekton.dev/v1
            kind: Pipeline
            metadata:
              name: test-pipeline
            spec:
              finally:
                - name: clone
                  taskRef:
                    name: git-clone
                  params:
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

        assert read_file_content(pipeline_finally_yaml_file) == expected

    def test_handle_pipeline_run_file_finally(self, pipeline_run_finally_yaml_file):
        """Test handle_pipeline_run_file method (with tasks in finally section).."""
        op = ModTaskRemoveParamOperation("deploy", "namespace")

        loaded_doc = load_yaml(pipeline_run_finally_yaml_file)
        style = YAMLStyle.detect(pipeline_run_finally_yaml_file)

        # This should not raise an exception
        op.handle_pipeline_run_file(pipeline_run_finally_yaml_file, loaded_doc, style)

        expected = dedent(
            """\
            apiVersion: tekton.dev/v1
            kind: PipelineRun
            metadata:
              name: test-pipeline-run
            spec:
              pipelineSpec:
                finally:
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
            """
        )

        assert read_file_content(pipeline_run_finally_yaml_file) == expected


class TestComplexScenarios:
    """Test complex scenarios involving multiple operations."""

    def test_multiple_add_operations(self, pipeline_yaml_file):
        """Test performing multiple add operations on the same file."""
        # Add first parameter
        op1 = ModTaskAddParamOperation("clone", "timeout", "30m")
        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)
        tasks = loaded_doc["spec"]["tasks"]
        result1 = op1._add_param(tasks, ["spec", "tasks"], pipeline_yaml_file, style)
        assert result1 is True

        # Add second parameter
        op2 = ModTaskAddParamOperation("clone", "depth", "1")
        loaded_doc = load_yaml(pipeline_yaml_file)
        tasks = loaded_doc["spec"]["tasks"]
        result2 = op2._add_param(tasks, ["spec", "tasks"], pipeline_yaml_file, style)
        assert result2 is True

        expected = dedent(
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
                    - name: timeout
                      value: 30m
                    - name: depth
                      value: '1'
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

        assert read_file_content(pipeline_yaml_file) == expected

    def test_add_then_remove_param(self, pipeline_yaml_file):
        """Test adding a parameter and then removing it."""
        # Add parameter
        op_add = ModTaskAddParamOperation("clone", "timeout", "30m")
        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)
        tasks = loaded_doc["spec"]["tasks"]
        result_add = op_add._add_param(tasks, ["spec", "tasks"], pipeline_yaml_file, style)
        assert result_add is True

        # Remove parameter
        op_remove = ModTaskRemoveParamOperation("clone", "timeout")
        loaded_doc = load_yaml(pipeline_yaml_file)
        tasks = loaded_doc["spec"]["tasks"]
        result_remove = op_remove._remove_param(tasks, ["spec", "tasks"], pipeline_yaml_file, style)
        assert result_remove is True

        expected = dedent(
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

        assert read_file_content(pipeline_yaml_file) == expected

    def test_operations_on_different_tasks(self, pipeline_yaml_file):
        """Test performing operations on different tasks in the same pipeline."""
        # Add param to clone task
        op1 = ModTaskAddParamOperation("clone", "timeout", "30m")
        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)
        tasks = loaded_doc["spec"]["tasks"]
        result1 = op1._add_param(tasks, ["spec", "tasks"], pipeline_yaml_file, style)
        assert result1 is True

        # Add param to build task
        op2 = ModTaskAddParamOperation("build", "CONTEXT", "./")
        loaded_doc = load_yaml(pipeline_yaml_file)
        tasks = loaded_doc["spec"]["tasks"]
        result2 = op2._add_param(tasks, ["spec", "tasks"], pipeline_yaml_file, style)
        assert result2 is True

        # Remove param from build task
        op3 = ModTaskRemoveParamOperation("build", "IMAGE")
        loaded_doc = load_yaml(pipeline_yaml_file)
        tasks = loaded_doc["spec"]["tasks"]
        result3 = op3._remove_param(tasks, ["spec", "tasks"], pipeline_yaml_file, style)
        assert result3 is True

        expected = dedent(
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
                    - name: timeout
                      value: 30m
                - name: build
                  taskRef:
                    name: buildah
                  params:
                    - name: CONTEXT
                      value: ./
                - name: test-task
                  taskRef:
                    name: test-runner
            """
        )

        assert read_file_content(pipeline_yaml_file) == expected
