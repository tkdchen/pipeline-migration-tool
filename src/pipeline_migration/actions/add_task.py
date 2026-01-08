import argparse
import logging
from argparse import ArgumentTypeError
from pathlib import Path
from typing import Any, Final

from ruamel.yaml.comments import CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from pipeline_migration.quay import get_active_tag
from pipeline_migration.registry import REGISTRY, Container
from pipeline_migration.types import FilePath
from pipeline_migration.pipeline import PipelineFileOperation, iterate_files_or_dirs
from pipeline_migration.yamleditor import EditYAMLEntry
from pipeline_migration.utils import YAMLStyle, git_add

logger = logging.getLogger("add_task")

SUBCMD_DESCRIPTION: Final = """\
The following are several examples of adding a task.

* Add task using a tag (digest is resolved automatically only for quay.io):

    pmt add-task quay.io/konflux-ci/konflux-vanguard/task-push-dockerfile:0.1

* Add task using a full bundle reference (tag and digest validated):

    pmt add-task quay.io/konflux-ci/konflux-vanguard/task-push-dockerfile:0.1@sha256:...

* Add task to multiple pipelines:

    pmt add-task quay.io/konflux-ci/konflux-vanguard/task-push-dockerfile:0.1@sha256:... \\
        /path/to/repo1/.tekton/pr.yaml /path/to/repo2/.tekton/push.yaml

* Add task with parameter and execution order:

    pmt add-task quay.io/konflux-ci/konflux-vanguard/task-push-dockerfile:0.1@sha256:... \\
        .tekton/pr.yaml .tekton/push.yaml \\
        --param param1=value1 --param param2=value2 \\
        --run-after build-image-index
"""


def validate_bundle_ref(bundle_ref: str) -> str:
    """
    Validates and resolves the bundle reference.

    - For Quay.io (REGISTRY): Validates the tag against the API.
      If digest is missing, it resolves and appends it.
    - For other registries: Strictly requires a full reference (Tag + Digest).

    :param str bundle_ref: Bundle reference, either with just a tag or a full one (Tag + Digest)
    :return: The fully resolved bundle reference (including digest).
    :rtype: str
    """
    try:
        c = Container(bundle_ref)
    except ValueError as e:
        # The underlying oras Container.parse raises ValueError
        raise ValueError(f"{bundle_ref} is not a valid image reference: {str(e)}")

    if f":{c.tag}" not in bundle_ref:
        raise ValueError(f"missing tag in {bundle_ref}. Task bundle reference must have a tag.")

    if c.registry == REGISTRY:
        tag_info = get_active_tag(c, c.tag)
        if tag_info is None:
            raise ValueError(f"tag {c.tag} does not exist in the image repository.")

        active_digest = tag_info["manifest_digest"]

        if c.digest:
            if active_digest != c.digest:
                raise ValueError(
                    f"Mismatch digest. Tag {c.tag} points to a different digest {active_digest}"
                )
            return bundle_ref
        else:
            return f"{bundle_ref}@{active_digest}"
    else:
        # we cannot use Quay API to validate or resolve these,
        # so we force the user to provide the full immutable reference.
        if not c.digest:
            raise ValueError(
                f"missing digest in {bundle_ref}. For non-Quay registries, "
                "task bundle reference must have both tag and digest."
            )
        if f":{c.tag}@" not in bundle_ref:
            raise ValueError(
                f"missing tag in {bundle_ref}. Task bundle reference must have both tag and digest."
            )
        return bundle_ref


def get_task_bundle_reference(value: str) -> str:
    """Argument type for checking and resolving input bundle reference

    :raises argparse.ArgumentTypeError: if input bundle reference is invalid.
    """
    try:
        resolved_value = validate_bundle_ref(value)
    except ValueError as e:
        raise ArgumentTypeError(str(e))
    return resolved_value


def task_param(value: str) -> tuple[str, str]:
    parts = value.split("=", 1)
    if len(parts) == 1:
        raise ArgumentTypeError("Missing parameter name or value.")
    return parts[0], parts[1]


def register_cli(subparser) -> None:
    add_task_parser = subparser.add_parser(
        "add-task",
        help="Add a task to build pipelines using a bundle reference.",
        description=SUBCMD_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_task_parser.add_argument(
        "bundle_ref",
        type=get_task_bundle_reference,
        help="The Tekton bundle reference. "
        "For quay.io, providing just a tag is supported (the digest is resolved automatically). "
        "For other registries, a full reference (registry/org/repo:tag@digest) is required. "
        "The pipeline task name is automatically derived from the bundle's repository name "
        "(e.g., 'quay.io/.../task-check' becomes 'task-check'). "
        "If the name ends in '-oci-ta', the suffix is removed. "
        "To specify a pipeline task name explicitly, use option --pipeline-task-name.",
    )
    add_task_parser.add_argument(
        "file_or_dir",
        nargs="*",
        help="Specify locations from where finding out pipelines to add task. "
        "A pipeline can be included in a PipelineRun or a single Pipeline definition. "
        "%(prog)s searches pipelines from given locations by rules, if files are specified, "
        "search just pipelines from them. If directories are specified, search YAML files from the "
        "first level of each one. If neither is specified, the location defaults to ./.tekton/ "
        "directory.",
    )
    add_task_parser.add_argument(
        "-n",
        "--pipeline-task-name",
        metavar="NAME",
        help="Specify an alternative name for the task configured in the pipeline. "
        "If omitted, name is derived from the bundle repository name."
        "For example, from 'quay.io/konflux-ci/task-sast-coverity-check:0.1@sha256:...' "
        "the name will be 'task-sast-coverity-check'.",
    )
    add_task_parser.add_argument(
        "-a",
        "--run-after",
        metavar="TASK_NAME",
        dest="run_after",
        action="append",
        help="Name of task running before the adding task. "
        "This is the task name used in the build pipeline definition. "
        "This argument can be specified multiple times to add more than one tasks.",
    )
    add_task_parser.add_argument(
        "-p",
        "--param",
        type=task_param,
        metavar="PARAM",
        dest="params",
        action="append",
        help="Specify a task parameter that consists of comma-separated name and value. "
        "This argument can be specified multiple times to add more than one parameters.",
    )
    add_task_parser.add_argument(
        "-s",
        "--skip-checks",
        action="store_true",
        dest="skip_checks",
        help="Skip this task if it can be skipped as a check for a fast build.",
    )
    add_task_parser.add_argument(
        "-g",
        "--git-add",
        dest="git_add",
        action="store_true",
        help="Add the modified files to git index.",
    )
    add_task_parser.add_argument(
        "-f",
        "--add-to-finally",
        action="store_true",
        dest="add_to_finally",
        help="Add the task to the 'finally' section instead of the 'tasks' section.",
    )
    add_task_parser.set_defaults(action=action)


class AddTaskOperation(PipelineFileOperation):

    def __init__(
        self,
        task_config: dict,
        pipeline_task_name: str,
        actual_task_name: str,
        git_add: bool = False,
        add_to_finally: bool = False,
    ) -> None:
        self.task_config = task_config
        self.pipeline_task_name = pipeline_task_name
        self.actual_task_name = actual_task_name
        self.git_add = git_add
        self.add_to_finally = add_to_finally

    def handle_pipeline_file(self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle) -> None:
        yaml_path, tasks = self._resolve_path_and_task_list(["spec"], loaded_doc)
        self._handle_pipeline_files(yaml_path, tasks, file_path, style, loaded_doc)

    def handle_pipeline_run_file(
        self, file_path: FilePath, loaded_doc: Any, style: YAMLStyle
    ) -> None:
        yaml_path, tasks = self._resolve_path_and_task_list(["spec", "pipelineSpec"], loaded_doc)
        self._handle_pipeline_files(yaml_path, tasks, file_path, style, loaded_doc)

    def _handle_pipeline_files(
        self,
        yaml_path: list[str],
        tasks: Any,
        file_path: FilePath,
        style: YAMLStyle,
        loaded_doc: Any,
    ) -> None:
        if not self._should_add_task(tasks, str(file_path)):
            return None

        yamledit = EditYAMLEntry(file_path, style=style)
        insert_path, insert_data = self._get_insertion_location_and_data(yaml_path, loaded_doc)
        yamledit.insert(insert_path, insert_data)

        if self.git_add:
            git_add(file_path)
            logger.info("%s is added to git index.", file_path)

    def _resolve_path_and_task_list(
        self,
        yaml_path: list[str],
        loaded_doc: Any,
    ) -> tuple[list[str], CommentedSeq]:
        """
        Identify whether to use the 'tasks' or 'finally' list and
        retrieve its path and current content.

        :param list[str] yaml_path: The path to the pipeline section.
        :param Any loaded_doc: The loaded YAML document structure.
        :return: A tuple containing the updated YAML path and the task list content.
        :rtype: tuple[list[str], CommentedSeq]
        """
        section = "tasks"
        if self.add_to_finally:
            section = "finally"

        yaml_path.append(section)

        for pipeline_section in yaml_path:
            loaded_doc = loaded_doc.get(pipeline_section, [])

        return yaml_path, loaded_doc

    def _get_insertion_location_and_data(
        self, yaml_path: list[str], loaded_doc: Any
    ) -> tuple[list[str], dict]:
        """
        Resolve the correct insertion path and data payload.

        Walks the `yaml_path` within `loaded_doc`:
        - If the full path exists, it returns the full `yaml_path` and the
          task config.
        - If a key ('tasks' or 'finally') is missing, it
          returns the path *to its parent* and a new `dict` containing the
          missing key and the new task list.

        :param list[str] yaml_path: The desired path to the task list.
        :param Any loaded_doc: The loaded document structure.
        :return: A tuple of (insert_path, insert_data_payload).
        :rtype: tuple[list[str], dict]
        """
        current = loaded_doc
        existing_path: list[str] = []

        for key in yaml_path:
            if key not in current:
                section_name = key
                task_list = CommentedSeq([self.task_config])

                return existing_path, {section_name: task_list}

            existing_path.append(key)
            current = current[key]

        return yaml_path, self.task_config

    def _should_add_task(self, tasks: CommentedSeq, pipeline_file: str) -> bool:
        """Check if task should be added and log appropriate messages.

        Returns True if task should be added, False otherwise.
        """
        existing_pipeline_task_names, existing_actual_task_names = extract_task_names(tasks)

        if (depended_tasks := self.task_config.get("runAfter")) is not None:
            for name in depended_tasks:
                if name not in existing_pipeline_task_names:
                    raise ValueError(
                        f"Task {name} does not exist in the pipeline definition {pipeline_file}."
                    )

        if self.pipeline_task_name in existing_pipeline_task_names:
            logger.info(
                "Task %s is included in pipeline %s already.",
                self.pipeline_task_name,
                pipeline_file,
            )
            return False

        if self.actual_task_name in existing_actual_task_names:
            logger.info(
                "Task %s is being referenced in pipeline %s already.",
                self.actual_task_name,
                pipeline_file,
            )
            return False

        if (
            self.pipeline_task_name in existing_actual_task_names
            or self.actual_task_name in existing_pipeline_task_names
        ):
            logger.warning(
                "The pipeline task name and actual task name seem swapped. Skip adding task."
            )
            return False

        logger.info("Task %s will be added to pipeline %s", self.actual_task_name, pipeline_file)
        return True


def extract_task_names(tasks: CommentedSeq) -> tuple[set[str], set[str]]:
    """
    Extract sets of pipeline task names and actual task names from a task list.

    :param CommentedSeq tasks: The list of tasks to extract names from.
    :return: A tuple of (pipeline_names, actual_names).
    :rtype: tuple[set[str], set[str]]
    """
    pipeline_names = set()
    actual_names = set()

    for t in tasks:
        p_name = t.get("name")
        if p_name:
            pipeline_names.add(p_name)
        else:
            logger.warning("Cannot get pipeline task name from %r, skip it.", t)
            continue

        task_ref = t.get("taskRef")
        if not task_ref:
            logger.warning("Task %s does not have taskRef. Skip it.", p_name)
            continue

        if task_ref.get("resolver") == "bundles":
            found_actual_name = False
            for param in task_ref.get("params", []):
                if param["name"] == "name":
                    actual_names.add(param["value"])
                    found_actual_name = True
                    break

            if not found_actual_name:
                logger.warning(
                    "Task %s uses tekton bundle resolver but no actual task name is specified "
                    "in the resolver.",
                    p_name,
                )

    return pipeline_names, actual_names


def action(args) -> None:
    bundle_ref: str = args.bundle_ref

    container = Container(bundle_ref)

    actual_task_name = container.repository.split("/")[-1]
    pipeline_task_name = args.pipeline_task_name or actual_task_name.removesuffix("-oci-ta")

    logger.info("Adding task %s, bundle %s", actual_task_name, bundle_ref)

    task_config = {
        "name": pipeline_task_name,
        "taskRef": {
            "resolver": "bundles",
            "params": [
                {"name": "kind", "value": "task"},
                {"name": "name", "value": actual_task_name},
                {"name": "bundle", "value": bundle_ref},
            ],
        },
    }
    if args.params:
        task_config["params"] = [{"name": name, "value": value} for name, value in args.params]
    if args.run_after:
        task_config["runAfter"] = args.run_after
    if args.skip_checks:
        task_config["when"] = [
            {
                "input": "$(params.skip-checks)",
                "operator": "in",
                "values": [DoubleQuotedScalarString("false")],
            }
        ]

    search_places = [path for path in args.file_or_dir if path]
    relative_tekton_dir = Path("./.tekton")
    if not search_places and relative_tekton_dir.exists():
        search_places = [str(relative_tekton_dir.absolute())]

    op = AddTaskOperation(
        task_config,
        pipeline_task_name,
        actual_task_name,
        git_add=args.git_add,
        add_to_finally=args.add_to_finally,
    )
    for file_path in iterate_files_or_dirs(search_places):
        op.handle(str(file_path))
