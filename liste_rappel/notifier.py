"""Discord notification helpers."""
from __future__ import annotations

import json
import logging
from typing import Optional

import requests

from .config import DiscordConfig

LOGGER = logging.getLogger(__name__)


class DiscordNotifier:
    def __init__(self, config: Optional[DiscordConfig]) -> None:
        self._config = config

    @property
    def enabled(self) -> bool:
        return self._config is not None

    def send(self, content: str) -> None:
        if not self.enabled or not self._config:
            LOGGER.debug("Discord notifier disabled; skipping message: %s", content)
            return
        payload = {"content": content}
        response = requests.post(self._config.webhook_url, data=json.dumps(payload), headers={"Content-Type": "application/json"}, timeout=15)
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            LOGGER.error("Failed to send Discord notification: %s - %s", exc, response.text)
            raise

    def format_message(self, template: str, **context: str) -> str:
        if not self._config:
            return template.format(**context)
        ping = self._config.ping.strip()
        context.setdefault("ping", ping)
        return template.format(**context)
