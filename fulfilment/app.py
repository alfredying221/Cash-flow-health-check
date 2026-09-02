from __future__ import annotations

import logging
import secrets
import time
from html import escape

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from .config import Settings
from .customer_sessions import (
    CUSTOMER_SESSION_COOKIE,
    CustomerSessionError,
    exchange_customer_token,
    validate_customer_session,
)
from .email_service import EmailDeliveryError, ResendEmailProvider, UnconfiguredEmailProvider
from .firestore_store import FirestoreOrderStore
from .fulfilment_service import OrderFulfilmentService
from .health import health
from .orders import FulfilmentError, process_stripe_event
from .operator_review import (
    OperatorAuthError,
    ReviewError,
    authenticate_operator,
    get_review_order,
    list_expert_review_orders,
    release_expert_review,
    safe_review_text,
    save_review_draft,
)
from .result_delivery import (
    ResultNotReleasableError,
    ResultTokenExpiredError,
    is_expert_review_blocked,
    is_result_releasable,
    load_authorized_artifact,
    load_authorized_artifact_for_order,
    mark_result_accessed,
    result_product_label,
)
from .stripe_webhook import StripeSignatureError, construct_event
from .storage import GoogleCloudUploadStorage, InMemoryUploadStorage, UploadStorageError
from .tokens import TokenConfigurationError, TokenValidationError
from .upload_intake import (
    IntakeError,
    InvalidBusinessTypeError,
    InvalidOpeningCashError,
    UploadNotAllowedError,
    ValidationFailedError,
    ensure_upload_allowed,
    submit_upload,
)


logger = logging.getLogger("senalo.fulfilment")
app = FastAPI(title="SENALO Fulfilment API")
LOCAL_UPLOAD_STORAGE = InMemoryUploadStorage()
UPLOAD_SECURITY_HEADERS = {
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
}


@app.middleware("http")
async def secure_token_page_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in {"/upload", "/result"} or request.url.path.startswith(("/download/", "/operator/")):
        response.headers.update(UPLOAD_SECURITY_HEADERS)
    return response


@app.get("/health")
def health_endpoint() -> dict[str, str]:
    return health()


def get_upload_storage(settings: Settings):
    if settings.upload_bucket:
        return GoogleCloudUploadStorage(settings.upload_bucket, project=settings.google_cloud_project)
    return LOCAL_UPLOAD_STORAGE


def secure_html_response(body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(body, status_code=status_code, headers=UPLOAD_SECURITY_HEADERS.copy())


def generic_denial_response() -> HTMLResponse:
    return secure_html_response(
        render_page(
            "<p>This secure SENALO link is invalid or has expired. Please contact hello@senalo.com.au for assistance.</p>"
        ),
        status_code=403,
    )


def generic_result_denial_response(message: str | None = None, status_code: int = 403) -> HTMLResponse:
    return secure_html_response(
        render_page(
            f"<p>{escape(message or 'This secure SENALO result link is invalid or has expired. Please contact hello@senalo.com.au for assistance.')}</p>"
        ),
        status_code=status_code,
    )


def generic_operator_denial_response() -> HTMLResponse:
    return secure_html_response(render_page("<p>Operator access is not available.</p>"), status_code=403)


@app.get("/access")
def access_bootstrap() -> Response:
    nonce = secrets.token_urlsafe(16)
    body = render_access_page(nonce)
    headers = UPLOAD_SECURITY_HEADERS.copy()
    headers["Content-Security-Policy"] = (
        f"default-src 'none'; script-src 'nonce-{nonce}'; connect-src 'self'; "
        "style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    return HTMLResponse(body, headers=headers)


@app.post("/session/exchange")
async def session_exchange(request: Request) -> Response:
    settings = Settings.from_env()
    store = FirestoreOrderStore(project=settings.google_cloud_project)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    token = payload.get("token") if isinstance(payload, dict) else None
    try:
        result = exchange_customer_token(
            token,
            store,
            derivation_secret=settings.token_derivation_secret,
            session_minutes=settings.customer_session_minutes,
        )
    except CustomerSessionError:
        return JSONResponse({"status": "denied"}, status_code=403, headers=UPLOAD_SECURITY_HEADERS.copy())

    response = JSONResponse({"status": "ok", "next": result.next_path}, headers=UPLOAD_SECURITY_HEADERS.copy())
    response.set_cookie(
        CUSTOMER_SESSION_COOKIE,
        result.raw_session_id,
        max_age=settings.customer_session_minutes * 60,
        expires=settings.customer_session_minutes * 60,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


def get_session_cookie(request: Request) -> str | None:
    return getattr(request, "cookies", {}).get(CUSTOMER_SESSION_COOKIE)


@app.get("/upload")
def upload_form(request: Request) -> Response:
    settings = Settings.from_env()
    store = FirestoreOrderStore(project=settings.google_cloud_project)
    try:
        _, order = validate_customer_session(get_session_cookie(request), "upload", store)
        ensure_upload_allowed(order)
    except (CustomerSessionError, UploadNotAllowedError):
        return generic_denial_response()
    return secure_html_response(render_upload_form())


@app.get("/result")
def result_page(request: Request) -> Response:
    settings = Settings.from_env()
    store = FirestoreOrderStore(project=settings.google_cloud_project)
    try:
        _, order = validate_customer_session(get_session_cookie(request), "result", store)
    except CustomerSessionError:
        return generic_result_denial_response()

    if is_expert_review_blocked(order):
        return secure_html_response(
            render_page(
                "<p>Your SENALO Expert Review is being prepared. We'll contact you when the review is complete.</p>"
            )
        )
    if not is_result_releasable(order):
        return generic_result_denial_response()

    mark_result_accessed(order, store)
    return secure_html_response(render_result_page(result_product_label(order)))


@app.get("/download/pdf")
def download_pdf(request: Request) -> Response:
    return download_artifact(request, "pdf")


@app.get("/download/excel")
def download_excel(request: Request) -> Response:
    return download_artifact(request, "excel")


@app.get("/operator/reviews")
def operator_reviews(request: Request) -> Response:
    settings = Settings.from_env()
    try:
        authenticate_operator(request, settings)
    except OperatorAuthError:
        return generic_operator_denial_response()
    store = FirestoreOrderStore(project=settings.google_cloud_project)
    return secure_html_response(render_operator_reviews_page(list_expert_review_orders(store)))


@app.get("/operator/reviews/{order_id}")
def operator_review_detail(request: Request, order_id: str) -> Response:
    settings = Settings.from_env()
    store = FirestoreOrderStore(project=settings.google_cloud_project)
    try:
        authenticate_operator(request, settings)
        order = get_review_order(store, order_id)
    except OperatorAuthError:
        return generic_operator_denial_response()
    except ReviewError:
        return secure_html_response(render_page("<p>Expert Review order was not found.</p>"), status_code=404)
    return secure_html_response(render_operator_review_detail(order))


@app.post("/operator/reviews/{order_id}/draft")
def operator_review_draft(
    request: Request,
    order_id: str,
    commentary: str = Form(""),
    action_1: str = Form(""),
    action_2: str = Form(""),
    action_3: str = Form(""),
    action_4: str = Form(""),
    action_5: str = Form(""),
) -> Response:
    settings = Settings.from_env()
    store = FirestoreOrderStore(project=settings.google_cloud_project)
    try:
        operator = authenticate_operator(request, settings)
        order = save_review_draft(
            order_id=order_id,
            store=store,
            operator=operator,
            commentary=commentary,
            action_values=[action_1, action_2, action_3, action_4, action_5],
        )
    except OperatorAuthError:
        return generic_operator_denial_response()
    except ReviewError as exc:
        return secure_html_response(render_page(f"<p>Draft was not saved: {escape(exc.error_code)}</p>"), status_code=400)
    return secure_html_response(render_operator_review_detail(order, ["Draft saved."]))


@app.post("/operator/reviews/{order_id}/release")
async def operator_review_release(
    request: Request,
    order_id: str,
    commentary: str = Form(...),
    action_1: str = Form(""),
    action_2: str = Form(""),
    action_3: str = Form(""),
    action_4: str = Form(""),
    action_5: str = Form(""),
    confirmation: str | None = Form(None),
    final_pdf: UploadFile | None = File(None),
    final_excel: UploadFile | None = File(None),
) -> Response:
    settings = Settings.from_env()
    store = FirestoreOrderStore(project=settings.google_cloud_project)
    try:
        operator = authenticate_operator(request, settings)
        pdf_bytes = await final_pdf.read() if final_pdf and getattr(final_pdf, "filename", None) else None
        excel_bytes = await final_excel.read() if final_excel and getattr(final_excel, "filename", None) else None
        if settings.resend_api_key and settings.senalo_email_from:
            email_provider = ResendEmailProvider(
                settings.resend_api_key,
                settings.senalo_email_from,
                settings.senalo_email_reply_to,
            )
        else:
            email_provider = UnconfiguredEmailProvider()
        order = release_expert_review(
            order_id=order_id,
            store=store,
            storage=get_upload_storage(settings),
            settings=settings,
            email_provider=email_provider,
            operator=operator,
            commentary=commentary,
            action_values=[action_1, action_2, action_3, action_4, action_5],
            confirmation=confirmation,
            replacement_pdf=pdf_bytes,
            replacement_excel=excel_bytes,
            replacement_excel_filename=getattr(final_excel, "filename", None),
        )
    except OperatorAuthError:
        return generic_operator_denial_response()
    except ReviewError as exc:
        return secure_html_response(render_page(f"<p>Review was not released: {escape(exc.error_code)}</p>"), status_code=400)
    return secure_html_response(render_operator_review_detail(order, ["Expert Review released."]))


@app.get("/operator/reviews/{order_id}/download/{artifact_type}")
def operator_review_download(request: Request, order_id: str, artifact_type: str) -> Response:
    settings = Settings.from_env()
    store = FirestoreOrderStore(project=settings.google_cloud_project)
    try:
        authenticate_operator(request, settings)
        order = get_review_order(store, order_id)
    except OperatorAuthError:
        return generic_operator_denial_response()
    except ReviewError:
        return secure_html_response(render_page("<p>Expert Review order was not found.</p>"), status_code=404)

    paths = {
        "source": (order.upload_object_path, "application/octet-stream", order.upload_original_filename or "source-file"),
        "base-pdf": (order.pdf_object_path, "application/pdf", "SENALO-Base-Full-Analysis.pdf"),
        "base-excel": (
            order.excel_object_path,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "SENALO-Base-Full-Analysis.xlsx",
        ),
        "final-pdf": (order.final_pdf_object_path, "application/pdf", "SENALO-Expert-Review.pdf"),
        "final-excel": (
            order.final_excel_object_path,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "SENALO-Expert-Review.xlsx",
        ),
    }
    if artifact_type not in paths:
        return generic_operator_denial_response()
    object_path, content_type, filename = paths[artifact_type]
    if not object_path:
        return secure_html_response(render_page("<p>Requested file is not available.</p>"), status_code=404)
    try:
        content = get_upload_storage(settings).load(str(object_path))
    except UploadStorageError:
        return secure_html_response(render_page("<p>Requested file is not available.</p>"), status_code=404)
    return Response(
        content,
        media_type=content_type,
        headers={**UPLOAD_SECURITY_HEADERS, "Content-Disposition": f'attachment; filename="{filename}"'},
    )


def download_artifact(request: Request, artifact_type: str) -> Response:
    settings = Settings.from_env()
    store = FirestoreOrderStore(project=settings.google_cloud_project)
    try:
        _, order = validate_customer_session(get_session_cookie(request), "result", store)
        _, content, content_type, filename = load_authorized_artifact_for_order(
            order=order,
            artifact_type=artifact_type,
            store=store,
            storage=get_upload_storage(settings),
        )
    except (CustomerSessionError, ResultNotReleasableError):
        return generic_result_denial_response()

    return Response(
        content,
        media_type=content_type,
        headers={
            **UPLOAD_SECURITY_HEADERS,
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.post("/upload")
async def upload_submit(
    request: Request,
    t: str | None = Form(None),
    business_type: str = Form(...),
    opening_cash: str = Form(...),
    financial_file: UploadFile = File(...),
) -> Response:
    started = time.perf_counter()
    settings = Settings.from_env()
    store = FirestoreOrderStore(project=settings.google_cloud_project)
    try:
        _, order = validate_customer_session(get_session_cookie(request), "upload", store)
        content = await financial_file.read(settings.max_upload_bytes + 1)
        result = submit_upload(
            order=order,
            store=store,
            storage=get_upload_storage(settings),
            business_type=business_type,
            opening_cash_value=opening_cash,
            filename=financial_file.filename or "upload",
            content=content,
            max_upload_bytes=settings.max_upload_bytes,
        )
    except (CustomerSessionError, UploadNotAllowedError):
        return generic_denial_response()
    except InvalidBusinessTypeError as exc:
        return secure_html_response(render_upload_form([str(exc)]), status_code=400)
    except InvalidOpeningCashError as exc:
        return secure_html_response(render_upload_form([str(exc)]), status_code=400)
    except ValidationFailedError as exc:
        logger.info(
            "upload_validation_failed",
            extra={"error_code": exc.error_code, "duration_ms": round((time.perf_counter() - started) * 1000, 2)},
        )
        return secure_html_response(render_upload_form(exc.errors), status_code=400)
    except (IntakeError, UploadStorageError) as exc:
        logger.warning(
            "upload_failed",
            extra={"error_code": getattr(exc, "error_code", str(exc)), "duration_ms": round((time.perf_counter() - started) * 1000, 2)},
        )
        return secure_html_response(render_upload_form(["The upload could not be completed. Please try again."]), status_code=400)

    logger.info(
        "upload_validated",
        extra={
            "order_id": result.order.order_id,
            "upload_status": result.order.upload_status,
            "file_type": result.order.upload_content_type,
            "file_size": result.order.upload_size_bytes,
            "validation_status": result.order.validation_status,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )
    return secure_html_response(
        render_page(
            "<p>Your financial data has been received successfully.</p>"
            "<p>SENALO will now prepare the next stage of your analysis.</p>"
            "<p>You do not need to submit the file again.</p>"
        )
    )


def render_access_page(nonce: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SENALO Secure Access</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f8fafc; color: #0f172a; }}
    main {{ max-width: 680px; margin: 0 auto; padding: 32px 18px; }}
    .brand {{ font-weight: 800; letter-spacing: 0.04em; margin-bottom: 6px; }}
    .panel {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 24px; box-shadow: 0 1px 2px rgba(15,23,42,.05); }}
    .note {{ color: #475569; }}
  </style>
</head>
<body>
  <main>
    <div class="brand">SENALO</div>
    <div class="panel">
      <h1>Secure Access</h1>
      <p class="note" id="status">Preparing your secure session...</p>
    </div>
  </main>
  <script nonce="{escape(nonce)}">
    (async function () {{
      const raw = window.location.hash || "";
      const token = raw.startsWith("#") ? raw.slice(1) : "";
      window.history.replaceState(null, "", "/access");
      if (!token) {{
        document.getElementById("status").textContent = "This secure SENALO link is invalid or has expired. Please contact hello@senalo.com.au for assistance.";
        return;
      }}
      try {{
        const response = await fetch("/session/exchange", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          credentials: "same-origin",
          body: JSON.stringify({{ token }})
        }});
        if (!response.ok) throw new Error("denied");
        const payload = await response.json();
        window.location.replace(payload.next || "/upload");
      }} catch (error) {{
        document.getElementById("status").textContent = "This secure SENALO link is invalid or has expired. Please contact hello@senalo.com.au for assistance.";
      }}
    }})();
  </script>
</body>
</html>"""


def render_upload_form(errors: list[str] | None = None) -> str:
    options = "".join(
        f'<option value="{escape(value)}">{escape(value)}</option>'
        for value in [
            "Food & Beverage",
            "Market Stall / Vendor",
            "Independent Retail",
            "Other Owner-Operated Business",
        ]
    )
    error_html = ""
    if errors:
        items = "".join(f"<li>{escape(error)}</li>" for error in errors)
        error_html = f'<div class="error"><p>Please fix the following:</p><ul>{items}</ul></div>'
    form = f"""
        {error_html}
        <form method="post" action="/upload" enctype="multipart/form-data">
            <label>Business Type
                <select name="business_type" required>{options}</select>
            </label>
            <label>Opening Cash
                <input name="opening_cash" type="number" min="0" step="0.01" required>
            </label>
            <label>Financial data file
                <input name="financial_file" type="file" accept=".csv,.xlsx" required>
            </label>
            <button type="submit">Submit financial data</button>
        </form>
    """
    return render_page(form)


def render_result_page(title: str = "SENALO Full Analysis") -> str:
    description = (
        "These files contain your final human-reviewed SENALO report and Excel analysis."
        if title == "SENALO Expert Review"
        else "These files contain your SENALO financial analysis, forecast, scenarios and management summary."
    )
    return render_page(
        f"""
        <h2>{escape(title)}</h2>
        <p>Your analysis is ready.</p>
        <p>{escape(description)}</p>
        <ul>
            <li><a href="/download/pdf">Download PDF Report</a></li>
            <li><a href="/download/excel">Download Excel Analysis</a></li>
        </ul>
        <p>If you need assistance, contact hello@senalo.com.au.</p>
        <p class="note">SENALO<br>See clearly. Decide better.<br>https://senalo.com.au</p>
        """
    )


def render_operator_reviews_page(orders) -> str:
    if not orders:
        rows = '<tr><td colspan="5">No Expert Reviews are pending.</td></tr>'
    else:
        rows = "".join(
            "<tr>"
            f'<td><a href="/operator/reviews/{escape(order.order_id)}">{escape(order.order_id)}</a></td>'
            f"<td>{escape(order.customer_name or '')}</td>"
            f"<td>{escape(order.customer_email or '')}</td>"
            f"<td>{escape(order.expert_review_status)}</td>"
            f"<td>{escape(str(order.updated_at))}</td>"
            "</tr>"
            for order in orders
        )
    return render_page(
        f"""
        <h2>Expert Reviews</h2>
        <table>
          <thead><tr><th>Order</th><th>Customer</th><th>Email</th><th>Status</th><th>Updated</th></tr></thead>
          <tbody>{rows}</tbody>
        </table>
        """
    )


def render_operator_review_detail(order, messages: list[str] | None = None) -> str:
    saved_actions = {int(action.get("rank", 0)): str(action.get("text", "")) for action in order.review_actions}
    action_inputs = "".join(
        f"""
        <label>Management Action {index}
            <input name="action_{index}" value="{escape(saved_actions.get(index, ''))}">
        </label>
        """
        for index in range(1, 6)
    )
    message_html = "".join(f'<div class="success">{escape(message)}</div>' for message in (messages or []))
    final_links = ""
    if order.final_pdf_object_path or order.final_excel_object_path:
        final_links = (
            f'<li><a href="/operator/reviews/{escape(order.order_id)}/download/final-pdf">Download Final PDF</a></li>'
            f'<li><a href="/operator/reviews/{escape(order.order_id)}/download/final-excel">Download Final Excel</a></li>'
        )
    body = f"""
        {message_html}
        <h2>Expert Review Detail</h2>
        <dl>
            <dt>Order</dt><dd>{escape(order.order_id)}</dd>
            <dt>Customer</dt><dd>{escape(order.customer_name or '')}</dd>
            <dt>Email</dt><dd>{escape(order.customer_email or '')}</dd>
            <dt>Status</dt><dd>{escape(order.expert_review_status)}</dd>
            <dt>Business Type</dt><dd>{escape(order.business_type or '')}</dd>
        </dl>
        <h3>Operator Downloads</h3>
        <ul>
            <li><a href="/operator/reviews/{escape(order.order_id)}/download/source">Download Source Upload</a></li>
            <li><a href="/operator/reviews/{escape(order.order_id)}/download/base-pdf">Download Base PDF</a></li>
            <li><a href="/operator/reviews/{escape(order.order_id)}/download/base-excel">Download Base Excel</a></li>
            {final_links}
        </ul>
        <h3>Review Draft</h3>
        <form method="post" action="/operator/reviews/{escape(order.order_id)}/draft">
            <label>Customised Commentary
                <textarea name="commentary" rows="8">{escape(order.review_commentary or '')}</textarea>
            </label>
            {action_inputs}
            <button type="submit">Save Draft</button>
        </form>
        <h3>Approve And Release</h3>
        <form method="post" action="/operator/reviews/{escape(order.order_id)}/release" enctype="multipart/form-data">
            <label>Customised Commentary
                <textarea name="commentary" rows="8">{escape(order.review_commentary or '')}</textarea>
            </label>
            {action_inputs}
            <label>Optional replacement final PDF
                <input name="final_pdf" type="file" accept=".pdf">
            </label>
            <label>Optional replacement final Excel
                <input name="final_excel" type="file" accept=".xlsx">
            </label>
            <label class="inline"><input name="confirmation" type="checkbox" value="approve-release"> I confirm this Expert Review has been manually reviewed and is approved for customer release.</label>
            <button type="submit">Approve And Release</button>
        </form>
        <h3>Saved Human Review</h3>
        <p>{safe_review_text(order.review_commentary)}</p>
        <ol>{"".join(f"<li>{safe_review_text(action.get('text'))}</li>" for action in order.review_actions)}</ol>
    """
    return render_page(body)


def render_page(body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SENALO Secure Upload</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f8fafc; color: #0f172a; }}
    main {{ max-width: 680px; margin: 0 auto; padding: 32px 18px; }}
    .brand {{ font-weight: 800; letter-spacing: 0.04em; margin-bottom: 6px; }}
    .panel {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 24px; box-shadow: 0 1px 2px rgba(15,23,42,.05); }}
    label {{ display: block; font-weight: 700; margin: 16px 0 6px; }}
    input, select, textarea {{ width: 100%; box-sizing: border-box; padding: 10px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 16px; }}
    button {{ margin-top: 20px; background: #2563eb; color: #fff; border: 0; border-radius: 6px; padding: 12px 16px; font-weight: 800; cursor: pointer; }}
    .error {{ border-left: 4px solid #dc2626; background: #fef2f2; padding: 12px 14px; margin-bottom: 16px; }}
    .success {{ border-left: 4px solid #16a34a; background: #f0fdf4; padding: 12px 14px; margin-bottom: 16px; }}
    .note {{ color: #475569; margin-top: 0; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid #e2e8f0; padding: 8px; text-align: left; }}
    dt {{ font-weight: 700; margin-top: 8px; }}
    .inline, .inline input {{ width: auto; }}
  </style>
</head>
<body>
  <main>
    <div class="brand">SENALO</div>
    <div class="panel">
      <h1>Secure Financial Data Upload</h1>
      <p class="note">Provide your business type, opening cash, and monthly financial data file.</p>
      {body}
    </div>
  </main>
</body>
</html>"""


@app.post("/webhooks/stripe")
async def stripe_webhook_endpoint(request: Request) -> dict[str, str]:
    started = time.perf_counter()
    settings = Settings.from_env()
    raw_body = await request.body()
    signature = request.headers.get("Stripe-Signature")

    try:
        event = construct_event(raw_body, signature, settings.stripe_webhook_secret)
    except StripeSignatureError as exc:
        logger.warning("stripe_webhook_rejected", extra={"error_code": "INVALID_SIGNATURE"})
        raise HTTPException(status_code=400, detail="Invalid Stripe signature") from exc

    store = FirestoreOrderStore(project=settings.google_cloud_project)
    if settings.resend_api_key and settings.senalo_email_from:
        email_provider = ResendEmailProvider(
            settings.resend_api_key,
            settings.senalo_email_from,
            settings.senalo_email_reply_to,
        )
    else:
        email_provider = UnconfiguredEmailProvider()
    fulfilment_service = OrderFulfilmentService(store, settings, email_provider)
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    try:
        result = process_stripe_event(event, store, settings, fulfilment_service=fulfilment_service)
    except FulfilmentError as exc:
        logger.warning(
            "stripe_webhook_processing_failed",
            extra={"event_id": event_id, "event_type": event_type, "error_code": exc.error_code},
        )
        raise HTTPException(status_code=exc.status_code, detail=exc.error_code) from exc
    except EmailDeliveryError as exc:
        logger.warning(
            "stripe_webhook_email_failed",
            extra={"event_id": event_id, "event_type": event_type, "error_code": str(exc)},
        )
        raise HTTPException(status_code=500, detail="EMAIL_DELIVERY_ERROR") from exc

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.info(
        "stripe_webhook_processed",
        extra={
            "event_id": event_id,
            "event_type": event_type,
            "order_id": result.get("order_id"),
            "product_code": result.get("product_code"),
            "payment_status": result.get("payment_status"),
            "processing_outcome": result.get("status"),
            "duration_ms": duration_ms,
        },
    )
    return {"status": str(result["status"])}
