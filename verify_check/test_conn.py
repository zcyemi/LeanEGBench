from __future__ import annotations

import json
import http.client
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = os.environ.get("LEAN_EXPLORE_BASE_URL", "http://localhost:8580")
QUERY = "Nat"
LIMIT = 10


def build_url(base_url: str = BASE_URL, query: str = QUERY, limit: int = LIMIT) -> str:
	clean_base = base_url.rstrip("/")
	params = urllib.parse.urlencode({"q": query, "limit": str(limit)})
	return f"{clean_base}/search?{params}"


def fetch_payload(url: str) -> dict[str, object]:
	request = urllib.request.Request(
		url,
		headers={"Accept": "application/json"},
		method="GET",
	)
	with urllib.request.urlopen(request, timeout=30) as response:
		body = response.read().decode("utf-8")
		payload = json.loads(body)
		if not isinstance(payload, dict):
			raise ValueError("LeanExplore response is not a JSON object")
		return payload


def main() -> int:
	url = build_url()
	print(f"Testing LeanExplore connectivity: {url}")

	try:
		payload = fetch_payload(url)
	except (http.client.RemoteDisconnected, ConnectionAbortedError, ConnectionResetError):
		print("Request failed: the LeanExplore server accepted the connection but is still warming up.")
		return 1
	except urllib.error.HTTPError as exc:
		detail = exc.read().decode("utf-8", errors="replace")
		print(f"HTTP {exc.code}: {detail or exc.reason}")
		return 1
	except urllib.error.URLError as exc:
		print(f"Request failed: {exc.reason}")
		return 1
	except json.JSONDecodeError as exc:
		print(f"Invalid JSON response: {exc}")
		return 1
	except ValueError as exc:
		print(str(exc))
		return 1

	results = payload.get("results") if isinstance(payload, dict) else None
	count = payload.get("count") if isinstance(payload, dict) else None
	print("Connection OK")
	print(f"count={count!r}")
	if isinstance(results, list):
		print(f"results={len(results)}")
	else:
		print("results=unavailable")
	return 0


if __name__ == "__main__":
	sys.exit(main())
