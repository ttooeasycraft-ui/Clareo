"""
Core video processing pipeline:
1. Download video with yt-dlp
2. Extract audio
3. Transcribe with Whisper
4. Detect volume peaks
5. Score segments by keywords + volume
6. Cut best clips, convert 9:16, burn subtitles
"""

import os
import re
import json
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional
import yt_dlp
import whisper
import numpy as np

try:
    from pydub import AudioSegment
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False


# ─── Download ────────────────────────────────────────────────────────────────

def download_video(url: str, output_dir: Path) -> tuple[Path, Path]:
    """Download video and return (video_path, audio_path)."""
    video_path = output_dir / "video.mp4"
    audio_path = output_dir / "audio.wav"

    ydl_opts = {
        "outtmpl": str(output_dir / "video.%(ext)s"),
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        ext = info.get("ext", "mp4")
        actual = output_dir / f"video.{ext}"
        if actual != video_path and actual.exists():
            actual.rename(video_path)

    # Extract audio as WAV for analysis
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video_path),
            "-ar", "16000", "-ac", "1", "-vn",
            str(audio_path),
        ],
        check=True,
        capture_output=True,
    )

    return video_path, audio_path


# ─── Transcription ────────────────────────────────────────────────────────────

def transcribe(audio_path: Path, model_name: str = "small") -> list[dict]:
    """
    Run Whisper transcription.
    Returns list of segments: {start, end, text}
    """
    model = whisper.load_model(model_name)
    result = model.transcribe(
        str(audio_path),
        language="pt",
        word_timestamps=True,
        verbose=False,
    )

    segments = []
    for seg in result["segments"]:
        segments.append({
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"].strip(),
            "words": seg.get("words", []),
        })

    return segments


# ─── Volume Analysis ──────────────────────────────────────────────────────────

def compute_volume_envelope(audio_path: Path, frame_duration: float = 0.5) -> list[tuple[float, float]]:
    """
    Returns list of (timestamp, rms_db) pairs using ffmpeg.
    """
    # Use ffmpeg to get audio volume info
    result = subprocess.run(
        [
            "ffmpeg", "-i", str(audio_path),
            "-af", f"astats=metadata=1:reset={int(frame_duration * 1000)},ametadata=print:key=lavfi.astats.Overall.RMS_level",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )

    envelope = []
    time_val = None
    for line in result.stderr.split("\n"):
        if "pts_time" in line:
            m = re.search(r"pts_time:([\d.]+)", line)
            if m:
                time_val = float(m.group(1))
        if "RMS_level" in line and time_val is not None:
            m = re.search(r"RMS_level=([-\d.]+)", line)
            if m:
                rms = float(m.group(1))
                if not math.isinf(rms):
                    envelope.append((time_val, rms))
                    time_val = None

    # Fallback: generate uniform envelope using numpy if ffmpeg parsing fails
    if not envelope:
        if HAS_PYDUB:
            audio = AudioSegment.from_wav(str(audio_path))
            chunk_ms = int(frame_duration * 1000)
            for i, chunk in enumerate(audio[::chunk_ms]):
                t = i * frame_duration
                rms = chunk.rms
                db = 20 * math.log10(max(rms, 1)) - 90  # rough dBFS
                envelope.append((t, db))
        else:
            # Last resort: all zeros
            duration = get_duration(audio_path)
            steps = int(duration / frame_duration)
            envelope = [(i * frame_duration, -30.0) for i in range(steps)]

    return envelope


def get_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


# ─── Moment Detection ─────────────────────────────────────────────────────────

def score_segments(
    transcript_segments: list[dict],
    volume_envelope: list[tuple[float, float]],
    keywords: list[str],
    video_duration: float,
) -> list[dict]:
    """
    Score each transcript segment by:
    - keyword presence (weighted by keyword excitement level)
    - volume peak relative to baseline
    Returns segments sorted by score desc.
    """
    keywords_lower = [k.lower() for k in keywords]

    # Build volume lookup: time → db
    if volume_envelope:
        vol_times = np.array([t for t, _ in volume_envelope])
        vol_vals = np.array([v for _, v in volume_envelope])
        baseline_db = float(np.percentile(vol_vals, 40))
    else:
        vol_times = np.array([0.0])
        vol_vals = np.array([-30.0])
        baseline_db = -30.0

    scored = []
    for seg in transcript_segments:
        text_lower = seg["text"].lower()
        start = seg["start"]
        end = seg["end"]
        duration = end - start
        if duration < 1.0:
            continue

        # Keyword score
        kw_score = 0.0
        matched_keywords = []
        for kw in keywords_lower:
            if kw in text_lower:
                # Profanity / exclamations get higher weight
                weight = 2.0 if any(p in kw for p in ["merda", "porra", "caralho", "caramba"]) else 1.0
                kw_score += weight
                matched_keywords.append(kw)

        # Volume score: avg volume in segment vs baseline
        mask = (vol_times >= start) & (vol_times <= end)
        if mask.sum() > 0:
            seg_vol = float(np.mean(vol_vals[mask]))
            vol_score = max(0.0, seg_vol - baseline_db) / 10.0
        else:
            vol_score = 0.0

        total_score = kw_score + vol_score

        reason = []
        if matched_keywords:
            reason.append(f"palavras-chave: {', '.join(set(matched_keywords))}")
        if vol_score > 0.5:
            reason.append(f"pico de volume")

        scored.append({
            **seg,
            "score": total_score,
            "reason": " + ".join(reason) if reason else "momento relevante",
            "matched_keywords": matched_keywords,
            "vol_score": vol_score,
        })

    return sorted(scored, key=lambda x: x["score"], reverse=True)


def merge_and_extend_segments(
    scored: list[dict],
    video_duration: float,
    min_duration: float,
    max_duration: float,
    max_clips: int,
) -> list[dict]:
    """
    Pick top segments, extend them to min_duration, cap at max_duration,
    and de-overlap.
    """
    selected = []
    used_ranges = []

    for seg in scored:
        if len(selected) >= max_clips:
            break

        start = seg["start"]
        end = seg["end"]

        # Extend to min_duration centered on segment
        seg_center = (start + end) / 2
        half = min_duration / 2
        ext_start = max(0.0, seg_center - half)
        ext_end = min(video_duration, ext_start + min_duration)
        ext_start = max(0.0, ext_end - min_duration)

        # Cap at max_duration
        if ext_end - ext_start > max_duration:
            ext_end = ext_start + max_duration

        # Check overlap
        overlap = False
        for us, ue in used_ranges:
            if not (ext_end <= us or ext_start >= ue):
                overlap = True
                break

        if not overlap:
            selected.append({
                **seg,
                "clip_start": round(ext_start, 2),
                "clip_end": round(ext_end, 2),
            })
            used_ranges.append((ext_start, ext_end))

    return selected


# ─── Subtitle Generation ──────────────────────────────────────────────────────

def build_ass_subtitles(transcript_segments: list[dict], clip_start: float, clip_end: float) -> str:
    """Build ASS subtitle content for a clip window."""

    header = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,72,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,60,60,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []

    def fmt_time(seconds: float) -> str:
        seconds = max(0.0, seconds)
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        cs = int((s % 1) * 100)
        return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"

    for seg in transcript_segments:
        if seg["end"] < clip_start or seg["start"] > clip_end:
            continue
        rel_start = max(0.0, seg["start"] - clip_start)
        rel_end = min(clip_end - clip_start, seg["end"] - clip_start)
        text = seg["text"].strip().upper()
        # Line wrap at ~30 chars
        if len(text) > 30:
            words = text.split()
            lines_wrapped = []
            current = ""
            for w in words:
                if len(current) + len(w) + 1 <= 30:
                    current += (" " if current else "") + w
                else:
                    lines_wrapped.append(current)
                    current = w
            if current:
                lines_wrapped.append(current)
            text = r"\N".join(lines_wrapped)
        lines.append(
            f"Dialogue: 0,{fmt_time(rel_start)},{fmt_time(rel_end)},Default,,0,0,0,,{text}"
        )

    return header + "\n".join(lines)


# ─── FFmpeg Clip + 9:16 + Subtitles ──────────────────────────────────────────

def cut_clip(
    video_path: Path,
    start: float,
    end: float,
    output_path: Path,
    ass_content: str,
) -> None:
    """
    Cut clip, convert to 9:16 (1080x1920), burn ASS subtitles.
    """
    ass_path = output_path.with_suffix(".ass")
    ass_path.write_text(ass_content, encoding="utf-8")

    duration = end - start

    # Escape path for ffmpeg filter
    ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", str(video_path),
        "-t", str(duration),
        # Video filter chain: scale + crop/pad to 9:16, then burn subs
        "-vf",
        (
            "scale=iw*min(1080/iw\\,1920/ih):ih*min(1080/iw\\,1920/ih),"
            "pad=1080:1920:(1080-iw)/2:(1920-ih)/2:black,"
            f"ass={ass_escaped}"
        ),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg error: {result.stderr[-1000:]}")

    # Cleanup ASS
    try:
        ass_path.unlink()
    except Exception:
        pass


# ─── Main Pipeline ────────────────────────────────────────────────────────────

def process_video(
    url: str,
    keywords: list[str],
    min_duration: float,
    max_duration: float,
    max_clips: int,
    job_dir: Path,
    progress_callback: Optional[Callable] = None,
) -> list[dict]:

    def progress(p: int, msg: str):
        if progress_callback:
            progress_callback(p, msg)

    # 1. Download
    progress(10, "Baixando vídeo...")
    video_path, audio_path = download_video(url, job_dir)

    video_duration = get_duration(video_path)
    progress(25, "Vídeo baixado! Transcrevendo áudio...")

    # 2. Transcribe
    transcript = transcribe(audio_path, model_name="small")
    progress(55, "Transcrição concluída! Analisando momentos...")

    # 3. Volume envelope
    envelope = compute_volume_envelope(audio_path)

    # 4. Score segments
    scored = score_segments(transcript, envelope, keywords, video_duration)

    if not scored:
        raise RuntimeError("Nenhum momento relevante detectado no vídeo.")

    # 5. Select and extend clips
    clips_meta = merge_and_extend_segments(
        scored, video_duration, min_duration, max_duration, max_clips
    )

    if not clips_meta:
        # Fallback: use top scored regardless of overlap
        clips_meta = scored[:max_clips]
        for c in clips_meta:
            c["clip_start"] = max(0.0, c["start"] - min_duration / 2)
            c["clip_end"] = min(video_duration, c["clip_start"] + min_duration)

    # 6. Cut each clip
    clips_out = []
    for i, clip in enumerate(clips_meta):
        progress(
            60 + int((i / max(len(clips_meta), 1)) * 35),
            f"Cortando clipe {i + 1}/{len(clips_meta)}...",
        )

        filename = f"clip_{i + 1:02d}.mp4"
        output_path = job_dir / filename

        ass = build_ass_subtitles(transcript, clip["clip_start"], clip["clip_end"])
        cut_clip(video_path, clip["clip_start"], clip["clip_end"], output_path, ass)

        clips_out.append({
            "filename": filename,
            "label": f"Clipe {i + 1}",
            "start": clip["clip_start"],
            "end": clip["clip_end"],
            "reason": clip.get("reason", ""),
            "score": clip.get("score", 0),
        })

    # Cleanup large files to save space
    try:
        audio_path.unlink()
    except Exception:
        pass

    return clips_out
