"""Command line entry point: python -m studio <command>."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, time
from pathlib import Path

from studio.buffer import BufferClient, BufferError
from studio.check import run_checks
from studio.cleanup import cleanup
from studio.week import (
    auto_week,
    build_week,
    plan_week,
    promote_week,
    schedule_week,
    unchecked_projects,
)
from studio.config import load_config
from studio.hosting import HostingError
from studio.publish import PublishError, publish_project
from studio.youtube import YouTubeError
from studio.youtube import login as youtube_login
from studio.ideas import generate_ideas, ideas_to_markdown
from studio.llm import LLMError
from studio.media import MediaError
from studio.pipeline import RenderError, render_project
from studio.project import load_script, new_project_dir, save_script
from studio.shorts import DEFAULT_MAX_SHORTS, plan_shorts, scene_durations
from studio.script import (
    FORMATS,
    WORDS_PER_MINUTE,
    ScriptError,
    generate_script,
    unchecked_scenes,
    word_count,
)
from studio.visuals import VisualsError
from studio.voice import VoiceError

KNOWN_ERRORS = (LLMError, ScriptError, RenderError, MediaError, VoiceError, VisualsError,
                BufferError, HostingError, PublishError, YouTubeError, FileNotFoundError,
                json.JSONDecodeError)  # e.g. a script.json broken while editing on GitHub


def _cmd_ideas(cfg, args) -> int:
    print(f"Generating {args.count} ideas...")
    ideas = generate_ideas(cfg, args.count, args.focus)
    cfg.projects_dir.mkdir(parents=True, exist_ok=True)
    (cfg.projects_dir / "ideas.json").write_text(json.dumps(ideas, indent=2), encoding="utf-8")
    (cfg.projects_dir / "ideas.md").write_text(ideas_to_markdown(ideas), encoding="utf-8")
    for i, idea in enumerate(ideas, start=1):
        print(f"{i:2d}. [{idea['format']}] {idea['title']}")
    print(f"\nSaved to {cfg.projects_dir / 'ideas.md'}")
    return 0


def _write_script(cfg, topic: str, fmt: str) -> Path:
    print(f"Writing {fmt} script: {topic}")
    script = generate_script(cfg, topic, fmt)
    project_dir = new_project_dir(cfg.projects_dir, script["title"], fmt)
    save_script(project_dir, script)
    print(f"Script saved: {project_dir / 'script.md'}")
    minutes = word_count(script) / WORDS_PER_MINUTE
    if fmt == "long" and minutes < cfg.video["long_minutes"]:
        print(f"Warning: script is about {minutes:.1f} min, under the "
              f"{cfg.video['long_minutes']} min target. Add a step or example before rendering.")
    pending = unchecked_scenes(script)
    if pending:
        print(f"Fact-check needed in scenes {pending} (look for [CHECK: ...] in script.json).")
    return project_dir


def _cmd_script(cfg, args) -> int:
    project_dir = _write_script(cfg, args.topic, args.format)
    print(f"\nReview and edit script.json, then run:\n  python -m studio render \"{project_dir}\"")
    return 0


def _cmd_render(cfg, args) -> int:
    print(f"Rendering {args.project}")
    final = render_project(cfg, Path(args.project), force=args.force)
    print(f"\nDone: {final}\nThumbnail and upload text are in the same folder.")
    return 0


def _cmd_make(cfg, args) -> int:
    project_dir = _write_script(cfg, args.topic, args.format)
    final = render_project(cfg, project_dir, force=args.force)
    print(f"\nDone: {final}")
    return 0


def _cmd_shorts(cfg, args) -> int:
    long_dir = Path(args.project).resolve()
    script = load_script(long_dir)
    if script["format"] != "long":
        print("That project is already a short. Pass a long-video project folder.")
        return 1
    shorts = plan_shorts(script, scene_durations(long_dir, script),
                         cfg.channel["follow_line"], args.max)
    if not shorts:
        print("No step sections between 15 and 75 seconds were found to turn into Shorts.")
        return 1
    print(f"Making {len(shorts)} Shorts from {long_dir.name}")
    for i, short in enumerate(shorts, start=1):
        project_dir = long_dir / "shorts" / f"short_{i:02d}"
        save_script(project_dir, short)
        print(f"\n[{i}/{len(shorts)}] {short['title']}")
        if not args.scripts_only:
            render_project(cfg, project_dir, force=args.force)
    print(f"\nDone. Shorts are in {long_dir / 'shorts'}")
    return 0


def _cmd_check(cfg, args) -> int:
    results = run_checks(cfg)
    for name, ok, detail in results:
        print(f"{'OK  ' if ok else 'FAIL'} {name:<11} {detail}")
    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{'All services OK.' if not failed else 'Failed: ' + ', '.join(failed)}")
    return 1 if failed else 0


def _cmd_youtube_login(cfg, args) -> int:
    print("A browser window will open. Sign in with the Google account that owns the "
          "Blue Ocean Marketing YouTube channel and allow upload access.")
    env_file = youtube_login(cfg.root)
    print("\nYouTube login saved on this PC.")
    print("To let GitHub upload too, run this once in PowerShell:")
    print(f"  gh secret set -f {env_file.name} --repo oceanfarm1992-design/blue-ocean-studio")
    return 0


def _cmd_channels(cfg, args) -> int:
    client = BufferClient(cfg.buffer_key or "")
    for channel in client.all_channels():
        status = "DISCONNECTED" if channel.get("isDisconnected") else "ok"
        print(f"{channel['service']:<10} {channel['name']:<35} {status}")
    return 0


def _cmd_publish(cfg, args) -> int:
    services = args.services.split(",") if args.services else list(cfg.publish.get("services", []))
    draft = not (args.queue or args.now or args.at)
    when = "now" if args.now else (args.at or "queue")
    for project in args.projects:
        print(f"\nPublishing {project}")
        results = publish_project(cfg, Path(project), services, when, draft,
                                  dry_run=args.dry_run, again=args.again)
        for r in results:
            if "url" in r:
                when_text = f"publishes {r['due_at']}" if r["due_at"] else (
                    "private" if r["draft"] else "public now")
                print(f"  youtube: uploaded, {when_text} · {r['url']}")
                continue
            where = "saved as a draft in Buffer" if r["draft"] else f"scheduled for {r['due_at']}"
            print(f"  {r['service']}: {where}")
    if draft and not args.dry_run:
        print("\nDrafts are waiting in Buffer. Review and approve them there.")
    return 0


def _cmd_cleanup(cfg, args) -> int:
    print("Checking published videos...")
    summary = cleanup(cfg, dry_run=args.dry_run)
    print(f"\nDeleted: {summary['deleted']} · still waiting to post: {summary['waiting']} · "
          f"failed in Buffer: {summary['failed_posts']}")
    return 0


def _cmd_week_plan(cfg, args) -> int:
    print("Planning the week (1 long video + 2 standalone Shorts)...")
    batch_path = plan_week(cfg, args.focus)
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    print("\nReview these scripts (add your own example, clear every [CHECK: ...]):")
    for path in [batch["long"], *batch["standalone_shorts"]]:
        print(f"  {Path(path) / 'script.json'}")
    print(f"\nThen run:\n  python -m studio week build \"{batch_path}\"")
    return 0


def _cmd_week_build(cfg, args) -> int:
    batch = json.loads(Path(args.batch).read_text(encoding="utf-8"))
    pending = unchecked_projects(cfg, batch)
    if pending and not args.force:
        print("These scripts still have [CHECK: ...] markers. Verify and remove them first:")
        for path in pending:
            print(f"  {path / 'script.json'}")
        return 1
    batch = build_week(cfg, Path(args.batch), force=args.force)
    total = len(batch["cut_shorts"]) + len(batch["standalone_shorts"])
    print(f"\nBuilt 1 long video and {total} Shorts.")
    print(f"Next:\n  python -m studio week schedule \"{args.batch}\"")
    return 0


def _parse_clock(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise PublishError(f"Can't read the time '{value}'. Use HH:MM, e.g. 18:00") from exc


def _cmd_week_auto(cfg, args) -> int:
    batch_path = auto_week(cfg)
    if batch_path is not None:
        print(f"\nWeek sent to Buffer: {batch_path.name}")
    return 0


def _cmd_week_promote(cfg, args) -> int:
    promote_week(cfg, Path(args.batch))
    print("\nDone. Scheduled posts will go out on their own at the times shown.")
    return 0


def _cmd_week_schedule(cfg, args) -> int:
    start = date.fromisoformat(args.start) if args.start else None
    settings = cfg.publish or {}
    long_time = args.long_time or settings.get("long_time", "18:00")
    short_time = args.short_time or settings.get("short_time", "19:00")
    schedule_week(cfg, Path(args.batch), start, _parse_clock(long_time),
                  _parse_clock(short_time), draft=not args.schedule, dry_run=args.dry_run)
    if args.dry_run:
        print("\nPreview only. Nothing was uploaded or posted.")
    elif args.schedule:
        print("\nScheduled in Buffer.")
    else:
        print("\nSaved as dated drafts in Buffer. Approve them there, or rerun with --schedule.")
    return 0


def _add_week_parser(sub) -> None:
    week = sub.add_parser("week", help="weekly batch: 1 long video + up to 7 Shorts (free plan)")
    week_sub = week.add_subparsers(dest="week_command", required=True)

    plan = week_sub.add_parser("plan", help="write this week's scripts for review")
    plan.add_argument("--focus", default="", help="e.g. 'restaurants' or 'Google reviews'")
    plan.set_defaults(func=_cmd_week_plan)

    build = week_sub.add_parser("build", help="render the long video and all Shorts")
    build.add_argument("batch", help="the batch file printed by 'week plan'")
    build.add_argument("--force", action="store_true", help="render even with [CHECK] markers")
    build.set_defaults(func=_cmd_week_build)

    schedule = week_sub.add_parser("schedule", help="send the week to Buffer, one Short a day")
    schedule.add_argument("batch")
    schedule.add_argument("--start", help="first day, YYYY-MM-DD (default: the planned Monday)")
    schedule.add_argument("--long-time", help="time for the long video (default channel.toml)")
    schedule.add_argument("--short-time", help="time for each Short (default channel.toml)")
    schedule.add_argument("--schedule", action="store_true",
                          help="really schedule (default saves dated drafts)")
    schedule.add_argument("--dry-run", action="store_true",
                          help="preview what would be posted; uploads and posts nothing")
    schedule.set_defaults(func=_cmd_week_schedule)

    auto = week_sub.add_parser("auto", help="build and send the pending batch (used by GitHub)")
    auto.set_defaults(func=_cmd_week_auto)

    promote = week_sub.add_parser("promote", help="turn a week's Buffer drafts into scheduled posts")
    promote.add_argument("batch")
    promote.set_defaults(func=_cmd_week_promote)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="studio", description="Blue Ocean Marketing video studio")
    sub = parser.add_subparsers(dest="command", required=True)

    ideas = sub.add_parser("ideas", help="generate a backlog of video ideas")
    ideas.add_argument("--count", type=int, default=20)
    ideas.add_argument("--focus", default="", help="e.g. 'restaurants' or 'Google reviews'")
    ideas.set_defaults(func=_cmd_ideas)

    for name, func, help_text in (
        ("script", _cmd_script, "write a script for review"),
        ("make", _cmd_make, "write a script and render it in one go"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("topic")
        p.add_argument("--format", choices=FORMATS, default="long")
        if name == "make":
            p.add_argument("--force", action="store_true", help="render even with [CHECK] markers")
        p.set_defaults(func=func)

    render = sub.add_parser("render", help="render a reviewed project folder")
    render.add_argument("project")
    render.add_argument("--force", action="store_true", help="render even with [CHECK] markers")
    render.set_defaults(func=_cmd_render)

    shorts = sub.add_parser("shorts", help="cut a long video's steps into vertical Shorts")
    shorts.add_argument("project", help="long-video project folder")
    shorts.add_argument("--max", type=int, default=DEFAULT_MAX_SHORTS)
    shorts.add_argument("--scripts-only", action="store_true",
                        help="write the Short scripts without rendering")
    shorts.add_argument("--force", action="store_true", help="render even with [CHECK] markers")
    shorts.set_defaults(func=_cmd_shorts)

    channels = sub.add_parser("channels", help="list the social channels connected to Buffer")
    channels.set_defaults(func=_cmd_channels)

    check = sub.add_parser("check", help="test every service connection (read-only)")
    check.set_defaults(func=_cmd_check)

    yt_login = sub.add_parser("youtube-login", help="one-time Google sign-in for YouTube uploads")
    yt_login.set_defaults(func=_cmd_youtube_login)

    publish = sub.add_parser("publish", help="send rendered videos to Buffer (drafts by default)")
    publish.add_argument("projects", nargs="+", help="one or more project folders")
    publish.add_argument("--services", default="",
                         help="comma list, e.g. youtube,facebook (default from channel.toml)")
    timing = publish.add_mutually_exclusive_group()
    timing.add_argument("--queue", action="store_true", help="add to Buffer's posting schedule")
    timing.add_argument("--now", action="store_true", help="post immediately")
    timing.add_argument("--at", help="post at a local date/time, e.g. 2026-10-06T15:00")
    publish.add_argument("--dry-run", action="store_true", help="show what would be posted")
    publish.add_argument("--again", action="store_true", help="repost to channels already used")
    publish.set_defaults(func=_cmd_publish)

    clean = sub.add_parser("cleanup", help="delete hosted videos Buffer has finished posting")
    clean.add_argument("--dry-run", action="store_true", help="show what would be deleted")
    clean.set_defaults(func=_cmd_cleanup)

    _add_week_parser(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    # The Windows console defaults to cp1252, which cannot print some characters models use.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.func(load_config(), args)
    except KNOWN_ERRORS as exc:
        print(f"\nError: {exc}")
        return 1
