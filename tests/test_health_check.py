"""Tests for health check: HealthCheckResult, LLMClient delegation, and ModelPool.check_health()."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from tuningagent.config import ModelConfig
from tuningagent.llm.model_pool import ModelPool
from tuningagent.schema import HealthCheckResult


# ---------------------------------------------------------------------------
# HealthCheckResult data model
# ---------------------------------------------------------------------------


class TestHealthCheckResult:
    def test_available_result(self):
        r = HealthCheckResult(
            alias="m1",
            model_name="test-model",
            provider="openai",
            available=True,
            latency_ms=123.4,
        )
        assert r.available is True
        assert r.error is None
        assert r.latency_ms == 123.4

    def test_failed_result(self):
        r = HealthCheckResult(
            alias="m2",
            model_name="bad-model",
            provider="anthropic",
            available=False,
            latency_ms=50.0,
            error="401 Unauthorized",
        )
        assert r.available is False
        assert r.error == "401 Unauthorized"

    def test_defaults(self):
        r = HealthCheckResult(
            alias="a", model_name="m", provider="openai", available=True
        )
        assert r.latency_ms == 0.0
        assert r.error is None


# ---------------------------------------------------------------------------
# LLMClient.health_check delegation
# ---------------------------------------------------------------------------


class TestLLMClientHealthCheck:
    @pytest.mark.asyncio
    async def test_delegates_to_underlying_client(self):
        from tuningagent.llm.llm_wrapper import LLMClient

        client = LLMClient(
            api_key="sk-test",
            api_base="https://api.example.com",
            model="test-model",
            provider="openai",
        )
        client._client.health_check = AsyncMock(return_value=True)

        result = await client.health_check()

        assert result is True
        client._client.health_check.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_propagates_exception(self):
        from tuningagent.llm.llm_wrapper import LLMClient

        client = LLMClient(
            api_key="sk-test",
            api_base="https://api.example.com",
            model="test-model",
            provider="anthropic",
        )
        client._client.health_check = AsyncMock(side_effect=RuntimeError("connection refused"))

        with pytest.raises(RuntimeError, match="connection refused"):
            await client.health_check()


# ---------------------------------------------------------------------------
# ModelPool.check_health
# ---------------------------------------------------------------------------


def _make_config(**overrides):
    defaults = {
        "api_key": "sk-test",
        "api_base": "https://api.example.com",
        "model": "test-model",
        "provider": "openai",
    }
    defaults.update(overrides)
    return ModelConfig(**defaults)


class TestModelPoolCheckHealth:
    @pytest.mark.asyncio
    async def test_all_healthy(self):
        pool = ModelPool()
        pool.add_model("m1", _make_config(model="model-a"))
        pool.add_model("m2", _make_config(model="model-b"))

        for client in pool._clients.values():
            client.health_check = AsyncMock(return_value=True)

        results = await pool.check_health()

        assert len(results) == 2
        assert all(r.available for r in results)
        assert all(r.latency_ms >= 0 for r in results)

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        pool = ModelPool()
        pool.add_model("good", _make_config(model="model-ok"))
        pool.add_model("bad", _make_config(model="model-fail"))

        pool._clients["good"].health_check = AsyncMock(return_value=True)
        pool._clients["bad"].health_check = AsyncMock(side_effect=RuntimeError("auth error"))

        results = await pool.check_health()

        by_alias = {r.alias: r for r in results}
        assert by_alias["good"].available is True
        assert by_alias["bad"].available is False
        assert "auth error" in by_alias["bad"].error

    @pytest.mark.asyncio
    async def test_empty_pool(self):
        pool = ModelPool()
        results = await pool.check_health()
        assert results == []

    @pytest.mark.asyncio
    async def test_concurrent_execution(self):
        """Two 0.1s checks should complete in well under 0.18s if run concurrently."""
        pool = ModelPool()
        pool.add_model("s1", _make_config(model="slow-a"))
        pool.add_model("s2", _make_config(model="slow-b"))

        async def slow_check():
            await asyncio.sleep(0.1)
            return True

        for client in pool._clients.values():
            client.health_check = AsyncMock(side_effect=slow_check)

        start = time.perf_counter()
        results = await pool.check_health()
        elapsed = time.perf_counter() - start

        assert len(results) == 2
        assert all(r.available for r in results)
        assert elapsed < 0.18, f"Expected concurrent execution, but took {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_result_fields(self):
        pool = ModelPool()
        pool.add_model("m1", _make_config(model="my-model", provider="anthropic"))

        pool._clients["m1"].health_check = AsyncMock(return_value=True)

        results = await pool.check_health()
        r = results[0]

        assert r.alias == "m1"
        assert r.model_name == "my-model"
        assert r.provider == "anthropic"
        assert r.available is True
        assert r.error is None
