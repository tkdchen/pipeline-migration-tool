from collections.abc import Generator
from typing import Any

from pipeline_migration.actions.migrate.constants import (
    ANNOTATION_HAS_MIGRATION,
    ANNOTATION_PREVIOUS_MIGRATION_BUNDLE,
    logger,
)
from pipeline_migration.actions.migrate.models import (
    TaskBundleMigration,
    TaskBundleUpgrade,
)
from pipeline_migration.actions.migrate.main import (
    fetch_migration_file,
)
from pipeline_migration.actions.migrate.resolvers import Resolver
from pipeline_migration.quay import QuayTagInfo
from pipeline_migration.registry import Container, Registry
from pipeline_migration.utils import is_true


class LinkedMigrationsResolver(Resolver):
    """Resolve linked migrations via bundle image annotation"""

    def _resolve_migrations(
        self, bundle_upgrade: TaskBundleUpgrade, upgrades_range: list[QuayTagInfo]
    ) -> Generator[TaskBundleMigration, Any, None]:
        """Resolve migrations by links represented by annotation"""
        dep_name = bundle_upgrade.dep_name

        if not upgrades_range:
            logger.info("Upgrade range is empty for %s. Skip resolving migrations.", dep_name)
            return

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
