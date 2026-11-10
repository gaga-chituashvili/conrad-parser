import logging
import random
import re
import time

from playwright.sync_api import BrowserContext, Page, Playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from . import config

log = logging.getLogger(__name__)

COOKIE_BUTTONS = [
    '[data-testid="uc-accept-all-button"]',
    'button:has-text("Alle akzeptieren")',
    'button:has-text("Alle erlauben")',
    'button:has-text("Akzeptieren")',
    'button:has-text("Einverstanden")',
]


class BlockedError(Exception):
    pass


def open_browser(pw: Playwright, headless: bool = False, use_chrome: bool = False) -> BrowserContext:
    """Persistent profile, so cookies and the login survive between runs."""
    options = dict(
        user_data_dir=config.PROFILE_DIR,
        headless=headless,
        locale="de-DE",
        timezone_id="Europe/Berlin",
        viewport={"width": 1440, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    if use_chrome:
        options["channel"] = "chrome"

    context = pw.chromium.launch_persistent_context(**options)
    context.set_default_timeout(20_000)
    return context


def accept_cookies(page: Page) -> None:
    for selector in COOKIE_BUTTONS:
        button = page.locator(selector).first
        try:
            if button.is_visible(timeout=1500):
                button.click()
                page.wait_for_timeout(800)
                return
        except PlaywrightTimeout:
            continue
        except Exception:
            continue


def choose_customer_type(page: Page) -> None:
    """First visit shows a 'Geschäftskunde / Privatkunde' popup over the page.
    Business prices (zzgl. MwSt.) match the example in the task."""
    button = page.get_by_text(re.compile(r"^\s*Ich bin Geschäftskunde\s*$", re.I)).first
    try:
        if button.is_visible(timeout=1500):
            button.click()
            page.wait_for_timeout(1500)
    except Exception:
        pass


_switch_failed = False


def ensure_business_prices(page: Page) -> bool:
    """Price tiers, other sellers' offers and shipping cost are shown only in the
    business view (zzgl. MwSt.). Uses the VAT switch in the header if the page is in
    the private view. Returns True if the view was switched."""
    global _switch_failed
    if _switch_failed or "zzgl. mwst" in page.inner_text("body").lower():
        return False

    toggle = page.get_by_text(re.compile(r"^\s*(Geschäftskunde|Privatkunde)\s*$", re.I))
    clicked = False
    for idx in range(min(toggle.count(), 3)):
        try:
            if toggle.nth(idx).is_visible():
                toggle.nth(idx).click()
                clicked = True
                break
        except Exception:
            continue

    if clicked:
        page.wait_for_timeout(2500)
        choose_customer_type(page)
        page.wait_for_timeout(1500)
        if "zzgl. mwst" in page.inner_text("body").lower():
            log.info("Switched to business prices (zzgl. MwSt.)")
            return True

    _switch_failed = True
    log.warning("Could not switch to business prices - price tiers and other offers "
                "are shown only to business customers")
    return False


def check_not_blocked(page: Page) -> None:
    title = (page.title() or "").lower()
    if "access denied" in title or "pardon our interruption" in title:
        raise BlockedError(f"Blocked by the site on {page.url}")


def goto(page: Page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightTimeout:
        pass  # some trackers never go idle, the content is there anyway
    accept_cookies(page)
    choose_customer_type(page)
    check_not_blocked(page)


def human_scroll(page: Page, steps: int = 8) -> None:
    """Scroll down in small steps - lazy blocks (tech data, docs) load on scroll."""
    for _ in range(steps):
        page.mouse.wheel(0, random.randint(500, 900))
        page.wait_for_timeout(random.randint(250, 500))
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(700)
    page.evaluate("window.scrollTo(0, 0)")


def click_by_text(page: Page, pattern: str, timeout: int = 1500) -> bool:
    target = page.get_by_text(re.compile(pattern, re.I)).first
    try:
        if target.is_visible(timeout=timeout):
            target.click()
            return True
    except Exception:
        pass
    return False


def pause(min_s: float = None, max_s: float = None) -> None:
    low = config.MIN_DELAY if min_s is None else min_s
    high = config.MAX_DELAY if max_s is None else max_s
    time.sleep(random.uniform(low, high))
