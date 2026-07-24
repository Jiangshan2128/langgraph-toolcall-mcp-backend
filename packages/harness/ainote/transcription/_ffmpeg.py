"""ffmpeg-based audio chunking for oversized uploads.

Groq's docs (console.groq.com/docs/speech-to-text) recommend breaking long
audio into *overlapping* segments and merging the results when a recording
exceeds the 25 MB request cap. This module splits a file into overlapping
chunks, each re-encoded to 16 kHz mono Opus (Groq's recommended preprocessing
— Whisper samples internally at 16 kHz mono, so higher fidelity is wasted bytes).

Requires `ffmpeg` (and `ffprobe`) on PATH. The Android client pre-compresses
before upload, so this path is only reached for very long recordings.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# Overlap between consecutive segments (seconds). Keeps words cut at a segment
# boundary captured by at least one chunk. Small enough not to bloat the total.
SEGMENT_OVERLAP_SECONDS = 1.0


def _ffprobe_duration(path: Path) -> float:
    """Return media duration in seconds via ffprobe (or 0.0 if unavailable)."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, check=True,
        )
        return float(out.stdout.strip() or 0.0)
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return 0.0


def split_audio_into_overlapping_chunks(src: Path, out_dir: Path, max_bytes: int) -> list[Path]:
    """Split `src` into overlapping < max_bytes Opus segments.

    Computes a segment duration from the source's average bitrate (80% headroom
    for Opus container overhead), then extracts overlapping windows with
    `-ss`/`-t`, re-encoding each to 16 kHz mono Opus @ 32 kbps.
    """
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise FileNotFoundError("ffmpeg/ffprobe not found on PATH")

    total_bytes = src.stat().st_size
    if total_bytes <= max_bytes:
        return [src]

    duration = _ffprobe_duration(src)
    if duration <= 0:
        # Cannot estimate bitrate; let the caller raise a clear error.
        raise RuntimeError("Could not determine audio duration; cannot chunk safely.")

    bytes_per_sec = total_bytes / duration
    # Segment length that stays comfortably under the cap after Opus re-encode.
    # Opus @ 32 kbps mono 16 kHz ≈ 4 KB/s, so segments will be far smaller than
    # the source; size against the *source* bitrate to be safe.
    seg_seconds = max(1.0, (max_bytes * 0.8) / bytes_per_sec)

    chunks: list[Path] = []
    idx = 0
    start = 0.0
    while start < duration:
        out_file = out_dir / f"chunk_{idx:03d}.ogg"
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start:.3f}",
            "-i", str(src),
            "-t", f"{seg_seconds:.3f}",
            "-vn",                       # drop any video stream
            "-ar", "16000", "-ac", "1",  # 16 kHz mono (Whisper's native rate)
            "-c:a", "libopus", "-b:a", "32k",
            str(out_file),
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        if out_file.exists() and out_file.stat().st_size > 0:
            chunks.append(out_file)
        idx += 1
        # Advance by (seg_seconds - overlap) so each window overlaps the next.
        start += max(1.0, seg_seconds - SEGMENT_OVERLAP_SECONDS)

    return chunks
