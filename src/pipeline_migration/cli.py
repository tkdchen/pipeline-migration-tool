import argparse
import json
import logging
from typing import Any, Final

from jsonschema.exceptions import ValidationError
from jsonschema.validators import Draft202012Validator

from pipeline_migration.migrate import (
    InvalidRenovateUpgradesData,
    migrate,
    SimpleIterationResolver,
    LinkedMigrationsResolver,
)

logging.basicConfig(level=logging.DEBUG, format="%(levelname)s:%(asctime)s:%(name)s:%(message)s")
logger = logging.getLogger("cli")

SCHEMA_UPGRADE: Final = {
    "type": "object",
    "properties": {
        "depName": {"type": "string"},
        "currentValue": {"type": "string"},
        "currentDigest": {"type": "string", "pattern": "^sha256:[0-9a-f]+$"},
        "newValue": {"type": "string"},
        "newDigest": {"type": "string", "pattern": "^sha256:[0-9a-f]+$"},
        "depTypes": {"type": "array", "items": {"type": "string"}},
        "packageFile": {"type": "string"},
        "parentDir": {"type": "string"},
    },
    "additionalProperties": True,
    "required": [
        "currentDigest",
        "currentValue",
        "depName",
        "depTypes",
        "newDigest",
        "newValue",
        "packageFile",
        "parentDir",
    ],
}

SCHEMA_UPGRADES: Final[dict[str, Any]] = {
    "$schema": "https://json-schema.org/draft/2020-12",
    "title": "Schema for Renovate upgrades data",
    "type": "array",
    "items": {},
}

SCHEMA_UPGRADES["items"].update(SCHEMA_UPGRADE)


def validate_upgrades(raw_input: str) -> list[dict[str, Any]]:
    """Validate input upgrades data

    Validate the input upgrades data. Raise errors if it is invalid.

    :param raw_input: an encoded JSON string including upgrades data.
    :type raw_input: str
    :return: validated upgrades data
    :rtype: list[dict[str, any]]
    """

    try:
        upgrades = json.loads(raw_input)
    except json.decoder.JSONDecodeError as e:
        logger.error("Input upgrades is not a valid encoded JSON string: %s", e)
        logger.error(
            "Argument --renovate-upgrades accepts a list of mappings which is a subset of Renovate "
            "template field upgrades. See https://docs.renovatebot.com/templates/"
        )
        raise InvalidRenovateUpgradesData("Input upgrades is not a valid encoded JSON string.")

    try:
        Draft202012Validator(SCHEMA_UPGRADES).validate(upgrades)
    except ValidationError as e:
        logger.error("Input upgrades data does not pass schema validation: %s", e)
        raise InvalidRenovateUpgradesData(
            f"Invalid upgrades data: {e.message} at path '{e.json_path}'"
        )

    return upgrades


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline migration tool for Konflux CI.")
    parser.add_argument(
        "-u",
        "--renovate-upgrades",
        required=True,
        metavar="JSON_STR",
        help="A JSON string converted from Renovate template field upgrades.",
    )
    parser.add_argument(
        "-l",
        "--use-legacy-resolver",
        action="store_true",
        help="Use legacy resolver to fetch migrations.",
    )

    args = parser.parse_args()

    if args.use_legacy_resolver:
        resolver_class = SimpleIterationResolver
    else:
        resolver_class = LinkedMigrationsResolver
    migrate(validate_upgrades(args.renovate_upgrades), resolver_class)


def entry_point():
    try:
        main()
    except Exception as e:
        logger.exception("Cannot do migration for pipeline. Reason: %r", e)
        return 1
