from __future__ import annotations

import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.background import BackgroundTask
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from .converter import convert_office_to_pdf, images_to_pdf, merge_pdfs

app = FastAPI(title="Конвертер документов в PDF")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

WORK_DIR = Path(tempfile.gettempdir()) / "document_pdf_webapp"
WORK_DIR.mkdir(parents=True, exist_ok=True)
MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_FILES = 20
ALLOWED_EXTENSIONS = {".doc", ".docx", ".xls", ".xlsx", ".jpg", ".jpeg"}
OFFICE_EXTENSIONS = {".doc", ".docx", ".xls", ".xlsx"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg"}


def remove_job(directory: Path) -> None:
    shutil.rmtree(directory, ignore_errors=True)


def safe_name(filename: str, index: int) -> tuple[str, str]:
    basename = Path(filename).name
    suffix = Path(basename).suffix.lower()
    stem = Path(basename).stem[:100] or "file"
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Формат файла «{basename}» не поддерживается.")
    return f"{index:03d}_{stem}{suffix}", suffix


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.post("/api/convert")
async def convert(
    files: list[UploadFile] = File(...),
    output_mode: Literal["separate", "merged"] = Form("separate"),
):
    if not files:
        raise HTTPException(400, "Добавьте хотя бы один файл.")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"Можно загрузить не более {MAX_FILES} файлов за один раз.")

    job_dir = WORK_DIR / uuid.uuid4().hex
    input_dir = job_dir / "input"
    output_dir = job_dir / "output"
    input_dir.mkdir(parents=True)
    output_dir.mkdir()

    try:
        uploaded: list[tuple[Path, str, int]] = []
        for index, upload in enumerate(files):
            filename, suffix = safe_name(upload.filename or "", index)
            destination = input_dir / filename
            size = 0
            with destination.open("wb") as buffer:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_FILE_SIZE:
                        raise HTTPException(413, f"Файл «{upload.filename}» превышает 50 МБ.")
                    buffer.write(chunk)
            await upload.close()
            uploaded.append((destination, suffix, index))

        document_pdfs: dict[int, Path] = {}
        images: list[tuple[Path, int]] = []
        for source, suffix, index in uploaded:
            if suffix in OFFICE_EXTENSIONS:
                document_pdfs[index] = convert_office_to_pdf(source, output_dir)
            elif suffix in IMAGE_EXTENSIONS:
                images.append((source, index))

        if images:
            image_pdf = images_to_pdf([source for source, _ in images], output_dir / "images.pdf")
            document_pdfs[images[0][1]] = image_pdf

        pdfs = [document_pdfs[index] for index in sorted(document_pdfs)]
        if not pdfs:
            raise HTTPException(422, "Не удалось создать PDF.")

        cleanup = BackgroundTask(remove_job, job_dir)
        if output_mode == "merged":
            result = merge_pdfs(pdfs, job_dir / "merged_document.pdf")
            return FileResponse(result, media_type="application/pdf", filename="converted_document.pdf", background=cleanup)

        if len(pdfs) == 1:
            result = pdfs[0]
            return FileResponse(result, media_type="application/pdf", filename=result.name, background=cleanup)

        archive = job_dir / "converted_pdfs.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for pdf in pdfs:
                zip_file.write(pdf, pdf.name)
        return FileResponse(archive, media_type="application/zip", filename="converted_pdfs.zip", background=cleanup)
    except HTTPException:
        remove_job(job_dir)
        raise
    except Exception as exc:
        remove_job(job_dir)
        raise HTTPException(500, f"Внутренняя ошибка обработки: {exc}") from exc
