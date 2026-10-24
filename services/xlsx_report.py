"""Wall Street-style Excel export of the portfolio analysis."""

from datetime import datetime
from io import BytesIO

import numpy as np
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

INK = "0F1114"
BRASS = "A8824A"
PAPER = "F7F6F2"
LINE = "D8D2C4"
GREEN = "4F7A5C"
RED = "B5544A"

HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)
TITLE_FONT = Font(color=INK, bold=True, size=16)
SUBTLE = Font(color="565B62", size=9, italic=True)
THIN = Side(style="thin", color=LINE)
BORDER = Border(bottom=THIN)

PCT = "0.00%"
NUM2 = "0.00"
USD = '#,##0'
USD2 = '#,##0.00'


def _title(ws, text, subtitle=None):
    ws["A1"] = text
    ws["A1"].font = TITLE_FONT
    if subtitle:
        ws["A2"] = subtitle
        ws["A2"].font = SUBTLE


def _header(ws, row, values):
    for col, value in enumerate(values, start=1):
        cell = ws.cell(row=row, column=col, value=value)
        cell.fill = PatternFill("solid", fgColor=INK)
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = ws.cell(row=row + 1, column=1)


def _widths(ws, widths):
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width


def _table(ws, start_row, rows, formats=None):
    formats = formats or {}
    for offset, row in enumerate(rows):
        for col, value in enumerate(row, start=1):
            cell = ws.cell(row=start_row + offset, column=col, value=value)
            cell.border = BORDER
            if col in formats and isinstance(value, (int, float)):
                cell.number_format = formats[col]
            cell.alignment = Alignment(
                horizontal="right" if isinstance(value, (int, float)) else "left",
                vertical="center",
            )


def build(example_data: dict, valuation: dict, cca: dict = None) -> BytesIO:
    meta = example_data.get("meta", {})
    metrics = example_data.get("metrics", {})
    holdings = example_data.get("holdings", [])
    advanced = example_data.get("advanced_risk", {})
    stress = example_data.get("stress_test", {})
    factors = example_data.get("factor_exposure", {})
    charts = example_data.get("charts", {})
    file_title = "Portfolio Optimizer — analysis export"

    wb = Workbook()

    # Summary -------------------------------------------------------------
    ws = wb.active
    ws.title = "Summary"
    _title(ws, "Portfolio Optimizer", file_title)
    ws["A3"] = meta.get("name", "")
    ws["A4"] = f"Computed {str(meta.get('computed_at', ''))[:10]} · period {meta.get('period', '')} · {meta.get('n_assets', len(holdings))} assets"
    ws["A4"].font = SUBTLE
    ws["A6"] = "Key metrics"
    ws["A6"].font = Font(bold=True, color=BRASS, size=11)

    summary_rows = [
        ("Annualised return", metrics.get("annualized_return"), PCT),
        ("Annualised volatility", metrics.get("volatility"), PCT),
        ("Sharpe ratio", metrics.get("sharpe_ratio"), NUM2),
        ("Sortino ratio", metrics.get("sortino_ratio"), NUM2),
        ("Maximum drawdown", metrics.get("max_drawdown"), PCT),
        ("Calmar ratio", metrics.get("calmar_ratio"), NUM2),
        ("VaR 95% (daily, loss positive)", metrics.get("var_95"), PCT),
        ("CVaR 95% (daily, loss positive)", metrics.get("cvar_95"), PCT),
        ("Beta vs S&P 500", metrics.get("beta"), NUM2),
        ("Alpha (annualised)", metrics.get("alpha"), PCT),
        ("Skewness", metrics.get("skewness"), NUM2),
        ("Excess kurtosis", metrics.get("kurtosis"), NUM2),
        ("Effective number of bets", metrics.get("effective_n"), NUM2),
        ("Concentration (HHI)", metrics.get("concentration"), NUM2),
    ]
    _table(ws, 7, [[label, value] for label, value, _ in summary_rows],
           formats={2: PCT})
    for offset, (_, _, fmt) in enumerate(summary_rows):
        ws.cell(row=7 + offset, column=2).number_format = fmt

    ws["D6"] = "Valuation"
    ws["D6"].font = Font(bold=True, color=BRASS, size=11)
    valuation_rows = [
        ("Owner earnings yield", valuation.get("owner_yield"), PCT),
        ("Implied return (owner yield + growth)", valuation.get("implied_return"), PCT),
        ("Cost of equity (CAPM)", valuation.get("cost_of_equity"), PCT),
        ("Spread", valuation.get("spread"), PCT),
        ("Portfolio beta (fundamentals)", valuation.get("beta"), NUM2),
        ("Equity risk premium", valuation.get("erp", {}).get("value"), PCT),
        ("ERP source", valuation.get("erp", {}).get("source"), None),
        ("Coverage by weight", valuation.get("coverage"), PCT),
    ]
    _table(ws, 7, valuation_rows, formats={2: PCT})
    for offset, (_, _, fmt) in enumerate(valuation_rows):
        cell = ws.cell(row=7 + offset, column=4)
        if fmt:
            cell.number_format = fmt

    ws["A24"] = "Conventions"
    ws["A24"].font = Font(bold=True, color=BRASS, size=11)
    notes = [
        "Returns are arithmetic daily returns annualised by 252 trading days.",
        "VaR and CVaR are reported as positive loss magnitudes at the stated confidence level.",
        "Sharpe uses the configured risk-free rate; Sortino uses downside deviation.",
        "Beta and alpha are estimated against the S&P 500 over the analysis window.",
        "Owner earnings yield uses trailing earnings yield (1/PE), falling back to FCF/market cap.",
        "Cost of equity is CAPM with the Damodaran implied ERP; see the Valuation sheet.",
    ]
    for offset, note in enumerate(notes):
        ws.cell(row=25 + offset, column=1, value=f"• {note}").font = SUBTLE

    _widths(ws, [30, 18, 4, 46, 18])

    # Holdings ------------------------------------------------------------
    ws = wb.create_sheet("Holdings")
    _title(ws, "Holdings", "Weights, prices and market values")
    _header(ws, 4, ["Ticker", "Company", "Sector", "Weight", "Price", "Market value"])
    portfolio_value = 100000
    for row, holding in enumerate(holdings, start=5):
        weight = float(holding.get("weight") or 0)
        ws.cell(row=row, column=1, value=holding.get("ticker"))
        ws.cell(row=row, column=2, value=holding.get("name"))
        ws.cell(row=row, column=3, value=holding.get("sector"))
        ws.cell(row=row, column=4, value=weight).number_format = PCT
        ws.cell(row=row, column=5, value=holding.get("price")).number_format = USD2
        ws.cell(row=row, column=6, value=f"=D{row}*{portfolio_value}").number_format = USD
    total_row = 5 + len(holdings)
    ws.cell(row=total_row, column=1, value="Total").font = Font(bold=True)
    ws.cell(row=total_row, column=4, value=f"=SUM(D5:D{total_row - 1})").number_format = PCT
    ws.cell(row=total_row, column=6, value=f"=SUM(F5:F{total_row - 1})").number_format = USD
    _widths(ws, [10, 30, 24, 12, 14, 16])
    ws.freeze_panes = "A5"

    # Risk ----------------------------------------------------------------
    ws = wb.create_sheet("Risk")
    _title(ws, "Risk metrics", "Portfolio-level risk and tail statistics")
    _header(ws, 4, ["Metric", "Value", "Definition"])
    risk_rows = [
        ("Annualised return", metrics.get("annualized_return"), "Geometric annualised return", PCT),
        ("Annualised volatility", metrics.get("volatility"), "Standard deviation of daily returns × √252", PCT),
        ("Downside volatility", metrics.get("downside_volatility"), "Deviation of negative returns × √252", PCT),
        ("Sharpe", metrics.get("sharpe_ratio"), "(Return − risk-free) / volatility", NUM2),
        ("Sortino", metrics.get("sortino_ratio"), "(Return − risk-free) / downside deviation", NUM2),
        ("Calmar", metrics.get("calmar_ratio"), "Annualised return / |maximum drawdown|", NUM2),
        ("Information ratio", metrics.get("information_ratio"), "Excess return / tracking error", NUM2),
        ("Maximum drawdown", metrics.get("max_drawdown"), "Largest peak-to-trough decline", PCT),
        ("Average drawdown", advanced.get("avg_drawdown"), "Mean of all drawdown episodes", PCT),
        ("VaR 95%", metrics.get("var_95"), "Parametric daily loss at 95%", PCT),
        ("VaR 99%", metrics.get("var_99"), "Parametric daily loss at 99%", PCT),
        ("CVaR 95%", metrics.get("cvar_95"), "Expected shortfall beyond VaR 95%", PCT),
        ("CVaR 99%", metrics.get("cvar_99"), "Expected shortfall beyond VaR 99%", PCT),
        ("mVaR 95", advanced.get("mvar_95"), "Marginal contribution to VaR 95%", PCT),
        ("Beta", metrics.get("beta"), "Sensitivity to the S&P 500", NUM2),
        ("Alpha", metrics.get("alpha"), "Annualised Jensen alpha", PCT),
        ("Skewness", metrics.get("skewness"), "Third standardised moment", NUM2),
        ("Kurtosis", metrics.get("kurtosis"), "Fourth standardised moment (excess)", NUM2),
        ("Kelly fraction", advanced.get("kelly_fraction"), "Full-Kelly optimal leverage", NUM2),
        ("Growth rate", advanced.get("growth_rate"), "Expected log growth at current weights", PCT),
    ]
    _table(ws, 5, [[a, b, c] for a, b, c, _ in risk_rows])
    for offset, (_, _, _, fmt) in enumerate(risk_rows):
        ws.cell(row=5 + offset, column=2).number_format = fmt
    _widths(ws, [26, 14, 52])

    # Contributions -------------------------------------------------------
    rc = charts.get("risk_contribution", {})
    ws = wb.create_sheet("Contributions")
    _title(ws, "Risk contribution", "Component and marginal contribution to risk")
    _header(ws, 4, ["Ticker", "Weight", "Risk contribution", "Component risk", "Marginal risk"])
    tickers = rc.get("tickers", [])
    for row, ticker in enumerate(tickers, start=5):
        index = row - 5
        ws.cell(row=row, column=1, value=ticker)
        ws.cell(row=row, column=2, value=rc.get("weights", [None] * len(tickers))[index]).number_format = PCT
        ws.cell(row=row, column=3, value=rc.get("risk_contrib_pct", [None] * len(tickers))[index] / 100).number_format = PCT
        ws.cell(row=row, column=4, value=rc.get("component_risk", [None] * len(tickers))[index]).number_format = PCT
        ws.cell(row=row, column=5, value=rc.get("marginal_risk", [None] * len(tickers))[index]).number_format = NUM2
    _widths(ws, [10, 12, 20, 16, 14])

    # Correlation ---------------------------------------------------------
    matrix = example_data.get("correlation_matrix", {})
    ws = wb.create_sheet("Correlation")
    _title(ws, "Correlation matrix", "Daily return correlations over the analysis window")
    names = sorted(matrix)
    _header(ws, 4, ["Ticker"] + names)
    for row_index, name in enumerate(names, start=5):
        ws.cell(row=row_index, column=1, value=name).font = Font(bold=True)
        for col_index, other in enumerate(names, start=2):
            value = matrix.get(name, {}).get(other)
            cell = ws.cell(row=row_index, column=col_index, value=value)
            cell.number_format = NUM2
            if isinstance(value, (int, float)):
                strength = 1 - min(abs(value), 1)
                base = (181, 84, 74) if value < 0 else (79, 122, 92)
                colour = "".join(f"{int(channel + (255 - channel) * strength):02x}" for channel in base)
                cell.fill = PatternFill("solid", fgColor=colour)
    _widths(ws, [10] + [8] * len(names))

    # Monthly returns -----------------------------------------------------
    mr = charts.get("monthly_returns", {})
    ws = wb.create_sheet("Monthly returns")
    _title(ws, "Monthly returns", "Calendar-month returns and annual totals")
    _header(ws, 4, ["Year"] + mr.get("months", []) + ["Year total"])
    for row_index, year in enumerate(mr.get("years", []), start=5):
        ws.cell(row=row_index, column=1, value=year).font = Font(bold=True)
        values = mr.get("data", [])[row_index - 5]
        for col_index, value in enumerate(values, start=2):
            cell = ws.cell(row=row_index, column=col_index, value=value)
            cell.number_format = PCT
        first = get_column_letter(2)
        last = get_column_letter(1 + len(values))
        formula = f"=PRODUCT(1+{first}{row_index}:{last}{row_index})-1"
        ws.cell(row=row_index, column=2 + len(values), value=formula).number_format = PCT
    _widths(ws, [8] + [10] * len(mr.get("months", [])) + [12])

    # Stress --------------------------------------------------------------
    ws = wb.create_sheet("Stress")
    _title(ws, "Stress scenarios", "Historical and hypothetical shocks")
    _header(ws, 4, ["Scenario", "Impact"])
    for row, (scenario, impact) in enumerate(stress.items(), start=5):
        ws.cell(row=row, column=1, value=scenario)
        ws.cell(row=row, column=2, value=impact).number_format = PCT
    _widths(ws, [36, 14])

    # Factors -------------------------------------------------------------
    ws = wb.create_sheet("Factors")
    _title(ws, "Factor exposure", "Fama–French five-factor plus momentum")
    _header(ws, 4, ["Factor", "Beta", "t-stat"])
    factor_rows = [
        ("Market", factors.get("market_beta"), None),
        ("Size (SMB)", factors.get("smb_beta"), None),
        ("Value (HML)", factors.get("hml_beta"), None),
        ("Momentum", factors.get("mom_beta"), None),
        ("Profitability (RMW)", factors.get("rmw_beta"), None),
        ("Investment (CMA)", factors.get("cma_beta"), None),
        ("Alpha", factors.get("alpha"), factors.get("alpha_tstat")),
        ("R²", factors.get("r_squared"), None),
        ("Adjusted R²", factors.get("adj_r_squared"), None),
    ]
    _table(ws, 5, [list(row) for row in factor_rows])
    for offset in range(len(factor_rows)):
        ws.cell(row=5 + offset, column=2).number_format = NUM2
        ws.cell(row=5 + offset, column=3).number_format = NUM2
    _widths(ws, [24, 12, 10])

    # Valuation -----------------------------------------------------------
    ws = wb.create_sheet("Valuation")
    _title(ws, "Valuation", "Owner-earnings implied return versus CAPM cost of equity")
    ws["A4"] = f"ERP {valuation.get('erp', {}).get('value', 0):.2%} · {valuation.get('erp', {}).get('source', '')}"
    ws["A4"].font = SUBTLE
    valuation_rows = [
        ("Owner earnings yield", valuation.get("owner_yield"), PCT),
        ("Growth assumption", 0.025, PCT),
        ("Implied return", valuation.get("implied_return"), PCT),
        ("Risk-free rate", valuation.get("risk_free"), PCT),
        ("Beta", valuation.get("beta"), NUM2),
        ("Cost of equity", valuation.get("cost_of_equity"), PCT),
        ("Spread", valuation.get("spread"), PCT),
        ("Coverage", valuation.get("coverage"), PCT),
    ]
    _header(ws, 6, ["Input", "Value"])
    _table(ws, 7, valuation_rows)
    for offset, (_, _, fmt) in enumerate(valuation_rows):
        ws.cell(row=7 + offset, column=2).number_format = fmt

    ws["D6"] = "Sensitivity: implied return − cost of equity"
    ws["D6"].font = Font(bold=True, color=BRASS, size=11)
    sensitivity = valuation.get("sensitivity", [])
    if sensitivity:
        columns = [key for key in sensitivity[0].keys() if key.startswith("erp_")]
        ws.cell(row=7, column=4, value="Growth \\ ERP")
        for col_index, key in enumerate(columns, start=5):
            ws.cell(row=7, column=col_index, value=int(key.split("_")[1]) / 100).number_format = PCT
        for row_index, row in enumerate(sensitivity, start=8):
            ws.cell(row=row_index, column=4, value=row["growth"]).number_format = PCT
            for col_index, key in enumerate(columns, start=5):
                ws.cell(row=row_index, column=col_index, value=row.get(key)).number_format = PCT
    _widths(ws, [26, 14, 4, 14, 12, 12])


    # Comparable companies ----------------------------------------------
    if cca and cca.get("rows"):
        ws = wb.create_sheet("CCA")
        _title(ws, "Comparable company analysis", "Peer median P/E and implied value")
        _header(ws, 4, ["Ticker", "Weight", "P/E", "Peers", "Peer median P/E", "Implied price", "Upside"])
        for row, item in enumerate(cca["rows"], start=5):
            ws.cell(row=row, column=1, value=item.get("ticker"))
            ws.cell(row=row, column=2, value=item.get("weight") or 0).number_format = PCT
            ws.cell(row=row, column=3, value=item.get("pe")).number_format = NUM2
            ws.cell(row=row, column=4, value=", ".join(item.get("peers") or []))
            ws.cell(row=row, column=5, value=item.get("peer_median_pe")).number_format = NUM2
            ws.cell(row=row, column=6, value=item.get("implied_price")).number_format = USD2
            ws.cell(row=row, column=7, value=item.get("upside")).number_format = PCT
        ws.cell(row=6 + len(cca["rows"]), column=1, value=cca.get("method", "")).font = SUBTLE
        _widths(ws, [10, 10, 10, 30, 16, 16, 12])

    # Methodology ---------------------------------------------------------
    ws = wb.create_sheet("Methodology")
    _title(ws, "Methodology and sources", "How each block is computed")
    notes = [
        ("Data", "Daily adjusted closes from Yahoo Finance; 2-year window; 252 trading days."),
        ("Optimisation", "Max Sharpe, min variance, mean-variance, risk parity; Ledoit–Wolf shrinkage; Black–Litterman; HRP."),
        ("Risk", "Parametric and historical VaR/CVaR; drawdown analysis; marginal and component VaR."),
        ("Distribution", "Jarque–Bera, Shapiro–Wilk, Anderson–Darling; Normal, Laplace, Student-t by AIC."),
        ("Factors", "Fama–French 3/5 plus momentum, estimated by OLS with HAC-robust inference."),
        ("Valuation", "Owner earnings yield (1/PE, else FCF/market cap) plus growth versus CAPM."),
        ("ERP", "Damodaran implied ERP (FCFE), histimpl.xls, cached for 30 days."),
        ("Limitations", "Educational project; no audit; no investment advice; historical data only."),
    ]
    _header(ws, 4, ["Item", "Detail"])
    _table(ws, 5, [list(row) for row in notes])
    _widths(ws, [18, 110])

    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream
