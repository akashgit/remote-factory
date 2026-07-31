#!/usr/bin/env python3
"""Host-header rewriting proxy for kubernetes-mcp-server.

Operator infrastructure, not factory code. kubernetes-mcp-server validates the HTTP Host header
(DNS-rebinding protection in the Go MCP SDK) and answers
`403 Forbidden: invalid Host header "host.openshell.internal:8440"` to anything arriving through
OpenShell's bridge hostname. This listens on the bridge-facing port and forwards to the real server
with a Host it accepts.

    python3 host_rewrite_proxy.py 8440 8441
"""

from __future__ import annotations

import http.server
import sys
import urllib.error
import urllib.request

LISTEN_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8440
UPSTREAM_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8441
UPSTREAM = f"http://127.0.0.1:{UPSTREAM_PORT}"

_HOP_BY_HOP = {"host", "connection", "keep-alive", "transfer-encoding", "upgrade"}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _forward(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP_BY_HOP}
        headers["Host"] = f"127.0.0.1:{UPSTREAM_PORT}"
        request = urllib.request.Request(
            UPSTREAM + self.path, data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                payload = response.read()
                status, out_headers = response.status, response.headers
        except urllib.error.HTTPError as exc:
            payload, status, out_headers = exc.read(), exc.code, exc.headers
        except OSError as exc:
            payload, status, out_headers = str(exc).encode(), 502, {}

        self.send_response(status)
        for key, value in (out_headers or {}).items():
            if key.lower() not in _HOP_BY_HOP | {"content-length"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802
        self._forward("POST")

    def do_GET(self) -> None:  # noqa: N802
        self._forward("GET")

    def do_DELETE(self) -> None:  # noqa: N802
        self._forward("DELETE")

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[proxy] {fmt % args}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    print(f"listening on 0.0.0.0:{LISTEN_PORT} → {UPSTREAM}", file=sys.stderr, flush=True)
    http.server.ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler).serve_forever()
