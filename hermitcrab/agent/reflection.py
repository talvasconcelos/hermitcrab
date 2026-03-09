"""
Reflection: First-person self-improvement.

After each session (or 30-min inactivity), the agent reflects:
- What did I learn about this user?
- How can I be more helpful next time?
- What patterns should I remember?

Output: 0-1 reflection file + optional bootstrap update.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import json_repair
from loguru import logger

if TYPE_CHECKING:
    from hermitcrab.agent.memory import MemoryStore
    from hermitcrab.providers.base import LLMProvider


class ReflectionService:
    """
    First-person reflection service.

    Single LLM call → 0-1 reflection → auto-promote if pattern.
    """

    SYSTEM_PROMPT = """You are reflecting on your recent conversation with the user.

Think about:
- What did you learn about how THIS user prefers to work?
- Did the user correct you? What should you remember?
- Did you notice any patterns in what the user asks or needs?
- How could you be more helpful, proactive, or symbiotic next time?

Be specific and actionable. Write for YOUR future self.

DO NOT log bugs or tool errors - those are code issues, not reflections.
DO NOT create multiple reflections - pick the ONE most valuable insight.
"""

    USER_PROMPT = """Review this conversation and extract your key learning.

Recent conversation:
{messages}

{recent_reflections_section}

Respond with JSON:
{{
  "title": "Short, descriptive title",
  "content": "What did you learn? Write in first person: 'I learned...', 'I should...', 'The user prefers...'",
  "type": "preference|correction|pattern|insight|workflow",
  "should_promote": true,
  "promote_to": "AGENTS.md|TOOLS.md|SOUL.md|IDENTITY.md|none",
  "promote_content": "Specific instruction for your future self"
}}

If nothing worth remembering, respond: {{"skip": true, "reason": "No new insights"}}

Rules:
- ONE insight only (pick the most valuable)
- First-person voice ("I learned...", not "The assistant should...")
- Check recent_reflections - don't duplicate what you already learned
- promote_content should be actionable instruction for bootstrap files
"""

    def __init__(
        self,
        memory: MemoryStore,
        provider: LLMProvider,
        model: str,
    ):
        """
        Initialize reflection service.

        Args:
            memory: Memory store for reading/writing reflections.
            provider: LLM provider for generating reflections.
            model: Model to use for reflection generation.
        """
        self.memory = memory
        self.provider = provider
        self.model = model

    async def reflect_on_session(
        self,
        messages: list[dict],
        session_key: str,
    ) -> None:
        """
        Reflect on a session and extract learnings.

        Args:
            messages: Session messages to analyze.
            session_key: Session identifier.
        """
        try:
            # Skip empty sessions
            if not messages:
                logger.debug("Reflection skipped: empty session {}", session_key)
                return

            # 1. Load recent reflections for dedup context
            recent = self.memory.list_memories("reflections")[:5]

            # 2. Build prompt
            messages_text = self._format_messages(messages)
            recent_section = self._format_recent_reflections(recent)

            user_prompt = self.USER_PROMPT.format(
                messages=messages_text,
                recent_reflections_section=recent_section,
            )

            # 3. Single LLM call
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                model=self.model,
                temperature=0.1,
                max_tokens=512,
            )

            # 4. Parse response
            result = self._parse_response(response.content)

            if result.get("skip"):
                logger.debug("Reflection skipped: {}", result.get("reason", "no insights"))
                return

            # 5. Validate required fields
            if not result.get("title") or not result.get("content"):
                logger.warning("Reflection missing required fields: {}", result)
                return

            # 6. Write reflection
            self._write_reflection(result, session_key)

            # 7. Auto-promote if flagged
            if result.get("should_promote") and result.get("promote_content"):
                await self._promote(result)

            logger.info("Reflection complete: {}", result.get("title", "unknown"))

        except Exception as e:
            logger.warning("Reflection failed (non-fatal): {}", e)

    def _format_messages(self, messages: list[dict]) -> str:
        """Format messages for prompt (truncated if needed)."""
        # Keep last 20 messages to stay in context
        recent = messages[-20:]
        lines = []
        for msg in recent:
            role = msg.get("role", "unknown")
            content = (msg.get("content") or "")[:500]  # Truncate long messages
            lines.append(f"{role}: {content}")
        return "\n\n".join(lines)

    def _format_recent_reflections(self, recent: list) -> str:
        """Format recent reflections for dedup context."""
        if not recent:
            return "No recent reflections."

        lines = ["Recent reflections (avoid duplicating):"]
        for i, ref in enumerate(recent[:5], 1):
            if ref is None:
                continue
            content_preview = (ref.content or "")[:100].replace("\n", " ")
            lines.append(f"{i}. {ref.title}: {content_preview}...")
        return "\n".join(lines)

    def _parse_response(self, content: str | None) -> dict:
        """Parse LLM JSON response."""
        if not content:
            return {"skip": True, "reason": "Invalid response format"}

        try:
            # Extract JSON from response
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = content[start:end]
                result = json_repair.loads(json_str)
                if isinstance(result, dict):
                    return result
        except Exception as e:
            logger.warning("Failed to parse reflection JSON: {}", e)

        return {"skip": True, "reason": "Invalid response format"}

    def _write_reflection(self, result: dict, session_key: str) -> None:
        """Write reflection to memory."""
        reflection_type = result.get("type", "insight")
        tags = [reflection_type, "reflection", "learning"]

        # Build context from promote info
        context_parts = []
        if result.get("should_promote"):
            context_parts.append(f"Marked for promotion to {result.get('promote_to', 'unknown')}")

        self.memory.write_reflection(
            title=result["title"],
            content=result["content"],
            tags=tags,
            context="\n".join(context_parts) if context_parts else None,
        )

    async def _promote(self, result: dict) -> None:
        """Auto-promote reflection to bootstrap file."""
        target_file = result.get("promote_to", "AGENTS.md")
        content = result.get("promote_content")

        if not content:
            return

        # Handle pipe-separated values or "none" (LLM may return multiple options)
        if "|" in target_file:
            # Extract first valid option from pipe-separated list
            for option in target_file.split("|"):
                option = option.strip()
                if option in ["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"]:
                    target_file = option
                    break
            else:
                target_file = "AGENTS.md"

        # Validate target file
        valid_files = ["AGENTS.md", "TOOLS.md", "SOUL.md", "IDENTITY.md"]
        if target_file not in valid_files:
            logger.warning("Invalid promote target: {}", target_file)
            target_file = "AGENTS.md"

        # Map reflection type to section header
        section_map = {
            "preference": "## User Preferences",
            "correction": "## Corrections & Learnings",
            "pattern": "## Observed Patterns",
            "insight": "## Insights",
            "workflow": "## Workflow Notes",
        }
        section = section_map.get(result.get("type", "insight"), "## Learnings")

        # Append to bootstrap file
        file_path = self.memory.workspace / target_file
        self._append_to_bootstrap(file_path, section, content)

        logger.info("Auto-promoted reflection to {}: {}", target_file, result["title"])

    def _append_to_bootstrap(self, file_path: Path, section: str, content: str) -> None:
        """Append content to bootstrap file section."""
        if not file_path.exists():
            file_path.write_text(f"{section}\n\n{content}\n")
            return

        existing = file_path.read_text(encoding="utf-8")

        if section in existing:
            # Append to existing section
            lines = existing.split("\n")
            new_lines = []
            in_section = False
            inserted = False

            for i, line in enumerate(lines):
                new_lines.append(line)

                # Detect section end (next ## header or EOF)
                if line.strip() == section:
                    in_section = True
                elif in_section and line.startswith("## "):
                    # Insert before next section
                    if not inserted:
                        new_lines.insert(-1, "")
                        new_lines.insert(-1, content)
                        new_lines.insert(-1, "")
                        inserted = True
                    in_section = False

            # If still in section at EOF, append
            if in_section and not inserted:
                new_lines.append("")
                new_lines.append(content)
                new_lines.append("")

            file_path.write_text("\n".join(new_lines), encoding="utf-8")
        else:
            # Create new section at end
            separator = "\n\n" if existing and not existing.endswith("\n\n") else ""
            file_path.write_text(f"{existing}{separator}{section}\n\n{content}\n", encoding="utf-8")
