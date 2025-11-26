import socket
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pytest

from pipeline_migration.pipeline import (
    NotAPipelineFile,
    PipelineFileOperation,
    iterate_files_or_dirs,
)


class PipelineFileOperationTestClass(PipelineFileOperation):

    def handle_pipeline_file(self, file_path, loaded_doc, style):
        pass

    def handle_pipeline_run_file(self, file_path, loaded_doc, style):
        pass


class TestPipelineFileOperationHandleMethod:
    """Test PipelineFileOperation.handle method"""

    def setup_method(self, method):
        self.patchers = []

        self.op = PipelineFileOperationTestClass()

        patcher = patch.object(self.op, "handle_pipeline_file")
        self.mock_handle_pipeline_file = patcher.start()
        self.patchers.append(patcher)

        patcher = patch.object(self.op, "handle_pipeline_run_file")
        self.mock_handle_pipeline_run_file = patcher.start()
        self.patchers.append(patcher)

    def teardown_method(self, method):
        for p in self.patchers:
            p.stop()

    def test_handle_pipeline_definition(self, pipeline_yaml, tmp_path):
        pipeline_file = tmp_path / "pl.yaml"
        pipeline_file.write_text(pipeline_yaml)

        self.op.handle(str(pipeline_file))

        self.mock_handle_pipeline_file.assert_called()
        self.mock_handle_pipeline_run_file.assert_not_called()

        content = Path(self.mock_handle_pipeline_file.call_args.args[0]).read_text()
        assert pipeline_yaml == content

    def test_handle_pipeline_run_definition(self, pipeline_run_yaml, tmp_path):
        pipeline_file = tmp_path / "plr.yaml"
        pipeline_file.write_text(pipeline_run_yaml)

        self.op.handle(str(pipeline_file))

        self.mock_handle_pipeline_file.assert_not_called()
        self.mock_handle_pipeline_run_file.assert_called()

        file_path = self.mock_handle_pipeline_run_file.call_args.args[0]
        content = Path(file_path).read_text()
        assert pipeline_run_yaml == content

    @pytest.mark.parametrize(
        "file_content,expected_err",
        [
            pytest.param(
                dedent(
                    """\
                    apiVersion: tekton.dev/v1
                    kind: PipelineRun
                    metadata:
                        name: plr
                    spec:
                        pipelineRef:
                            name: pipeline
                    """
                ),
                "PipelineRun definition seems not embedded",
                id="spec.pipelineRef-is-included",
            ),
            pytest.param("hello world", "not a YAML mapping", id="not-a-yaml-file"),
            pytest.param(
                dedent(
                    """\
                    apiVersion: tekton.dev/v1
                    kind: PipelineRun
                    metadata:
                        name: plr
                    spec:
                    """
                ),
                "neither .pipelineSpec nor .pipelineRef field",
                id="empty-spec",
            ),
            pytest.param(
                "apiVersion: tekton.dev/v1\nspec:",
                "does not have known kind Pipeline or PipelineRun",
                id="unknown-kind",
            ),
        ],
    )
    def test_not_a_pipeline_file(self, file_content, expected_err, tmp_path):
        pipeline_file = tmp_path / "plr.yaml"
        pipeline_file.write_text(file_content)

        with pytest.raises(NotAPipelineFile, match=expected_err):
            self.op.handle(str(pipeline_file))
        self.mock_handle_pipeline_file.assert_not_called()
        self.mock_handle_pipeline_run_file.assert_not_called()


class TestIterateFilesOrDirs:

    def setup_method(self, method):
        self.sock = None

    def teardown_method(self, method):
        if self.sock:
            self.sock.close()

    @pytest.mark.parametrize("data", [[], [""]])
    def test_empty_input_files_or_dirs(self, data):
        assert list(iterate_files_or_dirs(data)) == []

    def _create_noisy_files(self, component_a_repo, component_b_repo):
        text_file = component_b_repo.tekton_dir / "test.txt"
        text_file.write_text("hello world")

        invalid_yaml_file = component_b_repo.tekton_dir / "invalid.yaml"
        invalid_yaml_file.write_bytes(b"\x00")

        common_yaml_file = component_a_repo.tekton_dir / "common.yaml"
        common_yaml_file.write_text("book: Python programming")

        yaml_files = component_b_repo.tekton_dir.glob("*.yaml")
        symlink_to_pr_yaml = component_b_repo.tekton_dir / "link-to.yaml"
        symlink_to_pr_yaml.symlink_to(next(yaml_files))

        # Create a special socket file for satisfying the code path coverage
        sock_file = component_b_repo.tekton_dir / "app.sock"
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(sock_file))

    def test_iterate_given_files(
        self, component_a_repo, component_b_repo, caplog, tmp_path, monkeypatch
    ):
        self._create_noisy_files(component_a_repo, component_b_repo)

        files = map(
            str,
            [
                *[p.name for p in component_a_repo.tekton_dir.iterdir()],
                *component_b_repo.tekton_dir.iterdir(),
            ],
        )

        # for testing converting relative path to absolute path
        monkeypatch.chdir(component_a_repo.tekton_dir)

        found = list(map(str, iterate_files_or_dirs(list(files))))
        expected = [
            str(component_a_repo.tekton_dir / "common.yaml"),
            str(component_a_repo.tekton_dir / "pr.yaml"),
            str(component_a_repo.tekton_dir / "push.yaml"),
            str(component_b_repo.tekton_dir / "build-pipeline.yaml"),
            str(component_b_repo.tekton_dir / "invalid.yaml"),
            str(component_b_repo.tekton_dir / "test.txt"),
        ]
        assert sorted(found) == sorted(expected)

    def test_iterate_directory(self, component_a_repo, component_b_repo, tmp_path, caplog):
        """Ensure the search without recursive walk through directories"""

        self._create_noisy_files(component_a_repo, component_b_repo)

        sub_dir = component_b_repo.tekton_dir / "sub_dir"
        sub_dir.mkdir()
        (sub_dir / "another.file").touch()

        found = list(iterate_files_or_dirs([str(component_b_repo.tekton_dir)]))
        assert sorted(map(str, found)) == sorted(
            [
                str(component_b_repo.tekton_dir / "build-pipeline.yaml"),
                str(component_b_repo.tekton_dir / "invalid.yaml"),
            ],
        )
