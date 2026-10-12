"""FFmpeg helpers: probing, scene rendering and concatenation."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv"}
VIDEO_CRF = "23"
AUDIO_BITRATE = "160k"
AUDIO_RATE = "48000"
# Same colour tags on every scene, so scenes join cleanly and colours match on HD players
BT709_FLAGS = ("-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709")


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


SILENCE_DB = "-45dB"        # quieter than this counts as silence at the edges of a line
MIN_EDGE_SILENCE = 0.05      # shorter quiet edges are left alone


def silence_edges(path: Path) -> tuple[float, float]:
    """(leading, trailing) seconds of silence in an audio file."""
    duration = probe_duration(path)
    log = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
         f"silencedetect=noise={SILENCE_DB}:d={MIN_EDGE_SILENCE}", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    starts = [float(m) for m in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", log)]
    lead = ends[0] if starts and starts[0] <= 0.01 and ends else 0.0
    # A trailing silence either has no end (EOF) or ends at the end of the file
    trail = 0.0
    if starts and (len(ends) < len(starts) or ends[-1] >= duration - 0.02):
        trail = max(duration - starts[-1], 0.0)
    if lead >= duration:
        return 0.0, 0.0  # all silence: leave the file alone
    return lead, trail


def trim_audio(path: Path, start: float, length: float) -> None:
    """Keeps [start, start + length) of the file, re-encoded in its own format."""
    codec = ["-c:a", "pcm_s16le"] if path.suffix.lower() == ".wav" else ["-c:a", "libmp3lame", "-q:a", "2"]
    trimmed = path.with_name(f"trimmed{path.suffix}")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}",
         "-t", f"{length:.3f}", "-i", str(path), *codec, str(trimmed)])
    trimmed.replace(path)


def probe_duration(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    try:
        return float(out.strip())
    except ValueError as exc:
        raise MediaError(f"Could not read duration of {path}") from exc


def build_scene_cmd(clips: list[Path], audio: Path, duration: float, size: tuple[int, int],
                    fps: int, subs_name: str, out: Path, white_in: float = 0.0) -> list[str]:
    """One scene: clips share the duration equally, captions burned in, voice padded.

    subs_name must be a plain file name in the working directory, which avoids
    FFmpeg's awkward escaping of Windows paths inside the subtitles filter.
    white_in fades in from white, which smooths the cut from a whiteboard doodle scene.
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
    fade = f"fade=t=in:st=0:d={white_in:.2f}:color=white," if white_in > 0 else ""
    filters.append(f"{labels}concat=n={len(clips)}:v=1:a=0,{fade}"
                   f"scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
                   f"subtitles={subs_name}[vout]")
    filters.append(f"[{len(clips)}:a]apad=whole_dur={duration:.3f},aresample={AUDIO_RATE}[aout]")

    return cmd + [
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", "[aout]", "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", VIDEO_CRF, "-r", str(fps),
        *BT709_FLAGS, "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ac", "2", str(out),
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
