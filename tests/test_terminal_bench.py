"""Tests for the Terminal-Bench integration."""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from tuningagent.benchmark import (
    TUNINGAGENT_AGENT_IMPORT_PATH,
    TUNINGAGENT_AGENT_NAME,
    TerminalBenchRunConfig,
    TerminalBenchRunner,
)
from tuningagent.benchmark.terminal_bench_agent import (
    RemoteBackgroundRegistry,
    SessionBashTool,
    SessionReadTool,
)
from tuningagent.cli import parse_args


def test_terminal_bench_command_builds_from_profile_and_explicit_task(tmp_path):
    bench_dir = tmp_path / "terminal-bench"
    tb_path = bench_dir / ".venv" / "bin"
    tb_path.mkdir(parents=True)
    (tb_path / "tb").write_text("", encoding="utf-8")

    config = TerminalBenchRunConfig(
        bench_dir=bench_dir,
        profile="curated-smoke",
        task_ids=["assign-seats", "processing-pipeline"],
        run_id="run-123",
        agent="oracle",
    )

    command = TerminalBenchRunner(config).build_command()

    assert command[:2] == [str(tb_path / "tb"), "run"]
    assert "--dataset-path" in command
    assert command.count("--task-id") == 3
    assert command[command.index("--run-id") + 1] == "run-123"
    assert command.count("processing-pipeline") == 1


def test_terminal_bench_tuningagent_uses_import_path_and_pythonpath(tmp_path):
    bench_dir = tmp_path / "terminal-bench"
    tb_path = bench_dir / ".venv" / "bin"
    tb_path.mkdir(parents=True)
    (tb_path / "tb").write_text("", encoding="utf-8")

    runner = TerminalBenchRunner(
        TerminalBenchRunConfig(
            bench_dir=bench_dir,
            profile="curated-smoke",
            task_ids=[],
            run_id="run-123",
            agent="tuningagent",
        )
    )

    command = runner.build_command()
    env = runner.build_env()

    assert "--agent-import-path" in command
    assert TUNINGAGENT_AGENT_IMPORT_PATH in command
    assert "PYTHONPATH" in env
    assert str(Path.cwd()) in env["PYTHONPATH"]


def test_terminal_bench_run_config_defaults_to_tuningagent():
    config = TerminalBenchRunConfig()

    assert config.agent == TUNINGAGENT_AGENT_NAME


def test_terminal_bench_env_strips_proxy_vars(tmp_path, monkeypatch):
    bench_dir = tmp_path / "terminal-bench"
    bench_dir.mkdir()

    monkeypatch.setenv("HTTP_PROXY", "http://proxy")
    monkeypatch.setenv("NO_PROXY", "localhost")
    monkeypatch.setenv("HTTPS_PROXY", "http://secure-proxy")
    monkeypatch.setenv("KEEP_ME", "1")

    env = TerminalBenchRunner(
        TerminalBenchRunConfig(bench_dir=bench_dir, task_ids=["analyze-access-logs"])
    ).build_env()

    assert "HTTP_PROXY" in env
    assert "HTTPS_PROXY" in env
    assert "NO_PROXY" in env
    assert env["KEEP_ME"] == "1"


def test_terminal_bench_normalizes_results(tmp_path):
    bench_dir = tmp_path / "terminal-bench"
    run_dir = bench_dir / "runs" / "tuningagent" / "run-123"
    run_dir.mkdir(parents=True)

    raw_payload = {
        "n_resolved": 1,
        "n_unresolved": 1,
        "accuracy": 0.5,
        "results": [
            {
                "task_id": "processing-pipeline",
                "trial_name": "processing-pipeline.1-of-1.run-123",
                "is_resolved": True,
                "failure_mode": "unset",
                "parser_results": {"test_pipeline_execution": "passed"},
                "trial_started_at": "2026-03-08T07:55:12.228675+00:00",
                "trial_ended_at": "2026-03-08T07:56:44.183340+00:00",
                "recording_path": "runs/agent.cast",
            },
            {
                "task_id": "assign-seats",
                "trial_name": "assign-seats.1-of-1.run-123",
                "is_resolved": False,
                "failure_mode": "timeout",
                "parser_results": {"test_neighbors": "failed"},
                "trial_started_at": "2026-03-08T07:57:12.228675+00:00",
                "trial_ended_at": "2026-03-08T07:58:44.183340+00:00",
                "recording_path": None,
            },
        ],
    }
    raw_results_path = run_dir / "results.json"
    raw_results_path.write_text(json.dumps(raw_payload), encoding="utf-8")

    runner = TerminalBenchRunner(
        TerminalBenchRunConfig(
            bench_dir=bench_dir,
            output_path="runs/tuningagent",
            run_id="run-123",
            profile=None,
            task_ids=["processing-pipeline", "assign-seats"],
        )
    )

    summary = runner.normalize_results(raw_results_path, "run-123")
    summary_path = runner.write_summary(summary)

    assert summary.benchmark == "terminal-bench"
    assert summary.run_id == "run-123"
    assert summary.dataset == "original-tasks"
    assert summary.resolved_count == 1
    assert summary.unresolved_count == 1
    assert summary.accuracy == 0.5
    assert len(summary.tasks) == 2
    assert summary.tasks[0].resolved is True
    assert summary.tasks[0].started_at == datetime.fromisoformat("2026-03-08T07:55:12.228675+00:00")
    assert summary.tasks[1].failure_mode == "timeout"
    assert summary_path.exists()


def test_terminal_bench_normalizes_partial_failure_results(tmp_path):
    bench_dir = tmp_path / "terminal-bench"
    run_dir = bench_dir / "runs" / "tuningagent" / "run-err"
    run_dir.mkdir(parents=True)

    raw_results_path = run_dir / "results.json"
    raw_results_path.write_text(
        json.dumps(
            {
                "n_resolved": 0,
                "n_unresolved": 1,
                "accuracy": 0.0,
                "results": [
                    {
                        "task_id": "analyze-access-logs",
                        "trial_name": "analyze-access-logs.1-of-1.run-err",
                        "is_resolved": None,
                        "failure_mode": "unknown_agent_error",
                        "parser_results": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    runner = TerminalBenchRunner(
        TerminalBenchRunConfig(
            bench_dir=bench_dir,
            output_path="runs/tuningagent",
            run_id="run-err",
            profile=None,
            task_ids=["analyze-access-logs"],
        )
    )

    summary = runner.normalize_results(raw_results_path, "run-err")

    assert summary.tasks[0].resolved is False
    assert summary.tasks[0].parser_results == {}


def test_parse_args_benchmark_defaults(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["tuningagent", "benchmark", "--task-id", "assign-seats"],
    )

    args = parse_args()

    assert args.command == "benchmark"
    assert args.profile == "curated-smoke"
    assert args.agent == "tuningagent"
    assert args.task_id == ["assign-seats"]
    assert args.dataset_path == "original-tasks"


def test_parse_args_benchmark_no_profile(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["tuningagent", "benchmark", "--no-profile", "--task-id", "analyze-access-logs"],
    )

    args = parse_args()

    assert args.no_profile is True
    assert args.task_id == ["analyze-access-logs"]


def test_session_bash_tool_extracts_clean_output():
    tool = SessionBashTool(
        session=SimpleNamespace(),
        workspace_dir="/app",
        background_registry=RemoteBackgroundRegistry(),
    )
    buffer_text = """
root@box:/app# printf '__TUNINGAGENT_CMD_START__\\n'; cd /app && (wc -l access_log); __ta_status=$?; printf '__TUNINGAGENT_EXIT_CODE__=%s\\n' "$__ta_status"
__TUNINGAGENT_CMD_START__
2000
__TUNINGAGENT_EXIT_CODE__=0
root@box:/app#
"""
    output, exit_code = tool._extract_command_output(buffer_text)

    assert output == "2000"
    assert exit_code == 0


def test_session_read_tool_marks_large_files_as_truncated():
    def exec_run(args):
        script = args[-1]
        if "wc -l < /app/access_log" in script:
            return SimpleNamespace(exit_code=0, output=b"2000\n")
        if "nl -ba /app/access_log" in script:
            output = "\n".join(
                f"{line:>6}\tline-{line}"
                for line in range(1, SessionReadTool._MAX_DEFAULT_LINES + 1)
            )
            return SimpleNamespace(exit_code=0, output=output.encode("utf-8"))
        raise AssertionError(f"Unexpected script: {script}")

    tool = SessionReadTool(
        session=SimpleNamespace(container=SimpleNamespace(exec_run=exec_run)),
        workspace_dir="/app",
    )

    result = asyncio.run(tool.execute("/app/access_log"))

    assert result.success is True
    assert "truncated file view" in result.content
    assert "showing lines 1-250 of 2000" in result.content
    assert "1|line-1" in result.content
