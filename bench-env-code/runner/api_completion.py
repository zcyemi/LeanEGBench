from __future__ import annotations

import json
import threading
from typing import Any

from openai import OpenAI

from runner.api_common import ApiEventLogger, GenerationResult, ToolLoopResult, apply_extra_params, client_cache_key, emit_api_event, extract_reasoning_text, normalize_base_url, safe_json_object_loads, usage_delta
from runner.config import GlobalConfig, ModelConfig
from runner.tool import TOOL_SCHEMA_COMPLETION, lean_explore_search


_THREAD_LOCAL = threading.local()
_ENABLE_COMPLETION_STREAMING = False


def _client_cache() -> dict[tuple[str, str, str], OpenAI]:
	cache = getattr(_THREAD_LOCAL, "clients", None)
	if cache is None:
		cache = {}
		_THREAD_LOCAL.clients = cache
	return cache


def get_completion_client(model_config: ModelConfig) -> OpenAI:
	key = client_cache_key(model_config)
	cache = _client_cache()
	client = cache.get(key)
	if client is None:
		client = OpenAI(api_key=model_config.api_key, base_url=normalize_base_url(model_config.url))
		cache[key] = client
	return client


def clear_completion_client_cache() -> None:
	_THREAD_LOCAL.clients = {}


def _stream_completion_response(stream: object) -> GenerationResult:
	chunks: list[str] = []
	last_usage: object | None = None
	response_id: str | None = None
	for chunk in stream:
		response_id = str(getattr(chunk, "id", "") or "") or response_id
		last_usage = getattr(chunk, "usage", None) or last_usage
		for choice in getattr(chunk, "choices", []) or []:
			delta = getattr(choice, "delta", None)
			content = getattr(delta, "content", None)
			if isinstance(content, str) and content:
				chunks.append(content)
	input_tokens, input_cached_tokens, output_tokens, total_tokens = usage_delta(last_usage)
	return GenerationResult(
		text="".join(chunks).strip(),
		wire="completion",
		input_tokens=input_tokens,
		input_cached_tokens=input_cached_tokens,
		output_tokens=output_tokens,
		total_tokens=total_tokens,
		response_id=response_id,
	)


def _patch_completion_request(request: dict[str, object], model_config: ModelConfig, global_config: GlobalConfig) -> None:
	apply_extra_params(request, model_config)
	if "deepseek" in model_config.name:
		request["max_tokens"] = global_config.max_completion_tokens
	if "kimi" in model_config.name.lower():
		request.pop("reasoning", None)
	if "grok" in model_config.name.lower():
		request.pop("reasoning", None)


def _completion_reasoning_text(message: Any) -> str:
	chunks: list[str] = []
	for attr in ("reasoning_content", "reasoning"):
		value = getattr(message, attr, None)
		if value is None:
			continue
		extracted = extract_reasoning_text(value)
		if extracted:
			chunks.append(extracted)
	if not chunks:
		return ""
	return "\n".join(chunks)


def request_completion_once(
	model_config: ModelConfig,
	global_config: GlobalConfig,
	system_prompt: str,
	user_prompt: str,
	*,
	event_logger: ApiEventLogger | None = None,
) -> GenerationResult:
	client = get_completion_client(model_config)
	request: dict[str, object] = {
		"model": model_config.request_model_id,
		"messages": [
			{"role": "system", "content": system_prompt},
			{"role": "user", "content": user_prompt},
		],
		"temperature": global_config.temperature,
		"max_completion_tokens": global_config.max_completion_tokens,
	}
	_patch_completion_request(request, model_config, global_config)

	if _ENABLE_COMPLETION_STREAMING:
		request["stream"] = True
		request.setdefault("stream_options", {"include_usage": True})
		return _stream_completion_response(client.chat.completions.create(**request))
	response = client.chat.completions.create(**request)
	message = response.choices[0].message
	text = str(getattr(message, "content", "") or "").strip()
	input_tokens, input_cached_tokens, output_tokens, total_tokens = usage_delta(getattr(response, "usage", None))
	emit_api_event(
		event_logger,
		"llm_round",
		step=1,
		wire="completion",
		response_id=str(getattr(response, "id", "") or "") or None,
		input_tokens=input_tokens,
		input_cached_tokens=input_cached_tokens,
		output_tokens=output_tokens,
		total_tokens=total_tokens,
		reasoning=_completion_reasoning_text(message),
		output_text=text,
	)
	return GenerationResult(
		text=text,
		wire="completion",
		input_tokens=input_tokens,
		input_cached_tokens=input_cached_tokens,
		output_tokens=output_tokens,
		total_tokens=total_tokens,
		response_id=str(getattr(response, "id", "") or "") or None,
	)


def _completion_message_text(message: Any) -> str:
	content = getattr(message, "content", None)
	if isinstance(content, str):
		return content.strip()
	if isinstance(content, list):
		parts: list[str] = []
		for item in content:
			text = getattr(item, "text", None)
			if isinstance(text, str) and text.strip():
				parts.append(text.strip())
		return "\n".join(parts)
	return ""


def _completion_assistant_entry(message: Any, pass_back_reasoning: bool) -> dict[str, Any]:
	assistant_entry: dict[str, Any] = {"role": "assistant", "tool_calls": []}
	content_text = _completion_message_text(message)
	if content_text:
		assistant_entry["content"] = content_text
	reasoning_content = getattr(message, "reasoning_content", None)
	if pass_back_reasoning and reasoning_content is not None:
		assistant_entry["reasoning_content"] = reasoning_content
	tool_call_entries = getattr(message, "tool_calls", None)
	if tool_call_entries:
		assistant_entry["tool_calls"] = [call.model_dump() if hasattr(call, "model_dump") else call for call in tool_call_entries]
	return assistant_entry


def _completion_tool_calls(message: Any) -> list[tuple[str, str, str]]:
	results: list[tuple[str, str, str]] = []
	for call in getattr(message, "tool_calls", []) or []:
		function = getattr(call, "function", None)
		name = str(getattr(function, "name", "") or "")
		arguments = str(getattr(function, "arguments", "") or "")
		call_id = str(getattr(call, "id", "") or "")
		if name:
			results.append((call_id, name, arguments))
	return results


def request_completion_tool_loop(
	model_config: ModelConfig,
	global_config: GlobalConfig,
	system_prompt: str,
	user_prompt: str,
	max_search_call: int,
	*,
	event_logger: ApiEventLogger | None = None,
) -> ToolLoopResult:
	client = get_completion_client(model_config)
	messages: list[dict[str, Any]] = [
		{"role": "system", "content": system_prompt},
		{"role": "user", "content": user_prompt},
	]
	input_tokens = 0
	input_cached_tokens = 0
	output_tokens = 0
	total_tokens = 0
	response_id: str | None = None
	tool_calls = 0
	max_steps = max(global_config.max_tool_call, max_search_call) + 2

	exceed_step = 0
	for step in range(1, max_steps + 1):
		request: dict[str, Any] = {
			"model": model_config.request_model_id,
			"messages": messages,
			"temperature": global_config.temperature,
			"max_completion_tokens": global_config.max_completion_tokens,
			"tools": [TOOL_SCHEMA_COMPLETION],
			"tool_choice": "auto",
		}
		_patch_completion_request(request, model_config, global_config)

		if tool_calls >= global_config.max_tool_call:
			exceed_step+=1
		
		if exceed_step >= 3:
			break

		response = client.chat.completions.create(**request)
		response_id = str(getattr(response, "id", "") or "") or response_id
		delta = usage_delta(getattr(response, "usage", None))
		input_tokens += delta[0]
		input_cached_tokens += delta[1]
		output_tokens += delta[2]
		total_tokens += delta[3]
		message = response.choices[0].message
		content_text = _completion_message_text(message)
		emit_api_event(
			event_logger,
			"llm_round",
			step=step,
			wire="completion",
			response_id=response_id,
			input_tokens=delta[0],
			input_cached_tokens=delta[1],
			output_tokens=delta[2],
			total_tokens=delta[3],
			reasoning=_completion_reasoning_text(message),
			output_text=content_text,
		)
		assistant_entry = _completion_assistant_entry(message, model_config.reasoning_content_pass_back)
		messages.append(assistant_entry)
		calls = _completion_tool_calls(message)
		if not calls:
			return ToolLoopResult(text=content_text, wire="completion", input_tokens=input_tokens, input_cached_tokens=input_cached_tokens, output_tokens=output_tokens, total_tokens=total_tokens, response_id=response_id, tool_calls=tool_calls)
		for call_id, name, arguments in calls:
			if name != "lean_explore":
				continue
			tool_calls += 1
			payload = safe_json_object_loads(arguments)
			emit_api_event(event_logger, "tool_call", step=step, wire="completion", tool_name=name, arguments=payload)
			result = lean_explore_search(
				global_config.lean_explore_url,
				str(payload.get("query", "")),
				int(payload.get("limit", 10) or 10),
				query_count=tool_calls,
				max_search_call=max_search_call,
			)
			emit_api_event(event_logger, "tool_result", step=step, wire="completion", tool_name=name, result=result)
			messages.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, ensure_ascii=False)})
	return ToolLoopResult(text="", wire="completion", input_tokens=input_tokens, input_cached_tokens=input_cached_tokens, output_tokens=output_tokens, total_tokens=total_tokens, response_id=response_id, tool_calls=tool_calls)