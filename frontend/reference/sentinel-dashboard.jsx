import { useState, useMemo } from "react";

/* ============================================================
   SENTINEL — dry-run trading command deck (visual prototype)
   Mock data throughout; wiring points marked with `// API:`
   Palette: harbor #0F1622 · panel #171F2C · ink #E7EDF5
            teal #3EC9A7 (long) · coral #F2695C (risk-off)
            amber #E8A33D (risk dial) · muted #86119?
   Type: Space Grotesk (display) · IBM Plex Mono (data)
   Signature: the Risk Dial arc — one control, whole-system
              consequences previewed live beneath it.
   ============================================================ */

const C = {
  bg: "#0F1622",
  panel: "#171F2C",
  panel2: "#1C2635",
  line: "rgba(134,150,170,0.16)",
  ink: "#E7EDF5",
  mut: "#8696AA",
  teal: "#3EC9A7",
  coral: "#F2695C",
  amber: "#E8A33D",
};

/* ---------- mock data (deterministic) ---------- */
const spark = (seed, n = 28) => {
  let v = 100, out = [];
  for (let i = 0; i < n; i++) {
    v += Math.sin(seed * 3.7 + i * 0.9) * 1.6 + Math.cos(seed + i * 0.31) * 0.9;
    out.push(v);
  }
  return out;
};

const TICKERS = [
  { t: "NVDA", name: "NVIDIA", px: 172.41, chg: +2.34, conv: 68, tech: 74, news: 71, soc: 48, sig: "BUY",  seed: 1 },
  { t: "MSFT", name: "Microsoft", px: 512.09, chg: +0.41, conv: 41, tech: 52, news: 38, soc: 22, sig: "HOLD", seed: 2 },
  { t: "AAPL", name: "Apple", px: 236.77, chg: -0.62, conv: 12, tech: 18, news: 9,  soc: 4,  sig: "HOLD", seed: 3 },
  { t: "AMZN", name: "Amazon", px: 231.5,  chg: +1.12, conv: 55, tech: 49, news: 66, soc: 51, sig: "BUY",  seed: 4 },
  { t: "JPM",  name: "JPMorgan", px: 302.18, chg: -0.18, conv: -8, tech: -4, news: -21, soc: 6, sig: "HOLD", seed: 5 },
  { t: "XOM",  name: "Exxon Mobil", px: 118.6, chg: -1.44, conv: -46, tech: -51, news: -44, soc: -30, sig: "TRIM", seed: 6 },
];

const FEED = [
  { time: "14:32", t: "NVDA", act: "OPENED · 22 sh @ 171.98 (paper)", sig: "BUY", conf: 0.72,
    why: ["Volume z-score +2.4 on 50-day MA reclaim", "Supplier guidance raise scored material (4/5), fresh", "Tracked-author net stance +0.6, no crowding flag"] },
  { time: "13:05", t: "AMZN", act: "SIGNAL — awaiting entry window", sig: "BUY", conf: 0.61,
    why: ["Relative strength vs SPY at 30-day high", "Logistics-margin coverage skew positive", "Social momentum mild; credibility-weighted"] },
  { time: "11:47", t: "XOM", act: "TRIMMED 50% · stop tightened to 2.0× ATR", sig: "TRIM", conf: 0.58,
    why: ["Close below 50-day on rising volume", "Crude macro headlines net negative, decaying slowly", "Model disagreement low — confidence held"] },
  { time: "10:12", t: "TSLA", act: "SKIPPED — conviction 44 below gate 50", sig: "PASS", conf: 0.44,
    why: ["Crowding flag: retail sentiment 96th percentile one-sided", "Earnings in 6 sessions — blackout tightening active"] },
];

const TRACKERS = [
  { h: "@macro_ledger", src: "X", hit: 71, n: 34, stance: "+ NVDA", note: "supply-chain checks point to a strong quarter", ago: "12m" },
  { h: "u/quietvalue", src: "Reddit", hit: 64, n: 22, stance: "− XOM", note: "crack spreads compressing, trimming energy here", ago: "41m" },
  { h: "@delta_desk", src: "X", hit: 58, n: 51, stance: "+ AMZN", note: "AWS re-acceleration is still underpriced imo", ago: "1h" },
  { h: "u/theta_farmer", src: "Reddit", hit: 39, n: 17, stance: "+ TSLA", note: "loading calls into earnings 🚀", ago: "2h", warn: true },
];

/* ---------- risk mapping (mirrors spec §6) ---------- */
const riskMap = (r) => ({
  maxPos: (5 + (r - 1) * (15 / 9)).toFixed(0),
  maxExp: (30 + (r - 1) * (65 / 9)).toFixed(0),
  gate: Math.round(70 - (r - 1) * (35 / 9)),
  stop: (1.5 + (r - 1) * (2 / 9)).toFixed(1),
  perDay: r <= 3 ? 1 : r <= 7 ? 2 : 4,
  earnings: r <= 3 ? "never" : r <= 7 ? "reduced size" : "allowed",
});
const riskColor = (r) => (r <= 4 ? C.teal : r <= 7 ? C.amber : C.coral);

/* ---------- small pieces ---------- */
const Spark = ({ data, up }) => {
  const min = Math.min(...data), max = Math.max(...data);
  const pts = data.map((v, i) =>
    `${(i / (data.length - 1)) * 100},${34 - ((v - min) / (max - min || 1)) * 30}`).join(" ");
  return (
    <svg viewBox="0 0 100 36" style={{ width: "100%", height: 36 }} preserveAspectRatio="none" aria-hidden>
      <polyline points={pts} fill="none" stroke={up ? C.teal : C.coral} strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  );
};

const Chip = ({ sig }) => {
  const map = { BUY: C.teal, SELL: C.coral, TRIM: C.coral, HOLD: C.mut, PASS: C.mut };
  return (
    <span className="chip" style={{ color: map[sig], borderColor: map[sig] }}>{sig}</span>
  );
};

const Meter = ({ label, v }) => (
  <div className="meter">
    <span className="meter-l">{label}</span>
    <div className="meter-track">
      <div className="meter-mid" />
      <div
        className="meter-fill"
        style={{
          left: v >= 0 ? "50%" : `${50 + v / 2}%`,
          width: `${Math.abs(v) / 2}%`,
          background: v >= 0 ? C.teal : C.coral,
        }}
      />
    </div>
    <span className="meter-v" style={{ color: v >= 0 ? C.teal : C.coral }}>
      {v > 0 ? "+" : ""}{v}
    </span>
  </div>
);

/* ---------- risk dial (signature element) ---------- */
const Dial = ({ risk, setRisk }) => {
  const col = riskColor(risk);
  const R = 88, cx = 110, cy = 108;
  const angle = (i) => Math.PI * (1 - (i - 1) / 9); // 1→180°, 10→0°
  const pos = (i, r = R) => [cx + r * Math.cos(angle(i)), cy - r * Math.sin(angle(i))];
  const arcTo = (i) => {
    const [sx, sy] = pos(1), [ex, ey] = pos(i);
    const large = angle(1) - angle(i) > Math.PI ? 1 : 0;
    return `M ${sx} ${sy} A ${R} ${R} 0 ${large} 1 ${ex} ${ey}`;
  };
  const m = riskMap(risk);
  return (
    <section className="panel dial-wrap" aria-label="Risk factor">
      <div className="panel-head">
        <h2>Risk factor</h2>
        <span className="head-note">governs new decisions only · hard breakers always on</span>
      </div>
      <div className="dial-body">
        <svg viewBox="0 0 220 122" className="dial-svg" role="slider" aria-valuemin={1} aria-valuemax={10} aria-valuenow={risk}>
          <path d={arcTo(10)} fill="none" stroke={C.line} strokeWidth="10" strokeLinecap="round" />
          <path d={arcTo(risk)} fill="none" stroke={col} strokeWidth="10" strokeLinecap="round"
            style={{ transition: "stroke .25s" }} />
          {Array.from({ length: 10 }, (_, k) => k + 1).map((i) => {
            const [x, y] = pos(i);
            return (
              <g key={i} onClick={() => setRisk(i)} style={{ cursor: "pointer" }}>
                <circle cx={x} cy={y} r="11" fill="transparent" />
                <circle cx={x} cy={y} r={i === risk ? 7 : 3.5}
                  fill={i <= risk ? col : C.panel2} stroke={i === risk ? C.ink : C.line}
                  strokeWidth={i === risk ? 1.5 : 1} style={{ transition: "all .2s" }} />
              </g>
            );
          })}
          <text x={cx} y={cy - 14} textAnchor="middle" className="dial-num" fill={col}>{risk}</text>
          <text x={cx} y={cy + 4} textAnchor="middle" className="dial-sub" fill={C.mut}>of 10</text>
        </svg>
        <div className="dial-contract">
          <p className="contract-title">At risk {risk}, Sentinel will:</p>
          <dl>
            <div><dt>Max position</dt><dd>{m.maxPos}% of equity</dd></div>
            <div><dt>Max exposure</dt><dd>{m.maxExp}%</dd></div>
            <div><dt>Conviction gate</dt><dd>≥ {m.gate}</dd></div>
            <div><dt>Stop width</dt><dd>{m.stop}× ATR</dd></div>
            <div><dt>New trades / day</dt><dd>{m.perDay}</dd></div>
            <div><dt>Around earnings</dt><dd>{m.earnings}</dd></div>
          </dl>
          <div className="range-row">
            <input type="range" min="1" max="10" value={risk}
              onChange={(e) => setRisk(+e.target.value)}
              style={{ accentColor: col }} aria-label="Risk factor slider" />
          </div>
        </div>
      </div>
    </section>
  );
};

/* ---------- app ---------- */
export default function App() {
  const [risk, setRisk] = useState(5);
  const [armKill, setArmKill] = useState(false);
  const [killed, setKilled] = useState(false);
  const gate = riskMap(risk).gate;
  const sparks = useMemo(() => TICKERS.map((x) => spark(x.seed)), []);

  return (
    <div className="root">
      <style>{css}</style>

      <header className="top">
        <div className="brand">
          <span className="brand-mark" aria-hidden>◮</span>
          <div>
            <h1>Sentinel</h1>
            <span className="brand-sub">swing desk · 6 of 10 slots used</span>
          </div>
        </div>
        <div className="top-stats">
          <div className="stat">
            <span className="stat-l">Paper equity</span>
            <span className="stat-v mono">$104,318</span>
          </div>
          <div className="stat">
            <span className="stat-l">Day P&L</span>
            <span className="stat-v mono" style={{ color: C.teal }}>+$612 · 0.59%</span>
          </div>
          <span className="mode">DRY RUN</span>
          {!killed ? (
            <button
              className={"kill" + (armKill ? " armed" : "")}
              onClick={() => (armKill ? (setKilled(true), setArmKill(false)) : setArmKill(true))}
              onBlur={() => setArmKill(false)}
            >
              {armKill ? "Confirm: cancel all orders" : "Kill switch"}
            </button>
          ) : (
            <span className="killed">All orders cancelled · system flat</span>
          )}
        </div>
      </header>

      <Dial risk={risk} setRisk={setRisk} />

      <section className="panel">
        <div className="panel-head">
          <h2>Watchlist</h2>
          <span className="head-note">conviction −100…+100 · gate at ≥ {gate} highlighted</span>
        </div>
        <div className="grid">
          {TICKERS.map((s, i) => (
            <article key={s.t} className={"card" + (s.conv >= gate ? " card-live" : "")}>
              <div className="card-top">
                <div>
                  <span className="tkr">{s.t}</span>
                  <span className="tkr-name">{s.name}</span>
                </div>
                <Chip sig={s.conv >= gate ? "BUY" : s.sig} />
              </div>
              <div className="card-px">
                <span className="mono px">{s.px.toFixed(2)}</span>
                <span className="mono chg" style={{ color: s.chg >= 0 ? C.teal : C.coral }}>
                  {s.chg >= 0 ? "▲" : "▼"} {Math.abs(s.chg).toFixed(2)}%
                </span>
              </div>
              <Spark data={sparks[i]} up={s.chg >= 0} />
              <div className="card-conv">
                <span className="conv-l">Conviction</span>
                <span className="mono conv-v" style={{ color: s.conv >= 0 ? C.teal : C.coral }}>
                  {s.conv > 0 ? "+" : ""}{s.conv}
                </span>
              </div>
              <Meter label="Technical" v={s.tech} />
              <Meter label="News" v={s.news} />
              <Meter label="Social" v={s.soc} />
            </article>
          ))}
        </div>
      </section>

      <div className="two-col">
        <section className="panel">
          <div className="panel-head">
            <h2>Decision log</h2>
            <span className="head-note">every action, and every skip, explains itself</span>
          </div>
          <ol className="feed">
            {FEED.map((f, i) => (
              <li key={i} className="feed-row">
                <span className="mono feed-time">{f.time}</span>
                <div className="feed-main">
                  <div className="feed-head">
                    <span className="tkr sm">{f.t}</span>
                    <Chip sig={f.sig} />
                    <span className="mono conf">conf {f.conf.toFixed(2)}</span>
                  </div>
                  <p className="feed-act">{f.act}</p>
                  <ul className="why">
                    {f.why.map((w, j) => <li key={j}>{w}</li>)}
                  </ul>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>Sentiment desk</h2>
            <span className="head-note">tracked authors, weighted by measured hit rate</span>
          </div>
          <ul className="trk">
            {TRACKERS.map((u, i) => (
              <li key={i} className="trk-row">
                <div className="trk-head">
                  <span className="trk-h">{u.h}</span>
                  <span className="trk-src">{u.src}</span>
                  <span className="mono trk-hit" style={{ color: u.hit >= 55 ? C.teal : u.hit >= 45 ? C.amber : C.coral }}>
                    {u.hit}% over {u.n} calls
                  </span>
                  <span className="trk-ago">{u.ago}</span>
                </div>
                <p className="trk-note">
                  <b style={{ color: u.stance.startsWith("+") ? C.teal : C.coral }}>{u.stance}</b> — “{u.note}”
                  {u.warn && <span className="warn"> low-credibility · weight 0.2×</span>}
                </p>
              </li>
            ))}
          </ul>
          <button className="ghost">+ Track a new account</button>
        </section>
      </div>

      <footer className="foot">
        Prototype with mock data. Hard breakers (−3% day, −12% drawdown) are enforced in code and cannot be
        disabled from this screen. Decision support, not investment advice.
      </footer>
    </div>
  );
}

/* ---------- styles ---------- */
const css = `
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
* { box-sizing: border-box; margin: 0; }
.root {
  min-height: 100vh; background: ${C.bg}; color: ${C.ink};
  font-family: 'Space Grotesk', system-ui, sans-serif;
  padding: 20px clamp(14px, 4vw, 40px) 60px; max-width: 1240px; margin: 0 auto;
}
.mono { font-family: 'IBM Plex Mono', monospace; }
h1 { font-size: 20px; letter-spacing: .04em; text-transform: uppercase; }
h2 { font-size: 13px; letter-spacing: .14em; text-transform: uppercase; color: ${C.ink}; font-weight: 600; }

.top { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; justify-content: space-between; padding: 6px 2px 18px; }
.brand { display: flex; gap: 12px; align-items: center; }
.brand-mark { color: ${C.amber}; font-size: 24px; }
.brand-sub { font-size: 12px; color: ${C.mut}; }
.top-stats { display: flex; gap: 18px; align-items: center; flex-wrap: wrap; }
.stat { display: flex; flex-direction: column; }
.stat-l { font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: ${C.mut}; }
.stat-v { font-size: 15px; }
.mode {
  font-family: 'IBM Plex Mono', monospace; font-size: 11px; letter-spacing: .18em;
  color: ${C.amber}; border: 1px dashed ${C.amber}; padding: 5px 10px; border-radius: 4px;
}
.kill {
  font: 600 11px 'Space Grotesk', sans-serif; letter-spacing: .1em; text-transform: uppercase;
  background: transparent; color: ${C.coral}; border: 1px solid ${C.coral};
  padding: 7px 12px; border-radius: 6px; cursor: pointer; transition: background .15s;
}
.kill.armed { background: ${C.coral}; color: ${C.bg}; }
.killed { font-size: 12px; color: ${C.coral}; }

.panel { background: ${C.panel}; border: 1px solid ${C.line}; border-radius: 12px; padding: 18px; margin-bottom: 18px; }
.panel-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
.head-note { font-size: 11px; color: ${C.mut}; }

.dial-body { display: grid; grid-template-columns: 240px 1fr; gap: 22px; align-items: center; }
.dial-svg { width: 100%; max-width: 240px; }
.dial-num { font: 700 40px 'IBM Plex Mono', monospace; }
.dial-sub { font: 500 10px 'Space Grotesk', sans-serif; letter-spacing: .2em; text-transform: uppercase; }
.contract-title { font-size: 12px; color: ${C.mut}; margin-bottom: 10px; }
.dial-contract dl { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px 18px; }
.dial-contract dl > div { display: flex; justify-content: space-between; gap: 8px; border-bottom: 1px solid ${C.line}; padding-bottom: 5px; }
.dial-contract dt { font-size: 11px; color: ${C.mut}; }
.dial-contract dd { font: 500 12px 'IBM Plex Mono', monospace; }
.range-row { margin-top: 14px; }
.range-row input { width: 100%; }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(215px, 1fr)); gap: 12px; }
.card { background: ${C.panel2}; border: 1px solid ${C.line}; border-radius: 10px; padding: 14px; }
.card-live { border-color: ${C.teal}66; box-shadow: 0 0 0 1px ${C.teal}33; }
.card-top { display: flex; justify-content: space-between; align-items: flex-start; }
.tkr { font: 700 15px 'IBM Plex Mono', monospace; }
.tkr.sm { font-size: 12px; }
.tkr-name { display: block; font-size: 11px; color: ${C.mut}; }
.chip { font: 600 10px 'IBM Plex Mono', monospace; letter-spacing: .12em; border: 1px solid; border-radius: 4px; padding: 3px 7px; }
.card-px { display: flex; align-items: baseline; gap: 10px; margin: 10px 0 4px; }
.px { font-size: 17px; }
.chg { font-size: 11px; }
.card-conv { display: flex; justify-content: space-between; margin: 8px 0 6px; }
.conv-l { font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: ${C.mut}; }
.conv-v { font-size: 13px; font-weight: 600; }

.meter { display: grid; grid-template-columns: 58px 1fr 32px; gap: 8px; align-items: center; margin-top: 5px; }
.meter-l { font-size: 10px; color: ${C.mut}; }
.meter-track { position: relative; height: 5px; background: ${C.panel}; border-radius: 3px; overflow: hidden; }
.meter-mid { position: absolute; left: 50%; top: 0; bottom: 0; width: 1px; background: ${C.line}; }
.meter-fill { position: absolute; top: 0; bottom: 0; border-radius: 3px; transition: all .3s; }
.meter-v { font: 500 10px 'IBM Plex Mono', monospace; text-align: right; }

.two-col { display: grid; grid-template-columns: 1.25fr 1fr; gap: 18px; }
.feed { list-style: none; display: flex; flex-direction: column; gap: 14px; }
.feed-row { display: grid; grid-template-columns: 44px 1fr; gap: 12px; border-bottom: 1px solid ${C.line}; padding-bottom: 12px; }
.feed-row:last-child { border-bottom: none; padding-bottom: 0; }
.feed-time { font-size: 11px; color: ${C.mut}; padding-top: 2px; }
.feed-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.conf { font-size: 10px; color: ${C.mut}; }
.feed-act { font-size: 13px; margin: 5px 0 4px; }
.why { list-style: none; padding-left: 12px; border-left: 2px solid ${C.line}; }
.why li { font-size: 11.5px; color: ${C.mut}; margin-top: 2px; }

.trk { list-style: none; display: flex; flex-direction: column; gap: 12px; }
.trk-row { border-bottom: 1px solid ${C.line}; padding-bottom: 10px; }
.trk-row:last-child { border-bottom: none; }
.trk-head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.trk-h { font: 600 12px 'IBM Plex Mono', monospace; }
.trk-src { font-size: 10px; color: ${C.mut}; border: 1px solid ${C.line}; border-radius: 3px; padding: 1px 5px; }
.trk-hit { font-size: 10px; }
.trk-ago { font-size: 10px; color: ${C.mut}; margin-left: auto; }
.trk-note { font-size: 12px; color: ${C.ink}; margin-top: 5px; }
.warn { font-size: 10px; color: ${C.amber}; }
.ghost {
  margin-top: 12px; width: 100%; background: transparent; border: 1px dashed ${C.line};
  color: ${C.mut}; font: 500 12px 'Space Grotesk', sans-serif; padding: 9px; border-radius: 8px; cursor: pointer;
}
.ghost:hover { color: ${C.ink}; border-color: ${C.mut}; }

.foot { font-size: 11px; color: ${C.mut}; margin-top: 6px; line-height: 1.5; }
button:focus-visible, input:focus-visible { outline: 2px solid ${C.amber}; outline-offset: 2px; }
@media (max-width: 880px) {
  .two-col { grid-template-columns: 1fr; }
  .dial-body { grid-template-columns: 1fr; justify-items: center; }
}
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
`;