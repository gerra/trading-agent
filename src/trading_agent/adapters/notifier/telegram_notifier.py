"""Telegram Bot API notifier. Never raises: a failed notification is logged, not fatal."""

from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger(__name__)
TELEGRAM_MAX = 4000


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, timeout_s: float = 15.0) -> None:
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id
        self._timeout = timeout_s

    def send(self, text: str) -> None:
        for chunk in _chunks(text, TELEGRAM_MAX):
            try:
                r = httpx.post(
                    self._url,
                    json={
                        "chat_id": self._chat_id,
                        "text": chunk,
                        "disable_web_page_preview": True,
                    },
                    timeout=self._timeout,
                )
                if r.status_code != 200:
                    log.error("telegram_send_failed", status=r.status_code, body=r.text[:200])
            except Exception as exc:  # noqa: BLE001
                log.error("telegram_send_error", error=str(exc))


class ConsoleNotifier:
    def send(self, text: str) -> None:
        print("\n=== NOTIFICATION ===\n" + text + "\n====================\n", flush=True)


class NullNotifier:
    def send(self, text: str) -> None:
        return None


def _chunks(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > size:
            out.append(cur)
            cur = ""
        cur += line
    if cur:
        out.append(cur)
    return out
