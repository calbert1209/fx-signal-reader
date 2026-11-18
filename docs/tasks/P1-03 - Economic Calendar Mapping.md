# P1-03: Economic calendar mapping

* **Phase:** Phase 1: Data Source Mapping & API Setup
* **Status:** `DONE`
* **Last Updated (UTC):** `2026-11-18T07:56:00Z`
* **Architecture Plan:** [USD/JPY Signal Pipeline Plan.md](https://drive.google.com/file/d/1exIpR_gzlCxcdZuZDuY20ZtqY47ifkUb/view)

---

## 1. Objective & Scope

Fetch the Forex Factory weekly calendar feed (JSON primary, with XML/CSV fallback) and map fields into the SQLite `macro_events` table schema for scheduled timestamp, currency (`USD` and `JPY`), impact tier, forecast, previous, and actual figures.

## 2. Implementation Notes & Acceptance Criteria

Filter events to `USD` and `JPY` currencies and map fields into the `macro_events` table schema (`event_hash`, `timestamp_utc`, `currency`, `title`, `impact`, `forecast`, `previous`, `actual`).

- [x] Implementation complete (`validate_calendar.py` executed in `.venv` environment)
- [x] Verified live Forex Factory feed ingestion, HTTP caching, and Cloudflare rate-limit behavior
- [x] Filtered events strictly to `USD` and `JPY` (53 relevant events identified out of 125 total weekly events)
- [x] Normalized event timestamps to ISO-8601 UTC and generated deterministic SHA256 hashes (`event_hash`)
- [x] Validated schema mapping, type conversion, and deduplication via in-memory SQLite table insertion
- [x] Linked artifacts and structured output logs updated (`calendar_validation_results.json`)

---

## 3. Execution Log & Findings

### 3.1 Feed Discovery & Rate-Limit Profile

1. **Feed Endpoints:**
   * **Primary JSON Feed:** `https://nfs.faireconomy.media/ff_calendar_thisweek.json`
   * **Fallback XML Feed:** `https://nfs.faireconomy.media/ff_calendar_thisweek.xml`
   * **Fallback CSV Feed:** `https://nfs.faireconomy.media/ff_calendar_thisweek.csv`

2. **Upstream Rate Limiting Policy:**
   * **Rate Limit Gate:** Faireconomy enforces a strict Cloudflare rate-limit on calendar export endpoints: requests exceeding 1 request per 5 minutes per IP return `HTTP 429 Too Many Requests` with `Retry-After: ~300` and message:
     > *"You've exceeded the limit for Calendar Export requests. Please wait five minutes before trying again. The Calendar export file is only updated once per hour. Requesting it more than that is unnecessary and can result in being blocked."*
   * **Ingestion Architectural Decision:**
     * The ingestion collector (`collector.py` / cron) must cache the weekly calendar locally (`data/raw_calendar_thisweek.json`).
     * Ingestion frequency should be scheduled **once every 1 to 4 hours**, or daily at `00:05 UTC`, well within the 1-hour upstream update cycle.
     * The ingestion client must handle `HTTP 429` with exponential backoff and rely on the local cache if transient rate limits occur.

---

### 3.2 Weekly Feed Ingestion Breakdown (Week of Nov 15–21, 2026)

* **Total Events in Feed:** 125 events across 8 major currencies
* **Currency Distribution:**
  * `USD`: 46 events (36.8%)
  * `GBP`: 17 events (13.6%)
  * `EUR`: 16 events (12.8%)
  * `CAD`: 13 events (10.4%)
  * `NZD`: 11 events (8.8%)
  * `CNY`: 9 events (7.2%)
  * `JPY`: 7 events (5.6%)
  * `AUD`: 6 events (4.8%)
* **USD & JPY Target Events:** **53 events** (46 USD, 7 JPY)
* **USD & JPY Impact Breakdown:**
  * **High:** 4 events
  * **Medium:** 4 events
  * **Low:** 45 events

---

### 3.3 Schema Mapping Specification

The fields from the Forex Factory feed map directly into the `macro_events` SQLite table:

| SQLite Column | SQLite Type | Source Feed Field | Transformation & Normalization Rules | Sample Value |
| :--- | :--- | :--- | :--- | :--- |
| `event_hash` | `TEXT PRIMARY KEY` | Composite (`date`, `country`, `title`) | `sha256(f"{timestamp_utc}:{currency}:{title}").hexdigest()[:32]` | `5ffb1b3465405a57aa750e037611f623` |
| `timestamp_utc` | `TEXT NOT NULL` | `date` (`YYYY-MM-DDTHH:MM:SS-05:00`) | Parsed via `datetime.fromisoformat` and converted to UTC (`astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")`). Tentative/all-day events default to `T00:00:00Z`. | `2026-11-17T13:30:00Z` |
| `currency` | `TEXT NOT NULL` | `country` | `country.strip().upper()`, filtered to `USD` and `JPY`. | `USD` |
| `title` | `TEXT NOT NULL` | `title` | `title.strip()` | `Core Retail Sales m/m` |
| `impact` | `TEXT` | `impact` | Normalized to `High`, `Medium`, `Low`, or `Non-Economic` (for holidays/tentative non-data releases). | `High` |
| `forecast` | `TEXT` | `forecast` | Preserved as string (with units `%`, `B`, `K`, `T`). Empty string `""` coerced to `NULL`. | `0.4%` |
| `previous` | `TEXT` | `previous` | Preserved as string. Empty string `""` coerced to `NULL`. | `0.9%` |
| `actual` | `TEXT` | `actual` | Preserved as string once released. Unreleased/future events coerced to `NULL`. | `NULL` |

---

### 3.4 Verified High-Impact Events & JPY Data Releases

#### High-Impact USD Events (Week of Nov 15–21, 2026)
1. **`2026-11-16T17:00:00Z`** | `USD` | **Fed Chairman Warsh Speaks** | Impact: `High` | Forecast: `None` | Prev: `None`
2. **`2026-11-17T13:30:00Z`** | `USD` | **Core Retail Sales m/m** | Impact: `High` | Forecast: `0.4%` | Prev: `0.9%`
3. **`2026-11-17T13:30:00Z`** | `USD` | **Retail Sales m/m** | Impact: `High` | Forecast: `0.1%` | Prev: `0.8%`
4. **`2026-11-18T19:00:00Z`** | `USD` | **FOMC Meeting Minutes** | Impact: `High` | Forecast: `None` | Prev: `None`

#### JPY Releases (Week of Nov 15–21, 2026)
1. **`2026-11-15T23:50:00Z`** (`11-16 08:50 JST`) | `JPY` | **Prelim GDP Price Index y/y** | Impact: `Low` | Forecast: `2.6%` | Prev: `2.6%`
2. **`2026-11-15T23:50:00Z`** (`11-16 08:50 JST`) | `JPY` | **Prelim GDP q/q** | Impact: `Low` | Forecast: `0.1%` | Prev: `0.3%`
3. **`2026-11-16T04:30:00Z`** (`11-16 13:30 JST`) | `JPY` | **Revised Industrial Production m/m** | Impact: `Low` | Forecast: `2.2%` | Prev: `2.2%`
4. **`2026-11-17T04:30:00Z`** (`11-17 13:30 JST`) | `JPY` | **Tertiary Industry Activity m/m** | Impact: `Low` | Forecast: `0.1%` | Prev: `-0.1%`
5. **`2026-11-17T23:50:00Z`** (`11-18 08:50 JST`) | `JPY` | **Trade Balance** | Impact: `Low` | Forecast: `-0.90T` | Prev: `-0.70T`
6. **`2026-11-19T23:30:00Z`** (`11-20 08:30 JST`) | `JPY` | **National Core CPI y/y** | Impact: `Medium` | Forecast: `2.8%` | Prev: `2.8%`
7. **`2026-11-19T23:30:00Z`** (`11-20 08:30 JST`) | `JPY` | **National CPI y/y** | Impact: `Low` | Forecast: `2.9%` | Prev: `2.9%`

---

### 3.5 Raw JSON Sample vs. Normalized SQLite Record

#### Raw JSON Sample from Feed:
```json
{
  "title": "Core Retail Sales m/m",
  "country": "USD",
  "date": "2026-11-17T08:30:00-05:00",
  "impact": "High",
  "forecast": "0.4%",
  "previous": "0.9%"
}
```

#### Normalized Record in SQLite `macro_events`:
```json
{
  "event_hash": "5ffb1b3465405a57aa750e037611f623",
  "timestamp_utc": "2026-11-17T13:30:00Z",
  "currency": "USD",
  "title": "Core Retail Sales m/m",
  "impact": "High",
  "forecast": "0.4%",
  "previous": "0.9%",
  "actual": null
}
```

---

### 3.6 Verification Artifacts

* **Verification Script:** [`validate_calendar.py`](file:///Users/albert/Projects/fx-signal-reader/validate_calendar.py)
* **Structured Results Output:** [`calendar_validation_results.json`](file:///Users/albert/Projects/fx-signal-reader/calendar_validation_results.json)
* **Local Raw Feed Cache:** [`data/raw_calendar_thisweek.json`](file:///Users/albert/Projects/fx-signal-reader/data/raw_calendar_thisweek.json)
