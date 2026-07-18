"""NLP tests: lexicon sentiment + rule-based event classification (no model download)."""

from __future__ import annotations

from types import SimpleNamespace

from sentinel.nlp import events as events_mod
from sentinel.nlp.events import OllamaEventClassifier, RuleEventClassifier
from sentinel.nlp.sentiment import LexiconScorer


def test_lexicon_positive():
    r = LexiconScorer().score("Company beats earnings, stock surges to record")
    assert r.score > 0 and r.label == "positive" and r.confidence > 0


def test_lexicon_negative():
    r = LexiconScorer().score("Shares plunge after downgrade and lawsuit")
    assert r.score < 0 and r.label == "negative"


def test_lexicon_neutral():
    r = LexiconScorer().score("The company held its annual meeting today")
    assert r.score == 0.0 and r.label == "neutral" and r.confidence == 0.0


def test_rule_events_earnings():
    e = RuleEventClassifier().classify("Q3 earnings beat estimates", "revenue up")
    assert e.event_type == "earnings"
    assert e.materiality >= 3
    assert e.direction > 0  # "beat" is bullish


def test_rule_events_mna_high_materiality():
    e = RuleEventClassifier().classify("Company to acquire rival in $10B merger")
    assert e.event_type == "m&a"
    assert e.materiality == 5


def test_rule_events_litigation_negative():
    e = RuleEventClassifier().classify("Firm hit with antitrust lawsuit and fine")
    assert e.event_type == "litigation"
    assert e.direction < 0


def test_rule_events_analyst():
    e = RuleEventClassifier().classify("Analyst downgrades stock, cuts price target")
    assert e.event_type == "analyst"
    assert e.direction < 0


def _ollama_settings():
    return SimpleNamespace(ollama_base_url="http://localhost:11434", ollama_model="qwen2.5:7b")


def test_ollama_classifier_parses_json(monkeypatch):
    def fake_post(url, **kw):
        content = '{"event_type":"m&a","materiality":5,"direction":0.8,"novelty":0.9}'
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"message": {"content": content}},
        )

    clf = OllamaEventClassifier(_ollama_settings())
    monkeypatch.setattr(clf._client, "post", fake_post)
    e = clf.classify("Company to acquire rival", "big deal")
    assert e.event_type == "m&a" and e.materiality == 5 and e.direction == 0.8


def test_ollama_classifier_falls_back_on_error(monkeypatch):
    clf = OllamaEventClassifier(_ollama_settings())
    monkeypatch.setattr(clf._client, "post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    e = clf.classify("Q3 earnings beat estimates")  # falls back to rules
    assert e.event_type == "earnings"  # rule-based result


def test_make_event_classifier_prefers_ollama():
    s = SimpleNamespace(
        ollama_model="qwen2.5:7b", ollama_base_url="http://localhost:11434",
        has_ollama=True, has_anthropic_key=False,
    )
    assert isinstance(events_mod.make_event_classifier(s), OllamaEventClassifier)
