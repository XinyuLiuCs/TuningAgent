"""Tests for AgentLogger session directory and subagent correlation."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tuningagent.logger import AgentLogger


@pytest.fixture
def log_dir(tmp_path):
    """Patch AgentLogger to use a temporary log directory."""
    with patch.object(AgentLogger, "__init__", wraps=AgentLogger.__init__) as _:
        pass  # just ensuring class is importable

    def make_logger(**kwargs):
        logger = AgentLogger.__new__(AgentLogger)
        logger.log_dir = tmp_path
        logger.session_id = kwargs.get("session_id")
        logger.agent_id = kwargs.get("agent_id", "agent")
        logger.log_file = None
        logger.turn = 0
        logger.step = 0
        return logger

    return tmp_path, make_logger


class TestSessionDirectory:
    def test_auto_generates_session_id(self, log_dir):
        tmp_path, make_logger = log_dir
        logger = make_logger()
        logger.start_turn()

        assert logger.session_id is not None
        assert (tmp_path / logger.session_id).is_dir()
        assert logger.log_file == tmp_path / logger.session_id / "agent.jsonl"

    def test_shared_session_id(self, log_dir):
        tmp_path, make_logger = log_dir
        parent = make_logger()
        parent.start_turn()

        child = make_logger(session_id=parent.session_id, agent_id="explorer-abc123")
        child.start_turn()

        # Both loggers write to the same session directory
        assert child.session_id == parent.session_id
        session_dir = tmp_path / parent.session_id
        assert (session_dir / "agent.jsonl").exists()
        assert (session_dir / "explorer-abc123.jsonl").exists()

    def test_custom_agent_id(self, log_dir):
        tmp_path, make_logger = log_dir
        logger = make_logger(agent_id="my-subagent")
        logger.start_turn()

        assert logger.log_file.name == "my-subagent.jsonl"


class TestEventMetadata:
    def test_session_and_agent_id_in_events(self, log_dir):
        tmp_path, make_logger = log_dir
        logger = make_logger(session_id="test-session", agent_id="test-agent")
        logger.start_turn()

        lines = logger.log_file.read_text().strip().split("\n")
        for line in lines:
            record = json.loads(line)
            assert record["session_id"] == "test-session"
            assert record["agent_id"] == "test-agent"

    def test_events_written_correctly(self, log_dir):
        tmp_path, make_logger = log_dir
        logger = make_logger()
        logger.start_turn()
        logger.start_step(1)
        logger.end_turn("done")

        lines = logger.log_file.read_text().strip().split("\n")
        events = [json.loads(line)["event"] for line in lines]
        assert events == ["session_start", "turn_start", "turn_end"]


class TestSubagentDispatched:
    def test_log_subagent_dispatched(self, log_dir):
        tmp_path, make_logger = log_dir
        logger = make_logger()
        logger.start_turn()
        logger.log_subagent_dispatched("explorer-abc123", "foreground", "analyze code")

        lines = logger.log_file.read_text().strip().split("\n")
        last = json.loads(lines[-1])
        assert last["event"] == "subagent_dispatched"
        assert last["data"]["subagent_id"] == "explorer-abc123"
        assert last["data"]["mode"] == "foreground"
        assert last["data"]["task"] == "analyze code"

    def test_task_truncated_at_200(self, log_dir):
        tmp_path, make_logger = log_dir
        logger = make_logger()
        logger.start_turn()
        long_task = "x" * 300
        logger.log_subagent_dispatched("sub-1", "background", long_task)

        lines = logger.log_file.read_text().strip().split("\n")
        last = json.loads(lines[-1])
        assert len(last["data"]["task"]) == 200


class TestDefaultBehavior:
    def test_no_args_still_works(self, log_dir):
        """Agent without explicit logger still creates logs normally."""
        tmp_path, make_logger = log_dir
        logger = make_logger()
        logger.start_turn()
        logger.end_turn("result")

        assert logger.log_file is not None
        assert logger.log_file.exists()
        content = logger.log_file.read_text()
        assert "session_start" in content
        assert "turn_end" in content
