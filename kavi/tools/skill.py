"""Skill tool - load a skill's full instructions on demand.

Skills advertise only a name + description in the system prompt (progressive
disclosure). When the model decides a skill is relevant, it calls this tool with
the skill name to pull the full ``SKILL.md`` body (and a list of bundled files)
into context - keeping detailed instructions out of the prompt until needed.

The loaded skills are provided to the tool via ``ctx.extras["skills"]`` (a mapping
of name -> Skill), wired up at startup.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from kavi.tools.base import Tool, ToolContext, ToolResult


class SkillInput(BaseModel):
    name: str = Field(description="The name of the skill to load.")


class SkillTool(Tool):
    name = "Skill"
    description = """
    Load the full instructions for a named skill. Skills provide expert, step-by-step
    guidance for specific tasks; the available skills (name and description) are listed in
    your system prompt. Call this with a skill's name to read its detailed instructions
    before proceeding with a matching task. Read-only.
    """
    InputModel = SkillInput
    is_read_only = True

    def render_call(self, data: SkillInput) -> str:  # type: ignore[override]
        return f"Skill {data.name}"

    async def run(self, data: SkillInput, ctx: ToolContext) -> ToolResult:  # type: ignore[override]
        skills = ctx.extras.get("skills") or {}
        skill = skills.get(data.name)
        if skill is None:
            available = ", ".join(sorted(skills)) or "(none)"
            return ToolResult.error(
                f"Unknown skill: {data.name}. Available skills: {available}"
            )
        parts = [f"# Skill: {skill.name}\n", skill.body]
        bundled = skill.bundled_files()
        if bundled:
            listing = "\n".join(f"  - {f}" for f in bundled)
            parts.append(
                f"\n\nBundled files (relative to the skill directory {skill.path.parent}):\n"
                + listing
            )
        return ToolResult(
            content="\n".join(parts),
            title=f"Skill {skill.name}",
            display=skill.description,
        )
