import socket
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pytest
from ruamel.yaml import YAML

from pipeline_migration.pipeline import (
    NotAPipelineFile,
    resolve_pipeline,
    search_pipeline_files,
    TEKTON_KIND_PIPELINE,
    TEKTON_KIND_PIPELINE_RUN,
)
from pipeline_migration.utils import load_yaml, dump_yaml


class TestResolvePipeline:

    def test_resolve_from_a_pipeline_definition(self, pipeline_yaml, tmp_path):
        pipeline_file = tmp_path / "pl.yaml"
        pipeline_file.write_text(pipeline_yaml)
        with resolve_pipeline(pipeline_file) as f:
            assert pipeline_yaml == Path(f).read_text()

    def test_resolve_from_a_pipeline_run_definition(self, pipeline_run_yaml, tmp_path):
        pipeline_file = tmp_path / "plr.yaml"
        pipeline_file.write_text(pipeline_run_yaml)
        with resolve_pipeline(pipeline_file) as f:
            resolved_pipeline = load_yaml(f)
            assert "spec" in resolved_pipeline
            pipeline_run = load_yaml(pipeline_file)
            assert resolved_pipeline["spec"] == pipeline_run["spec"]["pipelineSpec"]

    def test_updates_to_pipeline_are_dumped(self, pipeline_and_run_yaml, tmp_path):
        pipeline_file = tmp_path / "file.yaml"
        pipeline_file.write_text(pipeline_and_run_yaml)

        with resolve_pipeline(pipeline_file) as f:
            pl = load_yaml(f)
            pl["spec"]["tasks"].append({"name": "test"})
            dump_yaml(f, pl)

        doc = load_yaml(pipeline_file)
        if doc["kind"] == TEKTON_KIND_PIPELINE:
            tasks = doc["spec"]["tasks"]
        elif doc["kind"] == TEKTON_KIND_PIPELINE_RUN:
            tasks = doc["spec"]["pipelineSpec"]["tasks"]
        else:
            raise ValueError(f"Unexpected kind {doc['kind']}")

        assert tasks[-1]["name"] == "test"

    def test_formatting_ensure_quotes_are_preserved(self, pipeline_and_run_yaml, tmp_path):
        pipeline_file = tmp_path / "file.yaml"
        pipeline_file.write_text(pipeline_and_run_yaml)

        original_yaml = pipeline_file.read_text().rstrip()

        with resolve_pipeline(pipeline_file) as file_path:
            # Make changes to ensure the resolve_pipeline writes content
            # to the original pipeline file
            with open(file_path, "r") as stream:
                content = stream.read()
            with open(file_path, "w+") as stream:
                stream.write("---\n")
                stream.write(content)

        changed_yaml = pipeline_file.read_text().strip("-\n")
        assert changed_yaml == original_yaml

    @patch("pipeline_migration.pipeline.dump_yaml")
    def test_do_not_save_if_pipeline_is_not_modified(
        self, mock_dump_yaml, pipeline_and_run_yaml, tmp_path
    ):
        pipeline_file = tmp_path / "plr.yaml"
        pipeline_file.write_text(pipeline_and_run_yaml)

        with resolve_pipeline(pipeline_file):
            pass  # Nothing is changed

        doc = YAML().load(pipeline_and_run_yaml)
        if doc["kind"] == TEKTON_KIND_PIPELINE:
            assert mock_dump_yaml.call_count == 0
        elif doc["kind"] == TEKTON_KIND_PIPELINE_RUN:
            assert mock_dump_yaml.call_count == 1

    def test_do_not_handle_pipelineref(self, tmp_path):
        pipeline_file = tmp_path / "plr.yaml"
        content = dedent(
            """\
            apiVersion: tekton.dev/v1
            kind: PipelineRun
            metadata:
                name: plr
            spec:
                pipelineRef:
                    name: pipeline
            """
        )
        pipeline_file.write_text(content)
        with pytest.raises(NotAPipelineFile, match="PipelineRun definition seems not embedded"):
            with resolve_pipeline(pipeline_file):
                pass

    def test_given_file_is_not_yaml_file(self, tmp_path):
        pipeline_file = tmp_path / "invalid.file"
        pipeline_file.write_text("hello world")
        with pytest.raises(NotAPipelineFile, match="not a YAML mapping"):
            with resolve_pipeline(pipeline_file):
                pass

    def test_empty_pipeline_run(self, tmp_path):
        pipeline_file = tmp_path / "plr.yaml"
        content = dedent(
            """\
            apiVersion: tekton.dev/v1
            kind: PipelineRun
            metadata:
                name: plr
            spec:
            """
        )
        pipeline_file.write_text(content)
        with pytest.raises(NotAPipelineFile, match="neither .pipelineSpec nor .pipelineRef field"):
            with resolve_pipeline(pipeline_file):
                pass

    def test_given_file_does_not_have_known_kind(self, tmp_path):
        pipeline_file = tmp_path / "plr.yaml"
        content = dedent(
            """\
            apiVersion: tekton.dev/v1
            spec:
            """
        )
        pipeline_file.write_text(content)
        with pytest.raises(
            NotAPipelineFile, match="does not have knownn kind Pipeline or PipelineRun"
        ):
            with resolve_pipeline(pipeline_file):
                pass


class TestSearchPipelineFiles:

    def setup_method(self, method):
        self.sock = None

    def teardown_method(self, method):
        if self.sock:
            self.sock.close()

    @pytest.mark.parametrize("data", [[], [""]])
    def test_empty_input_files_or_dirs(self, data):
        assert list(search_pipeline_files(data)) == []

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

    def test_pipeline_files_from_given_files(
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

        found = list(search_pipeline_files(files))
        assert len(found) == 3

        expected = [
            str(component_a_repo.tekton_dir / "pr.yaml"),
            str(component_a_repo.tekton_dir / "push.yaml"),
            str(component_b_repo.tekton_dir / "build-pipeline.yaml"),
        ]
        assert sorted([item[0] for item in found]) == sorted(expected)

    def test_search_pipeline_files_from_directory(
        self, component_a_repo, component_b_repo, tmp_path, caplog
    ):
        """Ensure the search without recursive walk through directories"""

        self._create_noisy_files(component_a_repo, component_b_repo)

        sub_dir = component_b_repo.tekton_dir / "sub_dir"
        sub_dir.mkdir()
        (sub_dir / "another.file").touch()

        found = list(search_pipeline_files([str(component_b_repo.tekton_dir)]))

        assert len(found) == 1
        original_pipeline_file = found[0][0]
        assert original_pipeline_file == str(component_b_repo.tekton_dir / "build-pipeline.yaml")
