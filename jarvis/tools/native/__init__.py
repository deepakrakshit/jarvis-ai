"""JARVIS Native High-Trust Tools and Central Dispatcher.

Provides native capabilities for filesystem, shell, clock, web fetch, and safe math.
"""

from typing import Any

from jarvis.tools.native.app_control import (
    close_application,
    launch_application,
    list_running_applications,
)
from jarvis.tools.native.calculator import evaluate_expression
from jarvis.tools.native.clock import get_time
from jarvis.tools.native.code import run_python_test
from jarvis.tools.native.filesystem import delete_file, list_dir, read_file, write_file
from jarvis.tools.native.screen_control import (
    capture_screen,
    click_coordinate,
    get_active_window,
    press_hotkey,
    type_text,
)
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

    if tool_id in (
        "sandbox:code:execute",
        "code.execute",
        "sandbox_code_execute",
        "jarvis_sandbox_code",
    ):
        from jarvis.tools.native.sandbox_code import execute_in_sandbox

        cmd = arguments.get("command") or arguments.get("cmd") or []
        timeout = float(arguments.get("timeout_seconds", 30.0))
        files = arguments.get("files")
        profile_id = str(arguments.get("profile_id", "sandbox_python_exec"))
        allow_td = bool(arguments.get("allow_test_doubles", False))
        return await execute_in_sandbox(
            command=cmd,
            timeout_seconds=timeout,
            files=files,
            profile_id=profile_id,
            allow_test_doubles=allow_td,
        )

    if tool_id in (
        "native:artifact:read_slice",
        "artifact.read_slice",
        "read_artifact_slice",
        "jarvis_read_artifact_slice",
    ):
        from jarvis.tools.native.artifact import read_artifact_slice

        return read_artifact_slice(
            task_id=str(arguments.get("task_id", "")),
            artifact_id=str(arguments.get("artifact_id", "")),
            offset=int(arguments.get("offset", 0)),
            limit=int(arguments.get("limit", 100)),
            artifacts_dir=arguments.get("artifacts_dir"),
        )

    if tool_id in (
        "native:artifact:get_metadata",
        "artifact.get_metadata",
        "get_artifact_metadata",
        "jarvis_artifact_metadata",
    ):
        from jarvis.tools.native.artifact import get_artifact_metadata

        return get_artifact_metadata(
            task_id=str(arguments.get("task_id", "")),
            artifact_id=str(arguments.get("artifact_id", "")),
            artifacts_dir=arguments.get("artifacts_dir"),
        )

    if tool_id in (
        "native:memory:write",
        "memory.write",
        "write_memory",
        "jarvis_write_memory",
    ):
        from jarvis.tools.native.memory import write_memory

        return write_memory(
            category=arguments.get("category", "EPISODIC_NOTE"),
            key=arguments.get("key", ""),
            content=arguments.get("content", ""),
            expected_version=arguments.get("expected_version"),
            tenant_id=arguments.get("tenant_id", "default"),
            user_id=arguments.get("user_id", "default_user"),
            epistemic_status=arguments.get("epistemic_status", "PROPOSED"),
            source_trust_level=arguments.get("source_trust_level", "EXTERNAL_UNTRUSTED"),
            db_path=arguments.get("db_path"),
        )

    if tool_id in (
        "native:memory:query",
        "memory.query",
        "query_memory",
        "jarvis_query_memory",
    ):
        from jarvis.tools.native.memory import query_memory

        return query_memory(
            category=arguments.get("category"),
            key=arguments.get("key"),
            as_of=arguments.get("as_of"),
            tenant_id=arguments.get("tenant_id", "default"),
            user_id=arguments.get("user_id", "default_user"),
            db_path=arguments.get("db_path"),
        )

    if tool_id.startswith("computer:"):
        from jarvis.computer import get_computer_executor

        executor = get_computer_executor()
        action_res = await executor.execute_capability(tool_id, arguments)
        return action_res.model_dump()

    if tool_id in (
        "native:screen:scroll",
        "jarvis_scroll",
        "computer:mouse:scroll",
        "computer:scroll",
    ):
        from jarvis.computer import get_computer_executor

        executor = get_computer_executor()
        direction = str(arguments.get("direction", "down"))
        amount = int(arguments.get("amount", arguments.get("clicks", 3)))
        clicks = -abs(amount) if direction == "down" else abs(amount)
        res = executor.scroll(clicks=clicks)
        return res.model_dump()

    if tool_id in ("jarvis_focus_window", "computer:window:focus", "computer:focus_window"):
        from jarvis.computer import get_computer_executor

        executor = get_computer_executor()
        win = str(
            arguments.get("window_title") or arguments.get("title") or arguments.get("query") or ""
        )
        res = executor.focus_window(query=win)
        return res.to_structured_dict()

    if tool_id in ("jarvis_navigate_browser", "computer:navigate_browser", "navigate_browser"):
        from jarvis.computer import get_computer_executor

        executor = get_computer_executor()
        url = str(arguments.get("url") or arguments.get("target") or "")
        browser = str(arguments.get("browser") or "chrome")
        res = executor.navigate_browser(url=url, browser=browser)
        return res.to_structured_dict()

    if tool_id in ("jarvis_inspect_ui", "computer:ui:inspect", "computer:inspect_ui", "inspect_ui"):
        from jarvis.computer import get_computer_executor

        executor = get_computer_executor()
        raw_win = arguments.get("window_title") or arguments.get("title")
        win_str: str | None = str(raw_win) if raw_win is not None else None
        res = executor.inspect_ui(window_title=win_str)
        return res.model_dump()

    if tool_id in ("jarvis_resolve_target", "computer:resolve_target", "resolve_target"):
        from jarvis.computer import get_computer_executor

        executor = get_computer_executor()
        action_res = await executor.execute_capability("computer:resolve_target", arguments)
        return action_res.model_dump()

    if tool_id in ("jarvis_click_element", "computer:click_element", "click_element"):
        from jarvis.computer import get_computer_executor

        executor = get_computer_executor()
        action_res = await executor.execute_capability("computer:click_element", arguments)
        return action_res.model_dump()

    if tool_id in ("native:app:launch", "app.launch", "launch_app", "jarvis_launch_app"):
        from jarvis.tools.native.app_control import launch_application

        return launch_application(
            app_name=arguments.get("app_name") or arguments.get("name") or "",
            args=arguments.get("args") or arguments.get("arguments"),
        )

    if tool_id in ("native:app:close", "app.close", "close_app", "jarvis_close_app"):
        from jarvis.tools.native.app_control import close_application

        return close_application(
            app_name=arguments.get("app_name")
            or arguments.get("target")
            or arguments.get("name")
            or "",
            force=bool(arguments.get("force", False)),
        )

    if tool_id in ("native:app:list", "app.list", "list_apps", "jarvis_list_apps"):
        from jarvis.tools.native.app_control import list_running_applications

        return list_running_applications(
            filter_name=arguments.get("filter_name") or arguments.get("filter"),
            limit=int(arguments.get("limit", 50)),
        )

    if tool_id in (
        "native:screen:capture",
        "screen.capture",
        "capture_screen",
        "jarvis_screenshot",
    ):
        from jarvis.tools.native.screen_control import capture_screen

        return capture_screen(
            output_format=str(arguments.get("output_format", "base64")),
            max_dimension=int(arguments.get("max_dimension", 1280)),
            quality=int(arguments.get("quality", 80)),
            monitor_index=int(arguments.get("monitor_index", 1)),
        )

    if tool_id in ("native:screen:click", "screen.click", "click_coordinate", "jarvis_click"):
        from jarvis.tools.native.screen_control import click_coordinate

        return click_coordinate(
            x=arguments.get("x"),
            y=arguments.get("y"),
            button=str(arguments.get("button", "left")),
            clicks=int(arguments.get("clicks", 1)),
            normalized=bool(arguments.get("normalized", False)),
            element_query=arguments.get("element_query"),
            window_title=arguments.get("window_title"),
            semantic_role=arguments.get("semantic_role"),
        )

    if tool_id in ("native:screen:type", "screen.type", "type_text", "jarvis_type"):
        from jarvis.tools.native.screen_control import type_text

        return type_text(
            text=str(arguments.get("text", "")),
            press_enter=bool(arguments.get("press_enter", False)),
            interval=float(arguments.get("interval", 0.01)),
            element_query=arguments.get("element_query"),
            window_title=arguments.get("window_title"),
            recipient=arguments.get("recipient"),
        )

    if tool_id in ("native:screen:hotkey", "screen.hotkey", "press_hotkey", "jarvis_hotkey"):
        from jarvis.tools.native.screen_control import press_hotkey

        raw_keys = arguments.get("keys") or []
        keys_list = (
            [k.strip() for k in raw_keys.split("+")]
            if isinstance(raw_keys, str)
            else list(raw_keys)
        )
        return press_hotkey(keys=keys_list)

    if tool_id in (
        "native:screen:get_window",
        "screen.get_window",
        "get_active_window",
        "jarvis_get_window",
    ):
        from jarvis.tools.native.screen_control import get_active_window

        return get_active_window()

    raise ValueError(f"Unknown native tool ID: '{tool_id}'")


__all__ = [
    "capture_screen",
    "click_coordinate",
    "close_application",
    "delete_file",
    "dispatch_native_tool",
    "evaluate_expression",
    "execute_shell",
    "fetch_url",
    "get_active_window",
    "get_system_stats",
    "get_time",
    "launch_application",
    "list_dir",
    "list_running_applications",
    "press_hotkey",
    "query_memory",
    "read_file",
    "search_web",
    "type_text",
    "write_file",
    "write_memory",
]
