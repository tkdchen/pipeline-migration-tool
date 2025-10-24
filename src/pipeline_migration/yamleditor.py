import copy
import contextlib
import os
import tempfile
import textwrap
from pathlib import Path
from collections.abc import Sequence
from typing import Union, TypeAlias, List, Tuple, Any
from io import StringIO

from ruamel.yaml import CommentedMap, CommentedSeq


from pipeline_migration.utils import load_yaml, create_yaml_obj, YAMLStyle, is_flow_style_seq

# YAMLPath type represents path to YAML entity as sequences of strings
# (for dictionaries) and integers (for arrays). Sequence items represent
# indexes of the YAML entries.
# For example, path ["test", 1, "entry"] results into value "one"
# ---
# test:
#   - entry: zero
#   - entry: one
#
YAMLPath: TypeAlias = Sequence[Union[int, str]]
PathStack: TypeAlias = List[Tuple[CommentedSeq | CommentedMap, str | int | None]]

# End of file constant
EOF = -1


class EditYAMLEntry:
    """Provides manipulation interface to YAML files using direct writes
    into YAML file, without regenerating the whole YAML content.
    This allows to do minimal changes to YAML file and keep the YAML
    diff as small as possible and keep custom indentation.

    It supports inserting, replacing and deleting operations.

    Functionality relays on ruamel.yaml parser ability, to provide "lc" line/column
    attribute that points to the exact location of the objects in the YAML file.
    Then exact line number range from to where should be content in file updated is
    decided based on the location of the next element.
    """

    def __init__(self, yaml_file_path: Path, style: YAMLStyle | None = None):
        """
        :param yaml_file_path: path to the yaml file to be modified
        :type yaml_file_path: Path
        :param style: custom yaml style to be used for loading and generating
                yaml files (None is default ruamel.yaml style)
        :type style: YAMLStyle | None
        """
        self.yaml_file_path = yaml_file_path
        self.style = style
        self._data = None

    @property
    def data(self):
        """Loaded yaml data, cached property

        :returns: YAML loaded data"""
        if self._data is None:
            self._data = load_yaml(self.yaml_file_path, self.style)
        return self._data

    @data.deleter
    def data(self):
        self._data = None

    def invalidate_yaml_data(self):
        """Invalidate loaded yaml data.
        After each change to yaml file, data must be invalidated"""
        del self.data

    def _get_path_stack(self, path: YAMLPath, allow_scalar: bool = False) -> PathStack:
        """Get path stack of the given path.
        Each stack item consist of tuple (Node, index), where node is
        yaml entry mapping(dict) or sequence(list) and index is str or int of the child
        element in stack (index None means terminal node)

        :param path: path to the yaml element in the yaml doc
        :type path: YAMLPath
        :param allow_scalar: if True, allows path to point to scalar values (non dict/list)
        :type allow_scalar: bool
        :returns: path stack representing path to each node on the path to the terminal node
        """
        path_stack: PathStack = []
        current_data = self.data
        for p in path:
            assert isinstance(p, (int, str))
            path_stack.append((current_data, p))
            current_data = current_data[p]
        if not isinstance(current_data, (dict, list)):
            if not allow_scalar:
                raise ValueError(
                    f"Path must point to list/dict object. Given path {path} does not."
                )
            # For scalars, we don't add a terminal node - the last item in path_stack
            # already points to the parent and the key/index of the scalar
        else:
            path_stack.append((current_data, None))  # terminal node
        return path_stack

    def insert(self, path: YAMLPath, data: Any):
        """Insert data into mapping or sequence, parent node must be specified as path.

        For scalar values, they can only be inserted into lists (sequences).
        For dict and list values, they can be inserted into either lists or dicts.

        :param path: path in yaml, target object must be list or dict, not a scalar
        :type path: YAMLPath
        :param data: data to be injected into path (can be dict, list, or scalar)
        :type data: Any
        :raises ValueError: if trying to insert a scalar into a dict
        """
        path_stack = self._get_path_stack(path)
        last_node, _ = path_stack[-1]

        # Check if data is a scalar value
        is_scalar = not isinstance(data, (dict, list))

        # Validate insertion rules: scalars can only be inserted into lists
        if is_scalar and not isinstance(last_node, list):
            raise ValueError(
                "Scalar values can only be inserted into lists (sequences). "
                f"The target path points to a {type(last_node).__name__}."
            )

        if is_flow_style_seq(last_node):
            # we must update the parent via replacing
            last_node = copy.deepcopy(last_node)
            last_node.fa.set_block_style()
            if isinstance(last_node, dict):
                last_node.update(data)
            else:
                last_node.append(data)
            return self.replace(path, last_node)

        yaml_str = self._gen_yaml_str(data, last_node.lc.col, seq_block=isinstance(last_node, list))

        # Appending as last item
        lineno = -1  # if sibling doesn't exist, append at the end
        next_entry_line = self._get_next_entry_line(path_stack)
        if next_entry_line is not None:
            # insert before the next entry
            lineno = next_entry_line
        insert_text_at_line(
            self.yaml_file_path, lineno, yaml_str, validation_callback=post_test_yaml_validity
        )
        self.invalidate_yaml_data()

    def replace(self, path: YAMLPath, data: Any):
        """Replace existing sequence, mapping or scalar of the given path with the new data.

        For scalars, the parent object is updated since scalars don't have line numbers.

        :param path: path in yaml, can point to list, dict, or scalar value
        :type path: YAMLPath
        :param data: data to be replaced at path
        :type data: Any
        """
        # try to get path stack, allowing scalars
        path_stack = self._get_path_stack(path, allow_scalar=True)

        # check if we're dealing with a scalar (no terminal node with None)
        is_scalar = path_stack[-1][1] is not None

        if is_scalar:
            # for scalars, we need to update the parent object
            if len(path_stack) < 1:
                raise ValueError("Cannot replace root scalar value")

            parent_node, scalar_key = path_stack[-1]

            # update the parent with the new scalar value
            parent_node = copy.deepcopy(parent_node)  # avoid reusing reference
            parent_node[scalar_key] = data

            # now replace the parent object
            parent_path = path[:-1]
            return self.replace(parent_path, parent_node)

        last_node, _ = path_stack[-1]

        if is_flow_style_seq(last_node):
            path_stack, data = self._pre_process_flow_style_replace(path_stack, data)
            # update last node to use new one
            last_node, _ = path_stack[-1]
            assert isinstance(last_node, (dict, list)) and hasattr(last_node, "lc")

        # replacing at the same position
        lineno = last_node.lc.line

        # ensure we are not at the root element
        col = last_node.lc.col
        seq_block = False
        if len(path_stack) > 1:
            parent_node, _ = path_stack[-2]

            if isinstance(parent_node, list):
                seq_block = True
                # by generating list item, it adds extra '- ', thus col is 2 less
                col = max(0, col - 2)

        yaml_str = self._gen_yaml_str(data, col, seq_block=seq_block)

        # first we need to remove old content, that could be
        # longer or shorter in matter of text lines
        next_entry_line = self._get_next_entry_line(path_stack)
        remove_lines_num = next_entry_line - lineno

        insert_text_at_line(
            self.yaml_file_path,
            lineno,
            yaml_str,
            replace_lines=remove_lines_num,
            validation_callback=post_test_yaml_validity,
        )
        self.invalidate_yaml_data()

    def delete(self, path: YAMLPath):
        """Delete existing sequence, mapping or scalar value of the given path.

        For scalars, the key/index is removed from the parent object.
        Empty items will be deleted by cascade.

        :param path: path in yaml, can point to list, dict, or scalar value
        :type path: YAMLPath
        """
        # try to get path stack, allowing scalars
        path_stack = self._get_path_stack(path, allow_scalar=True)

        # check if we're dealing with a scalar (no terminal node with None)
        is_scalar = path_stack[-1][1] is not None

        if is_scalar:
            # for scalars, we need to delete the key/index from the parent object
            if len(path_stack) < 1:
                raise ValueError("Cannot delete root scalar value")

            parent_node, scalar_key = path_stack[-1]

            # delete the scalar from parent
            parent_node = copy.deepcopy(parent_node)  # avoid reusing reference
            del parent_node[scalar_key]

            # Check if parent is now empty, and if so, cascade delete
            # This is similar to the cascade deletion logic for non-scalar values
            parent_path = path[:-1]

            # If parent is now empty, we should delete the parent instead
            if len(parent_node) == 0 and len(parent_path) > 0:
                # Parent became empty, delete it instead (cascade)
                return self.delete(parent_path)

            # Parent is not empty, just replace it
            return self.replace(parent_path, parent_node)

        path_stack = self._get_path_stack(path)

        # if the entry is the only item of parent, remove also the parent
        while len(path_stack) > 1:
            parent_node, _ = path_stack[-2]
            if len(parent_node) > 1:
                break
            path_stack.pop()
        # drop terminal item, only terminal item can be None
        path = [p for _, p in path_stack[:-1]]  # type: ignore

        last_node, _ = path_stack[-1]

        if is_flow_style_seq(last_node):
            # we must update the parent via replacing
            if len(path_stack) > 1:
                parent_node, parent_index = path_stack[-2]
                path = path[:-1]  # instead of deleting item replace content of the parent
                data = copy.deepcopy(parent_node)
                data.fa.set_block_style()

                del data[parent_index]
            else:
                # removing root node ?
                data = {}

            return self.replace(path, data)

        # removing from the node position
        lineno = last_node.lc.line

        # in case of preceding empty lines or comments, we have to remove them as well
        if last_node.ca.comment:
            # getting first empty line/comment
            lineno = last_node.ca.comment[1][0].start_mark.line

        if self._is_parent_dict(path_stack):
            # to also remove dict key, we have to do -1 in lineno
            lineno = max(lineno - 1, 0)

        # remove old content till next element
        next_entry_line = self._get_next_entry_line(path_stack)
        remove_lines_num = next_entry_line - lineno
        remove_lines_from_file(
            self.yaml_file_path,
            lineno,
            remove_lines_num,
            validation_callback=post_test_yaml_validity,
        )
        self.invalidate_yaml_data()

    def _is_parent_dict(self, path_stack):
        if len(path_stack) > 1:
            parent, _ = path_stack[-2]
            return isinstance(parent, dict)
        return False

    def _get_next_entry_line(self, path_stack) -> int | None:
        """Find lineno where the next item in yaml starts.

        Method looks for sibling item, if sibling doesn't exist
        recursively check sibling of the parent.

        IMPORTANT: this function works only with block style, make sure that
        path stack points to the block style

        :returns: line where next item in yaml file begins. When
                None is returned it's EOF (end of file), it's the
                last item in the yaml file
        :rtype: int | None
        """
        path_stack = copy.copy(path_stack)

        def find_next_sibling(node, index):
            if isinstance(node, list):
                assert isinstance(index, int)
                if len(node) - 1 > index:
                    return node[index + 1]  # sibling is just next item in array
            elif isinstance(node, dict):

                assert isinstance(index, str)
                # we rely on python dict feature that ordering is kept
                keys = tuple(node.keys())
                key_idx = keys.index(index)
                if len(keys) - 1 > key_idx:
                    return node[keys[key_idx + 1]]

            # other types cannot be used to find siblings
            # sibling doesn't exist
            return None

        while path_stack:
            current = path_stack.pop()
            node, index = current
            if index is None:
                # terminal element, we cannot find sibling from this level
                continue
            sibling = find_next_sibling(node, index)
            if sibling is None:
                # sibling doesn't exist, continue to next level
                continue

            line = sibling.lc.line

            # if (parent) node is dict, ruamel reports line+1 for key, get the real position
            if isinstance(node, dict):
                line = max(line - 1, 0)

            return line

        return EOF

    def _gen_yaml_str(self, data: Any, col: int, seq_block=False) -> str:
        if seq_block:
            data = [data]
        yaml = create_yaml_obj(style=self.style)
        stream = StringIO()
        yaml.dump(
            data,
            stream=stream,
        )
        yaml_output = stream.getvalue()

        # with different yaml styles depending on exact yaml doc, ruamel.yaml may generate various
        # indentation; we need to normalize it by dedent so col num can be used to do exact
        # indentation as we need
        # Note: we want to generate as close as possible to original doc style, so we cannot use
        # our own style
        dedented_yaml_output = textwrap.dedent(yaml_output)

        # Indent each line of the YAML output by the column position
        indented = textwrap.indent(dedented_yaml_output, " " * col)
        return indented

    def _pre_process_flow_style_replace(
        self, path_stack, data: Any
    ) -> Tuple[PathStack, dict | list]:
        """Flow style isn't fully supported, to comply with it, we will just use the yaml parser
        to generate  the whole block since the first block style parent entry.
        If first block is doc root, then everything will be regenerated.

        Returns new path_stack to be used and data; If no change is needed,
        original path_stack and data are returned

        :param path_stack: current path stack pointing to the desired object
        :type path_stack: PathStack
        :param data: current data for replacement
        :type data: Any
        :returns: tuple that contains new path stack and new data to be used in replacement
        """
        if not path_stack:
            return path_stack, data

        if len(path_stack) < 2:
            # no parent?
            return path_stack, data

        node, _ = path_stack[-1]
        if not is_flow_style_seq(node):
            return path_stack, data

        # it's FLOW STYLE, yay!!
        # update data first, we will use existing data to regenerate everything
        # regenerate in BLOCK STYLE for future

        # filthy data could sneak flow style into yaml
        # reduce it by one level at least for future
        if hasattr(data, "fa"):
            data.fa.set_block_style()

        path_stack = copy.copy(path_stack)

        # update nodes with new data
        # find first non-flow style parent, and replace it with block data
        # as we cannot reliably update flow style data
        while len(path_stack) > 1:
            node, _ = path_stack[-1]
            if not is_flow_style_seq(node):
                break
            parent, parent_idx = path_stack[-2]
            path_stack.pop()
            new_parent = copy.deepcopy(parent)
            new_parent[parent_idx] = data
            new_parent.fa.set_block_style()
            data = new_parent

        # mark new last node as terminal
        node, _ = path_stack.pop()
        path_stack.append((node, None))

        return path_stack, data


def post_test_yaml_validity(path):
    """Validate if update yaml is valid

    Given how this tool operates, it may happen that generated YAML isn't valid.
    Rather fail early than provide false positive success.
    """
    try:
        load_yaml(path)
    except Exception as e:
        raise RuntimeError("post-check: generated YAML is not valid") from e


def remove_lines_from_file(
    file_path: Path, start_line: int, num_lines: int, validation_callback=None
) -> None:
    """
    Remove a block of text from a file without loading the entire file into memory.

    :param file_path: Path to the file to modify
    :type file_path: Path:
    :param start_line: Line number where removal should start (0-indexed)
    :type start_line: int
    :param num_lines: Number of lines to remove (negative value mean till EOF)
    :type num_lines: int

    :raises FileNotFoundError: If the file doesn't exist
    :raises ValueError: If start_line or num_lines are invalid
    :raises IOError: If there's an error reading or writing the file
    """
    if start_line < 0:
        raise ValueError("start_line must be >= 0")

    if num_lines == 0:
        return  # Nothing to remove

    # start_line is already 0-indexed
    start_index = start_line

    end_index = start_index + num_lines
    if num_lines < 0:  # till EOF
        end_index = -1

    # Create a temporary file in the same directory as the original
    temp_dir = os.path.dirname(file_path) or "."
    temp_fd, temp_path = tempfile.mkstemp(dir=temp_dir, text=True)

    try:
        with (
            os.fdopen(temp_fd, "w", encoding="utf-8") as temp_file,
            open(file_path, "r", encoding="utf-8") as original_file,
        ):
            current_line = 0

            for line in original_file:
                # Copy lines before the removal range
                if current_line < start_index:
                    temp_file.write(line)
                # Skip lines in the removal range
                elif current_line < end_index or end_index < 0:  # till EOF
                    pass  # Skip this line
                # Copy lines after the removal range
                else:
                    temp_file.write(line)

                current_line += 1

            # Check if start_line was beyond the file length
            if start_index >= current_line:
                raise ValueError(
                    f"start_line ({start_line}) is beyond the file "
                    f"length (max index: {current_line - 1})"
                )

        if validation_callback is not None:
            validation_callback(temp_path)

        # Atomically replace the original file with the temporary file
        os.replace(temp_path, file_path)

    finally:
        # Clean up temporary file
        with contextlib.suppress(OSError):
            os.unlink(temp_path)


def insert_text_at_line(
    file_path: Path,
    line_number: int,
    text_to_insert: str,
    replace_lines: int = 0,
    validation_callback=None,
) -> None:
    """
    Insert or replace multiline text at a specified line number.

    :param file_path: Path to the file to modify
    :type file_path: Path
    :param line_number: Line number where text should be inserted (0-indexed).
                        Negative number means to append at the end of file.
    :type line_number: int
    :param text_to_insert: Text to insert or replace with (can be multiline)
    :type text_to_insert: str
    :param replace_lines: If positive value is defined replace
                       a number of lines equal to the specified value.
                       Negative value means to replace till EOF.
                       (Default 0, no replacing)
    :type replace_lines: int

    :raises FileNotFoundError: If the file doesn't exist
    :raises ValueError: If line_number is invalid
    :raises IOError: If there's an error reading or writing the file
    """
    # line_number is already 0-indexed
    insert_index = line_number

    # Ensure text_to_insert ends with newline if it doesn't already
    if text_to_insert is not None and not text_to_insert.endswith("\n"):
        text_to_insert += "\n"

    # Create a temporary file in the same directory as the original
    temp_dir = os.path.dirname(file_path) or "."
    temp_fd, temp_path = tempfile.mkstemp(dir=temp_dir, text=True)

    try:
        with (
            os.fdopen(temp_fd, "w", encoding="utf-8") as temp_file,
            open(file_path, "r", encoding="utf-8") as original_file,
        ):
            current_line = 0
            replacing_lines_in_progress = 0

            for line in original_file:
                if replacing_lines_in_progress > 0:
                    replacing_lines_in_progress -= 1
                    current_line += 1
                    continue

                if current_line == insert_index:
                    # Write the new text
                    temp_file.write(text_to_insert)
                    if replace_lines > 0:
                        # one line is removed as part of this (with continue statement)
                        replacing_lines_in_progress = replace_lines - 1
                        current_line += 1
                        continue
                    elif replace_lines < 0:
                        # replacing till EOF
                        current_line += 1  # increase counter before break, so we don't insert again
                        break

                    temp_file.write(line)
                else:
                    # Copy the original line
                    temp_file.write(line)

                current_line += 1

            # Handle case where line_number is beyond file length or insert_index is negative
            # to append at the end file
            if insert_index < 0 or current_line <= insert_index:
                temp_file.write(text_to_insert)

        if validation_callback is not None:
            validation_callback(temp_path)

        # Atomically replace the original file with the temporary file
        os.replace(temp_path, file_path)

    finally:
        # Clean up temporary file
        with contextlib.suppress(OSError):
            os.unlink(temp_path)
