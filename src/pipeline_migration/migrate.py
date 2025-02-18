import logging
import os.path
import re
import subprocess as sp
import tempfile

from abc import ABC, abstractmethod
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Final, Any

from pipeline_migration.utils import dump_yaml, load_yaml
from pipeline_migration.registry import Container, Registry, ImageIndex
from pipeline_migration.quay import QuayTagInfo, list_active_repo_tags
from pipeline_migration.utils import is_true
from pipeline_migration.types import FilePath

ANNOTATION_HAS_MIGRATION: Final[str] = "dev.konflux-ci.task.has-migration"
ANNOTATION_IS_MIGRATION: Final[str] = "dev.konflux-ci.task.is-migration"
ANNOTATION_PREVIOUS_MIGRATION_BUNDLE: Final[str] = "dev.konflux-ci.task.previous-migration-bundle"

TEKTON_KIND_PIPELINE: Final = "Pipeline"
TEKTON_KIND_PIPELINE_RUN: Final = "PipelineRun"
ANNOTATION_TRUTH_VALUE: Final = "true"

# Example:  0.1-18a61693389c6c912df587f31bc3b4cc53eb0d5b
TASK_TAG_REGEXP: Final = r"^[0-9.]+-[0-9a-f]+$"
DIGEST_REGEXP: Final = r"sha256:[0-9a-f]+"

logger = logging.getLogger("migrate")


@dataclass
class TaskBundleMigration:
    # A complete image reference with both tag and digest
    task_bundle: str
    # Content of the script
    migration_script: str


@dataclass
class TaskBundleUpgrade:
    dep_name: str  # Renovate template field: depName
    current_value: str  # Renovate template field: currentValue. It is the image tag.
    current_digest: str  # Renovate template field: currentDigest
    new_value: str  # Renovate template field: newValue. It is the image tag.
    new_digest: str  # Renovate template field: newDigest

    migrations: list[TaskBundleMigration] = field(default_factory=list)

    @property
    def current_bundle(self) -> str:
        return f"{self.dep_name}:{self.current_value}@{self.current_digest}"

    @property
    def new_bundle(self) -> str:
        return f"{self.dep_name}:{self.new_value}@{self.new_digest}"

    @property
    def comes_from_konflux(self) -> bool:
        if os.environ.get("PMT_LOCAL_TEST"):
            logger.warning(
                "Environment variable PMT_LOCAL_TEST is set. Migration tool works with images "
                "from arbitrary registry organization."
            )
            return True
        return self.dep_name.startswith("quay.io/konflux-ci/")

    def __post_init__(self) -> None:
        if not self.dep_name:
            raise InvalidRenovateUpgradesData("Image name is empty.")
        if not self.current_value and not self.current_digest:
            raise InvalidRenovateUpgradesData("Both currentValue and currentDigest are empty.")
        if not self.new_value and not self.new_digest:
            raise InvalidRenovateUpgradesData("Both newValue and newDigest are empty.")
        if self.current_value == self.new_value and self.current_digest == self.new_digest:
            raise InvalidRenovateUpgradesData("Current and new task bundle are same.")

        regex = re.compile(DIGEST_REGEXP)
        if not regex.fullmatch(self.current_digest):
            raise InvalidRenovateUpgradesData("Current digest is not a valid digest string.")
        if not regex.fullmatch(self.new_digest):
            raise InvalidRenovateUpgradesData("New digest is not a valid digest string.")


@dataclass
class PackageFile:
    file_path: str  # Renovate template field packageFile
    parent_dir: str  # Renovate template field parentDir

    task_bundle_upgrades: list[TaskBundleUpgrade] = field(default_factory=list)


class InvalidRenovateUpgradesData(ValueError):
    """Raise this error if any required data is missing in the given Renovate upgrades"""


@contextmanager
def resolve_pipeline(pipeline_file: FilePath) -> Generator[FilePath, Any, None]:
    """Yield resolved pipeline file

    :param pipeline_file:
    :type pipeline_file: str
    :return: a generator yielding a file containing the pipeline definition.
    """
    origin_pipeline = load_yaml(pipeline_file)
    if not isinstance(origin_pipeline, dict):
        raise ValueError(f"Given file {pipeline_file} is not a YAML mapping.")

    kind = origin_pipeline.get("kind")
    if kind == TEKTON_KIND_PIPELINE:
        yield pipeline_file
        pl_yaml = load_yaml(pipeline_file)
        dump_yaml(pipeline_file, pl_yaml)
    elif kind == TEKTON_KIND_PIPELINE_RUN:
        spec = origin_pipeline.get("spec") or {}
        if "pipelineSpec" in spec:
            # pipeline definition is inline the PipelineRun
            fd, temp_pipeline_file = tempfile.mkstemp(suffix="-pipeline")
            os.close(fd)
            pipeline = {"spec": spec["pipelineSpec"]}
            dump_yaml(temp_pipeline_file, pipeline)
            yield temp_pipeline_file
            modified_pipeline = load_yaml(temp_pipeline_file)
            spec["pipelineSpec"] = modified_pipeline["spec"]
            dump_yaml(pipeline_file, origin_pipeline)
        elif "pipelineRef" in spec:
            # Pipeline definition can be referenced here, via either git-resolver or a name field
            # pointing to YAML file under the .tekton/.
            # In this case, Renovate should not handle the given file as a package file since
            # there is no task bundle references.
            raise ValueError("PipelineRun definition seems not embedded.")
        else:
            raise ValueError(
                "PipelineRun .spec field includes neither .pipelineSpec nor .pipelineRef field."
            )
    else:
        raise ValueError(
            f"Given file {pipeline_file} does not have knownn kind Pipeline or PipelineRun."
        )


# TODO: cache this as well?
def determine_task_bundle_upgrades_range(
    task_bundle_upgrade: TaskBundleUpgrade,
) -> list[QuayTagInfo]:
    """Determine task bundles range between given two task bundles

    The determined range consists of task bundles [new task bundle ... current task bundle].

    Each element inside the upgrades range is the raw tag information mapping
    responded from Quay.io registry, and the range is in the same order as the tags responded
    (newest to oldest).
    """

    r: list[QuayTagInfo] = []
    in_range = False
    has_tag = False
    task_tag_re = re.compile(TASK_TAG_REGEXP)

    current_bundle = task_bundle_upgrade.current_bundle
    new_bundle = task_bundle_upgrade.new_bundle

    c = Container(task_bundle_upgrade.dep_name)
    for tag in list_active_repo_tags(c):
        quay_tag = QuayTagInfo(name=tag["name"], manifest_digest=tag["manifest_digest"])
        has_tag = True
        if not task_tag_re.match(quay_tag.name):
            continue
        if quay_tag.manifest_digest == task_bundle_upgrade.new_digest:
            r.append(quay_tag)
            in_range = True
        elif quay_tag.manifest_digest == task_bundle_upgrade.current_digest:
            if not in_range:
                raise ValueError(f"New task bundle {new_bundle} has not been present.")
            return r
        elif in_range:
            r.append(quay_tag)

    if not has_tag:
        return r

    raise ValueError(
        f"Neither old task bundle {current_bundle} nor newer task bundle {new_bundle}"
        " is present in the registry."
    )


class TaskBundleUpgradesManager:

    def __init__(self, upgrades: list[dict[str, Any]], resolver_class: type["Resolver"]) -> None:
        # Deduplicated task bundle upgrades. Key is the full bundle image with tag and digest.
        self._task_bundle_upgrades: dict[str, TaskBundleUpgrade] = {}

        # Grouped task bundle upgrades by package file. Key is the package file path.
        # One package file may have the more than one task bundle upgrades, that reference the
        # objects in the ``_task_bundle_upgrades``.
        self._package_file_updates: dict[str, PackageFile] = {}

        self._resolver = resolver_class()

        self._collect(upgrades)

    @property
    def package_files(self) -> list[PackageFile]:
        return list(self._package_file_updates.values())

    def _collect(self, upgrades: list[dict[str, Any]]) -> None:
        for upgrade in upgrades:
            task_bundle_upgrade = TaskBundleUpgrade(
                dep_name=upgrade["depName"],
                current_value=upgrade["currentValue"],
                current_digest=upgrade["currentDigest"],
                new_value=upgrade["newValue"],
                new_digest=upgrade["newDigest"],
            )
            package_file = PackageFile(
                file_path=upgrade["packageFile"],
                parent_dir=upgrade["parentDir"],
            )

            if "tekton-bundle" not in upgrade["depTypes"]:
                logger.debug(
                    "Dependency %s is not handled by tekton-bundle manager.",
                    task_bundle_upgrade.dep_name,
                )
                continue

            if not task_bundle_upgrade.comes_from_konflux:
                logger.info(
                    "Dependency %s does not come from Konflux task definitions.",
                    task_bundle_upgrade.dep_name,
                )
                continue

            tb_update = self._task_bundle_upgrades.get(task_bundle_upgrade.current_bundle)
            if tb_update is None:
                self._task_bundle_upgrades[task_bundle_upgrade.current_bundle] = task_bundle_upgrade
                tb_update = task_bundle_upgrade

            pf = self._package_file_updates.get(package_file.file_path)
            if pf is None:
                self._package_file_updates[package_file.file_path] = package_file
                pf = package_file
            pf.task_bundle_upgrades.append(tb_update)

    def resolve_migrations(self) -> None:
        """Resolve migrations for given task bundle upgrades"""
        self._resolver.resolve(list(self._task_bundle_upgrades.values()))

    @staticmethod
    def _apply_migration(pipeline_file: FilePath, migration: TaskBundleMigration) -> None:
        if not os.path.exists(pipeline_file):
            raise ValueError(f"Pipeline file does not exist: {pipeline_file}")

        logger.info(
            "Apply migration of task bundle %s in package file %s",
            migration.task_bundle,
            pipeline_file,
        )

        fd, migration_file = tempfile.mkstemp()
        try:
            os.write(fd, migration.migration_script.encode("utf-8"))
        finally:
            os.close(fd)

        with resolve_pipeline(pipeline_file) as file_path:
            logger.info("Executing migration script %s on %s", migration_file, file_path)
            cmd = ["bash", migration_file, file_path]
            logger.debug("Run: %r", cmd)
            try:
                proc = sp.run(cmd, stderr=sp.STDOUT, stdout=sp.PIPE)
                logger.debug("%r", proc.stdout)
                proc.check_returncode()
            finally:
                os.unlink(migration_file)

    def apply_migrations(self) -> None:
        for package_file in self._package_file_updates.values():
            for task_bundle_upgrade in package_file.task_bundle_upgrades:
                for migration in task_bundle_upgrade.migrations:
                    self._apply_migration(package_file.file_path, migration)


class IncorrectMigrationAttachment(Exception):
    pass


def fetch_migration_file(image: str, digest: str) -> str | None:
    """Fetch migration file for a task bundle

    :param image: image name of a task bundle without tag or image.
    :type image: str
    :param digest: digest of the task bundle.
    :type digest: str
    :return: the migration file content. If migration file can't be found for the given task
        bundle, None is returned.
    """
    c = Container(image)
    if c.digest:
        raise ValueError("Image should not include digest.")
    c.digest = digest
    registry = Registry()

    # query and fetch migration file via referrers API
    image_index = ImageIndex(data=registry.list_referrers(c, "text/x-shellscript"))
    descriptors = [
        descriptor
        for descriptor in image_index.manifests
        if is_true(descriptor.annotations.get(ANNOTATION_IS_MIGRATION, "false"))
    ]
    if len(descriptors) > 1:
        msg = (
            f"{len(descriptors)} referrers containing migration script are listed. "
            "However, there should be one per task bundle."
        )
        logger.warning(msg)
        raise IncorrectMigrationAttachment(msg)
    if descriptors:
        c.digest = descriptors[0].digest
        manifest = registry.get_manifest(c)
        descriptor = manifest["layers"][0]
        return registry.get_artifact(c, descriptor["digest"])
    return None


def migrate(upgrades: list[dict[str, Any]], migration_resolver: type["Resolver"]) -> None:
    """The core method doing the migrations

    :param upgrades: upgrades data, that follows the schema of Renovate template field ``upgrades``.
    :type upgrades: list[dict[str, any]]
    """
    manager = TaskBundleUpgradesManager(upgrades, migration_resolver)
    manager.resolve_migrations()
    manager.apply_migrations()


class Resolver(ABC):
    """Base class for resolving migrations"""

    @abstractmethod
    def _resolve_migrations(
        self, dep_name: str, upgrades_range: list[QuayTagInfo]
    ) -> Generator[TaskBundleMigration, Any, None]:
        raise NotImplementedError

    def resolve(self, tb_upgrades: list[TaskBundleUpgrade]) -> None:

        def _resolve(tb_upgrade: TaskBundleUpgrade) -> None:
            upgrades_range = determine_task_bundle_upgrades_range(tb_upgrade)
            for tb_migration in self._resolve_migrations(tb_upgrade.dep_name, upgrades_range):
                tb_upgrade.migrations.append(tb_migration)
            # Quay.io lists tags from the newest to the oldest one.
            # Migrations must be applied in the reverse order.
            tb_upgrade.migrations.reverse()

        with ThreadPoolExecutor() as executor:
            futures = [executor.submit(_resolve, tb_upgrade) for tb_upgrade in tb_upgrades]
            for future in as_completed(futures):
                future.result()


class SimpleIterationResolver(Resolver):
    """Legacy resolution by checking individual task bundle within an upgrade"""

    def _resolve_migrations(
        self, dep_name: str, upgrades_range: list[QuayTagInfo]
    ) -> Generator[TaskBundleMigration, Any, None]:
        for tag_info in upgrades_range:
            c = Container(f"{dep_name}:{tag_info.name}@{tag_info.manifest_digest}")
            uri_with_tag = c.uri_with_tag

            manifest_json = Registry().get_manifest(c)
            if not is_true(
                manifest_json.get("annotations", {}).get(ANNOTATION_HAS_MIGRATION, "false")
            ):
                continue

            script_content = fetch_migration_file(dep_name, tag_info.manifest_digest)
            if script_content:
                logger.info("Task bundle %s has migration.", uri_with_tag)
                yield TaskBundleMigration(task_bundle=uri_with_tag, migration_script=script_content)
            else:
                logger.info("Task bundle %s does not have migration.", uri_with_tag)


class LinkedMigrationsResolver(Resolver):
    """Resolve linked migrations via bundle image annotation"""

    def _resolve_migrations(
        self, dep_name: str, upgrades_range: list[QuayTagInfo]
    ) -> Generator[TaskBundleMigration, Any, None]:
        manifest_digests = [tag.manifest_digest for tag in upgrades_range]
        i = 0
        while True:
            tag_info = upgrades_range[i]
            c = Container(f"{dep_name}:{tag_info.name}@{tag_info.manifest_digest}")
            uri_with_tag = c.uri_with_tag

            manifest_json = Registry().get_manifest(c)
            has_migration = manifest_json.get("annotations", {}).get(
                ANNOTATION_HAS_MIGRATION, "false"
            )

            if is_true(has_migration):
                script_content = fetch_migration_file(dep_name, tag_info.manifest_digest)
                if script_content:
                    logger.info("Task bundle %s has migration.", uri_with_tag)
                    yield TaskBundleMigration(
                        task_bundle=uri_with_tag, migration_script=script_content
                    )
                else:
                    logger.info("Task bundle %s does not have migration.", uri_with_tag)

            digest = manifest_json.get("annotations", {}).get(
                ANNOTATION_PREVIOUS_MIGRATION_BUNDLE, ""
            )
            if digest:
                try:
                    i = manifest_digests.index(digest)
                except ValueError:
                    logger.info(
                        "Migration search stops at %s. It points to a previous migration bundle %s "
                        "that is before the current upgrade.",
                        c.uri_with_tag,
                        digest,
                    )
                    break
            else:
                logger.info("Migration search stops at %s", c.uri_with_tag)
                break
