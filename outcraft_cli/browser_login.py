from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING
from urllib.parse import quote_plus, urljoin, urlsplit

from .errors import OutcraftError

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Page, Request, Route

    from .transport import Transport


def browser_login(transport: Transport, email: str, password: str) -> None:
    if not email or not password:
        raise OutcraftError(
            "Set OUTCRAFT_EMAIL and OUTCRAFT_PASSWORD locally, then run auth login."
        )
    try:
        from playwright.sync_api import Error, sync_playwright
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    except ImportError:
        raise OutcraftError(
            "Browser login needs Playwright. Install outcraft-cli[browser], then run "
            "python -m playwright install --with-deps --only-shell chromium."
        ) from None

    timeout = transport.timeout * 1000
    blocked: Callable[[], bool] | None = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, timeout=timeout)
            try:
                context = browser.new_context(service_workers="block")
                page = context.new_page()
                page.set_default_timeout(timeout)

                blocked = _install_origin_guards(context, page, transport, email, password)
                page.goto(transport.url("/auth/login"), wait_until="domcontentloaded")
                transport.url(page.url)
                headers: dict[str, str] = {}
                if not urlsplit(page.url).path.startswith("/campaigns"):
                    email_input = page.locator('input[type="email"]:visible')
                    # The form is hidden until the page's scripts run after domcontentloaded.
                    try:
                        email_input.first.wait_for(state="visible")
                    except PlaywrightTimeoutError:
                        pass
                    if email_input.count() != 1:
                        raise OutcraftError(
                            "Cannot identify 1 login form. Browser verification may be required."
                        )
                    form = page.locator("form").filter(has=email_input)
                    if form.count() != 1:
                        raise OutcraftError("Cannot identify 1 login form.")
                    email_input.fill(email)
                    password_input = page.locator('input[type="password"]:visible')
                    if password_input.count() == 0:
                        form.locator('button[type="submit"]').click()
                        password_input.wait_for(state="visible")
                    if password_input.count() != 1:
                        raise OutcraftError("Cannot identify 1 password field.")
                    password_input.fill(password)
                    submit = (
                        page.locator("form")
                        .filter(has=password_input)
                        .locator('button[type="submit"]')
                    )
                    with page.expect_response(
                        lambda response: (
                            response.request.method == "POST"
                            and urlsplit(response.url).netloc == urlsplit(transport.base_url).netloc
                        )
                    ) as pending:
                        submit.click()
                    response = pending.value
                    headers = response.request.all_headers()
                    if response.status >= 400:
                        raise OutcraftError(
                            "Login was rejected or challenged. Verify credentials and account access."
                        )
                    page.wait_for_url(transport.base_url + "/campaigns**", timeout=timeout)
                transport.url(page.url)
                if "user-agent" not in headers:
                    headers["user-agent"] = str(page.evaluate("navigator.userAgent"))
                cookies: list[dict[str, object]] = [
                    dict(cookie) for cookie in context.cookies([transport.base_url])
                ]
            finally:
                browser.close()
    except Error:
        if blocked is not None and blocked():
            raise OutcraftError(
                "Cross-origin login request refused; credentials stayed on the configured origin."
            ) from None
        raise OutcraftError(
            "Browser login did not complete. Check credentials, verification/MFA and Chromium installation. "
            "Run python -m playwright install --with-deps --only-shell chromium if Chromium is missing."
        ) from None
    transport.adopt_browser_session(cookies, headers)


def _install_origin_guards(
    context: BrowserContext, page: Page, transport: Transport, email: str, password: str
) -> Callable[[], bool]:
    blocked = False
    credential_markers = tuple(
        value.encode()
        for secret in (email, password)
        for value in (
            secret,
            quote_plus(secret),
            json.dumps(secret)[1:-1],
            json.dumps(secret, ensure_ascii=False)[1:-1],
        )
    )

    def carries_credentials(body: bytes | None) -> bool:
        return body is not None and any(marker in body for marker in credential_markers)

    def guard(route: Route, request: Request) -> None:
        nonlocal blocked
        if request.method not in ("GET", "HEAD") or (
            request.is_navigation_request() and request.frame == page.main_frame
        ):
            try:
                transport.url(request.url)
            except OutcraftError:
                blocked = (
                    blocked
                    or (request.is_navigation_request() and request.frame == page.main_frame)
                    or carries_credentials(request.post_data_buffer)
                )
                route.abort()
                return
        route.continue_()

    # Playwright routing skips redirect hops; inspect Location before Chromium follows it.
    cdp = context.new_cdp_session(page)
    main_frame_id = cdp.send("Page.getFrameTree")["frameTree"]["frame"]["id"]

    def guard_redirect(event: dict[str, object]) -> None:
        nonlocal blocked
        request_id = str(event["requestId"])
        request = event.get("request", {})
        method = str(request.get("method", "GET")) if isinstance(request, dict) else "GET"
        navigation = (
            event.get("resourceType") == "Document" and event.get("frameId") == main_frame_id
        )
        if method in ("GET", "HEAD") and not navigation:
            cdp.send("Fetch.continueRequest", {"requestId": request_id})
            return
        response_headers = event.get("responseHeaders", [])
        for header in response_headers if isinstance(response_headers, list) else []:
            if isinstance(header, dict) and str(header.get("name", "")).lower() == "location":
                source = str(request.get("url", "")) if isinstance(request, dict) else ""
                location = str(header.get("value", ""))
                try:
                    if location.startswith("//"):
                        raise OutcraftError("Protocol-relative login redirect refused.")
                    transport.url(urljoin(source, location))
                except OutcraftError:
                    body = (
                        str(request.get("postData", "")).encode()
                        if isinstance(request, dict)
                        else None
                    )
                    blocked = blocked or navigation or carries_credentials(body)
                    cdp.send(
                        "Fetch.failRequest",
                        {"requestId": request_id, "errorReason": "BlockedByClient"},
                    )
                    return
        cdp.send("Fetch.continueRequest", {"requestId": request_id})

    cdp.on("Fetch.requestPaused", guard_redirect)
    cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Response"}]})

    context.route("**/*", guard)
    return lambda: blocked
