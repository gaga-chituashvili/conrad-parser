import logging
import re
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse

from playwright.sync_api import Page

from . import config
from .browser import goto, human_scroll

log = logging.getLogger(__name__)

PRODUCT_RE = re.compile(r"/de/p/([a-z0-9-]+)-(\d{5,9})\.html", re.I)

NEXT_BUTTONS = [
    'a[rel="next"]',
    '[aria-label*="nächste" i]',
    '[aria-label*="next page" i]',
    'button:has-text("Mehr anzeigen")',
    'button:has-text("Weitere Produkte")',
]


def find_product_links(page: Page, brand: str) -> dict:
    """Returns {bestell_nr: url} for product links of the given brand on the page."""
    hrefs = page.eval_on_selector_all('a[href*="/p/"]', "els => els.map(e => e.href)")
    found = {}
    for href in hrefs:
        match = PRODUCT_RE.search(href)
        # conrad slugs start with the brand: /de/p/balluff-induktiver-sensor-...
        if match and match.group(1).lower().startswith(brand.lower()):
            found[match.group(2)] = config.BASE_URL + match.group(0)
    return found


def with_page_param(url: str, page_no: int) -> str:
    parts = urlparse(url)
    query = parse_qs(parts.query)
    query["page"] = [str(page_no)]
    return urlunparse(parts._replace(query=urlencode(query, doseq=True)))


def go_to_next_page(page: Page, page_no: int) -> bool:
    for selector in NEXT_BUTTONS:
        button = page.locator(selector).first
        try:
            if button.is_visible(timeout=1000) and button.is_enabled():
                button.click()
                page.wait_for_timeout(3000)
                return True
        except Exception:
            continue

    # no pagination control found - try the url parameter
    goto(page, with_page_param(page.url, page_no + 1))
    return True


def collect_product_urls(page: Page, brand: str, limit: int) -> list:
    goto(page, config.SEARCH_URL.format(query=quote(brand)))

    urls = {}
    page_no = 1
    empty_pages = 0

    while len(urls) < limit and empty_pages < 3:
        human_scroll(page, steps=6)
        before = len(urls)
        for nr, url in find_product_links(page, brand).items():
            urls.setdefault(nr, url)

        log.info("Listing page %d: %d product urls so far", page_no, len(urls))
        if len(urls) >= limit:
            break

        empty_pages = empty_pages + 1 if len(urls) == before else 0
        go_to_next_page(page, page_no)
        page_no += 1

    return list(urls.values())[:limit]
