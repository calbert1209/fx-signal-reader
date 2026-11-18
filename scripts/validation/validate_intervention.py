#!/usr/bin/env python3
"""
Validation and Feed Curation Script for Japan MoF / BoJ Verbal Intervention Feeds.

Task: P1-04 - Intervention Feed Curation
Pipeline: USD/JPY Signal Reader

Key Capabilities:
1. Curates and verifies active RSS/Atom/RDF and official feeds for Bank of Japan (BoJ),
   Ministry of Finance (MoF), Nikkei Asia, FXStreet, MarketWatch, Investing.com, and Google News syndication.
2. Implements a 4-tier verbal intervention escalation ladder with comprehensive regexes and bilingual triggers.
3. Maps feed items into the SQLite `news_headlines` schema:
   - url_hash (SHA-256)
   - published_utc (ISO-8601 UTC)
   - source
   - title
   - url
   - matched_keywords (comma-separated)
4. Inserts into SQLite database with WAL mode and validates deduplication.
5. Runs test assertions against curated benchmark headlines (Tiers 1-4 and controls).
6. Exports structured results to docs/validation_results/intervention_validation_results.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

# -----------------------------------------------------------------------------
# Configuration & Feed Registry
# -----------------------------------------------------------------------------

DB_PATH = Path(os.getenv("FX_DB_PATH", "data/usdjpy_signals.db"))
SCHEMA_PATH = Path("fx_signal_reader/core/schema.sql")
OUTPUT_JSON = Path("docs/validation_results/intervention_validation_results.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, application/atom+xml, text/html, */*",
}

FEED_REGISTRY = [
    {
        "id": "boj_en_whatsnew",
        "name": "Bank of Japan Releases (English)",
        "url": "https://www.boj.or.jp/en/rss/whatsnew.xml",
        "source": "Bank of Japan",
        "category": "Central Bank Official",
        "type": "rss2",
    },
    {
        "id": "boj_ja_whatsnew",
        "name": "Bank of Japan Releases (Japanese)",
        "url": "https://www.boj.or.jp/rss/whatsnew.xml",
        "source": "Bank of Japan",
        "category": "Central Bank Official",
        "type": "rss2",
    },
    {
        "id": "fxstreet_news",
        "name": "FXStreet Breaking News",
        "url": "https://www.fxstreet.com/rss/news",
        "source": "FXStreet",
        "category": "FX Market Wire",
        "type": "rss2",
    },
    {
        "id": "investing_forex",
        "name": "Investing.com Forex News",
        "url": "https://www.investing.com/rss/forex.rss",
        "source": "Investing.com",
        "category": "FX Market Wire",
        "type": "rss2",
    },
    {
        "id": "marketwatch_realtime",
        "name": "MarketWatch Real-Time MarketPulse",
        "url": "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
        "source": "MarketWatch",
        "category": "Financial News Wire",
        "type": "rss2",
    },
    {
        "id": "nikkei_asia_nar",
        "name": "Nikkei Asia Primary Wire",
        "url": "https://asia.nikkei.com/rss/feed/nar",
        "source": "Nikkei Asia",
        "category": "Asian Markets & Policy",
        "type": "rss1_rdf",
    },
    {
        "id": "google_news_usdjpy",
        "name": "Google News USD/JPY & FX Wire",
        "url": "https://news.google.com/rss/search?q=USDJPY+OR+%22USD/JPY%22+OR+%22yen+intervention%22&hl=en-US&gl=US&ceid=US:en",
        "source": "Google News FX",
        "category": "Syndicated Media Wire",
        "type": "rss2",
    },
    {
        "id": "google_news_mof_boj",
        "name": "Google News MoF / BoJ Intervention Alert",
        "url": "https://news.google.com/rss/search?q=%22Bank+of+Japan%22+OR+%22Ministry+of+Finance%22+yen+intervention&hl=en-US&gl=US&ceid=US:en",
        "source": "Google News Alert",
        "category": "Syndicated Media Wire",
        "type": "rss2",
    },
    {
        "id": "google_news_reuters_yen",
        "name": "Reuters Yen & FX Wire (via Google News)",
        "url": "https://news.google.com/rss/search?q=site:reuters.com+yen+intervention&hl=en-US&gl=US&ceid=US:en",
        "source": "Reuters",
        "category": "Financial News Wire",
        "type": "rss2",
    },
    {
        "id": "mof_whatsnew_html",
        "name": "Ministry of Finance Official Releases",
        "url": "https://www.mof.go.jp/english/public_relations/whats_new/202611.html",
        "source": "Ministry of Finance Japan",
        "category": "Government Ministry Official",
        "type": "html_scraper",
    },
]

# -----------------------------------------------------------------------------
# Verbal Intervention Escalation Ladder & Keyword Taxonomy
# -----------------------------------------------------------------------------

ESCALATION_TIERS: Dict[str, Dict[str, Any]] = {
    "tier_4": {
        "level": 4,
        "name": "Tier 4: Imminent Physical Intervention / Rate Check",
        "severity": "CRITICAL",
        "description": "Concrete operational precursor or direct execution of foreign exchange market intervention.",
        "patterns": [
            (r"\brate check\b", "rate check"),
            (r"\bbold action\b", "bold action"),
            (r"\bdecisive (?:action|steps?|measures?)\b", "decisive action"),
            (r"\b(?:not|won['’]t) rule out any options?\b", "not rule out any options"),
            (r"\ball options (?:are )?on the table\b", "all options on table"),
            (r"\b(?:physical|direct|actual|suspected) (?:yen |fx |currency )?intervention\b", "physical/suspected intervention"),
            (r"\binterven(?:ed|ing)\b", "intervened"),
            (r"\bstep(?:s|ped)? in(?:to)? (?:the )?(?:currency|fx|market)\b", "stepped into market"),
            (r"\bfor(?:eign)? ex(?:change)? intervention operations?\b", "intervention operations"),
            (r"レートチェック", "レートチェック (rate check)"),
            (r"断固たる(?:措置|行動)", "断固たる措置 (decisive action)"),
            (r"あらゆる(?:手段|選択肢)を排除し(?:ない|ず)", "あらゆる手段を排除せず (all options on table)"),
            (r"為替(?:平衡操作|介入)", "為替介入 (FX intervention)"),
        ],
    },
    "tier_3": {
        "level": 3,
        "name": "Tier 3: Direct Warning & Readiness",
        "severity": "HIGH",
        "description": "Explicit warning of heightened readiness to counter disorderly movements.",
        "patterns": [
            (r"\b(?:yen |fx |currency |tokyo |mof )?intervention (?:risks?|threats?|warning|watch|talk)\b", "intervention risk/watch"),
            (r"\bhint(?:s|ed)? at intervention\b", "hints at intervention"),
            (r"\b(?:yen |fx |currency )?intervention\b", "intervention"),
            (r"\b(?:high|tense|extreme) sense of urgency\b", "tense sense of urgency"),
            (r"\bextremely high vigilance\b", "extremely high vigilance"),
            (r"\bexcessive (?:volatility|moves?|fluctuations?)\b", "excessive volatility"),
            (r"\bdisorderly\b", "disorderly"),
            (r"\bstand(?:s)? ready (?:to act|to intervene|to respond|to take)?\b", "stand ready"),
            (r"\btake appropriate (?:steps?|action|measures?)\b", "take appropriate steps"),
            (r"\bcannot tolerate\b", "cannot tolerate"),
            (r"\bwon['’]t tolerate\b", "won't tolerate"),
            (r"極めて高い緊張感", "極めて高い緊張感 (extreme sense of urgency)"),
            (r"過度な(?:変動|動き)", "過度な変動 (excessive volatility)"),
            (r"無秩序な動き|無秩序", "無秩序な動き (disorderly moves)"),
            (r"適切に対応", "適切に対応 (take appropriate steps)"),
            (r"準備がある", "準備がある (stand ready)"),
            (r"容認できない", "容認できない (cannot tolerate)"),
        ],
    },
    "tier_2": {
        "level": 2,
        "name": "Tier 2: Verbal Concern & Discomfort",
        "severity": "MEDIUM",
        "description": "Statements highlighting rapid, one-sided, or speculative movements not backed by fundamentals.",
        "patterns": [
            (r"\brapid (?:currency |fx |market )?(?:moves?|movements?|depreciation|weakening|fluctuations?)\b", "rapid moves"),
            (r"\bone-sided (?:currency |fx )?(?:moves?|movements?|depreciation|action)\b", "one-sided moves"),
            (r"\bone-sided\b", "one-sided"),
            (r"\bspeculative (?:yen |fx |currency |market )?(?:moves?|movements?|action|trading|positions?)\b", "speculative moves"),
            (r"\b(?:not|diverging from) reflect(?:ing)? fundamentals\b", "not reflecting fundamentals"),
            (r"\bwatch(?:ing)? with (?:a )?sense of urgency\b", "sense of urgency"),
            (r"\bheightened vigilance\b", "heightened vigilance"),
            (r"急激な(?:変動|動き)", "急激な変動 (rapid moves)"),
            (r"一方向(?:の動き)?", "一方向の動き (one-sided moves)"),
            (r"投機的な(?:動き|取引)", "投機的な動き (speculative moves)"),
            (r"ファンダメンタルズから乖離", "ファンダメンタルズから乖離 (diverging from fundamentals)"),
            (r"緊張感を持って注視", "緊張感を持って注視 (watching with sense of urgency)"),
        ],
    },
    "tier_1": {
        "level": 1,
        "name": "Tier 1: Monitoring & Routine Awareness",
        "severity": "LOW",
        "description": "Baseline official posture, watching market developments stably reflecting economic fundamentals.",
        "patterns": [
            (r"\bclosely monitor(?:ing)? (?:fx|currency|market|developments?)\b", "monitoring closely"),
            (r"\bwatch(?:ing)?\b.*?\b(?:closely|carefully)\b|\b(?:closely|carefully)\b.*?\bwatch(?:ing)?\b", "watching closely"),
            (r"\bdesirable (?:for currencies )?to move stably\b", "move stably"),
            (r"\bstable moves?\b", "stable moves"),
            (r"\breflecting fundamentals\b", "reflecting fundamentals"),
            (r"\brefrain from commenting on (?:fx|currency|levels?)\b", "refrain from commenting"),
            (r"\bno comment on (?:daily )?(?:exchange|levels?)\b", "no comment on levels"),
            (r"注視", "注視 (watching)"),
            (r"動向を注視", "動向を注視 (monitoring developments)"),
            (r"安定的に推移", "安定的に推移 (move stably)"),
            (r"コメントは差し控える", "コメントは差し控える (refrain from commenting)"),
        ],
    },
}

KEY_ENTITIES: Dict[str, List[Tuple[str, str]]] = {
    "institutions": [
        (r"\bMinistry of Finance\b", "Ministry of Finance"),
        (r"\bMoF\b", "MoF"),
        (r"\bBank of Japan\b", "Bank of Japan"),
        (r"\bBoJ\b", "BoJ"),
        (r"財務省", "財務省 (MoF)"),
        (r"日銀|日本銀行", "日銀 (BoJ)"),
    ],
    "officials": [
        (r"\b(?:Masato )?Kanda\b", "Kanda"),
        (r"\b(?:Atsushi )?Mimura\b", "Mimura"),
        (r"\b(?:Katsunobu )?Kato\b", "Kato"),
        (r"\b(?:Shunichi )?Suzuki\b", "Suzuki"),
        (r"\b(?:Kazuo )?Ueda\b", "Ueda"),
        (r"\bcurrency diplomat\b", "currency diplomat"),
        (r"\b(?:fx|currency) chief\b", "FX chief"),
        (r"\btop currency official\b", "top currency official"),
        (r"神田(?:真人)?", "神田 (Kanda)"),
        (r"三村(?:淳)?", "三村 (Mimura)"),
        (r"加藤(?:勝信)?", "加藤 (Kato)"),
        (r"鈴木(?:俊一)?", "鈴木 (Suzuki)"),
        (r"植田(?:和男)?", "植田 (Ueda)"),
        (r"財務官", "財務官 (Vice Minister for Int'l Affairs)"),
        (r"財務相", "財務相 (Finance Minister)"),
    ],
}

# -----------------------------------------------------------------------------
# Database Setup & Ingestion
# -----------------------------------------------------------------------------


def init_database(db_path: Path) -> sqlite3.Connection:
    """Initialize SQLite database in WAL mode according to schema.sql."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")

    if SCHEMA_PATH.exists():
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS news_headlines (
                url_hash TEXT PRIMARY KEY,
                published_utc TEXT NOT NULL,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                matched_keywords TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_news_time ON news_headlines(published_utc DESC);
            """
        )
    conn.commit()
    return conn


def generate_url_hash(url: str) -> str:
    """Compute SHA-256 hash of normalized URL."""
    clean_url = url.strip()
    return hashlib.sha256(clean_url.encode("utf-8")).hexdigest()


def normalize_datetime_utc(date_str: str) -> str:
    """Parse various RFC-822, ISO-8601, or custom dates into ISO-8601 UTC string."""
    if not date_str or not date_str.strip():
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        dt = date_parser.parse(date_str)
        if dt.tzinfo is None:
            # Default naive dates to UTC
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -----------------------------------------------------------------------------
# Keyword Matching Engine
# -----------------------------------------------------------------------------


def evaluate_headline(text: str) -> Dict[str, Any]:
    """
    Evaluate a headline against intervention escalation tiers and key actors.
    Returns matched keywords, matched entities, highest escalation tier, and severity.
    """
    matched_tier_keywords: List[str] = []
    matched_entities: List[str] = []
    highest_level = 0
    highest_tier_name = "None (Routine / Non-Intervention)"
    highest_severity = "NONE"

    # Evaluate escalation tiers
    for tier_key, tier_data in ESCALATION_TIERS.items():
        for pattern, label in tier_data["patterns"]:
            if re.search(pattern, text, re.IGNORECASE):
                if label not in matched_tier_keywords:
                    matched_tier_keywords.append(label)
                if tier_data["level"] > highest_level:
                    highest_level = tier_data["level"]
                    highest_tier_name = tier_data["name"]
                    highest_severity = tier_data["severity"]

    # Evaluate institutions and key officials
    for category, pattern_list in KEY_ENTITIES.items():
        for pattern, label in pattern_list:
            if re.search(pattern, text, re.IGNORECASE):
                if label not in matched_entities:
                    matched_entities.append(label)

    all_matched = matched_entities + matched_tier_keywords
    matched_keywords_str = ", ".join(all_matched) if all_matched else ""

    return {
        "highest_tier_level": highest_level,
        "highest_tier_name": highest_tier_name,
        "severity": highest_severity,
        "matched_entities": matched_entities,
        "matched_tier_keywords": matched_tier_keywords,
        "all_matched": all_matched,
        "matched_keywords_csv": matched_keywords_str,
    }


# -----------------------------------------------------------------------------
# Feed Fetching & Parsing
# -----------------------------------------------------------------------------


def fetch_and_parse_feed(feed_cfg: Dict[str, str]) -> Dict[str, Any]:
    """Fetch feed content and extract normalized headline entries."""
    feed_id = feed_cfg["id"]
    name = feed_cfg["name"]
    url = feed_cfg["url"]
    feed_type = feed_cfg["type"]
    default_src = feed_cfg["source"]

    start_time = time.time()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        elapsed_sec = round(time.time() - start_time, 3)
        status_code = resp.status_code
        content_length = len(resp.content)

        if status_code != 200:
            return {
                "id": feed_id,
                "name": name,
                "url": url,
                "status": f"HTTP {status_code}",
                "elapsed_sec": elapsed_sec,
                "byte_count": content_length,
                "items_count": 0,
                "items": [],
                "error": f"HTTP status {status_code}",
            }

        parsed_items: List[Dict[str, Any]] = []

        if feed_type in ("rss2", "rss1_rdf"):
            soup = BeautifulSoup(resp.content, "xml")
            raw_items = soup.find_all("item")
            if not raw_items:
                raw_items = soup.find_all("entry")

            for item in raw_items:
                # Title
                title_tag = item.find("title")
                title = title_tag.text.strip() if title_tag else ""

                # Link / URL
                link_tag = item.find("link")
                link = ""
                if link_tag:
                    link = (
                        link_tag.text.strip()
                        if link_tag.text
                        else link_tag.get("href", "")
                    )
                if not link and item.get("rdf:about"):
                    link = item.get("rdf:about")

                # Date
                pub_tag = (
                    item.find("pubDate")
                    or item.find("date")
                    or item.find("published")
                    or item.find("dc:date")
                )
                pub_raw = pub_tag.text.strip() if pub_tag else ""

                # Source
                source_tag = item.find("source")
                source = source_tag.text.strip() if source_tag else default_src

                if title and link:
                    url_hash = generate_url_hash(link)
                    pub_utc = normalize_datetime_utc(pub_raw)
                    eval_result = evaluate_headline(title)

                    parsed_items.append(
                        {
                            "url_hash": url_hash,
                            "published_utc": pub_utc,
                            "source": source,
                            "title": title,
                            "url": link,
                            "matched_keywords": eval_result[
                                "matched_keywords_csv"
                            ],
                            "highest_tier_level": eval_result[
                                "highest_tier_level"
                            ],
                            "highest_tier_name": eval_result[
                                "highest_tier_name"
                            ],
                            "severity": eval_result["severity"],
                        }
                    )

        elif feed_type == "html_scraper":
            # Scrape MoF What's New HTML page
            soup = BeautifulSoup(resp.content, "html.parser")
            # Anchor links with dates or releases
            for a in soup.find_all("a", href=True):
                txt = a.text.strip()
                href = a["href"]
                if not txt or len(txt) < 10:
                    continue
                if (
                    "htm" in href
                    and not href.startswith("#")
                    and "index" not in href
                ):
                    full_url = requests.compat.urljoin(url, href)
                    url_hash = generate_url_hash(full_url)
                    pub_utc = normalize_datetime_utc(
                        datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    )
                    eval_result = evaluate_headline(txt)
                    parsed_items.append(
                        {
                            "url_hash": url_hash,
                            "published_utc": pub_utc,
                            "source": default_src,
                            "title": txt,
                            "url": full_url,
                            "matched_keywords": eval_result[
                                "matched_keywords_csv"
                            ],
                            "highest_tier_level": eval_result[
                                "highest_tier_level"
                            ],
                            "highest_tier_name": eval_result[
                                "highest_tier_name"
                            ],
                            "severity": eval_result["severity"],
                        }
                    )

        return {
            "id": feed_id,
            "name": name,
            "url": url,
            "status": "ACTIVE (HTTP 200)",
            "elapsed_sec": elapsed_sec,
            "byte_count": content_length,
            "items_count": len(parsed_items),
            "items": parsed_items,
            "error": None,
        }

    except Exception as exc:
        return {
            "id": feed_id,
            "name": name,
            "url": url,
            "status": "ERROR",
            "elapsed_sec": round(time.time() - start_time, 3),
            "byte_count": 0,
            "items_count": 0,
            "items": [],
            "error": str(exc),
        }


# -----------------------------------------------------------------------------
# Test Benchmark Suite (Tiers 1 - 4 & Controls)
# -----------------------------------------------------------------------------

BENCHMARK_TEST_CASES = [
    {
        "id": "TC-01",
        "headline": "Bank of Japan conducts rate check with commercial bank FX dealers as USD/JPY touches 160.20",
        "expected_tier": 4,
        "must_match": ["Bank of Japan", "rate check"],
    },
    {
        "id": "TC-02",
        "headline": "Kanda warns Japan ready to take bold action against speculative yen moves",
        "expected_tier": 4,
        "must_match": ["Kanda", "bold action", "speculative moves"],
    },
    {
        "id": "TC-03",
        "headline": "Japan top currency diplomat Mimura says won't rule out any options to counter excessive volatility",
        "expected_tier": 4,
        "must_match": [
            "Mimura",
            "currency diplomat",
            "not rule out any options",
            "excessive volatility",
        ],
    },
    {
        "id": "TC-04",
        "headline": "Japan stands ready to take appropriate steps on disorderly FX market with tense sense of urgency",
        "expected_tier": 3,
        "must_match": [
            "stand ready",
            "take appropriate steps",
            "disorderly",
            "tense sense of urgency",
        ],
    },
    {
        "id": "TC-05",
        "headline": "Finance Minister Kato says government cannot tolerate excessive volatility in foreign exchange",
        "expected_tier": 3,
        "must_match": ["Kato", "cannot tolerate", "excessive volatility"],
    },
    {
        "id": "TC-06",
        "headline": "MoF's Mimura warns one-sided rapid currency movements undesirable",
        "expected_tier": 2,
        "must_match": ["MoF", "Mimura", "one-sided", "rapid moves"],
    },
    {
        "id": "TC-07",
        "headline": "BoJ closely monitoring speculative trading not reflecting fundamentals with heightened vigilance",
        "expected_tier": 2,
        "must_match": [
            "BoJ",
            "speculative moves",
            "not reflecting fundamentals",
            "heightened vigilance",
        ],
    },
    {
        "id": "TC-08",
        "headline": "Suzuki says watching FX moves carefully, desirable for currencies to move stably reflecting fundamentals",
        "expected_tier": 1,
        "must_match": [
            "Suzuki",
            "watching closely",
            "move stably",
            "reflecting fundamentals",
        ],
    },
    {
        "id": "TC-09",
        "headline": "神田財務官は為替市場で過度な変動があれば断固たる措置をとると表明",
        "expected_tier": 4,
        "must_match": ["神田 (Kanda)", "断固たる措置", "過度な変動"],
    },
    {
        "id": "TC-10",
        "headline": "三村財務官、為替相場における急激な変動や投機的な動きに極めて高い緊張感を持って注視",
        "expected_tier": 3,
        "must_match": [
            "三村 (Mimura)",
            "極めて高い緊張感",
            "急激な変動",
            "投機的な動き",
        ],
    },
    {
        "id": "TC-11",
        "headline": "Bank of Japan releases summary of opinions from October monetary policy meeting",
        "expected_tier": 0,
        "must_match": ["Bank of Japan"],
    },
    {
        "id": "TC-12",
        "headline": "US S&P 500 rallies 1.2% as tech earnings beat Wall Street estimates",
        "expected_tier": 0,
        "must_match": [],
    },
]


def run_benchmark_verification() -> List[Dict[str, Any]]:
    """Execute benchmark test suite to verify regex and escalation tier accuracy."""
    results = []
    for tc in BENCHMARK_TEST_CASES:
        eval_res = evaluate_headline(tc["headline"])
        matched_all = eval_res["all_matched"]
        level = eval_res["highest_tier_level"]

        tier_pass = level == tc["expected_tier"]
        missing_keywords = [
            kw
            for kw in tc["must_match"]
            if not any(kw.lower() in m.lower() for m in matched_all)
        ]
        match_pass = len(missing_keywords) == 0

        overall_pass = tier_pass and match_pass
        results.append(
            {
                "test_id": tc["id"],
                "headline": tc["headline"],
                "expected_tier": tc["expected_tier"],
                "detected_tier": level,
                "severity": eval_res["severity"],
                "matched_keywords": eval_res["matched_keywords_csv"],
                "passed": overall_pass,
                "missing_keywords": missing_keywords,
            }
        )
    return results


# -----------------------------------------------------------------------------
# Main Execution Flow
# -----------------------------------------------------------------------------


def main() -> int:
    execution_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"================================================================")
    print(f"USD/JPY Intervention Feed Curation & Validation Script (P1-04)")
    print(f"Execution Started (UTC): {execution_start}")
    print(f"================================================================\n")

    # Step 1: Benchmark Verification
    print(">>> [Step 1/4] Running Verbal Intervention Benchmark Tests...")
    benchmark_results = run_benchmark_verification()
    benchmarks_passed = sum(1 for b in benchmark_results if b["passed"])
    print(
        f"    Benchmark Accuracy: {benchmarks_passed} / {len(benchmark_results)} passed (100% required)"
    )
    for b in benchmark_results:
        status_mark = "✓" if b["passed"] else "✗"
        print(
            f"    [{status_mark}] {b['test_id']}: Tier {b['detected_tier']} ({b['severity']}) - {b['headline'][:65]}..."
        )
        if not b["passed"]:
            print(f"        FAILED! Missing: {b['missing_keywords']}")

    if benchmarks_passed != len(benchmark_results):
        print("ERROR: Benchmark verification failed. Aborting.")
        return 1

    # Step 2: Fetch and Parse Active Feeds
    print(
        f"\n>>> [Step 2/4] Polling & Parsing {len(FEED_REGISTRY)} Curated Feeds..."
    )
    feed_reports = []
    all_extracted_items: List[Dict[str, Any]] = []

    for cfg in FEED_REGISTRY:
        report = fetch_and_parse_feed(cfg)
        feed_reports.append(report)
        items = report["items"]
        all_extracted_items.extend(items)
        print(
            f"    [{report['status']:18}] {report['name']:38} | {report['items_count']:3} items | {report['elapsed_sec']:.2f}s"
        )

    print(f"\n    Total Items Extracted: {len(all_extracted_items)}")

    # Step 3: SQLite Persistence & Deduplication
    print(f"\n>>> [Step 3/4] Persisting into SQLite ({DB_PATH})...")
    conn = init_database(DB_PATH)
    cursor = conn.cursor()

    inserted_count = 0
    tier_counts = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}

    for item in all_extracted_items:
        cursor.execute(
            """
            INSERT OR REPLACE INTO news_headlines (
                url_hash, published_utc, source, title, url, matched_keywords
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                item["url_hash"],
                item["published_utc"],
                item["source"],
                item["title"],
                item["url"],
                item["matched_keywords"],
            ),
        )
        inserted_count += 1
        tier_counts[item["highest_tier_level"]] += 1

    conn.commit()

    # Query database validation metrics
    cursor.execute("SELECT count(*) FROM news_headlines")
    total_db_rows = cursor.fetchone()[0]

    cursor.execute(
        "SELECT count(*) FROM news_headlines WHERE matched_keywords IS NOT NULL AND matched_keywords != ''"
    )
    flagged_intervention_rows = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT source, count(*) 
        FROM news_headlines 
        GROUP BY source 
        ORDER BY count(*) DESC
        """
    )
    source_distribution = dict(cursor.fetchall())

    # Get sample matched intervention headlines from database
    cursor.execute(
        """
        SELECT published_utc, source, title, matched_keywords
        FROM news_headlines
        WHERE matched_keywords IS NOT NULL AND matched_keywords != ''
        ORDER BY published_utc DESC
        LIMIT 10
        """
    )
    sample_matches = [
        {
            "published_utc": r[0],
            "source": r[1],
            "title": r[2],
            "matched_keywords": r[3],
        }
        for r in cursor.fetchall()
    ]

    conn.close()

    print(f"    SQLite Total Rows: {total_db_rows}")
    print(f"    Headlines Flagged with Keywords: {flagged_intervention_rows}")
    print(f"    Escalation Tier Breakdown across Ingested Items:")
    print(
        f"      - Tier 4 (Imminent Action / Rate Check): {tier_counts[4]} items"
    )
    print(f"      - Tier 3 (Direct Warning / Readiness):  {tier_counts[3]} items")
    print(f"      - Tier 2 (Verbal Concern / Discomfort): {tier_counts[2]} items")
    print(
        f"      - Tier 1 (Routine Monitoring Posture):  {tier_counts[1]} items"
    )
    print(f"      - Tier 0 (General / Unflagged Macro):   {tier_counts[0]} items")

    # Step 4: Export Structured Results JSON
    print(
        f"\n>>> [Step 4/4] Generating Structured Artifact ({OUTPUT_JSON})..."
    )
    final_output = {
        "task_id": "P1-04",
        "task_title": "Intervention Feed Curation",
        "phase": "Phase 1: Data Source Mapping & API Setup",
        "execution_utc": execution_start,
        "database_path": str(DB_PATH),
        "benchmark_suite": {
            "total_cases": len(benchmark_results),
            "passed_cases": benchmarks_passed,
            "accuracy_pct": 100.0 * benchmarks_passed / len(benchmark_results),
            "details": benchmark_results,
        },
        "feed_health_matrix": [
            {
                "id": f["id"],
                "name": f["name"],
                "url": f["url"],
                "source": f.get("source"),
                "status": f["status"],
                "latency_sec": f["elapsed_sec"],
                "items_parsed": f["items_count"],
                "error": f["error"],
            }
            for f in feed_reports
        ],
        "database_metrics": {
            "total_headlines_ingested": total_db_rows,
            "flagged_intervention_headlines": flagged_intervention_rows,
            "tier_distribution": tier_counts,
            "source_distribution": source_distribution,
        },
        "sample_flagged_headlines": sample_matches,
    }

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(final_output, indent=2, ensure_ascii=False))
    print(f"    Export complete -> {OUTPUT_JSON}")
    print(f"\n================================================================")
    print(f"P1-04 Validation & Curation Successfully Completed.")
    print(f"================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
