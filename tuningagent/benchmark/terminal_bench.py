"""Terminal-Bench runner integration."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from tuningagent.schema import BenchmarkRunSummary, BenchmarkTaskResult

TUNINGAGENT_AGENT_NAME = "tuningagent"
TUNINGAGENT_AGENT_IMPORT_PATH = (
    "tuningagent.benchmark.terminal_bench_agent:TuningAgentTerminalBenchAgent"
)

TERMINAL_BENCH_PROFILES: dict[str, list[str]] = {
    "curated-smoke": [
        "analyze-access-logs",
        "processing-pipeline",
    ],
    "curated-core": [
        "analyze-access-logs",
        "processing-pipeline",
        "assign-seats",
        "ancient-puzzle",
        "simple-sheets-put",
    ],
}

PROXY_ENV_VARS = (
    "ALL_PROXY",
    "all_proxy",
)


@dataclass(slots=True)
class TerminalBenchRunConfig:
    """Configuration for a Terminal-Bench run."""

    bench_dir: Path = Path("bench/terminal-bench")
    tb_executable: str = ".venv/bin/tb"
    dataset_path: str | None = "original-tasks"
    dataset: str | None = None
    profile: str | None = "curated-smoke"
    task_ids: list[str] = field(default_factory=list)
    output_path: str = "runs/tuningagent"
    run_id: str | None = None
    agent: str = TUNINGAGENT_AGENT_NAME
    model: str | None = None
    agent_import_path: str | None = None
    agent_kwargs: list[str] = field(default_factory=list)
    n_concurrent: int = 1
    n_attempts: int = 1
    no_rebuild: bool = False
    cleanup: bool = True
    upload_results: bool = False
    log_level: str = "info"
    strip_proxy_env: bool = True
    dry_run: bool = False

    def resolved_task_ids(self) -> list[str]:
        tasks: list[str] = []
        if self.profile:
            profile_tasks = TERMINAL_BENCH_PROFILES.get(self.profile)
            if profile_tasks is None:
                raise ValueError(
                    f"Unknown Terminal-Bench profile: {self.profile}. "
                    f"Available: {', '.join(sorted(TERMINAL_BENCH_PROFILES))}"
                )
            tasks.extend(profile_tasks)
        tasks.extend(self.task_ids)

        deduped: list[str] = []
        seen: set[str] = set()
        for task_id in tasks:
            if task_id not in seen:
                seen.add(task_id)
                deduped.append(task_id)
        return deduped


class TerminalBenchRunner:
    """Runs Terminal-Bench and converts raw results to TuningAgent schema."""

    def __init__(self, config: TerminalBenchRunConfig):
        self.config = config

    def resolve_bench_dir(self) -> Path:
        bench_dir = self.config.bench_dir.expanduser().resolve()
        if not bench_dir.exists():
            raise FileNotFoundError(f"Terminal-Bench directory not found: {bench_dir}")
        return bench_dir

    def resolve_tb_executable(self) -> str:
        executable = Path(self.config.tb_executable)
        if executable.is_absolute():
            if not executable.exists():
                raise FileNotFoundError(f"Terminal-Bench executable not found: {executable}")
            return str(executable)

        bench_dir = self.resolve_bench_dir()
        bench_relative = bench_dir / executable
        if bench_relative.exists():
            return str(bench_relative)

        return self.config.tb_executable

    def build_env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.config.strip_proxy_env:
            for key in PROXY_ENV_VARS:
                env.pop(key, None)
        if (
            self.config.agent == TUNINGAGENT_AGENT_NAME
            or self.config.agent_import_path == TUNINGAGENT_AGENT_IMPORT_PATH
        ):
            repo_root = str(Path(__file__).resolve().parents[2])
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                repo_root if not existing else os.pathsep.join([repo_root, existing])
            )
        return env

    def build_command(self) -> list[str]:
        task_ids = self.config.resolved_task_ids()
        if not task_ids:
            raise ValueError("At least one task must be selected via profile or --task-id")

        if self.config.dataset and self.config.dataset_path:
            raise ValueError("Only one of dataset or dataset_path may be set")
        if not self.config.dataset and not self.config.dataset_path:
            raise ValueError("One of dataset or dataset_path must be set")

        command = [self.resolve_tb_executable(), "run"]

        if self.config.dataset:
            command.extend(["--dataset", self.config.dataset])
        else:
            command.extend(["--dataset-path", self.config.dataset_path or "original-tasks"])

        for task_id in task_ids:
            command.extend(["--task-id", task_id])

        if self.config.agent == TUNINGAGENT_AGENT_NAME:
            command.extend(["--agent-import-path", TUNINGAGENT_AGENT_IMPORT_PATH])
        elif self.config.agent_import_path:
            command.extend(["--agent-import-path", self.config.agent_import_path])
        else:
            command.extend(["--agent", self.config.agent])
        command.extend(["--output-path", self.config.output_path])
        command.extend(["--n-concurrent", str(self.config.n_concurrent)])
        command.extend(["--n-attempts", str(self.config.n_attempts)])
        command.extend(["--log-level", self.config.log_level])

        if self.config.run_id:
            command.extend(["--run-id", self.config.run_id])
        if self.config.model:
            command.extend(["--model", self.config.model])
        for agent_kwarg in self.config.agent_kwargs:
            command.extend(["--agent-kwarg", agent_kwarg])
        if self.config.no_rebuild:
            command.append("--no-rebuild")
        if not self.config.cleanup:
            command.append("--no-cleanup")
        if not self.config.upload_results:
            command.append("--no-upload-results")

        return command

    def resolve_run_id(self) -> str:
        if self.config.run_id:
            return self.config.run_id
        return datetime.now(timezone.utc).strftime("terminal-bench-%Y%m%d-%H%M%S")

    def raw_results_path(self, run_id: str) -> Path:
        return self.resolve_bench_dir() / self.config.output_path / run_id / "results.json"

    def summary_path(self, run_id: str) -> Path:
        return (
            self.resolve_bench_dir()
            / self.config.output_path
            / run_id
            / "tuningagent_summary.json"
        )

    def normalize_results(self, raw_results_path: Path, run_id: str) -> BenchmarkRunSummary:
        payload = json.loads(raw_results_path.read_text(encoding="utf-8"))
        tasks: list[BenchmarkTaskResult] = []

        for item in payload.get("results", []):
            tasks.append(
                BenchmarkTaskResult(
                    task_id=item["task_id"],
                    trial_name=item["trial_name"],
                    resolved=bool(item.get("is_resolved", False)),
                    failure_mode=item.get("failure_mode"),
                    parser_results=item.get("parser_results") or {},
                    started_at=item.get("trial_started_at"),
                    ended_at=item.get("trial_ended_at"),
                    recording_path=item.get("recording_path"),
                )
            )

        dataset_name = self.config.dataset or self.config.dataset_path or "unknown"
        return BenchmarkRunSummary(
            benchmark="terminal-bench",
            run_id=run_id,
            dataset=dataset_name,
            task_ids=self.config.resolved_task_ids(),
            resolved_count=payload.get("n_resolved", 0),
            unresolved_count=payload.get("n_unresolved", 0),
            accuracy=payload.get("accuracy", 0.0),
            raw_results_path=str(raw_results_path),
            created_at=datetime.now(timezone.utc),
            tasks=tasks,
        )

    def write_summary(self, summary: BenchmarkRunSummary) -> Path:
        summary_path = self.summary_path(summary.run_id)
        summary.summary_path = str(summary_path)
        summary_path.write_text(
            json.dumps(summary.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return summary_path

    def run(self) -> BenchmarkRunSummary:
        bench_dir = self.resolve_bench_dir()
        run_id = self.resolve_run_id()
        command = self.build_command()

        if "--run-id" not in command:
            command.extend(["--run-id", run_id])

        if self.config.dry_run:
            summary = BenchmarkRunSummary(
                benchmark="terminal-bench",
                run_id=run_id,
                dataset=self.config.dataset or self.config.dataset_path or "unknown",
                task_ids=self.config.resolved_task_ids(),
                resolved_count=0,
                unresolved_count=0,
                accuracy=0.0,
                raw_results_path=str(self.raw_results_path(run_id)),
                created_at=datetime.now(timezone.utc),
                tasks=[],
            )
            return summary

        completed = subprocess.run(
            command,
            cwd=bench_dir,
            env=self.build_env(),
            check=False,
        )
        raw_results_path = self.raw_results_path(run_id)
        if not raw_results_path.exists():
            if completed.returncode != 0:
                raise RuntimeError(
                    f"Terminal-Bench run failed with exit code {completed.returncode}"
                )
            raise FileNotFoundError(
                f"Terminal-Bench finished but results file was not found: {raw_results_path}"
            )

        summary = self.normalize_results(raw_results_path, run_id)
        self.write_summary(summary)

        if completed.returncode != 0:
            raise RuntimeError(
                f"Terminal-Bench run failed with exit code {completed.returncode}; "
                f"partial results were written to {raw_results_path}"
            )
        return summary


def format_profiles(profiles: Sequence[str] | None = None) -> str:
    """Format built-in profiles for CLI output."""

    selected = profiles or sorted(TERMINAL_BENCH_PROFILES)
    lines = []
    for name in selected:
        tasks = TERMINAL_BENCH_PROFILES[name]
        lines.append(f"{name}: {', '.join(tasks)}")
    return "\n".join(lines)
