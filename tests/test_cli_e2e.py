"""End-to-end CLI tests using real API calls.

These tests drive the CLI programmatically via pipe input/output,
send real tasks to a real LLM, and verify real tool side-effects.

Requires a valid config with API keys at the default config path.
"""

import pytest

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from tuningagent.cli import run_agent
from tuningagent.config import Config

# All tests in this file hit a real API — mark them so they can be
# selected or excluded easily (e.g. pytest -m e2e / pytest -m "not e2e").
pytestmark = pytest.mark.e2e

# Keep agent loops short so tests finish in reasonable time.
E2E_MAX_STEPS = 5


def _load_config() -> Config:
    """Load config from the default path (must have valid API keys)."""
    config = Config.from_yaml(Config.get_default_config_path())
    config.agent.max_steps = E2E_MAX_STEPS
    return config


async def test_create_file_task(tmp_path):
    """User asks the agent to create a file; verify it appears on disk."""
    config = _load_config()

    with create_pipe_input() as inp:
        inp.send_text(
            "Create a file named hello.txt with content 'Hello World' in the current workspace directory. "
            "Use the write tool.\r"
        )
        inp.send_text("/exit\r")
        await run_agent(tmp_path, config=config, input=inp, output=DummyOutput())

    assert (tmp_path / "hello.txt").exists(), "hello.txt was not created"
    content = (tmp_path / "hello.txt").read_text()
    assert "Hello" in content, f"Unexpected file content: {content!r}"


async def test_bash_command_side_effect(tmp_path):
    """User asks the agent to run a bash command; verify its side-effect."""
    config = _load_config()

    with create_pipe_input() as inp:
        inp.send_text(f"Run this exact bash command: cd {tmp_path} && mkdir -p testdir && touch testdir/flag.txt\r")
        inp.send_text("/exit\r")
        await run_agent(tmp_path, config=config, input=inp, output=DummyOutput())

    assert (tmp_path / "testdir" / "flag.txt").exists(), "bash side-effect not observed"


async def test_read_and_edit_file(tmp_path):
    """User asks the agent to read a file and edit it; verify the edit."""
    # Seed a file for the agent to modify
    target = tmp_path / "greet.txt"
    target.write_text("Hello World")

    config = _load_config()

    with create_pipe_input() as inp:
        inp.send_text(
            "Read the file greet.txt in the workspace, then edit it to replace 'World' with 'Agent'.\r"
        )
        inp.send_text("/exit\r")
        await run_agent(tmp_path, config=config, input=inp, output=DummyOutput())

    content = target.read_text()
    assert "Agent" in content, f"Edit not applied. File content: {content!r}"
