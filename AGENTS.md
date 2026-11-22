# Project Resources
- Project: FX Signal Reader (Multi-Pair: USD/JPY & EUR/USD)
- Google Drive Folder: USD/JPY Signal Reader (ID: `1g7hYxJWgDVyMmJRBAOBxdNcB_rAmpxu2`)
- URL: https://drive.google.com/drive/folders/1g7hYxJWgDVyMmJRBAOBxdNcB_rAmpxu2

## Multi-Signal Capabilities & Specialist Agent Roles
- **10Y & 2Y Sovereign Yield Spreads (`spread_10y`, `spread_2y`):** Macro Analyst evaluates both 10-Year (`US 10Y - JGB 10Y` / `DE 10Y - US 10Y`) and 2-Year (`US 2Y - JGB 2Y` via FRED `DGS2` / Yahoo `2YY=F` and MoF `jgbcm.csv` `2年`) yield differentials, applying confidence confirmation boosts (`+0.05`) when 2Y and 10Y align or curve divergence dampening (`-0.05`) when front-end and long-end spreads conflict.
- **Crude Oil (`CL=F`) Terms-of-Trade Momentum:** Macro Analyst and Risk Committee track WTI Crude Oil Futures (`CL=F`) 5-day/20-day RoC and 50-day EMA (`compute_commodity_momentum`) to classify symmetric energy terms-of-trade regimes (`SURGING` -> `BULLISH_USD` import cost drag on JPY; `PLUNGING` -> `BEARISH_USD` terms-of-trade relief; `STABLE` -> `NEUTRAL`).
- **Tokyo Gotobi & Nakane (9:55 AM JST) Fixing Session Engine (`core/gotobi.py`):** Evaluates Japanese corporate settlement dates (days 5, 10, 15, 20, 25, 30, and month-end with weekend/holiday rollback to the preceding business day) and intraday Tokyo fixing windows (`PRE_FIX` 08:30–09:55 JST, `POST_FIX` 09:55–11:00 JST, `STANDARD`), applying a Pre-Fix `BUY` conviction boost (`+0.05`), soft `SELL` advisory warning, and Post-Fix exhaustion annotations.
- **Telegram Remote Approval Gate & Daily Summary Engine (`notifications/telegram.py`):** Delivers actionable `BUY`/`SELL` proposals with inline `[ ✅ Approve ]` / `[ ❌ Reject ]` buttons over Telegram Bot API long-polling (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, timeout auto-rejection), and manages scheduled/on-demand daily market & portfolio summaries (`data/telegram_settings.json`, enabled by default at `09:00` JST) controlled via `/summary on [HH:MM]`, `/summary off`, and `/summary now` (always displaying current summary schedule settings).


