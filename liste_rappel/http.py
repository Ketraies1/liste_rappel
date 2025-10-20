"""HTTP helpers for interacting with CTAQ intranet portals."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional

import requests
from bs4 import BeautifulSoup

from .config import Credentials, LoginConfig, WatchConfig

LOGGER = logging.getLogger(__name__)


@dataclass
class FetchResult:
    url: str
    status_code: int
    content: bytes
    headers: Dict[str, str]

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";")[0].strip().lower()


class WatcherSession:
    """Wrapper around ``requests.Session`` with retry & login helpers."""

    def __init__(self, config: WatchConfig) -> None:
        self._session = requests.Session()
        self._watch = config

    @property
    def requests(self) -> requests.Session:
        return self._session

    def login(self, login_config: Optional[LoginConfig]) -> None:
        if not login_config or not login_config.enabled:
            return

        LOGGER.info("Logging into %s", login_config.url)
        response = self._session.get(login_config.url, timeout=(self._watch.connect_timeout, self._watch.read_timeout))
        response.raise_for_status()
        payload = _build_login_payload(response.text, login_config.credentials)

        login_url = response.url
        LOGGER.debug("Submitting login form to %s with fields %s", login_url, list(payload))
        post = self._session.post(
            login_url,
            data=payload,
            timeout=(self._watch.connect_timeout, self._watch.read_timeout),
            allow_redirects=True,
        )
        post.raise_for_status()

    def fetch(self, url: str) -> FetchResult:
        backoff = 1.0
        for attempt in range(1, self._watch.max_retries + 1):
            try:
                LOGGER.debug("Fetching %s (attempt %s/%s)", url, attempt, self._watch.max_retries)
                response = self._session.get(
                    url,
                    timeout=(self._watch.connect_timeout, self._watch.read_timeout),
                    allow_redirects=True,
                )
                if response.status_code >= 500 or response.status_code in {429}:
                    raise requests.HTTPError(f"{response.status_code} Server error", response=response)
                response.raise_for_status()
                return FetchResult(url=response.url, status_code=response.status_code, content=response.content, headers=response.headers)
            except (requests.Timeout, requests.ConnectionError) as exc:  # type: ignore[attr-defined]
                LOGGER.warning("Timeout fetching %s: %s", url, exc)
            except requests.HTTPError as exc:
                LOGGER.warning("HTTP error fetching %s: %s", url, exc)
                if exc.response is not None and 400 <= exc.response.status_code < 500 and exc.response.status_code not in {429}:
                    raise
            if attempt == self._watch.max_retries:
                raise
            time.sleep(backoff)
            backoff *= self._watch.retry_backoff
        raise RuntimeError("Unreachable")


def _build_login_payload(html: str, credentials: Credentials) -> Dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    forms = soup.find_all("form")
    if not forms:
        raise RuntimeError("Unable to locate login form")

    for form in forms:
        password_input = form.find("input", {"type": "password"})
        if not password_input:
            continue

        inputs = form.find_all("input")
        payload: Dict[str, str] = {}
        for element in inputs:
            name = element.get("name")
            if not name:
                continue
            value = element.get("value", "")
            payload[name] = value

        user_field = _detect_user_field(payload, credentials)
        pass_field = password_input.get("name")
        if not user_field or not pass_field:
            continue

        payload[user_field] = credentials.username
        payload[pass_field] = credentials.password
        payload.update(credentials.extra)
        return payload

    raise RuntimeError("Unable to locate login form with password field")


def _detect_user_field(payload: Dict[str, str], credentials: Credentials) -> Optional[str]:
    lower_keys = {key.lower(): key for key in payload}
    for candidate in ("username", "user", "email", "login"):
        if candidate in lower_keys:
            return lower_keys[candidate]
    # fallback: first field that is empty and not password
    for key in payload:
        if "pass" in key.lower():
            continue
        return key
    return None
