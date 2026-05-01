from __future__ import annotations

import imghdr
import io
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from PIL import Image

from comp_agent.openai_search import OpenAIWebSearchProvider
from comp_agent.workspace import slugify, write_json


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_PAGE_BYTES = 2 * 1024 * 1024
SOURCE_PAGE_LIMIT = 3
IMAGE_CANDIDATE_LIMIT = 18
IMAGE_CANDIDATES_PER_PAGE = 10


class _ImageLinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value for key, value in attrs if value}
        if tag.lower() == "meta":
            name = (attr_map.get("property") or attr_map.get("name") or "").lower()
            if name in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"}:
                self._add(attr_map.get("content"))
        if tag.lower() == "link":
            rel = (attr_map.get("rel") or "").lower()
            if "image_src" in rel:
                self._add(attr_map.get("href"))
        if tag.lower() == "img":
            for key in ("src", "data-src", "data-original", "data-lazy-src"):
                self._add(attr_map.get(key))
            srcset = attr_map.get("srcset") or attr_map.get("data-srcset")
            if srcset:
                for part in srcset.split(","):
                    self._add(part.strip().split(" ")[0])

    def _add(self, value: str | None) -> None:
        if not value or value.startswith("data:"):
            return
        url = urllib.parse.urljoin(self.base_url, value)
        if url not in self.urls:
            self.urls.append(url)


def download_hero_images(deck_data: dict[str, Any], output_dir: str | Path) -> Path:
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    for index, comp in enumerate(deck_data.get("comps", []), start=1):
        hero = comp.get("hero_image") or {}
        package = comp.get("image_package") if isinstance(comp.get("image_package"), dict) else {}
        item = {
            "project_name": comp.get("project_name", ""),
            "url": hero.get("url") or "",
            "status": "not_available",
            "path": "",
            "error": "",
            "fallback_used": False,
            "attempted_urls": [],
            "slots": {},
            "rejections": [],
            "repair_attempted": False,
        }

        candidates = _collect_image_candidates(comp, hero, package)
        discovered_urls = _discover_page_image_urls(_source_page_urls(comp, hero))
        candidates.extend(_candidate_from_url(url, "alternate", "source page image discovery") for url in discovered_urls)
        selected = _download_image_slots(candidates, folder, index, comp, item)

        missing_slots = [slot for slot in ("overall", "focus", "detail") if slot not in selected]
        if missing_slots:
            repair_candidates = _repair_image_candidates(comp, missing_slots, item)
            if repair_candidates:
                candidates.extend(repair_candidates)
                selected = _download_image_slots(candidates, folder, index, comp, item)
                missing_slots = [slot for slot in ("overall", "focus", "detail") if slot not in selected]

        if not package:
            package = {"overall": {}, "focus": {}, "detail": {}}
        slots = ("overall", "focus", "detail")
        for slot in slots:
            slot_data = package.get(slot) if isinstance(package.get(slot), dict) else {}
            if slot in selected:
                selected_item = selected[slot]
                path = selected_item["path"]
                used_url = selected_item["url"]
                slot_data.update(
                    {
                        "slot": slot,
                        "path": str(path),
                        "url": used_url,
                        "source_url": selected_item.get("source_url") or used_url,
                        "confidence": selected_item.get("confidence") or "medium",
                        "selection_reason": selected_item.get("reason") or "Selected from validated project image candidates.",
                    }
                )
                item["slots"][slot] = {"status": "saved", "path": str(path), "url": used_url}
            else:
                slot_data.update(
                    {
                        "slot": slot,
                        "path": "",
                        "url": "",
                        "source_url": "",
                        "confidence": "not_available",
                        "selection_reason": "No validated real project image was available within the bounded search and repair pass.",
                    }
                )
                item["slots"][slot] = {"status": "missing", "path": "", "url": ""}
            package[slot] = slot_data

        overall = package["overall"]
        hero["path"] = overall.get("path", "")
        hero["url"] = overall.get("url") or hero.get("url", "")
        hero["source_url"] = overall.get("source_url") or hero.get("source_url", "")
        hero["image_confidence"] = overall.get("confidence", "medium")
        comp["hero_image"] = hero
        comp["image_package"] = package

        saved_count = sum(1 for value in item["slots"].values() if value["status"] == "saved")
        item["status"] = "saved" if saved_count == 3 else "partial_saved" if saved_count else "no_real_images"
        item["path"] = overall.get("path", "")
        item["used_url"] = overall.get("url", "")
        if saved_count < 3:
            item["error"] = f"{3 - saved_count} image slot(s) missing real validated project images."
            
        manifest.append(item)
    return write_json(folder / "image_manifest.json", manifest)


def _download_image_slots(
    candidates: list[dict[str, Any]],
    folder: Path,
    index: int,
    comp: dict[str, Any],
    item: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    seen_urls: set[str] = set()
    fingerprints: list[int] = []
    ranked = sorted(candidates, key=lambda candidate: _candidate_rank(candidate), reverse=True)
    for slot in ("overall", "focus", "detail"):
        for candidate in ranked:
            if slot in selected:
                break
            url = str(candidate.get("url") or "")
            if not _could_be_direct_image(url):
                _reject(item, url, slot, "not a direct image candidate")
                continue
            if not _role_matches_slot(candidate, slot):
                continue
            normalized_url = _normalize_image_url(url)
            if normalized_url in seen_urls:
                _reject(item, url, slot, "duplicate URL")
                continue
            seen_urls.add(normalized_url)
            item["attempted_urls"].append(url)
            try:
                path = _download_image(url, folder / f"comp_{index:02d}_{slot}_{slugify(comp.get('project_name', 'hero'))[:42]}")
                if not _validate_image_quality(path):
                    _reject(item, url, slot, "failed quality validation")
                    continue
                fingerprint = _image_fingerprint(path)
                if any(_hamming_distance(fingerprint, existing) <= 8 for existing in fingerprints):
                    _reject(item, url, slot, "visually duplicate image")
                    path.unlink(missing_ok=True)
                    continue
                fingerprints.append(fingerprint)
                selected[slot] = {
                    "path": path,
                    "url": url,
                    "source_url": candidate.get("source_url") or url,
                    "confidence": candidate.get("confidence") or "medium",
                    "reason": candidate.get("why_candidate") or candidate.get("reason") or f"Best validated {slot} image candidate.",
                }
            except RuntimeError as error:
                _reject(item, url, slot, str(error))
            continue
    return selected


def _collect_image_candidates(comp: dict[str, Any], hero: dict[str, Any], package: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for slot in ("overall", "focus", "detail"):
        slot_data = package.get(slot) if isinstance(package.get(slot), dict) else {}
        candidates.extend(
            [
                _candidate_from_url(slot_data.get("url"), slot, f"OpenAI image_package.{slot}", slot_data),
                _candidate_from_url(slot_data.get("source_url"), slot, f"OpenAI image_package.{slot} source URL", slot_data),
            ]
        )
    candidates.append(_candidate_from_url(hero.get("url"), "overall", "OpenAI hero image", hero))
    for url in hero.get("fallback_urls") or []:
        candidates.append(_candidate_from_url(url, "alternate", "OpenAI hero fallback URL"))
    for candidate in comp.get("image_candidates") or []:
        if isinstance(candidate, dict):
            candidates.append(
                {
                    "url": candidate.get("url"),
                    "role": candidate.get("role") or "alternate",
                    "source_url": candidate.get("source_url") or candidate.get("url"),
                    "caption": candidate.get("caption") or "",
                    "credit": candidate.get("credit") or "",
                    "confidence": candidate.get("confidence") or "medium",
                    "why_candidate": candidate.get("why_candidate") or "OpenAI supplied image candidate.",
                }
            )
    return [candidate for candidate in candidates if candidate.get("url")]


def _candidate_from_url(url: Any, role: str, reason: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data or {}
    return {
        "url": url,
        "role": role,
        "source_url": data.get("source_url") or url,
        "caption": data.get("caption") or "",
        "credit": data.get("credit") or "",
        "confidence": data.get("confidence") or data.get("image_confidence") or "medium",
        "why_candidate": data.get("selection_reason") or reason,
    }


def _repair_image_candidates(comp: dict[str, Any], missing_slots: list[str], item: dict[str, Any]) -> list[dict[str, Any]]:
    item["repair_attempted"] = True
    provider = OpenAIWebSearchProvider.from_env()
    if not provider:
        item["rejections"].append({"slot": ",".join(missing_slots), "url": "", "reason": "OpenAI live image repair unavailable."})
        return []
    repaired = provider.find_image_candidates(comp, missing_slots)
    raw_candidates = repaired.get("image_candidates") if isinstance(repaired, dict) else []
    candidates: list[dict[str, Any]] = []
    for candidate in raw_candidates or []:
        if not isinstance(candidate, dict):
            continue
        candidates.append(
            {
                "url": candidate.get("url"),
                "role": candidate.get("role") or "alternate",
                "source_url": candidate.get("source_url") or candidate.get("url"),
                "caption": candidate.get("caption") or "",
                "credit": candidate.get("credit") or "",
                "confidence": candidate.get("confidence") or "medium",
                "why_candidate": candidate.get("why_candidate") or "OpenAI targeted image repair candidate.",
            }
        )
    if isinstance(repaired, dict) and repaired.get("warnings"):
        item["rejections"].append({"slot": ",".join(missing_slots), "url": "", "reason": "; ".join(str(w) for w in repaired["warnings"])})
    return candidates


def _candidate_rank(candidate: dict[str, Any]) -> int:
    url = str(candidate.get("url") or "")
    role = str(candidate.get("role") or "alternate").lower()
    text = " ".join([url, str(candidate.get("caption") or ""), str(candidate.get("why_candidate") or "")]).lower()
    score = _image_url_rank(url)
    if role in {"overall", "focus", "detail"}:
        score += 6
    if str(candidate.get("confidence") or "").lower() == "high":
        score += 4
    elif str(candidate.get("confidence") or "").lower() == "medium":
        score += 2
    for token in ("placeholder", "preview", "logo", "icon", "map", "sprite"):
        if token in text:
            score -= 10
    return score


def _role_matches_slot(candidate: dict[str, Any], slot: str) -> bool:
    role = str(candidate.get("role") or "alternate").lower()
    if role == slot or role == "alternate":
        return True
    text = " ".join([str(candidate.get("url") or ""), str(candidate.get("caption") or ""), str(candidate.get("why_candidate") or "")]).lower()
    if slot == "overall":
        return any(token in text for token in ("exterior", "building", "facade", "tower", "site", "hero", "main"))
    if slot == "focus":
        return any(token in text for token in ("lobby", "entrance", "base", "amenity", "public", "plaza", "retail", "street"))
    if slot == "detail":
        return any(token in text for token in ("interior", "detail", "terrace", "rooftop", "amenity", "facade", "plaza", "retail"))
    return False


def _reject(item: dict[str, Any], url: str, slot: str, reason: str) -> None:
    if not url and reason == "not a direct image candidate":
        return
    item["rejections"].append({"slot": slot, "url": url, "reason": reason})


def _normalize_image_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query_items = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in {"w", "width", "h", "height", "fit", "crop", "resize", "quality", "q", "format", "fm"}
    ]
    return urllib.parse.urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            "",
            urllib.parse.urlencode(query_items),
            "",
        )
    )


def _image_fingerprint(path: Path) -> int:
    try:
        with Image.open(path) as image:
            gray = image.convert("L").resize((8, 8), Image.Resampling.LANCZOS)
            pixels = list(gray.getdata())
            average = sum(pixels) / len(pixels)
            bits = 0
            for pixel in pixels:
                bits = (bits << 1) | int(pixel >= average)
            return bits
    except Exception:
        return hash(path.read_bytes()[:4096])


def _hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _could_be_direct_image(url: Any) -> bool:
    if not url:
        return False
    parsed = urllib.parse.urlparse(str(url))
    if parsed.scheme not in {"http", "https"}:
        return False
    suffix = Path(parsed.path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return True
    lowered = str(url).lower()
    return any(token in lowered for token in ["/image/", "/images/", "image=", "img=", "format=jpg", "format=png", "format=webp"])


def _source_page_urls(comp: dict[str, Any], hero: dict[str, Any]) -> list[str]:
    urls = [hero.get("source_url")]
    for source in comp.get("primary_sources") or []:
        if isinstance(source, dict):
            urls.append(source.get("url"))
    return _unique_urls(urls)[:SOURCE_PAGE_LIMIT]


def _unique_urls(urls: list[Any]) -> list[str]:
    unique: list[str] = []
    for url in urls:
        if not url:
            continue
        value = str(url).strip()
        if value and value not in unique:
            unique.append(value)
    return unique


def _discover_page_image_urls(page_urls: list[str]) -> list[str]:
    discovered: list[str] = []
    for page_url in page_urls:
        if len(discovered) >= IMAGE_CANDIDATE_LIMIT:
            break
        parsed = urllib.parse.urlparse(page_url)
        if parsed.scheme not in {"http", "https"}:
            continue
        try:
            html = _download_page(page_url)
        except RuntimeError:
            continue
        parser = _ImageLinkParser(page_url)
        try:
            parser.feed(html)
        except Exception:
            continue
        page_count = 0
        for url in parser.urls:
            if _image_url_rank(url) > 0 and url not in discovered:
                discovered.append(url)
                page_count += 1
            if page_count >= IMAGE_CANDIDATES_PER_PAGE or len(discovered) >= IMAGE_CANDIDATE_LIMIT:
                break
    return sorted(discovered, key=_image_url_rank, reverse=True)[:IMAGE_CANDIDATE_LIMIT]


def _download_page(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "CompAgent/0.1 image discovery",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
            data = response.read(MAX_PAGE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise RuntimeError(str(error)) from error
    if len(data) > MAX_PAGE_BYTES:
        raise RuntimeError("Page exceeded size limit.")
    if content_type and "html" not in content_type:
        raise RuntimeError(f"Page was not HTML: {content_type}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", errors="ignore")


def _image_url_rank(url: str) -> int:
    lowered = url.lower()
    score = 0
    if Path(urllib.parse.urlparse(lowered).path).suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        score += 5
    for token in ("hero", "main", "featured", "project", "building", "exterior", "rendering", "large", "full"):
        if token in lowered:
            score += 2
    for token in ("logo", "icon", "avatar", "sprite", "thumbnail", "thumb", "small", "1x1"):
        if token in lowered:
            score -= 4
    return score


def _download_image(url: str, base_path: Path) -> Path:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "CompAgent/0.1 image downloader",
            "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
            data = response.read(MAX_IMAGE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise RuntimeError(str(error)) from error
    if len(data) > MAX_IMAGE_BYTES:
        raise RuntimeError("Image exceeded size limit.")
    kind = imghdr.what(None, data)
    if kind == "jpeg":
        suffix = ".jpg"
    elif kind == "png":
        suffix = ".png"
    elif kind == "webp":
        suffix = ".webp"
    elif content_type == "image/webp" or Path(urllib.parse.urlparse(url).path).suffix.lower() == ".webp":
        suffix = ".webp"
    elif content_type == "image/jpeg" or content_type == "image/jpg":
        suffix = ".jpg"
    elif content_type == "image/png":
        suffix = ".png"
    else:
        raise RuntimeError(f"Downloaded file was not a supported image: {content_type or 'unknown content type'}, detected: {kind}")
    if suffix == ".webp":
        path = base_path.with_suffix(".jpg")
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.convert("RGB").save(path, format="JPEG", quality=90)
        except Exception as error:
            raise RuntimeError(f"Could not convert WebP image: {error}") from error
        return path
    path = base_path.with_suffix(suffix)
    path.write_bytes(data)
    return path


def _validate_image_quality(image_path: Path) -> bool:
    """Basic image quality validation to ensure usable hero images."""
    try:
        with Image.open(image_path) as img:
            width, height = img.size
            
            # Minimum dimensions for hero images
            if width < 400 or height < 300:
                return False
                
            # Minimum aspect ratio checks (not too narrow)
            aspect_ratio = width / height
            if aspect_ratio < 0.3 or aspect_ratio > 5.0:
                return False
                
            # File size check (not too small, likely thumbnail)
            if image_path.stat().st_size < 50 * 1024:  # 50KB minimum
                return False
                
        return True
    except Exception:
        return False

