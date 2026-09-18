"""JARVIS Computer Specialist.

Handles system monitoring, operating system state, clock verification,
and environment management with LLM intelligence (ARCHITECTURE.md Layer 8 & 10).
"""

from typing import Any

from jarvis.agents.base import (
    BaseSpecialist,
    SpecialistManifest,
    SpecialistProposal,
    SpecialistRole,
    parse_llm_json,
)
from jarvis.core.gateway.interfaces import ChatMessage, GenerationRequest
from jarvis.core.gateway.router import ModelGateway
from jarvis.core.lifecycle.types import HealthProbeResult
from jarvis.core.logging import get_logger

logger = get_logger(__name__)

COMPUTER_SYSTEM_PROMPT = """You are the JARVIS Computer Specialist.
Your domain covers system diagnostics, current clock time, date verification, OS environment status, CPU/RAM stats, desktop application lifecycle (launching, closing, and listing apps), screen perception (screenshots), and GUI input automation (mouse clicks, typing, hotkeys).

Available Tools:
- "native:clock:get_time": Retrieves point-in-time ISO timestamp, UTC time, local time, and day of week.
  Arguments: {}
- "native:system:get_stats": Retrieves host operating system, CPU usage %, memory usage, disk usage, and uptime.
  Arguments: {}
- "native:app:launch": Launches a desktop application or program asynchronously.
  Arguments: {"app_name": string (e.g. "notepad", "calc", "chrome"), "args": list[str] (optional)}
- "native:app:close": Closes or terminates running application processes by name or PID.
  Arguments: {"app_name": string (e.g. "notepad", "calc"), "force": bool (default false)}
- "native:app:list": Lists active running applications and processes on the system.
  Arguments: {"filter_name": string (optional), "limit": int (default 50)}
- "native:screen:capture": Captures the current desktop display frame for visual grounding.
  Arguments: {"output_format": "base64" | "bytes" (default "base64"), "max_dimension": int (default 1280)}
- "native:screen:click": Injects a mouse click at specific coordinates.
  Arguments: {"x": float | int, "y": float | int, "button": "left" | "right" | "middle" (default "left"), "clicks": int (default 1), "normalized": bool (default false)}
- "native:screen:type": Injects text or keystrokes into the active focused window.
  Arguments: {"text": string, "press_enter": bool (default false)}
- "native:screen:hotkey": Triggers a keyboard hotkey combination.
  Arguments: {"keys": list[str] (e.g. ["ctrl", "s"], ["alt", "tab"])}
- "native:screen:get_window": Inspects the active foreground desktop window.
  Arguments: {}

Analyze the user's message.
You MUST output ONLY a valid JSON object matching this schema:
{
  "action": "tool_call" | "clarify" | "direct_answer",
  "tool_id": "native:clock:get_time" | "native:system:get_stats" | "native:app:launch" | "native:app:close" | "native:app:list" | "native:screen:capture" | "native:screen:click" | "native:screen:type" | "native:screen:hotkey" | "native:screen:get_window" | null,
  "arguments": dict,
  "intent": string,
  "clarification_question": string | null,
  "direct_response": string | null,
  "target_resource": string | null
}

Rules:
1. If the user asks for the current time, date, timestamp, clock, or what day it is, set action='tool_call', tool_id='native:clock:get_time', arguments={}, target_resource='system_clock'.
2. If the user asks for system stats, performance, CPU, memory, RAM, disk, host hardware, or system health metrics, set action='tool_call', tool_id='native:system:get_stats', arguments={}, target_resource='host_system'.
3. If the user asks to open, launch, or start an application/program (e.g. "open notepad", "launch calculator", "start chrome"), set action='tool_call', tool_id='native:app:launch', arguments={"app_name": "<name>"}, target_resource='host_system'.
4. If the user asks to close, exit, terminate, quit, or kill an application/program (e.g. "close notepad", "quit spotify", "kill process 1234"), set action='tool_call', tool_id='native:app:close', arguments={"app_name": "<name>"}, target_resource='host_system'.
5. If the user asks to list running applications, active tasks, or check what apps are open, set action='tool_call', tool_id='native:app:list', arguments={}, target_resource='host_system'.
6. If the user asks to take a screenshot, capture the screen, or see what is on display, set action='tool_call', tool_id='native:screen:capture', arguments={}, target_resource='desktop_screen'.
7. If the user asks to click at coordinates, set action='tool_call', tool_id='native:screen:click', arguments={"x": <x>, "y": <y>}, target_resource='desktop_screen'.
8. If the user asks to type text or press keys, set action='tool_call', tool_id='native:screen:type' or 'native:screen:hotkey', arguments=..., target_resource='desktop_screen'.
9. If the user asks what window is active or focused, set action='tool_call', tool_id='native:screen:get_window', arguments={}, target_resource='desktop_screen'.
10. If the user asks to reboot, shutdown, or execute dangerous destructive OS operations, set action='clarify' and ask for explicit user confirmation before proceeding.
11. If it is a query about system architecture or environment, set action='direct_answer' and explain the current status.
"""


class ComputerSpecialist(BaseSpecialist):
    """Specialist for OS control, desktop GUI automation, and runtime environment management."""

    def __init__(self, model_gateway: ModelGateway | None = None) -> None:
        super().__init__(
            manifest=SpecialistManifest(
                name="computer",
                role=SpecialistRole.COMPUTER,
                role_description="OS automation, application lifecycle, GUI screen perception, and desktop control.",
                allowed_tool_scopes=[
                    "native:clock:get_time",
                    "native:system:get_stats",
                    "native:app:launch",
                    "native:app:close",
                    "native:app:list",
                    "native:screen:capture",
                    "native:screen:click",
                    "native:screen:type",
                    "native:screen:hotkey",
                    "native:screen:get_window",
                    "native:shell:execute",
                    "os.window",
                ],
                memory_mode="PER_SPECIALIST",
            ),
            model_gateway=model_gateway,
        )

    async def propose(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
    ) -> SpecialistProposal:
        """Evaluate computer/system request with LLM intelligence or fallback."""
        if self.gateway:
            try:
                history_str = ""
                if context and "history" in context and context["history"]:
                    recent = context["history"][-3:]
                    lines = []
                    for turn in recent:
                        lines.append(f"User: {turn.get('user_message')}")
                        lines.append(f"JARVIS: {str(turn.get('assistant_response', ''))[:250]}")
                    history_str = "Recent Conversation History:\n" + "\n".join(lines) + "\n\n"

                intent_str = (
                    f"Classified Intent: {context.get('intent')}\n"
                    if context and context.get("intent")
                    else ""
                )
                full_content = f"{history_str}{intent_str}Current User Request: {user_message}"

                messages = [ChatMessage(role="user", content=full_content)]
                req = GenerationRequest(
                    model_id="gemini-3.5-flash-lite",
                    system_instruction=COMPUTER_SYSTEM_PROMPT,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=500,
                )
                resp = await self.gateway.generate(req)
                data = parse_llm_json(resp.content)
                action = data.get("action", "direct_answer")

                if action == "clarify":
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent=str(data.get("intent", "Clarification needed")),
                        needs_clarification=True,
                        clarification_question=data.get("clarification_question")
                        or "Please provide confirmation before proceeding with this system action.",
                    )

                if action == "tool_call" and data.get("tool_id"):
                    tool_id = str(data["tool_id"])
                    args = data.get("arguments") or {}
                    if "clock" in tool_id:
                        target = "system_clock"
                    elif "screen" in tool_id or "window" in tool_id:
                        target = "desktop_screen"
                    else:
                        target = "host_system"
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent=str(data.get("intent", f"Execute {tool_id}")),
                        tool_id=tool_id,
                        arguments=args,
                        target_resource=target,
                    )

                if action == "direct_answer" and data.get("direct_response"):
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent=str(data.get("intent", "System environment query")),
                        direct_response=str(data["direct_response"]),
                    )

            except Exception as exc:
                logger.warning("computer_specialist_llm_fallback", error=str(exc))

        return self._heuristic_propose(user_message)

    def _heuristic_propose(self, user_message: str) -> SpecialistProposal:
        """Rule-based fallback for offline test suites and network disconnection."""
        msg = user_message.strip()
        lower = msg.lower()

        # Check for time or date queries
        if any(
            w in lower for w in ("time", "date", "clock", "what day", "current time", "timestamp")
        ):
            return SpecialistProposal(
                specialist_role=self.role,
                intent="Get system clock time",
                tool_id="native:clock:get_time",
                arguments={},
                target_resource="system_clock",
            )

        # Check for system/OS performance stats
        if any(
            w in lower
            for w in (
                "system stats",
                "system status",
                "cpu",
                "memory",
                "ram",
                "disk",
                "hardware",
                "system metrics",
                "system info",
            )
        ):
            return SpecialistProposal(
                specialist_role=self.role,
                intent="Get system hardware and resource stats",
                tool_id="native:system:get_stats",
                arguments={},
                target_resource="host_system",
            )

        # Check for screen capture / screenshot
        if any(
            w in lower
            for w in (
                "screenshot",
                "capture screen",
                "take screenshot",
                "screen capture",
                "see my screen",
                "what's on my screen",
            )
        ):
            return SpecialistProposal(
                specialist_role=self.role,
                intent="Capture desktop screen frame",
                tool_id="native:screen:capture",
                arguments={"output_format": "base64"},
                target_resource="desktop_screen",
            )

        # Check for active window inspection
        if any(
            w in lower
            for w in ("active window", "current window", "focused window", "front window")
        ):
            return SpecialistProposal(
                specialist_role=self.role,
                intent="Inspect active foreground window",
                tool_id="native:screen:get_window",
                arguments={},
                target_resource="desktop_screen",
            )

        # Check for listing running apps
        if any(
            w in lower
            for w in (
                "list apps",
                "running apps",
                "running processes",
                "active apps",
                "what apps are running",
                "what apps are open",
                "show processes",
            )
        ):
            return SpecialistProposal(
                specialist_role=self.role,
                intent="List active running applications",
                tool_id="native:app:list",
                arguments={},
                target_resource="host_system",
            )

        # Check for closing an app
        for prefix in (
            "close app ",
            "close ",
            "quit app ",
            "quit ",
            "terminate ",
            "kill app ",
            "kill process ",
            "stop app ",
        ):
            if lower.startswith(prefix):
                target = msg[len(prefix) :].strip().rstrip(".!?")
                if target:
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent=f"Close application '{target}'",
                        tool_id="native:app:close",
                        arguments={"app_name": target},
                        target_resource="host_system",
                    )

        # Check for opening an app
        for prefix in (
            "open app ",
            "open ",
            "launch app ",
            "launch ",
            "start app ",
            "start ",
            "run app ",
        ):
            if lower.startswith(prefix):
                target = msg[len(prefix) :].strip().rstrip(".!?")
                if target and not target.startswith(("http://", "https://")):
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent=f"Launch application '{target}'",
                        tool_id="native:app:launch",
                        arguments={"app_name": target},
                        target_resource="host_system",
                    )

        # Check for reboot / shutdown
        if any(w in lower for w in ("restart", "reboot", "shutdown", "power off")):
            return SpecialistProposal(
                specialist_role=self.role,
                intent="System power state change",
                needs_clarification=True,
                clarification_question="Are you sure you want to reboot or shutdown? Please provide confirmation by replying with 'CONFIRM' to proceed.",
            )

        return SpecialistProposal(
            specialist_role=self.role,
            intent="General computer query",
            direct_response="I am the Computer Specialist. I can check current time, inspect CPU/RAM/disk metrics, manage desktop applications, and perceive the screen.",
        )

    async def synthesize(
        self,
        proposal: SpecialistProposal,
        tool_result: Any,
        user_message: str,
    ) -> str:
        """Synthesize clock, application lifecycle, or screen observation into human-readable response."""
        if isinstance(tool_result, dict):
            if proposal.tool_id == "native:clock:get_time":
                self.scratchpad.add_note(f"Checked clock: {tool_result.get('local_iso')}")
            elif proposal.tool_id == "native:system:get_stats":
                self.scratchpad.add_note(
                    f"Host status: CPU {tool_result.get('cpu_percent')}% | RAM {tool_result.get('memory_percent')}%"
                )
            elif proposal.tool_id == "native:app:launch":
                self.scratchpad.add_note(
                    f"Launched app: {tool_result.get('app_name')} (PID {tool_result.get('pid')})"
                )
            elif proposal.tool_id == "native:app:close":
                self.scratchpad.add_note(f"Closed app: {tool_result.get('app_name')}")

        if self.gateway:
            try:
                prompt = (
                    f"User asked: '{user_message}'\n"
                    f"Tool result: {tool_result}\n\n"
                    "State the results clearly, naturally, and concisely."
                )
                req = GenerationRequest(
                    model_id="gemini-3.5-flash-lite",
                    system_instruction="You are the JARVIS Computer Specialist. Present system, application, and screen results crisply.",
                    messages=[ChatMessage(role="user", content=prompt)],
                    temperature=0.1,
                    max_tokens=1000,
                )
                res = await self.gateway.generate(req)
                if res.content.strip():
                    return res.content.strip()
            except Exception as exc:
                logger.warning("computer_specialist_synth_fallback", error=str(exc))

        if isinstance(tool_result, str):
            return tool_result

        if not isinstance(tool_result, dict):
            return str(tool_result)

        if proposal.tool_id == "native:clock:get_time":
            local = tool_result.get("local_iso", "")
            utc = tool_result.get("utc_iso", "")
            day = tool_result.get("day_of_week", "")
            return f"Current Time: **{local}** ({day}) [UTC: `{utc}`]"

        if proposal.tool_id == "native:system:get_stats":
            p = tool_result.get("platform", "Unknown")
            rel = tool_result.get("platform_release", "")
            cpu = tool_result.get("cpu_percent", 0.0)
            cpu_count = tool_result.get("cpu_count", 1)
            mem_pct = tool_result.get("memory_percent", 0.0)
            mem_used = tool_result.get("memory_used_gb", 0.0)
            mem_total = tool_result.get("memory_total_gb", 0.0)
            disk_pct = tool_result.get("disk_percent", 0.0)
            uptime = tool_result.get("uptime_seconds", 0)
            uptime_hrs = round(uptime / 3600.0, 1)

            return (
                f"**Host System Health Metrics**:\n\n"
                f"- **OS**: {p} {rel}\n"
                f"- **CPU**: {cpu}% ({cpu_count} logical cores)\n"
                f"- **Memory**: {mem_pct}% used ({mem_used} GB / {mem_total} GB)\n"
                f"- **Disk**: {disk_pct}% used\n"
                f"- **Uptime**: ~{uptime_hrs} hours ({uptime}s)"
            )

        if proposal.tool_id == "native:app:launch":
            msg = tool_result.get("message") if isinstance(tool_result, dict) else str(tool_result)
            return f"**Application Launched**: {msg}"

        if proposal.tool_id == "native:app:close":
            msg = tool_result.get("message") if isinstance(tool_result, dict) else str(tool_result)
            return f"**Application Closed**: {msg}"

        if proposal.tool_id == "native:app:list" and isinstance(tool_result, dict):
            count = tool_result.get("count", 0)
            procs = tool_result.get("processes", [])[:15]
            lines = [f"**Active Applications ({count} detected)**:\n"]
            for proc in procs:
                lines.append(
                    f"- **{proc.get('name')}** (PID: {proc.get('pid')}, Memory: {proc.get('memory_percent')}%)"
                )
            return "\n".join(lines)

        if proposal.tool_id == "native:screen:capture":
            if isinstance(tool_result, dict) and tool_result.get("status") == "success":
                w = tool_result.get("width")
                h = tool_result.get("height")
                b = tool_result.get("byte_size")
                return f"**Desktop Screen Captured**: {w}x{h} pixels ({b} bytes, JPEG format)."
            msg = tool_result.get("message") if isinstance(tool_result, dict) else str(tool_result)
            return f"**Screen Capture**: {msg}"

        if proposal.tool_id == "native:screen:click":
            msg = tool_result.get("message") if isinstance(tool_result, dict) else str(tool_result)
            return f"**Mouse Click Executed**: {msg}"

        if proposal.tool_id == "native:screen:type":
            msg = tool_result.get("message") if isinstance(tool_result, dict) else str(tool_result)
            return f"**Keyboard Input Executed**: {msg}"

        if proposal.tool_id == "native:screen:hotkey":
            msg = tool_result.get("message") if isinstance(tool_result, dict) else str(tool_result)
            return f"**Hotkey Triggered**: {msg}"

        if proposal.tool_id == "native:screen:get_window":
            if isinstance(tool_result, dict) and tool_result.get("status") == "success":
                t = tool_result.get("title")
                w = tool_result.get("width")
                h = tool_result.get("height")
                left_pos = tool_result.get("left")
                top_pos = tool_result.get("top")
                return f"**Active Window**: '{t}' ({w}x{h} at position ({left_pos}, {top_pos}))"
            msg = tool_result.get("message") if isinstance(tool_result, dict) else str(tool_result)
            return f"**Window Inspection**: {msg}"

        return str(tool_result)

    async def health_probe(self) -> HealthProbeResult:
        """Run diagnostic health check on computer specialist capabilities."""
        return HealthProbeResult(
            component_id="specialist:computer",
            healthy=True,
            details={
                "capabilities": [
                    "native:clock:get_time",
                    "native:system:get_stats",
                    "native:app:launch",
                    "native:app:close",
                    "native:app:list",
                    "native:screen:capture",
                    "native:screen:click",
                    "native:screen:type",
                    "native:screen:hotkey",
                    "native:screen:get_window",
                ]
            },
        )
