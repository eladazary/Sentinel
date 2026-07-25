"""The trading cycle — signals → risk gate → broker → decision log.

Runs one recompute cycle over the watchlist. The same signal engine and risk
manager used by the backtester decide here, so backtest and live share one code
path. Every decision (fired or skipped) is written to the immutable decision log
(spec §7). Phase 1 is long-only; TRIM/SELL both fully exit (partial trims are a
Phase 2 refinement).
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sentinel.config import Settings, Watchlist
from sentinel.execution import decision_log as dlog
from sentinel.execution.broker import Broker
from sentinel.execution.scheduler import in_entry_window
from sentinel.features.dataset import build_symbol_features, load_bars
from sentinel.features.engineering import FEATURE_COLUMNS
from sentinel.logging_config import get_logger
from sentinel.model.technical import TechnicalModel
from sentinel.models import EquitySnapshot
from sentinel.news.earnings import is_in_blackout
from sentinel.repositories import get_latest_prices, get_sentiment_cache
from sentinel.risk.breakers import check_breakers, validate_order
from sentinel.risk.manager import size_position
from sentinel.risk.profile import risk_profile
from sentinel.signals.engine import SubScore, derive_signal, fuse
from sentinel.signals.technical_signal import technical_subscore
from sentinel.system_state import ensure_dry_run_started, record_breaker_event

log = get_logger("sentinel.execution.loop")

SessionFactory = Callable[[], AbstractContextManager[Session]]


@dataclass
class CycleReport:
    ran_at: datetime
    mode: str
    equity: float
    actions: list[dict] = field(default_factory=list)
    breaker_tripped: bool = False

    def add(self, symbol: str, action: str, reason: str) -> None:
        self.actions.append({"symbol": symbol, "action": action, "reason": reason})


def _day_start_and_hwm(
    session: Session, now: datetime, current_equity: float
) -> tuple[float, float]:
    """Earliest equity today (UTC date) and the all-time high-water mark."""
    day = now.astimezone(timezone.utc).date()
    first_today = session.execute(
        select(EquitySnapshot.equity)
        .where(func.date(EquitySnapshot.ts) == day)
        .order_by(EquitySnapshot.ts.asc())
        .limit(1)
    ).scalar_one_or_none()
    hwm = session.execute(select(func.max(EquitySnapshot.equity))).scalar_one_or_none()
    day_start = float(first_today) if first_today is not None else current_equity
    high_water = max(float(hwm) if hwm is not None else current_equity, current_equity)
    return day_start, high_water


def _mark_to_market(
    session_factory: SessionFactory, watchlist: Watchlist, broker: Broker
) -> None:
    """Feed the latest prices to a broker that marks its own book.

    Only the sim needs this — Alpaca marks positions itself. Without it the sim
    values every holding at its entry price forever, so equity never moves and
    no stop or target ever triggers.
    """
    mark = getattr(broker, "mark", None)
    if mark is None:
        return
    try:
        with session_factory() as session:
            prices = {
                symbol: float(row.price)
                for symbol, row in get_latest_prices(session, watchlist.symbols).items()
            }
        if prices:
            for symbol in mark(prices):
                log.info("sim bracket triggered, closed %s", symbol)
    except Exception:  # noqa: BLE001 - marking is best-effort, never fail a cycle
        log.warning("could not mark the sim to market", exc_info=True)


def run_cycle(
    *,
    session_factory: SessionFactory,
    settings: Settings,
    watchlist: Watchlist,
    broker: Broker,
    model: TechnicalModel | None,
    now: datetime | None = None,
    risk_factor: int | None = None,
    enforce_entry_window: bool = True,
    notifier=None,
) -> CycleReport:
    now = now or datetime.now(timezone.utc)
    if risk_factor is None:
        from sentinel.state import get_risk_factor

        risk_factor = get_risk_factor(settings.default_risk_factor)
    profile = risk_profile(risk_factor)
    gate = profile.min_conviction

    _mark_to_market(session_factory, watchlist, broker)
    account = broker.get_account()
    positions = broker.get_positions()
    open_keys = set(broker.open_order_keys())
    report = CycleReport(ran_at=now, mode=settings.mode, equity=account.equity)

    invested = sum(p.market_value for p in positions.values())
    exposure_pct = (invested / account.equity * 100.0) if account.equity > 0 else 0.0

    with session_factory() as session:
        ensure_dry_run_started(session, now)  # start the dry-run clock on first cycle
        # --- hard breakers first ---
        day_start, hwm = _day_start_and_hwm(session, now, account.equity)
        br = check_breakers(
            equity=account.equity,
            day_start_equity=day_start,
            high_water_mark=hwm,
            daily_loss_pct=settings.daily_loss_breaker_pct,
            max_drawdown_pct=settings.max_drawdown_breaker_pct,
        )
        if br.any_tripped:
            broker.cancel_all_orders()
            broker.close_all_positions()
            reason = (
                f"breaker: day {br.day_pnl_pct:.2f}% / dd {br.drawdown_pct:.2f}% "
                f"→ {','.join(br.actions)}"
            )
            dlog.log_decision(
                session, ts=now, symbol="*", action="BREAKER", signal="PASS",
                conviction=0.0, confidence=0.0, risk_factor=profile.risk_factor,
                mode=settings.mode, reason=reason, drivers=[],
            )
            record_breaker_event(
                session, ts=now,
                kind="drawdown" if br.drawdown_tripped else "daily_loss",
                detail=reason, day_pnl_pct=br.day_pnl_pct, drawdown_pct=br.drawdown_pct,
            )
            dlog.record_equity(
                session, ts=now, equity=account.equity, cash=account.cash,
                exposure_pct=0.0, mode=settings.mode,
            )
            report.breaker_tripped = True
            report.add("*", "BREAKER", reason)
            log.warning("breaker tripped: %s", reason)
            if notifier is not None:
                notifier.breaker(reason)
            return report

        sentiment = get_sentiment_cache(session, watchlist.symbols)
        new_today = 0
        for t in watchlist.tickers:
            symbol = t.symbol
            has_position = symbol in positions

            if model is None:
                dlog.log_decision(
                    session, ts=now, symbol=symbol, action="SKIP", signal="PASS",
                    conviction=0.0, confidence=0.0, risk_factor=profile.risk_factor,
                    mode=settings.mode, reason="no trained model", drivers=[],
                )
                report.add(symbol, "SKIP", "no trained model")
                continue

            feats = build_symbol_features(session, watchlist, symbol, settings)
            required = [c for c in FEATURE_COLUMNS if c != "rs_sector_20"]
            feats = feats.dropna(subset=required)
            if feats.empty:
                dlog.log_decision(
                    session, ts=now, symbol=symbol, action="SKIP", signal="PASS",
                    conviction=0.0, confidence=0.0, risk_factor=profile.risk_factor,
                    mode=settings.mode, reason="insufficient data for features",
                    drivers=[],
                )
                report.add(symbol, "SKIP", "insufficient data")
                continue

            row = feats.iloc[-1]
            subs = [technical_subscore(model, row, settings.weight_technical)]
            sc = sentiment.get(symbol)
            news_score = social_score = None
            if sc is not None:
                if sc.news_score is not None:
                    news_score = sc.news_score
                    subs.append(SubScore(
                        "news", sc.news_score, sc.news_confidence or 0.0,
                        settings.weight_news, list(sc.news_drivers or []),
                    ))
                if sc.social_score is not None:
                    social_score = sc.social_score
                    subs.append(SubScore(
                        "social", sc.social_score, sc.social_confidence or 0.0,
                        settings.weight_social, list(sc.social_drivers or []),
                    ))
            conv = fuse(subs)
            tech_sub = subs[0]
            signal = derive_signal(conv.conviction, gate, has_position)

            dlog.upsert_signal_snapshot(
                session, symbol=symbol, ts=now, conviction=conv.conviction,
                confidence=conv.confidence, technical_score=tech_sub.score,
                news_score=news_score, social_score=social_score,
                signal=signal, drivers=conv.drivers,
                model_version=model.trained_through,
            )

            bars = load_bars(session, symbol)
            last_price = float(bars["close"].iloc[-1]) if not bars.empty else None

            common = dict(
                conviction=conv.conviction, confidence=conv.confidence,
                risk_factor=profile.risk_factor, mode=settings.mode,
                drivers=conv.drivers, features={k: _safe(row[k]) for k in FEATURE_COLUMNS},
            )

            # ---- manage existing position ----
            if has_position:
                if signal in ("SELL", "TRIM"):
                    res = broker.close_position(symbol)
                    oid = res.id if res else None
                    dlog.log_decision(
                        session, ts=now, symbol=symbol, action="EXIT", signal=signal,
                        reason=f"{signal} at conviction {conv.conviction:.0f}",
                        broker_order_id=oid, **common,
                    )
                    report.add(symbol, "EXIT", signal)
                    if notifier is not None:
                        notifier.fill(symbol, f"{signal} — closed at conviction {conv.conviction:.0f}")
                else:
                    dlog.log_decision(
                        session, ts=now, symbol=symbol, action="HOLD", signal=signal,
                        reason=f"holding at conviction {conv.conviction:.0f}", **common,
                    )
                    report.add(symbol, "HOLD", "position held")
                continue

            # ---- consider a new entry ----
            if signal != "BUY":
                dlog.log_decision(
                    session, ts=now, symbol=symbol, action="SKIP", signal=signal,
                    reason=f"conviction {conv.conviction:.0f} below gate {gate:.0f}",
                    **common,
                )
                report.add(symbol, "SKIP", "below gate")
                continue
            if enforce_entry_window and not in_entry_window(now):
                dlog.log_decision(
                    session, ts=now, symbol=symbol, action="SKIP", signal=signal,
                    reason="outside 10:00–15:30 ET entry window", **common,
                )
                report.add(symbol, "SKIP", "outside entry window")
                continue
            if new_today >= profile.max_new_positions_per_day:
                dlog.log_decision(
                    session, ts=now, symbol=symbol, action="SKIP", signal=signal,
                    reason=f"daily new-position cap ({profile.max_new_positions_per_day}) reached",
                    **common,
                )
                report.add(symbol, "SKIP", "daily cap reached")
                continue
            if last_price is None:
                report.add(symbol, "SKIP", "no price")
                continue

            # ---- earnings blackout (spec §6) ----
            blackout = is_in_blackout(session, symbol, now, settings.earnings_blackout_hours)
            earnings_size_mult = 1.0
            if blackout:
                policy = profile.trade_around_earnings
                if policy == "never":
                    dlog.log_decision(
                        session, ts=now, symbol=symbol, action="SKIP", signal=signal,
                        reason="earnings blackout (risk profile: never trade around earnings)",
                        **common,
                    )
                    report.add(symbol, "SKIP", "earnings blackout")
                    continue
                if policy == "reduced":
                    earnings_size_mult = 0.5

            atr = float(row["atr_pct"]) * last_price
            sizing = size_position(
                equity=account.equity * earnings_size_mult, price=last_price, atr=atr,
                profile=profile, current_exposure_value=invested,
            )
            if sizing.blocked:
                dlog.log_decision(
                    session, ts=now, symbol=symbol, action="SKIP", signal=signal,
                    reason=f"sizing blocked: {sizing.reason}",
                    sizing=_sizing_dict(sizing), **common,
                )
                report.add(symbol, "SKIP", sizing.reason)
                continue

            limit_price = round(last_price * 1.001, 2)  # marketable buy offset
            chk = validate_order(
                symbol=symbol, side="buy", qty=sizing.shares, limit_price=limit_price,
                last_price=last_price, equity=account.equity, pending_keys=open_keys,
            )
            if not chk.ok:
                dlog.log_decision(
                    session, ts=now, symbol=symbol, action="SKIP", signal=signal,
                    reason=f"order check failed: {chk.reason}",
                    sizing=_sizing_dict(sizing), **common,
                )
                report.add(symbol, "SKIP", chk.reason)
                continue

            try:
                res = broker.submit_bracket(
                    symbol, sizing.shares, limit_price,
                    sizing.stop_price, sizing.take_profit,
                )
            except Exception as exc:  # noqa: BLE001 - broker/network errors
                dlog.log_decision(
                    session, ts=now, symbol=symbol, action="ERROR", signal=signal,
                    reason=f"order submit failed: {exc}",
                    sizing=_sizing_dict(sizing), **common,
                )
                report.add(symbol, "ERROR", str(exc))
                continue

            open_keys.add((symbol, "buy"))
            invested += sizing.notional
            new_today += 1
            dlog.log_decision(
                session, ts=now, symbol=symbol, action="OPEN", signal=signal,
                reason=(f"BUY {sizing.shares}@~{limit_price} · stop {sizing.stop_price} "
                        f"· tp {sizing.take_profit}"),
                sizing=_sizing_dict(sizing), broker_order_id=res.id, **common,
            )
            report.add(symbol, "OPEN", f"{sizing.shares} shares")
            if notifier is not None:
                notifier.fill(
                    symbol,
                    f"BUY {sizing.shares}@~{limit_price} · stop {sizing.stop_price} "
                    f"· tp {sizing.take_profit}",
                )

        dlog.record_equity(
            session, ts=now, equity=account.equity, cash=account.cash,
            exposure_pct=exposure_pct, mode=settings.mode,
        )

    return report


def _safe(v) -> float | None:
    try:
        f = float(v)
        return None if f != f else round(f, 6)  # NaN -> None
    except (TypeError, ValueError):
        return None


def _sizing_dict(s) -> dict:
    return {
        "shares": s.shares,
        "notional": s.notional,
        "stop": s.stop_price,
        "take_profit": s.take_profit,
    }
