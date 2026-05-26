from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from runner.api_common import normalize_base_url
from runner.config import GlobalConfig, ModelConfig


TOOL_SCHEMA_COMPLETION = {
	"type": "function",
	"function": {
		"name": "lean_explore",
		"description": "Search Lean decl or lemma. Result includes definition or statement, no proof.",
		"parameters": {
			"type": "object",
			"required": ["query", "limit"],
			"properties": {
				"query": {"type": "string", "description": "Name or description of the theorem or definition you are looking for"},
				"limit": {"type": "integer", "default": 10, "description": "Maximum number of results, capped at 10"},
			},
		},
	},
}

TOOL_SCHEMA_RESPONSE = {
	"type": "function",
	"name": "lean_explore",
	"description": "Search Lean decl or lemma. Result includes definition or statement, no proof.",
	"parameters": {
		"type": "object",
		"required": ["query", "limit"],
		"properties": {
			"query": {"type": "string", "description": "Name or description of the theorem or definition you are looking for"},
			"limit": {"type": "integer", "default": 10, "description": "Maximum number of results, capped at 10"},
		},
	},
	"strict": False,
}


def _extract_statement(source_text: str | None) -> str:
	if not source_text:
		return ""
	text = source_text.strip()
	idx = text.find(":=")
	idx_by = text.find(":= by")
	if idx_by == -1:
		idx_by = text.find(":=by")
	pos_candidates = [value for value in (idx, idx_by) if value != -1]
	if not pos_candidates:
		return " ".join(text.split())
	pos = min(min(pos_candidates) + 2, len(text))
	return " ".join(text[:pos].split())


def _normalize_search_result(item: dict[str, Any]) -> dict[str, Any]:
	return {
		# "id": item.get("id"),
		"name": item.get("name"),
		"source": _extract_statement(item.get("source_text") or item.get("source_code")),
		"module": item.get("module"),
	}


def _request_json(url: str, timeout: float = 20.0) -> Any:
	request = urllib.request.Request(
		url,
		headers={
			"Accept": "application/json",
			"User-Agent": "bench-runner-tool/1.0",
		},
	)
	try:
		with urllib.request.urlopen(request, timeout=timeout) as response:
			data = response.read()
			if not data:
				raise RuntimeError(f"Empty response from {url}")
			return json.loads(data.decode("utf-8"))
	except urllib.error.HTTPError as exc:
		detail = exc.read().decode("utf-8", errors="replace")
		raise RuntimeError(f"HTTP {exc.code} calling {url}: {detail or exc.reason}") from exc
	except urllib.error.URLError as exc:
		raise RuntimeError(f"Failed to connect to {url}: {exc.reason}") from exc
	except json.JSONDecodeError as exc:
		raise RuntimeError(f"Invalid JSON returned by {url}: {exc}") from exc


def _lean_explore_query_status(query_count: int | None, max_search_call: int | None) -> tuple[str | None, str | None, bool]:
	if query_count is None or max_search_call is None or max_search_call <= 0:
		return None, None, False
	progress = f"{min(max(query_count, 0), max_search_call)}/{max_search_call}"
	if query_count > max_search_call:
		return progress, f"tool call limit reached ({progress}). Do not call lean_explore again. Output the complete Lean answer directly.", True
	return progress, f"tool call limit: {progress}.", False


def lean_explore_search(
	base_url: str,
	query: str,
	limit: int,
	*,
	query_count: int | None = None,
	max_search_call: int | None = None,
) -> dict[str, Any]:
	progress, message, exhausted = _lean_explore_query_status(query_count, max_search_call)
	if exhausted:
		return {"count": 0, "results": [], "query_limit": progress, "message": message}
	clean_base = normalize_base_url(base_url)
	params = urllib.parse.urlencode({"q": query, "limit": max(1, min(limit, 10))})
	payload = _request_json(f"{clean_base}/search?{params}")
	items = payload if isinstance(payload, list) else payload.get("results", []) if isinstance(payload, dict) else []
	results = [_normalize_search_result(item) for item in items if isinstance(item, dict)]
	response = {"count": len(results), "results": results}
	if progress is not None and message is not None:
		response["query_limit"] = progress
		response["message"] = message
	return response
