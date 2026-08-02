"""Skills - on-demand expert instructions (progressive disclosure).

A skill is a directory containing a ``SKILL.md`` file with frontmatter:

    ---
    name: pdf-processing
    description: Extract text and fill forms in PDF files
    ---
    <detailed instructions, optionally referencing bundled scripts/files>

Only the name + description are advertised to the model up front (in the system
prompt). When a task matches, the model calls the ``Skill`` tool, which returns
the full SKILL.md body (plus a list of bundled files) - so detailed instructions
only enter the context when actually needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kavi.extensions import frontmatter as fm
from kavi.extensions import sources

_MAX_BODY = 60_000


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path  # the SKILL.md file

    def bundled_files(self) -> list[str]:
        """Other files shipped alongside SKILL.md (scripts, templates, refs)."""
        out: list[str] = []
        root = self.path.parent
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.name != "SKILL.md":
                out.append(str(p.relative_to(root)))
        return out


def load_skills(workspace: Path) -> dict[str, Skill]:
    """Discover skills across all source roots. First definition of a name wins."""
    found: dict[str, Skill] = {}
    for base in sources.dirs(workspace, "skills"):
        for skill_md in sorted(base.glob("*/SKILL.md")):
            try:
                meta, body = fm.parse(skill_md.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            name = str(meta.get("name") or skill_md.parent.name).strip()
            if not name or name in found:
                continue
            found[name] = Skill(
                name=name,
                description=str(meta.get("description") or "").strip() or "(no description)",
                body=body.strip()[:_MAX_BODY],
                path=skill_md,
            )
    return found
