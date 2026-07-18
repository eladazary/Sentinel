import { useEffect, useMemo, useState, useCallback } from "react";
import {
  fetchWatchlist, fetchHealth, fetchRiskProfiles, fetchAccount, fetchDecisions,
  fetchLatestBacktest, fetchNews, fetchTrackers, fetchPerformance,
  setRiskFactor, killSwitch, addTracker, removeTracker,
} from "./api.js";

/* Sentinel — Phase 2 command deck. Full ensemble (technical + news + social),
   sentiment desk, news feed, and performance. Palette from the reference. */

const C = {
  bg: "#0F1622", panel: "#171F2C", panel2: "#1C2635",
  line: "rgba(134,150,170,0.16)", ink: "#E7EDF5", mut: "#8696AA",
  teal: "#3EC9A7", coral: "#F2695C", amber: "#E8A33D",
};
const POLL_MS = 15000;
const CHIP = { BUY: C.teal, SELL: C.coral, TRIM: C.coral, HOLD: C.mut, PASS: C.mut };

const fmt = (v, d = 2) =>
  v == null || Number.isNaN(v) ? "—" : Number(v).toLocaleString(undefined, {
    minimumFractionDigits: d, maximumFractionDigits: d });
const fmtMoney = (v) => (v == null ? "—" : `$${fmt(v, 0)}`);
const fmtPct = (v, d = 1) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${fmt(v, d)}%`);
const timeOf = (ts) => new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
const dateOf = (ts) => new Date(ts).toLocaleDateString([], { month: "short", day: "numeric" });

const Spark = ({ data, up }) => {
  if (!data || data.length < 2) return <div style={{ height: 34 }} />;
  const min = Math.min(...data), max = Math.max(...data);
  const pts = data.map((v, i) =>
    `${(i / (data.length - 1)) * 100},${32 - ((v - min) / (max - min || 1)) * 28}`).join(" ");
  return (
    <svg viewBox="0 0 100 34" style={{ width: "100%", height: 34 }} preserveAspectRatio="none" aria-hidden>
      <polyline points={pts} fill="none" stroke={up ? C.teal : C.coral} strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  );
};

const Chip = ({ sig }) => sig ? (
  <span className="chip" style={{ color: CHIP[sig] || C.mut, borderColor: CHIP[sig] || C.mut }}>{sig}</span>
) : null;

const Meter = ({ label, v, dim }) => (
  <div className="meter">
    <span className="meter-l">{label}</span>
    <div className="meter-track">
      <div className="meter-mid" />
      <div className="meter-fill" style={{
        left: v >= 0 ? "50%" : `${50 + v / 2}%`, width: `${Math.abs(v) / 2}%`,
        background: v >= 0 ? C.teal : C.coral, opacity: dim ? 0.4 : 1 }} />
    </div>
    <span className="meter-v" style={{ color: v >= 0 ? C.teal : C.coral, opacity: dim ? 0.5 : 1 }}>
      {v > 0 ? "+" : ""}{Math.round(v)}</span>
  </div>
);

const riskColor = (r) => (r <= 4 ? C.teal : r <= 7 ? C.amber : C.coral);

function Dial({ profiles, current, onCommit }) {
  const [risk, setRisk] = useState(current);
  useEffect(() => setRisk(current), [current]);
  const col = riskColor(risk);
  const p = profiles.find((x) => x.risk_factor === risk) || {};
  const R = 88, cx = 110, cy = 108;
  const angle = (i) => Math.PI * (1 - (i - 1) / 9);
  const pos = (i, r = R) => [cx + r * Math.cos(angle(i)), cy - r * Math.sin(angle(i))];
  const arcTo = (i) => {
    const [sx, sy] = pos(1), [ex, ey] = pos(i);
    const large = angle(1) - angle(i) > Math.PI ? 1 : 0;
    return `M ${sx} ${sy} A ${R} ${R} 0 ${large} 1 ${ex} ${ey}`;
  };
  return (
    <section className="panel">
      <div className="panel-head"><h2>Risk factor</h2>
        <span className="head-note">governs new decisions only · hard breakers always on</span></div>
      <div className="dial-body">
        <svg viewBox="0 0 220 122" className="dial-svg" role="slider" aria-valuemin={1} aria-valuemax={10} aria-valuenow={risk}>
          <path d={arcTo(10)} fill="none" stroke={C.line} strokeWidth="10" strokeLinecap="round" />
          <path d={arcTo(risk)} fill="none" stroke={col} strokeWidth="10" strokeLinecap="round" />
          {Array.from({ length: 10 }, (_, k) => k + 1).map((i) => {
            const [x, y] = pos(i);
            return (
              <g key={i} onClick={() => { setRisk(i); onCommit(i); }} style={{ cursor: "pointer" }}>
                <circle cx={x} cy={y} r="11" fill="transparent" />
                <circle cx={x} cy={y} r={i === risk ? 7 : 3.5} fill={i <= risk ? col : C.panel2}
                  stroke={i === risk ? C.ink : C.line} strokeWidth={i === risk ? 1.5 : 1} />
              </g>
            );
          })}
          <text x={cx} y={cy - 14} textAnchor="middle" className="dial-num" fill={col}>{risk}</text>
          <text x={cx} y={cy + 4} textAnchor="middle" className="dial-sub" fill={C.mut}>of 10</text>
        </svg>
        <div className="dial-contract">
          <p className="contract-title">At risk {risk}, Sentinel will:</p>
          <dl>
            <div><dt>Max position</dt><dd>{fmt(p.max_position_pct, 0)}% of equity</dd></div>
            <div><dt>Max exposure</dt><dd>{fmt(p.max_exposure_pct, 0)}%</dd></div>
            <div><dt>Conviction gate</dt><dd>≥ {fmt(p.min_conviction, 0)}</dd></div>
            <div><dt>Stop width</dt><dd>{fmt(p.stop_atr_mult, 1)}× ATR</dd></div>
            <div><dt>New trades / day</dt><dd>{p.max_new_positions_per_day}</dd></div>
            <div><dt>Around earnings</dt><dd>{p.trade_around_earnings}</dd></div>
          </dl>
          <input type="range" min="1" max="10" value={risk} style={{ accentColor: col, width: "100%", marginTop: 12 }}
            onChange={(e) => setRisk(+e.target.value)} onMouseUp={(e) => onCommit(+e.target.value)}
            onTouchEnd={(e) => onCommit(+e.target.value)} aria-label="Risk factor slider" />
        </div>
      </div>
    </section>
  );
}

function PerfChart({ perf }) {
  const pts = perf?.points || [];
  if (pts.length < 2) return <p className="empty">No equity history yet — runs once the paper loop trades.</p>;
  const eq = pts.map((p) => p.equity);
  const spy = pts.map((p) => p.spy).filter((x) => x != null);
  const all = [...eq, ...spy];
  const min = Math.min(...all), max = Math.max(...all);
  const X = (i) => (i / (pts.length - 1)) * 100;
  const Y = (v) => 60 - ((v - min) / (max - min || 1)) * 56;
  const line = (key) => pts.map((p, i) => (p[key] == null ? null : `${X(i)},${Y(p[key])}`))
    .filter(Boolean).join(" ");
  const last = pts[pts.length - 1];
  return (
    <section className="panel">
      <div className="panel-head"><h2>Performance</h2>
        <span className="head-note">paper equity vs SPY · dd {fmt(last.drawdown_pct, 1)}%</span></div>
      <svg viewBox="0 0 100 64" style={{ width: "100%", height: 160 }} preserveAspectRatio="none">
        <polyline points={line("spy")} fill="none" stroke={C.mut} strokeWidth="1" strokeDasharray="2 2" />
        <polyline points={line("equity")} fill="none" stroke={C.teal} strokeWidth="1.6" />
      </svg>
      <div className="legend">
        <span><i style={{ background: C.teal }} /> equity {fmtMoney(last.equity)}</span>
        <span><i style={{ background: C.mut }} /> SPY (normalized)</span>
      </div>
    </section>
  );
}

function SentimentDesk({ trackers, onAdd, onRemove }) {
  const [handle, setHandle] = useState("");
  const [source, setSource] = useState("stocktwits");
  const submit = (e) => { e.preventDefault(); if (handle.trim()) { onAdd(handle.trim(), source); setHandle(""); } };
  const credColor = (c) => (c >= 0.55 ? C.teal : c >= 0.45 ? C.amber : C.coral);
  return (
    <section className="panel">
      <div className="panel-head"><h2>Sentiment desk</h2>
        <span className="head-note">tracked authors, weighted by measured hit rate</span></div>
      <ul className="trk">
        {trackers.map((u) => (
          <li key={`${u.source}:${u.handle}`} className="trk-row">
            <div className="trk-head">
              <span className="trk-h">{u.pinned ? "📌 " : ""}@{u.handle}</span>
              <span className="trk-src">{u.source}</span>
              <span className="mono trk-hit" style={{ color: credColor(u.credibility) }}>
                cred {fmt(u.credibility, 2)}{u.hit_rate != null ? ` · ${fmt(u.hit_rate * 100, 0)}% of ${u.n_scored}` : " · unproven"}
              </span>
              <button className="trk-x" onClick={() => onRemove(u.source, u.handle)} aria-label="remove">×</button>
            </div>
            {u.note && <p className="trk-note">{u.note}</p>}
          </li>
        ))}
        {trackers.length === 0 && <p className="empty">No tracked accounts yet — add fintwit handles to build a credibility ledger.</p>}
      </ul>
      <form className="trk-add" onSubmit={submit}>
        <input value={handle} onChange={(e) => setHandle(e.target.value)} placeholder="handle (e.g. macro_ledger)" />
        <select value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="stocktwits">StockTwits</option>
          <option value="reddit">Reddit</option>
          <option value="x">X</option>
        </select>
        <button type="submit">+ Track</button>
      </form>
    </section>
  );
}

function BacktestCard({ bt }) {
  if (!bt) return <section className="panel"><div className="panel-head"><h2>Backtest</h2></div><p className="empty">No backtest stored — run sentinel-backtest.</p></section>;
  const m = bt.metrics || {}, spy = bt.benchmarks?.spy_buy_hold, basket = bt.benchmarks?.basket_buy_hold;
  const Row = ({ label, s, b1, b2, pct }) => (
    <tr><td className="bt-l">{label}</td>
      <td className="mono">{pct ? fmtPct(s * 100) : fmt(s)}</td>
      <td className="mono dim">{b1 == null ? "—" : pct ? fmtPct(b1 * 100) : fmt(b1)}</td>
      <td className="mono dim">{b2 == null ? "—" : pct ? fmtPct(b2 * 100) : fmt(b2)}</td></tr>
  );
  return (
    <section className="panel">
      <div className="panel-head"><h2>Backtest</h2>
        <span className="head-note">walk-forward · risk {bt.risk_factor} · {bt.n_trades} trades · AUC {bt.wf_auc ? fmt(bt.wf_auc, 3) : "n/a"}</span></div>
      <table className="bt">
        <thead><tr><th></th><th>Strategy</th><th>SPY</th><th>Basket</th></tr></thead>
        <tbody>
          <Row label="CAGR" s={m.cagr} b1={spy?.cagr} b2={basket?.cagr} pct />
          <Row label="Sharpe" s={m.sharpe} b1={spy?.sharpe} b2={basket?.sharpe} />
          <Row label="Max drawdown" s={m.max_drawdown} b1={spy?.max_drawdown} b2={basket?.max_drawdown} pct />
          <Row label="Win rate" s={m.win_rate} b1={null} b2={null} pct />
        </tbody>
      </table>
    </section>
  );
}

export default function App() {
  const [d, setD] = useState({});
  const [error, setError] = useState(null);
  const [armKill, setArmKill] = useState(false);

  const load = useCallback(async () => {
    try {
      const [wl, health, account, risk, decisions, bt, news, trackers, perf] = await Promise.all([
        fetchWatchlist(), fetchHealth(), fetchAccount(), fetchRiskProfiles(),
        fetchDecisions(40), fetchLatestBacktest(), fetchNews(30), fetchTrackers(), fetchPerformance(),
      ]);
      setD({ wl, health, account, risk, decisions: decisions || [], bt, news: news || [], trackers: trackers || [], perf });
      setError(null);
    } catch (e) { setError(e.message || "connection error"); }
  }, []);

  useEffect(() => { load(); const id = setInterval(load, POLL_MS); return () => clearInterval(id); }, [load]);

  const commitRisk = async (rf) => { await setRiskFactor(rf); load(); };
  const doKill = async () => { if (!armKill) { setArmKill(true); return; } await killSwitch(true); setArmKill(false); load(); };
  const onAddTracker = async (h, s) => { await addTracker(h, s); load(); };
  const onRemoveTracker = async (s, h) => { await removeTracker(s, h); load(); };

  const { wl, health, account, risk = { profiles: [], default_risk_factor: 5 }, decisions = [], bt, news = [], trackers = [], perf } = d;
  const mode = wl?.mode || account?.mode || "—";
  const healthy = health?.status === "ok";
  const tickers = wl?.tickers || [];
  const gate = useMemo(() => {
    const p = (risk.profiles || []).find((x) => x.risk_factor === risk.default_risk_factor);
    return p ? p.min_conviction : 50;
  }, [risk]);

  return (
    <div className="root">
      <style>{css}</style>
      <header className="top">
        <div className="brand"><span className="brand-mark" aria-hidden>◮</span>
          <div><h1>Sentinel</h1><span className="brand-sub">swing desk · phase 2 · full ensemble</span></div></div>
        <div className="top-stats">
          <div className="stat"><span className="stat-l">Paper equity</span><span className="stat-v mono">{fmtMoney(account?.equity)}</span></div>
          <div className="stat"><span className="stat-l">Exposure</span><span className="stat-v mono">{account?.exposure_pct == null ? "—" : `${fmt(account.exposure_pct, 0)}%`}</span></div>
          <span className="mode">{mode}</span>
          <span className="health" style={{ color: healthy ? C.teal : C.coral, borderColor: healthy ? C.teal : C.coral }}>{healthy ? "● ok" : "● degraded"}</span>
          <button className={"kill" + (armKill ? " armed" : "")} onClick={doKill} onBlur={() => setArmKill(false)}>
            {armKill ? "Confirm: cancel & flatten" : "Kill switch"}</button>
        </div>
      </header>

      {error && <div className="banner">Can’t reach the API — retrying every {POLL_MS / 1000}s. ({error})</div>}

      <Dial profiles={risk.profiles} current={risk.default_risk_factor} onCommit={commitRisk} />

      <section className="panel">
        <div className="panel-head"><h2>Watchlist</h2>
          <span className="head-note">conviction −100…+100 · gate ≥ {fmt(gate, 0)} · 3-model ensemble</span></div>
        <div className="grid">
          {tickers.map((s) => {
            const up = (s.change ?? 0) >= 0;
            const live = s.conviction != null && s.conviction >= gate;
            return (
              <article key={s.symbol} className={"card" + (live ? " card-live" : "")}>
                <div className="card-top">
                  <div><span className="tkr">{s.symbol}</span><span className="tkr-name">{s.name}</span></div>
                  <div className="card-chips">
                    {s.crowding && <span className="crowd" title="Extreme one-sided retail sentiment">crowd</span>}
                    {s.stale && <span className="stale">stale</span>}
                    <Chip sig={s.signal} />
                  </div>
                </div>
                <div className="card-px">
                  <span className="mono px">{fmt(s.price)}</span>
                  {s.change_pct != null && <span className="mono chg" style={{ color: up ? C.teal : C.coral }}>{up ? "▲" : "▼"} {fmt(Math.abs(s.change_pct))}%</span>}
                </div>
                <Spark data={s.spark} up={up} />
                {s.conviction != null ? (
                  <>
                    <div className="card-conv"><span className="conv-l">Conviction</span>
                      <span className="mono conv-v" style={{ color: s.conviction >= 0 ? C.teal : C.coral }}>
                        {s.conviction > 0 ? "+" : ""}{Math.round(s.conviction)}<span className="conf"> · conf {fmt(s.confidence, 2)}</span></span></div>
                    <Meter label="Technical" v={s.technical_score ?? 0} />
                    <Meter label="News" v={s.news_score ?? 0} dim={s.news_score == null} />
                    <Meter label="Social" v={s.social_score ?? 0} dim={s.social_score == null} />
                    {s.drivers?.length > 0 && <ul className="drivers">{s.drivers.map((x, i) => <li key={i}>{x}</li>)}</ul>}
                  </>
                ) : <p className="await">Awaiting first model run…</p>}
              </article>
            );
          })}
        </div>
        {tickers.length === 0 && <p className="empty">No data yet — backfill + train first.</p>}
      </section>

      <PerfChart perf={perf} />

      <div className="two-col">
        <section className="panel">
          <div className="panel-head"><h2>Decision log</h2><span className="head-note">every action, and every skip</span></div>
          <ol className="feed">
            {decisions.map((f) => (
              <li key={f.id} className="feed-row">
                <span className="mono feed-time">{timeOf(f.ts)}</span>
                <div className="feed-main">
                  <div className="feed-head"><span className="tkr sm">{f.symbol}</span>
                    <span className="act-tag">{f.action}</span><Chip sig={f.signal} />
                    <span className="mono conf">conv {Math.round(f.conviction)}</span></div>
                  <p className="feed-act">{f.reason}</p>
                  {f.drivers?.length > 0 && <ul className="why">{f.drivers.map((w, j) => <li key={j}>{w}</li>)}</ul>}
                </div>
              </li>
            ))}
            {decisions.length === 0 && <p className="empty">No decisions yet.</p>}
          </ol>
        </section>
        <SentimentDesk trackers={trackers} onAdd={onAddTracker} onRemove={onRemoveTracker} />
      </div>

      <div className="two-col">
        <section className="panel">
          <div className="panel-head"><h2>News & filings</h2><span className="head-note">FinBERT + event classification</span></div>
          <ol className="feed">
            {news.map((n) => (
              <li key={n.id} className="feed-row">
                <span className="mono feed-time">{dateOf(n.ts)}</span>
                <div className="feed-main">
                  <div className="feed-head"><span className="tkr sm">{n.symbol}</span>
                    <span className="src-tag">{n.source}</span>
                    {n.event_type && <span className="ev-tag">{n.event_type}</span>}
                    {n.impact != null && <span className="mono conf" style={{ color: n.impact >= 0 ? C.teal : C.coral }}>{n.impact >= 0 ? "+" : ""}{fmt(n.impact, 2)}</span>}</div>
                  <p className="feed-act">{n.headline}</p>
                </div>
              </li>
            ))}
            {news.length === 0 && <p className="empty">No news yet — the worker refreshes on a slow cadence.</p>}
          </ol>
        </section>
        <BacktestCard bt={bt} />
      </div>

      <footer className="foot">
        Phase 2 — technical + news + social ensemble, tracker ledger, dashboard complete. Hard breakers
        (−3% day, −12% drawdown) enforced in code. Decision support, not investment advice.
      </footer>
    </div>
  );
}

const css = `
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
* { box-sizing: border-box; margin: 0; }
body { background: ${C.bg}; }
.root { min-height: 100vh; background: ${C.bg}; color: ${C.ink}; font-family: 'Space Grotesk', system-ui, sans-serif; padding: 20px clamp(14px,4vw,40px) 60px; max-width: 1240px; margin: 0 auto; }
.mono { font-family: 'IBM Plex Mono', monospace; }
h1 { font-size: 20px; letter-spacing: .04em; text-transform: uppercase; }
h2 { font-size: 13px; letter-spacing: .14em; text-transform: uppercase; color: ${C.ink}; font-weight: 600; }
.top { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; justify-content: space-between; padding: 6px 2px 18px; }
.brand { display: flex; gap: 12px; align-items: center; }
.brand-mark { color: ${C.amber}; font-size: 24px; }
.brand-sub { font-size: 12px; color: ${C.mut}; }
.top-stats { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
.stat { display: flex; flex-direction: column; }
.stat-l { font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: ${C.mut}; }
.stat-v { font-size: 15px; }
.mode { font-family: 'IBM Plex Mono', monospace; font-size: 11px; letter-spacing: .18em; color: ${C.amber}; border: 1px dashed ${C.amber}; padding: 5px 10px; border-radius: 4px; }
.health { font-size: 11px; border: 1px solid; border-radius: 4px; padding: 5px 10px; }
.kill { font: 600 11px 'Space Grotesk'; letter-spacing: .1em; text-transform: uppercase; background: transparent; color: ${C.coral}; border: 1px solid ${C.coral}; padding: 7px 12px; border-radius: 6px; cursor: pointer; }
.kill.armed { background: ${C.coral}; color: ${C.bg}; }
.banner { background: ${C.panel2}; border: 1px solid ${C.coral}55; color: ${C.ink}; font-size: 12px; padding: 10px 14px; border-radius: 8px; margin-bottom: 14px; }
.panel { background: ${C.panel}; border: 1px solid ${C.line}; border-radius: 12px; padding: 18px; margin-bottom: 18px; }
.panel-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
.head-note { font-size: 11px; color: ${C.mut}; }
.empty { font-size: 13px; color: ${C.mut}; }
.dial-body { display: grid; grid-template-columns: 240px 1fr; gap: 22px; align-items: center; }
.dial-svg { width: 100%; max-width: 240px; }
.dial-num { font: 700 40px 'IBM Plex Mono'; }
.dial-sub { font: 500 10px 'Space Grotesk'; letter-spacing: .2em; text-transform: uppercase; }
.contract-title { font-size: 12px; color: ${C.mut}; margin-bottom: 10px; }
.dial-contract dl { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px,1fr)); gap: 8px 18px; }
.dial-contract dl > div { display: flex; justify-content: space-between; gap: 8px; border-bottom: 1px solid ${C.line}; padding-bottom: 5px; }
.dial-contract dt { font-size: 11px; color: ${C.mut}; } .dial-contract dd { font: 500 12px 'IBM Plex Mono'; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px,1fr)); gap: 12px; }
.card { background: ${C.panel2}; border: 1px solid ${C.line}; border-radius: 10px; padding: 14px; }
.card-live { border-color: ${C.teal}66; box-shadow: 0 0 0 1px ${C.teal}33; }
.card-top { display: flex; justify-content: space-between; align-items: flex-start; }
.card-chips { display: flex; gap: 6px; align-items: center; }
.tkr { font: 700 15px 'IBM Plex Mono'; } .tkr.sm { font-size: 12px; }
.tkr-name { display: block; font-size: 11px; color: ${C.mut}; }
.chip { font: 600 10px 'IBM Plex Mono'; letter-spacing: .12em; border: 1px solid; border-radius: 4px; padding: 3px 7px; }
.stale { font: 600 9px 'IBM Plex Mono'; letter-spacing: .1em; text-transform: uppercase; color: ${C.amber}; border: 1px solid ${C.amber}66; border-radius: 4px; padding: 2px 5px; }
.crowd { font: 600 9px 'IBM Plex Mono'; letter-spacing: .1em; text-transform: uppercase; color: ${C.coral}; border: 1px solid ${C.coral}66; border-radius: 4px; padding: 2px 5px; }
.card-px { display: flex; align-items: baseline; gap: 10px; margin: 10px 0 4px; }
.px { font-size: 17px; } .chg { font-size: 11px; }
.card-conv { display: flex; justify-content: space-between; margin: 8px 0 6px; align-items: baseline; }
.conv-l { font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: ${C.mut}; }
.conv-v { font-size: 13px; font-weight: 600; } .conf { font-size: 10px; color: ${C.mut}; font-weight: 400; }
.await { font-size: 11px; color: ${C.mut}; margin-top: 8px; }
.meter { display: grid; grid-template-columns: 58px 1fr 30px; gap: 8px; align-items: center; margin-top: 5px; }
.meter-l { font-size: 10px; color: ${C.mut}; }
.meter-track { position: relative; height: 5px; background: ${C.panel}; border-radius: 3px; overflow: hidden; }
.meter-mid { position: absolute; left: 50%; top: 0; bottom: 0; width: 1px; background: ${C.line}; }
.meter-fill { position: absolute; top: 0; bottom: 0; border-radius: 3px; }
.meter-v { font: 500 10px 'IBM Plex Mono'; text-align: right; }
.drivers { list-style: none; margin-top: 8px; padding-left: 10px; border-left: 2px solid ${C.line}; }
.drivers li { font-size: 10.5px; color: ${C.mut}; margin-top: 2px; }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.feed { list-style: none; display: flex; flex-direction: column; gap: 12px; max-height: 460px; overflow-y: auto; }
.feed-row { display: grid; grid-template-columns: 46px 1fr; gap: 12px; border-bottom: 1px solid ${C.line}; padding-bottom: 10px; }
.feed-time { font-size: 11px; color: ${C.mut}; padding-top: 2px; }
.feed-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.act-tag { font: 600 10px 'IBM Plex Mono'; letter-spacing: .1em; color: ${C.ink}; }
.src-tag, .ev-tag { font-size: 9px; letter-spacing: .1em; text-transform: uppercase; color: ${C.mut}; border: 1px solid ${C.line}; border-radius: 3px; padding: 1px 5px; }
.ev-tag { color: ${C.amber}; border-color: ${C.amber}55; }
.conf { font-size: 10px; color: ${C.mut}; }
.feed-act { font-size: 12.5px; margin: 5px 0 4px; }
.why { list-style: none; padding-left: 10px; border-left: 2px solid ${C.line}; }
.why li { font-size: 11px; color: ${C.mut}; margin-top: 2px; }
.trk { list-style: none; display: flex; flex-direction: column; gap: 12px; max-height: 400px; overflow-y: auto; }
.trk-row { border-bottom: 1px solid ${C.line}; padding-bottom: 10px; }
.trk-head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.trk-h { font: 600 12px 'IBM Plex Mono'; }
.trk-src { font-size: 10px; color: ${C.mut}; border: 1px solid ${C.line}; border-radius: 3px; padding: 1px 5px; }
.trk-hit { font-size: 10px; }
.trk-x { margin-left: auto; background: transparent; border: none; color: ${C.mut}; font-size: 16px; cursor: pointer; line-height: 1; }
.trk-x:hover { color: ${C.coral}; }
.trk-note { font-size: 12px; color: ${C.ink}; margin-top: 5px; }
.trk-add { display: flex; gap: 8px; margin-top: 14px; }
.trk-add input { flex: 1; background: ${C.panel2}; border: 1px solid ${C.line}; color: ${C.ink}; border-radius: 6px; padding: 8px; font-size: 12px; }
.trk-add select { background: ${C.panel2}; border: 1px solid ${C.line}; color: ${C.ink}; border-radius: 6px; padding: 8px; font-size: 12px; }
.trk-add button { background: transparent; border: 1px dashed ${C.line}; color: ${C.mut}; border-radius: 6px; padding: 8px 12px; font-size: 12px; cursor: pointer; }
.trk-add button:hover { color: ${C.ink}; border-color: ${C.mut}; }
.legend { display: flex; gap: 18px; margin-top: 8px; font-size: 11px; color: ${C.mut}; }
.legend i { display: inline-block; width: 10px; height: 3px; margin-right: 5px; vertical-align: middle; }
.bt { width: 100%; border-collapse: collapse; }
.bt th { text-align: right; font-size: 10px; letter-spacing: .1em; text-transform: uppercase; color: ${C.mut}; padding: 4px 6px; }
.bt th:first-child { text-align: left; }
.bt td { text-align: right; padding: 5px 6px; font-size: 12px; border-top: 1px solid ${C.line}; }
.bt .bt-l { text-align: left; color: ${C.mut}; font-size: 11px; } .bt .dim { color: ${C.mut}; }
.foot { font-size: 11px; color: ${C.mut}; margin-top: 6px; line-height: 1.5; }
button:focus-visible, input:focus-visible, select:focus-visible { outline: 2px solid ${C.amber}; outline-offset: 2px; }
@media (max-width: 880px) { .two-col { grid-template-columns: 1fr; } .dial-body { grid-template-columns: 1fr; } }
`;
