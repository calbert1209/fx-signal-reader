"""Curated historical MoF/BoJ FX intervention crisis headline backfill module (Task P7-08).

Populates `news_headlines` in an isolated backtest SQLite database
(`data/backtest_signals.db`) with historically curated official policymaker
verbal escalation and intervention statements across 2022–2026 crisis episodes so
the Risk Committee's 4-tier escalation and physical veto logic can be stress-tested.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from fx_signal_reader.backtest.backfill import (
    DEFAULT_BACKTEST_DB_PATH,
    _open_db_connection,
    _resolve_date_range,
)
from fx_signal_reader.collectors.news_rss import NewsRSSCollector
from fx_signal_reader.core.normalizer import normalize_datetime_utc

_EVALUATOR = NewsRSSCollector()

CURATED_INTERVENTION_HEADLINES: tuple[dict[str, Any], ...] = (
    # --- 2022 September & October Intervention Crises ---
    {
        "published_utc": "2022-09-08T01:30:00Z",
        "source": "Reuters",
        "title": "Japan MoF Kanda warns against rapid moves and speculative moves in yen; not rule out any options",
        "url": "https://archive.fx-signal-reader.local/mof/2022-09-08-kanda",
        "episode": "2022-09 Intervention",
    },
    {
        "published_utc": "2022-09-14T00:30:00Z",
        "source": "Nikkei Asia",
        "title": "Bank of Japan conducts rate check with currency dealers as USD/JPY nears 145",
        "url": "https://archive.fx-signal-reader.local/mof/2022-09-14-rate-check",
        "episode": "2022-09 Intervention",
    },
    {
        "published_utc": "2022-09-22T08:15:00Z",
        "source": "Reuters",
        "title": "Japan Ministry of Finance Suzuki confirms direct yen intervention; took decisive action against speculative moves",
        "url": "https://archive.fx-signal-reader.local/mof/2022-09-22-intervention",
        "episode": "2022-09 Intervention",
    },
    {
        "published_utc": "2022-10-20T03:00:00Z",
        "source": "Reuters",
        "title": "Japan Finance Minister Suzuki warns cannot tolerate disorderly one-sided moves; stand ready for decisive action",
        "url": "https://archive.fx-signal-reader.local/mof/2022-10-20-suzuki",
        "episode": "2022-10 Intervention",
    },
    {
        "published_utc": "2022-10-21T14:45:00Z",
        "source": "Reuters",
        "title": "Japan MoF Kanda won't rule out any options as Tokyo suspected intervention stepped into market near 151.94",
        "url": "https://archive.fx-signal-reader.local/mof/2022-10-21-intervention",
        "episode": "2022-10 Intervention",
    },
    {
        "published_utc": "2022-10-24T01:10:00Z",
        "source": "Nikkei Asia",
        "title": "MoF top currency official Kanda warns against excessive volatility after second round of yen intervention",
        "url": "https://archive.fx-signal-reader.local/mof/2022-10-24-kanda",
        "episode": "2022-10 Intervention",
    },
    # --- 2024 April/May & July Intervention Crises ---
    {
        "published_utc": "2024-04-25T23:30:00Z",
        "source": "Reuters",
        "title": "Japan Finance Minister Suzuki watching with a sense of urgency; stand ready to take appropriate steps on rapid moves",
        "url": "https://archive.fx-signal-reader.local/mof/2024-04-25-suzuki",
        "episode": "2024-04/05 Intervention",
    },
    {
        "published_utc": "2024-04-29T04:00:00Z",
        "source": "Reuters",
        "title": "Japan MoF Kanda signals decisive action; physical yen intervention stepped into market after USD/JPY hit 160.24",
        "url": "https://archive.fx-signal-reader.local/mof/2024-04-29-intervention",
        "episode": "2024-04/05 Intervention",
    },
    {
        "published_utc": "2024-05-01T20:15:00Z",
        "source": "Nikkei Asia",
        "title": "Bank of Japan and Ministry of Finance conduct suspected intervention operations with bold action near 157.5",
        "url": "https://archive.fx-signal-reader.local/mof/2024-05-01-intervention",
        "episode": "2024-04/05 Intervention",
    },
    {
        "published_utc": "2024-07-10T02:00:00Z",
        "source": "Reuters",
        "title": "Japan MoF top currency official Kanda warns one-sided speculative moves are not reflecting fundamentals",
        "url": "https://archive.fx-signal-reader.local/mof/2024-07-10-kanda",
        "episode": "2024-07 Intervention",
    },
    {
        "published_utc": "2024-07-11T12:45:00Z",
        "source": "Reuters",
        "title": "Japan MoF stepped into market with direct yen intervention as BoJ checked rates above 161.50",
        "url": "https://archive.fx-signal-reader.local/mof/2024-07-11-intervention",
        "episode": "2024-07 Intervention",
    },
    {
        "published_utc": "2024-07-12T06:30:00Z",
        "source": "Nikkei Asia",
        "title": "Bank of Japan conducts second rate check with dealers while MoF refuses to rule out decisive action",
        "url": "https://archive.fx-signal-reader.local/mof/2024-07-12-rate-check",
        "episode": "2024-07 Intervention",
    },
    # --- 2024-11 to 2026-11 High-Spot (>157.50) Crisis Episodes ---
    {
        "published_utc": "2024-12-19T06:00:00Z",
        "source": "Reuters",
        "title": "Japan MoF Mimura warns against rapid moves and speculative moves above 157 as BoJ Ueda holds stance",
        "url": "https://archive.fx-signal-reader.local/mof/2024-12-19-mimura",
        "episode": "2024-12 High-Spot Escalation",
    },
    {
        "published_utc": "2025-01-13T23:00:00Z",
        "source": "Reuters",
        "title": "Japan MoF Mimura and Minister Kato will take decisive action against excessive volatility and not rule out any options",
        "url": "https://archive.fx-signal-reader.local/mof/2025-01-13-mimura-kato",
        "episode": "2025-01 High-Spot Escalation",
    },
    {
        "published_utc": "2025-01-15T01:00:00Z",
        "source": "Nikkei Asia",
        "title": "Finance Minister Kato flags tense sense of urgency over one-sided moves as USD/JPY approaches 158",
        "url": "https://archive.fx-signal-reader.local/mof/2025-01-15-kato",
        "episode": "2025-01 High-Spot Escalation",
    },
    {
        "published_utc": "2026-01-15T12:00:00Z",
        "source": "Reuters",
        "title": "Japan MoF Mimura warns all options are on the table and stand ready against disorderly moves above 158.50",
        "url": "https://archive.fx-signal-reader.local/mof/2026-01-15-mimura",
        "episode": "2026-01 Intervention Crisis",
    },
    {
        "published_utc": "2026-01-21T12:00:00Z",
        "source": "Nikkei Asia",
        "title": "Bank of Japan conducts rate check with dealers while MoF warns decisive action ahead of steep yen reversal",
        "url": "https://archive.fx-signal-reader.local/mof/2026-01-21-rate-check",
        "episode": "2026-01 Intervention Crisis",
    },
    {
        "published_utc": "2026-03-18T04:00:00Z",
        "source": "Reuters",
        "title": "Japan MoF Kato confirms rate check and threatens decisive action against rapid moves near 159.90",
        "url": "https://archive.fx-signal-reader.local/mof/2026-03-18-rate-check",
        "episode": "2026-03 High-Spot Escalation",
    },
    {
        "published_utc": "2026-04-29T08:00:00Z",
        "source": "Reuters",
        "title": "Japan Ministry of Finance stepped into market with physical yen intervention; bold action as USD/JPY hits 160.72",
        "url": "https://archive.fx-signal-reader.local/mof/2026-04-29-intervention",
        "episode": "2026-04 Flash Crash",
    },
    {
        "published_utc": "2026-04-30T10:00:00Z",
        "source": "Nikkei Asia",
        "title": "MoF currency diplomat Mimura says won't rule out any options after suspected intervention operations",
        "url": "https://archive.fx-signal-reader.local/mof/2026-04-30-mimura",
        "episode": "2026-04 Flash Crash",
    },
    {
        "published_utc": "2026-07-22T06:00:00Z",
        "source": "Reuters",
        "title": "Bank of Japan emergency rate check triggered as MoF warns of decisive action with USD/JPY surging above 163",
        "url": "https://archive.fx-signal-reader.local/mof/2026-07-22-rate-check",
        "episode": "2026-07 Intervention Crisis",
    },
    {
        "published_utc": "2026-07-28T06:00:00Z",
        "source": "Reuters",
        "title": "Japan MoF warns physical yen intervention operations are imminent; all options on the table near 163.95",
        "url": "https://archive.fx-signal-reader.local/mof/2026-07-28-warning",
        "episode": "2026-07 Intervention Crisis",
    },
    {
        "published_utc": "2026-07-30T04:00:00Z",
        "source": "Nikkei Asia",
        "title": "Japan MoF stepped into market with direct yen intervention as USD/JPY plunges nearly 580 pips from 163.74",
        "url": "https://archive.fx-signal-reader.local/mof/2026-07-30-intervention",
        "episode": "2026-07 Intervention Crisis",
    },
    {
        "published_utc": "2026-09-02T04:00:00Z",
        "source": "Reuters",
        "title": "Japan MoF threatens decisive action and won't rule out any options as USD/JPY breaches 160.39",
        "url": "https://archive.fx-signal-reader.local/mof/2026-09-02-warning",
        "episode": "2026-09 Intervention Crisis",
    },
    {
        "published_utc": "2026-09-29T06:00:00Z",
        "source": "Nikkei Asia",
        "title": "MoF currency chief warns of decisive action and heightened vigilance over speculative moves near 158",
        "url": "https://archive.fx-signal-reader.local/mof/2026-09-29-warning",
        "episode": "2026-09/10 Reversal",
    },
    {
        "published_utc": "2026-10-18T12:00:00Z",
        "source": "Reuters",
        "title": "Bank of Japan rate check chatter resurfaces as MoF warns of excessive volatility above 158",
        "url": "https://archive.fx-signal-reader.local/mof/2026-10-18-rate-check",
        "episode": "2026-09/10 Reversal",
    },
    {
        "published_utc": "2026-11-11T08:00:00Z",
        "source": "Reuters",
        "title": "Japan MoF Mimura warns decisive action and conducts rate check as USD/JPY pushes toward 159.94",
        "url": "https://archive.fx-signal-reader.local/mof/2026-11-11-rate-check",
        "episode": "2026-11 Barrier Crisis",
    },
    {
        "published_utc": "2026-11-15T18:00:00Z",
        "source": "Nikkei Asia",
        "title": "Ministry of Finance says all options on the table against disorderly one-sided moves approaching 160",
        "url": "https://archive.fx-signal-reader.local/mof/2026-11-15-warning",
        "episode": "2026-11 Barrier Crisis",
    },
    {
        "published_utc": "2026-11-17T18:00:00Z",
        "source": "Reuters",
        "title": "BoJ Ueda and MoF Mimura flag extremely high vigilance and rate check readiness above 159.4",
        "url": "https://archive.fx-signal-reader.local/mof/2026-11-17-rate-check",
        "episode": "2026-11 Barrier Crisis",
    },
)


def backfill_intervention_headlines(
    db_path: str | Path = DEFAULT_BACKTEST_DB_PATH,
    start_date: str | date | datetime | None = None,
    end_date: str | date | datetime | None = None,
    extra_headlines: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Score curated historical MoF/BoJ intervention headlines with `NewsRSSCollector`
    and insert them into SQLite `news_headlines` with deterministic SHA-256 hashes.
    """
    filter_by_window = start_date is not None or end_date is not None
    if filter_by_window:
        _, _, start_utc, end_utc, start_date_str, end_date_str = _resolve_date_range(
            start_date, end_date, interval="1d"
        )
    else:
        start_utc = None
        end_utc = None
        start_date_str = "2022-01-01"
        end_date_str = datetime.now().strftime("%Y-%m-%d")

    combined = list(CURATED_INTERVENTION_HEADLINES)
    if extra_headlines:
        combined.extend(dict(item) for item in extra_headlines)

    records_by_hash: dict[str, tuple[str, str, str, str, str, str]] = {}
    tier_counts: dict[str, int] = {"1": 0, "2": 0, "3": 0, "4": 0}
    episodes_seen: list[str] = []
    episodes_set: set[str] = set()

    for item in combined:
        raw_pub = str(item.get("published_utc") or "").strip()
        raw_source = str(item.get("source") or "Reuters").strip()
        raw_title = str(item.get("title") or "").strip()
        raw_url = str(item.get("url") or "").strip()
        if not (raw_pub and raw_title and raw_url):
            continue

        published_utc = normalize_datetime_utc(raw_pub, fallback_now=False)
        if filter_by_window and start_utc is not None and end_utc is not None:
            if not (start_utc <= published_utc <= end_utc):
                continue

        eval_res = _EVALUATOR.evaluate_headline(raw_title)
        matched_keywords = str(eval_res.get("matched_keywords") or "")
        url_hash = NewsRSSCollector.generate_url_hash(raw_url)
        records_by_hash[url_hash] = (
            url_hash,
            published_utc,
            raw_source,
            raw_title,
            raw_url,
            matched_keywords,
        )

        tier = int(eval_res.get("highest_tier_level") or 0)
        if str(tier) in tier_counts:
            tier_counts[str(tier)] += 1

        episode = item.get("episode")
        if episode:
            ep_str = str(episode)
            if ep_str not in episodes_set:
                episodes_set.add(ep_str)
                episodes_seen.append(ep_str)

    ordered_rows = sorted(records_by_hash.values(), key=lambda r: (r[1], r[0]))
    conn = _open_db_connection(db_path)
    try:
        conn.executemany(
            """
            INSERT OR REPLACE INTO news_headlines (
                url_hash, published_utc, source, title, url, matched_keywords
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ordered_rows,
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "SUCCESS" if ordered_rows else "EMPTY",
        "db_path": str(Path(db_path)),
        "start_date": start_date_str,
        "end_date": end_date_str,
        "rows_inserted": len(ordered_rows),
        "tier_counts": tier_counts,
        "episodes_covered": episodes_seen,
    }
