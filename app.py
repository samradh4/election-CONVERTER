from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from backend.config import OUTPUT_DIR, RESOURCE_ROOT, UPLOAD_DIR, ConversionConfig
from backend.converter import convert_pdf
from backend.excel_writer import write_excel
from backend.utils import safe_filename

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Election PDF to Excel Backend",
    description="Hindi/English electoral-roll PDF to formatted Excel converter",
    version="3.0.0",
)

origins = [value.strip() for value in os.getenv("CORS_ORIGINS", "*").split(",") if value.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@dataclass
class Job:
    id: str
    status: str = "queued"
    progress: int = 0
    message: str = "Queued"
    input_path: str = ""
    output_path: str = ""
    original_name: str = ""
    error: str = ""
    records: int = 0
    review_records: int = 0
    warnings: int = 0
    created_at: float = field(default_factory=time.time)


JOBS: Dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def _job_dict(job: Job) -> dict:
    data = asdict(job)
    data["download_url"] = "/api/jobs/{}/download".format(job.id) if job.status == "completed" else None
    return data


def _save_upload(upload: UploadFile, destination: Path) -> None:
    total = 0
    try:
        with destination.open("wb") as target:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="PDF is larger than 500 MB.")
                target.write(chunk)
    except Exception:
        if destination.exists():
            destination.unlink()
        raise


def _run_job(job_id: str, config: ConversionConfig) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        job.status = "processing"
        job.message = "PDF processing started"

    def progress(done: int, total: int, message: str) -> None:
        percentage = 5 if total <= 0 else min(94, 5 + round((done / float(total)) * 85))
        with JOBS_LOCK:
            current = JOBS[job_id]
            current.progress = percentage
            current.message = message

    try:
        result = convert_pdf(job.input_path, config=config, progress_callback=progress)
        write_excel(result, Path(job.output_path))
        with JOBS_LOCK:
            current = JOBS[job_id]
            current.status = "completed"
            current.progress = 100
            current.message = "Excel ready"
            current.records = len(result.records)
            current.review_records = result.review_count
            current.warnings = len(result.warnings)
    except Exception as exc:
        with JOBS_LOCK:
            current = JOBS[job_id]
            current.status = "failed"
            current.error = str(exc)
            current.message = "Conversion failed"


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    html_path = RESOURCE_ROOT / "static" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "election-pdf-to-excel", "version": "3.0.0"}


@app.post("/api/jobs")
def create_job(
    pdf: UploadFile = File(...),
    mode: str = Form("accurate"),
    use_manual_metadata: bool = Form(False),
    constituency: str = Form(""),
    section: str = Form(""),
    part_number: str = Form(""),
) -> JSONResponse:
    if not (pdf.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")
    if mode not in ("fast", "balanced", "accurate"):
        raise HTTPException(status_code=400, detail="Invalid processing mode.")
    job_id = uuid.uuid4().hex
    safe_name = safe_filename(pdf.filename or "election_roll.pdf")
    input_path = UPLOAD_DIR / "{}_{}".format(job_id, safe_name)
    output_path = OUTPUT_DIR / "{}_converted_{}.xlsx".format(Path(safe_name).stem, job_id[:8])
    _save_upload(pdf, input_path)
    job = Job(
        id=job_id,
        input_path=str(input_path),
        output_path=str(output_path),
        original_name=safe_name,
    )
    with JOBS_LOCK:
        JOBS[job_id] = job
    config = ConversionConfig(
        mode=mode,
        use_manual_metadata=use_manual_metadata,
        constituency_override=constituency.strip(),
        section_override=section.strip(),
        part_override=part_number.strip(),
    )
    threading.Thread(target=_run_job, args=(job_id, config), daemon=True).start()
    return JSONResponse(_job_dict(job), status_code=202)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")
        return _job_dict(job)


@app.get("/api/jobs/{job_id}/download")
def download_job(job_id: str) -> FileResponse:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")
        if job.status != "completed":
            raise HTTPException(status_code=409, detail="Job is not complete.")
        output_path = Path(job.output_path)
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="Output file is missing.")
    return FileResponse(
        str(output_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=output_path.name,
    )


@app.post("/api/convert")
def direct_convert(
    pdf: UploadFile = File(...),
    mode: str = Form("accurate"),
    use_manual_metadata: bool = Form(False),
    constituency: str = Form(""),
    section: str = Form(""),
    part_number: str = Form(""),
) -> FileResponse:
    response = create_job(
        pdf, mode, use_manual_metadata, constituency, section, part_number
    )
    payload = json.loads(response.body)
    job_id = payload["id"]
    while True:
        with JOBS_LOCK:
            job = JOBS[job_id]
            status = job.status
            error = job.error
        if status == "completed":
            return download_job(job_id)
        if status == "failed":
            raise HTTPException(status_code=500, detail=error)
        time.sleep(0.5)
