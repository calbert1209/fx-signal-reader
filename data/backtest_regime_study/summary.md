# 5-Year USD/JPY Regime Asymmetry Backtest Study

## Run Metadata
* **Database:** `data/backtest_signals_5y.db`
* **Pair:** `USD/JPY`
* **Window (UTC):** `2021-11-22T00:00:00Z` $\rightarrow$ `2026-11-20T00:00:00Z` (1,300 bars, 1,299 evaluated steps)
* **Base Config:** `step_hours=4`, `max_hold_hours=120`, `spread_pips=1.5`, `initial_capital_usd=10000.0`, `allow_overlapping_trades=False`, `conservative_tie_break=True`, `mof_lookback_hours=48`
* **Data Coverage:** Yield Spread `100.0%` | 2Y Spread `100.0%` | Oil `99.92%` | Gotobi `100.0%` | VIX `99.92%` | Macro Events `59.12%` | MoF Alerts `3.23%`

---

## Comparison by Regime & Option

### `RISK_ON` Regime (Isolated Conservative Variations)

| Option | Config (`risk% / SL / TP / BE`) | Trades | Win Rate | Net PnL (Return %) | Profit Factor | Expectancy | Avg R | Sharpe | Max Drawdown |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Baseline** | `3.0% / 120p / 240p / off` | 144 | 51.39% | \$4,143.61 (+41.44%) | 1.2463 | \$28.78 | +0.1047 | 0.5887 | \$2,641.74 (17.20%) |
| **Option A** *(Sizing)* | `1.5% / 120p / 240p / off` | 144 | 51.39% | \$2,071.80 (+20.72%) | 1.2463 | \$14.39 | +0.1047 | 0.5456 | **\$1,320.84 (9.94%)** |
| **Option B** *(Breakeven)* | `3.0% / 120p / 240p / 1.0R` | 144 | 47.92% | \$3,539.50 (+35.40%) | 1.2170 | \$24.58 | +0.0904 | 0.5354 | \$3,330.42 (19.80%) |
| **Option C** *(Brackets)* | `3.0% / 80p / 160p / off` | 169 | 45.56% | **\$5,743.66 (+57.44%)** | 1.2258 | **\$33.99** | **+0.1218** | **0.6441** | \$2,766.15 (20.26%) |
| **Combined** *(A+B+C)* | `1.5% / 80p / 160p / 1.0R` | 177 | 36.72% | \$615.21 (+6.15%) | 1.0459 | \$3.48 | +0.0310 | 0.1687 | \$2,707.82 (20.32%) |

### `TRANSITIONAL` Regime (Isolated Aggressive Variations)

| Option | Config (`risk% / SL / TP / BE`) | Trades | Win Rate | Net PnL (Return %) | Profit Factor | Expectancy | Avg R | Sharpe | Max Drawdown |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Baseline** | `3.0% / 120p / 240p / off` | 70 | **65.71%** | \$8,241.24 (+82.41%) | **2.2813** | \$117.73 | **+0.4039** | 1.3354 | **\$2,475.27 (13.72%)** |
| **Option A** *(Sizing)* | `3.5% / 120p / 240p / off` | 70 | **65.71%** | **\$9,614.82 (+96.15%)** | **2.2813** | **\$137.35** | **+0.4039** | **1.3416** | \$2,887.81 (14.90%) |
| **Option B** *(Breakeven)* | `3.0% / 120p / 240p / 1.0R` | 72 | 59.72% | \$7,297.80 (+72.98%) | 2.1456 | \$101.36 | +0.3484 | 1.2176 | \$2,475.27 (14.19%) |
| **Option C** *(Brackets)* | `3.0% / 150p / 300p / off` | 68 | 64.71% | \$5,056.07 (+50.56%) | 1.8380 | \$74.35 | +0.2582 | 0.9847 | \$2,588.83 (16.70%) |
| **Combined** *(A+C)* | `3.5% / 150p / 300p / off` | 68 | 64.71% | \$5,898.80 (+58.99%) | 1.8380 | \$86.75 | +0.2582 | 0.9922 | \$3,020.30 (18.40%) |

---

## Key Findings

1. **Option A (Regime-Dependent Risk Sizing) is the clearest winner:**
   * In `RISK_ON`, cutting risk from `3.0%` to `1.5%` halved dollar max drawdown (**\$2,641.74 $\rightarrow$ \$1,320.84**, and **17.20% $\rightarrow$ 9.94%**) while leaving Profit Factor (**1.2463**) and Avg R (**+0.1047**) intact.
   * In `TRANSITIONAL`, raising risk from `3.0%` to `3.5%` delivered the highest overall profit (**\$9,614.82 / +96.15%**) and highest Sharpe (**1.3416**), maintaining a **65.71%** win rate and **2.2813** Profit Factor.
2. **Option B (`--breakeven-r 1.0`) degraded performance in both regimes:**
   * In `RISK_ON`, win rate dropped from **51.39% $\rightarrow$ 47.92%**, Net PnL fell (\$4,143.61 $\rightarrow$ \$3,539.50), and Max Drawdown actually *worsened* (\$2,641.74 $\rightarrow$ \$3,330.42) because normal pullbacks stopped out trades at breakeven that otherwise reached TP.
   * In `TRANSITIONAL`, win rate fell from **65.71% $\rightarrow$ 59.72%** and Net PnL dropped from \$8,241.24 to \$7,297.80.
3. **Option C (Bracket Width Adjustments) showed mixed, asymmetric behavior:**
   * Tighter brackets (`80p / 160p`) in `RISK_ON` raised Net PnL (\$4,143.61 $\rightarrow$ \$5,743.66), Avg R (+0.1047 $\rightarrow$ +0.1218), and Sharpe (0.5887 $\rightarrow$ 0.6441) via higher trade turnover (169 trades), though Max Drawdown increased slightly (\$2,766.15 / 20.26%). Note that pairing `80p / 160p` with `1.0R` breakeven (`combo_risk_on`) severely over-tightened exits (win rate dropped to 36.72%, Net PnL \$615.21).
   * Wider brackets (`150p / 300p`) in `TRANSITIONAL` underperformed `120p / 240p` (Net PnL \$5,056.07 vs \$8,241.24, Sharpe 0.9847 vs 1.3354) because the 120-hour (5-day) `TIME_STOP` cut off trades before reaching the 300-pip target.

---

## Stored Artifacts

### JSON Reports
* [baseline_risk_on_report.json](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/baseline_risk_on_report.json)
* [baseline_transitional_report.json](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/baseline_transitional_report.json)
* [optA_risk_on_report.json](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optA_risk_on_report.json)
* [optA_transitional_report.json](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optA_transitional_report.json)
* [optB_risk_on_report.json](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optB_risk_on_report.json)
* [optB_transitional_report.json](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optB_transitional_report.json)
* [optC_risk_on_report.json](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optC_risk_on_report.json)
* [optC_transitional_report.json](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optC_transitional_report.json)
* [combo_risk_on_report.json](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/combo_risk_on_report.json)
* [combo_transitional_report.json](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/combo_transitional_report.json)

### CSV Trade Logs
* [baseline_risk_on_trades.csv](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/baseline_risk_on_trades.csv)
* [baseline_transitional_trades.csv](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/baseline_transitional_trades.csv)
* [optA_risk_on_trades.csv](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optA_risk_on_trades.csv)
* [optA_transitional_trades.csv](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optA_transitional_trades.csv)
* [optB_risk_on_trades.csv](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optB_risk_on_trades.csv)
* [optB_transitional_trades.csv](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optB_transitional_trades.csv)
* [optC_risk_on_trades.csv](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optC_risk_on_trades.csv)
* [optC_transitional_trades.csv](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optC_transitional_trades.csv)
* [combo_risk_on_trades.csv](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/combo_risk_on_trades.csv)
* [combo_transitional_trades.csv](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/combo_transitional_trades.csv)

---

## Option A Extended Sizing Sweep

Fixed brackets across all runs: `SL=120p`, `TP=240p`, `--no-breakeven`, `step_hours=4`, `initial_capital_usd=$10,000`.

### `RISK_ON` Conservative Sizing Sweep (144 trades, 51.39% Win Rate, 1.2463 PF, +0.1047 Avg R)

| Risk % | Net PnL (Return %) | Expectancy | Sharpe | Max Drawdown |
| :---: | :---: | :---: | :---: | :---: |
| **0.5%** | \$690.57 (+6.91%) | \$4.80 | 0.5068 | **\$440.26 (3.97%)** |
| **1.0%** | \$1,381.21 (+13.81%) | \$9.59 | 0.5275 | \$880.54 (7.22%) |
| **1.5%** | \$2,071.80 (+20.72%) | \$14.39 | 0.5456 | \$1,320.84 (9.94%) |
| **3.0%** *(Baseline)* | **\$4,143.61 (+41.44%)** | **\$28.78** | **0.5887** | \$2,641.74 (17.20%) |

### `TRANSITIONAL` Aggressive Sizing Sweep (70 trades, 65.71% Win Rate, 2.2813 PF, +0.4039 Avg R)

| Risk % | Net PnL (Return %) | Expectancy | Sharpe | Max Drawdown |
| :---: | :---: | :---: | :---: | :---: |
| **3.0%** *(Baseline)* | \$8,241.24 (+82.41%) | \$117.73 | 1.3354 | **\$2,475.27 (13.72%)** |
| **3.5%** | \$9,614.82 (+96.15%) | \$137.35 | 1.3416 | \$2,887.81 (14.90%) |
| **4.0%** | \$10,988.38 (+109.88%) | \$156.98 | 1.3468 | \$3,300.37 (15.93%) |
| **5.0%** | **\$13,735.47 (+137.35%)** | **\$196.22** | **1.3545** | \$4,125.46 (17.64%) |

### Sizing Sweep Observations
* **Linear Dollar Scaling & Drawdown Compression:** Because position units scale off initial capital (`$10,000 * risk%`), Net PnL USD and Max Drawdown USD scale almost linearly with risk percentage, leaving Win Rate, Profit Factor, and Avg R unchanged within each regime.
* **`RISK_ON` Trade-off:** Dropping risk from `3.0%` $\rightarrow$ `1.0%` cuts Max Drawdown from **17.20% (\$2,641.74)** to **7.22% (\$880.54)** while still returning +13.81%; `0.5%` compresses drawdown below 4% (**3.97% / \$440.26**) at the cost of lower capital utilization (+6.91% return).
* **`TRANSITIONAL` Trade-off:** Pushing risk from `3.0%` $\rightarrow$ `5.0%` lifts Net PnL by +66.7% (**\$8,241.24 $\rightarrow$ \$13,735.47**) and slightly improves Sharpe (**1.3354 $\rightarrow$ 1.3545**), while Max Drawdown % grows moderately (**13.72% $\rightarrow$ 17.64%**) because accumulated profits raise the peak equity denominator—though dollar drawdown reaches **\$4,125.46** (41.3% of initial \$10k capital), increasing vulnerability if a losing streak hits early in a run.

### Extended Sweep Artifacts
* [optA_risk_on_0.5pct_report.json](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optA_risk_on_0.5pct_report.json) / [optA_risk_on_0.5pct_trades.csv](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optA_risk_on_0.5pct_trades.csv)
* [optA_risk_on_1.0pct_report.json](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optA_risk_on_1.0pct_report.json) / [optA_risk_on_1.0pct_trades.csv](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optA_risk_on_1.0pct_trades.csv)
* [optA_transitional_4.0pct_report.json](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optA_transitional_4.0pct_report.json) / [optA_transitional_4.0pct_trades.csv](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optA_transitional_4.0pct_trades.csv)
* [optA_transitional_5.0pct_report.json](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optA_transitional_5.0pct_report.json) / [optA_transitional_5.0pct_trades.csv](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optA_transitional_5.0pct_trades.csv)

---

## Option A + C Refinement (`RISK_ON`, `80p / 160p`, `--no-breakeven`)

Testing tighter brackets (`80p / 160p`, no breakeven) paired with conservative `1.0%` and `1.5%` sizing in `RISK_ON`:

| Run | Config (`risk% / SL / TP`) | Trades | Win Rate | Net PnL (Return %) | Profit Factor | Expectancy | Avg R | Sharpe | Max Drawdown |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `optAC_risk_on_1.0pct` | `1.0% / 80p / 160p` | 169 | 45.56% | **\$1,914.48 (+19.14%)** | 1.2258 | \$11.33 | **+0.1218** | 0.5713 | \$922.04 (8.22%) |
| `optA_risk_on_1.0pct` | `1.0% / 120p / 240p` | 144 | 51.39% | \$1,381.21 (+13.81%) | **1.2463** | \$9.59 | +0.1047 | 0.5275 | **\$880.54 (7.22%)** |
| `optAC_risk_on_1.5pct` | `1.5% / 80p / 160p` | 169 | 45.56% | **\$2,871.79 (+28.72%)** | 1.2258 | \$16.99 | **+0.1218** | 0.5939 | \$1,383.06 (11.69%) |
| `optA_risk_on` | `1.5% / 120p / 240p` | 144 | 51.39% | \$2,071.80 (+20.72%) | **1.2463** | \$14.39 | +0.1047 | 0.5456 | **\$1,320.84 (9.94%)** |
| `optC_risk_on` | `3.0% / 80p / 160p` | 169 | 45.56% | \$5,743.66 (+57.44%) | 1.2258 | \$33.99 | +0.1218 | **0.6441** | \$2,766.15 (20.26%) |

### Refinement Findings
* **Confirmed Upgrade on Risk-Adjusted Return:** At both `1.0%` and `1.5%` risk, `80p / 160p` (without breakeven) increased Net PnL by **+38.6%** (\$1,381.21 $\rightarrow$ \$1,914.48 at `1.0%`; \$2,071.80 $\rightarrow$ \$2,871.79 at `1.5%`), raised Avg R (**+0.1047 $\rightarrow$ +0.1218**), and improved Sharpe (**0.5275 $\rightarrow$ 0.5713** at `1.0%`; **0.5456 $\rightarrow$ 0.5939** at `1.5%`), while adding minimal dollar drawdown (+\$41.50 at `1.0%`, +\$62.22 at `1.5%`).
* **Updated Balanced Recommendation:** `RISK_ON` at **`1.0% / 80p / 160p / --no-breakeven`** (\$1,914.48, 0.5713 Sharpe, \$922.04 max DD) paired with `TRANSITIONAL` at **`4.0% / 120p / 240p / --no-breakeven`** (\$10,988.38, 1.3468 Sharpe, \$3,300.37 max DD).

### Option A + C Artifacts
* [optAC_risk_on_1.0pct_report.json](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optAC_risk_on_1.0pct_report.json) / [optAC_risk_on_1.0pct_trades.csv](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optAC_risk_on_1.0pct_trades.csv)
* [optAC_risk_on_1.5pct_report.json](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optAC_risk_on_1.5pct_report.json) / [optAC_risk_on_1.5pct_trades.csv](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/optAC_risk_on_1.5pct_trades.csv)

---

## Unified Single-Pass 5-Year Backtest (`--regime-overrides`, P11-01)

True unified replay on `data/backtest_signals_5y.db` (`step_hours=4`, `allow_overlapping_trades=False`, `--no-breakeven`, single \$10,000 equity pool) with:
```json
{
  "RISK_ON":      {"risk_pct": 1.0, "sl_pips": 80,  "tp_pips": 160},
  "TRANSITIONAL": {"risk_pct": 4.0, "sl_pips": 120, "tp_pips": 240}
}
```

| Metric | Unified Recommended (`--regime-overrides`) |
| :--- | :--- |
| **Trades Executed** | **231** (224 skipped overlap; 117 wins / 114 losses, 50.65% WR) |
| **Net PnL (Return %)** | **\$12,042.91 (+120.43%)** (Gross Profit \$28,869.07 / Gross Loss \$16,826.16) |
| **Profit Factor / Sharpe** | **1.7157** PF \| **1.3930** Sharpe \| **+0.2072** Avg R \| **\$52.13** Expectancy |
| **Max Drawdown** | **\$3,839.70 (16.93%)** |
| **`RISK_ON` Breakdown** | 163 trades \| 45.40% WR \| **+\$2,075.38** \| +0.1360 Avg R |
| **`TRANSITIONAL` Breakdown** | 68 trades \| 63.24% WR \| **+\$9,967.53** \| +0.3777 Avg R |

* [unified_recommended_report.json](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/unified_recommended_report.json) / [unified_recommended_trades.csv](file:///Users/albert/Projects/fx-signal-reader/data/backtest_regime_study/unified_recommended_trades.csv)
