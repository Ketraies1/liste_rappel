"""State persistence for previously seen entries and alerts."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

from .parser import Entry

LOGGER = logging.getLogger(__name__)


@dataclass
class EntryState:
    rank: int
    page: int
    shift: Optional[str]
    date: Optional[str]
    raw: str
    updated_at: str


@dataclass
class AlertState:
    sent_at: str


@dataclass
class State:
    entries: Dict[str, Dict[str, EntryState]] = field(default_factory=dict)
    alerts: Dict[str, AlertState] = field(default_factory=dict)

    def best_previous(self, label: str, target: str) -> Optional[EntryState]:
        return self.entries.get(label, {}).get(target)

    def record_entry(self, entry: Entry) -> None:
        label_entries = self.entries.setdefault(entry.label, {})
        label_entries[entry.target] = EntryState(
            rank=entry.rank,
            page=entry.page,
            shift=entry.shift,
            date=entry.date,
            raw=entry.raw,
            updated_at=datetime.utcnow().isoformat(),
        )

    def should_alert(self, key: str, cooldown_minutes: int) -> bool:
        if key not in self.alerts:
            return True
        sent = datetime.fromisoformat(self.alerts[key].sent_at)
        return datetime.utcnow() - sent >= timedelta(minutes=cooldown_minutes)

    def record_alert(self, key: str) -> None:
        self.alerts[key] = AlertState(sent_at=datetime.utcnow().isoformat())


def load_state(path: Path) -> State:
    if not path.exists():
        return State()
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except json.JSONDecodeError:
        LOGGER.warning("State file %s is corrupted; starting fresh", path)
        return State()

    entries_payload = payload.get("entries", {})
    alerts_payload = payload.get("alerts", {})

    entries: Dict[str, Dict[str, EntryState]] = {}
    for label, targets in entries_payload.items():
        entries[label] = {}
        for target, values in targets.items():
            entries[label][target] = EntryState(
                rank=values.get("rank", 9999),
                page=values.get("page", 9999),
                shift=values.get("shift"),
                date=values.get("date"),
                raw=values.get("raw", ""),
                updated_at=values.get("updated_at", datetime.utcnow().isoformat()),
            )

    alerts: Dict[str, AlertState] = {}
    for key, values in alerts_payload.items():
        alerts[key] = AlertState(sent_at=values.get("sent_at", datetime.utcnow().isoformat()))

    return State(entries=entries, alerts=alerts)


def save_state(path: Path, state: State) -> None:
    payload = {
        "entries": {
            label: {
                target: entry.__dict__
                for target, entry in targets.items()
            }
            for label, targets in state.entries.items()
        },
        "alerts": {key: alert.__dict__ for key, alert in state.alerts.items()},
    }

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    tmp_path.replace(path)
