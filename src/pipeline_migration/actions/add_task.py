import argparse
import logging
import re
from argparse import ArgumentTypeError
from collections.abc import Generator
from pathlib import Path
from typing import Any, Final

import requests
from packaging.version import parse as parse_version
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
The following are several examples with a Konflux task push-dockerfile:

* Add task with latest bundle to pipelines within relative .tekton/ directory.

    cd /path/to/repo
    pmt add-task push-dockerfile

* Add task to multiple pipelines in several repositories:

    pmt add-task push-dockerfile \\
        /path/to/repo1/.tekton/pr.yaml /path/to/repo2/.tekton/push.yaml

* Add task with parameter and execution order:

    pmt add-task push-dockerfile \\
        --param param1=value1 --param param2=value2 \\
        --run-after build-image-index

* Add task with specific bundle reference:

    pmt add-task --bundle-ref <bundle-reference> push-dockerfile
"""


class InconsistentBundleBuild(Exception):
    """Registry does not have expected bundle image"""


class KonfluxTaskNotExist(Exception):
    """Konflux task is not found from build-definitions"""


class KonfluxTaskFileNotExist(Exception):
    """Konflux task file does not exist in a version-specific task directory"""


def konflux_task_bundle_reference(value: str) -> str:
    """Argument type for checking input bundle reference

    :raises argparse.ArgumentTypeError: if input bundle reference is invalid.
    """
    build_def = KonfluxBuildDefinitions()
    try:
        build_def.validate_bundle_ref(value)
    except ValueError as e:
        raise ArgumentTypeError(str(e))
    return value


def task_param(value: str) -> tuple[str, str]:
    parts = value.split("=", 1)
    if len(parts) == 1:
        raise ArgumentTypeError("Missing parameter name or value.")
    return parts[0], parts[1]


def register_cli(subparser) -> None:
    add_task_parser = subparser.add_parser(
        "add-task",
        help="Add a Konflux task to build pipelines.",
        description=SUBCMD_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_task_parser.add_argument(
        "task",
        help="Konflux task name. This is the actual task name defined in "
        "konflux-ci/build-definitions. By default, this name is also used as the pipeline task "
        "name. If a trusted artifact task is being added, suffix -oci-ta is removed automatically "
        "from the name and the result is used as the pipeline task name. To specify a pipeline "
        "task name explicitly, use option --pipeline-task-name.",
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
        help="Specify an alternative name for the task configured in the pipeline. If omitted, "
        "name is set according to the given actual task name.",
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
        "-r",
        "--bundle-ref",
        type=konflux_task_bundle_reference,
        metavar="IMAGE_REF",
        dest="bundle_ref",
        help="Use Tekton bundle resolver to reference the expected task bundle. "
        "The full reference has to include both tag and digest, "
        "e.g. registry/org/task-name:tag@digest."
        "If omitted, the latest task bundle is queried from the registry.",
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
        existing_pipeline_task_names = set([])
        existing_actual_task_names = set([])

        for name1, name2 in KonfluxBuildDefinitions.extract_task_names(tasks):
            existing_pipeline_task_names.add(name1)
            existing_actual_task_names.add(name2)

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


def action(args) -> None:
    actual_task_name: Final = args.task
    pipeline_task_name: Final = args.pipeline_task_name or args.task.removesuffix("-oci-ta")

    bundle_ref = args.bundle_ref
    if not bundle_ref:
        bundle_ref = KonfluxBuildDefinitions().query_latest_bundle(actual_task_name)

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


class KonfluxBuildDefinitions:

    DEFINITIONS_REPO: Final = "konflux-ci/build-definitions"
    KONFLUX_IMAGE_ORG: Final = "konflux-ci/tekton-catalog"
    VERSION_REGEX: Final = re.compile(r"^(\d+)\.(\d+)$")

    @staticmethod
    def extract_task_names(tasks: list[dict[str, Any]]) -> Generator[tuple[str, str]]:
        """Extract pipeline task name and actual task name from a task list

        :return: a generator that yields a list of two-elements tuples. The first one is the
            pipeline task name, and the second one is the actual task name.
        """
        for t in tasks:
            pipeline_task_name = t.get("name")
            if not pipeline_task_name:
                logger.warning("Cannot get pipeline task name from %r, skip it:", t)
                continue
            task_ref = t.get("taskRef")
            if not task_ref:
                logger.warning("Task %s does not have taskRef. Skip it.", pipeline_task_name)
                continue
            if task_ref.get("resolver") != "bundles":
                logger.warning("Task %s does not use tekton bundle. Skip it.", pipeline_task_name)
                continue
            actual_task_name = None
            for param in task_ref["params"]:
                if param["name"] == "name":
                    actual_task_name = param["value"]
                    break
            if not actual_task_name:
                logger.warning(
                    "Task %s uses tekton bundle resolver but no actual task name is specified "
                    "in the resolver. Skip it.",
                    pipeline_task_name,
                )
                continue
            yield pipeline_task_name, actual_task_name

    @staticmethod
    def validate_bundle_ref(bundle_ref: str) -> None:
        try:
            c = Container(bundle_ref)
        except ValueError as e:
            # The underlying oras Container.parse raises ValueError
            raise ValueError(f"{bundle_ref} is not a valid image reference: {str(e)}")
        if c.registry != REGISTRY:
            raise ValueError("Currently only support adding Konflux tasks from quay.io.")
        if not c.digest:
            raise ValueError(
                f"missing digest in {bundle_ref}. Task bundle reference must have both "
                "tag and digest."
            )
        if f":{c.tag}@" not in bundle_ref:
            raise ValueError(
                f"missing tag in {bundle_ref}. Task bundle reference must have both tag and digest."
            )
        tag_info = get_active_tag(c, c.tag)
        if tag_info is None:
            raise ValueError(f"tag {c.tag} does not exist in the image repository.")
        digest = tag_info["manifest_digest"]
        if digest != c.digest:
            raise ValueError(f"Mismatch digest. Tag {c.tag} points to a different digest {digest}")

    def determine_latest_version(self, task_name: str) -> str:
        url = f"https://api.github.com/repos/{self.DEFINITIONS_REPO}/contents/task/{task_name}"
        resp = requests.get(url)
        if resp.status_code == 404:
            raise KonfluxTaskNotExist(f"Task {task_name} is not found from build-definitions.")
        resp.raise_for_status()

        def _yield_version():
            for item in resp.json():
                version_str = item["name"]
                if not self.VERSION_REGEX.match(version_str):
                    raise ValueError(f"Malformed version {version_str}")
                yield parse_version(version_str)

        ordered = sorted(_yield_version())
        if not ordered:
            raise ValueError(f"No version is found for task {task_name}.")
        return str(ordered[-1])

    def get_task_latest_commit_sha(self, task_name: str, version: str) -> str:
        url = f"https://api.github.com/repos/{self.DEFINITIONS_REPO}/commits"
        task_file = f"task/{task_name}/{version}/{task_name}.yaml"
        params = {"path": task_file, "per_page": "1"}
        resp = requests.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            raise KonfluxTaskFileNotExist(f"Task file {task_file} does not exist.")
        if "sha" not in data[0]:
            raise ValueError("GitHub API /commits response does not include field sha.")
        return data[0]["sha"]

    def get_digest(self, task_name: str, tag: str) -> str | None:
        image = f"{REGISTRY}/{self.KONFLUX_IMAGE_ORG}/task-{task_name}"
        tag_info = get_active_tag(Container(image), tag)
        return tag_info["manifest_digest"] if tag_info else None

    def query_latest_bundle(self, task_name: str) -> str:
        task_version = self.determine_latest_version(task_name)
        commit_sha = self.get_task_latest_commit_sha(task_name, task_version)
        digest = self.get_digest(task_name, f"{task_version}-{commit_sha}")
        if not digest:
            raise InconsistentBundleBuild(
                f"Konflux image organization {REGISTRY}/{self.KONFLUX_IMAGE_ORG} does not have "
                f"a task bundle built from latest Git commit {commit_sha}"
            )
        return f"{REGISTRY}/{self.KONFLUX_IMAGE_ORG}/task-{task_name}:{task_version}@{digest}"
