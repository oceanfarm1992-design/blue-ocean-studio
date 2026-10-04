"""Weekly batch sized for free plans: 1 long video and up to 7 Shorts per channel.

Buffer's free plan allows 10 scheduled posts per channel, so a week of 8 posts fits.
Batch files store project paths relative to the project root, so the same batch works
on this PC and on the GitHub Actions runner.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from studio.buffer import BufferClient, BufferError
from studio.config import Config
from studio.ideas import generate_ideas
from studio.pipeline import render_project
from studio.project import load_script, new_project_dir, save_script
from studio.publish import PublishError, build_post_input, build_texts, publish_project
from studio.script import generate_script, unchecked_scenes
from studio.shorts import plan_shorts, scene_durations

WEEK_SHORTS = 7
STANDALONE_SHORTS = 2
IDEAS_TO_DRAW_FROM = 8
FREE_PLAN_POST_LIMIT = 10


def next_monday(today: date) -> date:
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


def pick_ideas(ideas: list[dict], shorts: int = STANDALONE_SHORTS) -> tuple[dict, list[dict]]:
    """First long idea, then the first short ideas (falling back to any remaining idea)."""
    if not ideas:
        raise PublishError("No ideas came back. Try again or pass --focus.")
    long_idea = next((i for i in ideas if i["format"] == "long"), ideas[0])
    rest = [i for i in ideas if i is not long_idea]
    preferred = [i for i in rest if i["format"] == "short"]
    others = [i for i in rest if i["format"] != "short"]
    return long_idea, (preferred + others)[:shorts]


def schedule_times(start: date, long_at: time, short_at: time,
                   shorts: int) -> tuple[datetime, list[datetime]]:
    """Long video on day one; one Short a day from day one onwards."""
    long_time = datetime.combine(start, long_at)
    short_times = [datetime.combine(start + timedelta(days=d), short_at) for d in range(shorts)]
    return long_time, short_times


def first_day(planned: date, today: date) -> date:
    """The planned Monday, or tomorrow if the review finished after that day started."""
    return max(planned, today + timedelta(days=1))


def _rel(cfg: Config, path: Path) -> str:
    try:
        return path.resolve().relative_to(cfg.root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _abs(cfg: Config, path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else cfg.root / candidate


def _load(batch_path: Path) -> dict:
    if not batch_path.is_file():
        raise FileNotFoundError(f"No batch file at {batch_path}")
    return json.loads(batch_path.read_text(encoding="utf-8"))


def _save(batch_path: Path, batch: dict) -> None:
    batch_path.parent.mkdir(parents=True, exist_ok=True)
    batch_path.write_text(json.dumps(batch, indent=2), encoding="utf-8")


def _write(cfg: Config, topic: str, fmt: str) -> str:
    print(f"  writing {fmt}: {topic}")
    script = generate_script(cfg, topic, fmt)
    project_dir = new_project_dir(cfg.projects_dir, script["title"], fmt)
    save_script(project_dir, script)
    return _rel(cfg, project_dir)


def plan_week(cfg: Config, focus: str = "", today: date | None = None) -> Path:
    """Writes 1 long and 2 standalone short scripts for review. About 1-3 cents of OpenAI."""
    start = next_monday(today or date.today())
    long_idea, short_ideas = pick_ideas(generate_ideas(cfg, IDEAS_TO_DRAW_FROM, focus))
    batch = {
        "week_of": start.isoformat(),
        "long": _write(cfg, long_idea["title"], "long"),
        "standalone_shorts": [_write(cfg, i["title"], "short") for i in short_ideas],
        "cut_shorts": [],
    }
    batch_path = cfg.projects_dir / "weeks" / f"{start.isoformat()}.json"
    _save(batch_path, batch)
    return batch_path


def unchecked_projects(cfg: Config, batch: dict) -> list[Path]:
    paths = [_abs(cfg, p) for p in [batch["long"], *batch["standalone_shorts"]]]
    return [p for p in paths if unchecked_scenes(load_script(p))]


def build_week(cfg: Config, batch_path: Path, force: bool = False) -> dict:
    """Renders the long video, cuts Shorts from it, renders the standalone Shorts."""
    batch = _load(batch_path)
    long_dir = _abs(cfg, batch["long"])
    print(f"Rendering long video: {long_dir.name}")
    render_project(cfg, long_dir, force=force)

    script = load_script(long_dir)
    room = WEEK_SHORTS - len(batch["standalone_shorts"])
    cuts = plan_shorts(script, scene_durations(long_dir, script), cfg.channel["follow_line"], room)
    cut_dirs = []
    for i, short in enumerate(cuts, start=1):
        short_dir = long_dir / "shorts" / f"short_{i:02d}"
        save_script(short_dir, short)
        print(f"Rendering cut Short {i}/{len(cuts)}")
        render_project(cfg, short_dir, force=force)
        cut_dirs.append(_rel(cfg, short_dir))
    for path in batch["standalone_shorts"]:
        print(f"Rendering Short: {Path(path).name}")
        render_project(cfg, _abs(cfg, path), force=force)

    batch = {**batch, "cut_shorts": cut_dirs}
    _save(batch_path, batch)
    return batch


def check_free_limit(cfg: Config, services: list[str], new_posts: int) -> None:
    """Refuses to schedule more than Buffer's free plan allows per channel."""
    client = BufferClient(cfg.buffer_key or "")
    channels = [c for c in client.all_channels() if c["service"] in services]
    by_org: dict[str, list[dict]] = {}
    for channel in channels:
        by_org.setdefault(channel["organizationId"], []).append(channel)
    limit = int((cfg.publish or {}).get("post_limit", FREE_PLAN_POST_LIMIT))
    for org_id, org_channels in by_org.items():
        counts = client.scheduled_counts(org_id, [c["id"] for c in org_channels])
        for channel in org_channels:
            total = counts.get(channel["id"], 0) + new_posts
            if total > limit:
                raise PublishError(
                    f"{channel['service']} would have {total} scheduled posts; the free plan "
                    f"allows {limit}. Wait for some to go out, or save as drafts instead.")


def schedule_week(cfg: Config, batch_path: Path, start: date | None, long_at: time,
                  short_at: time, draft: bool) -> None:
    batch = _load(batch_path)
    if not batch["cut_shorts"]:
        raise PublishError("Run 'week build' first so the Shorts exist.")
    shorts = [*batch["cut_shorts"], *batch["standalone_shorts"]][:WEEK_SHORTS]
    day_one = start or date.fromisoformat(batch["week_of"])
    long_time, short_times = schedule_times(day_one, long_at, short_at, len(shorts))
    services = list((cfg.publish or {}).get("services", []))
    if not draft:
        check_free_limit(cfg, services, 1 + len(shorts))

    plan = [(batch["long"], long_time), *zip(shorts, short_times)]
    for project, when in plan:
        print(f"\n{when:%a %d %b %H:%M}  {Path(project).name}")
        publish_project(cfg, _abs(cfg, project), services, when.isoformat(), draft)
    _save(batch_path, {**batch, "scheduled_at": datetime.now().isoformat(timespec="seconds")})


def _post_content(cfg: Config, project_dir: Path, service: str, video_url: str) -> dict:
    """Text, video and network settings for an edit, rebuilt from the project files."""
    script = load_script(project_dir)
    full = build_post_input(service, "unused", script, build_texts(cfg, project_dir, script),
                            video_url, "customScheduled", None, False, cfg.publish or {})
    return {key: full[key] for key in ("text", "assets", "metadata") if key in full}


def promote_project(cfg: Config, client: BufferClient, project_dir: Path,
                    now: datetime) -> list[str]:
    """Schedules a project's Buffer drafts at their saved times. Returns a line per post."""
    state_path = project_dir / "publish.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    lines = []
    posts = dict(state.get("posts", {}))
    for channel_id, record in posts.items():
        post = client.post(record["post_id"])
        if post is None:
            lines.append(f"{record['service']}: deleted in Buffer, skipped")
            continue
        if post["status"] != "draft":
            lines.append(f"{record['service']}: already {post['status']}")
            continue
        due = post.get("dueAt")
        if not due or datetime.fromisoformat(due.replace("Z", "+00:00")) <= now:
            lines.append(f"{record['service']}: its time has passed, left as a draft")
            continue
        content = _post_content(cfg, project_dir, record["service"], state["video_url"])
        try:
            scheduled = client.schedule_draft(record["post_id"], due, content)
        except BufferError as exc:
            lines.append(f"{record['service']}: NOT scheduled. {exc}")
            continue
        posts[channel_id] = {**record, "draft": False, "due_at": scheduled.get("dueAt", due)}
        lines.append(f"{record['service']}: scheduled for {due}")
    state_path.write_text(json.dumps({**state, "posts": posts}, indent=2), encoding="utf-8")
    return lines


def promote_week(cfg: Config, batch_path: Path) -> None:
    """Turns a week's dated Buffer drafts into scheduled posts."""
    batch = _load(batch_path)
    projects = [batch["long"], *batch["cut_shorts"], *batch["standalone_shorts"]]
    services = list((cfg.publish or {}).get("services", []))
    check_free_limit(cfg, services, len(projects))
    client = BufferClient(cfg.buffer_key or "")
    now = datetime.now(ZoneInfo("UTC"))
    for project in projects:
        print(Path(project).name)
        for line in promote_project(cfg, client, _abs(cfg, project), now):
            print(f"  {line}")


def pending_batch(cfg: Config) -> Path | None:
    """The oldest weekly batch that has not been sent to Buffer yet."""
    for path in sorted((cfg.projects_dir / "weeks").glob("*.json")):
        if not _load(path).get("scheduled_at"):
            return path
    return None


def auto_week(cfg: Config) -> Path | None:
    """Cloud step after review: build and send the pending batch. Returns it, or None."""
    batch_path = pending_batch(cfg)
    if batch_path is None:
        print("No weekly batch waiting to be built.")
        return None
    unchecked = unchecked_projects(cfg, _load(batch_path))
    if unchecked:
        names = ", ".join(p.name for p in unchecked)
        raise PublishError(f"These scripts still contain [CHECK: ...]: {names}. "
                           "Fix them on GitHub and the build will run again.")
    settings = cfg.publish or {}
    today = datetime.now(ZoneInfo(settings.get("timezone", "UTC"))).date()
    batch = build_week(cfg, batch_path)
    start = first_day(date.fromisoformat(batch["week_of"]), today)
    schedule_week(cfg, batch_path, start,
                  time.fromisoformat(settings.get("long_time", "18:00")),
                  time.fromisoformat(settings.get("short_time", "19:00")),
                  draft=not settings.get("auto_schedule", False))
    return batch_path
