"""Configuration loader for the liste_rappel watcher."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from dotenv import dotenv_values

LOGGER = logging.getLogger(__name__)


def _split_csv(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class Credentials:
    username: str
    password: str
    extra: Dict[str, str] = field(default_factory=dict)


@dataclass
class LoginConfig:
    url: str
    credentials: Credentials
    enabled: bool = True


@dataclass
class DiscordConfig:
    webhook_url: str
    ping: str = ""
    top_template: str = "{ping} 🔥 {label} #{rank} ({shift}) le {date} — {target}"
    warn_template: str = "{ping} ⚠️ {label} #{rank} ({shift}) le {date} — {target}"
    update_template: str = "{ping} ℹ️ {label} #{rank} ({shift}) le {date} — {target}"


@dataclass
class WatchConfig:
    list_urls: List[str]
    list_labels: List[str]
    target_ids: List[str]
    page_limit: int = 20
    sleep_seconds: int = 300
    cooldown_minutes: int = 30
    top_threshold: int = 6
    warn_threshold: int = 10
    state_file: Path = Path("state.json")
    log_file: Path = Path("watcher.log")
    connect_timeout: int = 15
    read_timeout: int = 90
    max_retries: int = 3
    retry_backoff: float = 2.0

    @property
    def paired_labels(self) -> Iterable[tuple[str, str]]:
        for index, url in enumerate(self.list_urls):
            if index < len(self.list_labels):
                yield url, self.list_labels[index]
            else:
                yield url, f"Liste {index}"


@dataclass
class AppConfig:
    login_primary: Optional[LoginConfig]
    login_secondary: Optional[LoginConfig]
    discord: Optional[DiscordConfig]
    watch: WatchConfig


def _load_credentials(prefix: str, data: Dict[str, str]) -> Optional[LoginConfig]:
    url_key = f"{prefix}_URL"
    user_key = f"{prefix}_USERNAME"
    pass_key = f"{prefix}_PASSWORD"
    enabled_key = f"{prefix}_ENABLED"

    url = data.get(url_key)
    username = data.get(user_key)
    password = data.get(pass_key)

    if not url or not username or not password:
        return None

    enabled = data.get(enabled_key, "true").lower() not in {"0", "false", "no"}

    extras: Dict[str, str] = {}
    prefix_extra = f"{prefix}_EXTRA_"
    for key, value in data.items():
        if key.startswith(prefix_extra):
            extras[key[len(prefix_extra) :]] = value

    return LoginConfig(
        url=url,
        credentials=Credentials(username=username, password=password, extra=extras),
        enabled=enabled,
    )


def load_config(path: Optional[Path] = None) -> AppConfig:
    """Load configuration from a .env file."""
    env_path = path or Path(".env")
    data = dotenv_values(str(env_path))

    list_urls = _split_csv(data.get("LIST_URLS"))
    if not list_urls:
        raise ValueError("LIST_URLS must contain at least one URL")

    list_labels = _split_csv(data.get("LIST_LABELS"))
    target_ids = _split_csv(data.get("TARGET_IDS"))
    if not target_ids:
        raise ValueError("TARGET_IDS must contain at least one matricule")

    discord_webhook = data.get("DISCORD_WEBHOOK")
    discord = None
    if discord_webhook:
        discord = DiscordConfig(
            webhook_url=discord_webhook,
            ping=data.get("DISCORD_PING", ""),
            top_template=data.get("DISCORD_TEMPLATE_TOP", DiscordConfig.top_template),
            warn_template=data.get("DISCORD_TEMPLATE_WARN", DiscordConfig.warn_template),
            update_template=data.get("DISCORD_TEMPLATE_UPDATE", DiscordConfig.update_template),
        )

    sleep_seconds_raw = int(data.get("INTERVAL_SECONDS", 300))
    if sleep_seconds_raw < 60:
        LOGGER.warning(
            "INTERVAL_SECONDS=%s is too low; enforcing minimum of 60 seconds", sleep_seconds_raw
        )
    sleep_seconds = max(sleep_seconds_raw, 60)

    watch = WatchConfig(
        list_urls=list_urls,
        list_labels=list_labels,
        target_ids=target_ids,
        page_limit=int(data.get("PAGE_LIMIT", 20)),
        sleep_seconds=sleep_seconds,
        cooldown_minutes=int(data.get("COOLDOWN_MINUTES", 30)),
        top_threshold=int(data.get("TOP_THRESHOLD", 6)),
        warn_threshold=int(data.get("WARN_THRESHOLD", 10)),
        state_file=Path(data.get("STATE_FILE", "state.json")),
        log_file=Path(data.get("LOG_FILE", "watcher.log")),
        connect_timeout=int(data.get("CONNECT_TIMEOUT", 15)),
        read_timeout=int(data.get("READ_TIMEOUT", 90)),
        max_retries=int(data.get("MAX_RETRIES", 3)),
        retry_backoff=float(data.get("RETRY_BACKOFF", 2.0)),
    )

    login_primary = _load_credentials("INTRANET1", data)
    login_secondary = _load_credentials("INTRANET2", data)

    return AppConfig(
        login_primary=login_primary,
        login_secondary=login_secondary,
        discord=discord,
        watch=watch,
    )
