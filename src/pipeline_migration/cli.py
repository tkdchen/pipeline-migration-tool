import argparse
import json
import logging
import os
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

SCHEMA_UPGRADE: Final[dict[str, Any]] = {
    "$schema": "https://json-schema.org/draft/2020-12",
    "title": "Schema for Renovate upgrade data",
    "type": "object",
    "properties": {
        "depName": {"type": "string", "minLength": 1},
        "currentValue": {"type": "string", "minLength": 1},
        "currentDigest": {"type": "string", "pattern": "^sha256:[0-9a-f]+$"},
        "newValue": {"type": "string", "minLength": 1},
        "newDigest": {"type": "string", "pattern": "^sha256:[0-9a-f]+$"},
        "depTypes": {"type": "array", "items": {"type": "string"}},
        "packageFile": {"type": "string", "minLength": 1},
        "parentDir": {"type": "string", "minLength": 1},
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


def comes_from_konflux(image_repo: str) -> bool:
    if os.environ.get("PMT_LOCAL_TEST"):
        logger.warning(
            "Environment variable PMT_LOCAL_TEST is set. Migration tool works with images "
            "from arbitrary registry organization."
        )
        return True
    return image_repo.startswith("quay.io/konflux-ci/")


def clean_upgrades(input_upgrades: str) -> list[dict[str, Any]]:
    """Clean input Renovate upgrades string

    Only images from konflux-ci image organization are returned. If
    PMT_LOCAL_TEST environment variable is set, this check is skipped and images
    from arbitrary image organizations are returned.

    Only return images handled by Renovate tekton manager.

    :param input_upgrades: a JSON string containing Renovate upgrades data.
    :type input_upgrades: str
    :return: a list of valid upgrade mappings.
    :raises InvalidRenovateUpgradesData: if the input upgrades data is not a
        JSON data and cannot be decoded. If the loaded upgrades data cannot be
        validated by defined schema, also raise this error.
    """
    cleaned_upgrades: list[dict[str, Any]] = []

    try:
        upgrades = json.loads(input_upgrades)
    except json.decoder.JSONDecodeError as e:
        logger.error("Input upgrades is not a valid encoded JSON string: %s", e)
        logger.error(
            "Argument --renovate-upgrades accepts a list of mappings which is a subset of Renovate "
            "template field upgrades. See https://docs.renovatebot.com/templates/"
        )
        raise InvalidRenovateUpgradesData("Input upgrades is not a valid encoded JSON string.")

    if not isinstance(upgrades, list):
        raise InvalidRenovateUpgradesData(
            "Input upgrades is not a list containing Renovate upgrade mappings."
        )

    validator = Draft202012Validator(SCHEMA_UPGRADE)

    for upgrade in upgrades:
        if not upgrade:
            continue  # silently ignore any falsy objects

        dep_name = upgrade.get("depName")

        if not dep_name:
            raise InvalidRenovateUpgradesData("Upgrade does not have value of field depName.")

        if not comes_from_konflux(dep_name):
            logger.info("Dependency %s does not come from Konflux task definitions.", dep_name)
            continue

        try:
            validator.validate(upgrade)
        except ValidationError as e:
            if e.path:  # path could be empty due to missing required properties
                field = e.path[0]
            else:
                field = ""

            logger.error("Input upgrades data does not pass schema validation: %s", e)

            if e.validator == "minLength":
                err_msg = f"Property {field} is empty: {e.message}"
            else:
                err_msg = f"Invalid upgrades data: {e.message}, path '{e.json_path}'"
            raise InvalidRenovateUpgradesData(err_msg)

        if "tekton-bundle" not in upgrade["depTypes"]:
            logger.debug("Dependency %s is not handled by tekton-bundle manager.", dep_name)
            continue

        cleaned_upgrades.append(upgrade)

    return cleaned_upgrades


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

    if args.renovate_upgrades:
        upgrades = clean_upgrades(args.renovate_upgrades)
        if upgrades:
            migrate(upgrades, resolver_class)
        else:
            logger.warning(
                "Input upgrades does not include Konflux bundles the migration tool aims to handle."
            )
            logger.warning(
                "The upgrades should represent bundles pushed to quay.io/konflux-ci and be "
                "generated by Renovate tekton-bundle manager."
            )
    else:
        logger.info("Empty input upgrades.")


def entry_point():
    try:
        main()
    except Exception as e:
        logger.exception("Cannot do migration for pipeline. Reason: %r", e)
        return 1
