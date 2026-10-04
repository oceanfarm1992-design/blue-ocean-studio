"""Turns a project's script.json into a finished video, thumbnail and metadata."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from studio.captions import LONG_STYLE, SHORT_STYLE, Word, to_ass
from studio.cards import make_card, make_thumbnail
from studio.config import Config
from studio.media import VIDEO_SUFFIXES, build_scene_cmd, concat_segments, extract_frame, run
from studio.metadata import build_chapters, build_metadata
from studio.project import load_script
from studio.script import unchecked_scenes
from studio.visuals import ClipFinder
from studio.voice import audio_filename, synthesize, voice_id

SCENE_PAUSE_SECONDS = 0.35  # breathing room after each scene's narration


class RenderError(RuntimeError):
    pass


@dataclass(frozen=True)
class SceneAssets:
    audio: Path
    duration: float
    words: tuple[Word, ...]
    clips: tuple[Path, ...]


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
    return {**state, "narration": scene["narration"], "voice": vid,
            "duration": result.duration,
            "words": [[w.text, w.start, w.end] for w in result.words]}


def _scene_clips(cfg: Config, scene: dict, scene_dir: Path, state: dict, finder: ClipFinder,
                 duration: float, size: tuple[int, int], clip_seconds: float) -> dict:
    cached = [Path(p) for p in state.get("clips", [])]
    # Cache key covers the card text too, and whether stock footage was available,
    # so adding a Pexels key later replaces old text cards with real footage.
    visual_key = [scene["visual_query"], scene["on_screen_text"], finder.enabled]
    if state.get("visual_key") == visual_key and cached and all(p.is_file() for p in cached):
        finder.used_ids.update(state.get("clip_ids", []))
        return state
    wanted = max(1, math.ceil(duration / clip_seconds))
    clips = finder.find(scene["visual_query"], wanted)
    if not clips:
        card_text = scene["on_screen_text"] or scene["narration"][:60]
        clips = [make_card(card_text, size, cfg.brand, scene_dir / "card.png")]
    ids = [int(p.stem.split("_")[0]) for p in clips if p.suffix in VIDEO_SUFFIXES]
    return {**state, "visual_key": visual_key,
            "clips": [str(p) for p in clips], "clip_ids": ids}


def _prepare_scene(cfg: Config, scene: dict, scene_dir: Path, finder: ClipFinder,
                   size: tuple[int, int], clip_seconds: float) -> SceneAssets:
    scene_dir.mkdir(parents=True, exist_ok=True)
    state_path = scene_dir / "state.json"
    state = _scene_voice(cfg, scene, scene_dir, _load_state(state_path))
    duration = state["duration"] + SCENE_PAUSE_SECONDS
    state = _scene_clips(cfg, scene, scene_dir, state, finder, duration, size, clip_seconds)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    words = tuple(Word(t, s, e) for t, s, e in state["words"])
    return SceneAssets(scene_dir / audio_filename(cfg), duration, words,
                       tuple(Path(p).resolve() for p in state["clips"]))


def _render_scene(cfg: Config, assets: SceneAssets, scene_dir: Path, size: tuple[int, int],
                  fmt: str) -> Path:
    style = SHORT_STYLE if fmt == "short" else LONG_STYLE
    ass = to_ass(assets.words, size[0], size[1], cfg.brand["caption_font"], style)
    (scene_dir / "subs.ass").write_text(ass, encoding="utf-8")
    out = scene_dir / "scene.mp4"
    cmd = build_scene_cmd(list(assets.clips), assets.audio, assets.duration, size,
                          int(cfg.video["fps"]), "subs.ass", out)
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
    finder = ClipFinder(cfg.pexels_key, cfg.cache_dir / "pexels", size)
    if not finder.enabled:
        print("  note: no PEXELS_API_KEY, using branded text cards instead of stock footage")

    assets: list[SceneAssets] = []
    segments: list[Path] = []
    total = len(script["scenes"])
    for i, scene in enumerate(script["scenes"], start=1):
        print(f"  scene {i}/{total}: {scene['section']}")
        scene_dir = project_dir / "build" / f"scene_{i:02d}"
        scene_assets = _prepare_scene(cfg, scene, scene_dir, finder, size, clip_seconds)
        segments.append(_render_scene(cfg, scene_assets, scene_dir, size, script["format"]))
        assets.append(scene_assets)

    final = project_dir / "video.mp4"
    concat_segments(segments, project_dir / "build", final)
    _finish(cfg, project_dir, script, assets, finder.credits)
    return final
