from __future__ import annotations

import threading
import json
from typing import Any

from runner.api_common import ApiEventLogger, GenerationResult, ToolLoopResult, client_cache_key, emit_api_event, extract_reasoning_text, normalize_base_url, safe_json_object_loads, usage_value
from runner.config import GlobalConfig, ModelConfig
from runner.tool import TOOL_SCHEMA_RESPONSE, lean_explore_search

try:
	import anthropic
except ModuleNotFoundError:  # pragma: no cover
	anthropic = None


_THREAD_LOCAL = threading.local()


def _client_cache() -> dict[tuple[str, str, str], Any]:
	cache = getattr(_THREAD_LOCAL, "clients", None)
	if cache is None:
		cache = {}
		_THREAD_LOCAL.clients = cache
	return cache


def get_antropic_client(model_config: ModelConfig) -> Any:
	if anthropic is None:  # pragma: no cover
		raise RuntimeError("anthropic package is not installed")
	key = client_cache_key(model_config)
	cache = _client_cache()
	client = cache.get(key)
	if client is None:
		client = anthropic.Anthropic(api_key=model_config.api_key, base_url=normalize_base_url(model_config.url))
		cache[key] = client
	return client


def clear_antropic_client_cache() -> None:
	_THREAD_LOCAL.clients = {}


def _anthropic_reasoning_text(response: Any) -> str:
	chunks: list[str] = []
	for block in getattr(response, "content", []) or []:
		if getattr(block, "type", "") not in {"thinking", "redacted_thinking"}:
			continue
		extracted = extract_reasoning_text(block)
		if extracted:
			chunks.append(extracted)
	if not chunks:
		return ""
	return "\n".join(chunks)


def request_antropic_once(
	model_config: ModelConfig,
	global_config: GlobalConfig,
	system_prompt: str,
	user_prompt: str,
	*,
	event_logger: ApiEventLogger | None = None,
) -> GenerationResult:
	client = get_antropic_client(model_config)
	request: dict[str, object] = {
		"model": model_config.request_model_id,
		"max_tokens": global_config.max_completion_tokens,
		"temperature": global_config.temperature,
		"system": system_prompt,
		"messages": [
			{"role": "user", "content": user_prompt},
		],
	}
	request.update(model_config.extra_param)
	response = client.messages.create(**request)
	content_parts = [
		str(block.text).strip()
		for block in getattr(response, "content", [])
		if getattr(block, "type", "") == "text" and str(block.text).strip()
	]
	text = "\n".join(content_parts).strip()
	usage = getattr(response, "usage", None)
	input_tokens = int(usage_value(usage, "input_tokens") or 0)
	output_tokens = int(usage_value(usage, "output_tokens") or 0)
	emit_api_event(
		event_logger,
		"llm_round",
		step=1,
		wire="anthropic",
		response_id=str(getattr(response, "id", "") or "") or None,
		input_tokens=input_tokens,
		input_cached_tokens=0,
		output_tokens=output_tokens,
		total_tokens=input_tokens + output_tokens,
		reasoning=_anthropic_reasoning_text(response),
		output_text=text,
	)
	return GenerationResult(
		text=text,
		wire="anthropic",
		input_tokens=input_tokens,
		input_cached_tokens=0,
		output_tokens=output_tokens,
		total_tokens=input_tokens + output_tokens,
		response_id=str(getattr(response, "id", "") or "") or None,
	)


def _anthropic_text_and_calls(response: Any) -> tuple[str, list[tuple[str, str, str]]]:
	text_parts: list[str] = []
	calls: list[tuple[str, str, str]] = []
	for block in getattr(response, "content", []) or []:
		block_type = getattr(block, "type", "")
		if block_type == "text":
			text = str(getattr(block, "text", "") or "").strip()
			if text:
				text_parts.append(text)
		elif block_type == "tool_use":
			name = str(getattr(block, "name", "") or "")
			call_id = str(getattr(block, "id", "") or "")
			arguments = json.dumps(getattr(block, "input", {}) or {}, ensure_ascii=False)
			if name:
				calls.append((call_id, name, arguments))
	return "\n".join(text_parts).strip(), calls


def request_antropic_tool_loop(
	model_config: ModelConfig,
	global_config: GlobalConfig,
	system_prompt: str,
	user_prompt: str,
	max_search_call: int,
	*,
	event_logger: ApiEventLogger | None = None,
) -> ToolLoopResult:
	client = get_antropic_client(model_config)
	messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
	input_tokens = 0
	output_tokens = 0
	response_id: str | None = None
	tool_calls = 0
	tool_schema = {
		"name": "lean_explore",
		"description": TOOL_SCHEMA_RESPONSE["description"],
		"input_schema": TOOL_SCHEMA_RESPONSE["parameters"],
	}
	for step in range(1, max_search_call + 2):
		request: dict[str, Any] = {
			"model": model_config.request_model_id,
			"max_tokens": global_config.max_completion_tokens,
			"temperature": global_config.temperature,
			"system": system_prompt,
			"messages": messages,
			"tools": [tool_schema],
		}
		request.update(model_config.extra_param)
		response = client.messages.create(**request)
		response_id = str(getattr(response, "id", "") or "") or response_id
		usage = getattr(response, "usage", None)
		delta_input = int(usage_value(usage, "input_tokens") or 0)
		delta_output = int(usage_value(usage, "output_tokens") or 0)
		input_tokens += delta_input
		output_tokens += delta_output
		text, calls = _anthropic_text_and_calls(response)
		emit_api_event(
			event_logger,
			"llm_round",
			step=step,
			wire="anthropic",
			response_id=response_id,
			input_tokens=delta_input,
			input_cached_tokens=0,
			output_tokens=delta_output,
			total_tokens=delta_input + delta_output,
			reasoning=_anthropic_reasoning_text(response),
			output_text=text,
		)
		assistant_content = []
		if text:
			assistant_content.append({"type": "text", "text": text})
		for call_id, name, arguments in calls:
			assistant_content.append({"type": "tool_use", "id": call_id, "name": name, "input": safe_json_object_loads(arguments)})
		if assistant_content:
			messages.append({"role": "assistant", "content": assistant_content})
		if not calls:
			return ToolLoopResult(text=text, wire="anthropic", input_tokens=input_tokens, input_cached_tokens=0, output_tokens=output_tokens, total_tokens=input_tokens + output_tokens, response_id=response_id, tool_calls=tool_calls)
		if tool_calls + len(calls) > max_search_call:
			return ToolLoopResult(text=text, wire="anthropic", input_tokens=input_tokens, input_cached_tokens=0, output_tokens=output_tokens, total_tokens=input_tokens + output_tokens, response_id=response_id, tool_calls=tool_calls)
		tool_results: list[dict[str, Any]] = []
		for call_id, name, arguments in calls:
			if name != "lean_explore":
				continue
			tool_calls += 1
			payload = safe_json_object_loads(arguments)
			emit_api_event(event_logger, "tool_call", step=step, wire="anthropic", tool_name=name, arguments=payload)
			result = lean_explore_search(global_config.lean_explore_url, str(payload.get("query", "")), int(payload.get("limit", 10) or 10))
			emit_api_event(event_logger, "tool_result", step=step, wire="anthropic", tool_name=name, result=result)
			tool_results.append({"type": "tool_result", "tool_use_id": call_id, "content": json.dumps(result, ensure_ascii=False)})
		messages.append({"role": "user", "content": tool_results})
	return ToolLoopResult(text="", wire="anthropic", input_tokens=input_tokens, input_cached_tokens=0, output_tokens=output_tokens, total_tokens=input_tokens + output_tokens, response_id=response_id, tool_calls=tool_calls)