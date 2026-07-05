import os
import uuid
import asyncio
import json
from pathlib import Path
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from video_processor import process_video

app = FastAPI(title="Video Clip Generator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # public API — no credentials sent
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Accept"],
)

JOBS_DIR = Path("jobs")
JOBS_DIR.mkdir(exist_ok=True)

jobs: dict[str, dict] = {}


class ProcessRequest(BaseModel):
    url: str
    keywords: Optional[list[str]] = None
    min_clip_duration: Optional[float] = 20.0
    max_clip_duration: Optional[float] = 60.0
    max_clips: Optional[int] = 5

    @classmethod
    def model_post_init(cls, __context):
        pass

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


@app.get("/")
def root():
    return {"status": "ok", "message": "Video Clip Generator API"}


@app.post("/process")
async def start_processing(req: ProcessRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    jobs[job_id] = {
        "status": "queued",
        "progress": 0,
        "message": "Na fila...",
        "clips": [],
        "error": None,
    }

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
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    return jobs[job_id]


@app.get("/download/{job_id}/{filename}")
def download_clip(job_id: str, filename: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job não encontrado")

    # Sanitize: strip any path components to prevent traversal
    safe_filename = Path(filename).name
    if not safe_filename or safe_filename != filename:
        raise HTTPException(status_code=400, detail="Nome de arquivo inválido")

    job_dir = JOBS_DIR / job_id
    clip_path = job_dir / safe_filename

    # Ensure resolved path is still inside the job directory
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


def default_keywords() -> list[str]:
    return [
        "caramba", "nossa", "meu deus", "incrível", "impossível",
        "uau", "wow", "que isso", "sério", "mentira",
        "absurdo", "fantástico", "impressionante", "surreal",
        "não acredito", "olha isso", "cara", "demais",
        "puta", "merda", "porra", "caralho", "viado",
        "kkkk", "kkk", "hahaha", "rsrs",
    ]


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
        jobs[job_id].update({"status": status, "progress": progress, "message": message})

    try:
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

        jobs[job_id].update({
            "status": "done",
            "progress": 100,
            "message": f"{len(clips)} clipe(s) prontos!",
            "clips": [
                {
                    "filename": c["filename"],
                    "label": c["label"],
                    "start": c["start"],
                    "end": c["end"],
                    "reason": c["reason"],
                }
                for c in clips
            ],
        })

    except Exception as e:
        jobs[job_id].update({
            "status": "error",
            "progress": 0,
            "message": "Erro durante o processamento",
            "error": str(e),
        })
        import traceback
        traceback.print_exc()
