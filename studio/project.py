"""Project folders: one folder per video under projects/."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from studio.script import to_markdown, validate_script

MAX_SLUG_LENGTH = 50


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:MAX_SLUG_LENGTH].rstrip("-") or "video"


def new_project_dir(projects_dir: Path, title: str, fmt: str, today: date | None = None) -> Path:
    stamp = (today or date.today()).strftime("%Y%m%d")
    base = projects_dir / f"{stamp}-{fmt}-{slugify(title)}"
    candidate = base
    counter = 2
    while candidate.exists():
        candidate = base.with_name(f"{base.name}-{counter}")
        counter += 1
    return candidate


def save_script(project_dir: Path, script: dict) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "script.json").write_text(
        json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (project_dir / "script.md").write_text(to_markdown(script), encoding="utf-8")


def load_script(project_dir: Path) -> dict:
    path = project_dir / "script.json"
    if not path.is_file():
        raise FileNotFoundError(f"No script.json in {project_dir}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return validate_script(data, data.get("format", "long"))
