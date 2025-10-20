"""Main watcher loop for the CTAQ recall lists."""
from __future__ import annotations

import argparse
import logging
import hashlib
from logging.handlers import RotatingFileHandler
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List

from .config import AppConfig, load_config
from .http import WatcherSession
from .notifier import DiscordNotifier
from .parser import Entry, extract_lines, parse_entries
from .state import load_state, save_state

LOGGER = logging.getLogger(__name__)


class Watcher:
    def __init__(self, config: AppConfig, once: bool = False, debug: bool = False) -> None:
        self.config = config
        self.once = once
        self.debug = debug
        state_parent = self.config.watch.state_file.parent
        if state_parent and state_parent != Path('.'):
            state_parent.mkdir(parents=True, exist_ok=True)
        log_parent = self.config.watch.log_file.parent
        if log_parent and log_parent != Path('.'):
            log_parent.mkdir(parents=True, exist_ok=True)
        self.session = WatcherSession(config.watch)
        self.notifier = DiscordNotifier(config.discord)
        self.state = load_state(config.watch.state_file)

    def run(self) -> None:
        setup_logging(self.config, debug=self.debug)
        LOGGER.info("Starting watcher. once=%s, debug=%s", self.once, self.debug)
        try:
            self._login()
            while True:
                start_ts = datetime.utcnow()
                self._run_once()
                duration = (datetime.utcnow() - start_ts).total_seconds()
                LOGGER.info("Pass completed in %.2fs", duration)
                if self.once:
                    break
                sleep_time = max(self.config.watch.sleep_seconds - duration, 0)
                LOGGER.info("Sleeping for %.0fs", sleep_time)
                time.sleep(sleep_time)
        except KeyboardInterrupt:
            LOGGER.info("Interrupted by user")
        finally:
            save_state(self.config.watch.state_file, self.state)

    def _login(self) -> None:
        self.session.login(self.config.login_primary)
        self.session.login(self.config.login_secondary)

    def _run_once(self) -> None:
        LOGGER.info("Fetching lists for %s targets", len(self.config.watch.target_ids))
        new_entries: List[Entry] = []
        seen_pages: Dict[str, List[str]] = defaultdict(list)
        total_pages = 0
        for base_url, label in self.config.watch.paired_labels:
            LOGGER.info("Processing %s (%s)", label, base_url)
            page = 0
            while page < self.config.watch.page_limit:
                url = f"{base_url.rstrip('/')}/{page}"
                result = self.session.fetch(url)
                LOGGER.info(
                    "Page %s for %s -> status=%s, bytes=%s, content-type=%s",
                    page,
                    label,
                    result.status_code,
                    len(result.content),
                    result.content_type or "unknown",
                )

                previous_contents = seen_pages[label]
                content_hash = hashlib.sha256(result.content).hexdigest()
                if previous_contents and content_hash in previous_contents:
                    LOGGER.info("Page %s for %s repeated content, stopping pagination", page, label)
                    break
                previous_contents.append(content_hash)

                lines = extract_lines(result.content, result.content_type)
                LOGGER.debug("Parsed %s lines from page %s", len(lines), page)
                entries = parse_entries(lines, label=label, page=page, targets=self.config.watch.target_ids)
                LOGGER.info("Found %s matching entries on page %s", len(entries), page)
                new_entries.extend(entries)

                page += 1
                total_pages += 1

        LOGGER.info("Processed %s pages across %s lists", total_pages, len(self.config.watch.list_urls))
        LOGGER.info("Total new entries this pass: %s", len(new_entries))
        self._handle_entries(new_entries)
        save_state(self.config.watch.state_file, self.state)

    def _handle_entries(self, entries: Iterable[Entry]) -> None:
        best: Dict[tuple[str, str], Entry] = {}
        for entry in entries:
            key = (entry.label, entry.target)
            previous = best.get(key)
            if previous is None or entry.rank < previous.rank or (entry.rank == previous.rank and entry.page < previous.page):
                best[key] = entry

        for (label, target), entry in best.items():
            LOGGER.debug("Evaluating entry for %s/%s: rank=%s page=%s", label, target, entry.rank, entry.page)
            previous_state = self.state.best_previous(label, target)
            self.state.record_entry(entry)

            if not previous_state:
                LOGGER.info("New entry for %s/%s -> rank %s", label, target, entry.rank)
                self._maybe_notify(entry, "new")
                continue

            if entry.rank < previous_state.rank or entry.page < previous_state.page or entry.shift != previous_state.shift or entry.date != previous_state.date:
                LOGGER.info(
                    "Improved entry for %s/%s: was #%s (page %s), now #%s (page %s)",
                    label,
                    target,
                    previous_state.rank,
                    previous_state.page,
                    entry.rank,
                    entry.page,
                )
                self._maybe_notify(entry, "update")
            else:
                LOGGER.debug("No change for %s/%s", label, target)

    def _maybe_notify(self, entry: Entry, change_type: str) -> None:
        if not self.notifier.enabled:
            return
        threshold = None
        template = None
        if entry.rank <= self.config.watch.top_threshold:
            threshold = "top"
            template = self.config.discord.top_template if self.config.discord else None
        elif entry.rank <= self.config.watch.warn_threshold:
            threshold = "warn"
            template = self.config.discord.warn_template if self.config.discord else None
        else:
            threshold = "update"
            template = self.config.discord.update_template if self.config.discord else None

        if not template:
            LOGGER.debug("No template for change type %s", change_type)
            return

        key = f"{entry.label}|{entry.target}|{entry.date}|{entry.shift}|{threshold}"
        if not self.state.should_alert(key, self.config.watch.cooldown_minutes):
            LOGGER.info("Skipping alert for %s due to cooldown", key)
            return

        context = {
            "label": entry.label,
            "rank": entry.rank,
            "target": entry.target,
            "shift": entry.shift or "?",
            "date": entry.date or "?",
        }
        message = self.notifier.format_message(template, **context)
        LOGGER.info("Sending %s notification for %s/%s", threshold, entry.label, entry.target)
        self.notifier.send(message)
        self.state.record_alert(key)
        save_state(self.config.watch.state_file, self.state)


def setup_logging(config: AppConfig, debug: bool = False) -> None:
    if LOGGER.handlers:
        return
    log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = RotatingFileHandler(
        config.watch.log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    file_handler.setFormatter(formatter)
    logging.getLogger().addHandler(file_handler)


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch CTAQ recall lists")
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--config", default=".env", help="Path to the configuration file")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        config = load_config(Path(args.config))
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to load configuration: {exc}", file=sys.stderr)
        return 2

    watcher = Watcher(config, once=args.once, debug=args.debug)
    watcher.run()
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
