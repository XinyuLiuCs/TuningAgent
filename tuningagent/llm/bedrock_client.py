"""AWS Bedrock LLM client implementation.

Uses the Anthropic SDK's AsyncAnthropicBedrock client which provides
the same messages API but authenticates via AWS SigV4 instead of API keys.
"""

import logging

import anthropic

from ..retry import RetryConfig
from .anthropic_client import AnthropicClient

logger = logging.getLogger(__name__)


class BedrockClient(AnthropicClient):
    """LLM client for Anthropic models hosted on AWS Bedrock.

    Subclasses AnthropicClient — all message conversion, tool conversion,
    response parsing, and retry logic are inherited. Only the underlying
    SDK client is swapped to AsyncAnthropicBedrock.
    """

    def __init__(
        self,
        model: str = "us.anthropic.claude-opus-4-6-v1:0",
        aws_region: str = "",
        aws_profile: str = "",
        retry_config: RetryConfig | None = None,
    ):
        """Initialize Bedrock client.

        Args:
            model: Bedrock model ID (e.g. us.anthropic.claude-opus-4-6-v1:0)
            aws_region: AWS region (defaults to SDK/env config if empty)
            aws_profile: AWS profile name (defaults to SDK/env config if empty)
            retry_config: Optional retry configuration
        """
        # Base class stores api_key, api_base, model, retry_config
        super().__init__(api_key="", api_base="", model=model, retry_config=retry_config)

        # Replace the client with AsyncAnthropicBedrock
        kwargs: dict = {}
        if aws_region:
            kwargs["aws_region"] = aws_region
        if aws_profile:
            kwargs["aws_profile"] = aws_profile

        self.client = anthropic.AsyncAnthropicBedrock(**kwargs)
