// Thin API client. All requests go through /api/* which is proxied to the
// backend by Vite (dev) or nginx (Docker).

const BASE = "/api";

async function getJSON(path) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { Accept: "application/json" },
  });
  // /health returns 503 with a valid body when degraded — still parse it.
  const body = await res.json().catch(() => null);
  if (!body) throw new Error(`bad response from ${path}`);
  return body;
}

export const fetchWatchlist = () => getJSON("/watchlist");
export const fetchHealth = () => getJSON("/health");
