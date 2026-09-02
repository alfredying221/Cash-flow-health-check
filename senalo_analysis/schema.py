from __future__ import annotations

from math import isfinite

import pandas as pd


REQUIRED_COLUMNS = [
    "Month",
    "Sales",
    "Direct Costs",
    "Labour Cost",
    "Occupancy Cost",
    "Other Operating Costs",
]

MONEY_COLUMNS = [
    "Sales",
    "Direct Costs",
    "Labour Cost",
    "Occupancy Cost",
    "Other Operating Costs",
]

LEGACY_REQUIRED_COLUMNS = [
    "Month",
    "Revenue",
    "COGS",
    "Payroll",
    "Rent",
    "Marketing",
    "Other Expenses",
]

PAID_ACCESS_DEFAULT = False

DEFAULT_DOWNSIDE_ADJUSTMENT = -0.15

DEFAULT_UPSIDE_ADJUSTMENT = 0.15

BUSINESS_TYPES = [
    "Food & Beverage",
    "Market Stall / Vendor",
    "Independent Retail",
    "Other Owner-Operated Business",
]

BUSINESS_TYPE_LABELS = {
    "Food & Beverage": {
        "direct_costs": "Food & Beverage Cost",
        "occupancy_cost": "Rent / Occupancy Cost",
    },
    "Market Stall / Vendor": {
        "direct_costs": "Cost of Goods / Direct Costs",
        "occupancy_cost": "Stall / Site Fees",
    },
    "Independent Retail": {
        "direct_costs": "Cost of Goods / Direct Costs",
        "occupancy_cost": "Rent / Occupancy Cost",
    },
    "Other Owner-Operated Business": {
        "direct_costs": "Direct Costs",
        "occupancy_cost": "Occupancy / Site Cost",
    },
}


def money(value: float) -> str:
    return f"${value:,.0f}"


def percent(value: float) -> str:
    return f"{value:.1%}"


def runway_label(value: float, cash_balance: float | None = None) -> str:
    if cash_balance is not None and cash_balance <= 0:
        return "Cash Depleted"
    if value == float("inf"):
        return "Cash Generating"
    if value > 24:
        return "24+ months"
    if value <= 0:
        return "0.0 months"
    return f"{value:.1f} months"


def safe_divide(numerator: float, denominator: float) -> float:
    if denominator == 0 or pd.isna(denominator):
        return 0.0
    result = numerator / denominator
    return float(result) if isfinite(result) else 0.0


def business_labels(business_type: str) -> dict[str, str]:
    labels = {
        "sales": "Sales",
        "direct_costs": "Direct Costs",
        "labour_cost": "Labour Cost",
        "occupancy_cost": "Occupancy Cost",
        "other_operating_costs": "Other Operating Costs",
    }
    labels.update(BUSINESS_TYPE_LABELS.get(business_type, {}))
    return labels


def occupancy_percent_label(business_type: str) -> str:
    return f"{business_labels(business_type)['occupancy_cost']} %"
