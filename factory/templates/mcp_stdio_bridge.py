#!/usr/bin/env python3
"""A stdio MCP server that forwards to an HTTP one.

Why this exists: Claude Code probes `/.well-known/oauth-protected-resource/<path>` before
connecting to an MCP server registered as `type: http`. Inside an OpenShell sandbox that probe is
denied — an `mcp`-protocol policy endpoint matches only its own path — and a 403 reads to the client
as "this resource is protected". It then exposes `authenticate` instead of the server's tools, and
that OAuth flow cannot complete in a headless run. The same server connects fine from outside the
sandbox. Registering the server as `stdio` skips discovery entirely; this process is what stdio
then talks to, and it speaks plain HTTP to the endpoint the policy already allows.

The policy still does its job: every tool call crosses the wire as an MCP request to the same
allowlisted endpoint, so the tool-name enforcement is unchanged. What moves is only where the
client's transport terminates.

Stdlib only, by necessity — the sandbox's network policy has no rule that would let pip run.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("FACTORY_MCP_BRIDGE_URL", "")
TIMEOUT = float(os.environ.get("FACTORY_MCP_BRIDGE_TIMEOUT", "600"))

# Some MCP servers implement DNS-rebinding protection by allowlisting Host values, and reject
# anything they do not recognise: kubernetes-mcp-server answers
# `403 Forbidden: invalid Host header "host.openshell.internal:8440"` to a request that arrives via
# OpenShell's bridge hostname. Overriding the header lets the request through without changing where
# it is actually sent — the connection still goes to the host in the URL.
HOST_HEADER = os.environ.get("FACTORY_MCP_BRIDGE_HOST_HEADER", "")

# The streamable-HTTP transport answers either a bare JSON body or an SSE stream, depending on the
# request, so both have to be understood.
_ACCEPT = "application/json, text/event-stream"


def _log(message: str) -> None:
    """Diagnostics go to stderr: stdout is the MCP channel and must carry nothing else."""
    print(f"[mcp-bridge] {message}", file=sys.stderr, flush=True)


def _extract(body: str, content_type: str) -> list[dict]:
    """Return the JSON-RPC messages in a response body."""
    if "text/event-stream" in content_type:
        messages = []
        for line in body.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    messages.append(json.loads(payload))
        return messages
    body = body.strip()
    if not body:
        return []
    parsed = json.loads(body)
    return parsed if isinstance(parsed, list) else [parsed]


def main() -> int:
    if not URL:
        _log("FACTORY_MCP_BRIDGE_URL is unset; nothing to forward to")
        return 2

    session_id = ""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        headers = {"Content-Type": "application/json", "Accept": _ACCEPT}
        if HOST_HEADER:
            headers["Host"] = HOST_HEADER
        if session_id:
            headers["mcp-session-id"] = session_id
        request = urllib.request.Request(URL, data=line.encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                # The session id arrives on the initialize response and is required on every
                # request after it; without it the server starts a new session per call and the
                # client's initialized handshake is never associated with anything.
                session_id = response.headers.get("mcp-session-id", session_id)
                messages = _extract(
                    response.read().decode("utf-8", "replace"),
                    response.headers.get("Content-Type", ""),
                )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            _log(f"HTTP {exc.code} from upstream: {body[:300]}")
            messages = _error_for(line, f"upstream returned HTTP {exc.code}: {body[:300]}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            _log(f"transport failure: {exc}")
            messages = _error_for(line, f"could not reach the MCP endpoint: {exc}")

        for message in messages:
            sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()
    return 0


def _error_for(request_line: str, reason: str) -> list[dict]:
    """A JSON-RPC error carrying the request's id, so the client fails the call rather than hanging.

    A notification has no id and expects no reply; answering one would be a protocol violation.
    """
    try:
        request_id = json.loads(request_line).get("id")
    except (json.JSONDecodeError, AttributeError):
        return []
    if request_id is None:
        return []
    return [{"jsonrpc": "2.0", "id": request_id, "error": {"code": -32001, "message": reason}}]


if __name__ == "__main__":
    sys.exit(main())
