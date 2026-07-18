import { useEffect, useMemo, useState } from "react";
import { fetchWatchlist, fetchHealth } from "./api.js";

/* Sentinel — Phase 0 live-price watchlist.
   Palette + Spark borrowed from reference/sentinel-dashboard.jsx so this grows
   into the full command deck later. This screen shows only what Phase 0
   produces: real prices ingested from Alpaca. No signals/conviction yet. */

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

const POLL_MS = 15000;

const Spark = ({ data, up }) => {
  if (!data || data.length < 2) {
    return <div style={{ height: 36 }} />;
  }
  const min = Math.min(...data);
  const max = Math.max(...data);
  const pts = data
    .map(
      (v, i) =>
        `${(i / (data.length - 1)) * 100},${
          34 - ((v - min) / (max - min || 1)) * 30
        }`
    )
    .join(" ");
  return (
    <svg
      viewBox="0 0 100 36"
      style={{ width: "100%", height: 36 }}
      preserveAspectRatio="none"
      aria-hidden
    >
      <polyline
        points={pts}
        fill="none"
        stroke={up ? C.teal : C.coral}
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  );
};

const fmtPrice = (v) =>
  v == null ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const fmtPct = (v) => (v == null ? "" : `${v >= 0 ? "▲" : "▼"} ${Math.abs(v).toFixed(2)}%`);

function Card({ t }) {
  const up = (t.change ?? 0) >= 0;
  const col = up ? C.teal : C.coral;
  return (
    <article className="card">
      <div className="card-top">
        <div>
          <span className="tkr">{t.symbol}</span>
          <span className="tkr-name">{t.name}</span>
        </div>
        {t.stale && <span className="stale" title="No recent update">stale</span>}
      </div>
      <div className="card-px">
        <span className="mono px">{fmtPrice(t.price)}</span>
        {t.change_pct != null && (
          <span className="mono chg" style={{ color: col }}>
            {fmtPct(t.change_pct)}
          </span>
        )}
      </div>
      <Spark data={t.spark} up={up} />
      <div className="card-foot">
        <span className="foot-l">prev close</span>
        <span className="mono foot-v">{fmtPrice(t.prev_close)}</span>
      </div>
    </article>
  );
}

export default function App() {
  const [data, setData] = useState(null);
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    async function tick() {
      try {
        const [wl, h] = await Promise.all([fetchWatchlist(), fetchHealth()]);
        if (!alive) return;
        setData(wl);
        setHealth(h);
        setError(null);
      } catch (e) {
        if (alive) setError(e.message || "connection error");
      } finally {
        if (alive) setLoading(false);
      }
    }
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const mode = data?.mode || health?.mode || "—";
  const healthy = health?.status === "ok";
  const tickers = data?.tickers || [];
  const dbUp = useMemo(
    () => health?.components?.find((c) => c.name === "database")?.ok ?? false,
    [health]
  );

  return (
    <div className="root">
      <style>{css}</style>
      <header className="top">
        <div className="brand">
          <span className="brand-mark" aria-hidden>◮</span>
          <div>
            <h1>Sentinel</h1>
            <span className="brand-sub">swing desk · phase 0 · live prices</span>
          </div>
        </div>
        <div className="top-stats">
          <span className="mode">{mode}</span>
          <span
            className="health"
            style={{ color: healthy ? C.teal : C.coral, borderColor: healthy ? C.teal : C.coral }}
            title={JSON.stringify(health?.components || [])}
          >
            {healthy ? "● systems ok" : "● degraded"}
          </span>
        </div>
      </header>

      {error && (
        <div className="banner">Can’t reach the API — retrying every {POLL_MS / 1000}s. ({error})</div>
      )}
      {!error && !dbUp && health && (
        <div className="banner">Database not ready — prices will appear once ingestion is up.</div>
      )}

      <section className="panel">
        <div className="panel-head">
          <h2>Watchlist</h2>
          <span className="head-note">
            {loading ? "loading…" : `${tickers.length} tickers · updates every ${POLL_MS / 1000}s`}
          </span>
        </div>
        {tickers.length === 0 && !loading ? (
          <p className="empty">
            No prices yet. The worker backfills history and polls live prices once
            Alpaca credentials are configured.
          </p>
        ) : (
          <div className="grid">
            {tickers.map((t) => (
              <Card key={t.symbol} t={t} />
            ))}
          </div>
        )}
      </section>

      <footer className="foot">
        Phase 0 — market-data ingestion only. No signals, no orders. Decision
        support, not investment advice.
      </footer>
    </div>
  );
}

const css = `
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
* { box-sizing: border-box; margin: 0; }
body { background: ${C.bg}; }
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
.top-stats { display: flex; gap: 12px; align-items: center; }
.mode {
  font-family: 'IBM Plex Mono', monospace; font-size: 11px; letter-spacing: .18em;
  color: ${C.amber}; border: 1px dashed ${C.amber}; padding: 5px 10px; border-radius: 4px;
}
.health { font-size: 11px; border: 1px solid; border-radius: 4px; padding: 5px 10px; }
.banner {
  background: ${C.panel2}; border: 1px solid ${C.coral}55; color: ${C.ink};
  font-size: 12px; padding: 10px 14px; border-radius: 8px; margin-bottom: 14px;
}
.panel { background: ${C.panel}; border: 1px solid ${C.line}; border-radius: 12px; padding: 18px; margin-bottom: 18px; }
.panel-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
.head-note { font-size: 11px; color: ${C.mut}; }
.empty { font-size: 13px; color: ${C.mut}; line-height: 1.6; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(215px, 1fr)); gap: 12px; }
.card { background: ${C.panel2}; border: 1px solid ${C.line}; border-radius: 10px; padding: 14px; }
.card-top { display: flex; justify-content: space-between; align-items: flex-start; min-height: 34px; }
.tkr { font: 700 15px 'IBM Plex Mono', monospace; }
.tkr-name { display: block; font-size: 11px; color: ${C.mut}; }
.stale { font: 600 9px 'IBM Plex Mono', monospace; letter-spacing: .12em; text-transform: uppercase;
  color: ${C.amber}; border: 1px solid ${C.amber}66; border-radius: 4px; padding: 2px 5px; }
.card-px { display: flex; align-items: baseline; gap: 10px; margin: 10px 0 4px; }
.px { font-size: 17px; }
.chg { font-size: 11px; }
.card-foot { display: flex; justify-content: space-between; margin-top: 8px; }
.foot-l { font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: ${C.mut}; }
.foot-v { font-size: 12px; color: ${C.mut}; }
.foot { font-size: 11px; color: ${C.mut}; margin-top: 6px; line-height: 1.5; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
`;
