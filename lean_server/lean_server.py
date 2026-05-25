from __future__ import annotations

import argparse
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

import leanclient as lc


class StatusCode(IntEnum):
	OK = 200
	BAD_REQUEST = 400
	SERVICE_UNAVAILABLE = 503
	INTERNAL_SERVER_ERROR = 500


def _fmt_diag(diag: dict[str, Any]) -> dict[str, Any]:
	start = diag.get("range", {}).get("start", {})
	end = diag.get("range", {}).get("end", {})
	severity_map = {1: "error", 2: "warning", 3: "info", 4: "hint"}
	return {
		"severity": severity_map.get(diag.get("severity", 0), "unknown"),
		"message": diag.get("message", ""),
		"line": start.get("line"),
		"column": start.get("character"),
		"end_line": end.get("line"),
		"end_column": end.get("character"),
	}


def _fmt_diagnostics(diags: Any) -> dict[str, Any]:
	items = [_fmt_diag(item) for item in getattr(diags, "diagnostics", [])]
	return {
		"success": getattr(diags, "success", False),
		"count": len(items),
		"diagnostics": items,
	}


def _verification_failed(diagnostic_result: dict[str, Any], sorries: list[dict[str, int]]) -> tuple[bool,str]:
	error_count = sum(
		1
		for item in diagnostic_result.get("diagnostics", [])
		if isinstance(item, dict) and str(item.get("severity", "")).lower() == "error"
	)
	unknown_count = sum(
		1
		for item in diagnostic_result.get("diagnostics", [])
		if isinstance(item, dict) and str(item.get("severity", "")).lower() == "unknown"
	)

	#scan for 'uses `sorry`'
	diag_sorry = False

	for item in diagnostic_result.get("diagnostics", []):
		if isinstance(item, dict) and "sorry" in item.get("message", "").lower():
			diag_sorry = True
			break

	failed = len(sorries) > 0 or error_count > 0 or unknown_count > 0 or diag_sorry
	return failed, f"unknown_count: {unknown_count}, diag_sorry: {diag_sorry}"


def _scan_sorries(code: str) -> list[dict[str, int]]:
	results: list[dict[str, int]] = []
	for line_number, line in enumerate(code.splitlines()):
		for match in re.finditer(r"\bsorry\b", line):
			results.append({"line": line_number, "column": match.start()})
	return results


@dataclass(slots=True)
class Slot:
	index: int
	path: Path
	rel_path: str
	sfc: Any


class SlotPool:
	def __init__(
		self,
		workspace: Path,
		client: lc.LeanLSPClient,
		size: int,
		prefix: str,
		publish_immediately: bool = True,
	) -> None:
		if size < 1:
			raise ValueError("slots must be >= 1")
		self._workspace = workspace
		self._condition = threading.Condition()
		self._available: list[Slot] = []
		self._in_use: set[int] = set()
		self._slots = self._build_slots(client, size, prefix)
		if publish_immediately:
			self._available.extend(self._slots)

	def _build_slots(self, client: lc.LeanLSPClient, size: int, prefix: str) -> list[Slot]:
		slots: list[Slot] = []
		for index in range(size):
			filename = f"{prefix}_{index}.lean"
			path = self._workspace / filename
			if not path.exists():
				path.write_text("-- lean verification slot\n", encoding="utf-8")
			rel_path = os.path.relpath(path, self._workspace)
			client.open_file(rel_path)
			sfc = client.create_file_client(rel_path)
			slots.append(Slot(index=index, path=path, rel_path=rel_path, sfc=sfc))
		return slots

	@contextmanager
	def acquire(self, timeout: float | None) -> Any:
		started_at = time.monotonic()
		with self._condition:
			while not self._available:
				if timeout is not None:
					elapsed = time.monotonic() - started_at
					remaining = timeout - elapsed
					if remaining <= 0:
						raise TimeoutError("no verification slot became available before timeout")
					self._condition.wait(remaining)
				else:
					self._condition.wait()

			slot = self._available.pop(0)
			self._in_use.add(slot.index)

		wait_ms = int((time.monotonic() - started_at) * 1000)
		try:
			yield slot, wait_ms
		finally:
			with self._condition:
				self._in_use.discard(slot.index)
				self._available.append(slot)
				self._condition.notify()

	def snapshot(self) -> dict[str, int]:
		with self._condition:
			return {
				"slots": len(self._slots),
				"available": len(self._available),
				"in_use": len(self._in_use),
			}

	def publish(self, slot: Slot) -> None:
		with self._condition:
			if slot.index in self._in_use:
				return
			if any(existing.index == slot.index for existing in self._available):
				return
			self._available.append(slot)
			self._condition.notify_all()


class LeanServerApp:
	def __init__(
		self,
		workspace: Path,
		slots: int,
		slot_prefix: str,
		default_wait_timeout: float,
		default_diagnostic_timeout: float,
		warmup_diagnostic_timeout: float,
	) -> None:
		self.workspace = workspace.resolve()
		if not self.workspace.exists():
			raise FileNotFoundError(f"workspace does not exist: {self.workspace}")
		if not self.workspace.is_dir():
			raise NotADirectoryError(f"workspace is not a directory: {self.workspace}")

		self.client = lc.LeanLSPClient(str(self.workspace), max_opened_files=max(slots, 4))
		self.pool = SlotPool(
			self.workspace,
			self.client,
			slots,
			slot_prefix,
			publish_immediately=False,
		)
		self.default_wait_timeout = default_wait_timeout
		self.default_diagnostic_timeout = default_diagnostic_timeout
		self.warmup_diagnostic_timeout = warmup_diagnostic_timeout
		self._warmup_lock = threading.Lock()
		self.warmup_results: list[dict[str, Any]] = []
		self.warmup_status = "pending"
		self._warmup_thread = self._start_warmup()

	def _warmup_slots(self) -> None:
		warmup_code = "import Mathlib\n"
		with self._warmup_lock:
			self.warmup_status = "running"
		for slot in self.pool._slots:
			started_at = time.monotonic()
			try:
				slot.path.write_text(warmup_code, encoding="utf-8")
				slot.sfc.update_file_content(warmup_code)
				diags = slot.sfc.get_diagnostics(
					inactivity_timeout=self.warmup_diagnostic_timeout,
				)
				result = {
					"slot": slot.index,
					"file": str(slot.path),
					"ok": True,
					"elapsed_ms": int((time.monotonic() - started_at) * 1000),
					"diagnostics": _fmt_diagnostics(diags),
					"mode": "import-dispatched",
				}
			except Exception as exc:
				result = {
					"slot": slot.index,
					"file": str(slot.path),
					"ok": False,
					"elapsed_ms": int((time.monotonic() - started_at) * 1000),
					"error": str(exc),
				}
			self.pool.publish(slot)
			print(f"warmup slot={result['slot']} ok={result['ok']} elapsed_ms={result['elapsed_ms']}")
			with self._warmup_lock:
				self.warmup_results.append(result)
		with self._warmup_lock:
			self.warmup_status = "completed"

	def _start_warmup(self) -> threading.Thread:
		thread = threading.Thread(target=self._warmup_slots, name="lean-slot-warmup", daemon=True)
		thread.start()
		return thread

	def warmup_snapshot(self) -> dict[str, Any]:
		with self._warmup_lock:
			return {
				"status": self.warmup_status,
				"completed": len(self.warmup_results),
				"total": len(self.pool._slots),
				"results": list(self.warmup_results),
			}

	def verify(
		self,
		code: str,
		wait_timeout: float | None = None,
		diagnostic_timeout: float | None = None,
	) -> tuple[dict[str, Any], int]:
		if not isinstance(code, str) or not code.strip():
			return {"error": "request field 'code' must be a non-empty string"}, StatusCode.BAD_REQUEST

		wait_timeout = self.default_wait_timeout if wait_timeout is None else wait_timeout
		diagnostic_timeout = (
			self.default_diagnostic_timeout
			if diagnostic_timeout is None
			else diagnostic_timeout
		)

		started_at = time.monotonic()
		try:
			with self.pool.acquire(wait_timeout) as (slot, wait_ms):
				slot.path.write_text(code, encoding="utf-8")
				slot.sfc.update_file_content(code)
				diags = slot.sfc.get_diagnostics(inactivity_timeout=diagnostic_timeout)
				diagnostic_result = _fmt_diagnostics(diags)
				sorries = _scan_sorries(code)
				slot_index = slot.index
				slot_file = str(slot.path)
			elapsed_ms = int((time.monotonic() - started_at) * 1000)

			failed, extra = _verification_failed(diagnostic_result, sorries)
			response = {
				"ok": not failed,
				"slot": slot_index,
				"file": slot_file,
				"wait_ms": wait_ms,
				"elapsed_ms": elapsed_ms,
				"diagnostics": diagnostic_result,
				"sorries": sorries,
			}
			return response, StatusCode.OK
		except TimeoutError as exc:
			return {
				"error": str(exc),
				"pool": self.pool.snapshot(),
			}, StatusCode.SERVICE_UNAVAILABLE
		except Exception as exc:
			return {
				"error": str(exc),
				"pool": self.pool.snapshot(),
		}, StatusCode.INTERNAL_SERVER_ERROR


class VerifyRequest(BaseModel):
	code: str = Field(min_length=1)
	wait_timeout: float | None = Field(default=None, ge=0)
	diagnostic_timeout: float | None = Field(default=None, ge=0)


class HealthResponse(BaseModel):
	status: str
	workspace: str
	pool: dict[str, int]
	warmup: dict[str, Any]


def create_api(app_state: LeanServerApp, debug: bool = False) -> FastAPI:
	api = FastAPI(title="Lean Verify Server", version="0.1.0", debug=debug)

	@api.get("/health", response_model=HealthResponse)
	def health() -> dict[str, Any]:
		return {
			"status": "ok",
			"workspace": str(app_state.workspace),
			"pool": app_state.pool.snapshot(),
			"warmup": app_state.warmup_snapshot(),
		}

	@api.post("/verify")
	def verify(request: VerifyRequest) -> dict[str, Any]:
		response, status = app_state.verify(
			code=request.code,
			wait_timeout=request.wait_timeout,
			diagnostic_timeout=request.diagnostic_timeout,
		)
		if status != StatusCode.OK:
			raise HTTPException(status_code=int(status), detail=response)
		return response

	return api


def _parse_optional_float(value: Any, name: str) -> float | None:
	if value is None:
		return None
	try:
		parsed = float(value)
	except (TypeError, ValueError) as exc:
		raise ValueError(f"{name} must be a number") from exc
	if parsed < 0:
		raise ValueError(f"{name} must be >= 0")
	return parsed


def _build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="Standalone Lean verification HTTP server")
	parser.add_argument("--workspace", required=True, help="Path to the Lean project workspace")
	parser.add_argument("-n", "--slots", type=int, required=True, help="Maximum concurrent verify slots")
	parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host")
	parser.add_argument("--port", type=int, default=8765, help="HTTP bind port")
	parser.add_argument(
		"--wait-timeout",
		type=float,
		default=40.0,
		help="Default seconds to wait for a free slot before returning 503",
	)
	parser.add_argument(
		"--diagnostic-timeout",
		type=float,
		default=30.0,
		help="Default inactivity timeout passed to Lean diagnostics",
	)
	parser.add_argument(
		"--warmup-diagnostic-timeout",
		type=float,
		default=300.0,
		help="Inactivity timeout used for startup slot warmup diagnostics",
	)
	parser.add_argument(
		"--slot-prefix",
		default="LeanVerifySlot",
		help="Prefix for temporary Lean files created inside the workspace",
	)
	parser.add_argument(
		"--debug",
		action="store_true",
		help="Enable FastAPI debug mode and verbose uvicorn logging",
	)
	return parser


def main() -> None:
	args = _build_parser().parse_args()
	app = LeanServerApp(
		workspace=Path(args.workspace),
		slots=args.slots,
		slot_prefix=args.slot_prefix,
		default_wait_timeout=args.wait_timeout,
		default_diagnostic_timeout=args.diagnostic_timeout,
		warmup_diagnostic_timeout=args.warmup_diagnostic_timeout,
	)
	api = create_api(app, debug=args.debug)
	print(
		f"Lean verify server listening on http://{args.host}:{args.port} "
		f"with workspace={app.workspace} slots={args.slots} debug={args.debug}"
	)
	try:
		uvicorn.run(
			api,
			host=args.host,
			port=args.port,
			log_level="debug" if args.debug else "info",
			access_log=True,
		)
	except KeyboardInterrupt:
		pass
	finally:
		app.client.close()


if __name__ == "__main__":
	main()
