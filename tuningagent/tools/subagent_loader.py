"""
Subagent Loader - Load fixed subagent definitions from YAML files.

Discovers SUBAGENT.yaml files in a subagents directory, similar to how
SkillLoader discovers SKILL.md files for skills.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml


@dataclass
class SubagentConfig:
    """Fixed subagent definition loaded from YAML."""

    name: str
    description: str
    system_prompt: str
    allowed_tools: Optional[List[str]] = None
    max_steps: int = 30
    token_limit: int = 80000


class SubagentLoader:
    """Discovers and loads fixed subagent definitions from SUBAGENT.yaml files."""

    def __init__(self, subagents_dir: str = "./subagents"):
        self.subagents_dir = Path(subagents_dir)
        self.loaded: Dict[str, SubagentConfig] = {}

    def load_file(self, path: Path) -> Optional[SubagentConfig]:
        """Load a single SUBAGENT.yaml file."""
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Failed to load subagent ({path}): {e}")
            return None

        if not isinstance(data, dict):
            print(f"{path} is not a valid YAML mapping")
            return None

        name = data.get("name")
        description = data.get("description")
        system_prompt = data.get("system_prompt")

        if not all([name, description, system_prompt]):
            print(f"{path} missing required fields (name, description, system_prompt)")
            return None

        return SubagentConfig(
            name=name,
            description=description,
            system_prompt=system_prompt,
            allowed_tools=data.get("allowed_tools"),
            max_steps=data.get("max_steps", 30),
            token_limit=data.get("token_limit", 80000),
        )

    def discover(self) -> List[SubagentConfig]:
        """Discover and load all SUBAGENT.yaml files in the subagents directory."""
        configs = []

        if not self.subagents_dir.exists():
            return configs

        for yaml_file in self.subagents_dir.rglob("SUBAGENT.yaml"):
            config = self.load_file(yaml_file)
            if config:
                configs.append(config)
                self.loaded[config.name] = config

        return configs

    def reload(self) -> dict:
        """Reload all subagent definitions. Returns change summary."""
        old_names = set(self.loaded.keys())
        self.loaded.clear()
        self.discover()
        new_names = set(self.loaded.keys())
        return {
            "added": sorted(new_names - old_names),
            "removed": sorted(old_names - new_names),
            "retained": sorted(old_names & new_names),
            "total": len(new_names),
        }
