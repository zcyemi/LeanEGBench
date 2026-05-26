from __future__ import annotations

import json
import threading
from typing import Any

from openai import OpenAI

from runner.api_common import ApiEventLogger, GenerationResult, ToolLoopResult, apply_extra_params, client_cache_key, emit_api_event, extract_reasoning_text, normalize_base_url, safe_json_object_loads, usage_delta
from runner.config import GlobalConfig, ModelConfig
from runner.tool import TOOL_SCHEMA_RESPONSE, lean_explore_search


_THREAD_LOCAL = threading.local()


def _client_cache() -> dict[tuple[str, str, str], OpenAI]:
	cache = getattr(_THREAD_LOCAL, "clients", None)
	if cache is None:
		cache = {}
		_THREAD_LOCAL.clients = cache
	return cache


def get_response_client(model_config: ModelConfig) -> OpenAI:
	key = client_cache_key(model_config)
	cache = _client_cache()
	client = cache.get(key)
	if client is None:
		client = OpenAI(api_key=model_config.api_key, base_url=normalize_base_url(model_config.url))
		cache[key] = client
	return client


def clear_response_client_cache() -> None:
	_THREAD_LOCAL.clients = {}


def response_output_text(response: Any) -> str:
	text = str(getattr(response, "output_text", "") or "").strip()
	if text:
		return text
	chunks: list[str] = []
	for item in getattr(response, "output", []) or []:
		if getattr(item, "type", "") != "message":
			continue
		for chunk in getattr(item, "content", []) or []:
			if getattr(chunk, "type", "") in {"output_text", "text"}:
				value = str(getattr(chunk, "text", "") or "").strip()
				if value:
					chunks.append(value)
	return "\n".join(chunks)


def _response_reasoning_text(response: Any) -> str:
	chunks: list[str] = []
	for item in getattr(response, "output", []) or []:
		if getattr(item, "type", "") != "reasoning":
			continue
		extracted = extract_reasoning_text(item)
		if extracted:
			chunks.append(extracted)
	if not chunks:
		return ""
	return "\n".join(chunks)


def request_response_once(
	model_config: ModelConfig,
	global_config: GlobalConfig,
	system_prompt: str,
	user_prompt: str,
	*,
	event_logger: ApiEventLogger | None = None,
) -> GenerationResult:
	client = get_response_client(model_config)
	request: dict[str, object] = {
		"model": model_config.request_model_id,
		"input": [
			{"role": "user", "content": user_prompt},
		],
		"instructions": system_prompt,
		# "temperature": global_config.temperature,
		"max_output_tokens": global_config.max_completion_tokens,
	}
	

	apply_extra_params(request, model_config)
	response = client.responses.create(**request)
	text = response_output_text(response)
	input_tokens, input_cached_tokens, output_tokens, total_tokens = usage_delta(getattr(response, "usage", None))
	emit_api_event(
		event_logger,
		"llm_round",
		step=1,
		wire="response",
		response_id=str(getattr(response, "id", "") or "") or None,
		input_tokens=input_tokens,
		input_cached_tokens=input_cached_tokens,
		output_tokens=output_tokens,
		total_tokens=total_tokens,
		reasoning=_response_reasoning_text(response),
		output_text=text,
	)
	return GenerationResult(
		text=text,
		wire="response",
		input_tokens=input_tokens,
		input_cached_tokens=input_cached_tokens,
		output_tokens=output_tokens,
		total_tokens=total_tokens,
		response_id=str(getattr(response, "id", "") or "") or None,
	)


def _response_tool_calls(response: Any) -> list[tuple[str, str, str]]:
	results: list[tuple[str, str, str]] = []
	for item in getattr(response, "output", []) or []:
		if getattr(item, "type", "") != "function_call":
			continue
		name = str(getattr(item, "name", "") or "")
		arguments = str(getattr(item, "arguments", "") or "")
		call_id = str(getattr(item, "call_id", "") or getattr(item, "id", "") or "")
		if name:
			results.append((call_id, name, arguments))
	return results


def request_response_tool_loop(
	model_config: ModelConfig,
	global_config: GlobalConfig,
	system_prompt: str,
	user_prompt: str,
	max_search_call: int,
	*,
	event_logger: ApiEventLogger | None = None,
) -> ToolLoopResult:
	client = get_response_client(model_config)
	pending_input: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]
	input_tokens = 0
	input_cached_tokens = 0
	output_tokens = 0
	total_tokens = 0
	response_id: str | None = None
	tool_calls = 0
	max_steps = max(global_config.max_tool_call, max_search_call) + 2
	for step in range(1, max_steps + 1):
		request: dict[str, Any] = {
			"model": model_config.request_model_id,
			"input": pending_input,
			"instructions": system_prompt,
			"max_output_tokens": global_config.max_completion_tokens,
			"tools": [TOOL_SCHEMA_RESPONSE],
		}
		if response_id is not None:
			request["previous_response_id"] = response_id
		apply_extra_params(request, model_config)
		response = client.responses.create(**request)
		response_id = str(getattr(response, "id", "") or "") or response_id
		delta = usage_delta(getattr(response, "usage", None))
		input_tokens += delta[0]
		input_cached_tokens += delta[1]
		output_tokens += delta[2]
		total_tokens += delta[3]
		text = response_output_text(response)
		emit_api_event(
			event_logger,
			"llm_round",
			step=step,
			wire="response",
			response_id=response_id,
			input_tokens=delta[0],
			input_cached_tokens=delta[1],
			output_tokens=delta[2],
			total_tokens=delta[3],
			reasoning=_response_reasoning_text(response),
			output_text=text,
		)
		calls = _response_tool_calls(response)
		if not calls:
			return ToolLoopResult(text=text, wire="response", input_tokens=input_tokens, input_cached_tokens=input_cached_tokens, output_tokens=output_tokens, total_tokens=total_tokens, response_id=response_id, tool_calls=tool_calls)
		pending_input = []
		for call_id, name, arguments in calls:
			if name != "lean_explore":
				continue
			tool_calls += 1
			payload = safe_json_object_loads(arguments)
			emit_api_event(event_logger, "tool_call", step=step, wire="response", tool_name=name, arguments=payload)
			result = lean_explore_search(
				global_config.lean_explore_url,
				str(payload.get("query", "")),
				int(payload.get("limit", 10) or 10),
				query_count=tool_calls,
				max_search_call=max_search_call,
			)
			emit_api_event(event_logger, "tool_result", step=step, wire="response", tool_name=name, result=result)
			pending_input.append({"type": "function_call_output", "call_id": call_id, "output": json.dumps(result, ensure_ascii=False)})
	return ToolLoopResult(text="", wire="response", input_tokens=input_tokens, input_cached_tokens=input_cached_tokens, output_tokens=output_tokens, total_tokens=total_tokens, response_id=response_id, tool_calls=tool_calls)