"""Read a SKILL.md and return its body without YAML frontmatter."""

from __future__ import annotations

from pathlib import Path


def skill_body(skill_dir: Path) -> str:
    """Return the body of `<skill_dir>/SKILL.md` with frontmatter stripped.

    Skills include the base path implicitly. We replace `{skill_path}` placeholder
    if present so each SDK sees an absolute path it can use in Bash commands.
    """
    md = (skill_dir / "SKILL.md").read_text()
    body = _strip_frontmatter(md, skill_dir / "SKILL.md")
    return body.replace("{skill_path}", str(skill_dir.resolve()))


def _strip_frontmatter(md: str, path: Path) -> str:
    """Remove a leading YAML frontmatter block.

    Tolerates trailing whitespace on the fence lines and either Unix or
    Windows line endings. If no opening fence is present, returns ``md`` as is.
    Raises if an opening fence is present but no matching closing fence is
    found — that indicates a malformed SKILL.md, not an intentionally
    fence-less body, and silently dropping the body would mask the bug.
    """
    lines = md.splitlines()
    if not lines or lines[0].strip() != "---":
        return md
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1 :])
    raise ValueError(f"Malformed frontmatter in {path}: opening '---' has no closing fence")
