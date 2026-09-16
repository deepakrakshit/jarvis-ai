"""JARVIS Coding Specialist.

Handles filesystem operations, code inspection, editing, syntax validation,
and test running with LLM intelligence (ARCHITECTURE.md Layer 8 & 10).
"""

import re
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
from jarvis.core.logging import get_logger

logger = get_logger(__name__)

CODING_SYSTEM_PROMPT = """You are the JARVIS Coding Specialist.
Your domain covers filesystem inspection/manipulation, code analysis, debugging, AST review, and sandbox execution.

Available Tools:
- "native:fs:list_dir": Lists files and subdirectories in a directory path.
  Arguments: {"dir_path": string} (use "." for current workspace root)
- "native:fs:read_file": Reads the full text content of a file.
  Arguments: {"file_path": string}
- "native:fs:write_file": Writes text content to a destination file path.
  Arguments: {"file_path": string, "content": string}
- "native:shell:execute": Runs a shell command inside the process sandbox.
  Arguments: {"command": string}

Analyze the user message and conversation context.
You MUST output ONLY a valid JSON object matching this schema:
{
  "action": "tool_call" | "clarify" | "direct_answer",
  "tool_id": "native:fs:list_dir" | "native:fs:read_file" | "native:fs:write_file" | "native:shell:execute" | null,
  "arguments": dict,
  "intent": string,
  "clarification_question": string | null,
  "direct_response": string | null,
  "target_resource": string | null
}

Rules:
1. If the user wants to list files/directories, read a file, write a file, or run a command, and specifies the required file/directory or command, set action='tool_call' and provide tool_id and exact arguments.
2. If the user asks to analyze, inspect, review, or view a file, but DOES NOT specify which file (or asks to create a file without path/content), set action='clarify' and ask a helpful question requesting the file name or path.
3. If it is a conceptual coding question or general code advice that does not need a tool, set action='direct_answer' and provide direct_response.
"""


class CodingSpecialist(BaseSpecialist):
    """Specialist for software engineering, code refactoring, and file operations."""

    def __init__(self, model_gateway: ModelGateway | None = None) -> None:
        super().__init__(
            manifest=SpecialistManifest(
                name="coding",
                role=SpecialistRole.CODING,
                role_description="Code analysis, syntax verification, filesystem read/write, and execution.",
                allowed_tool_scopes=[
                    "native:fs:read_file",
                    "native:fs:write_file",
                    "native:fs:list_dir",
                    "native:shell:execute",
                    "fs.read",
                    "fs.write",
                    "git.read",
                    "git.write",
                    "code.ast",
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
        """Evaluate user request with LLM intelligence or fallback."""
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
                    system_instruction=CODING_SYSTEM_PROMPT,
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
                        or "Which file would you like me to inspect or analyze? Please specify the file name or path.",
                    )

                if action == "tool_call" and data.get("tool_id"):
                    tool_id = str(data["tool_id"])
                    args = data.get("arguments") or {}
                    target = (
                        data.get("target_resource")
                        or args.get("file_path")
                        or args.get("dir_path")
                        or "coding_resource"
                    )
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent=str(data.get("intent", f"Execute {tool_id}")),
                        tool_id=tool_id,
                        arguments=args,
                        target_resource=str(target),
                    )

                if action == "direct_answer" and data.get("direct_response"):
                    return SpecialistProposal(
                        specialist_role=self.role,
                        intent=str(data.get("intent", "Direct answer")),
                        direct_response=str(data["direct_response"]),
                    )

            except Exception as exc:
                logger.warning("coding_specialist_llm_fallback", error=str(exc))

        return self._heuristic_propose(user_message)

    def _heuristic_propose(self, user_message: str) -> SpecialistProposal:
        """Rule-based fallback for offline test suites and network disconnection."""
        msg = user_message.strip()
        lower = msg.lower()

        # 1. Directory listing
        if any(
            w in lower
            for w in ("list files", "show files", "list directory", "list dir", "ls", "dir")
        ):
            path_match = re.search(
                r"(?:in|at|for|directory|dir|folder)\s+(?:the\s+)?(?:directory\s+|folder\s+)?([a-zA-Z0-9_\-\./\\]+)",
                msg,
                re.I,
            )
            dir_path = "."
            if path_match:
                extracted = path_match.group(1).strip()
                if extracted.lower() not in ("the", "a", "this", "my", "our", "current", "here"):
                    dir_path = extracted

            return SpecialistProposal(
                specialist_role=self.role,
                intent=f"List files in directory '{dir_path}'",
                tool_id="native:fs:list_dir",
                arguments={"dir_path": dir_path},
                target_resource=dir_path,
            )

        # 2. File read / analysis without specific file -> clarify
        if any(
            w in lower
            for w in (
                "inspect",
                "view file",
                "read file",
                "analyze file",
                "examine file",
                "show file",
            )
        ) and not re.search(r"([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9]+)", msg):
            return SpecialistProposal(
                specialist_role=self.role,
                intent="Clarify target file",
                needs_clarification=True,
                clarification_question="Which file would you like me to inspect or read? Please specify the file name or path.",
            )

        # 3. File read with path
        if any(
            w in lower
            for w in ("read file", "show file", "cat ", "view file", "open file", "examine file")
        ):
            path_match = re.search(
                r"(?:file\s+|open\s+|view\s+|cat\s+|read\s+)([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9]+)",
                msg,
                re.I,
            )
            if path_match:
                file_path = path_match.group(1).strip()
                return SpecialistProposal(
                    specialist_role=self.role,
                    intent=f"Read file '{file_path}'",
                    tool_id="native:fs:read_file",
                    arguments={"file_path": file_path},
                    target_resource=file_path,
                )

        # 4. File write
        if any(w in lower for w in ("write file", "create file", "save to file", "update file")):
            path_match = re.search(
                r"(?:to|file|create)\s+([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9]+)", msg, re.I
            )
            if not path_match:
                return SpecialistProposal(
                    specialist_role=self.role,
                    intent="Write file",
                    needs_clarification=True,
                    clarification_question="What file name and content would you like me to write? Please provide the path and text.",
                )
            file_path = path_match.group(1).strip()
            content_match = re.search(r'["\'](.*?)["\']', msg, re.DOTALL)
            content = content_match.group(1) if content_match else ""
            if not content:
                return SpecialistProposal(
                    specialist_role=self.role,
                    intent=f"Write file '{file_path}'",
                    needs_clarification=True,
                    clarification_question=f"What content should I write into '{file_path}'?",
                )
            return SpecialistProposal(
                specialist_role=self.role,
                intent=f"Write file '{file_path}'",
                tool_id="native:fs:write_file",
                arguments={"file_path": file_path, "content": content},
                target_resource=file_path,
            )

        # 5. Shell execution
        if any(w in lower for w in ("run test", "run command", "execute command", "run pytest")):
            cmd = "pytest" if "pytest" in lower else msg
            return SpecialistProposal(
                specialist_role=self.role,
                intent="Execute shell command",
                tool_id="native:shell:execute",
                arguments={"command": cmd},
                target_resource="sandbox",
            )

        return SpecialistProposal(
            specialist_role=self.role,
            intent="General coding query",
            direct_response="I am the Coding Specialist. I can read, write, or list files, inspect AST, and run test suites. How can I assist with your code?",
        )

    async def synthesize(
        self,
        proposal: SpecialistProposal,
        tool_result: Any,
        user_message: str,
    ) -> str:
        """Synthesize file/code observation into human-readable response using LLM."""
        if self.gateway:
            try:
                prompt = (
                    f"User asked: '{user_message}'\n"
                    f"Tool '{proposal.tool_id}' was executed with result:\n"
                    f"{tool_result}\n\n"
                    "Synthesize a clear, helpful, accurate response for the user. "
                    "Highlight file paths, counts, and key code observations."
                )
                req = GenerationRequest(
                    model_id="gemini-3.5-flash-lite",
                    system_instruction="You are the JARVIS Coding Specialist. Provide concise, clear, well-formatted observations.",
                    messages=[ChatMessage(role="user", content=prompt)],
                    temperature=0.2,
                    max_tokens=2500,
                )
                res = await self.gateway.generate(req)
                if res.content.strip():
                    return res.content.strip()
            except Exception as exc:
                logger.warning("coding_specialist_synth_fallback", error=str(exc))

        # Heuristic fallback formatting
        if proposal.tool_id == "native:fs:read_file":
            content = tool_result.get("content", "")
            path = tool_result.get("file_path", "")
            lines = content.splitlines()
            preview = "\n".join(lines[:30])
            summary = f"Read **{path}** ({len(lines)} lines, {tool_result.get('size_bytes', 0)} bytes):\n\n```\n{preview}\n```"
            if len(lines) > 30:
                summary += f"\n*(Truncated {len(lines) - 30} remaining lines)*"
            return summary

        if proposal.tool_id == "native:fs:write_file":
            return f"Successfully wrote **{tool_result.get('bytes_written', 0)} bytes** to `{tool_result.get('file_path')}`."

        if proposal.tool_id == "native:fs:list_dir":
            entries = tool_result.get("entries", [])
            items = []
            for e in entries:
                kind = "[DIR]" if e.get("is_dir") else f"[{e.get('size_bytes')} B]"
                items.append(f"- `{e.get('name')}` {kind}")
            return (
                f"Contents of **{tool_result.get('dir_path')}** ({tool_result.get('count', 0)} items):\n"
                + "\n".join(items)
            )

        if proposal.tool_id == "native:shell:execute":
            stdout = tool_result.get("stdout", "")
            stderr = tool_result.get("stderr", "")
            code = tool_result.get("exit_code", 0)
            return f"Executed command in sandbox (Exit Code: {code}):\n```\n{stdout or stderr}\n```"

        return str(tool_result)
