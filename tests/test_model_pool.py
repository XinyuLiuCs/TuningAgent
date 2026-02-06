"""Tests for ModelStats, ModelPool, and multi-model Config parsing."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tuningagent.config import Config, ModelConfig
from tuningagent.llm.model_pool import ModelPool
from tuningagent.schema import LLMResponse, ModelStats, TokenUsage


# ---------------------------------------------------------------------------
# ModelStats
# ---------------------------------------------------------------------------


class TestModelStats:
    def test_initial_values(self):
        stats = ModelStats(model_alias="test", model_name="m1", provider="anthropic")
        assert stats.call_count == 0
        assert stats.total_tokens == 0
        assert stats.avg_latency_s == 0.0

    def test_record_call_success(self):
        stats = ModelStats(model_alias="a", model_name="m", provider="openai")
        usage = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        stats.record_call(usage, latency_s=1.5)

        assert stats.call_count == 1
        assert stats.error_count == 0
        assert stats.prompt_tokens == 10
        assert stats.completion_tokens == 20
        assert stats.total_tokens == 30
        assert stats.total_latency_s == 1.5
        assert stats.avg_latency_s == 1.5

    def test_record_call_error(self):
        stats = ModelStats(model_alias="a", model_name="m", provider="anthropic")
        stats.record_call(None, latency_s=0.5, error=True)

        assert stats.call_count == 1
        assert stats.error_count == 1
        assert stats.total_tokens == 0  # No usage on error

    def test_record_multiple_calls(self):
        stats = ModelStats(model_alias="a", model_name="m", provider="anthropic")
        u1 = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        u2 = TokenUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30)
        stats.record_call(u1, latency_s=1.0)
        stats.record_call(u2, latency_s=3.0)

        assert stats.call_count == 2
        assert stats.total_tokens == 45
        assert stats.prompt_tokens == 30
        assert stats.completion_tokens == 15
        assert stats.avg_latency_s == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# ModelPool
# ---------------------------------------------------------------------------


class TestModelPool:
    def _make_config(self, **overrides):
        defaults = {
            "api_key": "sk-test",
            "api_base": "https://api.example.com",
            "model": "test-model",
            "provider": "openai",
        }
        defaults.update(overrides)
        return ModelConfig(**defaults)

    def test_add_and_list(self):
        pool = ModelPool()
        pool.add_model("m1", self._make_config(model="model-a"))
        pool.add_model("m2", self._make_config(model="model-b"))

        models = pool.list_models()
        assert len(models) == 2
        aliases = [m["alias"] for m in models]
        assert "m1" in aliases
        assert "m2" in aliases

    def test_set_active(self):
        pool = ModelPool()
        pool.add_model("m1", self._make_config())
        pool.set_active("m1")
        assert pool.active_alias == "m1"

    def test_set_active_invalid(self):
        pool = ModelPool()
        pool.add_model("m1", self._make_config())
        with pytest.raises(KeyError):
            pool.set_active("nonexistent")

    def test_model_property(self):
        pool = ModelPool()
        pool.add_model("m1", self._make_config(model="my-model"))
        pool.set_active("m1")
        assert pool.model == "my-model"

    def test_model_property_no_active(self):
        pool = ModelPool()
        assert pool.model == ""

    @pytest.mark.asyncio
    async def test_generate_delegates_and_records_stats(self):
        pool = ModelPool()
        cfg = self._make_config(model="test-model")
        pool.add_model("m1", cfg)
        pool.set_active("m1")

        mock_response = LLMResponse(
            content="hello",
            finish_reason="stop",
            usage=TokenUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8),
        )

        with patch.object(pool._clients["m1"], "generate", new_callable=AsyncMock, return_value=mock_response):
            result = await pool.generate([], None)

        assert result.content == "hello"
        stats = pool.get_stats("m1")
        assert stats.call_count == 1
        assert stats.total_tokens == 8
        assert stats.error_count == 0
        assert stats.total_latency_s > 0

    @pytest.mark.asyncio
    async def test_generate_records_error(self):
        pool = ModelPool()
        cfg = self._make_config()
        pool.add_model("m1", cfg)
        pool.set_active("m1")

        with patch.object(pool._clients["m1"], "generate", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                await pool.generate([], None)

        stats = pool.get_stats("m1")
        assert stats.call_count == 1
        assert stats.error_count == 1

    @pytest.mark.asyncio
    async def test_generate_no_active_model(self):
        pool = ModelPool()
        pool.add_model("m1", self._make_config())
        # Don't call set_active
        with pytest.raises(RuntimeError, match="No active model"):
            await pool.generate([], None)

    def test_get_all_stats(self):
        pool = ModelPool()
        pool.add_model("m1", self._make_config(model="a"))
        pool.add_model("m2", self._make_config(model="b"))
        all_stats = pool.get_stats()
        assert isinstance(all_stats, dict)
        assert "m1" in all_stats
        assert "m2" in all_stats

    def test_get_all_stats_summary(self):
        pool = ModelPool()
        pool.add_model("m1", self._make_config(model="model-a"))
        pool.set_active("m1")
        summary = pool.get_all_stats_summary()
        assert "model-a" in summary
        assert "Alias" in summary

    def test_retry_callback_propagation(self):
        pool = ModelPool()
        pool.add_model("m1", self._make_config())
        pool.add_model("m2", self._make_config())

        cb = lambda exc, attempt: None  # noqa: E731
        pool.retry_callback = cb

        assert pool._clients["m1"].retry_callback is cb
        assert pool._clients["m2"].retry_callback is cb


# ---------------------------------------------------------------------------
# Config multi-model parsing
# ---------------------------------------------------------------------------


class TestConfigMultiModel:
    def _write_yaml(self, content: str) -> Path:
        """Write YAML content to a temp file and return its path."""
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(content)
        f.close()
        return Path(f.name)

    def test_legacy_flat_format(self):
        path = self._write_yaml(
            """
api_key: "sk-test-key"
api_base: "https://api.minimax.io"
model: "MiniMax-M2.1"
provider: "anthropic"
"""
        )
        config = Config.from_yaml(path)

        assert len(config.models) == 1
        assert "default" in config.models
        assert config.default_model == "default"
        assert config.models["default"].api_key == "sk-test-key"
        assert config.models["default"].model == "MiniMax-M2.1"
        # Top-level llm config should match
        assert config.llm.api_key == "sk-test-key"

    def test_multi_model_format(self):
        path = self._write_yaml(
            """
models:
  fast:
    api_key: "sk-fast"
    api_base: "https://api.example.com"
    model: "fast-model"
    provider: "openai"
  smart:
    api_key: "sk-smart"
    api_base: "https://api.example.com"
    model: "smart-model"
    provider: "anthropic"
default_model: "smart"
"""
        )
        config = Config.from_yaml(path)

        assert len(config.models) == 2
        assert config.default_model == "smart"
        assert config.models["fast"].api_key == "sk-fast"
        assert config.models["smart"].model == "smart-model"
        # Top-level llm config should use default model's values
        assert config.llm.api_key == "sk-smart"
        assert config.llm.model == "smart-model"

    def test_multi_model_default_first_key(self):
        """When default_model is not specified, use the first key."""
        path = self._write_yaml(
            """
models:
  alpha:
    api_key: "sk-alpha"
    model: "alpha-model"
  beta:
    api_key: "sk-beta"
    model: "beta-model"
"""
        )
        config = Config.from_yaml(path)
        assert config.default_model == "alpha"

    def test_multi_model_missing_api_key(self):
        path = self._write_yaml(
            """
models:
  bad:
    model: "some-model"
"""
        )
        with pytest.raises(ValueError, match="missing required field: api_key"):
            Config.from_yaml(path)
