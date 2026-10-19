"""Real JavaScript login on loopback, with no personal browser or live service."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from _campaign_fixtures import campaign_page
from _support import LocalServer, Recorded, Reply

EMAIL = "synthetic@example.invalid"
PASSWORD = "synthetic-password-not-a-secret"
SESSION = "synthetic-browser-session"


def require_chromium() -> None:
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError as exc:
        raise unittest.SkipTest(
            "Optional Playwright unavailable; CI installs Playwright/Chromium"
        ) from exc
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
    except Error as exc:
        raise unittest.SkipTest(
            "Optional Chromium unavailable; run python -m playwright install chromium"
        ) from exc


def browser_processes() -> set[int]:
    """Observe our real child processes, without patching browser internals."""
    import os

    rows = subprocess.check_output(["ps", "-axo", "pid=,ppid=,command="], text=True)
    processes = [line.strip().split(None, 2) for line in rows.splitlines()]
    descendants = {os.getpid()}
    while True:
        children = {int(pid) for pid, parent, _ in processes if int(parent) in descendants}
        expanded = descendants | children
        if expanded == descendants:
            break
        descendants = expanded
    return {
        int(pid)
        for pid, _, command in processes
        if int(pid) in descendants
        and any(
            marker in command.lower() for marker in ("chromium", "headless_shell", "/playwright/")
        )
    }


def assert_browser_closed(test: unittest.TestCase, before: set[int]) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        remaining = browser_processes() - before
        if not remaining:
            return
        time.sleep(0.05)
    test.fail(f"Login left {len(remaining)} real browser/driver processes running: {remaining}")


def without_browser(base_url: str, script: str, session_file: Path | None = None) -> object:
    """Run a public Client flow with an audit of every attempted child-process launch."""
    session_expression = "None" if session_file is None else f"Path({str(session_file)!r})"
    source = f"""
import json
import sys
from pathlib import Path
from outcraft_cli.client import Client
from outcraft_cli.errors import OutcraftError
from outcraft_cli.transport import Transport
launches = []
def audit(event, args):
    if event in ("subprocess.Popen", "os.posix_spawn", "os.fork"):
        launches.append(event)
sys.addaudithook(audit)
client = Client(Transport(base_url={base_url!r}, session_file={session_expression}))
{script}
print(json.dumps({{"result": result, "launches": launches}}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", source], text=True, capture_output=True, timeout=20, check=False
    )
    if completed.returncode:
        raise AssertionError(f"HTTPX-only child failed: {completed.stderr}")
    payload = json.loads(completed.stdout)
    if payload["launches"]:
        raise AssertionError(f"HTTPX-only flow attempted browser processes: {payload['launches']}")
    return payload["result"]


class BrowserLoginServer(LocalServer):
    def __init__(
        self,
        *,
        two_step: bool = False,
        valid_credentials: bool = True,
        http_authenticated: bool = True,
        form_action: str = "/auth/session",
        redirect: str = "/campaigns",
        reveal_delay_ms: int = 0,
    ) -> None:
        super().__init__()
        self.two_step = two_step
        self.valid_credentials = valid_credentials
        self.http_authenticated = http_authenticated
        self.form_action = form_action
        self.redirect = redirect
        self.reveal_delay_ms = reveal_delay_ms
        self.login_user_agent = ""
        self.submissions: list[dict[str, object]] = []

    def http_reads(self) -> list[Recorded]:
        return [
            request
            for request in self.requests_to("GET", "/campaigns")
            if request.headers.get("sec-fetch-dest") != "document"
        ]

    def _next_reply(self, method: str, path: str) -> Reply:
        path = urlsplit(path).path
        if (method, path) in self._routes:
            return super()._next_reply(method, path)
        request = self.requests_to(method, path)[-1]
        if method == "GET" and path == "/auth/login":
            return Reply.html(self.login_form())
        if method == "POST" and path == "/auth/session":
            submitted = request.json()
            self.submissions.append(submitted)
            if not self.valid_credentials or (
                submitted.get("email"),
                submitted.get("password"),
            ) != (EMAIL, PASSWORD):
                return Reply.json({"message": f"Rejected {EMAIL} / {PASSWORD}"}, status=422)
            self.login_user_agent = request.headers.get("user-agent", "")
            return Reply.json(
                {"redirect": self.redirect},
                cookies=[f"outcraft_session={SESSION}; Path=/; HttpOnly; SameSite=Lax"],
            )
        if method == "GET" and path == "/campaigns":
            browser = request.headers.get("sec-fetch-dest") == "document"
            authenticated = (
                request.cookies().get("outcraft_session") == SESSION
                and request.headers.get("user-agent") == self.login_user_agent
            )
            if authenticated and (browser or self.http_authenticated):
                return Reply.html(campaign_page())
            return Reply.redirect("/auth/login")
        return Reply(status=404, body="No synthetic route")

    def login_form(self) -> str:
        return (
            """<!doctype html><html><head><title>Synthetic login</title></head><body>
<form id="login" method="post"><label for="email">Email address</label>
<input id="email" name="email" type="email" autocomplete="username" required>
<div id="password-fields"></div><button type="submit" id="submit"></button>
<p id="error" role="alert" hidden></p></form><script>
const form = document.querySelector('#login');
const button = document.querySelector('#submit');
const twoStep = TWO_STEP;
let email = '';
let passwordVisible = false;
form.action = FORM_ACTION;
if (REVEAL_DELAY_MS) {
  form.hidden = true;
  setTimeout(() => { form.hidden = false; }, REVEAL_DELAY_MS);
}
function showPassword() {
  document.querySelector('#password-fields').innerHTML =
    '<label for="password">Password</label>' +
    '<input id="password" name="password" type="password" autocomplete="current-password" required>';
  passwordVisible = true;
  button.textContent = 'Sign in';
}
if (twoStep) button.textContent = 'Continue'; else showPassword();
form.addEventListener('submit', async event => {
  event.preventDefault();
  email = email || document.querySelector('#email').value;
  if (!passwordVisible) {
    document.querySelector('label[for="email"]').remove();
    document.querySelector('#email').remove();
    showPassword();
    document.querySelector('#password').focus();
    return;
  }
  const response = await fetch(form.action, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({email, password: document.querySelector('#password').value,
                          userAgent: navigator.userAgent, webdriver: navigator.webdriver})
  });
  const data = await response.json();
  if (response.ok) window.location.assign(data.redirect);
  else {
    const error = document.querySelector('#error');
    error.hidden = false;
    error.textContent = data.message;
  }
});
</script></body></html>""".replace("TWO_STEP", json.dumps(self.two_step))
            .replace("FORM_ACTION", json.dumps(self.form_action))
            .replace("REVEAL_DELAY_MS", json.dumps(self.reveal_delay_ms))
        )
