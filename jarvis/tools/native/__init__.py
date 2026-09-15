"""JARVIS Native High-Trust Tools and Central Dispatcher.

Provides native capabilities for filesystem, shell, clock, web fetch, and safe math.
"""

from typing import Any

from jarvis.tools.native.calculator import evaluate_expression
from jarvis.tools.native.clock import get_time
from jarvis.tools.native.filesystem import list_dir, read_file, write_file
from jarvis.tools.native.shell import execute_shell
from jarvis.tools.native.web import fetch_url, search_web


async def dispatch_native_tool(tool_id: str, arguments: dict[str, Any]) -> Any:
    """Execute a native capability by its capability ID."""
    if tool_id in ("native:fs:read_file", "fs.read", "read_file"):
        path = arguments.get("file_path") or arguments.get("path") or ""
        return read_file(path)

    if tool_id in ("native:fs:write_file", "fs.write", "write_file"):
        path = arguments.get("file_path") or arguments.get("path") or ""
        content = arguments.get("content") or ""
        return write_file(path, content)

    if tool_id in ("native:fs:list_dir", "fs.list", "list_dir"):
        path = arguments.get("dir_path") or arguments.get("path") or "."
        return list_dir(path)

    if tool_id in ("native:clock:get_time", "clock.get_time", "get_time"):
        return get_time()

    if tool_id in ("native:calc:evaluate", "math.evaluate", "calculator"):
        expr = arguments.get("expression") or arguments.get("expr") or ""
        return evaluate_expression(expr)

    if tool_id in ("native:web:fetch", "web.fetch", "fetch_url"):
        url = arguments.get("url") or ""
        return fetch_url(url)

    if tool_id in ("native:web:search", "web.search", "search_web"):
        query = arguments.get("query") or arguments.get("q") or ""
        max_results = int(arguments.get("max_results", 5))
        return search_web(query, max_results=max_results)

    if tool_id in ("native:shell:execute", "shell.execute", "execute_command"):
        cmd = arguments.get("command") or arguments.get("cmd") or ""
        timeout = float(arguments.get("timeout_seconds", 30.0))
        return await execute_shell(cmd, timeout_seconds=timeout)

    raise ValueError(f"Unknown native tool ID: '{tool_id}'")


__all__ = [
    "dispatch_native_tool",
    "evaluate_expression",
    "execute_shell",
    "fetch_url",
    "get_time",
    "list_dir",
    "read_file",
    "search_web",
    "write_file",
]
