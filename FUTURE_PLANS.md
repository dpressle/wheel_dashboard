# Future Plans

Roadmap of planned improvements, roughly ordered by priority.

---

## 🔴 High Priority

### Per-Symbol Wheel Cycle Tracker
Track each ticker's full Wheel history as a single cycle: premiums collected on CSPs + premiums collected on CCs - unrealized stock loss = net P&L per wheel spin. Show how many full cycles have been completed per symbol and the all-in break-even price.

### Break-Even Price per Position
For each open stock position, compute:
```
Break-even = Stock Cost Basis − Total Premiums Collected on that ticker (realized)
```
Show this on the Stocks tab next to the current price so you can immediately see whether the Wheel has put you "in the money" on cost basis.

### Annualized Return (APY)
Convert monthly realized P&L into annualized yield:
```
APY = (Total Realized P&L / Starting Capital) × (12 / months_in_period)
```
Show per-month and period-total APY on the Monthly P&L tab.

---

## 🟡 Medium Priority

### Multi-Period Support
Allow uploading multiple statement CSVs (e.g., one per quarter) and merge them into a single unified view. Show a full-year chart even when IBKR splits exports by period.

### Win Rate by Strategy
Separate win rates for:
- CSPs (expired worthless vs bought back at a loss vs assigned)
- Covered Calls (expired worthless vs bought back vs called away)
- LEAPS (profitable close vs loss)

Show as a summary table on the Wheel Positions tab.

### Assignment Tracker
Detect when a CSP was assigned (stock received via put assignment) and when a CC was called away (stock sold via call assignment). Mark these events in the trades history so you can see the full cycle narrative per symbol.

### Greeks Display (Delta / Theta / IV)
Pull live Greeks from a market data source (e.g., yfinance or Tradier API) for open positions. Show delta exposure and daily theta decay for CSPs and CCs. Requires an API key.

---

## 🟢 Nice to Have

### Benchmark Comparison
Compare account return against SPY / QQQ over the same period. Overlay on the Monthly P&L chart as a reference line.

### Position Sizing Alerts
Highlight symbols where total allocation (stock + CSP collateral) exceeds a configurable % of NAV threshold (e.g., warn if any single symbol > 20%).

### Tax Summary View
Separate short-term vs long-term realized P&L per the IBKR data already present in the performance summary. Useful for tax planning.

### Export to Excel
Add a download button that exports all open positions and monthly P&L to a formatted Excel workbook (.xlsx) with one sheet per tab.

### Dark / Light Theme Toggle
Let the user switch between dark (current) and light mode via a sidebar toggle.

### Email / Telegram Alerts
Scheduled job (e.g., via GitHub Actions) that emails or messages a weekly P&L summary and flags positions approaching expiry within 7 days.

---

## 🔵 Technical Improvements

### Caching
Wrap `parse_ibkr_csv` with `@st.cache_data` so re-renders on the same file don't re-parse. Especially useful once multi-period merging is added.

### Unit Tests
Add `pytest` tests for the CSV parser and option classifier covering edge cases: zero-quantity positions, expired options, assignment codes, multi-leg entries.

### Support Flex Query Format
IBKR Flex Queries offer more granular data (e.g., trade-level commissions, wash sale adjustments). Add a second parser path that handles the Flex XML/CSV format in addition to the current Activity Statement format.
