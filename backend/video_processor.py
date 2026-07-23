"""
Clareo – Core video processing pipeline
1. Download video with yt-dlp
2. Extract audio (WAV 16kHz mono)
3. Transcribe with Whisper (word-level timestamps)
4. Detect volume peaks
5. Score segments by keywords + volume
6. Cut best clips, convert to 9:16, burn karaoke-style ASS subtitles
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
from faster_whisper import WhisperModel
import numpy as np

try:
    from pydub import AudioSegment
    HAS_PYDUB = True
except ImportError:
    HAS_PYDUB = False


# ─── Download ────────────────────────────────────────────────────────────────

def download_video(url: str, output_dir: Path) -> tuple[Path, Path]:
    """Download video and return (video_path, audio_path).

    Format selector is deliberately permissive so YouTube Lives (webm/opus)
    and regular videos (mp4/m4a) both work.  ffmpeg re-muxes everything to mp4.

    Bot-detection bypass strategy (item 1):
    - extractor_args android+web: avoids sign-in prompts on public videos
    - YTDLP_COOKIES env var: paste your cookies.txt content in Railway Variables;
      the file is written to a temp path and cleaned up after download.
    """
    video_path = output_dir / "video.mp4"
    audio_path = output_dir / "audio.wav"

    ydl_opts = {
        "outtmpl": str(output_dir / "video.%(ext)s"),
        # Try best ≤1080p with any codec; fall back to absolute best.
        # Never restrict by ext= so Lives (webm) and normal videos both match.
        "format": (
            "bestvideo[height<=1080]+bestaudio"
            "/bestvideo[height<=1080]"
            "/best[height<=1080]"
            "/best"
        ),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        # iOS and tv_embedded clients bypass bot-detection without cookies.
        # Order matters: yt-dlp tries each in sequence, stops at first success.
        "extractor_args": {"youtube": {"player_client": ["ios", "tv_embedded", "android", "web"]}},
    }

    # Optional cookies: set YTDLP_COOKIES env var in Railway to the full
    # contents of a Netscape-format cookies.txt exported from your browser.
    cookies_content = os.environ.get("YTDLP_COOKIES", "").strip()
    cookies_fd: Optional[int] = None
    cookies_tmp_path: Optional[str] = None
    if cookies_content:
        # mkstemp is atomic — no TOCTOU race unlike mktemp()
        cookies_fd, cookies_tmp_path = tempfile.mkstemp(suffix=".txt", prefix="yt_cookies_")
        try:
            os.write(cookies_fd, cookies_content.encode())
        finally:
            os.close(cookies_fd)
        os.chmod(cookies_tmp_path, 0o600)  # restrict to owner only
        ydl_opts["cookiefile"] = cookies_tmp_path

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            ext = info.get("ext", "mp4")
            actual = output_dir / f"video.{ext}"
            if actual != video_path and actual.exists():
                actual.rename(video_path)
    finally:
        if cookies_tmp_path:
            try:
                os.unlink(cookies_tmp_path)
            except Exception:
                pass

    # If still not .mp4 (edge case), re-mux with ffmpeg
    if not video_path.exists():
        candidates = list(output_dir.glob("video.*"))
        if candidates:
            src = candidates[0]
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(src), "-c", "copy", str(video_path)],
                check=True, capture_output=True, timeout=300,
            )
            src.unlink(missing_ok=True)

    # Extract audio as 16kHz mono WAV for Whisper.
    # Timeout 600s: a 2 h video on Railway's constrained CPU can take several minutes.
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video_path),
            "-ar", "16000", "-ac", "1", "-vn",
            str(audio_path),
        ],
        check=True,
        capture_output=True,
        timeout=600,
    )

    return video_path, audio_path


# ─── Transcription ────────────────────────────────────────────────────────────

def transcribe(audio_path: Path, model_name: str = "small") -> list[dict]:
    """
    Run faster-whisper with word-level timestamps.
    Returns list of segments: {start, end, text, words: [{word, start, end}]}

    faster-whisper uses CTranslate2 (not PyTorch) — ~400 MB RAM vs ~2 GB for
    openai-whisper, installs cleanly, and is 4x faster.
    """
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments_gen, _info = model.transcribe(
        str(audio_path),
        language="pt",
        word_timestamps=True,
    )

    segments = []
    for seg in segments_gen:
        words = []
        for w in (seg.words or []):
            words.append({
                "word": w.word.strip(),
                "start": w.start,
                "end": w.end,
            })
        segments.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
            "words": words,
        })

    return segments


# ─── Volume Analysis ──────────────────────────────────────────────────────────

def compute_volume_envelope(audio_path: Path, frame_duration: float = 0.5) -> list[tuple[float, float]]:
    """
    Returns list of (timestamp, rms_db) pairs via ffmpeg volumedetect-like approach.
    Falls back to pydub or uniform if parsing fails.
    """
    result = subprocess.run(
        [
            "ffmpeg", "-i", str(audio_path),
            "-af",
            f"astats=metadata=1:reset={int(frame_duration * 1000)},"
            "ametadata=print:key=lavfi.astats.Overall.RMS_level",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        timeout=600,  # 2 h video on slow CPU can take a while
    )

    envelope: list[tuple[float, float]] = []
    time_val: Optional[float] = None
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

    if not envelope:
        if HAS_PYDUB:
            audio = AudioSegment.from_wav(str(audio_path))
            chunk_ms = int(frame_duration * 1000)
            for i, chunk in enumerate(audio[::chunk_ms]):
                t = i * frame_duration
                rms = chunk.rms
                db = 20 * math.log10(max(rms, 1)) - 90
                envelope.append((t, db))
        else:
            duration = get_duration(audio_path)
            steps = int(duration / frame_duration)
            envelope = [(i * frame_duration, -30.0) for i in range(steps)]

    return envelope


def get_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


# ─── Moment Detection ─────────────────────────────────────────────────────────

# Exclamatory / profanity words get 2× weight
HIGH_WEIGHT_KEYWORDS = {"merda", "porra", "caralho", "caramba", "cacete", "viado"}


def score_segments(
    transcript_segments: list[dict],
    volume_envelope: list[tuple[float, float]],
    keywords: list[str],
    video_duration: float,
) -> list[dict]:
    """Score segments by keyword presence and volume peaks."""
    keywords_lower = [k.lower() for k in keywords]

    if volume_envelope:
        vol_times = np.array([t for t, _ in volume_envelope])
        vol_vals  = np.array([v for _, v in volume_envelope])
        baseline_db = float(np.percentile(vol_vals, 40))
    else:
        vol_times   = np.array([0.0])
        vol_vals    = np.array([-30.0])
        baseline_db = -30.0

    scored = []
    for seg in transcript_segments:
        text_lower = seg["text"].lower()
        start, end = seg["start"], seg["end"]
        if end - start < 1.0:
            continue

        kw_score = 0.0
        matched  = []
        for kw in keywords_lower:
            if kw in text_lower:
                weight = 2.0 if kw in HIGH_WEIGHT_KEYWORDS else 1.0
                kw_score += weight
                matched.append(kw)

        mask = (vol_times >= start) & (vol_times <= end)
        vol_score = float(np.mean(vol_vals[mask]) - baseline_db) / 10.0 if mask.sum() else 0.0
        vol_score = max(0.0, vol_score)

        reason_parts = []
        if matched:
            unique = list(dict.fromkeys(matched))  # deduplicate, preserve order
            reason_parts.append(f"palavras-chave: {', '.join(unique)}")
        if vol_score > 0.5:
            reason_parts.append("pico de volume")

        scored.append({
            **seg,
            "score": kw_score + vol_score,
            "reason": " + ".join(reason_parts) if reason_parts else "momento relevante",
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
    """Pick top segments, extend to min_duration, cap at max_duration, de-overlap."""
    selected: list[dict] = []
    used_ranges: list[tuple[float, float]] = []

    for seg in scored:
        if len(selected) >= max_clips:
            break

        center    = (seg["start"] + seg["end"]) / 2
        ext_start = max(0.0, center - min_duration / 2)
        ext_end   = min(video_duration, ext_start + min_duration)
        ext_start = max(0.0, ext_end - min_duration)

        if ext_end - ext_start > max_duration:
            ext_end = ext_start + max_duration

        # Skip if overlaps an already-selected clip
        if any(not (ext_end <= us or ext_start >= ue) for us, ue in used_ranges):
            continue

        selected.append({**seg, "clip_start": round(ext_start, 2), "clip_end": round(ext_end, 2)})
        used_ranges.append((ext_start, ext_end))

    return selected


# ─── Karaoke ASS Subtitle Generation ─────────────────────────────────────────

def _fmt_ass_time(seconds: float) -> str:
    """Format seconds → ASS time (H:MM:SS.cc)."""
    seconds = max(0.0, seconds)
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = seconds % 60
    cs = int((s % 1) * 100)
    return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"


def build_karaoke_ass(transcript_segments: list[dict], clip_start: float, clip_end: float) -> str:
    """
    Build ASS subtitle content with word-by-word karaoke highlighting.

    Style:
    - Text is white by default (SecondaryColour).
    - Current word sweeps to teal/cyan (PrimaryColour) using \\kf tags.
    - Large bold font centred at the lower-third of the 9:16 frame.
    """
    # Colours in ASS BGR-alpha format (&HAABBGGRR)
    # White: &H00FFFFFF   Teal/cyan: &H00DCB400  (BGR of #00B4DC = teal)
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        # PrimaryColour=teal  SecondaryColour=white  OutlineColour=black  BackColour=transparent
        "Style: Karaoke,Arial Black,82,&H00DCB400,&H00FFFFFF,&H00000000,&H00000000,"
        "-1,0,0,0,100,100,1,0,1,5,2,2,60,60,140,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    lines: list[str] = []

    for seg in transcript_segments:
        # Skip segments fully outside the clip window
        if seg["end"] < clip_start or seg["start"] > clip_end:
            continue

        words = seg.get("words", [])
        if not words:
            # Fallback: treat whole segment as one word
            words = [{"word": seg["text"].strip(), "start": seg["start"], "end": seg["end"]}]

        # Keep only words that overlap the clip window
        in_window = [
            w for w in words
            if w["end"] > clip_start and w["start"] < clip_end
        ]
        if not in_window:
            continue

        tagged_parts: list[str] = []
        for w in in_window:
            # Clamp word boundaries to clip window
            wstart = max(clip_start, w["start"])
            wend   = min(clip_end,   w["end"])
            # Only emit words where clamped duration is positive
            dur_s  = wend - wstart
            if dur_s <= 0:
                continue
            dur_cs = max(1, int(dur_s * 100))
            word_text = w["word"].strip().upper()
            if word_text:
                tagged_parts.append(f"{{\\kf{dur_cs}}}{word_text}")

        if not tagged_parts:
            continue

        karaoke_text = " ".join(tagged_parts)

        # Event bounds derived from filtered words, not raw segment
        ev_start = max(0.0, in_window[0]["start"] - clip_start)
        ev_end   = min(clip_end - clip_start, in_window[-1]["end"] - clip_start)

        if ev_end <= ev_start:
            continue

        lines.append(
            f"Dialogue: 0,{_fmt_ass_time(ev_start)},{_fmt_ass_time(ev_end)},"
            f"Karaoke,,0,0,0,k,{karaoke_text}"
        )

    return header + "\n".join(lines)


# ─── FFmpeg: Cut + 9:16 + Subtitles ──────────────────────────────────────────

def cut_clip(video_path: Path, start: float, end: float, output_path: Path, ass_content: str) -> None:
    """Cut clip, pad/crop to 9:16 (1080×1920), burn karaoke ASS subtitles."""
    ass_path = output_path.with_suffix(".ass")
    ass_path.write_text(ass_content, encoding="utf-8")

    duration = end - start
    # Escape ass path for ffmpeg filter (backslash and colon)
    ass_esc = str(ass_path).replace("\\", "/").replace(":", "\\:")

    vf = (
        # Scale to fit inside 1080×1920, preserving aspect ratio
        "scale=iw*min(1080/iw\\,1920/ih):ih*min(1080/iw\\,1920/ih),"
        # Pad to exact 9:16 with black bars
        "pad=1080:1920:(1080-iw)/2:(1920-ih)/2:black,"
        # Burn karaoke subtitles
        f"ass={ass_esc}"
    )

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", str(video_path),
        "-t", str(duration),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg error:\n{result.stderr[-2000:]}")

    try:
        ass_path.unlink()
    except Exception:
        pass


# ─── Main Pipeline ────────────────────────────────────────────────────────────

DEFAULT_KEYWORDS = [
    "caramba", "nossa", "meu deus", "incrível", "impossível",
    "uau", "wow", "que isso", "sério", "mentira",
    "absurdo", "fantástico", "impressionante", "surreal",
    "não acredito", "olha isso", "cara", "demais",
    "puta", "merda", "porra", "caralho", "cacete",
    "kkkk", "kkk", "hahaha", "rsrs", "kkkkk",
]


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

    max_secs = float(os.environ.get("MAX_VIDEO_DURATION", "7200"))

    # Pre-flight duration check — avoids downloading a 3 h+ live only to
    # reject it. Uses yt-dlp metadata (no download). If duration is unknown
    # (some live streams don't expose it), we skip and check after download.
    progress(5, "Verificando duração do vídeo...")
    _preflight_base_opts = {
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {"youtube": {"player_client": ["ios", "tv_embedded", "android", "web"]}},
    }
    try:
        with yt_dlp.YoutubeDL(_preflight_base_opts) as _ydl:
            _info_pre = _ydl.extract_info(url, download=False)
            _pre_dur = _info_pre.get("duration")
            if _pre_dur and float(_pre_dur) > max_secs:
                raise RuntimeError(
                    f"Vídeo muito longo ({float(_pre_dur) / 3600:.1f} h). "
                    f"O limite é {max_secs / 3600:.0f} h. "
                    "Corte um trecho menor e tente novamente."
                )
    except RuntimeError:
        raise
    except Exception:
        pass  # metadata unavailable — proceed and check after download

    # 1. Download
    progress(10, "Baixando vídeo...")
    video_path, audio_path = download_video(url, job_dir)
    video_duration = get_duration(video_path)

    # Post-download guard (safety net for cases where pre-flight was skipped).
    if video_duration > max_secs:
        video_path.unlink(missing_ok=True)
        audio_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Vídeo muito longo ({video_duration / 3600:.1f} h). "
            f"O limite é {max_secs / 3600:.0f} h. "
            "Corte um trecho menor e tente novamente."
        )

    # 2. Transcribe
    progress(26, "Transcrevendo áudio com Whisper...")
    whisper_model = os.environ.get("WHISPER_MODEL", "small")
    transcript = transcribe(audio_path, model_name=whisper_model)

    # 3. Volume analysis
    progress(56, "Analisando volume e detectando momentos...")
    envelope = compute_volume_envelope(audio_path)

    # 4. Score segments
    all_keywords = list(dict.fromkeys(keywords + DEFAULT_KEYWORDS))
    scored = score_segments(transcript, envelope, all_keywords, video_duration)

    if not scored:
        raise RuntimeError(
            "Nenhum segmento de fala detectado. "
            "Verifique se o vídeo tem áudio em português."
        )

    # 5. Select and extend clips
    clips_meta = merge_and_extend_segments(
        scored, video_duration, min_duration, max_duration, max_clips
    )

    # Fallback: use top scored even with overlap
    if not clips_meta:
        clips_meta = scored[:max_clips]
        for c in clips_meta:
            c["clip_start"] = max(0.0, c["start"] - min_duration / 2)
            c["clip_end"]   = min(video_duration, c["clip_start"] + min_duration)

    # 6. Cut each clip with karaoke subtitles
    clips_out = []
    total = max(len(clips_meta), 1)
    for i, clip in enumerate(clips_meta):
        progress(
            62 + int((i / total) * 33),
            f"Cortando clipe {i + 1}/{total} com legenda karaokê...",
        )

        filename    = f"clip_{i + 1:02d}.mp4"
        output_path = job_dir / filename

        ass = build_karaoke_ass(transcript, clip["clip_start"], clip["clip_end"])
        cut_clip(video_path, clip["clip_start"], clip["clip_end"], output_path, ass)

        clips_out.append({
            "filename": filename,
            "label":    f"Clipe {i + 1}",
            "start":    clip["clip_start"],
            "end":      clip["clip_end"],
            "reason":   clip.get("reason", ""),
            "score":    round(clip.get("score", 0), 2),
        })

    # Free disk space — remove source video AND audio after clips are cut.
    # Leaving video.mp4 would fill Railway's ephemeral disk over time.
    for tmp in (video_path, audio_path):
        try:
            tmp.unlink()
        except Exception:
            pass

    return clips_out
