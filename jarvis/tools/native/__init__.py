"""JARVIS Native High-Trust Tools and Central Dispatcher.

Provides native capabilities for filesystem, shell, clock, web fetch, and safe math.
"""

from typing import Any

from jarvis.tools.native.calculator import evaluate_expression
from jarvis.tools.native.clock import get_time
from jarvis.tools.native.code import run_python_test
from jarvis.tools.native.filesystem import delete_file, list_dir, read_file, write_file
from jarvis.tools.native.shell import execute_shell
from jarvis.tools.native.system import get_system_stats
from jarvis.tools.native.web import fetch_url, search_web


async def dispatch_native_tool(tool_id: str, arguments: dict[str, Any]) -> Any:
    """Execute a native capability by its capability ID."""
    if tool_id in ("native:fs:read_file", "fs.read", "read_file", "jarvis_read_file"):
        path = arguments.get("file_path") or arguments.get("path") or ""
        ws = arguments.get("workspace_root")
        return read_file(path, workspace_root=ws)

    if tool_id in ("native:fs:write_file", "fs.write", "write_file", "jarvis_write_file"):
        path = arguments.get("file_path") or arguments.get("path") or ""
        content = arguments.get("content") or ""
        ws = arguments.get("workspace_root")
        return write_file(path, content, workspace_root=ws)

    if tool_id in ("native:fs:delete_file", "fs.delete", "delete_file", "jarvis_delete_file"):
        path = arguments.get("file_path") or arguments.get("path") or ""
        ws = arguments.get("workspace_root")
        return delete_file(path, workspace_root=ws)

    if tool_id in ("native:fs:list_dir", "fs.list", "list_dir", "jarvis_list_dir"):
        path = arguments.get("dir_path") or arguments.get("path") or "."
        ws = arguments.get("workspace_root")
        return list_dir(path, workspace_root=ws)

    if tool_id in ("native:clock:get_time", "clock.get_time", "get_time", "jarvis_get_time"):
        return get_time()

    if tool_id in (
        "native:system:get_stats",
        "system.get_stats",
        "system_stats",
        "jarvis_system_stats",
    ):
        return get_system_stats()

    if tool_id in (
        "native:calc:evaluate",
        "math.evaluate",
        "calculator",
        "jarvis_calc",
        "jarvis_calculator",
    ):
        expr = arguments.get("expression") or arguments.get("expr") or ""
        return evaluate_expression(expr)

    if tool_id in ("native:web:fetch", "web.fetch", "fetch_url", "jarvis_fetch_web"):
        url = arguments.get("url") or ""
        timeout = float(arguments.get("timeout_seconds", 10.0))
        return fetch_url(url, timeout_seconds=timeout)

    if tool_id in ("native:web:search", "web.search", "search_web", "jarvis_search_web"):
        query = arguments.get("query") or arguments.get("q") or ""
        max_results = int(arguments.get("max_results", 5))
        return search_web(query, max_results=max_results)

    if tool_id in (
        "native:code:run_test",
        "code.run_test",
        "run_python_test",
        "jarvis_run_python_test",
        "jarvis_run_test",
    ):
        fpath = arguments.get("file_path") or arguments.get("path") or ""
        mode = arguments.get("mode", "script")
        test_args = arguments.get("test_args")
        timeout = float(arguments.get("timeout_seconds", 30.0))
        ws = arguments.get("workspace_root")
        return await run_python_test(
            file_path=fpath,
            mode=mode,
            test_args=test_args,
            timeout_seconds=timeout,
            workspace_root=ws,
        )

    if tool_id in ("native:shell:execute", "shell.execute", "execute_command", "jarvis_shell"):
        cmd = arguments.get("command") or arguments.get("cmd") or ""
        timeout = float(arguments.get("timeout_seconds", 30.0))
        return await execute_shell(cmd, timeout_seconds=timeout)

    if tool_id in (
        "native:citation:verify",
        "citation.verify",
        "verify_citation",
        "jarvis_verify_citation",
    ):
        from jarvis.tools.native.citation import verify_citation

        return await verify_citation(
            query=arguments.get("query"),
            doi=arguments.get("doi"),
            url=arguments.get("url"),
            claimed_authors=arguments.get("claimed_authors"),
            claimed_year=arguments.get("claimed_year"),
            claimed_title=arguments.get("claimed_title"),
        )

    raise ValueError(f"Unknown native tool ID: '{tool_id}'")


__all__ = [
    "delete_file",
    "dispatch_native_tool",
    "evaluate_expression",
    "execute_shell",
    "fetch_url",
    "get_system_stats",
    "get_time",
    "list_dir",
    "read_file",
    "search_web",
    "write_file",
]
