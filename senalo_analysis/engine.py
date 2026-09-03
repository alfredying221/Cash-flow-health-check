from __future__ import annotations

import pandas as pd

from .schema import REQUIRED_COLUMNS, money, percent, runway_label, safe_divide


def calculate_financials(df: pd.DataFrame, opening_cash: float) -> pd.DataFrame:
    result = df.copy()
    result["Gross Profit"] = result["Sales"] - result["Direct Costs"]
    result["Gross Margin"] = result.apply(
        lambda row: safe_divide(row["Gross Profit"], row["Sales"]), axis=1
    )
    result["Direct Cost %"] = result.apply(
        lambda row: safe_divide(row["Direct Costs"], row["Sales"]), axis=1
    )
    result["Labour Cost %"] = result.apply(
        lambda row: safe_divide(row["Labour Cost"], row["Sales"]), axis=1
    )
    result["Occupancy Cost %"] = result.apply(
        lambda row: safe_divide(row["Occupancy Cost"], row["Sales"]), axis=1
    )
    result["Other Operating Costs %"] = result.apply(
        lambda row: safe_divide(row["Other Operating Costs"], row["Sales"]), axis=1
    )
    result["Prime Cost"] = result["Direct Costs"] + result["Labour Cost"]
    result["Prime Cost %"] = result.apply(
        lambda row: safe_divide(row["Prime Cost"], row["Sales"]), axis=1
    )
    result["Operating Expenses"] = (
        result["Labour Cost"] + result["Occupancy Cost"] + result["Other Operating Costs"]
    )
    result["Operating Profit"] = result["Gross Profit"] - result["Operating Expenses"]
    result["Operating Margin"] = result.apply(
        lambda row: safe_divide(row["Operating Profit"], row["Sales"]), axis=1
    )
    result["Estimated Cash Movement"] = result["Operating Profit"]
    result["Estimated Closing Cash Balance"] = opening_cash + result["Estimated Cash Movement"].cumsum()
    result["Break-even Sales"] = result.apply(
        lambda row: safe_divide(row["Operating Expenses"], row["Gross Margin"])
        if row["Gross Margin"] > 0
        else 0.0,
        axis=1,
    )
    return result


def calculate_burn_and_runway(closing_cash: float, cash_flows: pd.Series) -> tuple[float, float]:
    if (cash_flows > 0).all():
        return 0.0, float("inf")
    burn_months = cash_flows[cash_flows < 0]
    if burn_months.empty:
        return 0.0, float("inf")
    average_burn = abs(float(burn_months.mean()))
    if closing_cash <= 0:
        return average_burn, 0.0
    return average_burn, closing_cash / average_burn


def build_forecast(
    history: pd.DataFrame,
    starting_cash: float,
    sales_growth: float,
    direct_costs_percentage: float,
    labour_cost_growth: float,
    occupancy_cost_growth: float,
    other_opex_growth: float,
) -> pd.DataFrame:
    last = history.iloc[-1]
    rows = []
    prior_sales = float(last["Sales"])
    prior_labour_cost = float(last["Labour Cost"])
    prior_occupancy_cost = float(last["Occupancy Cost"])
    prior_other_operating_costs = float(last["Other Operating Costs"])
    prior_month = pd.Timestamp(last["Month"])

    for offset in range(1, 13):
        month = prior_month + pd.DateOffset(months=offset)
        sales = prior_sales * ((1 + sales_growth) ** offset)
        labour_cost = prior_labour_cost * ((1 + labour_cost_growth) ** offset)
        other_operating_costs = prior_other_operating_costs * ((1 + other_opex_growth) ** offset)
        occupancy_cost = prior_occupancy_cost * ((1 + occupancy_cost_growth) ** offset)
        direct_costs = sales * direct_costs_percentage

        rows.append(
            {
                "Month": month,
                "Sales": sales,
                "Direct Costs": direct_costs,
                "Labour Cost": labour_cost,
                "Occupancy Cost": occupancy_cost,
                "Other Operating Costs": other_operating_costs,
            }
        )

    return calculate_financials(pd.DataFrame(rows), starting_cash)


def build_scenario_from_base(
    base_forecast: pd.DataFrame,
    starting_cash: float,
    scenario_name: str,
    sales_adjustment: float,
    direct_costs_percentage: float,
) -> tuple[pd.DataFrame, dict[str, float | str]]:
    scenario = base_forecast[
        ["Month", "Sales", "Labour Cost", "Occupancy Cost", "Other Operating Costs"]
    ].copy()
    scenario["Sales"] = scenario["Sales"] * (1 + sales_adjustment)
    scenario["Direct Costs"] = scenario["Sales"] * direct_costs_percentage
    scenario = scenario[REQUIRED_COLUMNS]
    scenario = calculate_financials(scenario, starting_cash)
    scenario_metrics = summarize_metrics(scenario)
    return scenario, {
        "Scenario": scenario_name,
        "Sales Adjustment": sales_adjustment,
        "Sales": scenario_metrics["sales"],
        "Operating Profit": scenario_metrics["operating_profit"],
        "Estimated Ending Cash Balance": scenario_metrics["cash_balance"],
        "Cash Runway": runway_label(
            float(scenario_metrics["cash_runway"]), float(scenario_metrics["cash_balance"])
        ),
        "Operating Margin": scenario_metrics["operating_margin"],
    }


def summarize_metrics(financials: pd.DataFrame) -> dict[str, float]:
    latest = financials.iloc[-1]
    total_sales = float(financials["Sales"].sum())
    total_direct_costs = float(financials["Direct Costs"].sum())
    total_gross_profit = float(financials["Gross Profit"].sum())
    total_labour_cost = float(financials["Labour Cost"].sum())
    total_occupancy_cost = float(financials["Occupancy Cost"].sum())
    total_other_operating_costs = float(financials["Other Operating Costs"].sum())
    total_prime_cost = total_direct_costs + total_labour_cost
    total_operating_profit = float(financials["Operating Profit"].sum())
    latest_sales = float(latest["Sales"])
    break_even_sales = float(latest["Break-even Sales"])
    average_burn, runway = calculate_burn_and_runway(
        float(latest["Estimated Closing Cash Balance"]), financials["Estimated Cash Movement"]
    )
    return {
        "sales": total_sales,
        "gross_profit": total_gross_profit,
        "gross_margin": float(total_gross_profit / total_sales)
        if total_sales != 0
        else 0.0,
        "direct_costs_percentage": safe_divide(total_direct_costs, total_sales),
        "labour_cost_percentage": safe_divide(total_labour_cost, total_sales),
        "occupancy_cost_percentage": safe_divide(total_occupancy_cost, total_sales),
        "other_operating_costs_percentage": safe_divide(total_other_operating_costs, total_sales),
        "prime_cost": total_prime_cost,
        "prime_cost_percentage": safe_divide(total_prime_cost, total_sales),
        "operating_profit": total_operating_profit,
        "operating_margin": safe_divide(total_operating_profit, total_sales),
        "latest_sales": latest_sales,
        "cash_balance": float(latest["Estimated Closing Cash Balance"]),
        "cash_runway": runway,
        "average_burn": average_burn,
        "break_even_sales": break_even_sales,
        "break_even_buffer": safe_divide(latest_sales - break_even_sales, latest_sales),
    }


def sales_growth_rate(financials: pd.DataFrame) -> float:
    first = float(financials.iloc[0]["Sales"])
    last = float(financials.iloc[-1]["Sales"])
    return safe_divide(last - first, abs(first))


def expense_growth_rate(financials: pd.DataFrame) -> float:
    first = float(financials.iloc[0]["Operating Expenses"])
    last = float(financials.iloc[-1]["Operating Expenses"])
    return safe_divide(last - first, abs(first))


def expense_trend_score(sales_growth: float, expense_growth: float) -> dict[str, int | str]:
    leverage_gap = sales_growth - expense_growth
    if (expense_growth < 0 and sales_growth >= 0) or leverage_gap >= 0.10:
        return score_component(
            15,
            15,
            f"Expense growth is materially below sales growth; leverage gap is {percent(leverage_gap)}",
        )
    if leverage_gap >= 0.05:
        return score_component(
            12,
            15,
            f"Expense growth is moderately below sales growth; leverage gap is {percent(leverage_gap)}",
        )
    if leverage_gap >= -0.03:
        return score_component(
            8,
            15,
            f"Expenses and sales moved at approximately similar rates; leverage gap is {percent(leverage_gap)}",
        )
    if leverage_gap >= -0.10:
        return score_component(
            4,
            15,
            f"Expenses grew moderately faster than sales; leverage gap is {percent(leverage_gap)}",
        )
    return score_component(
        0,
        15,
        f"Expenses grew materially faster than sales; leverage gap is {percent(leverage_gap)}",
    )


def score_component(value: int, maximum: int, reason: str) -> dict[str, int | str]:
    return {"Score": value, "Maximum Score": maximum, "Reason": reason}


def score_cash_health(financials: pd.DataFrame, metrics: dict[str, float]) -> tuple[int, str, pd.DataFrame]:
    historical_margin = metrics["operating_margin"]
    rev_growth = sales_growth_rate(financials)
    exp_growth = expense_growth_rate(financials)
    buffer = metrics["break_even_buffer"]
    runway = metrics["cash_runway"]

    if metrics["cash_balance"] <= 0:
        cash = score_component(0, 30, "Estimated cash resources are depleted")
    elif runway == float("inf"):
        cash = score_component(30, 30, "Business is cash generating")
    elif runway >= 24:
        cash = score_component(30, 30, f"Estimated cash runway is {runway:.1f} months")
    elif runway >= 12:
        cash = score_component(24, 30, f"Estimated cash runway is {runway:.1f} months")
    elif runway >= 6:
        cash = score_component(18, 30, f"Estimated cash runway is {runway:.1f} months")
    elif runway >= 3:
        cash = score_component(10, 30, f"Estimated cash runway is {runway:.1f} months")
    elif runway > 0:
        cash = score_component(5, 30, f"Estimated cash runway is {runway:.1f} months")
    else:
        cash = score_component(0, 30, "Estimated cash balance is depleted")

    if historical_margin >= 0.20:
        margin = score_component(25, 25, f"Operating margin is {percent(historical_margin)}")
    elif historical_margin >= 0.10:
        margin = score_component(20, 25, f"Operating margin is {percent(historical_margin)}")
    elif historical_margin >= 0.05:
        margin = score_component(15, 25, f"Operating margin is {percent(historical_margin)}")
    elif historical_margin >= 0:
        margin = score_component(10, 25, f"Operating margin is {percent(historical_margin)}")
    elif historical_margin >= -0.05:
        margin = score_component(5, 25, f"Operating margin is {percent(historical_margin)}")
    else:
        margin = score_component(0, 25, f"Operating margin is {percent(historical_margin)}")

    if rev_growth >= 0.10:
        sales = score_component(20, 20, f"Sales increased by {percent(rev_growth)} over the period")
    elif rev_growth >= 0.03:
        sales = score_component(16, 20, f"Sales increased by {percent(rev_growth)} over the period")
    elif rev_growth >= 0:
        sales = score_component(12, 20, f"Sales were broadly flat at {percent(rev_growth)} growth")
    elif rev_growth >= -0.05:
        sales = score_component(6, 20, f"Sales declined by {percent(abs(rev_growth))} over the period")
    else:
        sales = score_component(0, 20, f"Sales declined by {percent(abs(rev_growth))} over the period")

    expense = expense_trend_score(rev_growth, exp_growth)

    if buffer >= 0.25:
        break_even = score_component(10, 10, f"Latest sales is {percent(buffer)} above break-even")
    elif buffer >= 0.10:
        break_even = score_component(8, 10, f"Latest sales is {percent(buffer)} above break-even")
    elif buffer >= 0:
        break_even = score_component(5, 10, f"Latest sales is {percent(buffer)} above break-even")
    elif buffer >= -0.10:
        break_even = score_component(2, 10, f"Latest sales is {percent(abs(buffer))} below break-even")
    else:
        break_even = score_component(0, 10, f"Latest sales is {percent(abs(buffer))} below break-even")

    breakdown = pd.DataFrame(
        [
            {"Factor": "Cash Position / Runway", **cash},
            {"Factor": "Operating Margin", **margin},
            {"Factor": "Sales Trend", **sales},
            {"Factor": "Expense Trend", **expense},
            {"Factor": "Break-even Buffer", **break_even},
        ]
    )
    score = int(breakdown["Score"].sum())
    if score >= 80:
        label = "Healthy"
    elif score >= 60:
        label = "Stable"
    elif score >= 40:
        label = "Watch"
    else:
        label = "At Risk"
    return score, label, breakdown


def make_cfo_summary(
    financials: pd.DataFrame,
    metrics: dict[str, float],
    score_label: str,
    downside_metrics: dict[str, float] | None = None,
) -> list[str]:
    rev_growth = sales_growth_rate(financials)
    exp_growth = expense_growth_rate(financials)
    latest = financials.iloc[-1]
    first = financials.iloc[0]
    break_even_delta = metrics["break_even_buffer"]

    if rev_growth > 0.05:
        sales_line = (
            f"Sales increased from approximately {money(float(first['Sales']))} to "
            f"{money(float(latest['Sales']))} per month ({percent(rev_growth)} total growth)."
        )
    elif rev_growth < -0.05:
        sales_line = (
            f"Sales declined from approximately {money(float(first['Sales']))} to "
            f"{money(float(latest['Sales']))} per month ({percent(abs(rev_growth))} total decline)."
        )
    else:
        sales_line = (
            f"Sales were broadly flat, moving from approximately {money(float(first['Sales']))} to "
            f"{money(float(latest['Sales']))} per month."
        )

    if metrics["operating_margin"] >= 0.15:
        margin_note = "strong operating profitability"
    elif metrics["operating_margin"] >= 0.05:
        margin_note = "positive but modest operating profitability"
    elif metrics["operating_margin"] >= 0:
        margin_note = "thin operating profitability"
    else:
        margin_note = "an operating loss"
    profit_line = (
        f"Gross margin is {percent(metrics['gross_margin'])} and operating margin is "
        f"{percent(metrics['operating_margin'])}, indicating {margin_note}."
    )
    cash_line = (
        f"Estimated cash balance is approximately {money(metrics['cash_balance'])}, with "
        f"runway shown as {runway_label(metrics['cash_runway'], metrics['cash_balance'])}."
    )
    if break_even_delta >= 0:
        break_even_line = (
            f"Estimated break-even sales is approximately {money(metrics['break_even_sales'])} per month; "
            f"latest monthly sales is approximately {percent(break_even_delta)} above break-even."
        )
    else:
        break_even_line = (
            f"Estimated break-even sales is approximately {money(metrics['break_even_sales'])} per month; "
            f"latest monthly sales is approximately {percent(abs(break_even_delta))} below break-even."
        )

    downside_line = ""
    if downside_metrics is not None:
        downside_runway = runway_label(
            float(downside_metrics["cash_runway"]), float(downside_metrics["cash_balance"])
        )
        downside_line = (
            f"In the downside scenario, 12-month operating profit is "
            f"{money(float(downside_metrics['operating_profit']))}, estimated ending cash is "
            f"{money(float(downside_metrics['cash_balance']))}, and runway is {downside_runway}."
        )

    if metrics["cash_balance"] <= 0:
        risk = "Primary risk: estimated cash resources are exhausted under the current simplified cash assumptions."
    elif latest["Sales"] < latest["Break-even Sales"]:
        risk = "Primary risk: the business is operating below estimated break-even sales."
    elif metrics["operating_margin"] < 0:
        risk = "Primary risk: operating losses are reducing estimated cash resilience."
    elif exp_growth > rev_growth:
        risk = "Primary risk: operating expenses are growing faster than sales, weakening operating leverage."
    else:
        risk = "Primary risk: margin quality and downside resilience could weaken as the business grows."

    if score_label == "Healthy":
        action = "Recommended action: preserve operating leverage, maintain margin discipline, and deploy surplus cash carefully."
    elif score_label == "Stable":
        action = "Recommended action: monitor margin, cost growth, and downside resilience before committing to additional fixed costs."
    elif score_label == "Watch":
        action = "Recommended action: review pricing, reduce discretionary costs, improve gross margin, tighten working capital discipline, and preserve cash."
    else:
        action = "Recommended action: immediate management attention is required to reduce cash outflows, restore gross margin, and protect liquidity."

    risk_action_line = f"{risk} {action}"
    return [
        line
        for line in [sales_line, profit_line, cash_line, break_even_line, downside_line, risk_action_line]
        if line
    ]


def make_management_priorities(
    financials: pd.DataFrame,
    metrics: dict[str, float],
    downside_metrics: dict[str, float] | None = None,
) -> list[str]:
    priorities: list[str] = []
    sales_growth = sales_growth_rate(financials)
    first = financials.iloc[0]
    latest = financials.iloc[-1]
    labour_growth = safe_divide(
        float(latest["Labour Cost"] - first["Labour Cost"]), abs(float(first["Labour Cost"]))
    )
    direct_cost_pct_change = safe_divide(
        safe_divide(float(latest["Direct Costs"]), float(latest["Sales"]))
        - safe_divide(float(first["Direct Costs"]), float(first["Sales"])),
        1,
    )

    if metrics["cash_balance"] <= 0:
        priorities.append("Estimated cash resources are depleted; prepare an immediate cash preservation plan.")
    elif metrics["cash_runway"] != float("inf") and metrics["cash_runway"] < 6:
        priorities.append("Cash runway is limited; preserve cash and review near-term spending commitments.")

    if metrics["operating_margin"] < 0:
        priorities.append("Operating margin is negative; review pricing, direct costs and fixed operating costs.")

    if metrics["break_even_buffer"] < 0:
        priorities.append("Current sales are below calculated break-even; set a near-term sales or cost reduction target.")
    elif metrics["break_even_buffer"] < 0.10:
        priorities.append("Break-even buffer is narrow; avoid adding fixed costs until sales resilience improves.")

    if sales_growth < -0.05:
        priorities.append("Sales are declining; review product mix, pricing and customer acquisition activity.")

    if labour_growth - sales_growth > 0.10:
        priorities.append("Labour costs are growing faster than sales and should be reviewed.")

    if direct_cost_pct_change > 0.03:
        priorities.append("Direct costs are taking a larger share of sales; review supplier pricing, wastage or product mix.")

    if metrics["occupancy_cost_percentage"] > 0 and sales_growth < 0:
        priorities.append("Occupancy costs are fixed while sales are weakening; review site economics and sales recovery plans.")

    if downside_metrics is not None and downside_metrics["cash_balance"] <= 0:
        priorities.append("The downside scenario creates cash depletion; prepare a contingency plan before liquidity tightens.")

    if len(priorities) < 3 and metrics["operating_margin"] >= 0.15:
        priorities.append(
            f"Operating margin remains strong at {percent(metrics['operating_margin'])}; protect this margin before adding fixed costs."
        )

    if len(priorities) < 3 and metrics["break_even_buffer"] >= 0.20:
        priorities.append(
            f"Sales are currently {percent(metrics['break_even_buffer'])} above calculated break-even, providing a healthy operating buffer."
        )

    if len(priorities) < 3 and metrics["cash_runway"] == float("inf"):
        priorities.append(
            f"Estimated cash balance is {money(metrics['cash_balance'])} and the business is cash generating; keep forecast assumptions disciplined."
        )

    fallback = [
        "Monitor gross margin and operating margin each month before increasing fixed costs.",
        "Keep forecast assumptions conservative until recent trading performance confirms improvement.",
        "Review break-even sales regularly and compare it with the latest monthly sales result.",
    ]
    for item in fallback:
        if len(priorities) >= 3:
            break
        priorities.append(item)
    return priorities[:3]


def make_free_interpretation(metrics: dict[str, float], score_label: str) -> list[str]:
    if metrics["break_even_buffer"] >= 0:
        break_even_text = (
            f"Latest sales is {percent(metrics['break_even_buffer'])} above estimated break-even."
        )
    else:
        break_even_text = (
            f"Latest sales is {percent(abs(metrics['break_even_buffer']))} below estimated break-even."
        )
    cash_text = (
        f"Estimated cash position is {money(metrics['cash_balance'])}, with runway shown as "
        f"{runway_label(metrics['cash_runway'], metrics['cash_balance'])}."
    )
    profit_text = (
        f"Status is {score_label}; historical operating margin is {percent(metrics['operating_margin'])}."
    )
    return [profit_text, cash_text, break_even_text]
