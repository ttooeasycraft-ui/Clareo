import os
import uuid
import time
import asyncio
import shutil
import json
from pathlib import Path
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

# NOTE: video_processor is NOT imported at the top level on purpose.
# Importing it triggers faster_whisper → ctranslate2 init, which can block
# uvicorn startup long enough for Railway's healthcheck to time out.
# We import lazily inside the background task instead.

app = FastAPI(title="Video Clip Generator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Accept"],
)

JOBS_DIR = Path("jobs")
JOBS_DIR.mkdir(exist_ok=True)

# How long to keep finished/errored job directories on disk (seconds).
JOB_TTL = int(os.environ.get("JOB_TTL_SECONDS", "3600"))  # default 1 h

# ─── Persistent job state ─────────────────────────────────────────────────────
# Each job gets a status.json inside its directory.  The in-memory dict is just
# a cache; every mutation is flushed to disk atomically so a Railway restart
# never causes a 404 for a job whose clips already exist.

jobs: dict[str, dict] = {}


def _status_path(job_id: str) -> Path:
    return JOBS_DIR / job_id / "status.json"


def _save_job(job_id: str, state: dict) -> None:
    """Atomically write state to jobs/{job_id}/status.json."""
    path = _status_path(job_id)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False))
        os.replace(tmp, path)          # atomic on POSIX
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _load_job(job_id: str) -> dict | None:
    """Read state from disk; return None if the file doesn't exist or is corrupt."""
    path = _status_path(job_id)
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _set_job(job_id: str, state: dict) -> None:
    """Update in-memory cache and persist to disk."""
    jobs[job_id] = state
    _save_job(job_id, state)


def _update_job(job_id: str, **fields) -> None:
    """Merge fields into current state, then persist."""
    current = jobs.get(job_id) or _load_job(job_id) or {}
    current.update(fields)
    _set_job(job_id, current)


def _recover_jobs_from_disk() -> None:
    """On startup: load every status.json found on disk into the memory cache.

    Jobs that were 'queued' or 'running' when the server died are marked
    'error' with an explanation — they cannot be resumed, but at least the
    frontend gets a meaningful response instead of a 404.
    """
    for job_dir in JOBS_DIR.iterdir():
        if not job_dir.is_dir():
            continue
        state = _load_job(job_dir.name)
        if state is None:
            continue
        if state.get("status") in ("queued", "running"):
            state.update({
                "status": "error",
                "message": "Servidor reiniciado durante o processamento. Tente novamente.",
                "error": "server_restart",
            })
            _save_job(job_dir.name, state)
        jobs[job_dir.name] = state


# Recover on import (runs once when uvicorn loads the app module).
_recover_jobs_from_disk()


# ─── Cleanup ──────────────────────────────────────────────────────────────────

def _cleanup_old_jobs() -> None:
    """Delete job dirs older than JOB_TTL. Skips actively running jobs."""
    cutoff = time.time() - JOB_TTL
    for job_dir in JOBS_DIR.iterdir():
        try:
            if not job_dir.is_dir():
                continue
            status = jobs.get(job_dir.name, {}).get("status", "")
            if status in ("queued", "running"):
                continue
            if job_dir.stat().st_mtime < cutoff:
                shutil.rmtree(job_dir, ignore_errors=True)
                jobs.pop(job_dir.name, None)
        except Exception:
            pass


# ─── Request model ────────────────────────────────────────────────────────────

class ProcessRequest(BaseModel):
    url: str
    keywords: Optional[list[str]] = None
    min_clip_duration: Optional[float] = 20.0
    max_clip_duration: Optional[float] = 60.0
    max_clips: Optional[int] = 5

    def model_post_init(self, __context):
        if self.min_clip_duration is not None and self.min_clip_duration <= 0:
            raise ValueError("min_clip_duration deve ser positivo")
        if self.max_clip_duration is not None and self.max_clip_duration <= 0:
            raise ValueError("max_clip_duration deve ser positivo")
        if (
            self.min_clip_duration is not None
            and self.max_clip_duration is not None
            and self.min_clip_duration > self.max_clip_duration
        ):
            raise ValueError("min_clip_duration não pode ser maior que max_clip_duration")
        if self.max_clips is not None and (self.max_clips < 1 or self.max_clips > 10):
            raise ValueError("max_clips deve estar entre 1 e 10")


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "message": "Video Clip Generator API"}


@app.post("/process")
async def start_processing(req: ProcessRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    _set_job(job_id, {
        "status": "queued",
        "progress": 0,
        "message": "Na fila...",
        "clips": [],
        "error": None,
    })

    background_tasks.add_task(
        run_processing,
        job_id=job_id,
        url=req.url,
        keywords=req.keywords or default_keywords(),
        min_duration=req.min_clip_duration,
        max_duration=req.max_clip_duration,
        max_clips=req.max_clips,
        job_dir=job_dir,
    )

    return {"job_id": job_id}


@app.get("/status/{job_id}")
def get_status(job_id: str):
    # Check memory cache first; fall back to disk for post-restart lookups.
    state = jobs.get(job_id) or _load_job(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    # Warm the cache so next poll is instant.
    if job_id not in jobs:
        jobs[job_id] = state
    return state


@app.get("/download/{job_id}/{filename}")
def download_clip(job_id: str, filename: str):
    # Accept both cached and disk-recovered jobs.
    state = jobs.get(job_id) or _load_job(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Job não encontrado")

    safe_filename = Path(filename).name
    if not safe_filename or safe_filename != filename:
        raise HTTPException(status_code=400, detail="Nome de arquivo inválido")

    job_dir = JOBS_DIR / job_id
    clip_path = job_dir / safe_filename

    try:
        clip_path.resolve().relative_to(job_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Acesso negado")

    if not clip_path.exists():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")

    return FileResponse(
        path=str(clip_path),
        media_type="video/mp4",
        filename=safe_filename,
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def default_keywords() -> list[str]:
    return [
        "caramba", "nossa", "meu deus", "incrível", "impossível",
        "uau", "wow", "que isso", "sério", "mentira",
        "absurdo", "fantástico", "impressionante", "surreal",
        "não acredito", "olha isso", "cara", "demais",
        "puta", "merda", "porra", "caralho", "viado",
        "kkkk", "kkk", "hahaha", "rsrs",
    ]


# ─── Background task ──────────────────────────────────────────────────────────

async def run_processing(
    job_id: str,
    url: str,
    keywords: list[str],
    min_duration: float,
    max_duration: float,
    max_clips: int,
    job_dir: Path,
):
    def update(status: str, progress: int, message: str):
        _update_job(job_id, status=status, progress=progress, message=message)

    try:
        _cleanup_old_jobs()

        from video_processor import process_video  # noqa: PLC0415

        update("running", 5, "Baixando vídeo...")
        clips = await asyncio.to_thread(
            process_video,
            url=url,
            keywords=keywords,
            min_duration=min_duration,
            max_duration=max_duration,
            max_clips=max_clips,
            job_dir=job_dir,
            progress_callback=lambda p, m: update("running", p, m),
        )

        _update_job(job_id,
            status="done",
            progress=100,
            message=f"{len(clips)} clipe(s) prontos!",
            clips=[
                {
                    "filename": c["filename"],
                    "label": c["label"],
                    "start": c["start"],
                    "end": c["end"],
                    "reason": c["reason"],
                    "score": c.get("score", 0),
                }
                for c in clips
            ],
        )

    except Exception as e:
        _update_job(job_id,
            status="error",
            progress=0,
            message="Erro durante o processamento",
            error=str(e),
        )
        import traceback
        traceback.print_exc()
