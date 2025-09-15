import argparse
import copy
import logging
from pathlib import Path
from typing import Any, Final

from ruamel.yaml.comments import CommentedSeq, CommentedMap

from pipeline_migration.yamleditor import EditYAMLEntry, YAMLPath
from pipeline_migration.types import FilePath
from pipeline_migration.utils import YAMLStyle, create_yaml_obj
from pipeline_migration.pipeline import PipelineFileOperation, iterate_files_or_dirs


logger = logging.getLogger("modify.generic")


SUBCMD_DESCRIPTION: Final = """\

Subcommanad "generic" requires path within the YAML doc in "yq" path function style,
where an operation should be executed.

YAML path is list of indexes in YAML format.
For example:
- spec
- tasks
- 5

It can be also written on singleline in YAML flow format: '["spec", "tasks", 5]'.

The following are several examples with a raw yaml modification:

* Modify an yaml item within relative .tekton/ directory.

    cd /path/to/repo
    pmt modify generic insert '["path", "to", "yaml", "item"]' '{"new": "item"}'

* Modify an yaml item in multiple pipelines in several repositories:

    pmt modify \\
        -f /path/to/repo1/.tekton/pr.yaml -f /path/to/repo2/.tekton/push.yaml \\
        generic replace \\
        '["path", "to", "yaml", "item", 3]' '{"replaced": "new"}'

* Remove a task using yq's `path` function:

   pmt modify \\
        -f .tekton/pr.yaml \\
        generic remove \\
        "$(yq '.spec.pipelineSpec.tasks[] | select(.name == "prefetch-dependencies") | \\
            path' .tekton/pr.yaml)"

WARNING: generic subcommand should be used as the last resort subcommand, it doesn't do any
semantic validation for Konflux tasks.
Use resource specific subcommands if they are available instead to have a proper validation.
"""


class YAMLPathNotFoundError(Exception):
    """Exception when given path doesn't exist in the YAML doc"""


def _yaml_path_from_param(yaml_path_param: str) -> YAMLPath:
    """Parses and validates yaml_path parameter and returns YAMLPath variable type"""
    yaml = create_yaml_obj()
    loaded_path_params = yaml.load(yaml_path_param)

    yaml_path: YAMLPath = []

    if not isinstance(loaded_path_params, list):
        raise ValueError("Provided YAML path must be a sequence")

    for item in loaded_path_params:
        if not isinstance(item, (str, int)):
            raise ValueError(
                "Provided YAML path sequence must contain only string or integer values"
            )
        yaml_path.append(item)

    return yaml_path


def yaml_path_type(param: str) -> YAMLPath:
    "Argparser custom type for yaml path validation"
    try:
        yaml_path = _yaml_path_from_param(param)
    except Exception as e:
        raise argparse.ArgumentTypeError(str(e))
    else:
        return yaml_path


def _yaml_from_value_param(value: str) -> dict | list:
    """Parses and validates value param"""

    def make_block_style_yaml(y):
        """Recursively updates"""
        if not hasattr(y, "fa"):
            # scalar node, nothing to do
            return

        y.fa.set_block_style()

        if isinstance(y, dict):
            for item in y.values():
                make_block_style_yaml(item)
        if isinstance(y, list):
            for item in y:
                make_block_style_yaml(item)

    yaml = create_yaml_obj()
    loaded_value = yaml.load(value)

    if not isinstance(loaded_value, (list, dict)):
        raise ValueError("Value parameter must be YAML sequence or map")

    make_block_style_yaml(loaded_value)

    return loaded_value


def yaml_value_type(param: str) -> dict | list:
    "Argparser custom type for yaml value validation"
    try:
        yaml_path = _yaml_from_value_param(param)
    except Exception as e:
        raise argparse.ArgumentTypeError(str(e))
    else:
        return yaml_path


def register_cli(subparser) -> None:
    mod_generic_parser = subparser.add_parser(
        "generic",
        help=(
            "Generic modification of YAML file (specific resource subcommands should "
            "be preferred)"
        ),
        description=SUBCMD_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparser_mod = mod_generic_parser.add_subparsers(
        title="subcommands to generic modifications", required=True
    )

    # insert
    subparser_insert = subparser_mod.add_parser(
        "insert",
        help="Inserts item into YAML path",
    )
    subparser_insert.add_argument(
        "yaml_path",
        help=(
            "YAML path (in YAML format). Must point to a sequence or a map item. "
            "It's the same path as returned by yq's path function (list of indexes)"
        ),
        metavar="YAML-PATH",
        type=yaml_path_type,
    )
    subparser_insert.add_argument(
        "value",
        help="YAML sequence or map (in YAML format) to be inserted)",
        metavar="VALUE",
        type=yaml_value_type,
    )

    subparser_insert.set_defaults(action=action_insert)

    # replace
    subparser_replace = subparser_mod.add_parser(
        "replace",
        help="Replaces item at given YAML path",
    )
    subparser_replace.add_argument(
        "yaml_path",
        help=(
            "YAML path (in YAML format). Must point to a sequence or a map item. "
            "It's the same path as returned by yq's path function (list of indexes)"
        ),
        metavar="YAML-PATH",
        type=yaml_path_type,
    )
    subparser_replace.add_argument(
        "value",
        help="YAML sequence or map (in YAML format) to be used as the replacement)",
        metavar="VALUE",
        type=yaml_value_type,
    )

    subparser_replace.set_defaults(action=action_replace)

    # remove
    subparser_remove = subparser_mod.add_parser(
        "remove",
        help="Removes item at given YAML path",
    )
    subparser_remove.add_argument(
        "yaml_path",
        help=(
            "YAML path (in YAML format). Must point to a sequence or a map item. "
            "It's the same path as returned by yq's path function (list of indexes)"
        ),
        metavar="YAML-PATH",
        type=yaml_path_type,
    )

    subparser_remove.set_defaults(action=action_remove)


class ModGenericBase(PipelineFileOperation):
    """Base class for generic resource modifications"""

    def __init__(self, yaml_path: YAMLPath):
        self.yaml_path = yaml_path

    def validate_yaml_path(self, loaded_doc: Any):
        def get_path_doc(ypath):
            """:raises KeyError: when path doesn't exist"""
            tmp_doc = copy.copy(loaded_doc)
            for p in ypath:
                tmp_doc = tmp_doc[p]
            return tmp_doc

        try:
            tmp_doc = get_path_doc(self.yaml_path)
        except KeyError:
            raise YAMLPathNotFoundError(
                f"Given YAML path {self.yaml_path} doesn't exist in the doc"
            )
        else:
            if not isinstance(tmp_doc, (CommentedSeq, CommentedMap)):
                raise RuntimeError(
                    f"Provided YAML path {self.yaml_path} must point to sequence or map"
                )

    def handle_pipeline_run_file(
        self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle
    ) -> None:
        # the same implementation as pipeline
        self.handle_pipeline_file(file_path, loaded_doc, style)


class ModGenericInsert(ModGenericBase):
    def __init__(self, yaml_path: YAMLPath, value: dict | list):
        super().__init__(yaml_path)
        self.value = value

    def handle_pipeline_file(self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle) -> None:
        logger.info("Inserting content into YAML path %s in file %s", self.yaml_path, file_path)
        self.validate_yaml_path(loaded_doc)
        yamledit = EditYAMLEntry(file_path, style=style)
        yamledit.insert(self.yaml_path, self.value)


def action_insert(args) -> None:
    search_places = [path for path in args.file_or_dir if path]
    relative_tekton_dir = Path("./.tekton")
    if not search_places and relative_tekton_dir.exists():
        search_places = [str(relative_tekton_dir.absolute())]

    op = ModGenericInsert(args.yaml_path, args.value)
    for file_path in iterate_files_or_dirs(search_places):
        try:
            op.handle(str(file_path))
        except YAMLPathNotFoundError as e:
            logger.warning("Skipped file %s update: %s", file_path, e)


class ModGenericReplace(ModGenericBase):
    def __init__(self, yaml_path: YAMLPath, value: dict | list):
        super().__init__(yaml_path)
        self.value = value

    def handle_pipeline_file(self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle) -> None:
        logger.info("Replacing content at YAML path %s in file %s", self.yaml_path, file_path)
        self.validate_yaml_path(loaded_doc)
        yamledit = EditYAMLEntry(file_path, style=style)
        yamledit.replace(self.yaml_path, self.value)


def action_replace(args) -> None:
    search_places = [path for path in args.file_or_dir if path]
    relative_tekton_dir = Path("./.tekton")
    if not search_places and relative_tekton_dir.exists():
        search_places = [str(relative_tekton_dir.absolute())]

    op = ModGenericReplace(args.yaml_path, args.value)
    for file_path in iterate_files_or_dirs(search_places):
        try:
            op.handle(str(file_path))
        except YAMLPathNotFoundError as e:
            logger.warning("Skipped file %s update: %s", file_path, e)


class ModGenericRemove(ModGenericBase):
    def __init__(self, yaml_path: YAMLPath):
        super().__init__(yaml_path)

    def handle_pipeline_file(self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle) -> None:
        logger.info("Removing YAML path %s in file %s", self.yaml_path, file_path)
        self.validate_yaml_path(loaded_doc)
        yamledit = EditYAMLEntry(file_path, style=style)
        yamledit.delete(self.yaml_path)


def action_remove(args) -> None:
    search_places = [path for path in args.file_or_dir if path]
    relative_tekton_dir = Path("./.tekton")
    if not search_places and relative_tekton_dir.exists():
        search_places = [str(relative_tekton_dir.absolute())]

    op = ModGenericRemove(args.yaml_path)
    for file_path in iterate_files_or_dirs(search_places):
        try:
            op.handle(str(file_path))
        except YAMLPathNotFoundError as e:
            logger.warning("Skipped file %s update: %s", file_path, e)
