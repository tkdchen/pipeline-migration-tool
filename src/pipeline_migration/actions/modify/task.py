from abc import abstractmethod
import argparse
import copy
from enum import Enum
import logging
from pathlib import Path
from typing import Any, Final, List

from ruamel.yaml.comments import CommentedSeq

from pipeline_migration.yamleditor import EditYAMLEntry, YAMLPath
from pipeline_migration.types import FilePath
from pipeline_migration.utils import YAMLStyle
from pipeline_migration.pipeline import PipelineFileOperation, iterate_files_or_dirs


logger = logging.getLogger("modify.task")


SUBCMD_DESCRIPTION: Final = """\
The following are several examples with a Konflux task push-dockerfile:

* Modify a task within relative .tekton/ directory.

    cd /path/to/repo
    pmt modify task push-dockerfile add-param new-param new-value

* Modify task in multiple pipelines in several repositories:

    pmt modify \\
        -f /path/to/repo1/.tekton/pr.yaml -f /path/to/repo2/.tekton/push.yaml \\
        task push-dockerfile \\
        add-param new-param new-value

* Add array of values.

    cd /path/to/repo
    pmt modify task push-dockerfile add-param -t array new-param new-value1 new-value2

    Note: if the param name exist current values will be replaced, not appended

* Supported task modifications:
   - add-param: adds a new param to the task (or updates existing)
   - remove-param: removes the specified param from the task
"""


class ParamType(Enum):
    string = "string"
    array = "array"

    def __str__(self):
        return self.value


class TaskNotFoundError(Exception):
    """Task of the given name not found"""


class TaskBase(PipelineFileOperation):
    """Base class for task handling"""

    def __init__(self, task_name):
        super().__init__()
        self.task_name = task_name

    @abstractmethod
    def _do_action(
        self, tasks: CommentedSeq, path_prefix: YAMLPath, pipeline_file: FilePath, style: YAMLStyle
    ):
        """Method where the real YAML change is happening"""
        raise NotImplementedError

    def handle_pipeline_file(self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle) -> None:
        yaml_paths = [
            ["spec", "tasks"],
            ["spec", "finally"],
        ]
        self._handle_paths(yaml_paths, file_path, loaded_doc, style)

    def handle_pipeline_run_file(
        self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle
    ) -> None:
        yaml_paths = [
            ["spec", "pipelineSpec", "tasks"],
            ["spec", "pipelineSpec", "finally"],
        ]
        self._handle_paths(yaml_paths, file_path, loaded_doc, style)

    def _handle_paths(
        self, yaml_paths: List[List[str]], file_path: FilePath, loaded_doc: Any, style: YAMLStyle
    ):
        not_found_task = [False] * len(yaml_paths)

        def get_path_doc(ypath):
            """:raises KeyError: when path doesn't exist"""
            tmp_doc = copy.copy(loaded_doc)
            for p in ypath:
                tmp_doc = tmp_doc[p]
            return tmp_doc

        for index, yaml_path in enumerate(yaml_paths):
            # check if path exist
            try:
                tmp_doc = get_path_doc(yaml_path)
            except KeyError:
                not_found_task[index] = True
                continue

            try:
                self._do_action(tmp_doc, yaml_path, file_path, style)
            except TaskNotFoundError:
                not_found_task[index] = True

        if all(not_found_task):
            logger.warning(
                "task '%s' does not exist in '%s'",
                self.task_name,
                file_path,
            )


def register_cli(subparser) -> None:
    mod_task_parser = subparser.add_parser(
        "task",
        help="Update the specified Konflux task",
        description=SUBCMD_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    mod_task_parser.add_argument(
        "task_name",
        metavar="TASK-NAME",
        help="Pipeline task name in pipeline/pipeline run YAML file.",
    )
    subparser_mod = mod_task_parser.add_subparsers(
        title="subcommands to modify task", required=True
    )

    # add-param
    subparser_add_param = subparser_mod.add_parser(
        "add-param",
        help="Add the specified parameter to a task. If parameter already exists, "
        "it updates the value.",
    )
    subparser_add_param.add_argument("param_name", help="parameter name", metavar="PARAM-NAME")
    subparser_add_param.add_argument(
        "param_value", nargs="+", help="parameter values", metavar="PARAM-VALUE"
    )
    subparser_add_param.add_argument(
        "-t",
        "--type",
        dest="param_type",
        help="parameter type (Default: %(default)s)",
        type=ParamType,
        choices=list(ParamType),
        default=ParamType.string,
    )
    subparser_add_param.set_defaults(action=action_add_param)

    # remove-param
    subparser_remove_param = subparser_mod.add_parser(
        "remove-param",
        help="Remove the specified parameter from a task.",
    )
    subparser_remove_param.add_argument("param_name", help="parameter name", metavar="PARAM-NAME")

    subparser_remove_param.set_defaults(action=action_remove_param)


class ModTaskAddParamOperation(TaskBase):
    def __init__(
        self,
        task_name: str,
        param_name: str,
        param_value: str | List[str],
    ) -> None:
        super().__init__(task_name)
        self.param_name = param_name
        self.param_value = param_value

    def _do_action(
        self, tasks: CommentedSeq, path_prefix: YAMLPath, pipeline_file: FilePath, style: YAMLStyle
    ) -> bool:
        """Private function that adds parameter into task if needed (or create the whole params
        section if missing)

        If parameter with the same value exist, this is a no-op

        Given the tasks are located in different locations in pipeline VS pipelineRun objects,
        we need path_prefix consisting of path to the tasks in yaml
        """
        path = path_prefix
        task_found = False
        for index, task in enumerate(tasks):
            if task.get("name", "") != self.task_name:
                continue

            task_found = True
            path.append(index)

            # When params section doesn't exist
            if "params" not in task:
                new_data_with_parent = {
                    "params": [{"name": self.param_name, "value": self.param_value}]
                }
                logger.info(
                    (
                        "task '%s' in '%s': param '%s' will be created (params attribute "
                        "will be created)"
                    ),
                    self.task_name,
                    pipeline_file,
                    self.param_name,
                )
                yamledit = EditYAMLEntry(pipeline_file, style=style)
                yamledit.insert(path, new_data_with_parent)
                return True

            path.append("params")
            for index_param, param in enumerate(task["params"]):
                if param["name"] == self.param_name:
                    path.append(index_param)
                    if (
                        param["value"] is None
                        or (
                            isinstance(self.param_value, str) and param["value"] != self.param_value
                        )
                        or (
                            # assume that order of params doesn't matter
                            set(self.param_value)
                            != set(param["value"])
                        )
                    ):
                        param["value"] = self.param_value
                        logger.info(
                            "task '%s' in '%s': param '%s' will be updated",
                            self.task_name,
                            pipeline_file,
                            self.param_name,
                        )
                        yamledit = EditYAMLEntry(pipeline_file)
                        yamledit.replace(path, param)
                        return True

                    logger.info(
                        "task '%s' in '%s': param '%s' already has required values",
                        self.task_name,
                        pipeline_file,
                        self.param_name,
                    )
                    return False  # param task found and doesn't need replacement

            # param name doesn't exist
            new_data = {"name": self.param_name, "value": self.param_value}
            logger.info(
                "task '%s' in '%s': param '%s' will be created",
                self.task_name,
                pipeline_file,
                self.param_name,
            )
            yamledit = EditYAMLEntry(pipeline_file)
            yamledit.insert(path, new_data)
            return True

        if not task_found:
            raise TaskNotFoundError

        return False


def action_add_param(args) -> None:
    value = args.param_value
    if args.param_type == ParamType.string:
        if len(value) > 1:

            raise RuntimeError("Param value must be only one item with string type")
        value = value[0]  # extract value when type is string

    search_places = [path for path in args.file_or_dir if path]
    relative_tekton_dir = Path("./.tekton")
    if not search_places and relative_tekton_dir.exists():
        search_places = [str(relative_tekton_dir.absolute())]

    op = ModTaskAddParamOperation(args.task_name, args.param_name, value)
    for file_path in iterate_files_or_dirs(search_places):
        op.handle(str(file_path))


class ModTaskRemoveParamOperation(TaskBase):
    def __init__(
        self,
        task_name: str,
        param_name: str,
    ) -> None:
        super().__init__(task_name)
        self.task_name = task_name
        self.param_name = param_name

    def _do_action(
        self, tasks: CommentedSeq, path_prefix: YAMLPath, pipeline_file: FilePath, style: YAMLStyle
    ) -> bool:
        """Private function that removes parameter from task if needed

        If parameter with the same name doesn't exist, this is a no-op

        Given the tasks are located in different locations in pipeline VS pipelineRun objects,
        we need path_prefix consisting of path to the tasks in yaml
        """
        path = path_prefix
        task_found = False
        for index, task in enumerate(tasks):
            if task.get("name", "") != self.task_name:
                continue

            task_found = True
            path.append(index)

            # When params section doesn't exist
            if "params" not in task:
                logger.info(
                    "task '%s' in '%s': param '%s' does not exist, nothing to remove",
                    self.task_name,
                    pipeline_file,
                    self.param_name,
                )
                return False  # nothing to do

            path.append("params")
            for index_param, param in enumerate(task["params"]):
                if param["name"] == self.param_name:
                    path.append(index_param)
                    logger.info(
                        "task '%s' in '%s': param '%s' will be removed",
                        self.task_name,
                        pipeline_file,
                        self.param_name,
                    )
                    yamledit = EditYAMLEntry(pipeline_file, style=style)
                    yamledit.delete(path)
                    return True

            return False  # param doesn't exist, nothing to do

        if not task_found:
            raise TaskNotFoundError

        return False


def action_remove_param(args) -> None:
    search_places = [path for path in args.file_or_dir if path]
    relative_tekton_dir = Path("./.tekton")
    if not search_places and relative_tekton_dir.exists():
        search_places = [str(relative_tekton_dir.absolute())]

    op = ModTaskRemoveParamOperation(args.task_name, args.param_name)
    for file_path in iterate_files_or_dirs(search_places):
        op.handle(str(file_path))
