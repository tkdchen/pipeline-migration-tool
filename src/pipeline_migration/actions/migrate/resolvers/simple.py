from collections.abc import Generator
from typing import Any

from pipeline_migration.actions.migrate.constants import ANNOTATION_HAS_MIGRATION, logger
from pipeline_migration.actions.migrate.main import (
    fetch_migration_file,
)
from pipeline_migration.actions.migrate.models import TaskBundleMigration, TaskBundleUpgrade
from pipeline_migration.actions.migrate.resolvers import Resolver
from pipeline_migration.quay import QuayTagInfo
from pipeline_migration.registry import Container, Registry
from pipeline_migration.utils import is_true


class SimpleIterationResolver(Resolver):
    """Legacy resolution by checking individual task bundle within an upgrade"""

    def _resolve_migrations(
        self, bundle_upgrade: TaskBundleUpgrade, upgrades_range: list[QuayTagInfo]
    ) -> Generator[TaskBundleMigration, Any, None]:
        """Resolve migration of individual task bundle one-by-one through the upgrade range"""
        dep_name = bundle_upgrade.dep_name
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
