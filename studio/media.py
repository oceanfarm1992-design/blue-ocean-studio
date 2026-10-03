"""FFmpeg helpers: probing, scene rendering and concatenation."""
from __future__ import annotations

import subprocess
from pathlib import Path

VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv"}
VIDEO_CRF = "23"
AUDIO_BITRATE = "160k"
AUDIO_RATE = "48000"


class MediaError(RuntimeError):
    pass


def run(cmd: list[str], cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise MediaError(f"{cmd[0]} is not installed or not on PATH.") from exc
    if result.returncode != 0:
        raise MediaError(f"{cmd[0]} failed:\n{result.stderr.strip()[-2000:]}")
    return result.stdout


def probe_duration(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    try:
        return float(out.strip())
    except ValueError as exc:
        raise MediaError(f"Could not read duration of {path}") from exc


def build_scene_cmd(clips: list[Path], audio: Path, duration: float, size: tuple[int, int],
                    fps: int, subs_name: str, out: Path) -> list[str]:
    """One scene: clips share the duration equally, captions burned in, voice padded.

    subs_name must be a plain file name in the working directory, which avoids
    FFmpeg's awkward escaping of Windows paths inside the subtitles filter.
    """
    if not clips:
        raise MediaError("A scene needs at least one clip or image.")
    width, height = size
    slot = duration / len(clips)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for clip in clips:
        if clip.suffix.lower() in VIDEO_SUFFIXES:
            cmd += ["-stream_loop", "-1", "-i", str(clip)]
        else:
            cmd += ["-loop", "1", "-framerate", str(fps), "-i", str(clip)]
    cmd += ["-i", str(audio)]

    filters = [
        f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps={fps},trim=duration={slot:.3f},"
        f"setpts=PTS-STARTPTS[v{i}]"
        for i in range(len(clips))
    ]
    labels = "".join(f"[v{i}]" for i in range(len(clips)))
    filters.append(f"{labels}concat=n={len(clips)}:v=1:a=0,format=yuv420p,"
                   f"subtitles={subs_name}[vout]")
    filters.append(f"[{len(clips)}:a]apad=whole_dur={duration:.3f},aresample={AUDIO_RATE}[aout]")

    return cmd + [
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", "[aout]", "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", VIDEO_CRF, "-r", str(fps),
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ac", "2", str(out),
    ]


def concat_segments(segments: list[Path], work_dir: Path, out: Path) -> None:
    list_file = work_dir / "segments.txt"
    lines = [f"file '{seg.resolve().as_posix()}'" for seg in segments]
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(list_file), "-c", "copy", "-movflags", "+faststart", str(out)])


def extract_frame(video: Path, out: Path, at_seconds: float = 1.0) -> None:
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{at_seconds}",
         "-i", str(video), "-frames:v", "1", str(out)])
