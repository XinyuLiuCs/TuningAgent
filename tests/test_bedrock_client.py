"""Tests for AWS Bedrock provider: BedrockClient, LLMClient integration, config parsing, and ModelPool."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tuningagent.config import Config, ModelConfig
from tuningagent.llm.bedrock_client import BedrockClient
from tuningagent.llm.llm_wrapper import LLMClient
from tuningagent.llm.model_pool import ModelPool
from tuningagent.schema import LLMProvider, LLMResponse, TokenUsage


# ---------------------------------------------------------------------------
# BedrockClient initialization
# ---------------------------------------------------------------------------


class TestBedrockClientInit:
    @patch("tuningagent.llm.bedrock_client.anthropic.AsyncAnthropicBedrock")
    def test_default_init(self, mock_bedrock_cls):
        client = BedrockClient(model="us.anthropic.claude-opus-4-6-v1:0")

        assert client.model == "us.anthropic.claude-opus-4-6-v1:0"
        mock_bedrock_cls.assert_called_once_with()

    @patch("tuningagent.llm.bedrock_client.anthropic.AsyncAnthropicBedrock")
    def test_with_region(self, mock_bedrock_cls):
        BedrockClient(model="test-model", aws_region="us-west-2")

        mock_bedrock_cls.assert_called_once_with(aws_region="us-west-2")

    @patch("tuningagent.llm.bedrock_client.anthropic.AsyncAnthropicBedrock")
    def test_with_profile(self, mock_bedrock_cls):
        BedrockClient(model="test-model", aws_profile="my-profile")

        mock_bedrock_cls.assert_called_once_with(aws_profile="my-profile")

    @patch("tuningagent.llm.bedrock_client.anthropic.AsyncAnthropicBedrock")
    def test_with_region_and_profile(self, mock_bedrock_cls):
        BedrockClient(model="test-model", aws_region="eu-west-1", aws_profile="prod")

        mock_bedrock_cls.assert_called_once_with(aws_region="eu-west-1", aws_profile="prod")

    @patch("tuningagent.llm.bedrock_client.anthropic.AsyncAnthropicBedrock")
    def test_inherits_anthropic_client(self, mock_bedrock_cls):
        from tuningagent.llm.anthropic_client import AnthropicClient

        client = BedrockClient(model="test-model")
        assert isinstance(client, AnthropicClient)


# ---------------------------------------------------------------------------
# BedrockClient generate (delegates to inherited logic)
# ---------------------------------------------------------------------------


class TestBedrockClientGenerate:
    @patch("tuningagent.llm.bedrock_client.anthropic.AsyncAnthropicBedrock")
    async def test_generate_delegates_to_api(self, mock_bedrock_cls):
        # Build a mock response that looks like an Anthropic Message
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Hello from Bedrock")]
        mock_response.stop_reason = "end_turn"
        mock_response.usage = MagicMock(
            input_tokens=10, output_tokens=5,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        )

        mock_client_instance = AsyncMock()
        mock_client_instance.messages.create = AsyncMock(return_value=mock_response)
        mock_bedrock_cls.return_value = mock_client_instance

        client = BedrockClient(model="test-model", aws_region="us-east-1")

        from tuningagent.schema import Message
        messages = [Message(role="user", content="Hi")]
        result = await client.generate(messages)

        assert result.content == "Hello from Bedrock"
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 5
        mock_client_instance.messages.create.assert_awaited_once()


# ---------------------------------------------------------------------------
# LLMClient with bedrock provider
# ---------------------------------------------------------------------------


class TestLLMClientBedrock:
    @patch("tuningagent.llm.llm_wrapper.BedrockClient")
    def test_creates_bedrock_client(self, mock_bedrock_cls):
        client = LLMClient(
            provider=LLMProvider.BEDROCK,
            model="us.anthropic.claude-opus-4-6-v1:0",
            aws_region="us-east-1",
        )

        assert client.provider == LLMProvider.BEDROCK
        assert client.api_base == ""
        mock_bedrock_cls.assert_called_once_with(
            model="us.anthropic.claude-opus-4-6-v1:0",
            aws_region="us-east-1",
            aws_profile="",
            retry_config=None,
        )

    @patch("tuningagent.llm.llm_wrapper.BedrockClient")
    def test_no_api_key_required(self, mock_bedrock_cls):
        """Bedrock provider should work without an api_key."""
        client = LLMClient(provider=LLMProvider.BEDROCK, model="test-model")
        assert client.api_key == ""

    @patch("tuningagent.llm.llm_wrapper.BedrockClient")
    async def test_health_check_delegates(self, mock_bedrock_cls):
        mock_instance = AsyncMock()
        mock_instance.health_check = AsyncMock(return_value=True)
        mock_bedrock_cls.return_value = mock_instance

        client = LLMClient(provider=LLMProvider.BEDROCK, model="test-model")
        result = await client.health_check()

        assert result is True
        mock_instance.health_check.assert_awaited_once()


# ---------------------------------------------------------------------------
# ModelPool with bedrock provider
# ---------------------------------------------------------------------------


class TestModelPoolBedrock:
    @patch("tuningagent.llm.llm_wrapper.BedrockClient")
    def test_add_bedrock_model(self, mock_bedrock_cls):
        pool = ModelPool()
        config = ModelConfig(
            provider="bedrock",
            model="us.anthropic.claude-opus-4-6-v1:0",
            aws_region="us-east-1",
        )
        pool.add_model("bedrock-claude", config)

        models = pool.list_models()
        assert len(models) == 1
        assert models[0]["alias"] == "bedrock-claude"
        assert models[0]["provider"] == "bedrock"

    @patch("tuningagent.llm.llm_wrapper.BedrockClient")
    def test_mixed_pool(self, mock_bedrock_cls):
        pool = ModelPool()
        pool.add_model("openai-gpt", ModelConfig(
            api_key="sk-test",
            api_base="https://api.example.com",
            model="gpt-4",
            provider="openai",
        ))
        pool.add_model("bedrock-claude", ModelConfig(
            provider="bedrock",
            model="us.anthropic.claude-opus-4-6-v1:0",
            aws_region="us-east-1",
        ))

        models = pool.list_models()
        assert len(models) == 2
        providers = {m["alias"]: m["provider"] for m in models}
        assert providers["openai-gpt"] == "openai"
        assert providers["bedrock-claude"] == "bedrock"

    def test_unknown_provider_raises(self):
        pool = ModelPool()
        config = ModelConfig(api_key="sk-test", provider="unknown")
        with pytest.raises(ValueError, match="Unknown provider"):
            pool.add_model("bad", config)


# ---------------------------------------------------------------------------
# Config parsing with bedrock
# ---------------------------------------------------------------------------


class TestConfigBedrock:
    def _write_yaml(self, content: str) -> Path:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(content)
        f.close()
        return Path(f.name)

    def test_bedrock_model_no_api_key(self):
        path = self._write_yaml("""
models:
  bedrock-claude:
    provider: "bedrock"
    model: "us.anthropic.claude-opus-4-6-v1:0"
    aws_region: "us-east-1"
""")
        config = Config.from_yaml(path)

        assert "bedrock-claude" in config.models
        mc = config.models["bedrock-claude"]
        assert mc.provider == "bedrock"
        assert mc.aws_region == "us-east-1"
        assert mc.api_key == ""

    def test_bedrock_with_profile(self):
        path = self._write_yaml("""
models:
  br:
    provider: "bedrock"
    model: "test-model"
    aws_region: "us-west-2"
    aws_profile: "my-profile"
""")
        config = Config.from_yaml(path)
        mc = config.models["br"]
        assert mc.aws_profile == "my-profile"
        assert mc.aws_region == "us-west-2"

    def test_mixed_models_config(self):
        path = self._write_yaml("""
models:
  direct:
    api_key: "sk-test"
    api_base: "https://api.anthropic.com"
    model: "claude-sonnet"
    provider: "anthropic"
  bedrock:
    provider: "bedrock"
    model: "us.anthropic.claude-opus-4-6-v1:0"
    aws_region: "us-east-1"
default_model: "direct"
""")
        config = Config.from_yaml(path)

        assert len(config.models) == 2
        assert config.models["direct"].api_key == "sk-test"
        assert config.models["bedrock"].api_key == ""
        assert config.default_model == "direct"

    def test_non_bedrock_still_requires_api_key(self):
        path = self._write_yaml("""
models:
  bad:
    provider: "anthropic"
    model: "some-model"
""")
        with pytest.raises(ValueError, match="missing required field: api_key"):
            Config.from_yaml(path)
