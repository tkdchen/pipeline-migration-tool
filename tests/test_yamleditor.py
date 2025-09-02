import pytest
from textwrap import dedent
from pathlib import Path

from pipeline_migration.yamleditor import (
    remove_lines_from_file,
    insert_text_at_line,
    EditYAMLEntry,
    EOF,
)
from pipeline_migration.utils import (
    load_yaml,
)


@pytest.fixture
def temp_file_with_content(create_yaml_file):
    """Create a temporary file with sample content for testing."""
    content = dedent(
        """\
        Line 1
        Line 2
        Line 3
        Line 4
        Line 5
        Line 6
        Line 7
        """
    )
    return create_yaml_file(content)


@pytest.fixture
def empty_temp_file(create_yaml_file):
    """Create an empty temporary file for testing."""
    return create_yaml_file("")


def read_file_content(file_path: str) -> str:
    """Helper function to read file content."""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


class TestRemoveLinesFromFile:
    """Test cases for remove_lines_from_file function."""

    def test_remove_lines_from_middle(self, temp_file_with_content):
        """Test removing lines from the middle of the file."""
        remove_lines_from_file(temp_file_with_content, start_line=2, num_lines=2)

        expected = dedent(
            """\
            Line 1
            Line 2
            Line 5
            Line 6
            Line 7
            """
        )

        assert read_file_content(temp_file_with_content) == expected

    def test_removal_to_EOF(self, temp_file_with_content):
        """Test removing lines from the middle to the end of file."""
        remove_lines_from_file(temp_file_with_content, start_line=2, num_lines=-1)

        expected = dedent(
            """\
            Line 1
            Line 2
            """
        )

        assert read_file_content(temp_file_with_content) == expected

    def test_remove_lines_from_beginning(self, temp_file_with_content):
        """Test removing lines from the beginning of the file."""
        remove_lines_from_file(temp_file_with_content, start_line=0, num_lines=2)

        expected = dedent(
            """\
            Line 3
            Line 4
            Line 5
            Line 6
            Line 7
            """
        )

        assert read_file_content(temp_file_with_content) == expected

    def test_remove_lines_from_end(self, temp_file_with_content):
        """Test removing lines from the end of the file."""
        remove_lines_from_file(temp_file_with_content, start_line=5, num_lines=2)

        expected = dedent(
            """\
            Line 1
            Line 2
            Line 3
            Line 4
            Line 5
            """
        )

        assert read_file_content(temp_file_with_content) == expected

    def test_remove_more_lines_than_exist(self, temp_file_with_content):
        """Test removing more lines than exist in the file."""
        remove_lines_from_file(temp_file_with_content, start_line=4, num_lines=10)

        expected = dedent(
            """\
            Line 1
            Line 2
            Line 3
            Line 4
            """
        )

        assert read_file_content(temp_file_with_content) == expected

    def test_remove_zero_lines(self, temp_file_with_content):
        """Test removing zero lines (should do nothing)."""
        original_content = read_file_content(temp_file_with_content)
        remove_lines_from_file(temp_file_with_content, start_line=2, num_lines=0)

        assert read_file_content(temp_file_with_content) == original_content

    def test_remove_all_lines(self, temp_file_with_content):
        """Test removing all lines from the file."""
        remove_lines_from_file(temp_file_with_content, start_line=0, num_lines=10)

        assert read_file_content(temp_file_with_content) == ""

    def test_invalid_start_line_negative(self, temp_file_with_content):
        """Test error when start_line is negative."""
        with pytest.raises(ValueError, match="start_line must be >= 0"):
            remove_lines_from_file(temp_file_with_content, start_line=-1, num_lines=1)

    def test_start_line_beyond_file_length(self, temp_file_with_content):
        """Test error when start_line is beyond file length."""
        with pytest.raises(
            ValueError, match="start_line \\(10\\) is beyond the file length \\(max index: 6\\)"
        ):
            remove_lines_from_file(temp_file_with_content, start_line=10, num_lines=1)

    def test_file_not_found(self):
        """Test error when file doesn't exist."""
        with pytest.raises(FileNotFoundError):
            remove_lines_from_file("nonexistent.txt", start_line=1, num_lines=1)

    def test_remove_from_empty_file(self, empty_temp_file):
        """Test removing lines from an empty file."""
        with pytest.raises(
            ValueError, match="start_line \\(0\\) is beyond the file length \\(max index: -1\\)"
        ):
            remove_lines_from_file(empty_temp_file, start_line=0, num_lines=1)


class TestInsertTextAtLine:
    """Test cases for insert_text_at_line function."""

    def test_insert_single_line_at_beginning(self, temp_file_with_content):
        """Test inserting a single line at the beginning."""
        insert_text_at_line(temp_file_with_content, 0, "New Line 0")

        expected = dedent(
            """\
            New Line 0
            Line 1
            Line 2
            Line 3
            Line 4
            Line 5
            Line 6
            Line 7
            """
        )

        assert read_file_content(temp_file_with_content) == expected

    def test_insert_single_line_in_middle(self, temp_file_with_content):
        """Test inserting a single line in the middle."""
        insert_text_at_line(temp_file_with_content, 3, "New Line 3.5")

        expected = dedent(
            """\
            Line 1
            Line 2
            Line 3
            New Line 3.5
            Line 4
            Line 5
            Line 6
            Line 7
            """
        )

        assert read_file_content(temp_file_with_content) == expected

    def test_insert_multiple_lines(self, temp_file_with_content):
        """Test inserting multiple lines."""
        multiline_text = dedent(
            """\
            New Line A
            New Line B
            New Line C"""
        )

        insert_text_at_line(temp_file_with_content, 2, multiline_text)

        expected = dedent(
            """\
            Line 1
            Line 2
            New Line A
            New Line B
            New Line C
            Line 3
            Line 4
            Line 5
            Line 6
            Line 7
            """
        )

        assert read_file_content(temp_file_with_content) == expected

    def test_insert_beyond_file_length(self, temp_file_with_content):
        """Test inserting beyond file length (should append)."""
        insert_text_at_line(temp_file_with_content, 10, "Appended Line")

        expected = dedent(
            """\
            Line 1
            Line 2
            Line 3
            Line 4
            Line 5
            Line 6
            Line 7
            Appended Line
            """
        )

        assert read_file_content(temp_file_with_content) == expected

    def test_insert_at_the_end_of_file(self, temp_file_with_content):
        """Test inserting at the end of file."""
        insert_text_at_line(temp_file_with_content, -1, "Appended Line")

        expected = dedent(
            """\
            Line 1
            Line 2
            Line 3
            Line 4
            Line 5
            Line 6
            Line 7
            Appended Line
            """
        )

        assert read_file_content(temp_file_with_content) == expected

    def test_replace_single_line(self, temp_file_with_content):
        """Test replacing a single line."""
        insert_text_at_line(temp_file_with_content, 2, "Replaced Line 3", replace_lines=1)

        expected = dedent(
            """\
            Line 1
            Line 2
            Replaced Line 3
            Line 4
            Line 5
            Line 6
            Line 7
            """
        )

        assert read_file_content(temp_file_with_content) == expected

    def test_replace_multiple_lines(self, temp_file_with_content):
        """Test replacing multiple lines."""
        multiline_replacement = dedent(
            """\
            Replacement Line A
            Replacement Line B
            Replacement Line C"""
        )

        insert_text_at_line(temp_file_with_content, 1, multiline_replacement, replace_lines=3)

        expected = dedent(
            """\
            Line 1
            Replacement Line A
            Replacement Line B
            Replacement Line C
            Line 5
            Line 6
            Line 7
            """
        )

        assert read_file_content(temp_file_with_content) == expected

    def test_replace_exactly_at_end(self, temp_file_with_content):
        """Test replacing the last line."""
        insert_text_at_line(temp_file_with_content, 6, "New Last Line", replace_lines=1)

        expected = dedent(
            """\
            Line 1
            Line 2
            Line 3
            Line 4
            Line 5
            Line 6
            New Last Line
            """
        )

        assert read_file_content(temp_file_with_content) == expected

    def test_insert_empty_text(self, temp_file_with_content):
        """Test inserting empty text."""
        insert_text_at_line(temp_file_with_content, 2, "")

        # Should add just a newline
        expected = dedent(
            """\
            Line 1
            Line 2

            Line 3
            Line 4
            Line 5
            Line 6
            Line 7
            """
        )

        assert read_file_content(temp_file_with_content) == expected

    def test_insert_text_without_newline(self, temp_file_with_content):
        """Test inserting text without trailing newline (should add one)."""
        insert_text_at_line(temp_file_with_content, 2, "Text without newline")

        expected = dedent(
            """\
            Line 1
            Line 2
            Text without newline
            Line 3
            Line 4
            Line 5
            Line 6
            Line 7
            """
        )

        assert read_file_content(temp_file_with_content) == expected

    def test_file_not_found_insert(self):
        """Test error when file doesn't exist for insertion."""
        with pytest.raises(FileNotFoundError):
            insert_text_at_line("nonexistent.txt", 1, "Text")

    def test_insert_into_empty_file(self, empty_temp_file):
        """Test inserting into an empty file."""
        insert_text_at_line(empty_temp_file, 0, "First Line")

        expected = "First Line\n"
        assert read_file_content(empty_temp_file) == expected


class TestEditYAMLEntry:
    """Test cases for EditYAMLEntry class."""

    @pytest.fixture
    def simple_yaml_file(self, create_yaml_file):
        """Create a temporary YAML file with simple structure."""
        content = dedent(
            """\
            name: test-pipeline
            spec:
              tasks:
                - name: task1
                  taskRef:
                    name: clone
                  params:
                  - name: repo-url
                    value: "https://example.com/example/repo"
            """
        )

        return create_yaml_file(content)

    @pytest.fixture
    def simple_yaml_file_flow(self, create_yaml_file):
        """Create a temporary YAML file with simple structure and flow style."""
        content = dedent(
            """\
            name: test-pipeline
            spec:
              tasks:
                - name: task1
                  taskRef: {name: clone}
                  params: [{"name": "repo-url", "value": "https://example.com/example/repo"}]
            """
        )

        return create_yaml_file(content)

    @pytest.fixture
    def empty_yaml_file(self, create_yaml_file):
        """Create an empty YAML file."""
        content = "{}\n"

        return create_yaml_file(content)

    @pytest.fixture
    def get_next_entry_test_yaml_file(self, create_yaml_file):
        """Create a temporary YAML file with simple structure."""
        content = dedent(
            """\
            name: test-pipeline
            spec:
              tasks:
                - name: task0
                - name: task1
                  taskRef:
                    name: clone
                  params:
                  - name: param1
                    value: val1
                  - name: param2
                    value: val2
            """
        )

        return create_yaml_file(content)

    def test_initialization(self, simple_yaml_file):
        """Test EditYAMLEntry initialization."""
        editor = EditYAMLEntry(simple_yaml_file)
        assert editor.yaml_file_path == simple_yaml_file
        assert editor._data is None

    def test_data_property_loading(self, simple_yaml_file):
        """Test that data property loads YAML content."""
        editor = EditYAMLEntry(simple_yaml_file)
        data = editor.data

        assert data is not None
        assert data["name"] == "test-pipeline"
        assert "spec" in data
        assert "tasks" in data["spec"]
        assert len(data["spec"]["tasks"]) == 1

    def test_data_property_caching(self, simple_yaml_file):
        """Test that data property caches the loaded data."""
        editor = EditYAMLEntry(simple_yaml_file)
        data1 = editor.data
        data2 = editor.data

        # Should return the same object instance (cached)
        assert data1 is data2

    def test_data_deleter(self, simple_yaml_file):
        """Test data property deleter."""
        editor = EditYAMLEntry(simple_yaml_file)
        _ = editor.data  # Load data
        assert editor._data is not None

        del editor.data
        assert editor._data is None

    def test_invalidate_yaml_data(self, simple_yaml_file):
        """Test invalidate_yaml_data method."""
        editor = EditYAMLEntry(simple_yaml_file)
        _ = editor.data  # Load data
        assert editor._data is not None

        editor.invalidate_yaml_data()
        assert editor._data is None

    def test_get_path_stack_dict(self, simple_yaml_file):
        """Test _get_path_stack with dictionary paths."""
        editor = EditYAMLEntry(simple_yaml_file)
        path_stack = editor._get_path_stack(["spec", "tasks", 0, "params"])

        assert len(path_stack) == 5  # root -> spec -> tasks -> params + terminal
        assert path_stack[0][1] == "spec"
        assert path_stack[1][1] == "tasks"
        assert path_stack[2][1] == 0
        assert path_stack[3][1] == "params"
        assert path_stack[4][1] is None  # terminal node

    def test_get_path_stack_list(self, simple_yaml_file):
        """Test _get_path_stack with list indices."""
        editor = EditYAMLEntry(simple_yaml_file)
        path_stack = editor._get_path_stack(["spec", "tasks", 0])

        assert len(path_stack) == 4  # root -> spec -> tasks -> index 0 + terminal
        assert path_stack[0][1] == "spec"
        assert path_stack[1][1] == "tasks"
        assert path_stack[2][1] == 0
        assert path_stack[3][1] is None  # terminal node

    def test_insert_into_dict(self, simple_yaml_file):
        """Test inserting new key-value pair into a dictionary."""
        editor = EditYAMLEntry(simple_yaml_file)

        new_data = {"runAfter": ["another-task"]}
        editor.insert(["spec", "tasks", 0], new_data)

        expected = dedent(
            """\
            name: test-pipeline
            spec:
              tasks:
                - name: task1
                  taskRef:
                    name: clone
                  params:
                  - name: repo-url
                    value: "https://example.com/example/repo"
                  runAfter:
                  - another-task
            """
        )

        assert read_file_content(simple_yaml_file) == expected

    def test_insert_into_dict_nested_structure(self, simple_yaml_file):
        """Test inserting new nested structure pair into a dictionary."""
        editor = EditYAMLEntry(simple_yaml_file)

        new_data = {"matrix": {"params": [{"name": "platform", "value": ["linux", "mac"]}]}}
        editor.insert(["spec", "tasks", 0], new_data)

        expected = dedent(
            """\
            name: test-pipeline
            spec:
              tasks:
                - name: task1
                  taskRef:
                    name: clone
                  params:
                  - name: repo-url
                    value: "https://example.com/example/repo"
                  matrix:
                    params:
                    - name: platform
                      value:
                      - linux
                      - mac
            """
        )

        assert read_file_content(simple_yaml_file) == expected

    def test_insert_into_list(self, simple_yaml_file):
        """Test inserting new item into a list."""
        editor = EditYAMLEntry(simple_yaml_file)

        new_data = {"name": "new-param", "value": "new-value"}
        editor.insert(["spec", "tasks", 0, "params"], new_data)

        expected = dedent(
            """\
            name: test-pipeline
            spec:
              tasks:
                - name: task1
                  taskRef:
                    name: clone
                  params:
                  - name: repo-url
                    value: "https://example.com/example/repo"
                  - name: new-param
                    value: new-value
            """
        )

        assert read_file_content(simple_yaml_file) == expected

    def test_replace_dict_value(self, simple_yaml_file):
        """Test replacing a value in a dictionary."""
        editor = EditYAMLEntry(simple_yaml_file)

        new_data = {"name": "test"}
        editor.replace(["spec", "tasks", 0, "taskRef"], new_data)

        expected = dedent(
            """\
            name: test-pipeline
            spec:
              tasks:
                - name: task1
                  taskRef:
                    name: test
                  params:
                  - name: repo-url
                    value: "https://example.com/example/repo"
            """
        )

        assert read_file_content(simple_yaml_file) == expected

    def test_replace_list_item(self, simple_yaml_file):
        """Test replacing an item in a list."""
        editor = EditYAMLEntry(simple_yaml_file)

        new_item = {"name": "replaced-item", "value": 999}
        editor.replace(["spec", "tasks", 0, "params", 0], new_item)

        expected = dedent(
            """\
            name: test-pipeline
            spec:
              tasks:
                - name: task1
                  taskRef:
                    name: clone
                  params:
                  - name: replaced-item
                    value: 999
            """
        )

        assert read_file_content(simple_yaml_file) == expected

    def test_delete_from_dict(self, simple_yaml_file):
        """Test deleting a key from a dictionary."""
        editor = EditYAMLEntry(simple_yaml_file)

        editor.delete(["spec", "tasks", 0, "taskRef"])

        expected = dedent(
            """\
            name: test-pipeline
            spec:
              tasks:
                - name: task1
                  params:
                  - name: repo-url
                    value: "https://example.com/example/repo"
            """
        )

        assert read_file_content(simple_yaml_file) == expected

    def test_delete_from_list(self, simple_yaml_file):
        """Test deleting an item from a list."""
        editor = EditYAMLEntry(simple_yaml_file)

        editor.delete(["spec", "tasks", 0, "params", 0])

        expected = dedent(
            """\
            name: test-pipeline
            spec:
              tasks:
                - name: task1
                  taskRef:
                    name: clone
            """
        )

        assert read_file_content(simple_yaml_file) == expected

    def test_delete_empty_parents(self, simple_yaml_file):
        """Test deleting empty parents recursivelly"""
        editor = EditYAMLEntry(simple_yaml_file)

        editor.delete(["spec", "tasks", 0])

        expected = dedent(
            """\
            name: test-pipeline
            """
        )

        with open(simple_yaml_file, "r") as f:
            content = f.read()
            assert content == expected

    def test_file_not_found(self):
        """Test error when YAML file doesn't exist."""
        non_existent_path = Path("non_existent.yaml")
        editor = EditYAMLEntry(non_existent_path)

        with pytest.raises(FileNotFoundError):
            _ = editor.data

    def test_invalid_path_key_error(self, simple_yaml_file):
        """Test error when path contains non-existent key."""
        editor = EditYAMLEntry(simple_yaml_file)

        with pytest.raises(KeyError):
            editor._get_path_stack(["non_existent_key"])

    def test_invalid_path_index_error(self, simple_yaml_file):
        """Test error when path contains invalid list index."""
        editor = EditYAMLEntry(simple_yaml_file)

        with pytest.raises(IndexError):
            editor._get_path_stack(["spec", "tasks", 999])

    def test_invalid_path_type_assertion(self, simple_yaml_file):
        """Test assertion error when path contains invalid type."""
        editor = EditYAMLEntry(simple_yaml_file)

        with pytest.raises(AssertionError):
            editor._get_path_stack([123.45])  # float is not allowed

    def test_insert_invalid_parent_assertion(self, simple_yaml_file):
        """Test assertion error when trying to insert into non-container."""
        editor = EditYAMLEntry(simple_yaml_file)

        with pytest.raises(ValueError):
            # Trying to insert into a string value
            editor.insert(["name"], "new-data")

    def test_operations_with_empty_yaml(self, empty_yaml_file):
        """Test operations on empty YAML file."""
        editor = EditYAMLEntry(empty_yaml_file)

        # Should load as empty dict
        data = editor.data
        assert isinstance(data, dict)
        assert len(data) == 0

        # Should be able to insert into empty dict
        editor.insert([], {"new_key": "new_value"})

        expected = dedent(
            """\
            new_key: new_value
            """
        )
        # Verify insertion worked
        assert read_file_content(empty_yaml_file) == expected

    def test_multiple_operations_sequence(self, simple_yaml_file):
        """Test performing multiple operations in sequence."""
        editor = EditYAMLEntry(simple_yaml_file)

        # Insert new task
        new_task = {"name": "task2", "taskRef": {"name": "deploy"}}
        editor.insert(["spec", "tasks"], new_task)

        # Delete a parameter
        editor.delete(["spec", "tasks", 0, "params", 0])

        expected = dedent(
            """\
            name: test-pipeline
            spec:
              tasks:
                - name: task1
                  taskRef:
                    name: clone
                - name: task2
                  taskRef:
                    name: deploy
            """
        )

        assert read_file_content(simple_yaml_file) == expected

    @pytest.mark.parametrize(
        "yaml_path,expected_lineno",
        [
            (["spec"], EOF),
            (["spec", "tasks"], EOF),
            (["spec", "tasks", 0], 4),
            (["spec", "tasks", 1], EOF),
            (["spec", "tasks", 1, "taskRef"], 7),
            (["spec", "tasks", 1, "params"], EOF),
            (["spec", "tasks", 1, "params", 0], 10),
            (["spec", "tasks", 1, "params", 1], EOF),
        ],
    )
    def test__get_next_entry_line(self, get_next_entry_test_yaml_file, yaml_path, expected_lineno):
        """Test cases for EditYAMLEntry._get_next_entry_line method. (lineno starts with 0)"""
        editor = EditYAMLEntry(get_next_entry_test_yaml_file)
        path_stack = editor._get_path_stack(yaml_path)
        assert editor._get_next_entry_line(path_stack) == expected_lineno


class TestEditYAMLEntryComments:
    """Tests to make sure comments are preserved"""

    @pytest.fixture
    def comments_yaml_file(self, create_yaml_file):
        content = dedent(
            """\
            spec:
                tasks:
                # comment line
                - name: init
                  params:
                    # comment as first line in array, data follows
                    - name: image-url
                      value: image  # inline comment

                    - name: rebuild
                      # comment between keys
                      value: $(params.rebuild)

                - name: build # build code
                # comment first, no data follows

                - name: test
                    # indented comment between keys
                  data: ["line1", {name: value}]
            """
        )
        return create_yaml_file(content)

    def test_insert_into_empty_commented_section(self, comments_yaml_file):
        editor = EditYAMLEntry(comments_yaml_file)

        new_item = {"params": [{"name": "test", "value": 999}]}
        editor.insert(["spec", "tasks", 1], new_item)

        expected = dedent(
            """\
            spec:
                tasks:
                # comment line
                - name: init
                  params:
                    # comment as first line in array, data follows
                    - name: image-url
                      value: image  # inline comment

                    - name: rebuild
                      # comment between keys
                      value: $(params.rebuild)

                - name: build # build code
                # comment first, no data follows

                  params:
                  - name: test
                    value: 999
                - name: test
                    # indented comment between keys
                  data: ["line1", {name: value}]
            """
        )
        assert read_file_content(comments_yaml_file) == expected

    def test_insert_into_non_empty_commented_section(self, comments_yaml_file):
        editor = EditYAMLEntry(comments_yaml_file)

        new_item = {"params": [{"name": "test", "value": 999}]}
        editor.insert(["spec", "tasks", 2], new_item)

        expected = dedent(
            """\
            spec:
                tasks:
                # comment line
                - name: init
                  params:
                    # comment as first line in array, data follows
                    - name: image-url
                      value: image  # inline comment

                    - name: rebuild
                      # comment between keys
                      value: $(params.rebuild)

                - name: build # build code
                # comment first, no data follows

                - name: test
                    # indented comment between keys
                  data: ["line1", {name: value}]
                  params:
                  - name: test
                    value: 999
            """
        )
        assert read_file_content(comments_yaml_file) == expected

    def test_delete_array_item_with_comments(self, comments_yaml_file):
        editor = EditYAMLEntry(comments_yaml_file)

        editor.delete(["spec", "tasks", 0])

        expected = dedent(
            """\
            spec:
                tasks:
                # comment line
                - name: build # build code
                # comment first, no data follows

                - name: test
                    # indented comment between keys
                  data: ["line1", {name: value}]
            """
        )
        assert read_file_content(comments_yaml_file) == expected

    def test_delete_object_item_with_comments(self, comments_yaml_file):
        editor = EditYAMLEntry(comments_yaml_file)

        editor.delete(["spec", "tasks", 0, "params"])

        expected = dedent(
            """\
            spec:
                tasks:
                # comment line
                - name: init
                - name: build # build code
                # comment first, no data follows

                - name: test
                    # indented comment between keys
                  data: ["line1", {name: value}]
            """
        )
        assert read_file_content(comments_yaml_file) == expected

    @pytest.mark.xfail(reason="known issue that inline comment doesn't keep indentation")
    def test_replace_value_with_inline_comment(self, comments_yaml_file):
        editor = EditYAMLEntry(comments_yaml_file)

        # of course by just adding a stdlib new dict, comments wouldn't be kept
        # instead of that use existing ruamel object and update value
        d = load_yaml(comments_yaml_file)
        replace = d["spec"]["tasks"][0]["params"][0]
        replace["value"] = "replaced"
        editor.replace(["spec", "tasks", 0, "params", 0], replace)

        expected = dedent(
            """\
            spec:
                tasks:
                # comment line
                - name: init
                  params:
                    # comment as first line in array, data follows
                    - name: image-url
                      value: replaced  # inline comment

                    - name: rebuild
                      # comment between keys
                      value: $(params.rebuild)

                - name: build # build code
                # comment first, no data follows

                - name: test
                    # indented comment between keys
                  data: ["line1", {name: value}]
            """
        )
        assert read_file_content(comments_yaml_file) == expected

    def test_replace_empty_commented_section(self, comments_yaml_file):
        editor = EditYAMLEntry(comments_yaml_file)

        new_item = {"name": "test-replaced"}
        editor.replace(["spec", "tasks", 1], new_item)

        expected = dedent(
            """\
            spec:
                tasks:
                # comment line
                - name: init
                  params:
                    # comment as first line in array, data follows
                    - name: image-url
                      value: image  # inline comment

                    - name: rebuild
                      # comment between keys
                      value: $(params.rebuild)

                - name: test-replaced
                - name: test
                    # indented comment between keys
                  data: ["line1", {name: value}]
            """
        )
        assert read_file_content(comments_yaml_file) == expected


class TestEditYAMLEntryFlowStyle:
    """Tests for handling partial flow-style YAML structures."""

    @pytest.fixture
    def simple_yaml_file_flow(self, create_yaml_file):
        """Create a temporary YAML file with simple structure and flow style."""
        content = dedent(
            """\
            name: test-pipeline
            spec:
              tasks:
                - name: task1
                  taskRef: {name: clone}
                  params: [{"name": "repo-url", "value": "https://example.com/example/repo"}]
            """
        )

        return create_yaml_file(content)

    @pytest.fixture
    def flow_style_yaml_file(self, create_yaml_file):
        """A YAML with flow-style lists and mappings under spec."""
        content = dedent(
            """\
            metadata: {name: flow-pipeline}
            spec:
              tasks: [
                {name: clone, taskRef: {name: git-clone}, params: [
                  {name: url, value: "https://github.com/example/repo"},
                  {name: revision, value: "main"}
                ]},
                {name: build, taskRef: {name: build}, params: [
                  {name: IMAGE, value: "buildah"}
                ]}
              ]
            """
        )
        return create_yaml_file(content)

    @pytest.fixture
    def root_flow_seq_file(self, create_yaml_file):
        """A YAML whose root is a flow-style sequence."""
        content = """[{a: 1}, {b: 2}, {c: 3}]\n"""
        return create_yaml_file(content)

    @pytest.fixture
    def flow_style_map_file(self, create_yaml_file):
        """A YAML with a flow-style mapping."""
        content = """config: {a: 1, b: 2}\n"""
        return create_yaml_file(content)

    def test_replace_list_item_flow_simple(self, simple_yaml_file_flow):
        """Test replacing an item in a list."""
        editor = EditYAMLEntry(simple_yaml_file_flow)

        new_item = {"name": "replaced-item", "value": 999}
        editor.replace(["spec", "tasks", 0, "params", 0], new_item)

        expected = dedent(
            """\
            name: test-pipeline
            spec:
              tasks:
                - name: task1
                  taskRef: {name: clone}
                  params:
                  - name: replaced-item
                    value: 999
            """
        )

        with open(simple_yaml_file_flow, "r") as f:
            content = f.read()
            assert content == expected

    def test_insert_into_list_flow_simple(self, simple_yaml_file_flow):
        """Test inserting new item into a list."""
        editor = EditYAMLEntry(simple_yaml_file_flow)

        new_data = {"name": "new-param", "value": "new-value"}
        editor.insert(["spec", "tasks", 0, "params"], new_data)

        expected = dedent(
            """\
            name: test-pipeline
            spec:
              tasks:
                - name: task1
                  taskRef: {name: clone}
                  params:
                  - {name: repo-url, value: https://example.com/example/repo}
                  - name: new-param
                    value: new-value
            """
        )

        with open(simple_yaml_file_flow, "r") as f:
            content = f.read()
            assert content == expected

    def test_delete_from_list_flow(self, simple_yaml_file_flow):
        """Test deleting an item from a list."""
        editor = EditYAMLEntry(simple_yaml_file_flow)

        editor.delete(["spec", "tasks", 0, "params", 0])

        expected = dedent(
            """\
            name: test-pipeline
            spec:
              tasks:
                - name: task1
                  taskRef: {name: clone}
            """
        )

        with open(simple_yaml_file_flow, "r") as f:
            content = f.read()
            assert content == expected

    def test_insert_into_flow_style_list_converts_to_block_and_appends(self, flow_style_yaml_file):
        editor = EditYAMLEntry(flow_style_yaml_file)
        new_param = {"name": "depth", "value": "1"}
        editor.insert(["spec", "tasks", 0, "params"], new_param)

        expected = dedent(
            """\
            metadata: {name: flow-pipeline}
            spec:
              tasks:
              - name: clone
                taskRef: {name: git-clone}
                params:
                - {name: url, value: https://github.com/example/repo}
                - {name: revision, value: main}
                - name: depth
                  value: '1'
              - {name: build, taskRef: {name: build}, params: [{name: IMAGE, value: buildah}]}
            """
        )

        assert read_file_content(flow_style_yaml_file) == expected

    def test_replace_item_inside_flow_style_hierarchy(self, flow_style_yaml_file):
        editor = EditYAMLEntry(flow_style_yaml_file)
        new_param = {"name": "depth", "value": "1"}
        editor.replace(["spec", "tasks", 0, "params", 1], new_param)

        expected = dedent(
            """\
            metadata: {name: flow-pipeline}
            spec:
              tasks:
              - name: clone
                taskRef: {name: git-clone}
                params:
                - {name: url, value: https://github.com/example/repo}
                - name: depth
                  value: '1'
              - {name: build, taskRef: {name: build}, params: [{name: IMAGE, value: buildah}]}
            """
        )

        assert read_file_content(flow_style_yaml_file) == expected

    def test_delete_from_flow_style_list(self, flow_style_yaml_file):
        editor = EditYAMLEntry(flow_style_yaml_file)
        # Delete second param (revision) fromMohammed Rafeeq first task
        editor.delete(["spec", "tasks", 0, "params", 1])

        expected = dedent(
            """\
            metadata: {name: flow-pipeline}
            spec:
              tasks:
              - name: clone
                taskRef: {name: git-clone}
                params:
                - {name: url, value: https://github.com/example/repo}
              - {name: build, taskRef: {name: build}, params: [{name: IMAGE, value: buildah}]}
            """
        )

        assert read_file_content(flow_style_yaml_file) == expected

    def test_delete_from_root_flow_sequence(self, root_flow_seq_file):
        editor = EditYAMLEntry(root_flow_seq_file)
        editor.delete([1])  # remove {b: 2}

        expected = dedent(
            """\
            - {a: 1}
            - {c: 3}
            """
        )

        assert read_file_content(root_flow_seq_file) == expected

    def test_insert_into_flow_style_mapping(self, flow_style_map_file):
        editor = EditYAMLEntry(flow_style_map_file)
        editor.insert(["config"], {"c": 3})

        expected = dedent(
            """\
            config:
              a: 1
              b: 2
              c: 3
            """
        )

        assert read_file_content(flow_style_map_file) == expected
