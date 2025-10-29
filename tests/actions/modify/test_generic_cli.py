from pathlib import Path
from textwrap import dedent
from typing import Any

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
          params:
            - name: repo-url
              value: "https://github.com/default/repo"
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
            params:
              - name: global-param
                value: "global-value"
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
          workspaces:
            - name: source
        """
    )

    pipeline_file = tekton_dir / "build.yaml"
    pipeline_file.write_text(pipeline_content)

    return ComponentRepo(component_dir)


def verify_yaml_path_exists(file_path: Path, yaml_path: list, expected_value: Any = None):
    """Helper function to verify a YAML path exists and optionally has a specific value."""
    doc = load_yaml(file_path)

    current = doc
    for path_element in yaml_path:
        assert path_element in current or (
            isinstance(current, list) and path_element < len(current)
        ), f"Path element {path_element} not found in {file_path}"
        current = current[path_element]

    if expected_value is not None:
        assert (
            current == expected_value
        ), f"Value at path {yaml_path} is {current}, expected {expected_value}"


def verify_yaml_path_not_exists(file_path: Path, yaml_path: list):
    """Helper function to verify a YAML path does not exist."""
    doc = load_yaml(file_path)

    current = doc
    for _, path_element in enumerate(yaml_path):
        if isinstance(current, dict) and path_element not in current:
            return  # Path doesn't exist, as expected
        elif isinstance(current, list) and path_element >= len(current):
            return  # Index out of bounds, path doesn't exist
        elif path_element not in current:
            return  # Path doesn't exist
        current = current[path_element]

    # If we get here, the path exists when it shouldn't
    assert False, f"Path {yaml_path} still exists in {file_path}"


class TestModifyGenericInsert:
    """Test cases for the modify generic insert CLI command."""

    def test_insert_into_dict(self, component_pipeline_dir, monkeypatch):
        """Test inserting a new key-value pair into a dictionary."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "generic",
            "insert",
            '["metadata"]',
            '{"labels": {"app": "test"}}',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        for yaml_file in component_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_yaml_path_exists(yaml_file, ["metadata", "labels"], {"app": "test"})

    def test_insert_into_list(self, component_pipeline_dir, monkeypatch):
        """Test inserting a new item into a list."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "generic",
            "insert",
            '["spec", "tasks"]',
            '{"name": "test", "taskRef": {"name": "test-runner"}}',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        doc = load_yaml(pipeline_file)
        tasks = doc["spec"]["tasks"]
        assert len(tasks) == 3
        assert tasks[2]["name"] == "test"

    def test_insert_complex_nested_structure(self, component_pipeline_dir, monkeypatch):
        """Test inserting a complex nested structure."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "generic",
            "insert",
            '["spec"]',
            '{"workspaces": [{"name": "source", "description": "Source workspace"}]}',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        verify_yaml_path_exists(
            pipeline_file,
            ["spec", "workspaces"],
            [{"name": "source", "description": "Source workspace"}],
        )

    def test_insert_into_specific_file(self, component_pipeline_dir, monkeypatch):
        """Test inserting into a specific file."""
        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(pipeline_file),
            "generic",
            "insert",
            '["spec", "params"]',
            '{"name": "new-param", "value": "new-value"}',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        doc = load_yaml(pipeline_file)
        params = doc["spec"]["params"]
        assert len(params) == 2
        assert params[1] == {"name": "new-param", "value": "new-value"}

    def test_insert_with_multiple_directories(
        self, component_pipeline_dir, second_component_dir, monkeypatch
    ):
        """Test inserting across multiple directories."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "--file-or-dir",
            str(second_component_dir.tekton_dir),
            "generic",
            "insert",
            '["metadata"]',
            '{"annotations": {"test": "annotation"}}',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        for repo in [component_pipeline_dir, second_component_dir]:
            for yaml_file in repo.tekton_dir.glob("*.yaml"):
                verify_yaml_path_exists(
                    yaml_file, ["metadata", "annotations"], {"test": "annotation"}
                )

    def test_use_relative_tekton_dir(self, component_pipeline_dir, monkeypatch):
        """Test using the default .tekton directory."""
        # Change to the component directory
        monkeypatch.chdir(str(component_pipeline_dir.base_path))

        cmd = ["pmt", "modify", "generic", "insert", '["spec"]', '{"description": "Test pipeline"}']

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        for yaml_file in component_pipeline_dir.tekton_dir.glob("*.yaml"):
            verify_yaml_path_exists(yaml_file, ["spec", "description"], "Test pipeline")

    def test_insert_scalar_text(self, second_component_dir, monkeypatch):
        """Test inserting a scalar integer into a list."""
        # Use workspaces list from second component
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(second_component_dir.tekton_dir),
            "generic",
            "insert",
            '["spec", "workspaces"]',
            '"output"',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        pipeline_file = second_component_dir.tekton_dir / "build.yaml"
        doc = load_yaml(pipeline_file)
        workspaces = doc["spec"]["workspaces"]
        assert len(workspaces) == 2
        assert workspaces[1] == "output"

    def test_insert_scalar_integer(self, second_component_dir, monkeypatch):
        """Test inserting a scalar integer into a list."""
        # Use workspaces list from second component
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(second_component_dir.tekton_dir),
            "generic",
            "insert",
            '["spec", "workspaces"]',
            "404",
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        pipeline_file = second_component_dir.tekton_dir / "build.yaml"
        doc = load_yaml(pipeline_file)
        workspaces = doc["spec"]["workspaces"]
        assert len(workspaces) == 2
        assert workspaces[1] == 404


class TestModifyGenericReplace:
    """Test cases for the modify generic replace CLI command."""

    def test_replace_dict_value(self, component_pipeline_dir, monkeypatch):
        """Test replacing a dictionary value."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "generic",
            "replace",
            '["spec", "params", 0]',
            '{"name": "new-param", "value": "new-value"}',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        verify_yaml_path_exists(
            pipeline_file, ["spec", "params", 0], {"name": "new-param", "value": "new-value"}
        )

    def test_replace_list_item(self, component_pipeline_dir, monkeypatch):
        """Test replacing a list item."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "generic",
            "replace",
            '["spec", "tasks", 0]',
            '{"name": "replaced-clone", "taskRef": {"name": "git-clone-v2"}}',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        doc = load_yaml(pipeline_file)
        first_task = doc["spec"]["tasks"][0]
        assert first_task["name"] == "replaced-clone"
        assert first_task["taskRef"]["name"] == "git-clone-v2"
        assert "params" not in first_task  # Old params should be gone

    def test_replace_entire_structure(self, component_pipeline_dir, monkeypatch):
        """Test replacing an entire structure."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "generic",
            "replace",
            '["spec", "params"]',
            '[{"name": "new-param", "value": "new-value"}]',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        verify_yaml_path_exists(
            pipeline_file, ["spec", "params"], [{"name": "new-param", "value": "new-value"}]
        )

    def test_replace_nested_item(self, component_pipeline_dir, monkeypatch):
        """Test replacing a nested item."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "generic",
            "replace",
            '["spec", "tasks", 0, "taskRef"]',
            '{"name": "git-clone-v3", "kind": "Task"}',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        verify_yaml_path_exists(
            pipeline_file, ["spec", "tasks", 0, "taskRef"], {"name": "git-clone-v3", "kind": "Task"}
        )


class TestModifyGenericRemove:
    """Test cases for the modify generic remove CLI command."""

    def test_remove_dict_key(self, component_pipeline_dir, monkeypatch):
        """Test removing a dictionary key."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "generic",
            "remove",
            '["spec", "params"]',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        verify_yaml_path_not_exists(pipeline_file, ["spec", "params"])

    def test_remove_list_item(self, component_pipeline_dir, monkeypatch):
        """Test removing a list item."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "generic",
            "remove",
            '["spec", "tasks", 1]',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        doc = load_yaml(pipeline_file)
        tasks = doc["spec"]["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["name"] == "clone"  # Only clone task should remain

    def test_remove_nested_structure(self, component_pipeline_dir, monkeypatch):
        """Test removing a nested structure."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "generic",
            "remove",
            '["spec", "tasks", 0, "params"]',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        doc = load_yaml(pipeline_file)
        clone_task = doc["spec"]["tasks"][0]
        assert "params" not in clone_task
        assert clone_task["name"] == "clone"  # Task should still exist

    def test_remove_from_pipeline_run(self, component_pipeline_dir, monkeypatch):
        """Test removing from a PipelineRun file."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "generic",
            "remove",
            '["spec", "pipelineSpec", "params"]',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        pipeline_run_file = component_pipeline_dir.tekton_dir / "pipeline-run.yaml"
        verify_yaml_path_not_exists(pipeline_run_file, ["spec", "pipelineSpec", "params"])

    def test_remove_from_multiple_files(
        self, component_pipeline_dir, second_component_dir, monkeypatch
    ):
        """Test removing from multiple files."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "--file-or-dir",
            str(second_component_dir.tekton_dir),
            "generic",
            "remove",
            '["spec", "workspaces"]',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        second_file = second_component_dir.tekton_dir / "build.yaml"
        verify_yaml_path_not_exists(second_file, ["spec", "workspaces"])

    def test_remove_scalar_from_metadata(self, component_pipeline_dir, monkeypatch):
        """Test removing a scalar value from metadata."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "generic",
            "remove",
            '["metadata", "name"]',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        verify_yaml_path_not_exists(pipeline_file, ["metadata", "name"])

    def test_remove_scalar_with_cascade(self, component_pipeline_dir, monkeypatch):
        """Test removing a scalar value that triggers cascade deletion."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "generic",
            "remove",
            '["spec", "tasks", 0, "taskRef", "name"]',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        # taskRef should be completely removed since it only had one key
        verify_yaml_path_not_exists(pipeline_file, ["spec", "tasks", 0, "taskRef"])


class TestModifyGenericErrorHandling:
    """Test error handling and edge cases."""

    def test_missing_yaml_path_argument(self, monkeypatch):
        """Test that missing YAML path argument is handled."""
        cmd = ["pmt", "modify", "generic", "insert"]

        monkeypatch.setattr("sys.argv", cmd)

        with pytest.raises(SystemExit):
            entry_point()

    def test_missing_value_argument_for_insert(self, monkeypatch):
        """Test that missing value argument for insert is handled."""
        cmd = ["pmt", "modify", "generic", "insert", '["path"]']

        monkeypatch.setattr("sys.argv", cmd)

        with pytest.raises(SystemExit):
            entry_point()

    def test_invalid_yaml_path_format(self, component_pipeline_dir, monkeypatch):
        """Test handling of invalid YAML path format."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "generic",
            "insert",
            '"not_a_list"',  # Invalid path format
            '{"key": "value"}',
        ]

        monkeypatch.setattr("sys.argv", cmd)

        with pytest.raises(SystemExit):
            entry_point()

    def test_nonexistent_file_path(self, monkeypatch):
        """Test handling of nonexistent file paths."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            "/nonexistent/path",
            "generic",
            "insert",
            '["spec"]',
            '{"key": "value"}',
        ]

        monkeypatch.setattr("sys.argv", cmd)

        # Should handle gracefully (may not find any files to process)
        entry_point()  # Should not crash

    def test_nonexistent_yaml_path(self, component_pipeline_dir, monkeypatch, caplog):
        """Test handling when YAML path doesn't exist in file."""
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "generic",
            "insert",
            '["nonexistent", "path"]',
            '{"key": "value"}',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Should log warnings about skipped files
        assert "Skipped file" in caplog.text
        assert "doesn't exist in the doc" in caplog.text

    def test_invalid_subcommand(self, monkeypatch):
        """Test that invalid subcommands are handled."""
        cmd = ["pmt", "modify", "generic", "invalid-command"]

        monkeypatch.setattr("sys.argv", cmd)

        with pytest.raises(SystemExit):
            entry_point()


class TestModifyGenericYQIntegration:
    """Test integration with yq-style path expressions."""

    def test_yq_style_path_simple(self, component_pipeline_dir, monkeypatch):
        """Test using a yq-style path expression."""
        # This simulates what you'd get from: yq '.spec.tasks[0] | path'
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "generic",
            "replace",
            dedent(
                """\
                - spec
                - tasks
                - 0
                """
            ),
            '{"name": "yq-replaced", "taskRef": {"name": "yq-task"}}',
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify replacement
        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        doc = load_yaml(pipeline_file)
        first_task = doc["spec"]["tasks"][0]
        assert first_task["name"] == "yq-replaced"

    def test_complex_yq_path(self, component_pipeline_dir, monkeypatch):
        """Test using a complex yq-style path."""
        # Remove a specific parameter from a specific task
        cmd = [
            "pmt",
            "modify",
            "--file-or-dir",
            str(component_pipeline_dir.tekton_dir),
            "generic",
            "remove",
            dedent(
                """\
                - spec
                - tasks
                - 0
                - params
                - 1
                """
            ),  # Remove revision param
        ]

        monkeypatch.setattr("sys.argv", cmd)
        entry_point()

        # Verify removal
        pipeline_file = component_pipeline_dir.tekton_dir / "pipeline.yaml"
        doc = load_yaml(pipeline_file)
        clone_task = doc["spec"]["tasks"][0]
        params = clone_task["params"]
        assert len(params) == 1
        assert params[0]["name"] == "url"  # Only url param should remain
