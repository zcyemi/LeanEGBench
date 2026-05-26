from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from runner.config import ModelConfig


VERIFY_URL_DEFAULT = "http://localhost:8578/verify"
_CODE_FENCE_RE = re.compile(r"```(?:lean4?|)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_LEAN_TAG_RE = re.compile(r"<lean>\s*(.*?)\s*</lean>", re.IGNORECASE | re.DOTALL)


@dataclass(slots=True)
class GenerationResult:
	text: str
	wire: str
	input_tokens: int = 0
	input_cached_tokens: int = 0
	output_tokens: int = 0
	total_tokens: int = 0
	response_id: str | None = None


@dataclass(slots=True)
class ToolLoopResult(GenerationResult):
	tool_calls: int = 0


ApiEventLogger = Callable[[dict[str, Any]], None]


def safe_json_object_loads(raw: str | None) -> dict[str, Any]:
	if not raw:
		return {}
	try:
		payload = json.loads(raw)
	except json.JSONDecodeError:
		return {}
	return payload if isinstance(payload, dict) else {}


def normalize_base_url(url: str) -> str:
	cleaned = url.rstrip("/")
	for suffix in ("/chat/completions", "/responses"):
		if cleaned.endswith(suffix):
			return cleaned[: -len(suffix)]
	return cleaned


def usage_value(usage: Any, *names: str) -> Any:
	for name in names:
		if isinstance(usage, dict):
			value = usage.get(name)
		else:
			value = getattr(usage, name, None)
		if value is not None:
			return value
	return None


def cached_input_tokens(usage: Any) -> int:
	for details_name in ("input_tokens_details", "prompt_tokens_details"):
		details = usage_value(usage, details_name)
		if details is None:
			continue
		cached = usage_value(details, "cached_tokens")
		if cached is not None:
			return int(cached or 0)
	return 0


def usage_delta(usage: Any) -> tuple[int, int, int, int]:
	if usage is None:
		return 0, 0, 0, 0
	return (
		int(usage_value(usage, "input_tokens", "prompt_tokens") or 0),
		cached_input_tokens(usage),
		int(usage_value(usage, "output_tokens", "completion_tokens") or 0),
		int(usage_value(usage, "total_tokens") or 0),
	)


def emit_api_event(logger: ApiEventLogger | None, event: str, **fields: Any) -> None:
	if logger is None:
		return
	logger({"event": event, **fields})


def extract_reasoning_text(value: Any) -> str:
	parts: list[str] = []

	def _visit(node: Any) -> None:
		if node is None:
			return
		if isinstance(node, str):
			stripped = node.strip()
			if stripped:
				parts.append(stripped)
			return
		if isinstance(node, (int, float, bool)):
			return
		if isinstance(node, list):
			for item in node:
				_visit(item)
			return
		if isinstance(node, dict):
			for key in ("reasoning_content", "reasoning", "summary", "text", "content"):
				if key in node:
					_visit(node[key])
			return
		for attr in ("reasoning_content", "reasoning", "summary", "text", "content"):
			if hasattr(node, attr):
				_visit(getattr(node, attr))

	_visit(value)
	if not parts:
		return ""
	return "\n".join(dict.fromkeys(parts))


def normalize_thinking(value: Any) -> Any:
	if isinstance(value, str):
		normalized = value.strip().lower()
		if normalized in {"enable", "enabled"}:
			return {"type": "enabled"}
		if normalized in {"disable", "disabled"}:
			return {"type": "disabled"}
		return {"type": normalized}
	if isinstance(value, bool):
		return {"type": "enabled" if value else "disabled"}
	if isinstance(value, dict):
		copied = dict(value)
		if "type" in copied:
			return copied
		if "enabled" in copied:
			enabled = copied.get("enabled")
			if isinstance(enabled, str):
				enabled = enabled.strip().lower() in {"enable", "enabled", "true", "1", "yes"}
			copied["type"] = "enabled" if bool(enabled) else "disabled"
			copied.pop("enabled", None)
		return copied
	return value


def apply_extra_params(request: dict[str, Any], model_config: ModelConfig) -> None:
	extra = dict(model_config.extra_param)
	if not extra:
		return

	passthrough_keys = {
		"max_tokens",
		"top_p",
		"frequency_penalty",
		"presence_penalty",
		"seed",
		"stop",
		"timeout",
		"service_tier",
		"reasoning",
		"reasoning_effort",
	}

	extra_body: dict[str, Any] = {}
	for key, value in extra.items():
		if key in passthrough_keys and key not in request:
			request[key] = value
		else:
			extra_body[key] = value

	if "thinking" in extra_body:
		extra_body["thinking"] = normalize_thinking(extra_body["thinking"])

	if extra_body:
		request["extra_body"] = extra_body


def client_cache_key(model_config: ModelConfig) -> tuple[str, str, str]:
	return (model_config.name, model_config.api_key, normalize_base_url(model_config.url))


def extract_lean_code(text: str) -> str:
	text = text.strip()
	match = _CODE_FENCE_RE.search(text)
	if match:
		return match.group(1).strip()
	match = _LEAN_TAG_RE.search(text)
	if match:
		return match.group(1).strip()
	return text


def normalize_whitespace(text: str) -> str:
	return " ".join(text.split())


def protected_statement_prefix(source_text: str) -> str:
	for marker in (":= by", ":=by"):
		index = source_text.find(marker)
		if index != -1:
			return source_text[: index + len(marker)]
	return source_text.strip()


def statement_is_preserved(source_text: str, candidate_text: str) -> bool:
	source = normalize_whitespace(protected_statement_prefix(source_text))
	candidate = normalize_whitespace(extract_lean_code(candidate_text))
	return bool(source) and source in candidate


def verify_problem_code(code: str, verify_url: str = VERIFY_URL_DEFAULT, timeout: float = 120.0) -> dict[str, Any]:
	payload = json.dumps({"code": code}, ensure_ascii=False).encode("utf-8")
	request = urllib.request.Request(
		verify_url,
		data=payload,
		headers={"Accept": "application/json", "Content-Type": "application/json"},
		method="POST",
	)
	try:
		with urllib.request.urlopen(request, timeout=timeout) as response:
			body = response.read()
			return json.loads(body.decode("utf-8"))
	except urllib.error.HTTPError as exc:
		detail = exc.read().decode("utf-8", errors="replace")
		raise RuntimeError(f"HTTP {exc.code} calling {verify_url}: {detail or exc.reason}") from exc
	except urllib.error.URLError as exc:
		raise RuntimeError(f"Failed to connect to {verify_url}: {exc.reason}") from exc
	except json.JSONDecodeError as exc:
		raise RuntimeError(f"Invalid JSON returned by {verify_url}: {exc}") from exc


def extract_verification_error_messages(response: dict[str, Any]) -> list[str]:
	diagnostics = response.get("diagnostics")
	items = diagnostics.get("diagnostics") if isinstance(diagnostics, dict) else None
	messages: list[str] = []
	if isinstance(items, list):
		for item in items:
			if not isinstance(item, dict):
				continue
			if str(item.get("severity", "")).lower() != "error":
				continue
			message = item.get("message")
			if message:
				messages.append(str(message))
	if messages:
		return messages
	if response.get("error"):
		return [str(response.get("error"))]
	return []


def extract_verification_success(response: dict[str, Any]) -> bool:
	sorries = response.get("sorries")
	sorry_count = len(sorries) if isinstance(sorries, list) else 0
	return sorry_count == 0 and not extract_verification_error_messages(response) and response.get("ok") is True


def extract_verification_error(response: dict[str, Any]) -> str:
	messages = extract_verification_error_messages(response)
	if messages:
		return "\n".join(messages)
	return json.dumps(response, ensure_ascii=False)