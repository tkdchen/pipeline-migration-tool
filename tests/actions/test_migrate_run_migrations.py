import json
import logging
import os
import os.path
from textwrap import dedent, indent
from typing import Callable

import pytest
import responses
from ruamel.yaml import YAML

from pipeline_migration.cli import entry_point
from pipeline_migration.pipeline import TEKTON_KIND_PIPELINE, TEKTON_KIND_PIPELINE_RUN
from tests.actions.test_migrate import TASK_BUNDLE_CLONE
from tests.actions.test_migrate_cli import mock_has_migration_images
from tests.utils import generate_sha256sum, generate_timestamp


@pytest.fixture(params=["pipeline", "pipeline_run"])
def package_file(request, component_a_repo, component_b_repo):
    match request.param:
        case "pipeline":
            return component_b_repo.tekton_dir / "build-pipeline.yaml"
        case "pipeline_run":
            return component_a_repo.tekton_dir / "pr.yaml"
        case _:
            raise ValueError(f"Unexpected param {request.param}")


MIGRATION_0_2_1_ADD_PARAM = """\
#!/usr/bin/env bash
pipeline=$1
pmt modify -f "$pipeline" task clone add-param git-url "https://github.com/org/app"
"""

MIGRATION_0_3_ADD_PARAM = """\
#!/usr/bin/env bash
pipeline=$1
pmt modify -f "$pipeline" task clone add-param revision 1234567
"""


def ensure_params_are_added(modified_pipeline_content: str):

    def _generate_added_params_text(yaml_content) -> str:
        yaml = YAML()
        doc = yaml.load(yaml_content)
        if doc["kind"] == TEKTON_KIND_PIPELINE:
            indent_level = doc["spec"]["tasks"][0].lc.col
        else:
            indent_level = doc["spec"]["pipelineSpec"]["tasks"][0].lc.col

        return indent(
            dedent(
                """\
                params:
                - name: git-url
                  value: https://github.com/org/app
                - name: revision
                  value: '1234567'
                """
            ),
            " " * indent_level,
        )

    expected = _generate_added_params_text(modified_pipeline_content)
    assert expected in modified_pipeline_content


MIGRATION_0_2_1_GENERIC_INSERT = """\
#!/usr/bin/env bash
pipeline=$1
if grep -q -m 1 "^kind: Pipeline$" "$pipeline"; then
    pmt modify -f "$pipeline" generic insert \
        '["spec"]' \
        '{"workspaces": [{"name": "git-auth", "optional": "true"}]}'
fi
"""

MIGRATION_0_3_GENERIC_REMOVE = """\
#!/usr/bin/env bash
pipeline=$1
if grep -q -m 1 "^kind: PipelineRun$" "$pipeline"; then
    pmt modify -f "$pipeline" generic remove '["spec", "pipelineSpec", "params", 0]'
fi
"""


def ensure_workspace_is_inserted(modified_pipeline_content: str):
    yaml = YAML()
    doc = yaml.load(modified_pipeline_content)
    if doc["kind"] != TEKTON_KIND_PIPELINE:
        return
    indent_level = doc["spec"].lc.col

    expected = indent(
        dedent(
            """\
            workspaces:
            - name: git-auth
              optional: 'true'
            """
        ),
        " " * indent_level,
    )
    assert expected in modified_pipeline_content


def ensure_pipeline_param_git_url_is_removed(modified_pipeline_content: str):
    yaml = YAML()
    doc = yaml.load(modified_pipeline_content)
    if doc["kind"] != TEKTON_KIND_PIPELINE_RUN:
        return
    indent_level = doc["spec"]["pipelineSpec"].lc.col
    # Param git-url is expected to be removed
    expected = indent(
        dedent(
            """\
            params:
            - name: revision
              default: "main"
            """
        ),
        " " * indent_level,
    )
    assert expected in modified_pipeline_content


@responses.activate
@pytest.mark.parametrize(
    "migration_scripts,assertion_methods",
    [
        pytest.param(
            [MIGRATION_0_2_1_ADD_PARAM, MIGRATION_0_3_ADD_PARAM],
            [ensure_params_are_added],
            id="migration-by-task-add-param",
        ),
        pytest.param(
            [MIGRATION_0_2_1_GENERIC_INSERT, MIGRATION_0_3_GENERIC_REMOVE],
            [ensure_workspace_is_inserted, ensure_pipeline_param_git_url_is_removed],
            id="migration-by-generic-insert-and-remove",
        ),
    ],
)
def test_apply_migration(
    migration_scripts: list[str],
    assertion_methods: list[Callable[[str], None]],
    caplog,
    monkeypatch,
    tmp_path,
    package_file,
    mock_migration_images,
):
    """Apply migrations by running a real pmt-modify command

    This test requires setting an environment variable BIN_DIR, its value is an
    absolute path where the ``pmt`` command is present. Test will construct a pmt
    command with the absolute path and replace pmt in the migration script.

    If BIN_DIR is not present, keep the pmt command unchanged in the migration
    script. It will rely on the PATH to look for pmt.
    """
    bundle_upgrades = [
        {
            "depName": TASK_BUNDLE_CLONE,
            "currentValue": "0.1.3",
            "currentDigest": "sha256:021020bc57b1",
            "newValue": "0.3",
            "newDigest": "sha256:d1366e3650bb",
            "depTypes": ["tekton-bundle"],
            "packageFile": str(package_file),
            "parentDir": package_file.parent.name,
        },
    ]

    upgrades_file = tmp_path / "upgrades.txt"
    upgrades_file.write_text(json.dumps(bundle_upgrades))

    caplog.set_level(logging.DEBUG)
    mock_has_migration_images(TASK_BUNDLE_CLONE, True)

    prepared_migration_scripts = migration_scripts
    bin_dir = os.environ.get("BIN_DIR")
    if bin_dir:
        pmt_cmd = os.path.join(bin_dir, "pmt")
        if not os.path.exists(pmt_cmd):
            raise ValueError("Command pmt is not present under BIN_DIR " + bin_dir)
        monkeypatch.setitem(os.environ, "PATH", os.environ["PATH"] + ":" + bin_dir)

    ts_gen = generate_timestamp()
    mock_migration_images(
        TASK_BUNDLE_CLONE,
        [
            {"name": f"migration-0.2.1-{generate_sha256sum()}-{ts_gen()}"},
            {"name": f"migration-0.3-{generate_sha256sum()}-{ts_gen()}"},
        ],
        migration_scripts=prepared_migration_scripts,
    )

    cli_cmd = ["pmt", "migrate", "-f", str(upgrades_file)]
    monkeypatch.setattr("sys.argv", cli_cmd)

    entry_point()

    for method in assertion_methods:
        method(package_file.read_text())
