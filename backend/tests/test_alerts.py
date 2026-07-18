"""Alert notifier: channel selection, dispatch, failure isolation."""

from __future__ import annotations

from sentinel.alerts import notifier as N
from sentinel.config import Settings


def test_log_channel_always_present():
    n = N.make_notifier(Settings())
    assert [c.name for c in n.channels] == ["log"]


def test_channels_activate_from_config():
    s = Settings(
        ntfy_topic="sentinel-test",
        telegram_bot_token="tok", telegram_chat_id="123",
        slack_webhook_url="https://hooks.slack.com/x",
    )
    names = {c.name for c in N.make_notifier(s).channels}
    assert names == {"log", "ntfy", "telegram", "slack"}


def test_telegram_needs_both_token_and_chat():
    s = Settings(telegram_bot_token="tok")  # missing chat id
    assert "telegram" not in {c.name for c in N.make_notifier(s).channels}


class _Recorder:
    name = "rec"

    def __init__(self):
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


class _Boom:
    name = "boom"

    def send(self, alert):
        raise RuntimeError("channel down")


def test_dispatch_and_failure_isolation():
    rec = _Recorder()
    n = N.Notifier([_Boom(), rec])  # first channel raises, second must still fire
    n.breaker("daily loss -3%")
    assert len(rec.sent) == 1
    assert rec.sent[0].kind == "breaker" and rec.sent[0].level == "critical"


def test_convenience_helpers():
    rec = _Recorder()
    n = N.Notifier([rec])
    n.signal("NVDA", "conviction 62")
    n.fill("NVDA", "BUY 22@172")
    kinds = [a.kind for a in rec.sent]
    assert kinds == ["signal", "fill"]


def test_ntfy_posts(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        N.httpx, "post",
        lambda url, **kw: calls.update(url=url, headers=kw.get("headers"), data=kw.get("data")),
    )
    N.NtfyChannel("https://ntfy.sh", "sentinel-test").send(
        N.Alert("fill", "Fill · NVDA", "BUY 22@172")
    )
    assert calls["url"] == "https://ntfy.sh/sentinel-test"
    assert calls["headers"]["Title"] == "Fill · NVDA"
