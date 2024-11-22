import argparse
import logging
import os
import tempfile

from pipeline_migration.cache import ENV_FBC_DIR, set_cache_dir
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

    cache_dir = args.cache_dir or os.environ.get(ENV_FBC_DIR)
    if not cache_dir:
        cache_dir = tempfile.mkdtemp(prefix="cache-dir-")
        logger.info(
            "Cache directory is not specified either from command line or by environment "
            "variable %s, use directory %s instead.",
            ENV_FBC_DIR,
            cache_dir,
        )
    set_cache_dir(cache_dir)

    migrate(args.renovate_upgrades)


def entry_point():
    try:
        return main()
    except Exception as e:
        logger.error("Cannot do migration for pipeline. Reason: %r", e)
