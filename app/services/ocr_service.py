"""Document text extraction, both intake channels.

Typed PDFs (all of test_data/) extract cleanly via a local text layer read -- no
network call needed. Azure AI Vision's Read API is only invoked as a fallback, for
scanned/image-only pages where the local extraction comes back empty, and only when
Azure credentials are configured; otherwise the local (possibly empty) result is
returned as-is rather than failing the whole intake.
"""

from __future__ import annotations

import io
import os
import time

import httpx
from pypdf import PdfReader

AZURE_VISION_ENDPOINT = os.environ.get("AZURE_VISION_ENDPOINT")
AZURE_VISION_KEY = os.environ.get("AZURE_VISION_KEY")

# A typed page with real text almost always has well over this many characters;
# well below it signals a scanned/image page worth sending to Azure Vision instead.
MIN_EXPECTED_CHARS_PER_PAGE = 20


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    local_text = _extract_locally(pdf_bytes)
    if _looks_sufficient(local_text, pdf_bytes):
        return local_text

    if AZURE_VISION_ENDPOINT and AZURE_VISION_KEY:
        vision_text = _extract_with_azure_vision(pdf_bytes)
        if vision_text.strip():
            return vision_text

    return local_text


def _extract_locally(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages).strip()


def _looks_sufficient(text: str, pdf_bytes: bytes) -> bool:
    page_count = len(PdfReader(io.BytesIO(pdf_bytes)).pages)
    return len(text) >= MIN_EXPECTED_CHARS_PER_PAGE * max(page_count, 1)


def _extract_with_azure_vision(pdf_bytes: bytes) -> str:
    """Azure AI Vision Read API (async: submit, then poll for the result)."""
    submit_url = f"{AZURE_VISION_ENDPOINT}/vision/v3.2/read/analyze"
    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_VISION_KEY,
        "Content-Type": "application/octet-stream",
    }

    with httpx.Client(timeout=30.0) as client:
        submit_response = client.post(submit_url, headers=headers, content=pdf_bytes)
        submit_response.raise_for_status()
        operation_url = submit_response.headers["Operation-Location"]

        for _ in range(20):
            time.sleep(1)
            result_response = client.get(operation_url, headers=headers)
            result_response.raise_for_status()
            result = result_response.json()
            if result["status"] in ("succeeded", "failed"):
                break
        else:
            return ""

        if result["status"] != "succeeded":
            return ""

        lines = [
            line["text"]
            for page in result["analyzeResult"]["readResults"]
            for line in page["lines"]
        ]
        return "\n".join(lines)
