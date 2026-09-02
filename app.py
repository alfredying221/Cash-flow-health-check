from __future__ import annotations

from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

from senalo_analysis import (
    BUSINESS_TYPES,
    DEFAULT_DOWNSIDE_ADJUSTMENT,
    DEFAULT_UPSIDE_ADJUSTMENT,
    PAID_ACCESS_DEFAULT,
    build_forecast,
    build_input_template,
    build_report_pdf,
    build_scenario_from_base,
    business_labels,
    calculate_financials,
    export_excel,
    make_cfo_summary,
    make_free_interpretation,
    make_management_priorities,
    money,
    occupancy_percent_label,
    percent,
    read_uploaded_file,
    runway_label,
    sales_growth_rate,
    score_cash_health,
    summarize_metrics,
    uploaded_file_signature,
    validate_and_prepare,
)

st.set_page_config(
    page_title="Business Financial Health Check",
    page_icon=":bar_chart:",
    layout="wide",
)


CUSTOM_CSS = """
<style>
    .block-container {
        padding-top: 2rem;
        padding-bottom: 3rem;
        max-width: 1180px;
    }
    .app-subtitle {
        color: #475569;
        font-size: 1.08rem;
        margin-top: -0.75rem;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 1rem;
        min-height: 112px;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }
    .metric-label {
        color: #64748b;
        font-size: 0.85rem;
        font-weight: 600;
        margin-bottom: 0.4rem;
    }
    .metric-value {
        color: #0f172a;
        font-size: 1.55rem;
        font-weight: 750;
        line-height: 1.2;
    }
    .metric-note {
        color: #64748b;
        font-size: 0.8rem;
        margin-top: 0.4rem;
    }
    .score-band {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-left: 6px solid #2563eb;
        border-radius: 8px;
        padding: 1rem 1.1rem;
    }
    .status-healthy { border-left-color: #16a34a; }
    .status-stable { border-left-color: #2563eb; }
    .status-watch { border-left-color: #f59e0b; }
    .status-at-risk { border-left-color: #dc2626; }
    .footer-note {
        color: #64748b;
        font-size: 0.82rem;
        border-top: 1px solid #e2e8f0;
        margin-top: 2rem;
        padding-top: 1rem;
    }
    .landing-panel {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 1.25rem;
        margin: 1rem 0 1.25rem 0;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }
    .landing-panel h2 {
        margin-top: 0;
        color: #0f172a;
    }
    .cta-link {
        display: inline-block;
        background: #2563eb;
        color: #ffffff !important;
        padding: 0.7rem 1rem;
        border-radius: 6px;
        text-decoration: none;
        font-weight: 700;
        margin-top: 0.4rem;
    }
    .pricing-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 1rem;
        min-height: 300px;
    }
    .pricing-card-featured {
        border: 2px solid #2563eb;
        box-shadow: 0 8px 18px rgba(37, 99, 235, 0.12);
    }
    .plan-name {
        font-weight: 800;
        color: #0f172a;
        font-size: 1.05rem;
    }
    .plan-price {
        color: #0f172a;
        font-weight: 800;
        font-size: 1.45rem;
        margin: 0.3rem 0;
    }
    .plan-purpose {
        color: #475569;
        font-size: 0.88rem;
        margin-bottom: 0.65rem;
    }
    .upgrade-panel {
        background: #eff6ff;
        border: 1px solid #bfdbfe;
        border-left: 6px solid #2563eb;
        border-radius: 8px;
        padding: 1rem 1.1rem;
        margin-top: 1rem;
    }
</style>
"""




















def initialize_forecast_assumptions(dataset_signature: str, historical_direct_costs_percentage: float) -> None:
    if st.session_state.get("forecast_dataset_signature") == dataset_signature:
        return
    st.session_state["forecast_dataset_signature"] = dataset_signature
    st.session_state["forecast_sales_growth_pct"] = 0.0
    st.session_state["forecast_direct_costs_pct"] = historical_direct_costs_percentage * 100
    st.session_state["forecast_labour_cost_growth_pct"] = 0.0
    st.session_state["forecast_occupancy_cost_growth_pct"] = 0.0
    st.session_state["forecast_other_opex_growth_pct"] = 0.0




















































def metric_card(label: str, value: str, note: str = "") -> None:
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
            <div class="metric-note">{note}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def style_chart(fig):
    fig.update_layout(
        template="plotly_white",
        margin=dict(l=10, r=10, t=36, b=10),
        hovermode="x unified",
        height=330,
    )
    fig.update_xaxes(title=None)
    return fig


def render_landing_section() -> None:
    st.markdown(
        """
        <div class="landing-panel">
            <h2>Know How Healthy Your Business Really Is</h2>
            <p>See your margins, break-even point, cash resilience and financial risks in minutes.</p>
            <p style="color:#475569;">Built for owner-operated small businesses that want a clearer view of their numbers without building a complex financial model.</p>
            <a class="cta-link" href="#upload-section">Check My Business for Free</a>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("**In minutes, see:**")
    value_cols = st.columns(2)
    with value_cols[0]:
        st.write("- Your Financial Health Score")
        st.write("- Your estimated cash resilience")
        st.write("- Whether your business is above or below break-even")
    with value_cols[1]:
        st.write("- Key profitability trends")
        st.write("- Where management attention may be required")


def render_pricing_section() -> None:
    st.subheader("Plans")
    cols = st.columns(3)
    plans = [
        (
            "Free",
            "AUD 0",
            "Quick financial health check",
            [
                "Financial Health Score",
                "Health Status",
                "Sales and margin KPIs",
                "Estimated Cash Balance and Runway",
                "Break-even metrics",
                "Short financial interpretation",
            ],
            False,
        ),
        (
            "Founding User",
            "AUD 39 one-time",
            "Full financial health and scenario report",
            [
                "Everything in Free",
                "12-Month Forecast",
                "Base, Downside, and Upside scenarios",
                "Full CFO Summary",
                "Downloadable PDF report",
                "Downloadable Excel forecast model",
            ],
            True,
        ),
        (
            "CFO Review",
            "AUD 149 one-time",
            "Personal review of financial position and assumptions",
            [
                "Everything in Founding User",
                "Manual review of uploaded data",
                "Review of forecast assumptions",
                "Customised CFO commentary",
                "3 to 5 prioritised management actions",
            ],
            False,
        ),
    ]
    for col, (name, price, purpose, features, featured) in zip(cols, plans):
        with col:
            with st.container(border=True):
                st.markdown(f"**{name}**")
                st.markdown(f"### {price}")
                st.caption(purpose)
                if featured:
                    st.markdown("**Founding User Offer**")
                for feature in features:
                    st.write(f"- {feature}")
    st.caption(
        "CFO Review is a manually delivered service and not an automated software feature."
    )


def render_upgrade_panel() -> None:
    st.markdown(
        """
        <div class="upgrade-panel">
            <strong>Unlock the Full Business Financial Health Report</strong><br>
            <span style="font-size:1.25rem;font-weight:800;">AUD 39 one-time</span><br>
            Includes 12-month forecast, downside and upside scenarios, CFO Summary, PDF report, and Excel model.
        </div>
        """,
        unsafe_allow_html=True,
    )
    cta_cols = st.columns(2)
    with cta_cols[0]:
        if st.button("Get the Full Report", use_container_width=True):
            st.info("Founding User access is currently being offered during the pilot. Payment access will be added after pilot validation.")
    with cta_cols[1]:
        if st.button("Request a CFO Review", use_container_width=True):
            st.info("CFO Review is currently available to founding users. Contact details will be provided during the pilot.")




st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
st.title("Business Financial Health Check")
st.markdown(
    '<div class="app-subtitle">See your margins, break-even point, cash resilience and financial risks in minutes.</div>',
    unsafe_allow_html=True,
)
st.caption(
    "Built for owner-operated small businesses that want a clearer view of their numbers without building a complex financial model."
)
render_landing_section()
paid_access = PAID_ACCESS_DEFAULT

with st.sidebar:
    st.markdown('<div id="upload-section"></div>', unsafe_allow_html=True)
    st.header("Inputs")
    business_type = st.selectbox("Business Type", BUSINESS_TYPES, index=0)
    st.markdown("1. Download the template")
    st.markdown("2. Enter your monthly financial data")
    st.markdown("3. Upload the completed CSV or Excel file")
    st.download_button(
        "Download Input Template",
        data=build_input_template(),
        file_name="business_financial_health_check_input_template.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    uploaded = st.file_uploader("Upload CSV or Excel", type=["csv", "xlsx", "xls"])
    opening_cash = st.number_input(
        "Opening Cash Balance",
        value=0.0,
        step=1000.0,
        format="%.2f",
        help="Enter the cash available at the beginning of your first reporting month.",
    )
    st.caption(
        "Required columns: Month, Sales, Direct Costs, Labour Cost, Occupancy Cost, Other Operating Costs."
    )

if opening_cash < 0:
    st.error("Opening Cash Balance cannot be negative.")
    st.stop()

if uploaded is None:
    st.info("Upload your completed financial data file to generate your Business Financial Health Check.")
    st.caption("Your uploaded financial data is used only to generate the analysis during your current app session.")
    render_pricing_section()
    st.markdown(
        '<div class="footer-note">This tool provides an indicative financial analysis based on information supplied by the user. It is not accounting, tax, audit, investment, or financial advice. Results should be reviewed alongside actual cash-flow records and professional advice where appropriate.</div>',
        unsafe_allow_html=True,
    )
    st.stop()

dataset_signature = uploaded_file_signature(uploaded)
uploaded.seek(0)

try:
    raw_df = read_uploaded_file(uploaded)
except Exception as exc:
    st.error(f"Could not read the uploaded file: {exc}")
    st.stop()

prepared_df, validation_errors = validate_and_prepare(raw_df)
if validation_errors:
    st.error("Please fix the uploaded file before continuing.")
    for error in validation_errors:
        st.write(f"- {error}")
    st.stop()

history = calculate_financials(prepared_df, opening_cash)
metrics = summarize_metrics(history)
score, score_label, score_breakdown = score_cash_health(history, metrics)
initialize_forecast_assumptions(dataset_signature, metrics["direct_costs_percentage"])
labels = business_labels(business_type)

st.subheader("Dashboard")
st.caption(
    "Estimated cash movement uses operating profit as a simplified proxy for operating cash flow. "
    "It does not include working capital movements, capital expenditure, financing, tax payments, or owner distributions."
)
status_class = score_label.lower().replace(" ", "-")
status_interpretations = {
    "Healthy": "Your business is currently showing strong operating and estimated cash-flow fundamentals.",
    "Stable": "Your business is broadly stable, but some indicators should be monitored.",
    "Watch": "Your business shows financial pressure that should be addressed before liquidity weakens further.",
    "At Risk": "Your business is showing material financial stress and requires immediate management attention.",
}
st.markdown(
    f"""
    <div class="score-band status-{status_class}">
        <div class="metric-label">Financial Health Score</div>
        <div class="metric-value">{score}/100 - {score_label}</div>
        <div class="metric-note">{status_interpretations[score_label]}</div>
    </div>
    """,
    unsafe_allow_html=True,
)
if metrics["cash_balance"] <= 0:
    st.warning(
        "Estimated cash resources are exhausted within the analysed period under the current simplified cash assumptions."
    )

kpi_cols = st.columns(3)
with kpi_cols[0]:
    metric_card("Total Sales", money(metrics["sales"]), "Historical total")
with kpi_cols[1]:
    metric_card("Gross Profit", money(metrics["gross_profit"]), "Historical total")
with kpi_cols[2]:
    metric_card("Gross Margin", percent(metrics["gross_margin"]), "Historical weighted average")

kpi_cols = st.columns(3)
with kpi_cols[0]:
    metric_card("Labour Cost %", percent(metrics["labour_cost_percentage"]), "Historical weighted average")
with kpi_cols[1]:
    metric_card(occupancy_percent_label(business_type), percent(metrics["occupancy_cost_percentage"]), "Historical weighted average")
with kpi_cols[2]:
    metric_card("Operating Margin", percent(metrics["operating_margin"]), "Historical weighted average")

kpi_cols = st.columns(3)
with kpi_cols[0]:
    metric_card("Estimated Cash Position", money(metrics["cash_balance"]), "Latest estimated closing balance")
with kpi_cols[1]:
    metric_card(
        "Cash Runway",
        runway_label(metrics["cash_runway"], metrics["cash_balance"]),
        "Based on estimated cash movement",
    )
with kpi_cols[2]:
    metric_card("Sales Trend", percent(sales_growth_rate(history)), "First to latest month")

diagnostic_cols = st.columns(3)
with diagnostic_cols[0]:
    metric_card("Break-even Sales", money(metrics["break_even_sales"]), "Latest monthly estimate")
with diagnostic_cols[1]:
    metric_card("Break-even Buffer", percent(metrics["break_even_buffer"]), "Latest sales above break-even")
with diagnostic_cols[2]:
    metric_card("Operating Profit", money(metrics["operating_profit"]), "Historical total")

if business_type == "Food & Beverage":
    food_cols = st.columns(3)
    with food_cols[0]:
        metric_card("Food Cost %", percent(metrics["direct_costs_percentage"]), "Historical weighted average")
    with food_cols[1]:
        metric_card("Prime Cost", money(metrics["prime_cost"]), "Direct costs plus labour")
    with food_cols[2]:
        metric_card("Prime Cost %", percent(metrics["prime_cost_percentage"]), "Historical weighted average")

with st.expander("View score breakdown"):
    st.dataframe(score_breakdown, use_container_width=True, hide_index=True)
    st.caption(f"Component scores total {int(score_breakdown['Score'].sum())}/100.")
    st.caption(
        "Expense Trend scoring compares sales growth with operating expense growth: "
        "15 points for a leverage gap of at least 10 percentage points or declining expenses with stable/growing sales; "
        "12 for at least 5 points; 8 for approximately similar growth within -3 points; "
        "4 when expenses grow up to 10 points faster; 0 when expenses grow materially faster."
    )

burn_cols = st.columns(2)
with burn_cols[0]:
    if metrics["average_burn"] > 0:
        metric_card("Average Monthly Cash Burn", money(metrics["average_burn"]), "Average of negative estimated cash-movement months")
    else:
        metric_card("Cash Burn", "$0 / No burn", "Estimated cash movement is not negative")

chart_cols = st.columns(3)
with chart_cols[0]:
    fig = px.bar(history, x="Month", y="Sales", title="Monthly Sales")
    st.plotly_chart(style_chart(fig), use_container_width=True)
with chart_cols[1]:
    fig = px.bar(history, x="Month", y="Operating Profit", title="Monthly Operating Profit")
    st.plotly_chart(style_chart(fig), use_container_width=True)
with chart_cols[2]:
    fig = px.line(
        history,
        x="Month",
        y="Estimated Closing Cash Balance",
        markers=True,
        title="Monthly Estimated Cash Balance",
    )
    st.plotly_chart(style_chart(fig), use_container_width=True)

cost_structure = history[
    ["Month", "Direct Cost %", "Labour Cost %", "Occupancy Cost %", "Other Operating Costs %"]
].melt(id_vars="Month", var_name="Cost Type", value_name="Percentage of Sales")
cost_structure["Cost Type"] = cost_structure["Cost Type"].replace(
    {"Occupancy Cost %": occupancy_percent_label(business_type)}
)
fig = px.line(
    cost_structure,
    x="Month",
    y="Percentage of Sales",
    color="Cost Type",
    markers=True,
    title="Cost Structure as % of Sales",
)
fig.update_yaxes(tickformat=".0%")
st.plotly_chart(style_chart(fig), use_container_width=True)

st.subheader("Short Financial Interpretation")
for line in make_free_interpretation(metrics, score_label):
    st.write(f"- {line}")

sales_growth = st.session_state["forecast_sales_growth_pct"] / 100
direct_costs_percentage = st.session_state["forecast_direct_costs_pct"] / 100
labour_cost_growth = st.session_state["forecast_labour_cost_growth_pct"] / 100
other_opex_growth = st.session_state["forecast_other_opex_growth_pct"] / 100
occupancy_cost_growth = st.session_state["forecast_occupancy_cost_growth_pct"] / 100

if paid_access:
    st.subheader("12-month Forecast")
    st.caption(
        "Base Case assumes current business economics broadly continue, with no default monthly sales growth or expense growth."
    )
    assumption_cols = st.columns(5)
    with assumption_cols[0]:
        sales_growth = st.number_input(
            "Monthly sales growth",
            step=0.5,
            format="%.2f",
            key="forecast_sales_growth_pct",
        ) / 100
    with assumption_cols[1]:
        direct_costs_percentage = st.number_input(
            f"{labels['direct_costs']} %",
            min_value=0.0,
            max_value=100.0,
            step=1.0,
            format="%.2f",
            key="forecast_direct_costs_pct",
        ) / 100
    with assumption_cols[2]:
        labour_cost_growth = st.number_input(
            "Labour Cost growth",
            step=0.5,
            format="%.2f",
            key="forecast_labour_cost_growth_pct",
        ) / 100
    with assumption_cols[3]:
        occupancy_cost_growth = st.number_input(
            f"{labels['occupancy_cost']} growth",
            step=0.5,
            format="%.2f",
            key="forecast_occupancy_cost_growth_pct",
        ) / 100
    with assumption_cols[4]:
        other_opex_growth = st.number_input(
            "Other operating expense growth",
            step=0.5,
            format="%.2f",
            key="forecast_other_opex_growth_pct",
        ) / 100

forecast = build_forecast(
    history,
    metrics["cash_balance"],
    sales_growth,
    direct_costs_percentage,
    labour_cost_growth,
    occupancy_cost_growth,
    other_opex_growth,
)
forecast_metrics = summarize_metrics(forecast)

if paid_access:
    forecast_cols = st.columns(3)
    with forecast_cols[0]:
        metric_card("Forecast Sales", money(forecast_metrics["sales"]), "Next 12 months")
    with forecast_cols[1]:
        metric_card("Forecast Operating Profit", money(forecast_metrics["operating_profit"]), "Next 12 months")
    with forecast_cols[2]:
        metric_card("Forecast Estimated Cash Balance", money(forecast_metrics["cash_balance"]), "Month 12")

    forecast_runway_cols = st.columns(2)
    with forecast_runway_cols[0]:
        metric_card(
            "Forecast Cash Runway",
            runway_label(forecast_metrics["cash_runway"], forecast_metrics["cash_balance"]),
            "Based on forecast estimated cash movement",
        )
    with forecast_runway_cols[1]:
        if forecast_metrics["average_burn"] > 0:
            metric_card("Forecast Average Monthly Cash Burn", money(forecast_metrics["average_burn"]), "Average of negative forecast months")
        else:
            metric_card("Forecast Cash Burn", "$0 / No burn", "Forecast is cash generating")

    fig = px.line(
        forecast,
        x="Month",
        y=["Sales", "Operating Profit", "Estimated Closing Cash Balance"],
        title="12-month Forecast",
    )
    st.plotly_chart(style_chart(fig), use_container_width=True)

    with st.expander("View forecast table"):
        st.dataframe(prepare_export_dataframe(forecast, business_type), use_container_width=True)

downside_adjustment = DEFAULT_DOWNSIDE_ADJUSTMENT
upside_adjustment = DEFAULT_UPSIDE_ADJUSTMENT

if paid_access:
    st.subheader("Scenario Analysis")
    st.caption("Scenario adjustments are one-time sales adjustments versus the 12-month Base Case forecast, not compounded monthly growth rates.")
    scenario_cols = st.columns(2)
    with scenario_cols[0]:
        downside_adjustment = st.number_input("Downside Case sales adjustment", value=-15.0, step=1.0, format="%.1f") / 100
    with scenario_cols[1]:
        upside_adjustment = st.number_input("Upside Case sales adjustment", value=15.0, step=1.0, format="%.1f") / 100

scenario_rows = []
scenario_details = {}
for scenario_name, adjustment in [
    ("Base Case", 0.0),
    ("Downside Case", downside_adjustment),
    ("Upside Case", upside_adjustment),
]:
    scenario_forecast, scenario_row = build_scenario_from_base(
        forecast,
        metrics["cash_balance"],
        scenario_name,
        adjustment,
        direct_costs_percentage,
    )
    scenario_details[scenario_name] = {
        "forecast": scenario_forecast,
        "metrics": summarize_metrics(scenario_forecast),
    }
    scenario_rows.append(scenario_row)

scenarios = pd.DataFrame(scenario_rows)
if paid_access:
    st.dataframe(
        scenarios.style.format(
            {
                "Sales Adjustment": "{:.1%}",
                "Sales": "${:,.0f}",
                "Operating Profit": "${:,.0f}",
                "Estimated Ending Cash Balance": "${:,.0f}",
                "Operating Margin": "{:.1%}",
            }
        ),
        use_container_width=True,
    )

    fig = px.bar(
        scenarios,
        x="Scenario",
        y=["Sales", "Operating Profit", "Estimated Ending Cash Balance"],
        barmode="group",
        title="Scenario Outcomes",
    )
    st.plotly_chart(style_chart(fig), use_container_width=True)

downside_metrics = scenario_details["Downside Case"]["metrics"]
if paid_access:
    if downside_metrics["cash_balance"] <= 0:
        st.info("The selected downside scenario indicates a potential funding shortfall.")
    elif downside_metrics["operating_profit"] < 0:
        st.info("The selected downside scenario materially weakens profitability and estimated cash resilience.")
    else:
        st.info("The business remains estimated cash-generative under the selected downside scenario.")

summary_lines = make_cfo_summary(
    history,
    metrics,
    score_label,
    downside_metrics,
)
management_priorities = make_management_priorities(history, metrics, downside_metrics)

st.subheader("Management Priorities")
for index, priority in enumerate(management_priorities, start=1):
    st.write(f"{index}. {priority}")

assumptions = {
    "Opening Cash Balance": opening_cash,
    "Business Type": business_type,
    "Forecast Sales Growth": sales_growth,
    "Direct Costs %": direct_costs_percentage,
    "Labour Cost Growth": labour_cost_growth,
    "Occupancy Cost Growth": occupancy_cost_growth,
    "Other Operating Expense Growth": other_opex_growth,
    "Downside Adjustment": downside_adjustment,
    "Upside Adjustment": upside_adjustment,
}

if paid_access:
    st.subheader("CFO Summary")
    for line in summary_lines:
        st.write(f"- {line}")

    st.subheader("Exports")
    export_cols = st.columns(2)
    with export_cols[0]:
        st.download_button(
            "Download Forecast Excel",
            data=export_excel(
                history,
                forecast,
                scenarios,
                score_breakdown,
                assumptions,
                business_type,
                scenario_details,
            ),
            file_name=f"business_financial_forecast_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with export_cols[1]:
        st.download_button(
            "Download Summary PDF",
            data=build_report_pdf(
                metrics,
                score,
                score_label,
                forecast_metrics,
                scenarios,
                summary_lines,
                business_type,
                management_priorities,
                assumptions,
            ),
            file_name=f"business_financial_health_report_{datetime.now().strftime('%Y%m%d')}.pdf",
            mime="application/pdf",
        )
else:
    render_upgrade_panel()
    render_pricing_section()
    st.caption("Your uploaded financial data is used only to generate the analysis during your current app session.")

st.markdown(
    '<div class="footer-note">This tool provides an indicative financial analysis based on information supplied by the user. It is not accounting, tax, audit, investment, or financial advice. Results should be reviewed alongside actual cash-flow records and professional advice where appropriate.</div>',
    unsafe_allow_html=True,
)
