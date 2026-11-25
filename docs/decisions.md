# SENALO design decisions

Recorded 2026-11-25 from the approved gates and current source. See [HANDOVER](../HANDOVER.md) for dated release evidence, commands, configuration inventory and unresolved verification gaps. These decisions are not permission to expand scope.

## Customer access without accounts

Use paid-order-bound email links, not customer accounts/passwords. The MVP needs a small, auditable fulfilment flow rather than account management. Upload and result purposes have separate HMAC derivation contexts and expiries. Tokens are reproducible for email retry but not stored raw. Possession of a valid email link is sensitive bearer access; do not publish it or request it in chat.

The original query-string token transport was rejected because URLs leaked into server/access logs. D2 replaced it with a fragment bootstrap, immediate address clearing, body-only token exchange, and short-lived Secure/HttpOnly session cookies. This preserves no-account access while removing raw tokens from HTTP URLs. Existing sessions have their own lifetime; token revocation is not currently automatic session revocation.

## Free processing stays session-only

The Streamlit free check analyzes uploads in session memory without a customer database or persisted financial uploads. This keeps the low-friction free experience separate from paid fulfilment and limits retained data. Paid orders require private storage and audit state; that does not authorize adding persistence to the free app. A paid presentation flag in Streamlit is not a Stripe entitlement.

## One shared financial engine

Gate 1 extracted `senalo_analysis/` so Streamlit and fulfilment use the same normalization, formulas, score, forecasts and reports. V0.5.1 financial methodology is the approved baseline. Do not independently derive historical margin or break-even buffer in new UI code: reuse canonical metrics. Regression fixtures are synthetic, committed explicitly, and benchmark expectations must not be regenerated merely to accommodate a change.

Historical operating margin is total operating profit / total sales. Break-even buffer is (latest sales - latest break-even) / latest sales. Those denominators resolve earlier inconsistent dashboard/score/CFO descriptions.

## Estimated cash is a declared proxy

Operating profit is used as simplified estimated operating cash movement because the input has monthly P&L costs, not a working-capital/capital/financing cash-flow model. Outputs say Estimated Cash Balance and include the limitation. No receivable/payable timing, capex, tax, debt flows or owner distributions are modeled. Never call it a complete cash flow statement or silently add financial assumptions.

Runway uses ending cash divided by average loss-making-month burn. It is an indicative resilience measure, not a forecast first-insolvency date. Cash-generating/depleted and 24+ labels prevent misleading decimal precision.

## Conservative base forecast and relative scenarios

Base defaults are zero monthly growth with weighted historical direct-cost ratio. Explicit base growth assumptions compound monthly from the latest month. Downside/upside are relative sales shocks to EACH base forecast month, not new compounded growth rates. Defaults remain -15%/+15%; other base operating costs remain unchanged. This makes scenario comparisons interpretable.

## Auditable scoring and business-specific labels

Financial Health Score is a rule-based 100-point sum: cash/runway 30, operating margin 25, sales trend 20, expense trend 15, break-even buffer 10. Display the exact components and reasons; no external AI or unverified industry thresholds. Management priorities use actual calculated figures, exactly three items.

Keep canonical Occupancy Cost and Prime Cost internally; adapt occupancy terminology to the business type. Prime Cost is shown only for Food & Beverage, descriptively rather than benchmarked as good/bad. Presentation changes must not alter score weights or numerical results.

## Separate public, operator and free services

Fulfilment is FastAPI, isolated from the existing Streamlit service. The public service permits incoming HTTP but validates Stripe signatures/customer sessions and denies operator actions in public role. The operator service requires Cloud Run IAM; the local header-token mechanism is not production primary authentication. One configured operator audit identity is attribution only, not proof of identity.

Expert Review can generate base analysis automatically but cannot deliver final customer artifacts until explicit human release. Never route Full Analysis's immediate release rule onto Expert Review.

## Minimal Stripe secrets and explicit product identity

Use the official Stripe SDK webhook verifier with exact raw bytes and timestamp validation. Normal Checkout webhook payloads need not include line items. Payment Link metadata identifies FULL_ANALYSIS/EXPERT_REVIEW; configured IDs sanity-check available payload IDs. Amount is validation, not identity.

The audited current flow performs no Stripe API retrieval, so an API secret key is unnecessary. Keep STRIPE_SECRET_KEY unused; only the endpoint signing secret is required. TEST and LIVE objects remain distinct. Existing live marketing links must not be regenerated when adding metadata. LIVE fulfilment activation requires separate approval.

## Private artifacts, bounded data and recoverable retries

Store raw uploads and reports in private GCS; Firestore stores order metadata, hashes, statuses and minimal audit fields. Validate uploads before persistence and reject formulas in required XLSX cells. Randomized object names avoid embedding customer names/emails or credentials.

Serve reports through fixed session-authorized backend download routes; do not expose storage URLs or accept arbitrary object paths. Record aggregate download count/timestamps, not fingerprints or geolocation. The audit proves server-side serving, not receipt/opening by a person.

Deterministic order IDs and event reservation prevent ordinary duplicate Checkout processing. Claim analysis/email work before performing it. Retry email with the same derived link and provider idempotency key; do not regenerate reports/tokens just to retry sending. Cross-service exactly-once guarantees are not claimed. The current in-process background worker and stale-claim recovery limitations remain technical debt, not authorization to implement a queue during unrelated work.

## Reports are presentation, not a second model

PDF uses local vector/text generation, Excel uses OpenPyXL; no remote rendering service or AI-generated report text. Commercial report gates added Summary worksheet, clear percentage/point formatting, score breakdown, actual forecast cash chart and stronger SENALO branding. F1C-1 specifically makes score cells integer points and uses 22pt first-page branding with 36pt separation. Financial values must remain identical for identical inputs.

Golden comparisons use numerical/content/visual assertions rather than binary hashes because generated dates and ZIP metadata may differ. The named Harbour Street Cafe sample is not present in this checkout; obtain provenance rather than inventing a golden fixture.

## Reproducible builds and approval boundaries

Commit required synthetic CSV fixtures, not customer files or incidental QA output. The dedicated fulfilment Dockerfile copies both local runtime packages and declares runtime dependencies separately from Streamlit. `fulfilment/cloudbuild.yaml` tests, builds, starts/health-checks and pushes only; deployment is separately approved.

Worker release 2026.09 was compatibility-tested and pinned instead of relying on a moving default. The pin does not lock pip dependencies or the Python base image. Review release support periodically. Use immutable build tags and deploy by digest; preserve a previous revision for traffic rollback. Never modify Stripe, secrets, IAM, DNS or unrelated services as a side effect of a build.

## Documentation and secret hygiene

The repo is public. No secret values, customer data or secure links belong in source, docs, screenshots or logs. Past interactive secret exposures must not be reused. The historical Git pattern scan found no confirmed credential files, but `.gitignore` has a documented general-secret coverage gap. This docs-only commit does not fix it: keep secret files outside the repo and seek a narrow follow-up approval.

Distinguish tested source behavior from dated production observations. Do not claim November production revalidation from September evidence. Never repair/fabricate an order, entitlement, token, upload, report or email state merely to make a QA gate pass.
