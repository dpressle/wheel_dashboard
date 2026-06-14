# Wheel Strategy Dashboard

A Streamlit dashboard for analyzing options trading activity exported from Interactive Brokers, focused on the **Wheel Strategy** (Cash Secured Puts → assignment → Covered Calls) with LEAPS as a side strategy.

---

## Features

| Tab | What it shows |
|-----|--------------|
| **⚙️ Wheel Positions** | Open CSPs and Covered Calls with collateral, DTE, unrealized P&L; realized premium income by symbol (puts vs calls) |
| **🚀 LEAPS** | Open long calls >1yr to expiry; closed LEAPS trades with realized P&L |
| **📦 Stocks** | Open stock positions with Wheel phase label, cost basis, return %; closed stock trades |
| **📊 Monthly P&L** | Realized P&L per month split by Options / Stocks, cumulative line, ROI % based on starting capital |
| **🥧 Portfolio Analysis** | Asset class donut (Stock / Options / Cash), symbol allocation pie, realized P&L by symbol, free cash waterfall + gauge |

### Key metrics (top bar)
- Total NAV · Cash Balance · YTD Realized P&L · Unrealized P&L · Free Cash (cash minus CSP collateral) · Deposits

---

## Getting Started

### Prerequisites

```
Python 3.10+
```

### Install dependencies

```bash
pip install -r requirements.txt
```

### Run locally

```bash
streamlit run dashboard.py
```

The app auto-loads `U16585944_20260101_20260605.csv` if present in the same folder. Upload a different file via the sidebar.

---

## Getting the CSV from Interactive Brokers

1. Log in to **Client Portal** or **TWS**
2. Go to **Reports → Activity → Statements**
3. Select **Activity Statement** for your desired date range
4. Set format to **CSV**
5. Download and upload via the sidebar file uploader

> The parser expects the standard IBKR activity statement CSV format. Flex Queries with the same sections also work.

---

## Deploying to Streamlit Cloud (free)

1. Push this repo to GitHub (only `dashboard.py` and `requirements.txt` are required)
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your GitHub repo
4. Set **Main file path** to `dashboard.py`
5. Click **Deploy**

Once live, upload each new statement via the sidebar — no re-deployment needed.

---

## How positions are classified

| Classification | Condition |
|----------------|-----------|
| **CSP** | Short put (qty < 0, type P) |
| **Covered Call** | Short call (qty < 0, type C) where underlying stock is held |
| **LEAPS** | Long call (qty > 0, type C) with expiry > 365 days from report date |
| **Long Call** | Long call with expiry ≤ 365 days |
| **Long Put** | Long put (qty > 0, type P) |

**Free Cash** = Total Cash − CSP Collateral (strike × 100 × |contracts|)

**Realized ROI %** = Monthly Realized P&L ÷ Starting Account Value (not ending NAV)

---

## Project Structure

```
interactive_dashboard/
├── dashboard.py          # Single-file Streamlit app
├── requirements.txt      # Python dependencies
├── README.md
└── FUTURE_PLANS.md       # Roadmap
```

---

## Dependencies

```
streamlit>=1.32.0
pandas>=2.0.0
plotly>=5.18.0
numpy>=1.24.0
```
