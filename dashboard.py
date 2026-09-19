"""
Interactive Brokers – Wheel Strategy Dashboard
Supports: CSP → assignment → Covered Call cycle + LEAPS side trades
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import re
import csv
import io
from datetime import datetime, date

# ─────────────────────────────────────────────────────────────
# Page configuration
# ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Wheel Strategy Dashboard",
    page_icon="⚙️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    div[data-testid="metric-container"] {
        background-color: #1a1d27;
        border: 1px solid #2d3045;
        border-radius: 8px;
        padding: 12px 18px;
    }
    .section-header {
        font-size: 1.05em; font-weight: 700; color: #c8d0e0;
        border-bottom: 2px solid #3d4870;
        padding-bottom: 6px; margin-bottom: 12px; margin-top: 8px;
    }
    .wheel-phase {
        display: inline-block; border-radius: 5px; padding: 2px 8px;
        font-size: 0.8em; font-weight: 600;
    }
    .phase-csp     { background:#1a3a2a; color:#27ae60; }
    .phase-cc      { background:#1a2a3a; color:#3498db; }
    .phase-leaps   { background:#2a1a3a; color:#9b59b6; }
    .phase-stock   { background:#3a2a1a; color:#e67e22; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def parse_option_symbol(symbol_str: str):
    """Return (underlying, expiry_date, strike, opt_type) or (None,)*4."""
    match = re.match(r'^(\w+)\s+(\d{2}[A-Z]{3}\d{2})\s+([\d.]+)\s+([CP])$',
                     str(symbol_str).strip())
    if match:
        try:
            return (
                match.group(1),
                datetime.strptime(match.group(2), '%d%b%y').date(),
                float(match.group(3)),
                match.group(4),
            )
        except Exception:
            pass
    return None, None, None, None


def classify_option(symbol_str: str, quantity, stock_symbols: set, report_date: date) -> str:
    underlying, expiry, _, opt_type = parse_option_symbol(symbol_str)
    if underlying is None:
        return 'Other'
    qty = float(quantity)
    days = (expiry - report_date).days if expiry else 0
    if qty < 0 and opt_type == 'P':
        return 'CSP'
    if qty < 0 and opt_type == 'C':
        return 'Covered Call' if underlying in stock_symbols else 'Naked Call'
    if qty > 0 and opt_type == 'C':
        return 'LEAPS' if days > 365 else 'Long Call'
    if qty > 0 and opt_type == 'P':
        return 'Long Put'
    return 'Other'


def fmt(val, decimals=2, prefix='$'):
    try:
        v = float(val)
        s = f"{abs(v):,.{decimals}f}"
        return f"{prefix}{s}" if v >= 0 else f"-{prefix}{s}"
    except Exception:
        return str(val)


def pnl_color(val):
    try:
        return 'color: #27ae60' if float(val) >= 0 else 'color: #e74c3c'
    except Exception:
        return ''


def numerify(df: pd.DataFrame, cols) -> pd.DataFrame:
    """Convert listed columns to numeric in-place, coercing errors to NaN."""
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    return df


# ─────────────────────────────────────────────────────────────
# CSV Parser
# ─────────────────────────────────────────────────────────────

def parse_ibkr_csv(file_content: str) -> dict:
    reader = csv.reader(io.StringIO(file_content))
    raw = [r for r in reader if len(r) >= 2]

    sections: dict[str, list] = {}
    for row in raw:
        key = row[0].strip()
        sections.setdefault(key, []).append((row[1].strip(), [c.strip() for c in row[2:]]))

    result = dict(
        statement={}, nav={}, change_nav={},
        open_stocks=pd.DataFrame(), open_options=pd.DataFrame(),
        trades_stocks=pd.DataFrame(), trades_options=pd.DataFrame(),
        performance=pd.DataFrame(), deposits=pd.DataFrame(),
    )

    # Statement + Account Information (both feed the same dict)
    for section_key in ('Statement', 'Account Information'):
        for rt, c in sections.get(section_key, []):
            if rt == 'Data' and len(c) >= 2:
                result['statement'][c[0]] = c[1]

    # NAV
    nav_hdr = None
    for rt, c in sections.get('Net Asset Value', []):
        if rt == 'Header':
            nav_hdr = c
        elif rt == 'Data' and nav_hdr and c:
            row = {nav_hdr[i]: c[i] if i < len(c) else '' for i in range(len(nav_hdr))}
            asset = row.get('Asset Class', '').strip()
            if asset and 'Rate of Return' not in asset:
                try:
                    result['nav'][asset] = {
                        k: float(row.get(v, 0) or 0)
                        for k, v in [('prior','Prior Total'),('current','Current Total'),
                                     ('current_long','Current Long'),('current_short','Current Short'),
                                     ('change','Change')]
                    }
                except Exception:
                    pass

    # Change in NAV
    for rt, c in sections.get('Change in NAV', []):
        if rt == 'Data' and len(c) >= 2:
            try:
                result['change_nav'][c[0]] = float(c[1].replace(',', '') or 0)
            except Exception:
                result['change_nav'][c[0]] = c[1]

    # Open Positions
    op_hdr = None
    s_rows, o_rows = [], []
    for rt, c in sections.get('Open Positions', []):
        if rt == 'Header':
            op_hdr = c
        elif rt == 'Data' and op_hdr and c and c[0] == 'Summary':
            row = {op_hdr[i]: c[i] if i < len(c) else '' for i in range(len(op_hdr))}
            asset = row.get('Asset Category', '')
            (o_rows if 'Option' in asset else s_rows).append(row)

    if s_rows:
        result['open_stocks'] = pd.DataFrame(s_rows)
    if o_rows:
        result['open_options'] = pd.DataFrame(o_rows)

    # Trades
    tr_hdr = None
    ts_rows, to_rows = [], []
    for rt, c in sections.get('Trades', []):
        if rt == 'Header':
            tr_hdr = c
        elif rt == 'Data' and tr_hdr and c and c[0] == 'Order':
            row = {tr_hdr[i]: c[i] if i < len(c) else '' for i in range(len(tr_hdr))}
            asset = row.get('Asset Category', '')
            (to_rows if 'Option' in asset else ts_rows).append(row)

    if ts_rows:
        result['trades_stocks'] = pd.DataFrame(ts_rows)
    if to_rows:
        result['trades_options'] = pd.DataFrame(to_rows)

    # Performance Summary
    perf_hdr = None
    perf_rows = []
    for rt, c in sections.get('Realized & Unrealized Performance Summary', []):
        if rt == 'Header':
            perf_hdr = c
        elif rt == 'Data' and perf_hdr and c:
            row = {perf_hdr[i]: c[i] if i < len(c) else '' for i in range(len(perf_hdr))}
            asset = row.get('Asset Category', '').strip()
            sym = row.get('Symbol', '').strip()
            if asset not in ('Total', 'Total (All Assets)', '') and sym not in ('', 'Total'):
                perf_rows.append(row)
    if perf_rows:
        result['performance'] = pd.DataFrame(perf_rows)

    # Deposits
    dep_hdr = None
    dep_rows = []
    for rt, c in sections.get('Deposits & Withdrawals', []):
        if rt == 'Header':
            dep_hdr = c
        elif rt == 'Data' and dep_hdr and c:
            row = {dep_hdr[i]: c[i] if i < len(c) else '' for i in range(len(dep_hdr))}
            if row.get('Currency', '') not in ('', 'Total'):
                dep_rows.append(row)
    if dep_rows:
        result['deposits'] = pd.DataFrame(dep_rows)

    return result


# ─────────────────────────────────────────────────────────────
# Analytics
# ─────────────────────────────────────────────────────────────

def compute_monthly_pnl(trades_stocks: pd.DataFrame, trades_options: pd.DataFrame) -> pd.DataFrame:
    monthly: dict[str, dict] = {}
    for df, cat in [(trades_stocks, 'Stocks'), (trades_options, 'Options')]:
        if df.empty:
            continue
        tmp = df.copy()
        tmp['_date'] = pd.to_datetime(
            tmp['Date/Time'].str.split(',').str[0].str.strip(), errors='coerce')
        tmp['_month'] = tmp['_date'].dt.to_period('M')
        tmp['Realized P/L'] = pd.to_numeric(tmp['Realized P/L'], errors='coerce').fillna(0)
        for period, grp in tmp.groupby('_month'):
            k = str(period)
            if k not in monthly:
                monthly[k] = {'Stocks': 0.0, 'Options': 0.0}
            monthly[k][cat] += grp['Realized P/L'].sum()
    if not monthly:
        return pd.DataFrame()
    return pd.DataFrame([
        {'Month': k, 'Options': round(v['Options'], 2),
         'Stocks': round(v['Stocks'], 2),
         'Total': round(v['Options'] + v['Stocks'], 2)}
        for k, v in sorted(monthly.items())
    ])


def compute_monthly_deposits(deposits: pd.DataFrame) -> pd.Series:
    """Sum deposits/withdrawals by month, keyed by 'YYYY-MM' period string."""
    if deposits.empty:
        return pd.Series(dtype=float)
    date_col = next((c for c in deposits.columns if 'date' in c.lower()), None)
    amt_col = next((c for c in deposits.columns if 'amount' in c.lower()), None)
    if not date_col or not amt_col:
        return pd.Series(dtype=float)
    df = deposits.copy()
    df['_date'] = pd.to_datetime(df[date_col], errors='coerce')
    df['_amount'] = pd.to_numeric(
        df[amt_col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    df = df.dropna(subset=['_date'])
    if df.empty:
        return pd.Series(dtype=float)
    df['_month'] = df['_date'].dt.to_period('M').astype(str)
    return df.groupby('_month')['_amount'].sum()


def wheel_premium_by_symbol(trades_options: pd.DataFrame) -> pd.DataFrame:
    """Sum realized P&L from closed option trades grouped by underlying symbol."""
    if trades_options.empty:
        return pd.DataFrame()
    df = trades_options.copy()
    df['Realized P/L'] = pd.to_numeric(df['Realized P/L'], errors='coerce').fillna(0)
    df['underlying'] = df['Symbol'].apply(lambda s: parse_option_symbol(s)[0] or s.split()[0])
    df['opt_type_label'] = df['Symbol'].apply(
        lambda s: ('Put' if parse_option_symbol(s)[3] == 'P' else 'Call')
        if parse_option_symbol(s)[3] else 'Unknown'
    )
    grp = (df.groupby(['underlying', 'opt_type_label'])['Realized P/L']
             .sum().reset_index()
             .rename(columns={'underlying': 'Symbol', 'opt_type_label': 'Type', 'Realized P/L': 'Premium P&L'}))
    pivot = grp.pivot_table(index='Symbol', columns='Type', values='Premium P&L', fill_value=0).reset_index()
    pivot.columns.name = None
    pivot['Total Options P&L'] = pivot.drop(columns='Symbol').sum(axis=1)
    return pivot.sort_values('Total Options P&L', ascending=False).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
# Main dashboard
# ─────────────────────────────────────────────────────────────

def main():
    # ── Sidebar ──
    with st.sidebar:
        st.markdown("## ⚙️ Wheel Strategy")
        st.markdown("---")
        uploaded = st.file_uploader(
            "Upload IBKR Statement (CSV)", type=['csv'],
            help="Interactive Brokers activity/flex statement CSV",
        )

    file_content = None
    if uploaded:
        file_content = uploaded.read().decode('utf-8-sig')
    else:
        import os
        default = os.path.join(os.path.dirname(__file__), 'U16585944_20260101_20260605.csv')
        if os.path.exists(default):
            with open(default, 'r', encoding='utf-8-sig') as f:
                file_content = f.read()
            with st.sidebar:
                st.info("Loaded default statement file.")
        else:
            st.title("⚙️ Wheel Strategy Dashboard")
            st.info("Upload your IBKR statement CSV via the sidebar.")
            st.stop()

    # ── Parse ──
    try:
        data = parse_ibkr_csv(file_content)
    except Exception as e:
        st.error(f"Parse error: {e}")
        st.stop()

    nav = data['nav']
    change_nav = data['change_nav']
    stmt = data['statement']

    total_value = nav.get('Total', {}).get('current', 0)
    cash_value = nav.get('Cash ', {}).get('current', nav.get('Cash', {}).get('current', 0))
    realized_pnl = change_nav.get('Realized P/L', 0)
    unrealized_pnl = change_nav.get('Change in Unrealized P/L', 0)
    deposits = change_nav.get('Deposits & Withdrawals', 0)

    open_options = data['open_options'].copy()
    open_stocks = data['open_stocks'].copy()

    for col in ['Quantity', 'Cost Price', 'Cost Basis', 'Close Price', 'Value', 'Unrealized P/L']:
        for df in [open_stocks, open_options]:
            if not df.empty and col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

    stock_symbols = (
        set(open_stocks['Symbol'].str.strip().tolist()) if not open_stocks.empty else set()
    )

    try:
        period_str = stmt.get('Period', '')
        report_date = datetime.strptime(period_str.split(' - ')[-1].strip(), '%B %d, %Y').date()
    except Exception:
        report_date = date.today()

    # Classify & enrich options
    if not open_options.empty:
        parsed = open_options['Symbol'].apply(parse_option_symbol)
        open_options['underlying'] = parsed.apply(lambda x: x[0] or '')
        open_options['expiry'] = parsed.apply(lambda x: x[1])
        open_options['strike'] = pd.to_numeric(parsed.apply(lambda x: x[2]), errors='coerce')
        open_options['opt_type'] = parsed.apply(lambda x: x[3])
        open_options['position_type'] = open_options.apply(
            lambda r: classify_option(r['Symbol'], r['Quantity'], stock_symbols, report_date),
            axis=1,
        )
        open_options['csp_collateral'] = open_options.apply(
            lambda r: abs(r['strike'] * 100 * r['Quantity'])
            if r['position_type'] == 'CSP' else 0.0, axis=1,
        )
        # Days to expiry
        open_options['dte'] = open_options['expiry'].apply(
            lambda e: (e - report_date).days if e else None
        )

    total_csp_collateral = open_options['csp_collateral'].sum() if not open_options.empty else 0.0
    free_cash = cash_value - total_csp_collateral
    free_cash_pct = (free_cash / total_value * 100) if total_value > 0 else 0.0

    # Wheel phase per symbol: CSP-phase or CC-phase
    wheel_phase: dict[str, str] = {}
    if not open_options.empty:
        for _, row in open_options.iterrows():
            sym = row['underlying']
            pt = row['position_type']
            if pt in ('CSP', 'Covered Call'):
                wheel_phase[sym] = pt
    for sym in stock_symbols:
        if sym not in wheel_phase:
            wheel_phase[sym] = 'Stock (no active option)'

    # ── Sidebar extras ──
    with st.sidebar:
        st.markdown("---")
        st.markdown(f"**Account:** {stmt.get('Account', '—')}")
        st.markdown(f"**Name:** {stmt.get('Name', '—')}")
        st.markdown(f"**Period:** {stmt.get('Period', '—')}")
        st.markdown("---")
        st.markdown("**Wheel Phase Summary**")
        csp_count = sum(1 for v in wheel_phase.values() if 'CSP' in v)
        cc_count = sum(1 for v in wheel_phase.values() if 'Covered Call' in v)
        leaps_count = (open_options['position_type'] == 'LEAPS').sum() if not open_options.empty else 0
        st.markdown(f"- CSP phase: **{csp_count}** symbols")
        st.markdown(f"- CC phase: **{cc_count}** symbols")
        st.markdown(f"- LEAPS open: **{leaps_count}** contracts")
        st.markdown(f"- Stocks held: **{len(open_stocks)}**")

    # ══════════════════════════════════════════════════
    # Header
    # ══════════════════════════════════════════════════
    st.markdown("# ⚙️ Wheel Strategy Dashboard")
    st.caption(
        f"Interactive Brokers · {stmt.get('Period', '')}  |  "
        f"Account: {stmt.get('Account', '')}  |  Report date: {report_date}"
    )
    st.markdown("---")

    # ── KPI Row ──
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Total NAV", fmt(total_value))
    k2.metric("Cash Balance", fmt(cash_value))
    k3.metric("YTD Realized P&L", fmt(realized_pnl),
              delta=f"{realized_pnl / max(1, total_value - realized_pnl) * 100:.1f}%")
    k4.metric("Unrealized P&L", fmt(unrealized_pnl))
    k5.metric("Free Cash", fmt(free_cash),
              delta=f"{free_cash_pct:.1f}% of NAV",
              delta_color="normal" if free_cash_pct > 10 else "inverse")
    k6.metric("Deposits (period)", fmt(deposits))

    st.markdown("")

    # ══════════════════════════════════════════════════
    # Tabs
    # ══════════════════════════════════════════════════
    tab_wheel, tab_leaps, tab_stocks, tab_pnl, tab_alloc = st.tabs([
        "⚙️  Wheel Positions",
        "🚀  LEAPS",
        "📦  Stocks",
        "📊  Monthly P&L",
        "🥧  Portfolio Analysis",
    ])

    # ══════════════════════════════════════════════════
    # TAB 1 — Wheel Positions (CSP + Covered Calls)
    # ══════════════════════════════════════════════════
    with tab_wheel:
        st.markdown(
            "The **Wheel Strategy** cycles: *Sell CSP → get assigned stock → Sell Covered Call → "
            "stock called away → repeat.*"
        )
        st.markdown("")

        col_csp, col_cc = st.columns(2)

        # ── CSP ──
        with col_csp:
            st.markdown('<div class="section-header">💰 Cash Secured Puts (CSP Phase)</div>',
                        unsafe_allow_html=True)
            csps = open_options[open_options['position_type'] == 'CSP'].copy() \
                if not open_options.empty else pd.DataFrame()

            if not csps.empty:
                disp = csps[['underlying', 'Symbol', 'Quantity', 'strike', 'dte',
                              'Cost Price', 'Close Price', 'Value', 'Unrealized P/L', 'csp_collateral']].copy()
                disp.columns = ['Ticker', 'Contract', 'Qty', 'Strike', 'DTE',
                                 'Premium/Share', 'Last', 'Mkt Value', 'Unreal P&L', 'Collateral']
                numerify(disp, ['Strike', 'DTE', 'Premium/Share', 'Last', 'Mkt Value', 'Unreal P&L', 'Collateral'])
                disp['Qty'] = disp['Qty'].astype(int)
                disp['Max Profit'] = disp['Collateral'] - disp['Mkt Value'].abs()

                fmt_csp = {
                    'Strike': '${:.2f}', 'Premium/Share': '${:.2f}', 'Last': '${:.4f}',
                    'Mkt Value': '${:,.0f}', 'Unreal P&L': '${:,.0f}',
                    'Collateral': '${:,.0f}', 'Max Profit': '${:,.0f}',
                }
                st.dataframe(
                    disp.style.format(fmt_csp).map(pnl_color, subset=['Unreal P&L']),
                    use_container_width=True, hide_index=True,
                )
                m1, m2, m3 = st.columns(3)
                m1.metric("Collateral Reserved", fmt(csps['csp_collateral'].sum()))
                m2.metric("Current Liability", fmt(csps['Value'].sum()))
                m3.metric("Unrealized P&L", fmt(csps['Unrealized P/L'].sum()))
            else:
                st.info("No open CSP positions.")

        # ── Covered Calls ──
        with col_cc:
            st.markdown('<div class="section-header">📞 Covered Calls (CC Phase)</div>',
                        unsafe_allow_html=True)
            ccs = open_options[open_options['position_type'] == 'Covered Call'].copy() \
                if not open_options.empty else pd.DataFrame()

            if not ccs.empty:
                # Match CC to stock cost basis
                cc_display_rows = []
                for _, row in ccs.iterrows():
                    sym = row['underlying']
                    stk_row = open_stocks[open_stocks['Symbol'] == sym]
                    stk_cost = float(stk_row['Cost Price'].iloc[0]) if not stk_row.empty else None
                    stk_last = float(stk_row['Close Price'].iloc[0]) if not stk_row.empty else None
                    cc_display_rows.append({
                        'Ticker': sym,
                        'Contract': row['Symbol'],
                        'Qty': int(row['Quantity']),
                        'Strike': row['strike'],
                        'DTE': row['dte'],
                        'Premium/Share': row['Cost Price'],
                        'Last': row['Close Price'],
                        'Mkt Value': row['Value'],
                        'Unreal P&L': row['Unrealized P/L'],
                        'Stock Cost': stk_cost,
                        'Stock Last': stk_last,
                    })
                disp_cc = pd.DataFrame(cc_display_rows)
                numerify(disp_cc, ['Strike', 'DTE', 'Premium/Share', 'Last',
                                   'Mkt Value', 'Unreal P&L', 'Stock Cost', 'Stock Last'])
                fmt_cc = {
                    'Strike': '${:.2f}', 'Premium/Share': '${:.2f}', 'Last': '${:.4f}',
                    'Mkt Value': '${:,.0f}', 'Unreal P&L': '${:,.0f}',
                    'Stock Cost': '${:.2f}', 'Stock Last': '${:.2f}',
                }
                st.dataframe(
                    disp_cc.style.format({k: v for k, v in fmt_cc.items() if k in disp_cc.columns})
                               .map(pnl_color, subset=['Unreal P&L']),
                    use_container_width=True, hide_index=True,
                )
                m1, m2 = st.columns(2)
                m1.metric("Mkt Value (liability)", fmt(ccs['Value'].sum()))
                m2.metric("Unrealized P&L", fmt(ccs['Unrealized P/L'].sum()))
            else:
                st.info("No open covered call positions.")

        # ── Wheel Premium Income by Symbol ──
        st.markdown("---")
        st.markdown('<div class="section-header">⚙️ Wheel Premium Collected by Symbol (Realized)</div>',
                    unsafe_allow_html=True)

        prem_df = wheel_premium_by_symbol(data['trades_options'])
        if not prem_df.empty:
            col_tbl, col_chart = st.columns([1, 2])
            with col_tbl:
                fmt_cols = {c: '${:,.0f}' for c in prem_df.columns if c != 'Symbol'}
                pnl_subset = [c for c in prem_df.columns if c != 'Symbol']
                st.dataframe(
                    prem_df.style.format(fmt_cols).map(pnl_color, subset=pnl_subset),
                    use_container_width=True, hide_index=True,
                )
            with col_chart:
                # Grouped bar: Calls vs Puts premium per symbol
                melted = prem_df.melt(id_vars='Symbol', value_vars=[c for c in prem_df.columns
                                                                     if c not in ('Symbol', 'Total Options P&L')],
                                      var_name='Type', value_name='P&L')
                color_map = {'Call': '#3498db', 'Put': '#27ae60'}
                fig = px.bar(
                    melted, x='Symbol', y='P&L', color='Type',
                    barmode='group', color_discrete_map=color_map,
                    title='Realized Premium P&L: Puts (CSP income) vs Calls (CC income)',
                    template='plotly_dark', height=340,
                )
                fig.update_layout(margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No closed option trades found.")

    # ══════════════════════════════════════════════════
    # TAB 2 — LEAPS
    # ══════════════════════════════════════════════════
    with tab_leaps:
        st.markdown(
            "**LEAPS** (Long-term Equity Anticipation Securities) — "
            "long calls with >1 year to expiry used as directional bets alongside the Wheel."
        )
        leaps = open_options[open_options['position_type'] == 'LEAPS'].copy() \
            if not open_options.empty else pd.DataFrame()

        if not leaps.empty:
            col_l, col_r = st.columns([2, 1])
            with col_l:
                st.markdown('<div class="section-header">🚀 Open LEAPS Positions</div>',
                            unsafe_allow_html=True)
                disp = leaps[['underlying', 'Symbol', 'Quantity', 'strike', 'expiry', 'dte',
                               'Cost Price', 'Cost Basis', 'Close Price', 'Value', 'Unrealized P/L']].copy()
                disp.columns = ['Ticker', 'Contract', 'Qty', 'Strike', 'Expiry', 'DTE',
                                 'Avg Cost', 'Cost Basis', 'Last', 'Mkt Value', 'Unreal P&L']
                numerify(disp, ['Strike', 'DTE', 'Avg Cost', 'Cost Basis', 'Last', 'Mkt Value', 'Unreal P&L'])
                disp['Qty'] = disp['Qty'].astype(int)
                disp['Expiry'] = disp['Expiry'].astype(str)
                disp['Return %'] = ((disp['Mkt Value'] - disp['Cost Basis']) /
                                     disp['Cost Basis'].abs() * 100).round(1)
                fmt_l = {
                    'Strike': '${:.2f}', 'Avg Cost': '${:.2f}', 'Last': '${:.2f}',
                    'Cost Basis': '${:,.0f}', 'Mkt Value': '${:,.0f}',
                    'Unreal P&L': '${:,.0f}', 'Return %': '{:.1f}%',
                }
                st.dataframe(
                    disp.style.format(fmt_l).map(pnl_color, subset=['Unreal P&L', 'Return %']),
                    use_container_width=True, hide_index=True,
                )
            with col_r:
                st.markdown('<div class="section-header">LEAPS Summary</div>', unsafe_allow_html=True)
                cost = leaps['Cost Basis'].sum()
                mkt = leaps['Value'].sum()
                unreal = leaps['Unrealized P/L'].sum()
                st.metric("Total Cost Basis", fmt(cost))
                st.metric("Market Value", fmt(mkt))
                st.metric("Unrealized P&L", fmt(unreal),
                          delta=f"{unreal / abs(cost) * 100:.1f}%" if cost else None)
                st.metric("Contracts", int(leaps['Quantity'].sum()))
        else:
            st.info("No open LEAPS positions.")

        # Historical LEAPS trades
        st.markdown("---")
        st.markdown('<div class="section-header">📈 Closed LEAPS Trades (Historical)</div>',
                    unsafe_allow_html=True)
        if not data['trades_options'].empty:
            df_t = data['trades_options'].copy()
            df_t['Realized P/L'] = pd.to_numeric(df_t['Realized P/L'], errors='coerce').fillna(0)
            df_t['_qty'] = pd.to_numeric(df_t['Quantity'], errors='coerce').fillna(0)
            df_t['_underlying'], df_t['_expiry'], df_t['_strike'], df_t['_type'] = \
                zip(*df_t['Symbol'].apply(parse_option_symbol))
            df_t['_trade_date'] = pd.to_datetime(
                df_t['Date/Time'].str.split(',').str[0].str.strip(), errors='coerce').dt.date
            # DTE at time of trade — determines if it qualified as LEAPS when traded
            df_t['_dte_at_trade'] = df_t.apply(
                lambda r: (r['_expiry'] - r['_trade_date']).days
                if r['_expiry'] and r['_trade_date'] else 0, axis=1)
            # LEAPS = sell to close a long call (qty < 0) where expiry was > 1 year at trade time
            leaps_hist = df_t[
                (df_t['_type'] == 'C') &
                (df_t['_qty'] < 0) &
                (df_t['Realized P/L'] != 0) &
                (df_t['_dte_at_trade'] > 365)
            ].copy()
            if not leaps_hist.empty:
                show = leaps_hist[['Symbol', 'Date/Time', 'Quantity', 'T. Price', 'Realized P/L']].copy()
                show.columns = ['Contract', 'Date', 'Qty', 'Price', 'Realized P&L']
                numerify(show, ['Price', 'Realized P&L'])
                st.dataframe(
                    show.style.format({'Price': '${:.2f}', 'Realized P&L': '${:,.0f}'})
                              .map(pnl_color, subset=['Realized P&L']),
                    use_container_width=True, hide_index=True,
                )
                st.metric("Total Closed LEAPS P&L", fmt(leaps_hist['Realized P/L'].sum()))
            else:
                st.info("No closed LEAPS trades with realized P&L.")
        else:
            st.info("No trade data.")

    # ══════════════════════════════════════════════════
    # TAB 3 — Stocks
    # ══════════════════════════════════════════════════
    with tab_stocks:
        st.markdown('<div class="section-header">📦 Open Stock Positions</div>',
                    unsafe_allow_html=True)

        if not open_stocks.empty:
            stk = open_stocks[['Symbol','Quantity','Cost Price','Cost Basis',
                                'Close Price','Value','Unrealized P/L']].copy()
            stk.columns = ['Symbol','Qty','Avg Cost','Cost Basis','Last','Value','Unreal P&L']
            numerify(stk, ['Avg Cost','Cost Basis','Last','Value','Unreal P&L'])
            stk['Qty'] = stk['Qty'].astype(int)
            stk['Return %'] = ((stk['Value'] - stk['Cost Basis']) / stk['Cost Basis'] * 100).round(1)

            # Wheel phase for each stock
            stk['Wheel Phase'] = stk['Symbol'].map(wheel_phase).fillna('—')

            fmt_s = {
                'Avg Cost':'${:.2f}','Cost Basis':'${:,.0f}','Last':'${:.2f}',
                'Value':'${:,.0f}','Unreal P&L':'${:,.0f}','Return %':'{:.1f}%',
            }
            st.dataframe(
                stk.style.format(fmt_s).map(pnl_color, subset=['Unreal P&L','Return %']),
                use_container_width=True, hide_index=True,
            )

            col_s1, col_s2, col_s3, col_s4 = st.columns(4)
            col_s1.metric("Cost Basis", fmt(open_stocks['Cost Basis'].sum()))
            col_s2.metric("Market Value", fmt(open_stocks['Value'].sum()))
            col_s3.metric("Unrealized P&L", fmt(open_stocks['Unrealized P/L'].sum()))
            col_s4.metric("% of NAV", f"{open_stocks['Value'].sum() / total_value * 100:.1f}%"
                          if total_value else "—")

            st.markdown("---")
            # Stock P&L from closed trades
            st.markdown('<div class="section-header">Stock Trades — Realized P&L</div>',
                        unsafe_allow_html=True)
            if not data['trades_stocks'].empty:
                df_ts = data['trades_stocks'].copy()
                df_ts['Realized P/L'] = pd.to_numeric(df_ts['Realized P/L'], errors='coerce').fillna(0)
                closed = df_ts[df_ts['Realized P/L'] != 0][['Symbol','Date/Time','Quantity','T. Price','Realized P/L']].copy()
                closed.columns = ['Symbol','Date','Qty','Price','Realized P&L']
                numerify(closed, ['Price', 'Realized P&L'])
                if not closed.empty:
                    st.dataframe(
                        closed.style.format({'Price':'${:.2f}','Realized P&L':'${:,.2f}'})
                                    .map(pnl_color, subset=['Realized P&L']),
                        use_container_width=True, hide_index=True,
                    )
                    st.metric("Total Realized Stock P&L", fmt(closed['Realized P&L'].sum()))
                else:
                    st.info("No closed stock trades with P&L.")
        else:
            st.info("No open stock positions.")

    # ══════════════════════════════════════════════════
    # TAB 4 — Monthly P&L
    # ══════════════════════════════════════════════════
    with tab_pnl:
        st.markdown('<div class="section-header">Monthly Realized P&L</div>', unsafe_allow_html=True)
        monthly_df = compute_monthly_pnl(data['trades_stocks'], data['trades_options'])
        starting_value = change_nav.get('Starting Value', total_value)

        if monthly_df.empty:
            st.info("No trade data found.")
        else:
            fig = go.Figure()
            fig.add_trace(go.Bar(
                name='Options (CSP + CC + LEAPS)', x=monthly_df['Month'], y=monthly_df['Options'],
                marker_color=['#27ae60' if v >= 0 else '#e74c3c' for v in monthly_df['Options']],
                text=[fmt(v, decimals=0) for v in monthly_df['Options']],
                textposition='inside', textfont_color='white',
            ))
            fig.add_trace(go.Bar(
                name='Stocks', x=monthly_df['Month'], y=monthly_df['Stocks'],
                marker_color=['#2980b9' if v >= 0 else '#c0392b' for v in monthly_df['Stocks']],
                text=[fmt(v, decimals=0) for v in monthly_df['Stocks']],
                textposition='inside', textfont_color='white',
            ))
            fig.add_trace(go.Scatter(
                name='Total P&L', x=monthly_df['Month'], y=monthly_df['Total'],
                mode='lines+markers+text', line=dict(color='#f39c12', width=2.5),
                marker=dict(size=9), text=[fmt(v, decimals=0) for v in monthly_df['Total']],
                textposition='top center',
            ))
            cumulative = monthly_df['Total'].cumsum()
            fig.add_trace(go.Scatter(
                name='Cumulative P&L', x=monthly_df['Month'], y=cumulative,
                mode='lines', line=dict(color='#9b59b6', width=2, dash='dot'), yaxis='y2',
            ))
            fig.update_layout(
                barmode='group', xaxis_title='Month', yaxis_title='P&L ($)',
                yaxis2=dict(title='Cumulative ($)', overlaying='y', side='right', showgrid=False),
                template='plotly_dark', height=460,
                legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
                margin=dict(l=10, r=10, t=30, b=30),
            )
            st.plotly_chart(fig, use_container_width=True)

            col_tbl, col_stats = st.columns([3, 1])
            with col_tbl:
                tbl = monthly_df.copy()
                tbl['Running Total'] = tbl['Total'].cumsum().round(2)
                monthly_deposits = compute_monthly_deposits(data['deposits']).reindex(tbl['Month'], fill_value=0.0)
                cum_deposits_prior = monthly_deposits.cumsum().shift(1, fill_value=0.0).values
                tbl['Month Start Value'] = (
                    starting_value + tbl['Running Total'].shift(1, fill_value=0) + cum_deposits_prior
                ).round(2)
                tbl['ROI %'] = (tbl['Total'] / tbl['Month Start Value'] * 100).round(2) if starting_value else 0.0
                tbl['Cum ROI %'] = (tbl['Running Total'] / starting_value * 100).round(2) if starting_value else 0.0
                st.dataframe(
                    tbl.style.format({
                        'Options': '${:,.0f}', 'Stocks': '${:,.0f}',
                        'Total': '${:,.0f}', 'Running Total': '${:,.0f}',
                        'Month Start Value': '${:,.0f}',
                        'ROI %': '{:+.2f}%', 'Cum ROI %': '{:+.2f}%',
                    }).map(pnl_color, subset=['Options','Stocks','Total','Running Total','ROI %','Cum ROI %']),
                    use_container_width=True, hide_index=True,
                )
            with col_stats:
                st.markdown("**Period Summary**")
                st.metric("Options P&L", fmt(monthly_df['Options'].sum()))
                st.metric("Stock P&L", fmt(monthly_df['Stocks'].sum()))
                st.metric("Total Realized", fmt(monthly_df['Total'].sum()))
                winning = int((monthly_df['Total'] > 0).sum())
                st.metric("Win Rate", f"{winning}/{len(monthly_df)} months")
                if len(monthly_df):
                    best_idx = monthly_df['Total'].idxmax()
                    best = monthly_df.loc[best_idx]
                    st.metric("Best Month", f"{best['Month']} ({fmt(best['Total'], decimals=0)})")

    # ══════════════════════════════════════════════════
    # TAB 5 — Portfolio Analysis
    # ══════════════════════════════════════════════════
    with tab_alloc:
        # ── Asset Class Ratio ──
        st.markdown('<div class="section-header">Asset Class Breakdown (Stock / Options / Cash)</div>',
                    unsafe_allow_html=True)

        cash_nav   = nav.get('Cash ', {}).get('current', nav.get('Cash', {}).get('current', 0))
        stock_nav  = nav.get('Stock', {}).get('current', 0)
        opt_nav    = nav.get('Options', {}).get('current', 0)   # net options value (can be negative)

        ra1, ra2, ra3, ra4 = st.columns(4)
        ra1.metric("Cash",    fmt(cash_nav),  delta=f"{cash_nav  / total_value * 100:.1f}% of NAV")
        ra2.metric("Stocks",  fmt(stock_nav), delta=f"{stock_nav / total_value * 100:.1f}% of NAV")
        ra3.metric("Options (net)", fmt(opt_nav),
                   delta=f"{opt_nav / total_value * 100:.1f}% of NAV",
                   delta_color="normal" if opt_nav >= 0 else "inverse")
        ra4.metric("Total NAV", fmt(total_value))

        # Donut chart showing asset class proportions
        abs_opt = abs(opt_nav)
        other = max(0.0, total_value - cash_nav - stock_nav - abs_opt)
        donut_labels = ['Cash', 'Stocks', 'Options', 'Other']
        donut_values = [cash_nav, stock_nav, abs_opt, other]
        donut_colors = ['#3498db', '#e67e22', '#9b59b6', '#7f8c8d']

        fig_ratio = go.Figure(go.Pie(
            labels=donut_labels,
            values=donut_values,
            hole=0.55,
            marker_colors=donut_colors,
            textinfo='label+percent',
            hovertemplate='<b>%{label}</b><br>$%{value:,.0f}<br>%{percent}<extra></extra>',
            direction='clockwise',
            sort=False,
        ))
        fig_ratio.add_annotation(
            text=f"NAV<br><b>{fmt(total_value, decimals=0)}</b>",
            x=0.5, y=0.5, showarrow=False,
            font=dict(size=14, color='white'),
            align='center',
        )
        fig_ratio.update_layout(
            template='plotly_dark', height=320,
            legend=dict(orientation='h', yanchor='bottom', y=-0.15, xanchor='center', x=0.5),
            margin=dict(l=10, r=10, t=10, b=10),
        )
        st.plotly_chart(fig_ratio, use_container_width=True)

        st.markdown("---")
        col_pie, col_bar = st.columns(2)

        # ── Allocation Pie ──
        with col_pie:
            st.markdown('<div class="section-header">Symbol Allocation (% of NAV)</div>',
                        unsafe_allow_html=True)
            st.caption("Stocks: market value · CSP: collateral reserved · LEAPS: cost basis")

            allocation: dict[str, float] = {}
            if not open_stocks.empty:
                for _, r in open_stocks.iterrows():
                    sym = r['Symbol'].strip()
                    allocation[sym] = allocation.get(sym, 0.0) + float(r['Value'])
            if not open_options.empty:
                for _, r in open_options[open_options['position_type'] == 'CSP'].iterrows():
                    sym = r['underlying']
                    allocation[sym] = allocation.get(sym, 0.0) + r['csp_collateral']
                for _, r in open_options[open_options['position_type'] == 'LEAPS'].iterrows():
                    sym = r['underlying']
                    cb = abs(float(r['Cost Basis'])) if r['Cost Basis'] else 0.0
                    allocation[sym] = allocation.get(sym, 0.0) + cb

            allocated = sum(allocation.values())
            allocation['Free Cash'] = max(0.0, total_value - allocated)

            labels = list(allocation.keys())
            values = [max(0.0, v) for v in allocation.values()]
            palette = px.colors.qualitative.Pastel + px.colors.qualitative.Set2 + px.colors.qualitative.Bold

            fig_pie = go.Figure(go.Pie(
                labels=labels, values=values, hole=0.4,
                textinfo='label+percent',
                hovertemplate='<b>%{label}</b><br>$%{value:,.0f}<br>%{percent}<extra></extra>',
                marker_colors=palette[:len(labels)],
            ))
            fig_pie.update_layout(
                title=f'Capital Allocation — NAV: {fmt(total_value)}',
                template='plotly_dark', height=440,
                legend=dict(orientation='v', x=1.0),
                margin=dict(l=10, r=10, t=50, b=10),
            )
            st.plotly_chart(fig_pie, use_container_width=True)

            alloc_df = pd.DataFrame({'Symbol': labels, 'Allocated ($)': values})
            alloc_df['% of NAV'] = (alloc_df['Allocated ($)'] / total_value * 100).round(1)
            alloc_df = alloc_df.sort_values('Allocated ($)', ascending=False).reset_index(drop=True)
            st.dataframe(
                alloc_df.style.format({'Allocated ($)':'${:,.0f}','% of NAV':'{:.1f}%'}),
                use_container_width=True, hide_index=True,
            )

        # ── P&L by Symbol ──
        with col_bar:
            st.markdown('<div class="section-header">Realized P&L by Underlying Symbol</div>',
                        unsafe_allow_html=True)
            perf = data['performance'].copy()
            if not perf.empty:
                perf['Realized Total'] = pd.to_numeric(perf['Realized Total'], errors='coerce').fillna(0)
                perf['underlying'] = perf['Symbol'].apply(
                    lambda s: parse_option_symbol(s)[0] or s.strip()
                )
                sym_pnl = (perf.groupby('underlying')['Realized Total'].sum()
                               .reset_index()
                               .rename(columns={'underlying':'Symbol','Realized Total':'Realized P&L'})
                               .sort_values('Realized P&L'))

                fig_bar = go.Figure(go.Bar(
                    x=sym_pnl['Realized P&L'], y=sym_pnl['Symbol'], orientation='h',
                    marker_color=['#27ae60' if v >= 0 else '#e74c3c' for v in sym_pnl['Realized P&L']],
                    text=[fmt(v, decimals=0) for v in sym_pnl['Realized P&L']],
                    textposition='auto',
                ))
                fig_bar.update_layout(
                    title='YTD Realized P&L per Symbol (all instruments)',
                    xaxis_title='P&L ($)', template='plotly_dark', height=440,
                    showlegend=False, margin=dict(l=10, r=10, t=50, b=10),
                )
                st.plotly_chart(fig_bar, use_container_width=True)
            else:
                st.info("No performance data.")

        # ── Free Cash Analysis ──
        st.markdown("---")
        st.markdown('<div class="section-header">💵 Free Cash &amp; CSP Collateral</div>',
                    unsafe_allow_html=True)

        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            st.metric("Total Cash", fmt(cash_value))
            st.metric("CSP Collateral Reserved", fmt(total_csp_collateral))
            st.metric("Free Cash", fmt(free_cash),
                      delta=f"{free_cash_pct:.1f}% of NAV",
                      delta_color="normal" if free_cash_pct > 10 else "inverse")

        with fc2:
            fig_wf = go.Figure(go.Waterfall(
                orientation='v', measure=['absolute', 'relative', 'total'],
                x=['Total Cash', 'CSP Reserved', 'Free Cash'],
                y=[cash_value, -total_csp_collateral, 0],
                texttemplate='%{y:$,.0f}',
                connector={'line': {'color': 'rgba(63,63,63,0.4)'}},
                increasing={'marker': {'color': '#27ae60'}},
                decreasing={'marker': {'color': '#e74c3c'}},
                totals={'marker': {'color': '#3498db'}},
            ))
            fig_wf.update_layout(
                title='Cash Waterfall', template='plotly_dark',
                height=260, margin=dict(l=10, r=10, t=40, b=10),
            )
            st.plotly_chart(fig_wf, use_container_width=True)

        with fc3:
            color_gauge = ('#27ae60' if free_cash_pct > 20 else
                           '#f39c12' if free_cash_pct > 10 else '#e74c3c')
            fig_gauge = go.Figure(go.Indicator(
                mode='gauge+number',
                value=free_cash_pct,
                number={'suffix': '%', 'font': {'size': 36}},
                title={'text': 'Free Cash % of NAV', 'font': {'size': 14}},
                gauge={
                    'axis': {'range': [0, 100], 'tickwidth': 1},
                    'bar': {'color': color_gauge},
                    'steps': [
                        {'range': [0, 10], 'color': '#3d1a1a'},
                        {'range': [10, 25], 'color': '#2c3e50'},
                        {'range': [25, 100], 'color': '#1a2d1a'},
                    ],
                    'threshold': {'line': {'color': 'white', 'width': 3},
                                  'thickness': 0.75, 'value': 15},
                },
            ))
            fig_gauge.update_layout(
                template='plotly_dark', height=260,
                margin=dict(l=10, r=10, t=20, b=10),
            )
            st.plotly_chart(fig_gauge, use_container_width=True)

        # CSP detail table
        if not open_options.empty:
            csps = open_options[open_options['position_type'] == 'CSP'].copy()
            if not csps.empty:
                st.markdown("**CSP Collateral Detail**")
                csp_tbl = csps[['Symbol','Quantity','strike','expiry','csp_collateral',
                                 'Cost Price','Value','Unrealized P/L']].copy()
                csp_tbl.columns = ['Contract','Qty','Strike','Expiry','Collateral',
                                    'Premium Sold','Mkt Value','Unreal P&L']
                numerify(csp_tbl, ['Strike','Collateral','Premium Sold','Mkt Value','Unreal P&L'])
                csp_tbl['Qty'] = csp_tbl['Qty'].astype(int)
                csp_tbl['Expiry'] = csp_tbl['Expiry'].astype(str)
                csp_tbl['% of NAV'] = (csp_tbl['Collateral'] / total_value * 100).round(1)
                st.dataframe(
                    csp_tbl.style.format({
                        'Strike':'${:.2f}','Collateral':'${:,.0f}',
                        'Premium Sold':'${:.2f}','Mkt Value':'${:,.0f}',
                        'Unreal P&L':'${:,.0f}','% of NAV':'{:.1f}%',
                    }).map(pnl_color, subset=['Unreal P&L']),
                    use_container_width=True, hide_index=True,
                )


if __name__ == '__main__':
    main()
