"""Parsing helpers for PDF/HTML content."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Iterable, List, Optional

from bs4 import BeautifulSoup
from pdfminer.high_level import extract_text

LOGGER = logging.getLogger(__name__)


MATRICULE_REGEX = re.compile(r"\b([A-Z]\d{4})\b")
RANK_REGEX = re.compile(r"\b(\d{1,3})\b")
DATE_REGEX = re.compile(r"\b((\d{4}-\d{2}-\d{2})|(\d{1,2}/\d{1,2}/\d{4}))\b")
SHIFT_REGEX = re.compile(r"(\d{1,2}[:h]\d{2})\s*[-à]\s*(\d{1,2}[:h]\d{2})")


@dataclass
class Entry:
    label: str
    page: int
    rank: int
    target: str
    shift: Optional[str]
    date: Optional[str]
    raw: str


def extract_lines(result_content: bytes, content_type: str) -> List[str]:
    if content_type == "application/pdf":
        try:
            text = extract_text(BytesIO(result_content))
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Failed to extract PDF text: %s", exc)
            return []
        if not text.strip():
            LOGGER.warning("PDF had no extractable text")
        return text.splitlines()

    if content_type in {"text/html", "application/xhtml+xml"}:
        soup = BeautifulSoup(result_content, "lxml")
        return [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]

    LOGGER.warning("Unknown content-type %s; treating as binary", content_type)
    return result_content.decode("utf-8", errors="ignore").splitlines()


def parse_entries(lines: Iterable[str], label: str, page: int, targets: Iterable[str]) -> List[Entry]:
    targets_set = {target.upper() for target in targets}
    entries: List[Entry] = []
    for raw_line in lines:
        line = " ".join(raw_line.split())
        matches = MATRICULE_REGEX.findall(line)
        matched_targets = [match for match in matches if match.upper() in targets_set]
        if not matched_targets:
            continue

        rank = _extract_rank(line)
        shift = _extract_shift(line)
        date = _extract_date(line)
        for target in matched_targets:
            entries.append(Entry(label=label, page=page, rank=rank, target=target.upper(), shift=shift, date=date, raw=line))
    return entries


def _extract_rank(line: str) -> int:
    ranks = RANK_REGEX.findall(line)
    for rank in ranks:
        try:
            value = int(rank)
            if value > 0:
                return value
        except ValueError:
            continue
    return 9999


def _extract_shift(line: str) -> Optional[str]:
    match = SHIFT_REGEX.search(line)
    if not match:
        return None
    start, end = match.groups()
    return f"{_normalise_time(start)}-{_normalise_time(end)}"


def _normalise_time(value: str) -> str:
    parts = value.replace("h", ":").split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return f"{hour:02d}:{minute:02d}"


def _extract_date(line: str) -> Optional[str]:
    match = DATE_REGEX.search(line)
    if not match:
        return None
    value = match.group(1)
    if "-" in value:
        return value
    return _normalise_date(value)


def _normalise_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        try:
            return datetime.strptime(value, "%Y/%m/%d").strftime("%Y-%m-%d")
        except ValueError:
            LOGGER.debug("Unable to normalise date %s", value)
            return value
