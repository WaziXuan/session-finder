#!/usr/bin/env python3
"""Minimal stdio MCP server that exposes the local Session Finder CLI to Amazon Quick Desktop.

No third-party dependency: speaks newline-delimited JSON-RPC 2.0 over stdin/stdout,
which is the MCP stdio transport. Every tool shells out to find_session.py so the
index, chunking and source adapters stay in one place.
"""

import json
import subprocess
import sys
from pathlib import Path

PY = r"C:\Users\OOF\.workbuddy\skills\session-finder\.venv\Scripts\python.exe"
SCRIPT = r"C:\Users\OOF\.workbuddy\skills\session-finder\scripts\find_session.py"

if not Path(PY).is_file():
    PY = sys.executable

SOURCES = ("all", "wb", "cx", "cl", "cb", "qd", "ag", "ds", "qk")
MAX_CHARS = 12000


def cli(argv, timeout=90):
    try:
        p = subprocess.run([PY, SCRIPT, *argv], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "error: session-finder timed out", True
    except OSError as e:
        return f"error: cannot start session-finder: {e}", True
    out = p.stdout.decode("utf-8", "replace")
    err = p.stderr.decode("utf-8", "replace").strip()
    text = out.strip()
    if err:
        text += "\n[stderr] " + err
    if not text:
        text = "(no output)"
    # "no session matched" is a valid empty result, not a tool failure.
    not_found = "no session matched" in text.lower() or "no matches" in text.lower()
    return text[:MAX_CHARS], p.returncode != 0 and not not_found


def source_arg(args):
    s = args.get("source", "all")
    return [] if s in ("", "all") else ["--source", s]


TOOLS = [
    {
        "name": "list_sessions",
        "description": "List the most recent indexed conversations. source: all|wb|cx|cl|cb|qd|ag|ds|qk (wb=WorkBuddy, cx=Codex, cl=Claude Code, cb=CodeBuddy, qd=Qoder, ag=Antigravity, ds=DeepSeek Harness, qk=Amazon Quick). Use this when the user asks what they talked about recently.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "enum": SOURCES, "default": "all"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
        },
    },
    {
        "name": "find_sessions",
        "description": "Find sessions by title or first user message. query may be natural language, e.g. 'model identity' or 'session finder build'. Returns session ids with titles; follow up with show_session to read content.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "source": {"type": "string", "enum": SOURCES, "default": "all"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "grep_sessions",
        "description": "Full-text search inside all indexed session transcripts. Use for exact words, code symbols, file names or phrases the user remembers saying.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "source": {"type": "string", "enum": SOURCES, "default": "all"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "show_session",
        "description": "Read a slice of one session. ref accepts a source prefix and id prefix, e.g. 'ag|5b754e44', 'cl|b0599fab', 'ds|f863a3e0', or a bare id prefix. role: user|assistant|tool|raw|all.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "role": {"type": "string", "enum": ["user", "assistant", "tool", "raw", "all"], "default": "user"},
                "head": {"type": "integer", "minimum": 1, "maximum": 400, "default": 40},
                "max_chars": {"type": "integer", "minimum": 100, "maximum": 8000, "default": 400},
            },
            "required": ["ref"],
        },
    },
    {
        "name": "sync_sessions",
        "description": "Refresh the local index and copy new session excerpts into the shared SessionSync archive so other machines can search them. Run when the user asks to store sessions or says a recent conversation is missing.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def call_tool(name, args):
    if name == "list_sessions":
        argv = ["list", *source_arg(args), "--limit", str(args.get("limit", 20))]
        return cli(argv)
    if name == "find_sessions":
        if not args.get("query"):
            return "error: query is required", True
        argv = ["find", str(args["query"]), *source_arg(args), "--limit", str(args.get("limit", 20))]
        return cli(argv)
    if name == "grep_sessions":
        if not args.get("pattern"):
            return "error: pattern is required", True
        argv = ["grep", str(args["pattern"]), *source_arg(args)]
        return cli(argv)
    if name == "show_session":
        if not args.get("ref"):
            return "error: ref is required", True
        argv = ["show", str(args["ref"]), "--role", str(args.get("role", "user")),
                "--head", str(args.get("head", 40)), "--max-chars", str(args.get("max_chars", 400))]
        return cli(argv)
    if name == "sync_sessions":
        return cli(["sync"], timeout=300)
    return f"error: unknown tool {name}", True


def handle(msg):
    method = msg.get("method")
    mid = msg.get("id")

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    if method == "initialize":
        return ok({
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "session-finder", "version": "2026.11.25.4"},
        })
    if method == "ping":
        return ok({})
    if method == "notifications/initialized" or method == "notifications/cancelled":
        return None
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        text, is_error = call_tool(params.get("name", ""), params.get("arguments") or {})
        return ok({"content": [{"type": "text", "text": text}], "isError": is_error})
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}


def main():
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        response = handle(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
