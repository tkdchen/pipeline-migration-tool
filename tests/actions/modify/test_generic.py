import pytest
from pathlib import Path
from textwrap import dedent

from pipeline_migration.actions.modify.generic import (
    ModGenericInsert,
    ModGenericReplace,
    ModGenericRemove,
    YAMLPathNotFoundError,
    _yaml_path_from_param,
    _yaml_from_value_param,
)
from pipeline_migration.utils import load_yaml, YAMLStyle


def read_file_content(file_path: Path) -> str:
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
          params:
            - name: repo-url
              value: "https://github.com/default/repo"
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
            params:
              - name: global-param
                value: "global-value"
        """
    )

    return create_yaml_file(content)


@pytest.fixture
def simple_yaml_file(create_yaml_file):
    """Create a simple YAML file for testing."""
    content = dedent(
        """\
        root:
          level1:
            items:
              - name: item1
                value: 1
              - name: item2
                value: 2
            config:
              setting1: "value1"
              setting2: "value2"
        """
    )

    return create_yaml_file(content)


class TestYAMLPathFromParam:
    """Test cases for yaml_path_from_param function."""

    def test_valid_yaml_path_list(self):
        """Test parsing a valid YAML path list."""
        yaml_path_str = '["spec", "tasks", 0, "params"]'
        result = _yaml_path_from_param(yaml_path_str)
        assert result == ["spec", "tasks", 0, "params"]

    def test_valid_yaml_path_mixed_types(self):
        """Test parsing a YAML path with mixed string and integer types."""
        yaml_path_str = '["metadata", "name"]'
        result = _yaml_path_from_param(yaml_path_str)
        assert result == ["metadata", "name"]

    def test_empty_yaml_path(self):
        """Test parsing an empty YAML path."""
        yaml_path_str = "[]"
        result = _yaml_path_from_param(yaml_path_str)
        assert result == []

    def test_invalid_yaml_path_not_list(self):
        """Test error when YAML path is not a list."""
        yaml_path_str = '"not_a_list"'
        with pytest.raises(ValueError, match="Provided YAML path must be a sequence"):
            _yaml_path_from_param(yaml_path_str)

    def test_invalid_yaml_path_invalid_types(self):
        """Test error when YAML path contains invalid types."""
        yaml_path_str = '["valid", 1.5, "string"]'
        with pytest.raises(ValueError, match="must contain only string or integer values"):
            _yaml_path_from_param(yaml_path_str)

    def test_invalid_yaml_syntax(self):
        """Test error when YAML syntax is invalid."""
        yaml_path_str = '["unclosed", list'
        with pytest.raises(Exception):  # YAML parsing error
            _yaml_path_from_param(yaml_path_str)


class TestYAMLFromValueParam:
    """Test cases for yaml_from_value_param function."""

    def test_valid_yaml_dict(self):
        """Test parsing a valid YAML dictionary."""
        value_str = '{"name": "test", "value": 123}'
        result = _yaml_from_value_param(value_str)
        assert result == {"name": "test", "value": 123}

    def test_valid_yaml_list(self):
        """Test parsing a valid YAML list."""
        value_str = '[{"name": "item1"}, {"name": "item2"}]'
        result = _yaml_from_value_param(value_str)
        assert result == [{"name": "item1"}, {"name": "item2"}]

    def test_complex_nested_structure(self):
        """Test parsing a complex nested YAML structure."""
        value_str = dedent(
            """\
            {
              "tasks": [
                {
                  "name": "test-task",
                  "params": [
                    {"name": "param1", "value": "value1"}
                  ]
                }
              ]
            }
            """
        )
        result = _yaml_from_value_param(value_str)
        expected = {
            "tasks": [{"name": "test-task", "params": [{"name": "param1", "value": "value1"}]}]
        }
        assert result == expected

    def test_valid_scalar_string(self):
        """Test parsing a valid scalar string value."""
        value_str = '"just_a_string"'
        result = _yaml_from_value_param(value_str)
        assert result == "just_a_string"

    def test_valid_scalar_integer(self):
        """Test parsing a valid scalar integer value."""
        value_str = "42"
        result = _yaml_from_value_param(value_str)
        assert result == 42

    def test_valid_scalar_boolean(self):
        """Test parsing a valid scalar boolean value."""
        value_str = "true"
        result = _yaml_from_value_param(value_str)
        assert result is True

    def test_invalid_yaml_syntax(self):
        """Test error when YAML syntax is invalid."""
        value_str = '{"unclosed": dict'
        with pytest.raises(Exception):  # YAML parsing error
            _yaml_from_value_param(value_str)


class TestModGenericInsert:
    """Test cases for ModGenericInsert class."""

    def test_initialization(self):
        """Test operation initialization."""
        yaml_path = ["spec", "tasks"]
        value = {"name": "new-task"}
        op = ModGenericInsert(yaml_path, value)
        assert op.yaml_path == yaml_path
        assert op.value == value

    def test_insert_into_dict(self, simple_yaml_file):
        """Test inserting new key-value pair into a dictionary."""
        op = ModGenericInsert(["root", "level1", "config"], {"setting3": "value3"})

        loaded_doc = load_yaml(simple_yaml_file)
        style = YAMLStyle.detect(simple_yaml_file)

        op.handle_pipeline_file(simple_yaml_file, loaded_doc, style)

        expected = dedent(
            """\
            root:
              level1:
                items:
                  - name: item1
                    value: 1
                  - name: item2
                    value: 2
                config:
                  setting1: "value1"
                  setting2: "value2"
                  setting3: value3
            """
        )

        actual = read_file_content(simple_yaml_file)
        assert actual == expected

    def test_insert_into_list(self, simple_yaml_file):
        """Test inserting new item into a list."""
        op = ModGenericInsert(["root", "level1", "items"], {"name": "item3", "value": 3})

        loaded_doc = load_yaml(simple_yaml_file)
        style = YAMLStyle.detect(simple_yaml_file)

        op.handle_pipeline_file(simple_yaml_file, loaded_doc, style)

        expected = dedent(
            """\
            root:
              level1:
                items:
                  - name: item1
                    value: 1
                  - name: item2
                    value: 2
                  - name: item3
                    value: 3
                config:
                  setting1: "value1"
                  setting2: "value2"
            """
        )

        actual = read_file_content(simple_yaml_file)
        assert actual == expected

    def test_insert_into_pipeline_tasks(self, pipeline_yaml_file):
        """Test inserting a new task into a pipeline."""
        new_task = {
            "name": "test",
            "taskRef": {"name": "test-runner"},
            "params": [{"name": "verbose", "value": "true"}],
        }
        op = ModGenericInsert(["spec", "tasks"], new_task)

        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)

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
                - name: build
                  taskRef:
                    name: buildah
                  params:
                    - name: IMAGE
                      value: "registry.io/app:latest"
                - name: test
                  taskRef:
                    name: test-runner
                  params:
                    - name: verbose
                      value: 'true'
              params:
                - name: repo-url
                  value: "https://github.com/default/repo"
            """
        )

        actual = read_file_content(pipeline_yaml_file)
        assert actual == expected

    def test_validate_yaml_path_valid(self, simple_yaml_file):
        """Test validation with a valid YAML path."""
        op = ModGenericInsert(["root", "level1", "config"], {"new": "value"})
        loaded_doc = load_yaml(simple_yaml_file)

        # Should not raise an exception
        op.validate_yaml_path(loaded_doc)

    def test_validate_yaml_path_not_found(self, simple_yaml_file):
        """Test validation with a non-existent YAML path."""
        op = ModGenericInsert(["root", "nonexistent", "path"], {"new": "value"})
        loaded_doc = load_yaml(simple_yaml_file)

        with pytest.raises(YAMLPathNotFoundError, match="doesn't exist in the doc"):
            op.validate_yaml_path(loaded_doc)

    def test_validate_yaml_path_not_container(self, simple_yaml_file):
        """Test validation when path points to a scalar value."""
        op = ModGenericInsert(["root", "level1", "config", "setting1"], {"new": "value"})
        loaded_doc = load_yaml(simple_yaml_file)

        with pytest.raises(RuntimeError, match="must point to sequence or map"):
            op.validate_yaml_path(loaded_doc)

    def test_handle_pipeline_run_file(self, pipeline_run_yaml_file):
        """Test handling PipelineRun files."""
        new_param = {"name": "new-param", "value": "new-value"}
        op = ModGenericInsert(["spec", "pipelineSpec", "params"], new_param)

        loaded_doc = load_yaml(pipeline_run_yaml_file)
        style = YAMLStyle.detect(pipeline_run_yaml_file)

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
                params:
                  - name: global-param
                    value: "global-value"
                  - name: new-param
                    value: new-value
            """
        )

        actual = read_file_content(pipeline_run_yaml_file)
        assert actual == expected

    def test_insert_scalar_string_into_list(self, create_yaml_file):
        """Test inserting a scalar string value into a list."""
        content = dedent(
            """\
            items:
              - first
              - second
            """
        )
        yaml_file = create_yaml_file(content)

        op = ModGenericInsert(["items"], "third")
        loaded_doc = load_yaml(yaml_file)
        style = YAMLStyle.detect(yaml_file)

        op.handle_pipeline_file(yaml_file, loaded_doc, style)

        expected = dedent(
            """\
            items:
              - first
              - second
              - third
            """
        )

        actual = read_file_content(yaml_file)
        assert actual == expected

    def test_insert_scalar_integer_into_list(self, create_yaml_file):
        """Test inserting a scalar integer value into a list."""
        content = dedent(
            """\
            numbers:
              - 1
              - 2
            """
        )
        yaml_file = create_yaml_file(content)

        op = ModGenericInsert(["numbers"], 3)
        loaded_doc = load_yaml(yaml_file)
        style = YAMLStyle.detect(yaml_file)

        op.handle_pipeline_file(yaml_file, loaded_doc, style)

        expected = dedent(
            """\
            numbers:
              - 1
              - 2
              - 3
            """
        )

        actual = read_file_content(yaml_file)
        assert actual == expected

    def test_insert_scalar_into_dict_fails(self, create_yaml_file):
        """Test that inserting a scalar into a dict raises an error."""
        content = dedent(
            """\
            config:
              key1: value1
              key2: value2
            """
        )
        yaml_file = create_yaml_file(content)

        op = ModGenericInsert(["config"], "scalar_value")
        loaded_doc = load_yaml(yaml_file)
        style = YAMLStyle.detect(yaml_file)

        with pytest.raises(ValueError, match="Only dict values can be inserted into a dict"):
            op.handle_pipeline_file(yaml_file, loaded_doc, style)


class TestModGenericReplace:
    """Test cases for ModGenericReplace class."""

    def test_initialization(self):
        """Test operation initialization."""
        yaml_path = ["spec", "tasks", 0]
        value = {"name": "replaced-task"}
        op = ModGenericReplace(yaml_path, value)
        assert op.yaml_path == yaml_path
        assert op.value == value

    def test_replace_dict_item(self, simple_yaml_file):
        """Test replacing a dictionary item."""
        new_config = {"setting1": "new_value1", "setting3": "value3"}
        op = ModGenericReplace(["root", "level1", "config"], new_config)

        loaded_doc = load_yaml(simple_yaml_file)
        style = YAMLStyle.detect(simple_yaml_file)

        op.handle_pipeline_file(simple_yaml_file, loaded_doc, style)

        expected = dedent(
            """\
            root:
              level1:
                items:
                  - name: item1
                    value: 1
                  - name: item2
                    value: 2
                config:
                  setting1: new_value1
                  setting3: value3
            """
        )

        actual = read_file_content(simple_yaml_file)
        assert actual == expected

    def test_replace_list_item(self, simple_yaml_file):
        """Test replacing a list item."""
        new_item = {"name": "replaced-item", "value": 999}
        op = ModGenericReplace(["root", "level1", "items", 0], new_item)

        loaded_doc = load_yaml(simple_yaml_file)
        style = YAMLStyle.detect(simple_yaml_file)

        op.handle_pipeline_file(simple_yaml_file, loaded_doc, style)

        expected = dedent(
            """\
            root:
              level1:
                items:
                  - name: replaced-item
                    value: 999
                  - name: item2
                    value: 2
                config:
                  setting1: "value1"
                  setting2: "value2"
            """
        )

        actual = read_file_content(simple_yaml_file)
        assert actual == expected

    def test_replace_pipeline_task(self, pipeline_yaml_file):
        """Test replacing a pipeline task."""
        new_task = {
            "name": "replaced-clone",
            "taskRef": {"name": "git-clone-v2"},
            "params": [{"name": "depth", "value": "1"}],
        }
        op = ModGenericReplace(["spec", "tasks", 0], new_task)

        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)

        op.handle_pipeline_file(pipeline_yaml_file, loaded_doc, style)

        expected = dedent(
            """\
            apiVersion: tekton.dev/v1
            kind: Pipeline
            metadata:
              name: test-pipeline
            spec:
              tasks:
                - name: replaced-clone
                  taskRef:
                    name: git-clone-v2
                  params:
                    - name: depth
                      value: '1'
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

        actual = read_file_content(pipeline_yaml_file)
        assert actual == expected

    def test_replace_entire_list(self, simple_yaml_file):
        """Test replacing an entire list."""
        new_items = [{"name": "new-item1", "value": 10}, {"name": "new-item2", "value": 20}]
        op = ModGenericReplace(["root", "level1", "items"], new_items)

        loaded_doc = load_yaml(simple_yaml_file)
        style = YAMLStyle.detect(simple_yaml_file)

        op.handle_pipeline_file(simple_yaml_file, loaded_doc, style)

        expected = dedent(
            """\
            root:
              level1:
                items:
                  - name: new-item1
                    value: 10
                  - name: new-item2
                    value: 20
                config:
                  setting1: "value1"
                  setting2: "value2"
            """
        )

        actual = read_file_content(simple_yaml_file)
        assert actual == expected

    def test_replace_scalar_string_value(self, simple_yaml_file):
        """Test replacing a scalar string value."""
        op = ModGenericReplace(["root", "level1", "config", "setting1"], "new_string_value")

        loaded_doc = load_yaml(simple_yaml_file)
        style = YAMLStyle.detect(simple_yaml_file)

        op.handle_pipeline_file(simple_yaml_file, loaded_doc, style)

        expected = dedent(
            """\
            root:
              level1:
                items:
                  - name: item1
                    value: 1
                  - name: item2
                    value: 2
                config:
                  setting1: "new_string_value"
                  setting2: "value2"
            """
        )

        actual = read_file_content(simple_yaml_file)
        assert actual == expected


class TestModGenericRemove:
    """Test cases for ModGenericRemove class."""

    def test_initialization(self):
        """Test operation initialization."""
        yaml_path = ["spec", "tasks", 0]
        op = ModGenericRemove(yaml_path)
        assert op.yaml_path == yaml_path

    def test_remove_dict_item(self, simple_yaml_file):
        """Test removing a dictionary item."""
        op = ModGenericRemove(["root", "level1", "config"])

        loaded_doc = load_yaml(simple_yaml_file)
        style = YAMLStyle.detect(simple_yaml_file)

        op.handle_pipeline_file(simple_yaml_file, loaded_doc, style)

        expected = dedent(
            """\
            root:
              level1:
                items:
                  - name: item1
                    value: 1
                  - name: item2
                    value: 2
            """
        )

        actual = read_file_content(simple_yaml_file)
        assert actual == expected

    def test_remove_list_item(self, simple_yaml_file):
        """Test removing a list item."""
        op = ModGenericRemove(["root", "level1", "items", 0])

        loaded_doc = load_yaml(simple_yaml_file)
        style = YAMLStyle.detect(simple_yaml_file)

        op.handle_pipeline_file(simple_yaml_file, loaded_doc, style)

        expected = dedent(
            """\
            root:
              level1:
                items:
                  - name: item2
                    value: 2
                config:
                  setting1: "value1"
                  setting2: "value2"
            """
        )

        actual = read_file_content(simple_yaml_file)
        assert actual == expected

    def test_remove_pipeline_task(self, pipeline_yaml_file):
        """Test removing a pipeline task."""
        op = ModGenericRemove(["spec", "tasks", 1])  # Remove build task

        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)

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
              params:
                - name: repo-url
                  value: "https://github.com/default/repo"
            """
        )

        actual = read_file_content(pipeline_yaml_file)
        assert actual == expected

    def test_remove_nested_structure(self, pipeline_yaml_file):
        """Test removing a nested structure."""
        op = ModGenericRemove(["spec", "tasks", 0, "params"])

        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)

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

        actual = read_file_content(pipeline_yaml_file)
        assert actual == expected

    def test_remove_scalar_from_dict(self, simple_yaml_file):
        """Test removing a scalar value from a dictionary."""
        op = ModGenericRemove(["root", "level1", "config", "setting1"])

        loaded_doc = load_yaml(simple_yaml_file)
        style = YAMLStyle.detect(simple_yaml_file)

        op.handle_pipeline_file(simple_yaml_file, loaded_doc, style)

        expected = dedent(
            """\
            root:
              level1:
                items:
                  - name: item1
                    value: 1
                  - name: item2
                    value: 2
                config:
                  setting2: "value2"
            """
        )

        actual = read_file_content(simple_yaml_file)
        assert actual == expected

    def test_remove_nested_scalar_with_cascade(self, pipeline_yaml_file):
        """Test removing a nested scalar value that triggers cascade deletion."""
        op = ModGenericRemove(["spec", "tasks", 0, "taskRef", "name"])

        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)

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

        actual = read_file_content(pipeline_yaml_file)
        assert actual == expected

    def test_remove_scalar_from_list_item(self, simple_yaml_file):
        """Test removing a scalar value from within a list item."""
        op = ModGenericRemove(["root", "level1", "items", 0, "value"])

        loaded_doc = load_yaml(simple_yaml_file)
        style = YAMLStyle.detect(simple_yaml_file)

        op.handle_pipeline_file(simple_yaml_file, loaded_doc, style)

        expected = dedent(
            """\
            root:
              level1:
                items:
                  - name: item1
                  - name: item2
                    value: 2
                config:
                  setting1: "value1"
                  setting2: "value2"
            """
        )

        actual = read_file_content(simple_yaml_file)
        assert actual == expected

    def test_remove_scalar_string_from_pipeline(self, pipeline_yaml_file):
        """Test removing a scalar string value from pipeline metadata."""
        op = ModGenericRemove(["metadata", "name"])

        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)

        op.handle_pipeline_file(pipeline_yaml_file, loaded_doc, style)

        expected = dedent(
            """\
            apiVersion: tekton.dev/v1
            kind: Pipeline
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

        actual = read_file_content(pipeline_yaml_file)
        assert actual == expected

    def test_remove_scalar_from_param_value(self, pipeline_yaml_file):
        """Test removing a scalar value from a task parameter."""
        op = ModGenericRemove(["spec", "tasks", 0, "params", 0, "value"])

        loaded_doc = load_yaml(pipeline_yaml_file)
        style = YAMLStyle.detect(pipeline_yaml_file)

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

        actual = read_file_content(pipeline_yaml_file)
        assert actual == expected


class TestErrorHandling:
    """Test error handling scenarios."""

    def test_insert_invalid_path(self, simple_yaml_file):
        """Test insert operation with invalid path."""
        op = ModGenericInsert(["nonexistent", "path"], {"key": "value"})
        loaded_doc = load_yaml(simple_yaml_file)
        style = YAMLStyle.detect(simple_yaml_file)

        with pytest.raises(YAMLPathNotFoundError):
            op.handle_pipeline_file(simple_yaml_file, loaded_doc, style)

    def test_replace_invalid_path(self, simple_yaml_file):
        """Test replace operation with invalid path."""
        op = ModGenericReplace(["nonexistent", "path"], {"key": "value"})
        loaded_doc = load_yaml(simple_yaml_file)
        style = YAMLStyle.detect(simple_yaml_file)

        with pytest.raises(YAMLPathNotFoundError):
            op.handle_pipeline_file(simple_yaml_file, loaded_doc, style)

    def test_remove_invalid_path(self, simple_yaml_file):
        """Test remove operation with invalid path."""
        op = ModGenericRemove(["nonexistent", "path"])
        loaded_doc = load_yaml(simple_yaml_file)
        style = YAMLStyle.detect(simple_yaml_file)

        with pytest.raises(YAMLPathNotFoundError):
            op.handle_pipeline_file(simple_yaml_file, loaded_doc, style)

    def test_path_to_scalar_value(self, simple_yaml_file):
        """Test operations when path points to scalar value."""
        op = ModGenericInsert(["root", "level1", "config", "setting1"], {"key": "value"})
        loaded_doc = load_yaml(simple_yaml_file)

        with pytest.raises(RuntimeError, match="must point to sequence or map"):
            op.validate_yaml_path(loaded_doc)
