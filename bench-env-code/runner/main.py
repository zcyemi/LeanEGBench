from __future__ import annotations

import argparse
import json
import sys
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))

from runner.api import (
	GenerationResult,
	extract_lean_code,
	extract_verification_error,
	extract_verification_error_messages,
	extract_verification_success,
	generate_with_mode,
	generate_once,
	protected_statement_prefix,
	statement_is_preserved,
	verify_problem_code,
)
from runner.config import AppConfig
from runner.db import benchtest_already_passed, benchtest_attempt_count, ensure_benchtest_schema, insert_benchtest_run


@dataclass(slots=True)
class CliArgs:
	model: str | None
	dataset: str
	pass_count: int
	batch: int
	mode: str
	verify_only: bool
	verify_url: str
	db_path: Path


@dataclass(slots=True)
class TaskItem:
	sample_index: int
	task_id: str
	problem: str
	lean: str
	prompt_text: str
	protected_prefix: str
	dataset_path: Path


@dataclass(slots=True)
class RunLogSession:
	path: Path

	def write_event(self, event: dict[str, Any]) -> None:
		with self.path.open("a", encoding="utf-8") as handle:
			handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="Lean benchmark test runner")
	parser.add_argument("--model", help="Model name from env.toml; required unless --verify is set")
	parser.add_argument("--dataset", required=True, help="JSONL dataset path")
	parser.add_argument("--pass", dest="pass_count", type=int, default=1, help="Target total attempts per task for the same dataset/model/mode; skip once the database already has this many attempts")
	parser.add_argument("--batch", type=int, default=1, help="Number of tasks to run concurrently")
	parser.add_argument("--mode", choices=("single", "tool", "agent"), default="single", help="Execution mode")
	parser.add_argument("--verify", dest="verify_only", action="store_true", help="Only verify each source problem has exactly one sorry and no other errors")
	parser.add_argument("--verify-url", default="http://localhost:8578/verify", help="Lean verification endpoint")
	parser.add_argument("--db-path", default=str(PROJECT_ROOT / "output"), help="SQLite database path or directory")
	return parser


def _resolve_db_path(db_path_arg: str, model: str | None, mode: str, verify_only: bool) -> Path:
	db_path = Path(db_path_arg)
	if db_path.suffix == ".db":
		return db_path
	if verify_only:
		return db_path
	if not model:
		raise ValueError("--model is required unless --verify is set")
	return db_path / f"{model}.{mode}.db"


def parse_args() -> CliArgs:
	args = _build_parser().parse_args()
	if args.pass_count < 1:
		raise ValueError("--pass must be >= 1")
	if args.batch < 1:
		raise ValueError("--batch must be >= 1")
	model_name = str(args.model).strip() if args.model else None
	if not args.verify_only and not model_name:
		raise ValueError("--model is required unless --verify is set")
	return CliArgs(
		model=model_name,
		dataset=args.dataset,
		pass_count=args.pass_count,
		batch=args.batch,
		mode=args.mode,
		verify_only=bool(args.verify_only),
		verify_url=args.verify_url,
		db_path=_resolve_db_path(args.db_path, model_name, args.mode, bool(args.verify_only)),
	)


def _now() -> str:
	return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _safe_log_component(value: str) -> str:
	cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in str(value))
	cleaned = cleaned.strip("._")
	return cleaned or "unknown"


def _format_log_timestamp(created_at: str) -> str:
	try:
		parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
	except ValueError:
		return _safe_log_component(created_at)
	return parsed.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


def _log_path_for_row(row: dict[str, Any]) -> Path:
	log_dir = PROJECT_ROOT / "logs" / _safe_log_component(str(row["model"]))
	log_dir.mkdir(parents=True, exist_ok=True)
	log_name = "_".join(
		[
			_safe_log_component(str(row["task_id"])),
			f"attempt{int(row.get('attempt_index', 0) or 0)}",
			_format_log_timestamp(str(row["created_at"])),
			_safe_log_component(str(row["run_id"])) or "run",
		]
	)
	return log_dir / f"{log_name}.log"


def _start_run_log(row: dict[str, Any]) -> RunLogSession:
	log_path = _log_path_for_row(row)
	with log_path.open("w", encoding="utf-8") as handle:
		handle.write(
			json.dumps(
				{
					"event": "run_meta",
					"run_id": row["run_id"],
					"task_id": row["task_id"],
					"model": row["model"],
					"mode": row["mode"],
					"attempt_index": row["attempt_index"],
					"created_at": row["created_at"],
				},
				ensure_ascii=False,
				sort_keys=True,
			)
			+ "\n"
		)
	return RunLogSession(log_path)


def _record_run_row(
	args: CliArgs,
	row: dict[str, Any],
	*,
	persist_db: bool,
	log_session: RunLogSession | None = None,
) -> Path:
	if log_session is None:
		log_session = _start_run_log(row)
	log_session.write_event({"event": "run_summary", **row})
	log_path = log_session.path
	if persist_db:
		insert_benchtest_run(args.db_path, row)
	return log_path


def _result_model_name(args: CliArgs) -> str:
	return args.model or "verify-only"


def _result_api_wire(app_config: AppConfig, args: CliArgs) -> str:
	if args.model:
		return app_config.get_model(args.model).api_wire
	return "verify"


def _read_text(path: Path) -> str:
	return path.read_text(encoding="utf-8")


def _render_problem_template(template_text: str, problem: str, lean: str) -> str:
	return template_text.replace("{PROBLEM}", problem).replace("{LEAN}", lean)


def _system_prompt_path(mode: str) -> Path:
	prompt_names = {
		"single": "system_single.md",
		"tool": "system_tool.md",
		"agent": "system.md",
	}
	try:
		prompt_name = prompt_names[mode]
	except KeyError as exc:
		raise ValueError(f"Unsupported mode for system prompt: {mode}") from exc
	return PROJECT_ROOT / "prompts" / prompt_name


def _load_system_prompt(mode: str, *, max_search_call: int | None = None) -> str:
	prompt = _read_text(_system_prompt_path(mode)).strip()
	if mode != "tool":
		return prompt
	if max_search_call is None:
		raise ValueError("max_search_call is required for mode=tool system prompt")
	return prompt.format(max_search_call=max_search_call)


def _load_dataset(dataset_path: Path, template_text: str) -> list[TaskItem]:
	tasks: list[TaskItem] = []
	with dataset_path.open("r", encoding="utf-8") as handle:
		for line_no, raw_line in enumerate(handle, start=1):
			line = raw_line.strip()
			if not line:
				continue
			payload = json.loads(line)
			if not isinstance(payload, dict):
				raise ValueError(f"Invalid JSON object at line {line_no}")
			problem = payload.get("problem")
			lean = payload.get("lean")
			if problem is None:
				problem = ""
			elif not isinstance(problem, str):
				raise ValueError(f"Invalid problem text at line {line_no}")
			elif not problem.strip():
				problem = ""
			if not isinstance(lean, str) or not lean.strip():
				raise ValueError(f"Missing lean text at line {line_no}")
			task_id = str(payload.get("id") or f"line_{line_no}")
			rendered = _render_problem_template(template_text, problem=problem, lean=lean)
			tasks.append(
				TaskItem(
					sample_index=line_no,
					task_id=task_id,
					problem=problem,
					lean=lean,
					prompt_text=rendered,
					protected_prefix=protected_statement_prefix(rendered),
					dataset_path=dataset_path,
				)
			)
	return tasks


def _summarize_text(text: str, limit: int = 400) -> str:
	flat = " ".join(text.split())
	if len(flat) <= limit:
		return flat
	return flat[:limit] + " ...[truncated]"


def _format_task_result_line(result: dict[str, Any], args: CliArgs) -> str:
	task_id = str(result.get("task_id", "unknown"))
	status = str(result.get("status", "unknown"))
	duration_ms = float(result.get("duration_ms", 0.0) or 0.0)
	total_tokens = int(result.get("total_tokens", 0) or 0)
	parts = [
		f"task_id={task_id}",
		f"model={_result_model_name(args)}",
		f"status={status}",
		f"duration_ms={duration_ms:.3f}",
		f"total_tokens={total_tokens}",
	]
	error = str(result.get("error", "") or "").strip()
	if error:
		parts.append(f"error={_summarize_text(error, limit=160)}")
	return " ".join(parts)


def _build_user_prompt(task: TaskItem) -> str:
	return (
		"Complete the Lean 4 file below. Return only valid Lean source code. "
		"Keep the theorem statement exactly as written and replace the final sorry with a complete proof.\n\n"
		f"{task.prompt_text}"
	)


def _source_precheck_error(response: dict[str, Any]) -> str | None:
	if response.get("error"):
		return str(response.get("error"))
	diagnostics = response.get("diagnostics")
	diagnostic_items = diagnostics.get("diagnostics", []) if isinstance(diagnostics, dict) else []
	error_items = [
		item for item in diagnostic_items if isinstance(item, dict) and str(item.get("severity", "")).lower() == "error"
	]
	sorries = response.get("sorries")
	sorry_count = len(sorries) if isinstance(sorries, list) else 0
	if extract_verification_success(response):
		return "source_precheck_expected_unsolved_problem"
	if sorry_count != 1:
		return f"source_precheck_expected_one_sorry_got_{sorry_count}"
	if error_items:
		return extract_verification_error(response)
	return None


def _run_source_verify(app_config: AppConfig, task: TaskItem, args: CliArgs, *, persist_result: bool, record_success: bool) -> dict[str, Any]:
	run_id = uuid.uuid4().hex
	created_at = _now()
	log_row = {
		"run_id": run_id,
		"task_id": task.task_id,
		"model": _result_model_name(args),
		"mode": args.mode,
		"attempt_index": 0,
		"created_at": created_at,
	}
	log_session: RunLogSession | None = None

	def ensure_log_session() -> RunLogSession | None:
		nonlocal log_session
		if args.verify_only:
			return None
		if log_session is None:
			log_session = _start_run_log(log_row)
		return log_session

	try:
		source_verify_response = verify_problem_code(task.prompt_text, verify_url=args.verify_url)
	except Exception as exc:
		ensure_log_session()
		if log_session is not None:
			log_session.write_event({"event": "verify", "stage": "source", "success": False, "error": str(exc)})
		row = {
			"run_id": run_id,
			"task_id": task.task_id,
			"sample_index": task.sample_index,
			"model": _result_model_name(args),
			"api_wire": _result_api_wire(app_config, args),
			"mode": args.mode,
			"attempt_index": 0,
			"max_attempts": args.pass_count,
			"batch_size": args.batch,
			"status": "error",
			"consistency_ok": False,
			"verify_ok": False,
			"input_tokens": 0,
			"input_cached_tokens": 0,
			"output_tokens": 0,
			"total_tokens": 0,
			"duration_ms": 0.0,
			"tool_call_counts": {"lean_submit_verify": 1},
			"final_code": None,
			"verify_response": None,
			"error": str(exc),
			"created_at": created_at,
		}
		log_path = _record_run_row(args, row, persist_db=persist_result, log_session=log_session) if log_session is not None else None
		result = {"task_id": task.task_id, "status": "error", "attempt_index": 0, "error": str(exc)}
		if log_path is not None:
			result["log_path"] = str(log_path)
		return result

	precheck_error = _source_precheck_error(source_verify_response)
	verify_error = extract_verification_error(source_verify_response) if extract_verification_error_messages(source_verify_response) else ""
	status = "source_verified" if precheck_error is None else "source_invalid"
	if precheck_error is not None or record_success:
		ensure_log_session()
	if log_session is not None:
		log_session.write_event(
			{
				"event": "verify",
				"stage": "source",
				"success": precheck_error is None,
				"error": precheck_error or verify_error,
				"response": source_verify_response,
			}
		)
	result = {"task_id": task.task_id, "status": status, "attempt_index": 0}
	row = {
		"run_id": run_id,
		"task_id": task.task_id,
		"sample_index": task.sample_index,
		"model": _result_model_name(args),
		"api_wire": _result_api_wire(app_config, args),
		"mode": args.mode,
		"attempt_index": 0,
		"max_attempts": args.pass_count,
		"batch_size": args.batch,
		"status": status,
		"consistency_ok": False,
		"verify_ok": precheck_error is None,
		"input_tokens": 0,
		"input_cached_tokens": 0,
		"output_tokens": 0,
		"total_tokens": 0,
		"duration_ms": 0.0,
		"tool_call_counts": {"lean_submit_verify": 1},
		"final_code": None,
		"verify_response": json.dumps(source_verify_response, ensure_ascii=False),
		"error": verify_error,
		"created_at": created_at,
	}
	if log_session is not None:
		log_path = _record_run_row(
			args,
			row,
			persist_db=persist_result and (precheck_error is not None or record_success),
			log_session=log_session,
		)
		result["log_path"] = str(log_path)
	if precheck_error is not None:
		result["error"] = precheck_error
	else:
		result["source_verify_response"] = source_verify_response
	return result


def _run_attempt(
	app_config: AppConfig,
	task: TaskItem,
	args: CliArgs,
	attempt_index: int,
	system_prompt: str,
	*,
	source_verify_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
	if not args.model:
		raise ValueError("--model is required for generation attempts")
	model_config = app_config.get_model(args.model)
	run_id = uuid.uuid4().hex
	created_at = _now()
	log_session = _start_run_log(
		{
			"run_id": run_id,
			"task_id": task.task_id,
			"model": model_config.name,
			"mode": args.mode,
			"attempt_index": attempt_index,
			"created_at": created_at,
		}
	)
	started_at = datetime.now(timezone.utc)
	user_prompt = _build_user_prompt(task)

	def event_logger(event: dict[str, Any]) -> None:
		log_session.write_event(event)

	if source_verify_response is not None:
		log_session.write_event(
			{
				"event": "verify",
				"stage": "source",
				"success": True,
				"error": "",
				"response": source_verify_response,
			}
		)

	try:
		generation = generate_with_mode(
			model_config,
			app_config.config,
			system_prompt,
			user_prompt,
			mode=args.mode,
			max_search_call=app_config.config.mode_tool_max_search_call if args.mode == "tool" else None,
			event_logger=event_logger,
		)
		candidate_code = extract_lean_code(generation.text)
		consistency_ok = statement_is_preserved(task.prompt_text, candidate_code)
		verify_response: dict[str, Any] | None = None
		verify_ok = False
		if consistency_ok:
			verify_response = verify_problem_code(candidate_code, verify_url=args.verify_url)
			verify_ok = extract_verification_success(verify_response)
		log_session.write_event(
			{
				"event": "verify",
				"stage": "candidate",
				"success": verify_ok,
				"error": "" if verify_ok else extract_verification_error(verify_response or {}),
				"response": verify_response,
			}
		)
		status = "passed" if consistency_ok and verify_ok else ("consistency_failed" if not consistency_ok else "verify_failed")
		error = "" if status == "passed" else (
			"statement_mismatch" if not consistency_ok else extract_verification_error(verify_response or {})
		)
		duration_ms = round((datetime.now(timezone.utc) - started_at).total_seconds() * 1000, 3)
		row = {
			"run_id": run_id,
			"task_id": task.task_id,
			"sample_index": task.sample_index,
			"model": model_config.name,
			"api_wire": model_config.api_wire,
			"mode": args.mode,
			"attempt_index": attempt_index,
			"max_attempts": args.pass_count,
			"batch_size": args.batch,
			"status": status,
			"consistency_ok": consistency_ok,
			"verify_ok": verify_ok,
			"input_tokens": generation.input_tokens,
			"input_cached_tokens": generation.input_cached_tokens,
			"output_tokens": generation.output_tokens,
			"total_tokens": generation.total_tokens,
			"duration_ms": duration_ms,
			"tool_call_counts": ({"lean_submit_verify": 1 if consistency_ok else 0} | ({"lean_explore": getattr(generation, "tool_calls", 0)} if getattr(generation, "tool_calls", 0) else {})),
			"final_code": candidate_code,
			"verify_response": json.dumps(verify_response, ensure_ascii=False) if verify_response is not None else None,
			"error": error,
			"created_at": created_at,
		}
		log_session.write_event({"event": "final_output", "output_text": generation.text})
		log_path = _record_run_row(args, row, persist_db=True, log_session=log_session)
		return {
			"task_id": task.task_id,
			"attempt_index": attempt_index,
			"status": status,
			"consistency_ok": consistency_ok,
			"verify_ok": verify_ok,
			"input_tokens": generation.input_tokens,
			"output_tokens": generation.output_tokens,
			"total_tokens": generation.total_tokens,
			"duration_ms": duration_ms,
			"response_id": generation.response_id,
			"error": error,
			"final_code": candidate_code,
			"log_path": str(log_path),
		}
	except Exception as exc:
		duration_ms = round((datetime.now(timezone.utc) - started_at).total_seconds() * 1000, 3)
		row = {
			"run_id": uuid.uuid4().hex,
			"task_id": task.task_id,
			"sample_index": task.sample_index,
			"model": _result_model_name(args),
			"api_wire": _result_api_wire(app_config, args),
			"mode": args.mode,
			"attempt_index": attempt_index,
			"max_attempts": args.pass_count,
			"batch_size": args.batch,
			"status": "error",
			"consistency_ok": False,
			"verify_ok": False,
			"input_tokens": 0,
			"input_cached_tokens": 0,
			"output_tokens": 0,
			"total_tokens": 0,
			"duration_ms": duration_ms,
			"tool_call_counts": {},
			"final_code": None,
			"verify_response": None,
			"error": str(exc),
			"created_at": _now(),
		}
		log_session.write_event({"event": "attempt_error", "error": str(exc)})
		log_path = _record_run_row(args, row, persist_db=True, log_session=log_session)
		return {
			"task_id": task.task_id,
			"attempt_index": attempt_index,
			"status": "error",
			"error": str(exc),
			"duration_ms": duration_ms,
			"log_path": str(log_path),
		}


def _run_task(app_config: AppConfig, task: TaskItem, args: CliArgs, system_prompt: str) -> dict[str, Any]:
	if not args.model:
		raise ValueError("--model is required for task execution")
	if benchtest_already_passed(args.db_path, task.task_id, args.model, args.mode):
		row = {
			"run_id": uuid.uuid4().hex,
			"task_id": task.task_id,
			"sample_index": task.sample_index,
			"model": _result_model_name(args),
			"api_wire": _result_api_wire(app_config, args),
			"mode": args.mode,
			"attempt_index": 0,
			"max_attempts": args.pass_count,
			"batch_size": args.batch,
			"status": "skipped_already_passed",
			"consistency_ok": False,
			"verify_ok": False,
			"input_tokens": 0,
			"input_cached_tokens": 0,
			"output_tokens": 0,
			"total_tokens": 0,
			"duration_ms": 0.0,
			"tool_call_counts": {},
			"final_code": None,
			"verify_response": None,
			"error": "task already passed for current dataset/model/mode",
			"created_at": _now(),
		}
		log_path = _record_run_row(args, row, persist_db=False)
		return {"task_id": task.task_id, "status": "skipped_already_passed", "attempt_index": 0, "log_path": str(log_path)}
	existing_attempts = benchtest_attempt_count(args.db_path, task.task_id, args.model, args.mode)
	if existing_attempts >= args.pass_count:
		row = {
			"run_id": uuid.uuid4().hex,
			"task_id": task.task_id,
			"sample_index": task.sample_index,
			"model": _result_model_name(args),
			"api_wire": _result_api_wire(app_config, args),
			"mode": args.mode,
			"attempt_index": 0,
			"max_attempts": args.pass_count,
			"batch_size": args.batch,
			"status": "skipped_attempt_limit",
			"consistency_ok": False,
			"verify_ok": False,
			"input_tokens": 0,
			"input_cached_tokens": 0,
			"output_tokens": 0,
			"total_tokens": 0,
			"duration_ms": 0.0,
			"tool_call_counts": {},
			"final_code": None,
			"verify_response": None,
			"error": f"attempt limit reached ({existing_attempts}/{args.pass_count})",
			"created_at": _now(),
		}
		log_path = _record_run_row(args, row, persist_db=False)
		return {"task_id": task.task_id, "status": "skipped_attempt_limit", "attempt_index": 0, "log_path": str(log_path)}

	source_result = _run_source_verify(app_config, task, args, persist_result=True, record_success=False)
	if source_result["status"] != "source_verified":
		return source_result
	source_verify_response = source_result.get("source_verify_response")

	last_result: dict[str, Any] | None = None
	for attempt_index in range(existing_attempts + 1, args.pass_count + 1):
		result = _run_attempt(
			app_config,
			task,
			args,
			attempt_index,
			system_prompt,
			source_verify_response=source_verify_response if attempt_index == 1 else None,
		)
		last_result = result
	return last_result or {"task_id": task.task_id, "status": "error", "error": "no attempts executed"}


def main() -> int:
	args = parse_args()
	if args.mode != "single":
		if args.mode == "agent":
			raise NotImplementedError("mode=agent is reserved and not yet implemented")

	root = PROJECT_ROOT
	env_path = root / "env.toml"
	env_example_path = root / "env.example.toml"
	template_path = root / "prompts" / "problem_template.txt"
	system_prompt_path = _system_prompt_path(args.mode)
	if not env_path.exists():
		raise FileNotFoundError(
			f"Missing env file: {env_path}. Copy {env_example_path.name} to env.toml and fill in your model configuration."
		)
	if not template_path.exists():
		raise FileNotFoundError(f"Missing problem template file: {template_path}")
	if not system_prompt_path.exists():
		raise FileNotFoundError(f"Missing system prompt file: {system_prompt_path}")

	app_config = AppConfig.load(env_path)
	if args.mode == "tool" and not app_config.config.lean_explore_url.strip():
		raise ValueError("env.toml lean_explore.url must be configured for mode=tool")
	dataset_path = Path(args.dataset).expanduser().resolve()
	if not dataset_path.exists():
		raise FileNotFoundError(f"Dataset file not found: {dataset_path}")

	template_text = _read_text(template_path)
	max_search_call = app_config.config.mode_tool_max_search_call if args.mode == "tool" else None
	system_prompt = _load_system_prompt(args.mode, max_search_call=max_search_call)
	tasks = _load_dataset(dataset_path, template_text)
	if not args.verify_only:
		ensure_benchtest_schema(args.db_path)

	print(json.dumps({"event": "benchtest_start", "tasks": len(tasks), "batch": args.batch, "pass_count": args.pass_count, "model": _result_model_name(args)}, ensure_ascii=False))
	results: list[dict[str, Any]] = []
	with ThreadPoolExecutor(max_workers=args.batch) as executor:
		pending_tasks = iter(tasks)
		active_futures: dict[Future[dict[str, Any]], TaskItem] = {}

		def submit_next_task() -> bool:
			try:
				task = next(pending_tasks)
			except StopIteration:
				return False
			task_runner = _run_source_verify if args.verify_only else _run_task
			task_args: tuple[Any, ...] = (app_config, task, args) if args.verify_only else (app_config, task, args, system_prompt)
			future = executor.submit(
				task_runner,
				*task_args,
				**({"persist_result": False, "record_success": False} if args.verify_only else {}),
			)
			active_futures[future] = task
			return True

		for _ in range(min(args.batch, len(tasks))):
			if not submit_next_task():
				break

		while active_futures:
			done, _ = wait(active_futures.keys(), return_when=FIRST_COMPLETED)
			for future in done:
				active_futures.pop(future, None)
				result = future.result()
				results.append(result)
				if not args.verify_only or result.get("status") != "source_verified":
					print(_format_task_result_line(result, args))
				submit_next_task()

	passed_statuses = {"source_verified"} if args.verify_only else {"passed", "skipped_attempt_limit", "skipped_already_passed"}
	passed = sum(1 for item in results if item.get("status") in passed_statuses)
	failed = sum(1 for item in results if item.get("status") not in passed_statuses)
	print(json.dumps({"event": "benchtest_end", "passed": passed, "failed": failed, "total": len(results)}, ensure_ascii=False))
	return 0 if failed == 0 else 1


if __name__ == "__main__":
	raise SystemExit(main())
