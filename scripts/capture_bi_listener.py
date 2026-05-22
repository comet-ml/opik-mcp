"""Tiny local listener that records every BI POST and prints the body.

Run in one terminal; point ``OPIK_MCP_ANALYTICS_URL`` at it from another.
This is the realistic verification path: real opik-mcp subprocess, real
httpx daemon thread, real flush() — only the destination URL changes.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw)
            pretty = json.dumps(body, indent=2, sort_keys=True)
        except json.JSONDecodeError:
            pretty = raw.decode("utf-8", errors="replace")
        print(f"\n--- POST {self.path} ---\n{pretty}\n", flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"success":true}')

    def log_message(self, fmt: str, *args: object) -> None:  # silence noise
        return


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    srv = HTTPServer(("127.0.0.1", port), _Handler)
    print(f"BI capture listening on http://127.0.0.1:{port}/notify/event/", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
