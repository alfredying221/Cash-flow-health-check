# SENALO Fulfilment API Gate 7

This backend is intentionally separate from the Streamlit customer-facing app.
Gate 2 implemented order persistence and Stripe webhook processing. Gate 3 adds
secure customer access-token generation and transactional next-step email.
Gate 4 adds the secure paid-customer financial-data intake workflow. Gate 5
adds automated analysis and private result generation for validated paid orders.
Gate 6 adds secure result delivery for `FULL_ANALYSIS` orders that are already
`READY`. Gate 7 adds the minimal human approval workflow required before
`EXPERT_REVIEW` final files can be released to customers.

It does not implement customer accounts, AI-generated expert commentary, live
webhook activation, production deployment, or a complex admin system.

## Endpoints

- `GET /health`
- `POST /webhooks/stripe`
- `GET /access#<opaque_token>`
- `POST /session/exchange`
- `GET /upload`
- `POST /upload`
- `GET /result`
- `GET /download/pdf`
- `GET /download/excel`
- `GET /operator/reviews`
- `GET /operator/reviews/{order_id}`
- `POST /operator/reviews/{order_id}/draft`
- `POST /operator/reviews/{order_id}/release`
- `GET /operator/reviews/{order_id}/download/{artifact_type}`

The `/access` route serves a minimal first-party fragment bootstrap page. The
browser reads the URL fragment, clears it from the address bar, exchanges the
token through `POST /session/exchange`, and then uses a secure session cookie to
load `/upload` or `/result`. The `/upload` route presents a single financial-data
intake form. The form accepts business type, opening cash, and one CSV or XLSX
file using the same canonical SENALO validation pipeline as the Streamlit app.

## Required Configuration

Use environment variables or Secret Manager-backed Cloud Run environment
configuration. Do not commit real secrets.

- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_SECRET_KEY`
- `SENALO_FULL_ANALYSIS_PRICE_ID`
- `SENALO_EXPERT_REVIEW_PRICE_ID`
- `SENALO_FULL_ANALYSIS_PRODUCT_ID`
- `SENALO_EXPERT_REVIEW_PRODUCT_ID`
- `GOOGLE_CLOUD_PROJECT`
- `RESEND_API_KEY`
- `SENALO_EMAIL_FROM`
- `SENALO_EMAIL_REPLY_TO`
- `SENALO_PUBLIC_FULFILMENT_BASE_URL`
- `SENALO_TOKEN_EXPIRY_DAYS`
- `SENALO_RESULT_TOKEN_EXPIRY_DAYS`
- `SENALO_TOKEN_DERIVATION_SECRET`
- `SENALO_UPLOAD_BUCKET`
- `SENALO_MAX_UPLOAD_BYTES`
- `SENALO_OPERATOR_AUTH_TOKEN`
- `SENALO_DEPLOYMENT_ROLE`
- `SENALO_OPERATOR_AUDIT_ID`
- `SENALO_CUSTOMER_SESSION_MINUTES`

Recommended production sender after DNS is configured:

- `SENALO <notifications@mail.senalo.com.au>`
- Reply-To: `hello@senalo.com.au`

Do not commit real API keys.

`SENALO_OPERATOR_AUTH_TOKEN` is a minimal local/test operator-auth abstraction
used by Gate 7 operator routes through the `X-SENALO-OPERATOR-TOKEN` header.
Production uses `SENALO_DEPLOYMENT_ROLE`: `public` disables operator routes in
the public service, and `operator` relies on Cloud Run IAM as the outer access
boundary while using `SENALO_OPERATOR_AUDIT_ID` only for audit attribution.

The live/test Stripe Price IDs and Product IDs must be supplied by the operator
before any live webhook is enabled. Payment Link URLs are not used for product
mapping.

## Stripe Product Metadata

Gate 2.1 requires explicit SENALO product metadata on the Stripe Payment Link,
because a normal `checkout.session.completed` webhook should not be assumed to
include line item Price/Product details directly.

Configure the Payment Link metadata key:

- `senalo_product_code`

Allowed values:

- `FULL_ANALYSIS`
- `EXPERT_REVIEW`

Stripe copies Payment Link metadata to the generated Checkout Session. The
webhook uses that metadata as the product identity and treats configured
Price/Product IDs as validation when they are available in the event payload or
future line-item lookup.

If metadata is missing or unknown, the event fails safely and no fulfilment-ready
order is created.

## Firestore Collections

`orders/{order_id}`

- `order_id`
- `stripe_checkout_session_id`
- `stripe_payment_intent_id`
- `stripe_customer_id`
- `stripe_price_id`
- `stripe_product_id`
- `product_code`
- `amount_total`
- `currency`
- `customer_name`
- `customer_email`
- `payment_status`
- `fulfilment_status`
- `created_at`
- `updated_at`
- `paid_at`
- `customer_data_flags`
- `token_hash`
- `token_created_at`
- `token_expires_at`
- `token_revoked_at`
- `email_status`
- `email_provider_message_id`
- `email_sent_at`
- `email_last_error`
- `email_attempt_count`
- `business_type`
- `opening_cash`
- `upload_status`
- `upload_object_path`
- `upload_original_filename`
- `upload_content_type`
- `upload_size_bytes`
- `upload_created_at`
- `validation_status`
- `validation_error_code`
- `validated_at`
- `analysis_status`
- `analysis_started_at`
- `analysis_completed_at`
- `analysis_error_code`
- `pdf_object_path`
- `pdf_size_bytes`
- `excel_object_path`
- `excel_size_bytes`
- `result_status`
- `expert_review_status`
- `result_token_hash`
- `result_token_seed`
- `result_token_version`
- `result_token_created_at`
- `result_token_expires_at`
- `result_token_revoked_at`
- `result_email_status`
- `result_email_provider_message_id`
- `result_email_sent_at`
- `result_email_last_error`
- `result_email_attempt_count`
- `delivered_at`
- `last_download_at`
- `download_count`
- `review_commentary`
- `review_actions`
- `review_started_at`
- `review_updated_at`
- `approved_at`
- `released_at`
- `review_operator_id`
- `final_pdf_object_path`
- `final_pdf_size_bytes`
- `final_excel_object_path`
- `final_excel_size_bytes`

`stripe_events/{stripe_event_id}`

- `event_id`
- `event_type`
- `received_at`
- `processed_at`
- `processing_status`
- `order_id`
- `error_code`

## Local Test Command

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_fulfilment_gate2 -v
```

Gate 2 and Gate 3 tests use an in-memory store and signed fixture payloads.
They do not connect to live Stripe, live Resend, or live Firestore.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

## Gate 3 Token and Email Notes

- Token seeds use `secrets.token_urlsafe(32)`.
- Customer-facing tokens are derived with HMAC-SHA256 using
  `SENALO_TOKEN_DERIVATION_SECRET`, the token seed, and token version.
- Raw customer-facing tokens are never persisted; only token seeds, token
  versions, and SHA-256 token hashes are stored.
- A database compromise alone does not reveal the customer access credential
  because the stored seed is not directly usable without the separate server-side
  token derivation secret.
- Tokens expire after `SENALO_TOKEN_EXPIRY_DAYS`, defaulting to 14 days.
- Reissuing a token is an explicit operation that creates a new seed/version and
  replaces the stored hash, making the old token invalid.
- Token validation rejects malformed, unknown, expired, and revoked tokens.
- Customer email links use `/access#<opaque_token>` and do not include order IDs.
  URL fragments are not sent in HTTP requests, so the raw customer token is not
  present in request URLs or access logs.
- `/session/exchange` validates the token and creates a short-lived customer
  session. The browser receives only an `HttpOnly`, `Secure`, `SameSite=Lax`
  cookie containing a raw session ID; the server stores only its hash.
- Customer session expiry defaults to 45 minutes via
  `SENALO_CUSTOMER_SESSION_MINUTES`.
- `/access`, `/session/exchange`, and customer pages set
  `Referrer-Policy: no-referrer` and `Cache-Control: no-store`.

## Gate 4 Upload Intake Notes

- Upload is allowed only after a verified paid order reaches
  `AWAITING_UPLOAD`. Re-upload is allowed for `VALIDATION_FAILED` and
  `VALIDATED` orders so a customer can recover from a rejected file or replace a
  prior valid file.
- Accepted file extensions are `.csv` and `.xlsx`. Macro-enabled `.xlsm`, legacy
  `.xls`, executable-looking content, empty files, binary-looking CSV content,
  and invalid XLSX containers are rejected before persistence.
- The default maximum upload size is 5 MB, controlled by
  `SENALO_MAX_UPLOAD_BYTES`.
- Uploaded files are validated before storage using `senalo_analysis`; both the
  current SENALO schema and the legacy MVP schema are supported by the shared
  input normalisation code.
- Spreadsheet files are parsed as data only through the existing
  pandas/openpyxl path. Formulas and macros are never executed. For this v1
  workflow, required financial cells in XLSX uploads must contain literal
  values; formula-bearing required financial cells are rejected before storage
  with a customer-facing instruction to replace formulas with values.
- Validation failures update the order to `VALIDATION_FAILED` with a recoverable
  error code. Financial row values are not stored in Firestore.
- Successful uploads store only private object metadata on the order:
  business type, opening cash, sanitized original filename, object path, content
  type, file size, upload timestamps, and validation status.
- A later valid re-upload supersedes the previous object path and attempts to
  delete the older private object after the order is saved.
- If object storage succeeds but the order update fails, the just-created object
  is deleted so no orphaned customer file remains from that failed submission.
- Production must configure `SENALO_UPLOAD_BUCKET` to a private Cloud Storage
  bucket with uniform bucket-level access and no public object access. The local
  in-memory upload store is for automated tests and local development only.

## Gate 5 Analysis Processing Notes

- Gate 5 processing is service-level only; there is no customer download page,
  result email, signed result URL, operator review UI, or delivery workflow.
- Processing is started by calling the internal analysis processor with an order
  ID, order store, and private storage adapter.
- Eligible orders must be `PAID`, `VALIDATED`, have a valid `business_type`,
  valid `opening_cash`, `upload_status=UPLOADED`,
  `validation_status=VALIDATED`, and a private upload object path.
- The processor claims an order before work starts:
  `VALIDATED -> PROCESSING`. Duplicate workers and already `READY` orders are
  not processed again. Explicit retry is allowed only from `PROCESSING_FAILED`.
- Source files are loaded from private storage, rechecked for file type/formula
  policy, parsed with the shared `senalo_analysis` parser, and revalidated with
  canonical SENALO validation before analysis.
- Forecast assumptions are conservative defaults: monthly sales growth `0%`,
  direct cost percentage from the weighted historical ratio, labour growth `0%`,
  occupancy growth `0%`, and other operating cost growth `0%`.
- The processor reuses the canonical SENALO engine for historical calculations,
  score, forecast, scenarios, CFO summary, management priorities, PDF export,
  and Excel export.
- Results are stored privately as randomized objects under
  `results/<internal-order-id>/<random-id>.pdf` and
  `results/<internal-order-id>/<random-id>.xlsx`. Object names do not contain
  customer name, customer email, raw token, Stripe ID, or source filename.
- `READY` means analysis succeeded, PDF generated, Excel generated, both result
  objects stored privately, and order result metadata saved. Partial results are
  not released.
- If generation or storage fails, the order moves to `PROCESSING_FAILED` with a
  safe `analysis_error_code`, and any newly stored partial result objects are
  deleted where possible.
- `EXPERT_REVIEW` receives the same base analysis artifacts and is marked
  `expert_review_status=PENDING_REVIEW`; manual review is intentionally outside
  Gate 5.

## Gate 6 Result Delivery Notes

- Result delivery is allowed only for `FULL_ANALYSIS` orders with
  `payment_status=PAID`, `fulfilment_status=READY`, `result_status=READY`, and
  canonical private PDF/Excel object paths.
- `EXPERT_REVIEW` remains blocked from result release even when base analysis
  artifacts exist. Its result page shows only a safe preparation message and its
  downloads are denied.
- Result access uses a purpose-scoped deterministic token derived from the same
  server-side secret but with a separate HMAC context:
  `senalo-result-token`. Upload tokens use `senalo-upload-token`, so upload and
  result links are not interchangeable.
- Result tokens are not stored raw. The order stores only result token seed,
  version, hash, issue/expiry timestamps, and optional revocation timestamp.
- Result-token expiry defaults to 30 days via
  `SENALO_RESULT_TOKEN_EXPIRY_DAYS`.
- Explicit result-token reissue creates a new seed/version/hash, invalidates the
  previous result token, and preserves the existing PDF/Excel artifacts.
- Result-ready email is sent only for `FULL_ANALYSIS`, uses idempotency key
  `result-ready/<order_id>`, does not attach files, and does not include
  financial values, storage URLs, order IDs, or Stripe IDs.
- Email retry preserves the same result token and secure result URL. It does not
  regenerate analysis artifacts or reissue tokens.
- Downloads are backend-proxied through fixed cookie-authorized routes:
  `/download/pdf` and `/download/excel`. No arbitrary object path parameter is
  accepted and private storage URLs are never exposed.
- PDF downloads use fixed filename `SENALO-Full-Analysis.pdf`; Excel downloads
  use fixed filename `SENALO-Full-Analysis.xlsx`.
- Minimal delivery audit fields track `delivered_at`, `last_download_at`, and
  aggregate `download_count`; IP address, user agent, and geolocation are not
  stored by the application.

## Gate 7 Expert Review Approval Notes

- `EXPERT_REVIEW` orders receive the same base analysis artifacts generated by
  Gate 5, then remain blocked with `expert_review_status=PENDING_REVIEW` until
  an operator completes the manual review.
- The operator queue lists only paid, ready `EXPERT_REVIEW` orders. It does not
  list `FULL_ANALYSIS` orders.
- In local/test mode, operator routes require `X-SENALO-OPERATOR-TOKEN`
  matching `SENALO_OPERATOR_AUTH_TOKEN`. `X-SENALO-OPERATOR-ID` may be supplied
  for local audit attribution. In production operator mode, Cloud Run IAM is the
  access boundary and `SENALO_OPERATOR_AUDIT_ID` supplies the fixed audit
  identity when no application-level principal is available.
- Operators can download the source upload and base PDF/XLSX, save draft
  commentary, and enter 3-5 prioritised management actions.
- Saving a draft changes `PENDING_REVIEW` to `IN_REVIEW` but does not release
  customer access and does not send a result-ready email.
- Release requires an explicit confirmation checkbox plus non-empty commentary
  and 3-5 actions. The workflow can generate final Expert Review PDF/XLSX files
  or accept optional replacement final PDF/XLSX files.
- Final Expert Review artifacts are stored privately under
  `results/<internal-order-id>/final/<random-id>.<ext>`. Object names do not
  contain customer name, customer email, raw token, Stripe ID, or source
  filename.
- Customer result access for `EXPERT_REVIEW` is allowed only after
  `expert_review_status=RELEASED` and final PDF/XLSX paths exist. Before release,
  the result page shows only a safe preparation message and downloads remain
  denied.
- The Expert Review result-ready email is sent only after human release, uses
  the same `result-ready/<order_id>` idempotency key pattern, and does not attach
  files or include financial values, storage URLs, order IDs, or Stripe IDs.
- Duplicate release attempts do not regenerate final artifacts or send a second
  email.
- If final artifact creation or storage fails, the order returns to
  `IN_REVIEW`; customer access remains blocked.

Emails are sent only after a paid order is verified and persisted. The store
claims an order for email sending before provider delivery. Provider acceptance
marks the email as `SENT` and the order as `AWAITING_UPLOAD`; failures remain
recoverable with `FAILED` email status.

If a provider timeout occurs after possible acceptance, exact-once delivery
cannot be proven indefinitely without provider-side idempotency guarantees. The
current strategy reuses the same token seed, secure URL, email payload, and
deterministic idempotency key for payment-confirmation retries:
`payment-confirmation/<order_id>`. Resend idempotency retention is provider
limited, so retries outside the provider retention window may still create a new
provider-side email delivery; the link remains valid because the payload is
reproducible and the token is not reissued for email retry.

## Remaining Production Prerequisite

True Firestore integration/emulator testing remains required before live webhook
activation. Do not create live Firestore solely to satisfy local Gate 3 testing.

## Local Backend Run Command

Install backend dependencies separately before running:

```powershell
pip install -r fulfilment/requirements.txt
uvicorn fulfilment.app:app --host 127.0.0.1 --port 8080
```
