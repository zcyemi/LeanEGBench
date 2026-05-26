from __future__ import annotations

try:
	import tomllib
except ModuleNotFoundError:  # pragma: no cover
	import tomli as tomllib  # type: ignore[no-redef]

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_VALID_API_WIRES = {"completion", "response", "anthropic"}


def _coerce_bool(value: Any) -> bool:
	if isinstance(value, bool):
		return value
	if isinstance(value, str):
		return value.strip().lower() in {"1", "true", "yes", "on", "enabled", "enable"}
	return bool(value)


def _normalize_api_wire(value: Any) -> str:
	normalized = str(value or "completion").strip().lower() or "completion"
	if normalized == "antropic":
		normalized = "anthropic"
	if normalized not in _VALID_API_WIRES:
		raise ValueError(f"Unsupported api_wire: {normalized}")
	return normalized


@dataclass(slots=True)
class GlobalConfig:
	temperature: float = 0.4
	max_tool_call: int = 25
	max_completion_tokens: int = 4096
	lean_explore_url: str = ""
	mode_tool_max_search_call: int = 50


@dataclass(slots=True)
class ModelConfig:
	name: str
	url: str
	api_key: str
	model_id: str | None = None
	api_wire: str = "completion"
	reasoning_content_pass_back: bool = False
	extra_param: dict[str, Any] = field(default_factory=dict)

	@property
	def request_model_id(self) -> str:
		return (self.model_id or "").strip() or self.name


@dataclass(slots=True)
class AppConfig:
	config: GlobalConfig
	models: list[ModelConfig]

	@classmethod
	def load(cls, env_path: str | Path) -> "AppConfig":
		raw = tomllib.loads(Path(env_path).read_text(encoding="utf-8"))
		cfg = raw.get("config", {})
		global_cfg = GlobalConfig(
			temperature=float(cfg.get("temperature", 0.4)),
			max_tool_call=int(cfg.get("max_tool_call", 100)),
			max_completion_tokens=int(cfg.get("max_completion_tokens", 4096)),
			lean_explore_url=str(cfg.get("lean_explore", {}).get("url", "") or ""),
			mode_tool_max_search_call=int(cfg.get("mode_tool", {}).get("max_search_call", 50)),
		)

		models: list[ModelConfig] = []
		for item in raw.get("model", []):
			models.append(
				ModelConfig(
					name=str(item["name"]),
					model_id=(str(item.get("model_id", "")).strip() or None),
					url=str(item["url"]),
					api_key=str(item["api_key"]),
					api_wire=_normalize_api_wire(item.get("api_wire", "completion")),
					reasoning_content_pass_back=_coerce_bool(item.get("reasoning_content_pass_back", False)),
					extra_param=dict(item.get("extra_param", {})),
				)
			)

		if not models:
			raise ValueError("No models configured in env.toml")

		return cls(config=global_cfg, models=models)

	def get_model(self, model_name: str) -> ModelConfig:
		for model in self.models:
			if model.name == model_name:
				return model
		raise ValueError(f"Unknown model: {model_name}")
