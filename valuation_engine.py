"""Pure financial helpers for the V7 technology valuation terminal.

Amounts passed to these functions use one consistent currency and unit (normally
millions); share counts use actual shares. Missing inputs stay missing.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping, Optional


def _num(x):
    try:
        y = float(x)
        return y if isfinite(y) else None
    except (TypeError, ValueError):
        return None


def estimate_wacc(risk_free_rate: float, beta: float, equity_risk_premium: float,
                  pre_tax_cost_of_debt: float, tax_rate: float,
                  market_equity: float, market_debt: float) -> dict:
    """CAPM cost of equity and market-value weighted after-tax WACC; rates decimal."""
    vals = [_num(v) for v in (risk_free_rate, beta, equity_risk_premium,
                               pre_tax_cost_of_debt, tax_rate, market_equity, market_debt)]
    if any(v is None for v in vals):
        raise ValueError("Todos los componentes del WACC son obligatorios.")
    rf, b, erp, rd, tax, eq, debt = vals
    if eq <= 0 or debt < 0 or not (0 <= tax <= 1) or b < 0 or rd < 0:
        raise ValueError("Pesos, beta, impuestos o coste de deuda fuera de rango.")
    re = rf + b * erp
    total = eq + debt
    wacc = eq / total * re + debt / total * rd * (1 - tax)
    return {"cost_of_equity": re, "after_tax_cost_of_debt": rd * (1-tax),
            "equity_weight": eq/total, "debt_weight": debt/total, "wacc": wacc}


@dataclass(frozen=True)
class DriverAssumptions:
    revenue_growth: tuple[float, ...]
    ebit_margin: tuple[float, ...]
    tax_rate: float
    da_pct_revenue: float
    capex_pct_revenue: float
    change_nwc_pct_incremental_revenue: float = 0.0
    terminal_growth: float = 0.025


def project_fcff(revenue: float, assumptions: DriverAssumptions) -> list[dict]:
    """Project operating drivers into FCFF: NOPAT + D&A - CAPEX - change in NWC."""
    rev = _num(revenue)
    if rev is None or rev <= 0 or not assumptions.revenue_growth:
        raise ValueError("Ingresos positivos y al menos un año de supuestos son necesarios.")
    if len(assumptions.revenue_growth) != len(assumptions.ebit_margin):
        raise ValueError("Crecimiento y margen deben tener la misma cantidad de años.")
    if not 0 <= assumptions.tax_rate <= 1 or not 0 <= assumptions.terminal_growth < 0.10:
        raise ValueError("Tasa fiscal o crecimiento terminal fuera de rango.")
    rows, prior = [], rev
    for year, (growth, margin) in enumerate(zip(assumptions.revenue_growth, assumptions.ebit_margin), 1):
        if not (-0.8 <= growth <= 2.0 and -1.0 <= margin <= 1.0):
            raise ValueError("Crecimiento o margen fuera de rangos plausibles de entrada.")
        rev = prior * (1 + growth)
        ebit = rev * margin
        nopat = ebit * (1-assumptions.tax_rate)
        da = rev * assumptions.da_pct_revenue
        capex = rev * assumptions.capex_pct_revenue
        nwc = (rev-prior) * assumptions.change_nwc_pct_incremental_revenue
        fcff = nopat + da - capex - nwc
        rows.append({"year": year, "revenue": rev, "ebit": ebit, "nopat": nopat,
                     "da": da, "capex": capex, "change_nwc": nwc, "fcff": fcff})
        prior = rev
    return rows


def dcf_from_fcff(fcff: list[float], wacc: float, terminal_growth: float,
                  net_debt: float = 0.0, shares: Optional[float] = None,
                  annual_dilution: float = 0.0, terminal_fcff_growth: Optional[float] = None) -> dict:
    """Discount explicit FCFF and terminal value; apply net debt and dilution explicitly."""
    r, gt = _num(wacc), _num(terminal_growth)
    if not fcff or r is None or gt is None or r <= gt or r <= 0:
        raise ValueError("Se requiere FCFF y WACC mayor que el crecimiento terminal.")
    if any(_num(v) is None for v in fcff):
        raise ValueError("Todos los FCFF explícitos deben ser valores numéricos.")
    if float(fcff[-1]) <= 0:
        raise ValueError("El FCFF del último año debe ser positivo para aplicar perpetuidad; alarga el periodo de recuperación.")
    if shares is not None and (_num(shares) is None or shares <= 0):
        raise ValueError("Las acciones deben ser positivas o N/D.")
    if not 0 <= annual_dilution < 0.25:
        raise ValueError("La dilución anual debe estar entre 0% y 25%.")
    pv = sum(float(v)/(1+r)**i for i, v in enumerate(fcff, 1))
    last = float(fcff[-1])
    gterm = gt if terminal_fcff_growth is None else _num(terminal_fcff_growth)
    if gterm is None or gterm >= r:
        raise ValueError("El crecimiento de FCFF terminal debe ser inferior al WACC.")
    tv = last*(1+gterm)/(r-gterm)
    pvtv = tv/(1+r)**len(fcff)
    ev = pv+pvtv
    equity = ev-float(net_debt)
    result = {"enterprise_value": ev, "equity_value": equity,
              "pv_explicit": pv, "terminal_value": tv, "pv_terminal": pvtv,
              "terminal_weight": pvtv/ev if ev else None}
    if shares is not None:
        diluted_shares = shares*(1+annual_dilution)**len(fcff)
        result.update(diluted_shares=diluted_shares,
                      value_per_share=equity*1_000_000/diluted_shares)
    return result


def scenario_values(base: Mapping, downside: Mapping, upside: Mapping) -> dict:
    """Build three transparent scenario outputs from complete assumption dictionaries."""
    return {name: project_fcff(**cfg) for name, cfg in
            (("base", base), ("downside", downside), ("upside", upside))}


def validate_market_inputs(price, shares, market_cap, financial_currency, quote_currency) -> list[str]:
    """Return auditable data quality flags; never silently infer a currency conversion."""
    flags = []
    p, s, cap = _num(price), _num(shares), _num(market_cap)
    if p is None or p <= 0: flags.append("Precio no disponible o no positivo.")
    if s is None or s <= 0: flags.append("Acciones en circulación N/D o no positivas.")
    if cap is not None and p and s and abs(cap-p*s)/(p*s) > 0.25:
        flags.append("Capitalización y precio × acciones difieren >25%; revisar clases y fechas.")
    if financial_currency and quote_currency and financial_currency.upper() != quote_currency.upper():
        flags.append(f"Monedas distintas ({financial_currency}/{quote_currency}); conversión explícita requerida.")
    return flags
