import pytest
from textwrap import dedent
from pipeline_migration.utils import YAMLStyle, dump_yaml, load_yaml, BlockSequenceIndentation

YAML_EXAMPLE_0_INDENT = """\
apiVersion: tekton.dev/v1
spec:
  params:
  - name: git-url
    type: string
  - name: revision
    type: string
  tasks:
  - name: clone-repository
  - name: build-container
"""

YAML_EXAMPLE_2_INDENTS = """\
apiVersion: tekton.dev/v1
spec:
  params:
    - name: git-url
      type: string
    - name: revision
      type: string
  tasks:
    - name: clone-repository
    - name: build-container
"""

YAML_EXAMPLE_3_INDENTS = """\
apiVersion: tekton.dev/v1
spec:
  params:
    - name: git-url
      type: string
    - name: revision
      type: string
    - name: build-args
      type: array
      default: []
  tasks:
    - name: clone-repository
    - name: build-container
"""

YAML_EXAMPLE_MIXED_INDENT_LEVELS = """\
apiVersion: tekton.dev/v1
spec:
  params:
    - name: git-url
      type: string
    - name: revision
      type: string
  tasks:
  - name: clone-repository
    params:
     - name: git-url
     - name: revision
  - name: build-container
    params:
     - name: git-url
     - name: revision
  finally:
       - name: show-summary
       - name: show-sbom
"""

YAML_EMPTY_FLOW_SEQ = """\
spec:
  params:
    - name: git-url
      type: string
      default: []
    - name: revision
      type: string
"""

YAML_FLOW_SEQ_WITH_VALUES = """\
spec:
  params:
    - name: git-url
      type: string
    - name: revision
      type: string
    - build-args: ["maintainer=build",
         "build-date=someday"]
"""


@pytest.mark.parametrize(
    "yaml,expected",
    [
        [YAML_EXAMPLE_0_INDENT, [True, {0: 2}]],
        [YAML_EXAMPLE_2_INDENTS, [True, {2: 2}]],
        [YAML_EXAMPLE_MIXED_INDENT_LEVELS, [False, {2: 1, 0: 1, 1: 2, 5: 1}]],
        [YAML_EMPTY_FLOW_SEQ, [True, {2: 1}]],
        [YAML_FLOW_SEQ_WITH_VALUES, [True, {2: 1}]],
    ],
)
def test_indentation_detection(yaml, expected, tmp_path):
    yaml_file = tmp_path / "file.yaml"
    yaml_file.write_text(yaml)
    ys = YAMLStyle.detect(yaml_file)
    is_consistent, levels = expected
    assert ys.indentation.is_consistent == is_consistent
    assert ys.indentation.levels == list(levels.keys())
    assert ys.indentation.indentations == levels


@pytest.mark.parametrize(
    "style,data,expected_yaml",
    [
        [
            None,
            {"params": [{"name": "git-url"}, {"name": "revision"}]},
            dedent(
                """\
                params:
                - name: git-url
                - name: revision
                """
            ),
        ],
        [
            YAMLStyle(indentation=BlockSequenceIndentation(indentations={0: 1})),
            {"params": [{"name": "git-url"}, {"name": "revision"}]},
            dedent(
                """\
                params:
                - name: git-url
                - name: revision
                """
            ),
        ],
        [
            YAMLStyle(indentation=BlockSequenceIndentation(indentations={2: 1})),
            {"params": [{"name": "git-url"}, {"name": "revision"}]},
            dedent(
                """\
                params:
                  - name: git-url
                  - name: revision
                """
            ),
        ],
        [
            YAMLStyle(indentation=BlockSequenceIndentation(indentations={2: 2, 0: 10, 3: 1})),
            {"params": [{"name": "git-url"}, {"name": "revision"}]},
            dedent(
                """\
                params:
                - name: git-url
                - name: revision
                """
            ),
        ],
    ],
)
def test_dump_yaml_with_style(style, data, expected_yaml, tmp_path):
    yaml_file = tmp_path / "file.yaml"
    dump_yaml(yaml_file, data, style=style)
    assert yaml_file.read_text() == expected_yaml


@pytest.mark.parametrize(
    "yaml_content", [YAML_EXAMPLE_0_INDENT, YAML_EXAMPLE_2_INDENTS, YAML_EXAMPLE_3_INDENTS]
)
def test_dump_same_yaml_with_consistent_indentation_formatting(yaml_content, tmp_path):
    yaml_file = tmp_path / "file.yaml"
    yaml_file.write_text(yaml_content)
    style = YAMLStyle.detect(yaml_file)
    doc = load_yaml(yaml_file)
    new_file = tmp_path / "new.yaml"
    dump_yaml(new_file, doc, style)
    assert yaml_file.read_text() == new_file.read_text()
