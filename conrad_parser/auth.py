import logging

from playwright.sync_api import Page

from . import config
from .browser import click_by_text, goto

log = logging.getLogger(__name__)

EMAIL_INPUT = (
    'input[type="email"], input[name="username"], input[name*="mail" i], '
    'input[id*="username" i], input[id*="email" i]'
)
PASSWORD_INPUT = 'input[type="password"]'


def login_form_visible(page: Page) -> bool:
    if page.locator(f"{EMAIL_INPUT}, {PASSWORD_INPUT}").first.is_visible():
        return True
    text = page.inner_text("body").lower()
    return "anmelden oder registrieren" in text or "login erforderlich" in text


def open_account(page: Page) -> None:
    """Header 'Konto' button: logged out -> login form, logged in -> account menu/page."""
    goto(page, config.ACCOUNT_URL)
    page.wait_for_timeout(2000)
    if not page.locator(f"{EMAIL_INPUT}, {PASSWORD_INPUT}").first.is_visible():
        # logged out: account page shows "Login erforderlich - Jetzt anmelden"
        # (the footer has a newsletter "Jetzt anmelden" too - only click it next to that message)
        needs_login = "login erforderlich" in page.inner_text("body").lower()
        if needs_login:
            click_by_text(page, r"^\s*jetzt anmelden\s*$", timeout=3000)
        else:
            click_by_text(page, r"^\s*konto\s*$", timeout=3000)
        page.wait_for_timeout(3000)


def is_logged_in(page: Page) -> bool:
    open_account(page)
    if login_form_visible(page):
        return False
    # the login form lives on a separate auth domain
    return "www.conrad.de" in page.url


def auto_login(page: Page, email: str, password: str) -> None:
    open_account(page)

    email_field = page.locator(EMAIL_INPUT).first
    email_field.wait_for(state="visible", timeout=20_000)
    email_field.click()
    email_field.press_sequentially(email, delay=60)

    password_field = page.locator(PASSWORD_INPUT).first
    if not password_field.is_visible():
        # two-step form: email first, then password
        click_by_text(page, r"^\s*(weiter|fortfahren)\s*$")
        password_field.wait_for(state="visible", timeout=15_000)

    password_field.click()
    password_field.press_sequentially(password, delay=60)
    password_field.press("Enter")
    page.wait_for_timeout(6000)


def ensure_login(page: Page) -> None:
    if is_logged_in(page):
        log.info("Already logged in (session from the browser profile)")
        return

    if config.EMAIL and config.PASSWORD:
        log.info("Logging in as %s", config.EMAIL)
        try:
            auto_login(page, config.EMAIL, config.PASSWORD)
        except Exception as exc:
            log.warning("Automatic login failed: %s", exc)

        if is_logged_in(page):
            log.info("Login OK")
            return

    # captcha, 2FA or changed form - let the user finish it in the open window
    open_account(page)
    print(
        "\nPlease log in to conrad.de in the browser window.\n"
        "When you see the 'Willkommen' page, come back here and press Enter..."
    )
    input()

    if is_logged_in(page):
        log.info("Login OK")
    else:
        log.warning("Could not confirm the login, continuing anyway")
