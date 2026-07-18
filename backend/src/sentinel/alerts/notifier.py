"""Multi-channel alert notifier.

Channels activate purely from config: the log channel is always on; ntfy,
Telegram, and Slack turn on when their credentials are present. Delivery is
best-effort — a channel failure never breaks the trading loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from sentinel.config import Settings
from sentinel.logging_config import get_logger

log = get_logger("sentinel.alerts")


@dataclass
class Alert:
    kind: str  # signal | fill | breaker
    title: str
    message: str
    level: str = "info"  # info | warning | critical


class Channel(Protocol):
    name: str

    def send(self, alert: Alert) -> None: ...


class LogChannel:
    name = "log"

    def send(self, alert: Alert) -> None:
        fn = log.warning if alert.level in ("warning", "critical") else log.info
        fn("ALERT[%s] %s — %s", alert.kind, alert.title, alert.message)


class NtfyChannel:
    name = "ntfy"

    def __init__(self, server: str, topic: str):
        self._url = f"{server.rstrip('/')}/{topic}"

    def send(self, alert: Alert) -> None:
        prio = {"info": "default", "warning": "high", "critical": "urgent"}.get(
            alert.level, "default"
        )
        tags = {"signal": "chart_with_upwards_trend", "fill": "moneybag",
                "breaker": "rotating_light"}.get(alert.kind, "bell")
        httpx.post(
            self._url,
            data=alert.message.encode("utf-8"),
            headers={"Title": alert.title, "Priority": prio, "Tags": tags},
            timeout=8,
        )


class TelegramChannel:
    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str):
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id

    def send(self, alert: Alert) -> None:
        httpx.post(
            self._url,
            json={"chat_id": self._chat_id, "text": f"*{alert.title}*\n{alert.message}",
                  "parse_mode": "Markdown"},
            timeout=8,
        )


class SlackChannel:
    name = "slack"

    def __init__(self, webhook_url: str):
        self._url = webhook_url

    def send(self, alert: Alert) -> None:
        httpx.post(self._url, json={"text": f"*{alert.title}*\n{alert.message}"}, timeout=8)


class Notifier:
    def __init__(self, channels: list[Channel]):
        self.channels = channels

    def send(self, alert: Alert) -> None:
        for ch in self.channels:
            try:
                ch.send(alert)
            except Exception as exc:  # noqa: BLE001 - alerting is best-effort
                log.warning("alert channel %s failed: %s", ch.name, exc)

    # Convenience helpers.
    def signal(self, symbol: str, message: str) -> None:
        self.send(Alert("signal", f"Signal · {symbol}", message))

    def fill(self, symbol: str, message: str) -> None:
        self.send(Alert("fill", f"Fill · {symbol}", message))

    def breaker(self, message: str) -> None:
        self.send(Alert("breaker", "⚠ Circuit breaker", message, level="critical"))


def make_notifier(settings: Settings) -> Notifier:
    channels: list[Channel] = [LogChannel()]
    if settings.ntfy_topic:
        channels.append(NtfyChannel(settings.ntfy_server, settings.ntfy_topic))
    if settings.telegram_bot_token and settings.telegram_chat_id:
        channels.append(TelegramChannel(settings.telegram_bot_token, settings.telegram_chat_id))
    if settings.slack_webhook_url:
        channels.append(SlackChannel(settings.slack_webhook_url))
    return Notifier(channels)
