# Cash Flow Health Check

Cash Flow Health Check is a Streamlit MVP for small business owners who want a clearer view of profitability, estimated cash resilience, and downside risk.

## Main Outputs

- Upload CSV or Excel financial data.
- Download a professional Excel input template.
- Validate required monthly columns.
- Calculate margins, operating profit, break-even revenue, cash burn, runway, and estimated cash balance.
- Show KPI cards and Plotly charts.
- Generate a 12-month forecast from editable assumptions.
- Compare Base Case, Downside Case, and Upside Case scenarios using revenue adjustments versus the Base Case forecast.
- Score cash-flow health from 0 to 100 with an auditable score breakdown.
- Export a formatted forecast Excel workbook and a commercial PDF report.

## Required Input Columns

- Month
- Revenue
- COGS
- Payroll
- Rent
- Marketing
- Other Expenses

## Local Installation

```bash
pip install -r requirements.txt
```

## Local Launch

```bash
streamlit run app.py
```

## Streamlit Deployment

Use `app.py` as the Streamlit entry point.

## Methodology Notes

Estimated cash movement currently uses operating profit as a simplified proxy for operating cash flow. It does not yet incorporate working capital, capital expenditure, financing, tax, or owner distributions.

Scenario analysis uses the 12-month Base Case forecast as the central forecast. Downside and Upside cases apply a non-compounded monthly revenue adjustment to each Base Case forecast month, then recalculate COGS, operating profit, and estimated ending cash balance.

Base Case forecast defaults assume current operating economics broadly continue: monthly revenue growth defaults to 0.0%, COGS percentage defaults to historical weighted COGS percentage, payroll growth defaults to 0.0%, and other operating expense growth defaults to 0.0%.

Cash runway displays as `Cash Generating` when forecast or historical estimated cash movement is consistently positive, `24+ months` when runway exceeds 24 months, or a one-decimal month value otherwise.

The Cash Flow Health Score uses these component weights:

- Cash position / runway: 30 points
- Operating margin: 25 points
- Revenue trend: 20 points
- Expense trend: 15 points
- Break-even buffer: 10 points

Expense Trend scoring compares historical revenue growth with historical operating expense growth. The operating leverage gap is revenue growth minus operating expense growth:

- 15 points: leverage gap is at least 10 percentage points, or expenses decline while revenue is stable or increasing.
- 12 points: leverage gap is at least 5 percentage points.
- 8 points: leverage gap is between -3 and 5 percentage points.
- 4 points: leverage gap is between -10 and -3 percentage points.
- 0 points: leverage gap is worse than -10 percentage points.

This tool provides an indicative financial analysis based on information supplied by the user. It is not accounting, tax, audit, investment, or financial advice.
