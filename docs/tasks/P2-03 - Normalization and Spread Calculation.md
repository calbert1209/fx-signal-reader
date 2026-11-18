# P2-03: Normalization & spread calculation

* **Phase:** Phase 2: Collection Architecture & Local Persistence
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T08:31:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Establish a unified normalization, deduplication, and financial calculation layer for the USD/JPY automated signal pipeline. Specifically:
1. Enforce strict ISO-8601 UTC timestamps (`YYYY-MM-DDTHH:MM:SSZ`) across all incoming data streams (market prices, macro calendar events, news headlines, and bond yields).
2. Generate deterministic, collision-resistant primary keys for SQLite tables: 64-hex SHA-256 `url_hash` for `news_headlines`, and 32-hex SHA-256 `event_hash` for `macro_events`.
3. Calculate the core engine of USD/JPY valuation: the 10-Year US–Japan sovereign yield spread (`spread_10y = round(us_10y - jp_10y, 4)`), basis points (`spread_bps`), and curve slope (`US 2s10s = round(us_10y - us_2y, 4)`).
4. Parse multi-source sovereign yields from FRED (`DGS10`, `DGS2`), Yahoo Finance (`^TNX`), and Japan Ministry of Finance (MoF `jgbcm.csv`).
5. Resolve calendar misalignment, trading hour gaps, and mismatched market holidays between US and Japanese bond markets via forward-fill (`ffill`) and staleness guardrails.

---

## 2. Implementation Notes & Acceptance Criteria

- [x] Implementation complete (`normalizer.py` and `spread_calculator.py` deployed to workspace root)
- [x] ISO-8601 UTC standardization (`YYYY-MM-DDTHH:MM:SSZ`) handling datetime objects, date objects, numeric epoch timestamps (seconds and milliseconds), ISO strings with arbitrary timezone offsets (`+09:00`, `-05:00`), RFC-2822 RSS timestamps, Forex Factory date/time combos, and Japanese Imperial Era dates (e.g., `R8.11.17` -> `2026-11-17T00:00:00Z`)
- [x] Deterministic SHA-256 deduplication hashing for `url_hash` (64 lowercase hex characters) and `event_hash` (32 lowercase hex characters from `timestamp_utc:CURRENCY:title`)
- [x] Yield spread computation (`spread_10y = round(us_10y - jp_10y, 4)`), basis points conversion, and US 2s10s curve slope (`round(us_10y - us_2y, 4)`) with robust None, NaN, and Inf handling
- [x] Multi-source ingestion parsing: FRED REST API observations (`.` holiday handling), Yahoo Finance `^TNX` quotes/candles, and MoF daily CSV tables
- [x] Bond market holiday and publication misalignment resolution with forward-filling (`ffill`) and configurable lookback bounds (`max_stale_days=5`)
- [x] Comprehensive unit test suite in `tests/test_normalization.py` with 100% pass rate (28/28 tests passed)
- [x] Linked artifacts and tracking records updated (Google Drive doc `108OdGTy7vQtYH7ZUr6uIG1dQhw7_0oVY` and Google Sheet tracker row 8)

---

## 3. Execution Log & Findings

### 3.1 Architecture Overview

The normalization and calculation layer consists of two modular libraries accessible across all ingestion collectors, analytical agents, and MCP server tools:

```text
Incoming Raw Data Sources
 ├── Yahoo Finance (JPY=X, ^TNX, ^GSPC, ^N225, ^VIX, GC=F)
 ├── FRED REST API (DGS10, DGS2)
 ├── Japan Ministry of Finance (MoF daily JGB CSV)
 ├── Forex Factory (Weekly JSON / XML macro calendar)
 └── Wire & Institutional RSS (Reuters, Nikkei, BoJ, MoF)
               │
               ▼
┌────────────────────────────────────────────────────────┐
│                      normalizer.py                     │
│  - normalize_datetime_utc() [YYYY-MM-DDTHH:MM:SSZ]     │
│  - parse_japanese_era_date() [R8.11.17 -> 2026-11-17]   │
│  - parse_event_datetime() [FF date + 12h time / all-day]│
│  - generate_url_hash() [64 hex SHA-256]                │
│  - generate_event_hash() [32 hex SHA-256]              │
│  - is_valid_iso8601_utc() & parse_float_or_none()      │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│                  spread_calculator.py                  │
│  - compute_yield_spread(us_10y, jp_10y, precision=4)   │
│  - compute_spread_bps(spread, precision=2)             │
│  - compute_us_2s10s_slope(us_10y, us_2y, precision=4) │
│  - parse_fred_yield() & extract_fred_series()          │
│  - parse_yahoo_tnx() & parse_mof_csv()                 │
│  - align_yield_series(ffill, max_stale_days=5)         │
│  - calculate_live_spread(lag check, staleness flags)   │
│  - save_yield_spread(conn, record)                     │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│               Local SQLite (WAL Mode)                  │
│  - market_ticks  (symbol, timestamp_utc)               │
│  - yield_spreads (timestamp_utc, us_10y, jp_10y, ...)  │
│  - macro_events  (event_hash, timestamp_utc, ...)      │
│  - news_headlines (url_hash, published_utc, ...)       │
└────────────────────────────────────────────────────────┘
```

---

### 3.2 Timestamp Normalization Engine (`normalizer.py`)

All database timestamps require strict ISO-8601 UTC representation (`YYYY-MM-DDTHH:MM:SSZ`). The normalization function `normalize_datetime_utc` supports:
1. **Timezone-aware Datetimes:** Converted directly to UTC (`dt.astimezone(timezone.utc)`).
2. **Timezone-naive Datetimes:** Defaults to UTC unless an explicit `default_timezone` is passed.
3. **Calendar Dates (`datetime.date`):** Converted to midnight UTC (`YYYY-MM-DDT00:00:00Z`).
4. **Epoch Timestamps (int/float):** Detects second vs millisecond granularity (> 1e11) and converts to UTC string.
5. **String Offsets:** Accurately calculates offsets (e.g., `2026-11-18T15:00:00+09:00` -> `2026-11-18T06:00:00Z`; `2026-11-18T01:00:00-05:00` -> `2026-11-18T06:00:00Z`).
6. **RFC-2822 / RSS Strings:** Parses HTTP/RSS dates (e.g., `Wed, 18 Nov 2026 15:00:00 +0900` -> `2026-11-18T06:00:00Z`).
7. **Japanese Imperial Era Dates:** Japan MoF publishes yields with era notation (`R<Year>.<Month>.<Day>`). `parse_japanese_era_date` converts Reiwa (`R8.11.17` -> `2026-11-17`), Heisei (`H30.12.31` -> `2018-12-31`), and Showa (`S60.1.1` -> `1985-01-01`).
8. **Forex Factory Times:** `parse_event_datetime` handles 12-hour AM/PM times (`11:50pm`) as well as non-timed events (`All Day`, `Tentative`) defaulting to `00:00:00Z`.

---

### 3.3 Deterministic Deduplication Hashing (`normalizer.py`)

Primary keys for non-market tables must prevent duplicate rows when collectors re-poll feeds:
* **`url_hash`:**
  * Formula: `sha256(clean_url.encode("utf-8")).hexdigest().lower()`
  * Output: 64 hexadecimal characters.
  * Ensures immutable, collision-resistant primary keys in `news_headlines`.
* **`event_hash`:**
  * Formula: `sha256(f"{norm_ts}:{currency.upper()}:{title}".encode("utf-8")).hexdigest()[:32].lower()`
  * Output: 32 hexadecimal characters.
  * Formatted consistently with task P1-03 specification and ensures unique macro event records in `macro_events`.

---

### 3.4 Yield Spread & Curve Slope Engine (`spread_calculator.py`)

1. **10-Year Spread Formula:**
   $$\text{spread\_10y} = \text{round}(\text{US10Y} - \text{JGB10Y}, 4)$$
   $$\text{spread\_bps} = \text{round}(\text{spread\_10y} \times 100, 2)$$
   * Live Example: $5.1030\% - 2.9250\% = +2.1780\%$ ($+217.80$ bps).
2. **Curve Slope Formula (US 2s10s):**
   $$\text{slope\_2s10s} = \text{round}(\text{US10Y} - \text{US2Y}, 4)$$
   * Live Example: $5.1200\% - 4.8300\% = +0.2900\%$ ($+29.00$ bps, upward sloping).
3. **Robustness Guardrails:**
   * Returns `None` if either leg is `None`, `NaN`, `+Inf`, `-Inf`, or unparseable.
   * Prevents database corruption or silent NaN propagation.

---

### 3.5 Multi-Source Yield Ingestion Parsers

| Source | Target Series | Ingestion Format | Missing/Holiday Marker | Parsed Output |
| :--- | :--- | :--- | :--- | :--- |
| **FRED REST API** | `DGS10`, `DGS2` | JSON observation objects: `{"date": "...", "value": "5.12"}` | `value: "."` | `5.12` (float) or `None` |
| **Yahoo Finance** | `^TNX` | Scalar float or candle dictionary: `{"Close": 5.103}` | Empty / NaN | `5.103` (float) |
| **Japan MoF** | JGB 10Y Benchmark | CSV: `jgbcm.csv` (`基準日`, `10年`) with `R8.11.17` | Non-trading day omitted | `2.925` (float) with ISO date |

---

### 3.6 Holiday & Market Misalignment Resolution

US and Japanese bond markets do not share trading calendars:
* **Japanese Holidays (e.g., Golden Week, Respect for the Aged Day):** Tokyo market closed, MoF publishes no new CSV row.
* **US Holidays (e.g., Thanksgiving, Martin Luther King Day):** US Treasury market closed, FRED outputs `"."`.
* **Trading Hour Lag:** Tokyo closes at ~15:00 JST (06:00 UTC), MoF publishes at ~17:00 JST (08:00 UTC), whereas US cash markets trade until 17:00 EST (22:00 UTC).

#### Resolution Mechanics:
1. **`align_yield_series`:**
   * Takes the union of all calendar dates across US and Japanese historical records.
   * Employs forward-fill (`ffill`): carries forward the prior valid published yield for up to `max_stale_days` (default: 5 calendar days, covering long holiday weekends).
   * Annotates records with `is_us_stale`, `is_jp_stale`, and source observation dates.
   * If a market is closed beyond `max_stale_days`, the spread drops to `None` with an explanatory staleness note.
2. **`calculate_live_spread`:**
   * Evaluates timestamp lag between real-time quotes. If the time delta exceeds `max_misalignment_hours` (default: 96 hours), flags the stale leg while preserving spread computation.

---

### 3.7 Unit Test Verification Suite (`tests/test_normalization.py`)

A comprehensive 28-test suite was implemented and verified using `.venv/bin/python -m unittest -v tests/test_normalization.py`:

```text
test_save_and_retrieve_spread_record (tests.test_normalization.TestDatabasePersistence.test_save_and_retrieve_spread_record) ... ok
test_event_hash_collision_resistance (tests.test_normalization.TestDeduplicationHashing.test_event_hash_collision_resistance) ... ok
test_event_hash_format_and_repeatability (tests.test_normalization.TestDeduplicationHashing.test_event_hash_format_and_repeatability) ... ok
test_event_hash_validation (tests.test_normalization.TestDeduplicationHashing.test_event_hash_validation) ... ok
test_url_hash_collision_resistance (tests.test_normalization.TestDeduplicationHashing.test_url_hash_collision_resistance) ... ok
test_url_hash_format_and_repeatability (tests.test_normalization.TestDeduplicationHashing.test_url_hash_format_and_repeatability) ... ok
test_url_hash_validation (tests.test_normalization.TestDeduplicationHashing.test_url_hash_validation) ... ok
test_forward_fill_on_holiday_misalignment (tests.test_normalization.TestHolidayAndMisalignmentHandling.test_forward_fill_on_holiday_misalignment) ... ok
test_live_spread_calculation (tests.test_normalization.TestHolidayAndMisalignmentHandling.test_live_spread_calculation) ... ok
test_max_stale_days_cutoff (tests.test_normalization.TestHolidayAndMisalignmentHandling.test_max_stale_days_cutoff) ... ok
test_extract_fred_series (tests.test_normalization.TestMultiSourceParsers.test_extract_fred_series) ... ok
test_fred_yield_parser (tests.test_normalization.TestMultiSourceParsers.test_fred_yield_parser) ... ok
test_mof_csv_parser (tests.test_normalization.TestMultiSourceParsers.test_mof_csv_parser) ... ok
test_yahoo_tnx_parser (tests.test_normalization.TestMultiSourceParsers.test_yahoo_tnx_parser) ... ok
test_curve_slope_calculation (tests.test_normalization.TestSpreadCalculation.test_curve_slope_calculation) ... ok
test_none_and_nan_handling (tests.test_normalization.TestSpreadCalculation.test_none_and_nan_handling) ... ok
test_precision_and_rounding (tests.test_normalization.TestSpreadCalculation.test_precision_and_rounding) ... ok
test_standard_spread_calculation (tests.test_normalization.TestSpreadCalculation.test_standard_spread_calculation) ... ok
test_date_object_conversion (tests.test_normalization.TestTimestampNormalization.test_date_object_conversion) ... ok
test_datetime_object_conversion (tests.test_normalization.TestTimestampNormalization.test_datetime_object_conversion) ... ok
test_epoch_timestamp_conversion (tests.test_normalization.TestTimestampNormalization.test_epoch_timestamp_conversion) ... ok
test_forex_factory_event_datetime (tests.test_normalization.TestTimestampNormalization.test_forex_factory_event_datetime) ... ok
test_japanese_era_dates (tests.test_normalization.TestTimestampNormalization.test_japanese_era_dates) ... ok
test_none_and_invalid_timestamp_handling (tests.test_normalization.TestTimestampNormalization.test_none_and_invalid_timestamp_handling) ... ok
test_rfc2822_strings (tests.test_normalization.TestTimestampNormalization.test_rfc2822_strings) ... ok
test_strict_iso8601_utc_invalid (tests.test_normalization.TestTimestampNormalization.test_strict_iso8601_utc_invalid) ... ok
test_strict_iso8601_utc_valid (tests.test_normalization.TestTimestampNormalization.test_strict_iso8601_utc_valid) ... ok
test_string_timezone_offsets (tests.test_normalization.TestTimestampNormalization.test_string_timezone_offsets) ... ok

----------------------------------------------------------------------
Ran 28 tests in 0.006s

OK (100% pass rate)
```
