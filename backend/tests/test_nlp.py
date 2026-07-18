"""NLP tests: lexicon sentiment + rule-based event classification (no model download)."""

from __future__ import annotations

from sentinel.nlp.events import RuleEventClassifier
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
