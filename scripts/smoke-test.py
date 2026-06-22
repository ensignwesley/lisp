#!/usr/bin/env python3
"""Deployed smoke test for Wesley's Lisp static REPL.

Checks the public page contract without browser-driver dependencies: the live page
must be reachable, carry the expected REPL shell, expose core controls, and
include the embedded evaluator/runtime markers that make the page functional.
"""
from __future__ import annotations

import sys
import urllib.error
import urllib.request

DEFAULT_URL = "https://wesley.thesisko.com/lisp/"
TIMEOUT = 10

REQUIRED_MARKERS = {
    "title": "Lisp REPL · Wesley",
    "header": "λ LISP",
    "input": 'id="input"',
    "run button": 'id="btn-run"',
    "examples tab": "Factorial (recursive)",
    "runtime welcome": "Welcome to Wesley\\'s Lisp",
    "evaluator": "function evaluate",
    "parser": "function parse",
    "stdlib": "define('map'",
}


def fail(message: str) -> None:
    print(f"not ok lisp smoke: {message}", file=sys.stderr)
    raise SystemExit(1)


def fetch(url: str) -> tuple[int, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "lisp-smoke/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:  # noqa: S310 - operator-supplied URL
            charset = response.headers.get_content_charset() or "utf-8"
            return response.status, response.headers.get_content_type(), response.read().decode(charset, "replace")
    except urllib.error.HTTPError as exc:
        fail(f"HTTP {exc.code} from {url}")
    except Exception as exc:  # noqa: BLE001 - CLI smoke test should report any transport failure
        fail(f"fetch failed for {url}: {exc}")


def main(argv: list[str]) -> int:
    url = argv[1] if len(argv) > 1 else DEFAULT_URL
    status, content_type, body = fetch(url)
    if status != 200:
        fail(f"expected HTTP 200, got {status}")
    if content_type != "text/html":
        fail(f"expected text/html, got {content_type}")

    missing = [name for name, marker in REQUIRED_MARKERS.items() if marker not in body]
    if missing:
        fail("missing deployed markers: " + ", ".join(missing))

    print(f"ok lisp smoke {url} bytes={len(body)} markers={len(REQUIRED_MARKERS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
