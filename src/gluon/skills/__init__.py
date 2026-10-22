"""Agent skills shipped with the package: `gluon-setup` (embed Gluon in a host) and `gluon-agent`
(use the probe correctly). `python -m gluon skills` or `install_skills(root)` writes them into a
project for Claude Code (`.claude/skills`), Codex (`.agents/skills`) or both (the default)."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

SKILL_NAMES = ("gluon-setup", "gluon-agent")
TARGETS = {"claude": Path(".claude") / "skills", "codex": Path(".agents") / "skills"}


def skill_text(name: str) -> str:
    if name not in SKILL_NAMES:
        raise ValueError(f"unknown skill {name!r}; available: {', '.join(SKILL_NAMES)}")
    return resources.files(__package__).joinpath(name, "SKILL.md").read_text(encoding="utf-8")


def install_skills(root: str | Path = ".", target: str = "both", force: bool = False) -> list[Path]:
    """Write each skill to <root>/<agent dir>/<name>/SKILL.md; refuse to overwrite unless force.

    target: "claude" (.claude/skills), "codex" (.agents/skills) or "both".
    """
    if target not in (*TARGETS, "both"):
        raise ValueError(f"unknown target {target!r}; choose claude, codex or both")
    chosen = list(TARGETS) if target == "both" else [target]
    base = Path(root)
    planned = [(base / TARGETS[t] / name / "SKILL.md", name) for t in chosen for name in SKILL_NAMES]
    if not force:
        existing = [str(path) for path, _ in planned if path.exists()]
        if existing:
            raise FileExistsError("already present (pass --force to overwrite): " + ", ".join(existing))
    written: list[Path] = []
    for path, name in planned:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(skill_text(name), encoding="utf-8", newline=chr(10))
        written.append(path)
    return written
