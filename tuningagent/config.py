"""Configuration management module

Provides unified configuration loading and management functionality
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class RetryConfig(BaseModel):
    """Retry configuration"""

    enabled: bool = True
    max_retries: int = 3
    initial_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0


class ModelConfig(BaseModel):
    """Single model configuration for multi-model pool."""

    api_key: str = ""
    api_base: str = "https://api.minimax.io"
    model: str = "MiniMax-M2.1"
    provider: str = "anthropic"  # "anthropic", "openai", or "bedrock"
    aws_region: str = ""
    aws_profile: str = ""


class LLMConfig(BaseModel):
    """LLM configuration"""

    api_key: str
    api_base: str = "https://api.minimax.io"
    model: str = "MiniMax-M2.1"
    provider: str = "anthropic"  # "anthropic" or "openai"
    retry: RetryConfig = Field(default_factory=RetryConfig)


class AgentConfig(BaseModel):
    """Agent configuration"""

    max_steps: int = 50
    workspace_dir: str = "./workspace"
    system_prompt_path: str = "system_prompt.md"


class MCPConfig(BaseModel):
    """MCP (Model Context Protocol) timeout configuration"""

    connect_timeout: float = 10.0  # Connection timeout (seconds)
    execute_timeout: float = 60.0  # Tool execution timeout (seconds)
    sse_read_timeout: float = 120.0  # SSE read timeout (seconds)


class ToolsConfig(BaseModel):
    """Tools configuration"""

    # Basic tools (file operations, bash)
    enable_file_tools: bool = True
    enable_bash: bool = True
    enable_note: bool = True

    # Skills
    enable_skills: bool = True
    skills_dir: str = "./skills"

    # MCP tools
    enable_mcp: bool = True
    mcp_config_path: str = "mcp.json"
    mcp: MCPConfig = Field(default_factory=MCPConfig)


class Config(BaseModel):
    """Main configuration class"""

    llm: LLMConfig
    agent: AgentConfig
    tools: ToolsConfig
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    default_model: str = "default"

    @classmethod
    def load(cls) -> "Config":
        """Load configuration from the default search path."""
        config_path = cls.get_default_config_path()
        if not config_path.exists():
            raise FileNotFoundError("Configuration file not found. Run scripts/setup-config.sh or place config.yaml in tuningagent/config/.")
        return cls.from_yaml(config_path)

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> "Config":
        """Load configuration from YAML file

        Args:
            config_path: Configuration file path

        Returns:
            Config instance

        Raises:
            FileNotFoundError: Configuration file does not exist
            ValueError: Invalid configuration format or missing required fields
        """
        config_path = Path(config_path)

        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file does not exist: {config_path}")

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data:
            raise ValueError("Configuration file is empty")

        # Parse multi-model configuration
        models: dict[str, ModelConfig] = {}
        default_model = "default"

        if "models" in data and isinstance(data["models"], dict):
            # New multi-model format
            for alias, model_data in data["models"].items():
                if not isinstance(model_data, dict):
                    raise ValueError(f"Invalid model config for alias '{alias}'")
                model_provider = model_data.get("provider", "anthropic").lower()
                # api_key is required for non-bedrock providers
                if model_provider != "bedrock":
                    if "api_key" not in model_data or not model_data["api_key"]:
                        raise ValueError(f"Model '{alias}' missing required field: api_key")
                models[alias] = ModelConfig(
                    api_key=model_data.get("api_key", ""),
                    api_base=model_data.get("api_base", "https://api.minimax.io"),
                    model=model_data.get("model", "MiniMax-M2.1"),
                    provider=model_data.get("provider", "anthropic"),
                    aws_region=model_data.get("aws_region", ""),
                    aws_profile=model_data.get("aws_profile", ""),
                )
            default_model = data.get("default_model", next(iter(models)))

            # For backward compat, use the default model's fields as the top-level llm config
            dm = models[default_model]
            api_key = data.get("api_key", dm.api_key)
            api_base = data.get("api_base", dm.api_base)
            model_name = data.get("model", dm.model)
            provider = data.get("provider", dm.provider)
        else:
            # Legacy flat format — require top-level api_key
            if "api_key" not in data:
                raise ValueError("Configuration file missing required field: api_key")
            if not data["api_key"] or data["api_key"] == "YOUR_API_KEY_HERE":
                raise ValueError("Please configure a valid API Key")

            api_key = data["api_key"]
            api_base = data.get("api_base", "https://api.minimax.io")
            model_name = data.get("model", "MiniMax-M2.1")
            provider = data.get("provider", "anthropic")

            # Build a single-model pool from flat fields
            models["default"] = ModelConfig(
                api_key=api_key,
                api_base=api_base,
                model=model_name,
                provider=provider,
            )
            default_model = "default"

        # Parse retry configuration
        retry_data = data.get("retry", {})
        retry_config = RetryConfig(
            enabled=retry_data.get("enabled", True),
            max_retries=retry_data.get("max_retries", 3),
            initial_delay=retry_data.get("initial_delay", 1.0),
            max_delay=retry_data.get("max_delay", 60.0),
            exponential_base=retry_data.get("exponential_base", 2.0),
        )

        llm_config = LLMConfig(
            api_key=api_key,
            api_base=api_base,
            model=model_name,
            provider=provider,
            retry=retry_config,
        )

        # Parse Agent configuration
        agent_config = AgentConfig(
            max_steps=data.get("max_steps", 50),
            workspace_dir=data.get("workspace_dir", "./workspace"),
            system_prompt_path=data.get("system_prompt_path", "system_prompt.md"),
        )

        # Parse tools configuration
        tools_data = data.get("tools", {})

        # Parse MCP configuration
        mcp_data = tools_data.get("mcp", {})
        mcp_config = MCPConfig(
            connect_timeout=mcp_data.get("connect_timeout", 10.0),
            execute_timeout=mcp_data.get("execute_timeout", 60.0),
            sse_read_timeout=mcp_data.get("sse_read_timeout", 120.0),
        )

        tools_config = ToolsConfig(
            enable_file_tools=tools_data.get("enable_file_tools", True),
            enable_bash=tools_data.get("enable_bash", True),
            enable_note=tools_data.get("enable_note", True),
            enable_skills=tools_data.get("enable_skills", True),
            skills_dir=tools_data.get("skills_dir", "./skills"),
            enable_mcp=tools_data.get("enable_mcp", True),
            mcp_config_path=tools_data.get("mcp_config_path", "mcp.json"),
            mcp=mcp_config,
        )

        return cls(
            llm=llm_config,
            agent=agent_config,
            tools=tools_config,
            models=models,
            default_model=default_model,
        )

    @staticmethod
    def get_package_dir() -> Path:
        """Get the package installation directory

        Returns:
            Path to the tuningagent package directory
        """
        # Get the directory where this config.py file is located
        return Path(__file__).parent

    @classmethod
    def find_config_file(cls, filename: str) -> Path | None:
        """Find configuration file with priority order

        Search for config file in the following order of priority:
        1) tuningagent/config/{filename} in current directory (development mode)
        2) ~/.mini-agent/config/{filename} in user home directory
        3) {package}/tuningagent/config/{filename} in package installation directory

        Args:
            filename: Configuration file name (e.g., "config.yaml", "mcp.json", "system_prompt.md")

        Returns:
            Path to found config file, or None if not found
        """
        # Priority 1: Development mode - current directory's config/ subdirectory
        dev_config = Path.cwd() / "tuningagent" / "config" / filename
        if dev_config.exists():
            return dev_config

        # Priority 2: User config directory
        user_config = Path.home() / ".mini-agent" / "config" / filename
        if user_config.exists():
            return user_config

        # Priority 3: Package installation directory's config/ subdirectory
        package_config = cls.get_package_dir() / "config" / filename
        if package_config.exists():
            return package_config

        return None

    @classmethod
    def get_default_config_path(cls) -> Path:
        """Get the default config file path with priority search

        Returns:
            Path to config.yaml (prioritizes: dev config/ > user config/ > package config/)
        """
        config_path = cls.find_config_file("config.yaml")
        if config_path:
            return config_path

        # Fallback to package config directory for error message purposes
        return cls.get_package_dir() / "config" / "config.yaml"
