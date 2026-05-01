from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from comp_agent.models import SourceLogEntry, utc_now_iso
from comp_agent.workspace import slugify, write_json


MAX_SOURCE_BYTES = 12 * 1024 * 1024


class _RedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if code in {301, 302, 303, 307, 308}:
            return urllib.request.Request(
                newurl,
                headers=dict(req.header_items()),
                method="GET",
            )
        return None


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip = False
        if tag.lower() in {"p", "div", "li", "tr", "h1", "h2", "h3", "br"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            text = data.strip()
            if text:
                self.parts.append(text)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", "\n".join(self.parts))).strip()


def archive_source_documents(source_log: list[SourceLogEntry], output_dir: str | Path) -> Path:
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    for index, entry in enumerate(source_log, start=1):
        url = entry.url_or_search
        if not _is_http_url(url):
            continue
        manifest.append(_archive_one(entry, folder, index))
    return write_json(folder / "sources_manifest.json", manifest)


def _archive_one(entry: SourceLogEntry, folder: Path, index: int) -> dict[str, Any]:
    base = f"{index:02d}-{slugify(entry.source_name)[:72]}"
    result: dict[str, Any] = {
        "source_name": entry.source_name,
        "source_type": entry.source_type,
        "url": entry.url_or_search,
        "retrieved_at": utc_now_iso(),
        "notes": entry.notes,
        "status": "pending",
        "saved_path": "",
        "text_path": "",
        "content_type": "",
        "error": "",
    }
    request = urllib.request.Request(
        entry.url_or_search,
        headers={
            "User-Agent": "CompAgent/0.1 source archiver",
            "Accept": "text/html,application/pdf,text/plain,*/*;q=0.8",
        },
    )
    opener = urllib.request.build_opener(_RedirectHandler)
    try:
        with opener.open(request, timeout=25) as response:
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            data = response.read(MAX_SOURCE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        result["status"] = "failed"
        result["error"] = str(error)
        return result

    if len(data) > MAX_SOURCE_BYTES:
        result["status"] = "failed"
        result["error"] = f"Source exceeded {MAX_SOURCE_BYTES} byte limit."
        return result

    extension = _extension_for(entry.url_or_search, content_type)
    saved_path = folder / f"{base}{extension}"
    saved_path.write_bytes(data)
    result["status"] = "saved"
    result["saved_path"] = str(saved_path)
    result["content_type"] = content_type

    text = _extract_text(data, content_type, extension)
    if text:
        text_path = folder / f"{base}.txt"
        text_path.write_text(text, encoding="utf-8")
        result["text_path"] = str(text_path)
    return result


def _is_http_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _extension_for(url: str, content_type: str) -> str:
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix in {".html", ".htm", ".pdf", ".txt", ".csv", ".json"}:
        return suffix
    if content_type == "application/pdf":
        return ".pdf"
    if content_type in {"text/plain", "text/csv"}:
        return ".txt"
    if content_type == "application/json":
        return ".json"
    return ".html"


def _extract_text(data: bytes, content_type: str, extension: str) -> str:
    if extension == ".pdf" or content_type == "application/pdf":
        return ""
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError:
        decoded = data.decode("latin-1", errors="ignore")
    if extension in {".txt", ".csv", ".json"} or content_type.startswith("text/"):
        return decoded.strip()
    parser = _TextExtractor()
    parser.feed(decoded)
    return parser.text()
