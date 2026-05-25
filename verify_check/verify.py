import json
import http.client
import os
import urllib.error
import urllib.request
from pathlib import Path


VERIFY_URL = os.environ.get("LEAN_VERIFY_URL", "http://localhost:8578/verify")
CODE_PATH = Path(__file__).with_name("test.txt")


def load_code() -> str:
	return CODE_PATH.read_text(encoding="utf-8")


def post_verify(code: str) -> object:
	payload = json.dumps(
		{
			"code": code,
			"wait_timeout": 0,
			"diagnostic_timeout": 0,
		},
		ensure_ascii=False,
	).encode("utf-8")
	request = urllib.request.Request(
		VERIFY_URL,
		data=payload,
		headers={"Accept": "application/json", "Content-Type": "application/json"},
		method="POST",
	)
	with urllib.request.urlopen(request, timeout=120) as response:
		body = response.read().decode("utf-8")
		return json.loads(body)


def main() -> None:
	code = load_code()
	try:
		response = post_verify(code)
	except (http.client.RemoteDisconnected, ConnectionAbortedError, ConnectionResetError):
		print("Request failed: the verification server accepted the connection but is not ready to return a response yet.")
		return
	except urllib.error.HTTPError as exc:
		detail = exc.read().decode("utf-8", errors="replace")
		print(f"HTTP {exc.code}: {detail or exc.reason}")
		return
	except urllib.error.URLError as exc:
		print(f"Request failed: {exc.reason}")
		return
	except json.JSONDecodeError as exc:
		print(f"Invalid JSON response: {exc}")
		return

	print(json.dumps(response, ensure_ascii=False, indent=2))


if __name__ == "__main__":
	main()
