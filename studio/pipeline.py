"""Turns a project's script.json into a finished video, thumbnail and metadata."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

from studio import doodle
from studio.clipcheck import make_checker
from studio.captions import LONG_STYLE, SHORT_STYLE, CaptionStyle, Word, to_ass
from studio.cards import make_card, make_thumbnail
from studio.config import Config
from studio.media import (
    VIDEO_SUFFIXES,
    MediaError,
    build_scene_cmd,
    concat_segments,
    extract_frame,
    run,
)
from studio.metadata import build_chapters, build_metadata
from studio.project import load_script
from studio.script import unchecked_scenes
from studio.visuals import ClipFinder
from studio.voice import audio_filename, synthesize, voice_id

SCENE_PAUSE_SECONDS = 0.35  # breathing room after each scene's narration
WHITE_IN_SECONDS = 0.3      # stock footage fades in from white after a doodle scene
DOODLE_SHORT_CAPTIONS = replace(SHORT_STYLE, margin_v=170)  # under the drawing, not over it


class RenderError(RuntimeError):
    pass


@dataclass(frozen=True)
class SceneAssets:
    audio: Path
    duration: float
    words: tuple[Word, ...]
    clips: tuple[Path, ...]
    kind: str = "stock"          # "stock" (footage or card) or "doodle"
    draw_seconds: float = 0.0    # doodle scenes only


def _load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except json.JSONDecodeError:
        return {}


def _scene_voice(cfg: Config, scene: dict, scene_dir: Path, state: dict) -> dict:
    """Reuses cached audio when narration and voice settings are unchanged."""
    audio = scene_dir / audio_filename(cfg)
    vid = voice_id(cfg)
    if (state.get("narration") == scene["narration"] and state.get("voice") == vid
            and audio.is_file()):
        return state
    result = synthesize(cfg, scene["narration"], audio)
    return {**state, "narration": scene["narration"], "voice": voice_id(cfg),
            "duration": result.duration,
            "words": [[w.text, w.start, w.end] for w in result.words]}


def _try_doodle(cfg: Config, scene: dict, size: tuple[int, int]) -> Path | None:
    try:
        return doodle.doodle_image(cfg, doodle.scene_prompt(scene), size,
                                   cfg.cache_dir / "doodles")
    except doodle.DoodleError as exc:
        print(f"  warning: {exc}")
        return None


def _visual_key(cfg: Config, scene: dict, finder: ClipFinder) -> list:
    """Covers everything that decides a scene's visuals, so changing any of it (card text,
    a new Pexels key, doodle settings) picks new visuals instead of reusing the cache."""
    s = doodle.settings(cfg)
    drawing = [s["mode"], s["image_model"], s["quality"]] if doodle.available(cfg) else ["off"]
    return [scene["visual_query"], scene["on_screen_text"], finder.enabled, finder.strict,
            finder.checker is not None,
            scene.get("visual", "stock"), scene.get("doodle_prompt", ""), *drawing]


def _scene_clips(cfg: Config, scene: dict, scene_dir: Path, state: dict, finder: ClipFinder,
                 duration: float, size: tuple[int, int], clip_seconds: float) -> dict:
    cached = [Path(p) for p in state.get("clips", [])]
    visual_key = _visual_key(cfg, scene, finder)
    if (state.get("visual_key") == visual_key and not state.get("doodle_failed")
            and cached and all(p.is_file() for p in cached)):
        finder.used_ids.update(state.get("clip_ids", []))
        finder.credits.extend(state.get("credits", []))  # cached clips still need credit
        return state
    wanted_doodle = doodle.wants_doodle(cfg, scene)
    image = _try_doodle(cfg, scene, size) if wanted_doodle else None
    clips = [] if image else finder.find(scene["visual_query"],
                                         max(1, math.ceil(duration / clip_seconds)))
    if not image and not clips and doodle.replaces_weak_stock(cfg):
        print("  no matching stock footage, drawing a doodle instead")
        image = _try_doodle(cfg, scene, size)
    if image:
        return {**state, "visual_key": visual_key, "kind": "doodle", "doodle_failed": False,
                "clips": [str(image)], "clip_ids": []}
    if not clips:
        card_text = scene["on_screen_text"] or scene["narration"][:60]
        clips = [make_card(card_text, size, cfg.brand, scene_dir / "card.png")]
    ids = [int(p.stem.split("_")[0]) for p in clips if p.suffix in VIDEO_SUFFIXES]
    credits = [finder.credit_for[str(p)] for p in clips if str(p) in finder.credit_for]
    return {**state, "visual_key": visual_key, "kind": "stock", "doodle_failed": wanted_doodle,
            "clips": [str(p) for p in clips], "clip_ids": ids, "credits": credits}


def _prepare_scene(cfg: Config, scene: dict, scene_dir: Path, finder: ClipFinder,
                   size: tuple[int, int], clip_seconds: float) -> SceneAssets:
    scene_dir.mkdir(parents=True, exist_ok=True)
    state_path = scene_dir / "state.json"
    state = _scene_voice(cfg, scene, scene_dir, _load_state(state_path))
    duration = state["duration"] + SCENE_PAUSE_SECONDS
    state = _scene_clips(cfg, scene, scene_dir, state, finder, duration, size, clip_seconds)
    words = tuple(Word(t, s, e) for t, s, e in state["words"])
    clips = tuple(Path(p).resolve() for p in state["clips"])
    audio = scene_dir / audio_filename(cfg)
    if state.get("kind") == "doodle":
        timing = doodle.timing(cfg, duration)
        assets = SceneAssets(audio, timing.total, words, clips, "doodle", timing.draw)
    else:
        assets = SceneAssets(audio, duration, words, clips)
    state = {**state, "scene_seconds": assets.duration}
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return assets


def _caption_style(fmt: str, kind: str) -> CaptionStyle:
    if fmt != "short":
        return LONG_STYLE
    return DOODLE_SHORT_CAPTIONS if kind == "doodle" else SHORT_STYLE


def _render_scene(cfg: Config, assets: SceneAssets, scene_dir: Path, size: tuple[int, int],
                  fmt: str, after_doodle: bool = False) -> Path:
    style = _caption_style(fmt, assets.kind)
    ass = to_ass(assets.words, size[0], size[1], cfg.brand["caption_font"], style)
    (scene_dir / "subs.ass").write_text(ass, encoding="utf-8")
    out = scene_dir / "scene.mp4"
    fps = int(cfg.video["fps"])
    if assets.kind == "doodle":
        timing = doodle.Timing(assets.duration, assets.draw_seconds)
        try:
            return doodle.render_scene(assets.clips[0], assets.audio, timing, size, fps,
                                       "subs.ass", out, cwd=scene_dir)
        except (MediaError, OSError, doodle.DoodleError) as exc:
            print(f"  warning: doodle animation failed, showing the still drawing: {exc}")
    white_in = WHITE_IN_SECONDS if after_doodle else 0.0
    cmd = build_scene_cmd(list(assets.clips), assets.audio, assets.duration, size,
                          fps, "subs.ass", out, white_in=white_in)
    run(cmd, cwd=scene_dir)
    return out


def _video_settings(cfg: Config, fmt: str) -> tuple[tuple[int, int], float]:
    key = "short" if fmt == "short" else "long"
    size = tuple(int(v) for v in cfg.video[f"{key}_size"])
    return size, float(cfg.video[f"{key}_clip_seconds"])


def _finish(cfg: Config, project_dir: Path, script: dict, assets: list[SceneAssets],
            credits: list[str]) -> None:
    first_video = next((c for a in assets for c in a.clips if c.suffix in VIDEO_SUFFIXES), None)
    background = None
    if first_video is not None:
        background = project_dir / "build" / "thumb_bg.jpg"
        extract_frame(first_video, background)
    make_thumbnail(script["thumbnail_text"], cfg.channel["name"], cfg.brand,
                   project_dir / "thumbnail.jpg", background)

    sections = [(s["section"], a.duration) for s, a in zip(script["scenes"], assets)]
    chapters = build_chapters(sections) if script["format"] == "long" else []
    cta = cfg.channel["cta"] if script["format"] == "long" else cfg.channel["follow_line"]
    (project_dir / "upload.txt").write_text(
        build_metadata(script, cta, chapters, credits), encoding="utf-8")


def _warn_if_short(cfg: Config, script: dict, assets: list[SceneAssets]) -> None:
    """Long videos under long_minutes lose YouTube mid-roll ads; say so loudly."""
    if script["format"] != "long":
        return
    seconds = sum(a.duration for a in assets)
    target = float(cfg.video["long_minutes"]) * 60
    if seconds < target:
        print(f"  WARNING: long video is {seconds / 60:.1f} min, under "
              f"{cfg.video['long_minutes']} min (no mid-roll ads). Add scenes to script.json.")


def _prepare_all(cfg: Config, script: dict, scene_dirs: list[Path], finder: ClipFinder,
                 size: tuple[int, int], clip_seconds: float) -> list[SceneAssets]:
    """Voice and visuals for every scene. If the voice changes part-way (edge-tts blocked,
    OpenAI takes over), a second pass re-voices the earlier scenes so one video never mixes
    two voices; everything else comes from the cache."""
    started_with = voice_id(cfg)
    total = len(scene_dirs)
    assets = []
    for i, (scene, scene_dir) in enumerate(zip(script["scenes"], scene_dirs), start=1):
        print(f"  scene {i}/{total}: {scene['section']}")
        assets.append(_prepare_scene(cfg, scene, scene_dir, finder, size, clip_seconds))
    if voice_id(cfg) == started_with:
        return assets
    print("  voice changed during this run, re-voicing earlier scenes to match")
    finder.used_ids.clear()
    finder.credits.clear()
    return [_prepare_scene(cfg, scene, scene_dir, finder, size, clip_seconds)
            for scene, scene_dir in zip(script["scenes"], scene_dirs)]


def render_project(cfg: Config, project_dir: Path, force: bool = False) -> Path:
    # Absolute paths matter: each scene is rendered with its own folder as the working dir.
    project_dir = project_dir.resolve()
    script = load_script(project_dir)
    pending = unchecked_scenes(script)
    if pending and not force:
        raise RenderError(
            f"Scenes {pending} still contain [CHECK: ...]. Verify those facts, edit script.json "
            "to remove the markers, then render again (or pass --force)."
        )
    size, clip_seconds = _video_settings(cfg, script["format"])
    finder = ClipFinder(cfg.pexels_key, cfg.cache_dir / "pexels", size,
                        strict=doodle.replaces_weak_stock(cfg), checker=make_checker(cfg))
    if not finder.enabled:
        print("  note: no PEXELS_API_KEY, using branded text cards instead of stock footage")

    scene_dirs = [project_dir / "build" / f"scene_{i:02d}"
                  for i in range(1, len(script["scenes"]) + 1)]
    assets = _prepare_all(cfg, script, scene_dirs, finder, size, clip_seconds)
    segments: list[Path] = []
    for i, (scene_assets, scene_dir) in enumerate(zip(assets, scene_dirs)):
        print(f"  rendering scene {i + 1}/{len(assets)} ({scene_assets.kind})")
        after_doodle = i > 0 and assets[i - 1].kind == "doodle"
        segments.append(_render_scene(cfg, scene_assets, scene_dir, size, script["format"],
                                      after_doodle))

    _warn_if_short(cfg, script, assets)
    final = project_dir / "video.mp4"
    concat_segments(segments, project_dir / "build", final)
    _finish(cfg, project_dir, script, assets, finder.credits)
    return final
