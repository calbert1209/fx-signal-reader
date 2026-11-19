"""P8-04: multi-pair calendar filtering and ECB/EUR news ingestion (network fully mocked)."""

import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fx_signal_reader.collectors.calendar import CalendarCollector
from fx_signal_reader.collectors.news_rss import NewsRSSCollector

CALENDAR_ITEMS = [
    {"title": "ECB Main Refinancing Rate", "country": "EUR", "date": "2026-12-10T08:15:00-05:00",
     "impact": "High", "forecast": "2.15%", "previous": "2.15%"},
    {"title": "German Prelim CPI m/m", "country": "EUR", "date": "2026-12-01T08:00:00+01:00",
     "impact": "High", "forecast": "0.3%", "previous": "0.2%"},
    {"title": "French Flash Manufacturing PMI", "country": "EUR", "date": "2026-12-03T09:15:00+00:00",
     "impact": "Medium", "forecast": "", "previous": "48.0"},
    {"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-12-04T08:30:00-05:00",
     "impact": "High", "forecast": "150K", "previous": "140K"},
    {"title": "BOJ Policy Rate", "country": "JPY", "date": "2026-12-18T03:00:00+00:00",
     "impact": "High", "forecast": "0.75%", "previous": "0.50%"},
    {"title": "BOE Rate Decision", "country": "GBP", "date": "2026-12-17T12:00:00+00:00",
     "impact": "High"},
]


@pytest.fixture
def cal_kwargs(tmp_path):
    cache = tmp_path / "cal.json"
    cache.write_text(json.dumps(CALENDAR_ITEMS))
    return {
        "db_path": tmp_path / "t.db",
        "cache_path": cache,
        "max_retries": 1,
        "jitter": False,
    }


def _no_network(collector):
    """Live JSON fetch fails -> cache is used; any other fetch would raise."""
    collector.fetch_url = MagicMock(side_effect=RuntimeError("network disabled"))
    return collector


def _rows(db_path, table="macro_events"):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        conn.close()


class TestCalendarPairs:
    def test_default_is_usdjpy(self, cal_kwargs):
        c = _no_network(CalendarCollector(**cal_kwargs))
        assert c.pair == "USD/JPY"
        assert c.currencies == {"USD", "JPY"}
        s = c.collect()
        assert s["status"] == "SUCCESS"
        assert s["source_used"] == "cached_json"
        assert s["usd_jpy_events_inserted"] == 2
        assert (s["usd_events"], s["jpy_events"]) == (1, 1)
        assert list(s) == [
            "status", "source_used", "total_feed_events", "usd_jpy_events_inserted",
            "db_macro_events_rows", "usd_events", "jpy_events", "impact_distribution",
        ]
        assert {r[2] for r in _rows(cal_kwargs["db_path"])} == {"USD", "JPY"}

    def test_eurusd_extracts_eur_and_usd(self, cal_kwargs):
        c = _no_network(CalendarCollector(pair="EUR/USD", **cal_kwargs))
        s = c.collect()
        assert s["eur_usd_events_inserted"] == 4
        assert (s["eur_events"], s["usd_events"]) == (3, 1)
        assert "jpy_events" not in s
        rows = _rows(cal_kwargs["db_path"])
        assert {r[2] for r in rows} == {"EUR", "USD"}
        by_title = {r[3]: r for r in rows}
        # UTC normalisation
        assert by_title["ECB Main Refinancing Rate"][1] == "2026-12-10T13:15:00Z"
        assert by_title["German Prelim CPI m/m"][1] == "2026-12-01T07:00:00Z"
        assert by_title["French Flash Manufacturing PMI"][1] == "2026-12-03T09:15:00Z"
        assert by_title["French Flash Manufacturing PMI"][5] is None  # empty forecast

    def test_pair_name_variants_and_unknown(self, cal_kwargs):
        assert CalendarCollector(pair="eurusd", **cal_kwargs).pair == "EUR/USD"
        with pytest.raises(ValueError):
            CalendarCollector(pair="GBP/CHF", **cal_kwargs)

    def test_currencies_override_and_collect_arg(self, cal_kwargs):
        c = _no_network(CalendarCollector(currencies={"gbp"}, **cal_kwargs))
        assert c.currencies == {"GBP"}
        s = c.collect()
        assert s["gbp_events"] == 1
        c2 = _no_network(CalendarCollector(pair="EUR/USD", **cal_kwargs))
        s2 = c2.collect(currencies={"EUR"})
        assert s2["eur_events"] == 3 and "usd_events" not in s2

    def test_sha256_dedup_for_eur(self, cal_kwargs):
        c = _no_network(CalendarCollector(pair="EUR/USD", **cal_kwargs))
        c.collect()
        s = c.collect()
        assert s["db_macro_events_rows"] == 4
        h = CalendarCollector.generate_event_hash("2026-12-10T13:15:00Z", "eur", "ECB Main Refinancing Rate")
        assert h == CalendarCollector.generate_event_hash("2026-12-10T13:15:00Z", "EUR", " ECB Main Refinancing Rate ")
        assert len(h) == 32
        assert h in {r[0] for r in _rows(cal_kwargs["db_path"])}

    def test_shared_db_pairs_coexist(self, cal_kwargs):
        _no_network(CalendarCollector(**cal_kwargs)).collect()
        s = _no_network(CalendarCollector(pair="EUR/USD", **cal_kwargs)).collect()
        # USD event deduplicated, JPY retained, 3 EUR added
        assert s["db_macro_events_rows"] == 5


RSS_TMPL = """<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>"""


def _rss(*titles, prefix="x"):
    items = "".join(
        f"<item><title>{t}</title><link>https://example.com/{prefix}/{i}</link>"
        f"<pubDate>Wed, 10 Dec 2026 13:15:00 GMT</pubDate></item>"
        for i, t in enumerate(titles)
    )
    return RSS_TMPL.format(items=items).encode()


@pytest.fixture
def news_db(tmp_path):
    return tmp_path / "n.db"


class TestNewsPairs:
    def test_default_unchanged(self, news_db):
        n = NewsRSSCollector(db_path=news_db)
        assert n.pair == "USD/JPY"
        assert n.feed_registry is NewsRSSCollector.FEED_REGISTRY
        assert n.key_entities is NewsRSSCollector.KEY_ENTITIES
        ids = [f["id"] for f in NewsRSSCollector.FEED_REGISTRY]
        assert len(ids) == 10 and "ecb_press" not in ids
        assert set(NewsRSSCollector.ESCALATION_TIERS) == {"tier_1", "tier_2", "tier_3", "tier_4"}
        assert not any("Lagarde" in lbl for _, lbl in NewsRSSCollector.KEY_ENTITIES["officials"])

    @pytest.mark.parametrize(
        "text,level,entities,kw",
        [
            ("Kanda says rate check not ruled out, all options on the table", 4, ["Kanda"], ["rate check", "all options on table"]),
            ("財務官 三村 断固たる措置", 4, ["三村 (Mimura)", "財務官 (Vice Minister for Int'l Affairs)"], ["断固たる措置 (decisive action)"]),
            ("Ueda: BoJ closely monitoring FX developments", 1, ["BoJ", "Ueda"], ["monitoring closely"]),
            ("Lagarde hawkish on policy divergence", 0, [], []),
        ],
    )
    def test_usdjpy_evaluate_identical(self, news_db, text, level, entities, kw):
        default = NewsRSSCollector(db_path=news_db).evaluate_headline(text)
        explicit = NewsRSSCollector(db_path=news_db, pair="usdjpy").evaluate_headline(text)
        assert default == explicit
        assert default["highest_tier_level"] == level
        for e in entities:
            assert e in default["matched_entities"]
        for k in kw:
            assert k in default["matched_tier_keywords"]
        assert "matched_policy_keywords" not in default
        assert set(default) == {
            "highest_tier_level", "highest_tier_name", "severity", "matched_entities",
            "matched_tier_keywords", "all_matched", "matched_keywords",
        }

    def test_eurusd_registry(self, news_db):
        n = NewsRSSCollector(db_path=news_db, pair="EUR/USD")
        by_id = {f["id"]: f for f in n.feed_registry}
        assert by_id["ecb_press"]["url"] == "https://www.ecb.europa.eu/rss/press.html"
        assert "EUR/USD" in by_id["google_news_eurusd"]["url"]
        assert "ECB+rate+decision" in by_id["google_news_eurusd"]["url"]
        assert not any(i.startswith(("boj", "nikkei", "mof")) or "mof" in i for i in by_id)
        # class-level USD/JPY registry not mutated
        assert "ecb_press" not in {f["id"] for f in NewsRSSCollector.FEED_REGISTRY}

    @pytest.mark.parametrize(
        "text,label",
        [
            ("Lagarde signals pause", "Lagarde"),
            ("Isabel Schnabel warns on inflation", "Schnabel"),
            ("Luis de Guindos sees risks", "de Guindos"),
            ("Bundesbank's Joachim Nagel hawkish", "Nagel"),
            ("Villeroy de Galhau backs cut", "Villeroy"),
            ("ECB holds rates", "ECB"),
        ],
    )
    def test_eurusd_entities(self, news_db, text, label):
        res = NewsRSSCollector(db_path=news_db, pair="EUR/USD").evaluate_headline(text)
        assert label in res["matched_entities"]
        assert label in res["matched_keywords"]

    def test_eurusd_divergence_keywords(self, news_db):
        n = NewsRSSCollector(db_path=news_db, pair="EUR/USD")
        res = n.evaluate_headline("ECB/Fed policy divergence widens yield spread; euro dovish")
        assert "policy divergence" in res["matched_policy_keywords"]
        assert "yield differential" in res["matched_policy_keywords"]
        assert "hawkish/dovish" in res["matched_policy_keywords"]
        assert res["highest_tier_level"] == 0  # ladder not rewired
        assert "policy divergence" in res["matched_keywords"]

    def test_eurusd_collect_polls_feeds_mocked(self, news_db):
        n = NewsRSSCollector(db_path=news_db, pair="EUR/USD")
        calls = []

        def fake_fetch(url, *a, **kw):
            calls.append(url)
            if "ecb.europa.eu" in url:
                return SimpleNamespace(content=_rss("Lagarde: ECB rate decision announced", prefix="ecb"))
            return SimpleNamespace(content=_rss("EUR/USD steady", prefix=url))

        n.fetch_url = fake_fetch
        s = n.collect()
        assert s["status"] == "SUCCESS"
        assert s["feeds_polled"] == len(n.feed_registry) == 5
        assert "https://www.ecb.europa.eu/rss/press.html" in calls
        assert s["feed_stats"]["ecb_press"] == 1
        assert not any("boj.or.jp" in u or "mof.go.jp" in u for u in calls)
        rows = _rows(news_db, "news_headlines")
        assert any("Lagarde" in (r[5] or "") for r in rows), rows

    def test_feed_failure_is_tolerated(self, news_db):
        n = NewsRSSCollector(db_path=news_db, pair="EUR/USD")
        n.fetch_url = MagicMock(side_effect=RuntimeError("offline"))
        s = n.collect()
        assert s["total_items_processed"] == 0
        assert s["status"] == "SUCCESS"
