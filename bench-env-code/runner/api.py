from __future__ import annotations

from runner.api_antropic import clear_antropic_client_cache, request_antropic_once, request_antropic_tool_loop
from runner.api_common import (
	ApiEventLogger,
	VERIFY_URL_DEFAULT,
	GenerationResult,
	ToolLoopResult,
	extract_lean_code,
	extract_verification_error,
	extract_verification_error_messages,
	extract_verification_success,
	normalize_whitespace,
	protected_statement_prefix,
	statement_is_preserved,
	verify_problem_code,
)
from runner.api_completion import clear_completion_client_cache, request_completion_once, request_completion_tool_loop
from runner.api_response import clear_response_client_cache, request_response_once, request_response_tool_loop
from runner.config import GlobalConfig, ModelConfig


def clear_client_caches() -> None:
	clear_completion_client_cache()
	clear_response_client_cache()
	clear_antropic_client_cache()


def generate_once(model_config: ModelConfig, global_config: GlobalConfig, system_prompt: str, user_prompt: str) -> GenerationResult:
	return generate_with_mode(model_config, global_config, system_prompt, user_prompt, mode="single")


def generate_with_mode(
	model_config: ModelConfig,
	global_config: GlobalConfig,
	system_prompt: str,
	user_prompt: str,
	*,
	mode: str,
	max_search_call: int | None = None,
	event_logger: ApiEventLogger | None = None,
) -> GenerationResult | ToolLoopResult:
	if mode == "single":
		if model_config.api_wire == "completion":
			return request_completion_once(model_config, global_config, system_prompt, user_prompt, event_logger=event_logger)
		if model_config.api_wire == "response":
			return request_response_once(model_config, global_config, system_prompt, user_prompt, event_logger=event_logger)
		if model_config.api_wire in {"anthropic", "antropic"}:
			return request_antropic_once(model_config, global_config, system_prompt, user_prompt, event_logger=event_logger)
		raise ValueError(f"Unsupported api wire: {model_config.api_wire}")
	if mode == "tool":
		if max_search_call is None or max_search_call < 1:
			raise ValueError("max_search_call must be >= 1")
		if not global_config.lean_explore_url.strip():
			raise ValueError("lean_explore.url is not configured")
		if model_config.api_wire == "completion":
			return request_completion_tool_loop(model_config, global_config, system_prompt, user_prompt, max_search_call, event_logger=event_logger)
		if model_config.api_wire == "response":
			return request_response_tool_loop(model_config, global_config, system_prompt, user_prompt, max_search_call, event_logger=event_logger)
		if model_config.api_wire in {"anthropic", "antropic"}:
			return request_antropic_tool_loop(model_config, global_config, system_prompt, user_prompt, max_search_call, event_logger=event_logger)
		raise ValueError(f"Unsupported api wire: {model_config.api_wire}")
	raise ValueError(f"Unsupported mode: {mode}")
