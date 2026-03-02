"""Model pool for managing multiple LLM clients.

Provides a unified generate() interface compatible with LLMClient so that
Agent can use a ModelPool as a drop-in replacement (duck typing).
"""

import asyncio
import logging
import time

from ..config import ModelConfig
from ..retry import RetryConfig
from ..schema import HealthCheckResult, LLMProvider, LLMResponse, Message, ModelStats
from .llm_wrapper import LLMClient

logger = logging.getLogger(__name__)


class ModelPool:
    """Manages multiple LLMClient instances with stats tracking.

    Implements the same ``generate()`` interface as ``LLMClient`` so that
    ``Agent`` can use it transparently via duck typing.
    """

    def __init__(self):
        self._clients: dict[str, LLMClient] = {}
        self._stats: dict[str, ModelStats] = {}
        self._active_alias: str | None = None

    # ------------------------------------------------------------------
    # Model management
    # ------------------------------------------------------------------

    def add_model(
        self,
        alias: str,
        config: ModelConfig,
        retry_config: RetryConfig | None = None,
    ) -> None:
        """Register a model in the pool.

        Args:
            alias: Short alias for the model (e.g. "claude-sonnet").
            config: Model configuration.
            retry_config: Optional retry configuration.
        """
        provider = (
            LLMProvider.ANTHROPIC
            if config.provider.lower() == "anthropic"
            else LLMProvider.OPENAI
        )
        client = LLMClient(
            api_key=config.api_key,
            provider=provider,
            api_base=config.api_base,
            model=config.model,
            retry_config=retry_config,
        )
        self._clients[alias] = client
        self._stats[alias] = ModelStats(
            model_alias=alias,
            model_name=config.model,
            provider=config.provider,
        )
        logger.info("Added model '%s' (%s) to pool", alias, config.model)

    def set_active(self, alias: str) -> None:
        """Switch the active model.

        Args:
            alias: Alias of the model to activate.

        Raises:
            KeyError: If alias not found in pool.
        """
        if alias not in self._clients:
            raise KeyError(f"Model alias '{alias}' not found in pool. Available: {list(self._clients.keys())}")
        self._active_alias = alias
        logger.info("Switched active model to '%s'", alias)

    def list_models(self) -> list[dict[str, str]]:
        """Return info about all models in the pool.

        Returns:
            List of dicts with keys: alias, model, provider, active.
        """
        result = []
        for alias, client in self._clients.items():
            result.append({
                "alias": alias,
                "model": client.model,
                "provider": str(client.provider.value),
                "active": alias == self._active_alias,
            })
        return result

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self, alias: str | None = None) -> ModelStats | dict[str, ModelStats]:
        """Get stats for one model or all models.

        Args:
            alias: Model alias. If None, returns all stats.

        Returns:
            Single ModelStats or dict of all stats.
        """
        if alias is not None:
            return self._stats[alias]
        return dict(self._stats)

    def get_all_stats_summary(self) -> str:
        """Format all model stats as a table string."""
        if not self._stats:
            return "No models in pool."

        header = f"{'Alias':<16} {'Model':<24} {'Calls':>6} {'Errors':>6} {'Tokens':>10} {'Avg Lat':>8}"
        sep = "-" * len(header)
        lines = [header, sep]

        for alias, stats in self._stats.items():
            marker = " *" if alias == self._active_alias else ""
            avg_lat = f"{stats.avg_latency_s:.2f}s" if stats.call_count else "-"
            lines.append(
                f"{alias + marker:<16} {stats.model_name:<24} {stats.call_count:>6} "
                f"{stats.error_count:>6} {stats.total_tokens:>10} {avg_lat:>8}"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def check_health(self) -> list[HealthCheckResult]:
        """Concurrently check health of all models in the pool.

        Returns:
            List of HealthCheckResult, one per model.
        """

        async def _check_one(alias: str, client: LLMClient) -> HealthCheckResult:
            stats = self._stats[alias]
            start = time.perf_counter()
            try:
                await client.health_check()
                latency_ms = (time.perf_counter() - start) * 1000
                return HealthCheckResult(
                    alias=alias,
                    model_name=stats.model_name,
                    provider=stats.provider,
                    available=True,
                    latency_ms=latency_ms,
                )
            except Exception as e:
                latency_ms = (time.perf_counter() - start) * 1000
                return HealthCheckResult(
                    alias=alias,
                    model_name=stats.model_name,
                    provider=stats.provider,
                    available=False,
                    latency_ms=latency_ms,
                    error=str(e),
                )

        tasks = [_check_one(alias, client) for alias, client in self._clients.items()]
        return list(await asyncio.gather(*tasks))

    # ------------------------------------------------------------------
    # LLMClient-compatible interface (duck typing)
    # ------------------------------------------------------------------

    @property
    def active_alias(self) -> str | None:
        return self._active_alias

    @property
    def active_model_name(self) -> str | None:
        if self._active_alias and self._active_alias in self._clients:
            return self._clients[self._active_alias].model
        return None

    @property
    def model(self) -> str:
        """Compatible with ``LLMClient.model`` — returns the active model name."""
        if self._active_alias and self._active_alias in self._clients:
            return self._clients[self._active_alias].model
        return ""

    @property
    def retry_callback(self):
        """Get retry callback from the active client."""
        if self._active_alias and self._active_alias in self._clients:
            return self._clients[self._active_alias].retry_callback
        return None

    @retry_callback.setter
    def retry_callback(self, value):
        """Propagate retry callback to all clients."""
        for client in self._clients.values():
            client.retry_callback = value

    async def generate(
        self,
        messages: list[Message],
        tools: list | None = None,
    ) -> LLMResponse:
        """Generate a response using the active model, recording stats.

        Args:
            messages: List of conversation messages.
            tools: Optional list of Tool objects or dicts.

        Returns:
            LLMResponse from the active model.

        Raises:
            RuntimeError: If no active model is set.
        """
        if self._active_alias is None or self._active_alias not in self._clients:
            raise RuntimeError("No active model set in ModelPool")

        client = self._clients[self._active_alias]
        stats = self._stats[self._active_alias]

        start = time.perf_counter()
        error_occurred = False
        response: LLMResponse | None = None

        try:
            response = await client.generate(messages, tools)
            return response
        except Exception:
            error_occurred = True
            raise
        finally:
            latency = time.perf_counter() - start
            usage = response.usage if response else None
            stats.record_call(usage, latency, error=error_occurred)
