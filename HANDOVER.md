# SENALO: handover to Muse

Prepared 2026-11-25 (Australia/Brisbane). Read this file and [decisions](docs/decisions.md) before changing anything. This is a documentation-only handover, not permission to deploy, activate live payments, change financial methods, or progress Expert Review.

## 1. Source of truth and release evidence

- Repository: https://github.com/alfredying221/Cash-flow-health-check (PUBLIC, verified through GitHub metadata on the handover date).
- Source inspected and tested: `8897319efe067e66f34a56bfee0f9fef9aaf2e0c`. The handover commit adds documentation only on top of it.
- Financial methodology baseline: V0.5.1. Later gates extracted the shared engine and added fulfilment/security/report presentation without an approved change to financial methods.
- Last approved deployed application source: `97565ab62cafe9392aa2a0fdf22e15d2f568a143` (F1C-2). It includes Excel score-point formatting and PDF first-page branding fixes.
- Canonical fulfilment build: `fulfilment/cloudbuild.yaml`, explicitly `options.workerRelease: "2026.09"`, default pool. The worker pin commit was built but NOT deployed.
- Last verified Cloud Build: `9648cde5-207a-47da-86fd-f73b9763a9ae`, 2026-09-29, SUCCESS; Docker client/server 29.8.1; Gate 1 PASS; 98 tests PASS; compileall PASS; image imports/startup/health PASS; registry push PASS.
- Build-only image: `australia-southeast1-docker.pkg.dev/senalo-production/senalo/senalo-fulfilment:build-9648cde5-207a-47da-86fd-f73b9763a9ae`, digest `sha256:6c7ac0c68f7f0b502ec7ed19be0d662852963b6be8f47f2268ac23a0724ee745`.
- Last verified public production revision: `senalo-fulfilment-00008-7q4`, 100% traffic, image digest `sha256:b26db178453ec998a3f01202709131d289973cbf497f3b6059e73c2795939761` in the same image repository.

These cloud facts are the last approved September evidence, NOT a fresh November cloud inventory. No cloud configuration or secret payload was inspected or changed for this handover. Reconfirm runtime state before any approved deployment. Older `README.md` and `fulfilment/README.md` include historical gate descriptions; executable code and this dated handover take precedence (notably Stripe API-key requirements and automated post-upload processing).

## 2. Product and customer journey

SENALO Business Financial Health Check helps owner-operated businesses understand profitability, indicative cash resilience, break-even and 12-month scenarios. Business types: Food & Beverage, Market Stall / Vendor, Independent Retail, Other Owner-Operated Business. Marketing site: https://senalo.com.au, hosted separately on Cloudflare; its source/deployment is NOT this repo.

Free check: `app.py` is a Streamlit app using the shared analysis package. Uploaded data and forecast controls live in the session; the free app does not write customer files to Firestore/GCS. `PAID_ACCESS_DEFAULT=False` in `senalo_analysis/schema.py`; do not bypass paid presentation gating or treat a Streamlit flag as payment entitlement.

Paid pipeline:

1. Stripe Payment Link -> Checkout payment -> signed webhook to public fulfilment API.
2. Official Stripe SDK verifies the exact raw body, signature header and endpoint secret (300-second tolerance). Metadata identifies the product; amount/currency and available Price/Product IDs are checked.
3. Reserve `stripe_events/{event_id}`; deterministically identify one order per Checkout Session; persist payment state. Only PAID orders qualify for upload access.
4. Generate an upload-purpose token and send payment-confirmation email with a secure link. The order reaches `AWAITING_UPLOAD` after accepted email delivery.
5. `/access#<opaque-token>` clears the fragment and POSTs the token body to `/session/exchange`. A purpose-bound, server-side customer session authorizes `/upload`.
6. Customer submits business type, opening cash, CSV/XLSX. Validate before storing privately; reject unsupported types, oversized/invalid files and formula-bearing required financial cells. Save upload metadata and `VALIDATED` state.
7. `fulfilment.app.upload_submit` schedules `process_validated_upload_background` via FastAPI `BackgroundTasks`. It claims `VALIDATED -> PROCESSING`, revalidates the stored input, runs shared analysis, generates PDF/Excel and saves both private artifacts.
8. Success: `analysis_status=COMPLETED`, `result_status=READY`, `fulfilment_status=READY`. Failure: `PROCESSING_FAILED`, safe error code and best-effort partial-object cleanup. No manually fabricated READY states.
9. Full Analysis gets a separate result-purpose token and single-send-guarded result-ready email. No report attachments or public storage URLs.
10. Customer exchanges the result token for a session, opens `/result`, and downloads through `/download/pdf` and `/download/excel`. Unauthenticated requests are denied (403).

Expert Review costs A$149 and shares base analysis, but customer final results remain blocked until an operator explicitly releases final artifacts. The implementation exists and unit tests cover it; do not infer a completed production customer end-to-end acceptance gate for Expert Review from Full Analysis acceptance.

## 3. Runtime architecture

Project: `senalo-production`. Approved fulfilment region: `australia-southeast1`.

| Component | Name / entry point | Boundary |
|---|---|---|
| Public Cloud Run | `senalo-fulfilment`; `uvicorn fulfilment.app:app --host 0.0.0.0 --port 8080` | Public HTTP; webhook signature and customer session authorization; public role denies operator actions |
| Operator Cloud Run | `senalo-fulfilment-operator`; same Python entry point/image family | Cloud Run IAM-private; operator deployment role; fixed audit identity is NOT authentication |
| Existing free Cloud Run | `senalo-business-financial-health-check`; Streamlit `app.py` | Separate service, leave untouched; exact current region/image/launch flags must be inventoried before maintenance |
| Shared package | `senalo_analysis/` | No Stripe, account or cloud dependencies in financial methods |
| Build | `fulfilment/cloudbuild.yaml` | Python 3.12 regression step, Docker build, startup/health smoke, push; NO deployment step |
| Container | `fulfilment/Dockerfile` | Python 3.12-slim, non-root `appuser`, copies only fulfilment and shared analysis packages |

Public URL: https://senalo-fulfilment-su3zoeru5q-ts.a.run.app

Both fulfilment services were approved in `australia-southeast1`, using runtime identity `senalo-fulfilment-runner@senalo-production.iam.gserviceaccount.com`. Do not broaden IAM. Operator authentication in production relies on the private service boundary; never expose an operator-role service anonymously. The same image registers customer routes too; IAM restricts the operator service, rather than a separate operator-only Python application.

Firestore: Native database in `australia-southeast1`; code uses the client's default database. Private GCS bucket from the approved production plan: `senalo-fulfilment-private-prod`, same region; uniform access and public-access prevention must remain enabled. Reconfirm actual bucket configuration from the runtime reference before maintenance. Uploads and reports share this bucket, not public download buckets. Object paths are randomized under `uploads/<order_id>/`, `results/<order_id>/`, and `results/<order_id>/final/` (see `fulfilment/storage.py`).

Artifact Registry repository: `australia-southeast1-docker.pkg.dev/senalo-production/senalo`. Cloud Build source staging bucket observed: `senalo-production_cloudbuild`; it is NOT customer document storage.

The untracked root `Dockerfile`, `.fresh-checkout-*` folders and `qa_outputs/` on the original workstation are not canonical deployment sources. Do not stage them. No Streamlit root Dockerfile is tracked at this baseline.

### Firestore document shapes

Authoritative dataclasses: `fulfilment/models.py`; persistence and transaction claims: `fulfilment/firestore_store.py`; in-memory equivalent: `fulfilment/orders.py`. Timestamps are UTC datetimes; optional fields default to null; counters/versions are integers. Financial row data belongs in GCS, not Firestore.

| Collection/document | Shape and meaning |
|---|---|
| `orders/{order_id}` | `order_id` is `ord_` plus first 24 SHA-256 hex characters of Checkout Session ID; Stripe session/payment/customer/price/product IDs; `product_code`, `amount_total` (cents), `currency`, customer name/email, `customer_data_flags`; payment/fulfilment states and created/updated/paid timestamps |
| Order upload fields | `business_type`, `opening_cash`, `upload_status`, `upload_object_path`, `upload_original_filename`, `upload_content_type`, `upload_size_bytes`, `upload_created_at`, `validation_status`, `validation_error_code`, `validated_at` |
| Order analysis fields | `analysis_status`, `analysis_started_at`, `analysis_completed_at`, `analysis_error_code`, `pdf_object_path`, `pdf_size_bytes`, `excel_object_path`, `excel_size_bytes`, `result_status`, `expert_review_status` |
| Order token fields | Upload: `token_hash`, `token_seed`, `token_version`, `token_created_at`, `token_expires_at`, `token_revoked_at`. Result: same names prefixed `result_`. No raw bearer tokens; there is NO separate token collection |
| Order email fields | `email_status`, provider message ID, sent timestamp, last error, attempt count; same fields prefixed `result_` for result-ready email |
| Order audit/review fields | `delivered_at`, `last_download_at`, `download_count`; `review_commentary`, `review_actions` (rank/text dictionaries), review started/updated timestamps, `approved_at`, `released_at`, `review_operator_id`, final PDF/Excel paths and sizes. There is NO separate download-audit collection |
| `stripe_events/{stripe_event_id}` | `event_id`, `event_type`, `received_at`, `processing_status` (`PROCESSING/PROCESSED/FAILED`), `processed_at`, `order_id`, `error_code` |
| `customer_sessions/{session_hash}` | `session_hash`, `order_id`, `purpose` (`upload/result`), `created_at`, `expires_at`, `revoked_at`; raw cookie value is never stored |

Payment enum: `PENDING/PAID/FAILED/REFUNDED` (a REFUNDED enum is not a refund webhook implementation). Fulfilment enum: `NOT_STARTED/AWAITING_UPLOAD/VALIDATION_FAILED/VALIDATED/PROCESSING/PROCESSING_FAILED/READY`. Email enum: `NOT_SENT/SENDING/SENT/FAILED`. Review enum: `NOT_REQUIRED/PENDING_REVIEW/IN_REVIEW/APPROVED/RELEASED`.

## 4. Stripe: preserve TEST/LIVE separation

Last approved public backend configuration is Stripe TEST mode. Full Analysis TEST payment/upload/result flow was accepted through F1C-2. Existing LIVE marketing Payment Links exist; that does NOT mean LIVE automated fulfilment is approved or configured. Do not run real customer payments as QA.

| Mode/product | Price | Product ID | Price ID |
|---|---|---|---|
| TEST Full Analysis | A$39 | `prod_VBbYzMVvDHEsxK` | `price_1UBEKxEExrYFXPRESbGvrqGu` |
| TEST Expert Review | A$149 | `prod_VBbZHL6tqMFE5s` | `price_1UBEM3EExrYFXPREKNzLKaUg` |
| LIVE Full Analysis | A$39 | `prod_V8bthOVa6meaPh` | `price_1U8KgCEExrYFXPRE9QnswxNX` |
| LIVE Expert Review | A$149 | `prod_V8cWc3rgYuVAkY` | `price_1U8LHWEExrYFXPREP0mvi9OX` |

Existing LIVE URLs (preserve):
- Full Analysis: https://buy.stripe.com/dRm8wP3v1gA1d2P12L4ow00
- Expert Review: https://buy.stripe.com/6oU28r8Pl0B30g312L4ow01

TEST webhook ID: `we_1UBEVfEExrYFXPREvfNaZuHw`. Destination: `https://senalo-fulfilment-su3zoeru5q-ts.a.run.app/webhooks/stripe`. Handled/subscribed events:
- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`
- `checkout.session.async_payment_failed`

TEST Payment Link URLs/IDs are not recorded in tracked source or the available handover evidence. Obtain the EXISTING links from Stripe TEST Dashboard; do not create replacements. No LIVE webhook activation is established by this handover.

Required Payment Link metadata: `senalo_product_code=FULL_ANALYSIS` or `senalo_product_code=EXPERT_REVIEW`. Metadata was approved for the existing live links without replacing URLs. `orders.identify_product` requires recognized metadata; `extract_price_and_product` checks embedded line items or optional metadata ID fields, without API lookup. IDs are sanity-checked only when both payload and configured ID are available. Mapping is never solely by amount. `validate_amount` checks present amount/currency; current code permits missing values, a limitation to review separately.

`STRIPE_SECRET_KEY` is read into Settings but unused by this webhook/payment path. No Stripe line-item API call occurs. Keep it unconfigured with zero active secret versions unless a separately reviewed feature genuinely needs it. An earlier TEST API key was exposed during browser automation and must not be reused. Use only the TEST webhook signing secret for the approved flow. Never place tokens or signing secrets in chat, command arguments, screenshots or source.

## 5. Email and secure access

Resend transactional domain: `mail.senalo.com.au`; approved sender `SENALO <notifications@mail.senalo.com.au>`; Reply-To `hello@senalo.com.au`. Domain verification/DKIM/custom MAIL FROM were approved in Gate C. Existing VentraIP apex mailbox records were preserved; DMARC changes were not authorized. Do not assume current DNS/mail delivery without rechecking.

Implementation: `email_service.py`, `fulfilment_service.py`, `tokens.py`, `customer_sessions.py`, `result_delivery.py`.

- Payment-confirmation idempotency key: `payment-confirmation/<order_id>`; result-ready key: `result-ready/<order_id>`. Firestore claims and SENT state prevent normal duplicate sends. Retry reproduces the same token/link, not a new entitlement.
- HMAC-derived purpose-separated upload/result tokens; SHA-256 hashes plus random seed/version stored on order. Server derivation secret remains separate in Secret Manager.
- Default upload token expiry 14 days; result token 30 days; customer session 45 minutes. Explicit token reissue invalidates old token hashes. Already-issued sessions are separate and currently validate their own purpose/expiry/revocation, not the original token on every request.
- Cookie `senalo_customer_session`: Secure, HttpOnly, SameSite=Lax, Path=/; hashed at rest. Fragment bootstrap uses nonce CSP, clears the address, and never puts tokens in local/session storage or query strings.
- Fixed backend-proxied download routes select canonical order artifacts, never arbitrary object paths. No public GCS URLs. Result-page access sets first `delivered_at`; successful artifact load increments aggregate `download_count` and updates `last_download_at`. This records server-side serving, not proof the browser saved/opened a file. No IP/user-agent/geolocation audit is stored by this application.
- Missing/expired sessions and unauthorized downloads return generic 403. Health is only liveness, not a database/email readiness probe.

## 6. Financial methodology map (V0.5.1)

All canonical formulas live in `senalo_analysis/engine.py`; presentation helpers/labels in `schema.py`, normalization/validation in `input_processing.py`. `summarize_metrics` is the source of historical aggregate margin and latest-month break-even buffer passed to scores, summaries and reports.

| Function | Method |
|---|---|
| `calculate_financials` | Gross profit = Sales - Direct Costs; operating expenses = Labour + Occupancy + Other Operating Costs; operating profit = gross profit - operating expenses; estimated cash movement = operating profit; closing cash = opening cash + cumulative movement |
| `summarize_metrics` | Historical operating margin = total operating profit / total sales (NOT mean monthly margin or forecast margin); weighted gross margin = total gross profit / total sales |
| `calculate_financials`, `summarize_metrics` | Monthly break-even = operating expenses / gross margin when gross margin > 0; dashboard break-even uses latest month. Buffer = (latest sales - latest break-even) / latest sales, NOT divided by break-even |
| `calculate_burn_and_runway` | Burn = absolute mean of NEGATIVE movement months only; runway = ending cash / that burn. No negative months -> infinity; burning with depleted cash -> zero. Not the first forecast insolvency date |
| `schema.runway_label` | Depleted cash first -> Cash Depleted; infinity -> Cash Generating; >24 -> 24+ months; otherwise one decimal |
| `build_forecast` | 12 months from latest historical month; compound monthly base sales and each expense growth assumption; direct costs = sales x ratio; opening forecast cash = historical ending cash |
| `build_scenario_from_base` | Each month's Base sales x (1 + scenario adjustment), without compounding the adjustment; recalculate direct costs/profit/cash, preserve base labour/occupancy/other expenses |
| `sales_growth_rate`, `expense_growth_rate` | Last-minus-first divided by absolute first value; period trend, not annualized/CAGR |
| `score_cash_health`, `expense_trend_score` | Historical 100-point score below; component sum exactly equals total |
| `make_cfo_summary`, `make_management_priorities` | Rule-based actual-number commentary and exactly 3 priorities; no AI API or verified industry benchmarks |

Forecast defaults in `fulfilment.analysis_processor.default_forecast_assumptions`: zero monthly sales/labour/occupancy/other expense growth; weighted historical direct-cost ratio. Default scenario adjustments: Base 0%, Downside -15%, Upside +15%. Streamlit assumptions are initialized/reset per uploaded dataset in `app.initialize_forecast_assumptions`.

Score thresholds (first matching threshold, descending):

| Factor | Maximum | Points |
|---|---:|---|
| Cash position/runway | 30 | Depleted cash: 0; infinite or >=24 months: 30; >=12: 24; >=6: 18; >=3: 10; >0: 5; otherwise 0 |
| Operating margin | 25 | >=20%: 25; >=10%: 20; >=5%: 15; >=0%: 10; >=-5%: 5; otherwise 0 |
| Sales trend | 20 | >=10%: 20; >=3%: 16; >=0%: 12; >=-5%: 6; otherwise 0 |
| Expense trend | 15 | Sales-growth minus expense-growth >=10 percentage points OR expenses decline with nondeclining sales: 15; >=5pp: 12; >=-3pp: 8; >=-10pp: 4; otherwise 0 |
| Break-even buffer | 10 | >=25%: 10; >=10%: 8; >=0%: 5; >=-10%: 2; otherwise 0 |

Labels: 80-100 Healthy; 60-79 Stable; 40-59 Watch; 0-39 At Risk.

Required canonical columns: Month, Sales, Direct Costs, Labour Cost, Occupancy Cost, Other Operating Costs. Legacy Revenue/COGS/Payroll/Rent map to canonical columns; Marketing + Other Expenses map to Other Operating Costs. Prime Cost = Direct Costs + Labour, available internally for all types, shown only for Food & Beverage. Occupancy display labels vary by business type without changing formulas.

Important simplifications: estimated cash is an operating-profit proxy, NOT a cash flow statement. No working capital, capex, debt/financing, tax, owner distributions, seasonality, inflation model or industry calibration. `safe_divide` returns zero for zero/invalid denominator; nonpositive gross margin produces break-even zero, not an economically meaningful break-even. Nonnegative zero-cashflow months can produce the Cash Generating label if cash is positive. Preserve these existing behaviors unless separately approved.

## 7. Reports and reproducible golden outputs

`senalo_analysis/exports.py` builds both outputs. Excel uses Pandas/OpenPyXL; `export_excel`, `format_excel_workbook`, `build_summary_export`, `build_scenario_export`. Sheets: Summary, Historical Analysis, 12-Month Forecast, Scenario Analysis, Health Score, Assumptions. Score/Maximum Score cells use integer format `0`; assumptions use `0.0%` for the recognized percentage names.

PDF: `build_report_pdf` and `pdf_*` helpers manually construct a four-page PDF with standard Helvetica fonts, vector lines/rectangles/charts. No external report template, logo bitmap or rendering service is required. First-page SENALO is 22pt with 36pt brand-to-title separation. Page 1 financial summary/priorities; page 2 scenarios and genuine 12-month cash trend; page 3 score breakdown; page 4 assumptions/disclaimer. `pdf_cash_balance_chart` consumes actual forecast balances. Expert Review uses separate builders in `fulfilment/operator_review.py`; do not accidentally redesign those.

**Harbour Street Cafe provenance gap:** no named Harbour Street Cafe fixture, report template or golden file exists in the tracked repo or inspected local QA filenames. Do not invent that business's data or claim `healthy_food_business.csv` is the same sample. Obtain its approved synthetic fixture/assumptions from the owner to reproduce that exact named sample. No real customer data belongs in Git.

The reproducible approved healthy-food golden benchmark IS `healthy_food_business.csv`, Food & Beverage, opening cash 100000, conservative defaults. Expected: sales 1122000; operating profit 250800; margin 22.3529411765%; ending cash 350800; latest break-even 64230.7692307692; buffer 38.8278388278%; 100 Healthy with points 30/25/20/15/10. See exact assertions in `qa_gate1_regression.py`.

To regenerate synthetic golden files locally, run the following Python from repo root with the test environment (output directory is deliberately outside the repo). It writes no order, token, email or cloud state:

```python
from pathlib import Path
from qa_gate1_regression import load_case, scenario_rows
from fulfilment.analysis_processor import default_forecast_assumptions
from senalo_analysis import (build_report_pdf, export_excel, summarize_metrics,
                             make_cfo_summary, make_management_priorities)

history, metrics, score, label, breakdown = load_case("healthy_food_business.csv", 100000)
forecast, scenarios, details = scenario_rows(history, metrics)
downside = details["Downside Case"]["metrics"]
assumptions = {"Business Type": "Food & Beverage", **default_forecast_assumptions(metrics),
               "Downside Sales Adjustment": -0.15, "Upside Sales Adjustment": 0.15}
pdf = build_report_pdf(metrics, score, label, summarize_metrics(forecast), scenarios,
    make_cfo_summary(history, metrics, label, downside), "Food & Beverage",
    make_management_priorities(history, metrics, downside), assumptions, forecast, breakdown)
xlsx = export_excel(history, forecast, scenarios, breakdown, assumptions,
                    "Food & Beverage", details, metrics, label)
out = Path("../senalo-golden-output")
out.mkdir(exist_ok=True)
(out / "healthy-food.pdf").write_bytes(pdf)
(out / "healthy-food.xlsx").write_bytes(xlsx)
```

Compare financial values and formatting, not binary hashes: PDF report date and XLSX ZIP metadata can vary. Open/render all four PDF pages; check score sum, charts and margins. Open Excel and check integer scores and percentage assumptions. `tests/test_export_presentation.py` automates workbook formatting regression. Local historical QA binaries are untracked, not authoritative fixtures.

## 8. Configuration inventory (names only)

This table intentionally contains no environment or secret values. Sources shown are approved deployment locations/default behavior, not a fresh secret-version audit. `Settings.from_env` does not load `.env` automatically. Local values must be supplied by the process environment or explicit `uvicorn --env-file` on a protected, untracked file. Until ignore rules are fixed, keep secret files OUTSIDE the repo.

| Name | Where set / consumed | Missing or wrong behavior |
|---|---|---|
| `STRIPE_WEBHOOK_SECRET` | Secret Manager reference -> public Cloud Run; Settings/SDK verifier | Signed intake rejected; wrong mode/secret rejects authentic events |
| `STRIPE_SECRET_KEY` | Settings only; intentionally NOT configured on Cloud Run; existing Secret Manager secret has no approved active version | No impact on current webhook flow; do not add an API key |
| `RESEND_API_KEY` | Secret Manager -> runtime email provider | Email cannot send; orders/results cannot complete customer notification |
| `SENALO_TOKEN_DERIVATION_SECRET` | Secret Manager -> runtime token derivation | Secure tokens cannot be issued/reproduced; arbitrary rotation breaks existing links |
| `GOOGLE_CLOUD_PROJECT` | Cloud Run process environment/ADC | Wrong/missing project can fail persistence or target wrong project |
| `SENALO_FULL_ANALYSIS_PRICE_ID`, `SENALO_EXPERT_REVIEW_PRICE_ID` | Public Cloud Run, Settings | Missing values remove available-ID sanity checks |
| `SENALO_FULL_ANALYSIS_PRODUCT_ID`, `SENALO_EXPERT_REVIEW_PRODUCT_ID` | Public Cloud Run, Settings | Same; never mix TEST and LIVE identifiers |
| `SENALO_EMAIL_FROM` | Cloud Run, email provider | Provider unconfigured if absent |
| `SENALO_EMAIL_REPLY_TO` | Cloud Run or Settings default | Replies may go to unintended/default mailbox if incorrect |
| `SENALO_PUBLIC_FULFILMENT_BASE_URL` | Cloud Run, email link builders | Absent uses localhost default; customer links unusable |
| `SENALO_TOKEN_EXPIRY_DAYS`, `SENALO_RESULT_TOKEN_EXPIRY_DAYS` | Cloud Run or Settings defaults | Defaults apply; malformed integers prevent settings construction |
| `SENALO_CUSTOMER_SESSION_MINUTES` | Cloud Run or Settings default | Default applies; invalid value can break or shorten access |
| `SENALO_UPLOAD_BUCKET` | Cloud Run, GCS adapter | Absent silently selects in-memory upload storage; unsafe in production |
| `SENALO_MAX_UPLOAD_BYTES` | Cloud Run or Settings default | Default limit applies; bad integer breaks configuration |
| `SENALO_DEPLOYMENT_ROLE` | Each Cloud Run service; operator authentication | Wrong role can disable operator functions or select inappropriate auth mode |
| `SENALO_OPERATOR_AUDIT_ID` | Operator Cloud Run | Default attribution if absent; not a credential |
| `SENALO_OPERATOR_AUTH_TOKEN` | Local/test environment only | Local operator access denied if absent; NOT production primary auth |
| `GOOGLE_APPLICATION_CREDENTIALS` | Optional local Google SDK credential reference, not application setting | Prefer ADC/emulator; never commit service-account JSON |
| `FIRESTORE_EMULATOR_HOST` | Local Google SDK/emulator environment only | Without it, Firestore clients may use ADC against real infrastructure; tests should use in-memory adapters |
| `PORT` | Cloud Run platform | Container currently binds fixed 8080; changing platform port alone breaks startup |

Free app has no secret-backed paid account system. `fulfilment/env.example` is placeholders only, not safe production configuration. Do not copy placeholder API keys into Secret Manager. Cloud Build uses its own identity; runtime uses the dedicated service account. Never dump full runtime environments, ADC files or secret versions into the docs.

## 9. Local operation and tests

From repo root, Python 3.12 is the production container version. Example PowerShell setup:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r fulfilment/requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Backend startup: `python -m uvicorn fulfilment.app:app --host 127.0.0.1 --port 8080`. `/health` needs no cloud credentials; other API routes construct Firestore stores. Do not point local experimentation at production ADC. Full browser session testing requires HTTPS because cookies are Secure; do not weaken production cookie settings to make local HTTP testing work.

Exact full-suite command (includes Gate 2-7, D2 and export presentation tests):

```powershell
python -m unittest discover -s tests -t .
python qa_gate1_regression.py
python -m compileall -q app.py fulfilment senalo_analysis tests
python -c "import pandas, openpyxl, multipart, senalo_analysis, fulfilment.app, fulfilment.operator_review"
```

**Fresh-checkout validation on 2026-11-25:** clean local Git clone of source SHA above, using the existing approved Windows Python 3.13.15 environment (not a fresh dependency installation): 98 tests PASS in 10.099 seconds; Gate 1 PASS for all six fixtures; compileall PASS; imports PASS; actual local Uvicorn startup and `/health` HTTP 200 PASS. No cloud writes/emails/payments. Initial sandbox test/startup attempts stalled/timed out; reruns outside that sandbox passed. Clean source did not rely on untracked files.

| Fixture | Opening cash | Score/label |
|---|---:|---|
| healthy_food_business.csv | 100000 | 100 Healthy |
| watch_food_business.csv | 60000 | 42 Watch |
| at_risk_food_business.csv | 50000 | 0 At Risk |
| healthy_market_vendor.csv | 100000 | 100 Healthy |
| watch_market_vendor.csv | 60000 | 45 Watch |
| sample_data.csv | 50000 | 90 Healthy |

Legacy margin 9.8%; buffer 25.4%; every Downside/Base ratio 0.85 and Upside/Base 1.15; score sums match. Expected failure-path log labels such as `order_email_failed` are emitted by passing negative tests, not evidence of real email attempts.

**Container build qualification:** Docker/gcloud are not installed in the current local execution context, so a new container build was NOT run for this docs-only handover. The exact same application/build source passed the clean Cloud Build/startup/push record in section 1. This is historical build evidence, not a claimed November rebuild. The broad dependency constraints and floating Python base tag mean a future rebuild must be revalidated.

## 10. Build, deploy and rollback runbook

Commands below are instructions for a FUTURE human-approved operation, not actions executed by this handover. Do not deploy docs just to publish a handover. Do not rebuild the marketing site or update the Streamlit/operator service alongside fulfilment.

Fresh-source validation (PowerShell, replace the approved commit placeholder):

```powershell
git clone https://github.com/alfredying221/Cash-flow-health-check.git senalo-release
Set-Location senalo-release
git checkout --detach <APPROVED_COMMIT_SHA>
git rev-parse HEAD
git status --porcelain
python -m pip install -r fulfilment/requirements.txt
python qa_gate1_regression.py
python -m unittest discover -s tests -t .
python -m compileall -q app.py fulfilment senalo_analysis tests
```

Stop on any nonzero exit or unexpected working-tree content. Run in a clean Python environment. No local secret files may be present in the build source: Cloud Build upload filtering and Docker context filtering are different controls.

Non-deploying canonical build (Cloud Shell or authenticated gcloud workstation):

```powershell
gcloud builds submit --project=senalo-production --config=fulfilment/cloudbuild.yaml .
gcloud builds describe <BUILD_ID> --project=senalo-production --format="yaml(id,status,options.workerRelease,results.images)"
gcloud builds log <BUILD_ID> --project=senalo-production
```

Require SUCCESS, 98 tests, Gate 1, compileall, startup marker and health 200, explicit pinned worker, image digest. Unique image tag is `build-<BUILD_ID>`; never overwrite/reuse a failed tag. The YAML does not deploy.

Deploy ONLY public fulfilment, by digest, after separate deployment approval. First inspect current revision/traffic, preserve it for rollback, and confirm runtime settings/secret references through approved names-only inspection:

```powershell
gcloud run services describe senalo-fulfilment --project=senalo-production --region=australia-southeast1 --format="yaml(status.latestReadyRevisionName,status.traffic,status.url)"
gcloud run services update senalo-fulfilment --project=senalo-production --region=australia-southeast1 --image=australia-southeast1-docker.pkg.dev/senalo-production/senalo/senalo-fulfilment@<VERIFIED_SHA256_DIGEST> --no-traffic
gcloud run services describe senalo-fulfilment --project=senalo-production --region=australia-southeast1 --format="yaml(status.latestCreatedRevisionName,status.latestReadyRevisionName,status.traffic)"
```

After checking the new revision is Ready and approved to receive traffic:

```powershell
gcloud run services update-traffic senalo-fulfilment --project=senalo-production --region=australia-southeast1 --to-revisions=<NEW_READY_REVISION>=100
curl.exe -i https://senalo-fulfilment-su3zoeru5q-ts.a.run.app/health
curl.exe -i -X POST https://senalo-fulfilment-su3zoeru5q-ts.a.run.app/webhooks/stripe -H "Content-Type: application/json" --data "{}"
```

Expect 200 health, 400 unsigned webhook; unauthenticated result/PDF/Excel routes 403. Preserve environment, secret references, IAM, service identity, CPU allocation/background-execution settings, ingress and scaling. Updating only image/traffic must not silently reset those. A fresh TEST purchase/upload and customer output inspection require explicit approval and customer action; never fabricate orders, tokens or reports to make validation pass.

Rollback to the verified pre-deploy revision (not automatically the historical September revision):

```powershell
gcloud run services update-traffic senalo-fulfilment --project=senalo-production --region=australia-southeast1 --to-revisions=<PREVIOUS_VERIFIED_REVISION>=100
gcloud run services describe senalo-fulfilment --project=senalo-production --region=australia-southeast1 --format="yaml(status.traffic,status.url)"
```

Recheck health/security. Traffic rollback does not revert Firestore/GCS changes or emails already sent; no destructive state reset. Keep failed revisions for evidence. Git pushes previously hit Windows sandbox/Git Bash signal-pipe errors: use normal Windows PowerShell `git push origin main` after approval; do not invent PATs or change global authentication.

References for the future runbook: [Cloud Build worker channels](https://docs.cloud.google.com/build/docs/release-channels), [build schema](https://docs.cloud.google.com/build/docs/build-config-file-schema), [Cloud Run image updates](https://docs.cloud.google.com/sdk/gcloud/reference/run/services/update), [traffic rollback](https://docs.cloud.google.com/sdk/gcloud/reference/run/services/update-traffic). Review the pinned release periodically for retirement; do not silently replace it with a moving channel.

## 11. Security audit, limitations and next work

**Ignore-rule verification: FAIL for general secret files.** Current `.gitignore` covers `.streamlit/secrets.toml` and virtualenvs, but does NOT cover `.env`, `fulfilment/.env`, `.env.production`, `service-account.json`, `credentials.json`, or `private-key.pem`. `.dockerignore` covers many such patterns, but is not Git protection. This task permits committing docs only, so ignore rules were NOT changed. Request a narrow follow-up to add `.env`/`.env.*`, explicit credential filename patterns and private-key extensions while retaining the harmless `fulfilment/env.example`. Until then keep secrets outside the tree and stage explicit filenames only.

History scan: all 13 locally reachable commits, 83 distinct blob/path pairs examined for Stripe/Resend/GitHub/AWS key patterns, private-key headers, service-account JSON markers and sensitive filenames. Confirmed sensitive files ever committed: **none identified**. Candidate filename list: **none**. This is a bounded heuristic scan, not proof that every possible credential format or unreachable Git object is clean. Test fixtures and `fulfilment/env.example` contain synthetic placeholder credentials only. Prior browser/terminal key exposures are separate from Git history; never retrieve or reuse exposed credentials.

Known limitations and follow-up gates (no fixes included here):
- Fix ignore rules before storing any secret file locally; public repository visibility magnifies accidental staging risk.
- Obtain the exact Harbour Street Cafe synthetic fixture/golden reference and missing TEST Payment Link identifiers. Do not rename a different fixture and call it equivalent.
- Reinventory current Cloud Run state, Streamlit region, secret-reference names/versions and bucket policy before operational work; this handover includes dated last-known cloud evidence only.
- Background analysis is in-process FastAPI background work, not a durable task queue. Instance termination may strand PROCESSING claims; no automatic stale-claim lease recovery is implemented. Preserve approved CPU/lifecycle settings and require a reviewed retry/recovery procedure.
- Event reservation/analysis/email claims are transactional, but whole fulfilment is not one cross-service transaction. Event reservation has no stale PROCESSING lease recovery. Order upsert and aggregate download-count save are read/modify/write; concurrent downloads may lose increments. Do not claim globally exact-once processing or audit precision.
- Provider idempotency has finite retention; an ambiguous send timeout/retry beyond retention can duplicate an email. SENT guards cover normal retries, not every crash boundary.
- Active sessions can outlive token reissue/revocation until session expiry/revocation. There is no implemented session-exchange rate limiter or automated session/object retention cleanup in source; review separately.
- Refund/chargeback lifecycle handling is not implemented by the three Checkout events. Metadata mapping/optional ID checks do not constitute a universal Stripe product catalog validator.
- Dependencies/base images are not fully locked. Worker pin stabilizes the worker, not all third-party packages. A new clean build must pass before every deployment.
- Financial zero-denominator/nonpositive-margin behavior, loss-month-only burn, flat defaults and operating-profit cash proxy are intentional current limitations, not accounting cash forecasting.
- Single-operator audit identity is configuration-based attribution; it needs redesign before multiple operators. Expert Review production acceptance remains a separate gate.
- No new production integration QA, live payments, DNS/IAM changes or deployment was performed during this handover. Local passing tests do not reverify external provider availability.

Muse's first safe action: read this file and `docs/decisions.md`, inspect `git status`, run the exact local tests, and agree the next narrow human approval gate. Never bypass payment, token, upload, analysis or release states to manufacture a pass.
