"""MCP server — expose factory operations as tools for other Claude Code sessions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

log = structlog.get_logger()

server = Server("factory")


# ── tool handlers (pure logic, testable without transport) ───────


async def handle_get_score(project_path: str) -> str:
    """Read .factory/last_eval.json and return its contents as JSON text."""
    p = Path(project_path).resolve()
    last_eval = p / ".factory" / "last_eval.json"
    if not last_eval.exists():
        return json.dumps({"error": f"No last_eval.json found at {last_eval}"})
    return last_eval.read_text()


async def handle_list_experiments(project_path: str, last_n: int = 10) -> str:
    """Read .factory/results.tsv, parse last N rows, return as JSON."""
    from factory.store import ExperimentStore

    p = Path(project_path).resolve()
    factory_dir = p / ".factory"
    if not factory_dir.is_dir():
        return json.dumps({"error": f"No .factory/ directory at {p}"})

    store = ExperimentStore(p)
    records = await store.load_history()
    tail = records[-last_n:] if last_n > 0 else records
    return json.dumps(
        [r.model_dump(mode="json") for r in tail],
        indent=2,
        default=str,
    )


async def handle_get_status(project_path: str) -> str:
    """Return project state + config summary."""
    from factory.state import detect_state

    p = Path(project_path).resolve()
    state = detect_state(p)
    result: dict[str, object] = {"project_path": str(p), "state": state.value}

    config_path = p / ".factory" / "config.json"
    if config_path.exists():
        result["config"] = json.loads(config_path.read_text())

    return json.dumps(result, indent=2, default=str)


async def handle_list_projects(projects_dir: str) -> str:
    """Scan for subdirectories containing .factory/config.json."""
    d = Path(projects_dir).resolve()
    if not d.is_dir():
        return json.dumps({"error": f"Directory not found: {d}"})

    projects: list[dict[str, str]] = []
    for child in sorted(d.iterdir()):
        if child.is_dir() and (child / ".factory" / "config.json").exists():
            config = json.loads((child / ".factory" / "config.json").read_text())
            projects.append({
                "name": child.name,
                "path": str(child),
                "goal": config.get("goal", ""),
            })

    return json.dumps(projects, indent=2)


# ── MCP server wiring ───────────────────────────────────────────


_TOOLS = [
    Tool(
        name="factory_get_score",
        description="Get the latest eval score for a factory-managed project",
        inputSchema={
            "type": "object",
            "properties": {
                "project_path": {
                    "type": "string",
                    "description": "Absolute path to the project directory",
                },
            },
            "required": ["project_path"],
        },
    ),
    Tool(
        name="factory_list_experiments",
        description="List recent experiments for a factory-managed project",
        inputSchema={
            "type": "object",
            "properties": {
                "project_path": {
                    "type": "string",
                    "description": "Absolute path to the project directory",
                },
                "last_n": {
                    "type": "integer",
                    "description": "Number of recent experiments to return (default: 10)",
                    "default": 10,
                },
            },
            "required": ["project_path"],
        },
    ),
    Tool(
        name="factory_get_status",
        description="Get the current state and config of a factory-managed project",
        inputSchema={
            "type": "object",
            "properties": {
                "project_path": {
                    "type": "string",
                    "description": "Absolute path to the project directory",
                },
            },
            "required": ["project_path"],
        },
    ),
    Tool(
        name="factory_list_projects",
        description="Scan a directory for factory-managed projects",
        inputSchema={
            "type": "object",
            "properties": {
                "projects_dir": {
                    "type": "string",
                    "description": "Absolute path to the directory containing projects",
                },
            },
            "required": ["projects_dir"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return _TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    handlers = {
        "factory_get_score": lambda args: handle_get_score(args["project_path"]),
        "factory_list_experiments": lambda args: handle_list_experiments(
            args["project_path"], args.get("last_n", 10)
        ),
        "factory_get_status": lambda args: handle_get_status(args["project_path"]),
        "factory_list_projects": lambda args: handle_list_projects(args["projects_dir"]),
    }

    handler = handlers.get(name)
    if handler is None:
        log.warning("mcp.unknown_tool", tool=name)
        return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]

    log.info("mcp.tool_call", tool=name, arguments=arguments)
    try:
        result_text = await handler(arguments)
    except Exception:
        log.exception("mcp.tool_error", tool=name)
        raise
    return [TextContent(type="text", text=result_text)]


async def run_server() -> None:
    """Start the MCP stdio server."""
    log.info("mcp.server_starting")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    """Entry point for the serve-mcp CLI command."""
    asyncio.run(run_server())
