"""
News and Intervention RSS Collector for USD/JPY Signal Reader.
Ingests:
  - Bank of Japan official releases (English & Japanese)
  - Ministry of Finance Japan official press releases
  - Nikkei Asia, FXStreet, Investing.com, MarketWatch, Google News feeds
Applies:
  - 4-Tier Verbal Intervention Escalation Ladder regex engine
  - Entity recognition for Japanese policymakers (Mimura, Kanda, Kato, Suzuki, Ueda)
  - EUR/USD (``pair="EUR/USD"``): ECB press RSS, Google News EUR/USD feed, European
    policymaker entities (Lagarde, Schnabel, de Guindos, Nagel, Villeroy) and
    ECB/Fed policy-divergence keywords (feeds/entities only; the MoF ladder is unchanged)
Writes:
  - news_headlines table with deterministic SHA256 url_hash deduplication.
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from fx_signal_reader.collectors.base import BaseCollector
from fx_signal_reader.core.pair_profile import get_pair_profile


class NewsRSSCollector(BaseCollector):
    """
    Collector for official central bank/ministry feeds and FX news wires.
    """

    FEED_REGISTRY = [
        {
            "id": "boj_en_whatsnew",
            "name": "Bank of Japan Releases (English)",
            "url": "https://www.boj.or.jp/en/rss/whatsnew.xml",
            "source": "Bank of Japan",
            "type": "rss",
        },
        {
            "id": "boj_ja_whatsnew",
            "name": "Bank of Japan Releases (Japanese)",
            "url": "https://www.boj.or.jp/rss/whatsnew.xml",
            "source": "Bank of Japan",
            "type": "rss",
        },
        {
            "id": "fxstreet_news",
            "name": "FXStreet Breaking News",
            "url": "https://www.fxstreet.com/rss/news",
            "source": "FXStreet",
            "type": "rss",
        },
        {
            "id": "investing_forex",
            "name": "Investing.com Forex News",
            "url": "https://www.investing.com/rss/forex.rss",
            "source": "Investing.com",
            "type": "rss",
        },
        {
            "id": "marketwatch_realtime",
            "name": "MarketWatch Real-Time MarketPulse",
            "url": "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
            "source": "MarketWatch",
            "type": "rss",
        },
        {
            "id": "nikkei_asia_nar",
            "name": "Nikkei Asia Primary Wire",
            "url": "https://asia.nikkei.com/rss/feed/nar",
            "source": "Nikkei Asia",
            "type": "rss",
        },
        {
            "id": "google_news_usdjpy",
            "name": "Google News USD/JPY & FX Wire",
            "url": "https://news.google.com/rss/search?q=USDJPY+OR+%22USD/JPY%22+OR+%22yen+intervention%22&hl=en-US&gl=US&ceid=US:en",
            "source": "Google News FX",
            "type": "rss",
        },
        {
            "id": "google_news_mof_boj",
            "name": "Google News MoF / BoJ Intervention Alert",
            "url": "https://news.google.com/rss/search?q=%22Bank+of+Japan%22+OR+%22Ministry+of+Finance%22+yen+intervention&hl=en-US&gl=US&ceid=US:en",
            "source": "Google News Alert",
            "type": "rss",
        },
        {
            "id": "google_news_reuters_yen",
            "name": "Reuters Yen & FX Wire (via Google News)",
            "url": "https://news.google.com/rss/search?q=site:reuters.com+yen+intervention&hl=en-US&gl=US&ceid=US:en",
            "source": "Reuters",
            "type": "rss",
        },
        {
            "id": "mof_whatsnew_html",
            "name": "Ministry of Finance Official Releases",
            "url": "https://www.mof.go.jp/english/public_relations/whats_new/202611.html",
            "source": "Ministry of Finance Japan",
            "type": "html_scraper",
        },
    ]

    ESCALATION_TIERS: Dict[str, Dict[str, Any]] = {
        "tier_4": {
            "level": 4,
            "name": "Tier 4: Imminent Physical Intervention / Rate Check",
            "severity": "CRITICAL",
            "patterns": [
                (r"\brate check\b", "rate check"),
                (r"\bbold action\b", "bold action"),
                (r"\bdecisive (?:action|steps?|measures?)\b", "decisive action"),
                (r"\b(?:not|won['’]t) rule out any options?\b", "not rule out any options"),
                (r"\ball options (?:are )?on the table\b", "all options on table"),
                (
                    r"\b(?:physical|direct|actual|suspected) (?:yen |fx |currency )?intervention\b",
                    "physical/suspected intervention",
                ),
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

    # ------------------------------------------------------------------
    # EUR/USD additions (P8-04). The USD/JPY registries above are untouched.
    # ------------------------------------------------------------------
    # Pair-agnostic wires shared with the USD/JPY registry (by feed id).
    SHARED_FEED_IDS = ("fxstreet_news", "investing_forex", "marketwatch_realtime")

    EUR_FEED_REGISTRY = [
        {
            "id": "ecb_press",
            "name": "European Central Bank Official Press Releases",
            "url": "https://www.ecb.europa.eu/rss/press.html",
            "source": "European Central Bank",
            "type": "rss",
        },
        {
            "id": "google_news_eurusd",
            "name": "Google News EUR/USD & ECB Rate Decision",
            "url": "https://news.google.com/rss/search?q=EURUSD+OR+%22EUR/USD%22+OR+%22ECB+rate+decision%22&hl=en-US&gl=US&ceid=US:en",
            "source": "Google News FX",
            "type": "rss",
        },
    ]

    EUR_KEY_ENTITIES: Dict[str, List[Tuple[str, str]]] = {
        "institutions": [
            (r"\bEuropean Central Bank\b", "European Central Bank"),
            (r"\bECB\b", "ECB"),
            (r"\b(?:Federal Reserve|FOMC)\b", "Federal Reserve"),
        ],
        "officials": [
            (r"\b(?:Christine )?Lagarde\b", "Lagarde"),
            (r"\b(?:Isabel )?Schnabel\b", "Schnabel"),
            (r"\b(?:Luis )?de Guindos\b", "de Guindos"),
            (r"\b(?:Joachim )?Nagel\b", "Nagel"),
            (r"\b(?:Fran[cç]ois )?Villeroy(?: de Galhau)?\b", "Villeroy"),
            (r"\b(?:Jerome )?Powell\b", "Powell"),
        ],
    }

    # ECB/Fed policy-divergence keywords (informational; they do not change the tier).
    POLICY_DIVERGENCE_KEYWORDS: List[Tuple[str, str]] = [
        (r"\bpolicy divergence\b", "policy divergence"),
        (r"\b(?:ECB|Fed)[-/ ](?:Fed|ECB) divergence\b", "ECB/Fed divergence"),
        (r"\bdivergen(?:ce|t) (?:in |of )?(?:monetary |rate |interest rate )?(?:policy|policies|paths?|outlooks?|expectations)\b", "policy divergence"),
        (r"\b(?:ECB|Fed|central banks?) (?:diverg(?:e|es|ing|ed))\b", "central bank divergence"),
        (r"\brate (?:cut|hike|decision)s?\b", "rate decision"),
        (r"\b(?:hawkish|dovish)\b", "hawkish/dovish"),
        (r"\byield (?:spread|differential|gap)\b", "yield differential"),
        (r"\b(?:rate|interest rate) differentials?\b", "rate differential"),
        (r"\bdeposit (?:facility )?rate\b", "deposit facility rate"),
        (r"\bquantitative tightening\b", "quantitative tightening"),
    ]

    def __init__(self, *args: Any, pair: str = "USD/JPY", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pair_profile = get_pair_profile(pair)
        self.pair = self.pair_profile.pair
        if self.pair == "USD/JPY":
            # Identical to the historical class-level configuration.
            self.feed_registry = self.FEED_REGISTRY
            self.key_entities = self.KEY_ENTITIES
            self.policy_keywords: List[Tuple[str, str]] = []
        else:
            shared = [f for f in self.FEED_REGISTRY if f["id"] in self.SHARED_FEED_IDS]
            self.feed_registry = shared + self.EUR_FEED_REGISTRY
            self.key_entities = {
                cat: list(self.EUR_KEY_ENTITIES.get(cat, []))
                for cat in self.EUR_KEY_ENTITIES
            }
            self.policy_keywords = self.POLICY_DIVERGENCE_KEYWORDS

    @staticmethod
    def generate_url_hash(url: str) -> str:
        """Computes SHA-256 hash of URL."""
        return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()

    @staticmethod
    def normalize_datetime_utc(date_str: str) -> str:
        """Parses various date string formats into standard ISO-8601 UTC."""
        if not date_str or not date_str.strip():
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            dt = date_parser.parse(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def evaluate_headline(self, text: str) -> Dict[str, Any]:
        """
        Evaluates a headline against the 4-tier verbal intervention ladder and key entities.
        """
        matched_tier_keywords: List[str] = []
        matched_entities: List[str] = []
        highest_level = 0
        highest_tier_name = "None"
        highest_severity = "NONE"

        for tier_key, tier_data in self.ESCALATION_TIERS.items():
            for pattern, label in tier_data["patterns"]:
                if re.search(pattern, text, re.IGNORECASE):
                    if label not in matched_tier_keywords:
                        matched_tier_keywords.append(label)
                    if tier_data["level"] > highest_level:
                        highest_level = tier_data["level"]
                        highest_tier_name = tier_data["name"]
                        highest_severity = tier_data["severity"]

        for category, pattern_list in self.key_entities.items():
            for pattern, label in pattern_list:
                if re.search(pattern, text, re.IGNORECASE):
                    if label not in matched_entities:
                        matched_entities.append(label)

        matched_policy_keywords: List[str] = []
        for pattern, label in self.policy_keywords:
            if re.search(pattern, text, re.IGNORECASE):
                if label not in matched_policy_keywords:
                    matched_policy_keywords.append(label)

        all_matched = matched_entities + matched_tier_keywords + matched_policy_keywords
        matched_str = ", ".join(all_matched) if all_matched else ""

        result = {
            "highest_tier_level": highest_level,
            "highest_tier_name": highest_tier_name,
            "severity": highest_severity,
            "matched_entities": matched_entities,
            "matched_tier_keywords": matched_tier_keywords,
            "all_matched": all_matched,
            "matched_keywords": matched_str,
        }
        if self.policy_keywords:
            result["matched_policy_keywords"] = matched_policy_keywords
        return result

    def fetch_feed(self, feed_cfg: Dict[str, str]) -> List[Dict[str, Any]]:
        """
        Fetches and parses a single feed (RSS, Atom, or HTML scraper).
        """
        url = feed_cfg["url"]
        default_src = feed_cfg["source"]
        feed_type = feed_cfg.get("type", "rss")

        try:
            resp = self.fetch_url(url)
            items: List[Dict[str, Any]] = []

            if feed_type == "rss":
                soup = BeautifulSoup(resp.content, "xml")
                raw_items = soup.find_all("item")
                if not raw_items:
                    raw_items = soup.find_all("entry")

                for item in raw_items:
                    title_tag = item.find("title")
                    title = title_tag.text.strip() if title_tag else ""

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

                    pub_tag = (
                        item.find("pubDate")
                        or item.find("date")
                        or item.find("published")
                        or item.find("dc:date")
                    )
                    pub_raw = pub_tag.text.strip() if pub_tag else ""

                    source_tag = item.find("source")
                    source = (
                        source_tag.text.strip() if source_tag else default_src
                    )

                    if title and link:
                        url_hash = self.generate_url_hash(link)
                        pub_utc = self.normalize_datetime_utc(pub_raw)
                        eval_res = self.evaluate_headline(title)

                        items.append(
                            {
                                "url_hash": url_hash,
                                "published_utc": pub_utc,
                                "source": source,
                                "title": title,
                                "url": link,
                                "matched_keywords": eval_res[
                                    "matched_keywords"
                                ],
                                "tier": eval_res["highest_tier_level"],
                            }
                        )

            elif feed_type == "html_scraper":
                soup = BeautifulSoup(resp.content, "html.parser")
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
                        url_hash = self.generate_url_hash(full_url)
                        pub_utc = self.normalize_datetime_utc(
                            datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        )
                        eval_res = self.evaluate_headline(txt)
                        items.append(
                            {
                                "url_hash": url_hash,
                                "published_utc": pub_utc,
                                "source": default_src,
                                "title": txt,
                                "url": full_url,
                                "matched_keywords": eval_res[
                                    "matched_keywords"
                                ],
                                "tier": eval_res["highest_tier_level"],
                            }
                        )

            return items
        except Exception as exc:
            self.logger.warning(
                f"Failed to fetch/parse feed {feed_cfg.get('id', url)}: {exc}"
            )
            return []

    def collect(self) -> Dict[str, Any]:
        """
        Polls all registered feeds, applies verbal intervention engine,
        and saves items to `news_headlines`.
        """
        conn = self.get_connection()
        total_items: List[Dict[str, Any]] = []
        feed_stats: Dict[str, int] = {}
        tier_counts: Dict[int, int] = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}

        for feed_cfg in self.feed_registry:
            feed_id = feed_cfg["id"]
            self.logger.info(f"Polling feed: {feed_cfg['name']} ({feed_id})")
            items = self.fetch_feed(feed_cfg)
            feed_stats[feed_id] = len(items)
            total_items.extend(items)

        # Batch insert with URL hash deduplication
        conn.executemany(
            """
            INSERT OR REPLACE INTO news_headlines
            (url_hash, published_utc, source, title, url, matched_keywords)
            VALUES (:url_hash, :published_utc, :source, :title, :url, :matched_keywords)
            """,
            total_items,
        )
        conn.commit()

        # Count intervention matches
        for it in total_items:
            tier_counts[it["tier"]] = tier_counts.get(it["tier"], 0) + 1

        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM news_headlines")
        total_in_db = cur.fetchone()[0]

        cur.execute(
            "SELECT count(*) FROM news_headlines WHERE matched_keywords IS NOT NULL AND matched_keywords != ''"
        )
        matched_in_db = cur.fetchone()[0]
        conn.close()

        summary = {
            "status": "SUCCESS",
            "feeds_polled": len(self.feed_registry),
            "feed_stats": feed_stats,
            "total_items_processed": len(total_items),
            "db_news_headlines_rows": total_in_db,
            "flagged_intervention_rows": matched_in_db,
            "tier_distribution": tier_counts,
        }
        self.logger.info(
            f"News RSS collection complete: {len(total_items)} processed, "
            f"{total_in_db} total in DB, {matched_in_db} intervention alerts flagged."
        )
        return summary
