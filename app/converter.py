from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageOps
from pypdf import PdfWriter


def convert_office_to_pdf(source: Path, output_dir: Path) -> Path:
    command = [
        "soffice", "--headless", "--nologo", "--nolockcheck", "--nodefault",
        "--nofirststartwizard", "--convert-to", "pdf", "--outdir", str(output_dir), str(source),
    ]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, f"Истекло время конвертации файла «{source.name}».") from exc
    except FileNotFoundError as exc:
        raise HTTPException(500, "LibreOffice не найден в окружении сервера.") from exc

    pdf_path = output_dir / f"{source.stem}.pdf"
    if completed.returncode != 0 or not pdf_path.exists():
        error = (completed.stderr or completed.stdout or "Неизвестная ошибка LibreOffice.").strip()
        raise HTTPException(422, f"Не удалось конвертировать «{source.name}»: {error}")
    return pdf_path


def images_to_pdf(images: list[Path], output_path: Path) -> Path:
    pages: list[Image.Image] = []
    try:
        for image_path in images:
            with Image.open(image_path) as image:
                pages.append(ImageOps.exif_transpose(image).convert("RGB").copy())
        if not pages:
            raise HTTPException(422, "Не найдены изображения для конвертации.")
        pages[0].save(output_path, "PDF", save_all=True, append_images=pages[1:], resolution=150.0)
        return output_path
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, f"Не удалось обработать изображение: {exc}") from exc
    finally:
        for page in pages:
            page.close()


def merge_pdfs(pdfs: list[Path], output_path: Path) -> Path:
    writer = PdfWriter()
    try:
        for pdf in pdfs:
            writer.append(str(pdf))
        with output_path.open("wb") as destination:
            writer.write(destination)
        return output_path
    except Exception as exc:
        raise HTTPException(422, f"Не удалось объединить PDF: {exc}") from exc
    finally:
        writer.close()
