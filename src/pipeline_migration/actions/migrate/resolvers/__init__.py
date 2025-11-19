from abc import ABC, abstractmethod
from itertools import takewhile
import operator
import re
from pipeline_migration.actions.migrate.constants import TASK_TAG_REGEXP, logger
from pipeline_migration.actions.migrate.exceptions import MigrationResolveError
from pipeline_migration.actions.migrate.models import (
    TaskBundleMigration,
    TaskBundleUpgrade,
)
from pipeline_migration.quay import QuayTagInfo, list_active_repo_tags
from collections.abc import Generator, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Final
from packaging.version import Version, parse as parse_version, InvalidVersion

from pipeline_migration.registry import Container


class Resolver(ABC):
    """Base class for resolving migrations"""

    @abstractmethod
    def _resolve_migrations(
        self, bundle_upgrade: TaskBundleUpgrade, upgrades_range: list[QuayTagInfo]
    ) -> Generator[TaskBundleMigration, Any, None]:
        """Resolve migrations for a bundle upgrade

        :param bundle_upgrade: instance of TaskBundleUpgrade.
        :param upgrades_range: a list of Quay tag mappings from which to discover migrations.
        :type upgrades_range: list[QuayTagInfo]
        :return: a generator yielding migrations represented by a TaskBundleMigration instances.
        """
        raise NotImplementedError("Must be implemented in a subclass.")

    def _resolve_task(self, bundle_upgrade: TaskBundleUpgrade) -> None:
        """Task to resolve migrations for a specific bundle upgrade

        :param bundle_upgrade: an instance of TaskBundleUpgrade.
        :type bundle_upgrade: TaskBundleUpgrade
        """
        upgrades_range = determine_task_bundle_upgrades_range(bundle_upgrade)
        for tb_migration in self._resolve_migrations(bundle_upgrade, upgrades_range):
            bundle_upgrade.migrations.append(tb_migration)
        # Quay.io lists tags from the newest to the oldest one.
        # Migrations must be applied in the reverse order.
        bundle_upgrade.migrations.reverse()

    def resolve_single_upgrade(self, bundle_upgrade: TaskBundleUpgrade) -> None:
        """This is used by resolver proxy"""
        return self._resolve_task(bundle_upgrade)

    def resolve(self, tb_upgrades: list[TaskBundleUpgrade]) -> None:
        """Resolve migrations for given task bundles upgrades

        Depending on the implementation of ``_resolve_migrations`` in subclasses, migrations are
        resolved from remote, i.e. Quay.io, and put into the ``TaskBundleUpgrade.migrations`` in
        place. This method ensures the migrations are in order from oldest to newest.

        :raises: ExceptionGroup[MigrationResolveError]. Any error happening during resolving
            migration for a specific upgrade is captured. Then, all such errors are grouped
            into an ``ExceptionGroup`` instance.
        """
        errors: list[Exception] = []

        with ThreadPoolExecutor() as executor:
            futures = {
                executor.submit(self._resolve_task, tb_upgrade): tb_upgrade
                for tb_upgrade in tb_upgrades
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    bundle_upgrade = futures[future]
                    err_msg = (
                        "Error occurs when resolving migration for upgrade "
                        f"from {bundle_upgrade.current_bundle} to {bundle_upgrade.new_bundle}"
                    )
                    logger.error(err_msg)
                    errors.append(MigrationResolveError(f"{err_msg}: {str(e)}", bundle_upgrade, e))

        if errors:
            raise ExceptionGroup("Migration resolve errors", errors)


def drop_out_of_order_versions(
    tags_info: Iterable[dict], bundle_upgrade: TaskBundleUpgrade
) -> tuple[list[dict], dict | None, dict | None, bool]:
    """Drop version tags that are out of order.

    Once a new version is bumped for a task, there should be no reason to attach
    migrations to the older version of the task. That means we can ignore "out of order"
    versions.

    For example, if we have these tags (ordered from newest to oldest by creation date):

        ["0.3-b", "0.2-b", "0.3-a", "0.1-b", "0.2-a", "0.1-a"]

    Then we only want to look at these:

        ["0.3-b", "0.3-a", "0.2-a", "0.1-a"]

    Because 0.2-b and 0.1-b are out of order - when they were created, a newer version
    tag already existed.

    :param tags_info: tags information responded by Quay.io listRepoTags endpoint.
        Each tag mapping must have a tag name pinned by version and revision, for
        example, ``0.2-<commit hash>``.
    :param bundle_upgrade: a ``TaskBundleUpgrade`` instance assisting on getting more information
        from the tags.
    :type bundle_upgrade: TaskBundleUpgrade
    :return: a 4-elements tuple. The first one is a list of tags cleaned up by dropping the
        out-of-order bundles. If input ``tags_info`` is empty, the result will be empty too. The
        second one references the current tag. The third one references the new tag. The last one
        indicates whether the current tag is out-of-order.
    """
    tags_that_follow_correct_version_order = []
    highest_version_so_far: Version | None = None
    is_out_of_order = False

    current_tag_info = None
    new_tag_info = None
    current_digest = bundle_upgrade.current_digest
    new_digest = bundle_upgrade.new_digest

    def _parse_version(tag_name: str) -> Version | None:
        try:
            return parse_version(tag_name.split("-")[0])
        except InvalidVersion:
            logger.warning(
                "Skipping tag '%s' with invalid version format. "
                "Expected semantic version format: 'X.Y.Z-<hash>' (e.g., '1.0.0-abc123')",
                tag_name,
            )
            return None

    for tag in reversed(list(tags_info)):
        version = _parse_version(tag["name"])
        if version is None:
            continue

        if current_tag_info is None and tag["manifest_digest"] == current_digest:
            current_tag_info = tag
            if highest_version_so_far and version < highest_version_so_far:
                is_out_of_order = True
        elif new_tag_info is None and tag["manifest_digest"] == new_digest:
            new_tag_info = tag
        if highest_version_so_far is None or version >= highest_version_so_far:
            tags_that_follow_correct_version_order.append(tag)
            highest_version_so_far = version

    sort_key = operator.itemgetter("start_ts")
    tags_that_follow_correct_version_order.sort(key=sort_key, reverse=True)
    return tags_that_follow_correct_version_order, current_tag_info, new_tag_info, is_out_of_order


def only_tags_pinned_by_version_revision(tags_info: Iterable[dict]) -> Generator[dict, Any, None]:
    regex = re.compile(TASK_TAG_REGEXP)
    for tag_info in tags_info:
        if regex.match(tag_info["name"]):
            yield tag_info


def expand_versions(from_: str, to: str) -> list[str]:
    """Expand versions

    Example:

        from 0.2 to 0.2 => ["0.2"]
        from 0.2 to 0.3 => ["0.2", "0.3"]
        from 0.2 to 0.5 => ["0.2", "0.3", "0.4", "0.5"]

    This expansion only works with the version management based on the minor version.
    """
    from_version = parse_version(from_)
    to_version = parse_version(to)

    if from_version > to_version:
        logger.warning(
            "From version %s is greater than the to version %s. Returning empty version list.",
            from_,
            to,
        )
        return []
    return [f"0.{minor}" for minor in range(int(from_version.minor), int(to_version.minor) + 1)]


def list_bundle_tags(bundle_upgrade: TaskBundleUpgrade) -> list[dict]:
    versions = expand_versions(bundle_upgrade.current_value, bundle_upgrade.new_value)
    tags: list[dict] = []
    c = Container(bundle_upgrade.dep_name)
    for version in versions:
        iter_tags = list_active_repo_tags(c, tag_name_pattern=f"{version}-")
        try:
            first_tag = next(iter_tags)
        except StopIteration:
            logger.info("No tag is queried from registry for version %s", version)
            continue
        tags.append(first_tag)
        tags.extend(iter_tags)
    return sorted(tags, key=operator.itemgetter("start_ts"), reverse=True)


# TODO: cache this as well?
def determine_task_bundle_upgrades_range(
    task_bundle_upgrade: TaskBundleUpgrade,
) -> list[QuayTagInfo]:
    """Determine upgrade range for a given bundle upgrade

    The upgrade range is a collection of tags pointing from the new bundle to the previous one of
    current bundle. This method handles several senariors against the tag scheme:

    * This method aims to work well with the tag scheme pushed by build-definitions CI pipeline.
      The expected tag form is ``<version>-<commit hash>``.
    * Ideally, the bundles should be built linearly version by version. However, out-of-order
      bundles started to present in bundle repositories, for example, old version task is built
      because of deprecation.
    * Transitioning to decentralized build-definitions. Some tasks have been decentralized and
      already have new tag scheme in their image repositories. Part of the repositories have single
      tag scheme, whereas others mixes two.

      The pure new tag scheme looks like (from newest to oldest):

        3.0
        sha256-123456
        sha256-345678

      Similarly, the mixed tag schemes looks like:

        3.0
        sha256-123456
        sha256-345678
        0.2
        0.2-revision_1
        0.2-revision_2

    As of writing this docstring, upgrade range is still determined based on the original tag scheme
    made by build-definitions CI pipeline, and this method tries best to not fail when possibly
    encounter the new tag scheme. For detailed information of the result range, refer to the below
    description.

    IMPORTANT: current implementation is not intended as a solution for addressing the decentralized
    task bundles.

    :param task_bundle_upgrade: a ``TaskBundleUpgrade`` instance providing upgrade information for
        the determination.
    :type task_bundle_upgrade: TaskBundleUpgrade
    :return: a list of ``QuayTagInfo`` instances representing the upgrade range. The current bundle
        is not included in the result range. Empty list is returned if either tag pointing to the
        current bundle or the one point to the new bundle is not retrieved from registry. Once it
        happens, it could either mean the input upgrade data is invalid or the new tag scheme is
        encountered.
    :rtype: list[QuayTagInfo]
    """
    result = drop_out_of_order_versions(
        only_tags_pinned_by_version_revision(list_bundle_tags(task_bundle_upgrade)),
        task_bundle_upgrade,
    )
    tags_info, current_tag_info, new_tag_info, is_out_of_order = result

    current_bundle_ref: Final = task_bundle_upgrade.current_bundle
    new_bundle_ref: Final = task_bundle_upgrade.new_bundle

    if current_tag_info is None:
        logger.warning("Registry does not have current bundle %s", current_bundle_ref)
        return []

    if new_tag_info is None:
        logger.warning("Registry does not have new bundle %s", new_bundle_ref)
        return []

    current_pos = new_pos = -1
    current_digest = task_bundle_upgrade.current_digest
    new_digest = task_bundle_upgrade.new_digest
    for i, tag in enumerate(tags_info):
        this_digest = tag["manifest_digest"]
        if this_digest == new_digest:
            new_pos = i
        elif this_digest == current_digest:
            current_pos = i

    the_range: Iterable[dict]

    if is_out_of_order:
        # This current bundle has been filtered out previously
        logger.info(
            "Current bundle %s is newer than new bundle %s", current_bundle_ref, new_bundle_ref
        )
        current_version = current_tag_info["name"].split("-")[0]
        the_range = takewhile(lambda item: item["name"].split("-")[0] != current_version, tags_info)
    else:
        the_range = tags_info[new_pos:current_pos]
    return [QuayTagInfo.from_tag_info(item) for item in the_range]


class NoopResolver(Resolver):
    """A resolver doing nothing"""

    def _resolve_migrations(
        self, bundle_upgrade: TaskBundleUpgrade, upgrades_range: list[QuayTagInfo]
    ) -> Generator[TaskBundleMigration, Any, None]:
        yield TaskBundleMigration(task_bundle="", migration_script="")
