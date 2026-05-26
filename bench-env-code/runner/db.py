from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


_BENCHTEST_RUN_COLUMNS = (
	"run_id",
	"task_id",
	"sample_index",
	"model",
	"api_wire",
	"mode",
	"attempt_index",
	"max_attempts",
	"batch_size",
	"status",
	"consistency_ok",
	"verify_ok",
	"input_tokens",
	"input_cached_tokens",
	"output_tokens",
	"total_tokens",
	"duration_ms",
	"tool_call_counts",
	"final_code",
	"verify_response",
	"error",
	"created_at",
)


def _create_benchtest_runs_table(conn: sqlite3.Connection) -> None:
	conn.execute(
		"""
		CREATE TABLE IF NOT EXISTS benchtest_runs (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			run_id TEXT NOT NULL,
			task_id TEXT NOT NULL,
			sample_index INTEGER NOT NULL,
			model TEXT NOT NULL,
			api_wire TEXT NOT NULL,
			mode TEXT NOT NULL,
			attempt_index INTEGER NOT NULL,
			max_attempts INTEGER NOT NULL,
			batch_size INTEGER NOT NULL,
			status TEXT NOT NULL,
			consistency_ok INTEGER NOT NULL,
			verify_ok INTEGER NOT NULL,
			input_tokens INTEGER NOT NULL DEFAULT 0,
			input_cached_tokens INTEGER NOT NULL DEFAULT 0,
			output_tokens INTEGER NOT NULL DEFAULT 0,
			total_tokens INTEGER NOT NULL DEFAULT 0,
			duration_ms REAL NOT NULL DEFAULT 0,
			tool_call_counts TEXT NOT NULL DEFAULT '{}',
			final_code TEXT,
			verify_response TEXT,
			error TEXT,
			created_at TEXT NOT NULL
		)
		"""
	)


def ensure_benchtest_schema(db_path: Path) -> None:
	db_path.parent.mkdir(parents=True, exist_ok=True)
	with sqlite3.connect(db_path) as conn:
		_create_benchtest_runs_table(conn)
		conn.commit()


def insert_benchtest_run(db_path: Path, row: dict[str, Any]) -> None:
	with sqlite3.connect(db_path) as conn:
		conn.execute(
			"""
			INSERT INTO benchtest_runs (
				run_id, task_id, sample_index, model, api_wire, mode,
				attempt_index, max_attempts, batch_size, status, consistency_ok, verify_ok,
				input_tokens, input_cached_tokens, output_tokens, total_tokens, duration_ms,
				tool_call_counts, final_code, verify_response, error, created_at
			) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
			""",
			(
				row["run_id"],
				row["task_id"],
				row["sample_index"],
				row["model"],
				row["api_wire"],
				row["mode"],
				row["attempt_index"],
				row["max_attempts"],
				row["batch_size"],
				row["status"],
				int(bool(row["consistency_ok"])),
				int(bool(row["verify_ok"])),
				row["input_tokens"],
				row["input_cached_tokens"],
				row["output_tokens"],
				row["total_tokens"],
				row["duration_ms"],
				json.dumps(row["tool_call_counts"], ensure_ascii=False, sort_keys=True),
				row.get("final_code"),
				row.get("verify_response"),
				row.get("error"),
				row["created_at"],
			),
		)
		conn.commit()


def benchtest_attempt_count(db_path: Path, task_id: str, model: str, mode: str) -> int:
	if not db_path.exists():
		return 0
	with sqlite3.connect(db_path) as conn:
		row = conn.execute(
			"""
			SELECT COUNT(*)
			FROM benchtest_runs
			WHERE task_id = ? AND model = ? AND mode = ? AND attempt_index > 0
			""",
			(task_id, model, mode),
		).fetchone()
	return int(row[0]) if row is not None else 0


def benchtest_already_passed(db_path: Path, task_id: str, model: str, mode: str) -> bool:
	if not db_path.exists():
		return False
	with sqlite3.connect(db_path) as conn:
		row = conn.execute(
			"""
			SELECT 1
			FROM benchtest_runs
			WHERE task_id = ? AND model = ? AND mode = ? AND status = 'passed'
			LIMIT 1
			""",
			(task_id, model, mode),
		).fetchone()
	return row is not None
