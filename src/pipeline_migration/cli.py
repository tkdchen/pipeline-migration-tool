import argparse
import json
import logging

from pipeline_migration.cache import set_cache_dir
from pipeline_migration.migrate import migrate

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s:%(asctime)s:%(name)s:%(message)s")
logger = logging.getLogger("cli")


def main():
    parser = argparse.ArgumentParser(description="Pipeline migration tool for Konflux CI.")

    parser.add_argument(
        "-u",
        "--renovate-upgrades",
        required=True,
        metavar="JSON_STR",
        help="A JSON string converted from Renovate template field upgrades.",
    )
    parser.add_argument(
        "-d",
        "--cache-dir",
        metavar="PATH",
        help="Path to the cache directory.",
    )

    args = parser.parse_args()
    set_cache_dir(args.cache_dir)

    upgrades = json.loads(args.renovate_upgrades)
    migrate(upgrades)


def entry_point():
    try:
        return main()
    except Exception as e:
        logger.error("Cannot do migration for pipeline. Reason: %r", e)
