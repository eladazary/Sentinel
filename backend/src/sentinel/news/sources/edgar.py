"""SEC EDGAR filings as news items (free, official, no API key).

Uses the public data.sec.gov submissions API. SEC requires a descriptive
User-Agent with contact info (configured via SENTINEL_SEC_USER_AGENT).
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from sentinel.logging_config import get_logger
from sentinel.news.base import NewsItem

log = get_logger(__name__)

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

# Filing forms we treat as material news, mapped to an event-type hint.
_FORM_EVENT = {
    "8-K": "other", "10-Q": "earnings", "10-K": "earnings",
    "4": "other", "SC 13D": "m&a", "SC 13G": "m&a", "DEF 14A": "other",
}


class EdgarSource:
    name = "sec_edgar"

    def __init__(self, user_agent: str):
        self._ua = user_agent
        self._cik: dict[str, str] | None = None

    def available(self) -> bool:
        return True  # keyless

    def _headers(self) -> dict:
        return {"User-Agent": self._ua, "Accept-Encoding": "gzip, deflate"}

    def _load_cik_map(self, client: httpx.Client) -> dict[str, str]:
        if self._cik is None:
            r = client.get(_TICKERS_URL, headers=self._headers(), timeout=15)
            r.raise_for_status()
            data = r.json()
            self._cik = {
                row["ticker"].upper(): f"{int(row['cik_str']):010d}"
                for row in data.values()
            }
        return self._cik

    def fetch(self, symbol: str, since: datetime, limit: int = 50) -> list[NewsItem]:
        items: list[NewsItem] = []
        try:
            with httpx.Client() as client:
                cik = self._load_cik_map(client).get(symbol.upper())
                if not cik:
                    return []
                r = client.get(
                    _SUBMISSIONS_URL.format(cik=cik), headers=self._headers(), timeout=15
                )
                r.raise_for_status()
                recent = r.json().get("filings", {}).get("recent", {})
        except Exception as exc:  # noqa: BLE001 - network/source failures are non-fatal
            log.warning("EDGAR fetch failed for %s: %s", symbol, exc)
            return []

        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accession = recent.get("accessionNumber", [])
        docs = recent.get("primaryDocument", [])
        primary_desc = recent.get("primaryDocDescription", [])
        for i, form in enumerate(forms):
            if len(items) >= limit:
                break
            try:
                ts = datetime.fromisoformat(dates[i]).replace(tzinfo=timezone.utc)
            except (ValueError, IndexError):
                continue
            if ts < since:
                continue
            acc = accession[i] if i < len(accession) else ""
            desc = primary_desc[i] if i < len(primary_desc) else ""
            items.append(
                NewsItem(
                    symbol=symbol.upper(),
                    ts=ts,
                    source=self.name,
                    headline=f"SEC {form} filing",
                    summary=desc or form,
                    url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}",
                    external_id=f"edgar:{acc}:{docs[i] if i < len(docs) else ''}",
                    event_type=_FORM_EVENT.get(form),
                )
            )
        return items
