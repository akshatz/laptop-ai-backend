"""Text from uploaded files, for adding them to a chat as context: PDF (pypdf, text layer only,
no OCR), Word .docx (python-docx, paragraphs and tables) and plain text (.txt, .md, .csv, .json,
.html, …). Raises UnsupportedFile for anything else, and for files with no text (e.g. a scanned
PDF without a text layer). Pictures (.png, .jpg, .webp, .gif) are read in the upload's background
task by read_image, with both: a vision model in Ollama says what the picture shows (IMAGE_MODEL,
default gemma3:4b; ~30 s for a screenshot on CPU), which OCR alone can't (on a screenshot of a chat
app, OCR found "Fable 5.2 Medium" but nothing saying it was the model picker), and Tesseract OCR
adds the exact text (~1 s), which the vision model may misread. Either one failing leaves the other."""

import asyncio
import base64
import io
import os
import subprocess
from pathlib import Path

from docx import Document as DocxDocument
from pypdf import PdfReader
from pypdf.errors import PdfReadError
import httpx

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".html", ".htm", ".xml", ".log", ".yaml", ".yml"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SUPPORTED = sorted(TEXT_SUFFIXES | IMAGE_SUFFIXES | {".pdf", ".docx"})

IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "gemma3:4b")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
IMAGE_PROMPT = (
    "Describe what this picture shows, using only what is visible. Copy every label, name, number and "
    "version exactly as written; never guess or add a version, brand or detail that is not shown. "
    "For a screenshot: which app or website (only if its name is visible), then each visible setting, "
    "menu, button or selector as 'what it is: its exact text' (for example 'model selector: <text>'), "
    "noting where it is on screen. If a value has parts, such as a model name followed by a level like "
    "Low/Medium/High, say what each part likely means (for example a reasoning or effort level). "
    "For a document or photo: what it is and its key facts. Plain text, at most 180 words."
)


class UnsupportedFile(ValueError):
    pass


def extract_text(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except (PdfReadError, ValueError) as exc:
            raise UnsupportedFile(f"{filename}: not a readable PDF ({exc})") from exc
    elif suffix == ".docx":
        try:
            doc = DocxDocument(io.BytesIO(data))
        except Exception as exc:  # python-docx raises several unrelated types for bad files
            raise UnsupportedFile(f"{filename}: not a readable Word file") from exc
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        text = "\n".join(parts)
    elif suffix in TEXT_SUFFIXES:
        text = data.decode("utf-8", errors="replace")
    else:
        raise UnsupportedFile(f"{filename}: unsupported file type (use {', '.join(SUPPORTED)})")

    text = text.strip()
    if not text:
        raise UnsupportedFile(f"{filename}: no text found (a scanned PDF needs OCR, which isn't supported here)")
    return text


def is_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in IMAGE_SUFFIXES


async def describe_image(filename: str, data: bytes) -> str:
    """The image's visible text plus a short description, from IMAGE_MODEL through Ollama."""
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(f"{OLLAMA_BASE_URL}/api/generate", json={
            "model": IMAGE_MODEL, "prompt": IMAGE_PROMPT, "stream": False,
            "images": [base64.b64encode(data).decode()], "options": {"temperature": 0.1},
        })
        response.raise_for_status()
    text = (response.json().get("response") or "").strip()
    if not text:
        raise UnsupportedFile(f"{filename}: no text or description could be read from the picture")
    return text


def ocr_image(data: bytes) -> str:
    """Text in the picture via the tesseract CLI (installed in the image), '' if it can't run."""
    try:
        result = subprocess.run(["tesseract", "stdin", "stdout", "-l", "eng"], input=data,
                                capture_output=True, timeout=120, check=True)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.decode("utf-8", errors="replace").strip()


async def read_image(filename: str, data: bytes) -> str:
    """The vision model's description plus the OCR text; UnsupportedFile if neither gives anything."""
    ocr = await asyncio.to_thread(ocr_image, data)
    try:
        description = await describe_image(filename, data)
    except Exception:
        if not ocr:
            raise
        description = ""
    parts = [f"Description of the picture {filename}: {description}" if description else "",
             f"Text in the picture (OCR):\n{ocr}" if ocr else ""]
    return "\n\n".join(p for p in parts if p)
