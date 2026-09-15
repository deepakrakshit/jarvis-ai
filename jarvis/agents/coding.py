"""JARVIS Coding Specialist.

Handles filesystem operations, code inspection, editing, syntax validation,
and test running (ARCHITECTURE.md Layer 8 & 10).
"""

import re
from typing import Any

from jarvis.agents.base import (
    BaseSpecialist,
    SpecialistManifest,
    SpecialistProposal,
    SpecialistRole,
)


class CodingSpecialist(BaseSpecialist):
    """Specialist for software engineering, code refactoring, and file operations."""

    def __init__(self) -> None:
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
            )
        )

    async def propose(
        self,
        user_message: str,
        context: dict[str, Any] | None = None,
    ) -> SpecialistProposal:
        """Evaluate user request for code or file operations."""
        msg = user_message.strip()
        lower = msg.lower()

        # 1. Check for directory listing requests
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

        # 2. Check for file analysis or inspection requests without file
        if any(
            w in lower
            for w in (
                "analyze file",
                "analyze a file",
                "inspect file",
                "inspect a file",
                "analyze code",
                "analyze a code",
            )
        ):
            path_match = re.search(
                r"(?:file|code)\s+([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9]+)",
                msg,
                re.I,
            )
            if not path_match:
                return SpecialistProposal(
                    specialist_role=self.role,
                    intent="Analyze code file",
                    needs_clarification=True,
                    clarification_question="Which file would you like me to inspect or analyze? Please specify the file name or path.",
                )

        # 2. Check for file read requests
        if any(
            w in lower
            for w in ("read file", "show file", "cat ", "view file", "open file", "examine file")
        ):
            path_match = re.search(
                r"(?:file\s+|open\s+|view\s+|cat\s+|read\s+)([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9]+)",
                msg,
                re.I,
            )
            if not path_match:
                # Ambiguous read request -> Ask question!
                return SpecialistProposal(
                    specialist_role=self.role,
                    intent="Read file",
                    needs_clarification=True,
                    clarification_question="Which file would you like me to read? Please specify the file name or relative path.",
                )
            file_path = path_match.group(1).strip()
            return SpecialistProposal(
                specialist_role=self.role,
                intent=f"Read file '{file_path}'",
                tool_id="native:fs:read_file",
                arguments={"file_path": file_path},
                target_resource=file_path,
            )

        # 3. Check for file write requests
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
            # Extract content if quoted, or ask
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

        # 4. Check for shell or test execution
        if any(w in lower for w in ("run test", "run command", "execute command", "run pytest")):
            cmd = "pytest" if "pytest" in lower else msg
            return SpecialistProposal(
                specialist_role=self.role,
                intent="Execute shell command",
                tool_id="native:shell:execute",
                arguments={"command": cmd},
                target_resource="sandbox",
            )

        # Fallback: general code question
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
        """Synthesize file/code observation into human-readable response."""
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
                kind = "[DIR]" if e["is_dir"] else f"[{e['size_bytes']} B]"
                items.append(f"- `{e['name']}` {kind}")
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
