# P9-03: Tokyo Gotobi & Nakane Fixing Intraday Session Engine

* **Phase:** Phase 9: Advanced USD/JPY Signals (Yield Curve, Terms-of-Trade & Intraday Fix)
* **Status:** `PENDING`
* **Complexity:** Single-Task Foundation
* **Last Updated (UTC):** `2026-11-20T13:35:00Z`
* **Implementation Plan:** [plan_p1_p2_p3_signals.md](file:///Users/albert/.gemini/antigravity/brain/df88ef12-66c7-48bc-a6ad-f98f47ed301e/plan_p1_p2_p3_signals.md)

---

## 1. Objective & Scope

Create a deterministic, calendar-accurate intraday session engine in `fx_signal_reader/core/gotobi.py` modeling Japanese commercial corporate settlement flows around the Tokyo 9:55 AM JST (`Nakane` / 仲値) fix:

1. **Gotobi Calendar Detection:** Identify days of the month ending in 5 or 0 ($5, 10, 15, 20, 25, 30$) and the final calendar day of each month.
2. **Weekend & Holiday Rollback Convention:** In Japanese banking and commercial FX practice, if a Gotobi settlement date falls on a weekend or Japanese national bank holiday, corporate settlement rolls backward to the **preceding business day**.
3. **Session Windows:**
   - **Pre-Fix Accumulation Window (08:30–09:55 JST / 23:30–00:55 UTC):** Commercial dollar-buying flow creates a systematic upward drift in USD/JPY spot.
   - **Post-Fix Exhaustion Window (09:55–11:00 JST / 00:55–02:00 UTC):** Fixing demand ceases; upside momentum frequently dissipates or consolidates.
   - **Standard Window:** Remaining trading hours.
4. **Structured Output:** Return `GotobiSessionInfo` containing `is_gotobi_day`, `session_phase`, `is_pre_fix_window`, `is_post_fix_window`, and descriptive notes.

## 2. Implementation Notes & Acceptance Criteria

1. **Files Affected:**
   - `fx_signal_reader/core/gotobi.py` [NEW]
   - `fx_signal_reader/core/__init__.py`
   - `tests/test_gotobi.py` [NEW]
2. **Acceptance Criteria:**
   - Detects all regular Gotobi days across standard and leap years.
   - Correctly rolls weekends (e.g., the 15th falling on a Sunday rolls to Friday the 13th).
   - Accurately identifies `PRE_FIX` (08:30–09:55 JST) and `POST_FIX` (09:55–11:00 JST) phases from UTC timestamps.
   - Pure Python implementation with zero mandatory third-party network dependencies.
   - High test coverage across historical dates and boundary minutes.

- [ ] `core/gotobi.py` created with Japanese calendar rollback logic
- [ ] Pre-Fix and Post-Fix intraday window detection implemented
- [ ] Comprehensive unit tests created in `tests/test_gotobi.py`
