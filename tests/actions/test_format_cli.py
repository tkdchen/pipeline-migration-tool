from textwrap import dedent

from pipeline_migration.cli import entry_point
from pipeline_migration.utils import YAMLStyle


def test_format(monkeypatch, tmp_path):

    tekton_dir = tmp_path / ".tekton"
    tekton_dir.mkdir()

    (tekton_dir / "pull.yaml").write_text(
        dedent(
            """\
            apiVersion: tekton.dev/v1
            kind: PipelineRun
            spec:
              params:
                - name: revision
                  value: '{{revision}}'
              pipelineSpec:
                - name: revision
                  default: "main"
            """
        )
    )

    # TODO: add wrapped description
    (tekton_dir / "build-pipeline.yaml").write_text(
        dedent(
            """\
            apiVersion: tekton.dev/v1
            kind: Pipeline
            spec:
              params:
                - name: revision
                  default: "main"
            """
        )
    )

    cmd = ["pmt", "format", str(tekton_dir)]
    monkeypatch.setattr("sys.argv", cmd)

    entry_point()

    for file_path in [tekton_dir / "pull.yaml", tekton_dir / "build-pipeline.yaml"]:
        style = YAMLStyle.detect(file_path)
        assert style.indentation.is_consistent
        assert style.indentation.levels == [0]

        assert 'default: "main"' in file_path.read_text()
