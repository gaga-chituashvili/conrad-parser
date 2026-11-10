import logging
import re
from pathlib import Path

from playwright.sync_api import Page

from . import config, parsing
from .browser import ensure_business_prices, goto, human_scroll

log = logging.getLogger(__name__)

# Everything is read by visible headings/labels ("Technische Daten", "EAN", "Versand durch"...)
# instead of css class names - conrad's classes are generated and change with every deploy.
EXTRACT_JS = r"""
() => {
  const clean = s => (s || '').replace(/ /g, ' ').replace(/[ \t]+/g, ' ')
    .replace(/ *\n */g, '\n').replace(/\n{2,}/g, '\n').trim();
  const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));

  // structured data, if the page has it
  const ld = [];
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const data = JSON.parse(s.textContent);
      ld.push(...(Array.isArray(data) ? data : (data['@graph'] || [data])));
    } catch (e) {}
  }
  const ofType = t => ld.find(i => i && [].concat(i['@type'] || []).includes(t)) || null;

  const findHeading = title => {
    const t = title.toLowerCase();
    const ok = el => clean(el.innerText).toLowerCase() === t && visible(el)
      && !el.closest('a, button, nav, [role="tab"], [role="tablist"]');
    return [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"]')].find(ok)
      || [...document.querySelectorAll('div,span,p,strong')].find(el => el.children.length === 0 && ok(el))
      || null;
  };

  // smallest block around the heading that holds more than the heading itself
  const sectionOf = title => {
    const h = findHeading(title);
    if (!h) return null;
    const own = clean(h.innerText).length;
    let el = h.parentElement;
    while (el && clean(el.innerText).length <= own + 20) el = el.parentElement;
    return el;
  };

  const pairs = root => {
    const res = [];
    if (!root) return res;
    for (const tr of root.querySelectorAll('tr')) {
      const cells = [...tr.querySelectorAll('th,td')].map(c => clean(c.innerText)).filter(Boolean);
      if (cells.length >= 2) res.push([cells[0], cells[1]]);
    }
    if (res.length) return res;
    for (const dt of root.querySelectorAll('dt')) {
      const dd = dt.nextElementSibling;
      if (dd && dd.tagName === 'DD') res.push([clean(dt.innerText), clean(dd.innerText)]);
    }
    if (res.length) return res;
    for (const row of root.querySelectorAll('div, li')) {
      const kids = [...row.children].filter(k => clean(k.innerText));
      if (kids.length !== 2) continue;
      const [key, val] = kids.map(k => clean(k.innerText));
      if (key.length > 80 || key.includes('\n') || val.includes('\n')) continue;
      res.push([key, val]);
    }
    return res;
  };

  const out = {};
  const product = ofType('Product');

  out.title = clean(document.querySelector('h1')?.innerText) || (product && product.name) || '';

  // breadcrumb
  let crumbs = [];
  const bl = ofType('BreadcrumbList');
  if (bl && bl.itemListElement) {
    crumbs = [...bl.itemListElement]
      .sort((a, b) => (a.position || 0) - (b.position || 0))
      .map(i => i.name || (i.item && i.item.name))
      .filter(Boolean);
  }
  if (!crumbs.length) {
    const nav = document.querySelector('nav[aria-label*="readcrumb" i], [class*="readcrumb" i]');
    if (nav) crumbs = [...nav.querySelectorAll('a, li > span')].map(a => clean(a.innerText)).filter(Boolean);
  }
  out.breadcrumb = [...new Set(crumbs)];

  // images
  const images = [];
  const addImg = src => { if (src && !src.startsWith('data:') && !images.includes(src)) images.push(src); };
  if (product && product.image) {
    [].concat(product.image).forEach(i => addImg(typeof i === 'string' ? i : (i.url || i.contentUrl)));
  }
  const titleStart = out.title.toLowerCase().slice(0, 20);
  for (const img of document.querySelectorAll('img')) {
    const alt = (img.alt || '').toLowerCase();
    if (titleStart && alt && alt.startsWith(titleStart)) addImg(img.currentSrc || img.src);
  }
  if (!images.length) addImg(document.querySelector('meta[property="og:image"]')?.content);
  out.images = images;

  // highlights, tech data, description
  const highlights = sectionOf('Highlights & Details');
  out.highlights = highlights ? clean(highlights.innerText).replace(/^Highlights & Details\s*/i, '') : '';
  out.tech_data = pairs(sectionOf('Technische Daten'));
  const description = sectionOf('Beschreibung');
  out.description_raw = description ? clean(description.innerText) : '';
  const notes = sectionOf('Hinweise');
  // "Hinweise" is its own block under the description - keep only what follows the heading
  out.notes_raw = '';
  if (notes) {
    const lines = clean(notes.innerText).split('\n');
    const start = lines.findIndex(l => l.trim().toLowerCase() === 'hinweise');
    const end = lines.findIndex(l => /^Wird oft zusammen gekauft/i.test(l));
    out.notes_raw = lines.slice(start + 1, end > start ? end : undefined).join('\n').trim();
  }

  // documents - mark the elements so python can click buttons that have no href
  out.documents = [];
  const docRoot = sectionOf('Dokumente & Downloads') || document.body;
  let i = 0;
  for (const el of docRoot.querySelectorAll('a, button')) {
    const text = clean(el.innerText);
    const href = el.tagName === 'A' ? el.href : '';
    if (!/download/i.test(text) && !/\.pdf(\?|$)/i.test(href)) continue;
    let row = el.parentElement;
    while (row && clean(row.innerText).length <= text.length + 3) row = row.parentElement;
    el.setAttribute('data-cp-doc', String(i));
    out.documents.push({
      index: i++,
      title: row ? clean(row.innerText).replace(/\s*Download\s*$/i, '').replace(/\n/g, ' ') : text,
      url: href,
    });
  }

  // buy box: climb up from the cart button until seller + stock are inside
  const cart = [...document.querySelectorAll('button, a')]
    .find(b => /in den (einkaufs)?wagen/i.test(clean(b.innerText)) && visible(b));
  let box = null;
  for (let el = cart, n = 0; el && n < 15; n++) {
    el = el.parentElement;
    const t = clean(el && el.innerText);
    if (/durch/.test(t) && /Stück/.test(t) && /Lieferung|lieferbar/i.test(t)) { box = el; break; }
  }
  out.buybox_text = box ? box.innerText : '';
  out.tier_rows = [];
  const tierTable = box && [...box.querySelectorAll('table')].find(t => /Menge/.test(t.innerText));
  if (tierTable) {
    out.tier_rows = [...tierTable.querySelectorAll('tr')]
      .map(tr => [...tr.querySelectorAll('th,td')].map(c => clean(c.innerText)));
  }

  out.page_text = document.body.innerText;
  out.ld_product = product;
  return out;
}
"""

OFFERS_JS = r"""
() => {
  const clean = s => (s || '').replace(/ /g, ' ').replace(/[ \t]+/g, ' ')
    .replace(/ *\n */g, '\n').replace(/\n{2,}/g, '\n').trim();
  const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));

  let root = [...document.querySelectorAll('[role="dialog"], [aria-modal="true"], dialog')]
    .filter(visible).find(d => /Angebot/.test(d.innerText));
  if (!root) {
    // no dialog role - take the smallest block that holds the "Alle Angebote" heading and the offers
    const heading = [...document.querySelectorAll('h1,h2,h3,h4,div,span,p')]
      .find(el => el.children.length === 0 && clean(el.innerText) === 'Alle Angebote' && visible(el));
    for (let el = heading; el; el = el.parentElement) {
      if ((el.innerText.match(/Versand durch/g) || []).length >= 1 && /\d,\d{2}\s*€/.test(el.innerText)) {
        root = el;
        break;
      }
    }
  }
  if (!root) return null;

  const cards = [];
  for (const radio of root.querySelectorAll('input[type="radio"], [role="radio"]')) {
    let el = radio.parentElement;
    while (el && el !== root && !/\d,\d{2}\s*€/.test(el.innerText)) el = el.parentElement;
    if (!el || el === root) continue;
    const text = clean(el.innerText);
    if (!cards.includes(text)) cards.push(text);
  }
  return { text: clean(root.innerText), cards };
}
"""

# "4 Angebote", "Alle 4 Angebote", "3 weitere Angebote", "Alle Angebote" - but not "Angebotsservice"
OFFERS_LINK = r"^\s*(alle\s+(\d+\s+)?angebote|\d+\s+(weitere\s+)?angebote?)(\s+anzeigen|\s+vergleichen)?\s*$"


def expand_sections(page: Page) -> None:
    pattern = re.compile(r"mehr anzeigen|alle anzeigen|weiterlesen|mehr lesen|alle technischen daten", re.I)
    buttons = page.get_by_role("button", name=pattern)
    for idx in range(min(buttons.count(), 6)):
        try:
            buttons.nth(idx).click(timeout=2000)
            page.wait_for_timeout(400)
        except Exception:
            pass


def read_offers(page: Page) -> list:
    url = page.url
    links = page.get_by_text(re.compile(OFFERS_LINK, re.I))
    clicked = False
    for idx in range(min(links.count(), 5)):
        try:
            if links.nth(idx).is_visible():
                links.nth(idx).click(timeout=5000)
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        return [], ""
    page.wait_for_timeout(2500)

    data = page.evaluate(OFFERS_JS)
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    if page.url != url:
        page.go_back()  # the link opened a separate offers page instead of a popup
    if not data:
        return [], ""

    cards = data["cards"] or parsing.split_offer_cards(data["text"])
    return [parsing.parse_offer(card) for card in cards], data["text"]


def resolve_document_urls(page: Page, documents: list) -> None:
    """Some 'Download' buttons are plain buttons - click once, take the url
    from the new tab (pdf opens in a tab) or from the download, then close it."""
    context = page.context
    for doc in documents:
        if doc["url"]:
            continue

        downloads = []
        page.once("download", lambda d: downloads.append(d))
        tabs_before = set(context.pages)
        try:
            page.locator(f'[data-cp-doc="{doc["index"]}"]').click(timeout=5000)
        except Exception:
            continue
        page.wait_for_timeout(3000)

        for tab in [t for t in context.pages if t not in tabs_before]:
            try:
                tab.wait_for_url(lambda u: u != "about:blank", timeout=10_000)
                doc["url"] = doc["url"] or tab.url
            except Exception:
                pass
            tab.close()

        if not doc["url"] and downloads:
            doc["url"] = downloads[0].url
            downloads[0].cancel()

        if not doc["url"]:
            log.debug("No url for document %s", doc["title"])


def save_raw_text(url: str, buybox: str, offers: str) -> None:
    """Keeps the raw buy box / offers text when something wasn't parsed - makes fixing easy."""
    folder = Path(config.OUTPUT_DIR) / "debug"
    folder.mkdir(parents=True, exist_ok=True)
    name = url.rstrip("/").split("-")[-1].replace(".html", "")
    (folder / f"{name}_raw.txt").write_text(
        f"=== BUYBOX ===\n{buybox}\n\n=== OFFERS ===\n{offers}\n", encoding="utf-8"
    )


def scrape_product(page: Page, url: str) -> dict:
    goto(page, url)
    page.locator("h1").first.wait_for(timeout=20_000)
    if ensure_business_prices(page):
        goto(page, url)
        page.locator("h1").first.wait_for(timeout=20_000)
    human_scroll(page)
    expand_sections(page)

    raw = page.evaluate(EXTRACT_JS)
    resolve_document_urls(page, raw["documents"])  # before the offers - they may reload the page
    offers, offers_text = read_offers(page)

    ids = parsing.parse_ids(raw["page_text"])
    ld = raw.get("ld_product") or {}
    ids["ean"] = ids["ean"] or str(ld.get("gtin13") or ld.get("gtin") or "")
    ids["manufacturer_part_no"] = ids["manufacturer_part_no"] or str(ld.get("mpn") or "")

    buybox = parsing.parse_buybox(raw["buybox_text"])
    tiers = parsing.parse_tier_rows(raw["tier_rows"]) or parsing.parse_tiers_from_text(raw["buybox_text"])
    if buybox["shipping"] is None:
        # conrad's own items show just "& Versand" in the buy box - the offer list has the amount
        same_seller = [o for o in offers if o["seller"] == buybox["shipped_by"] and o["shipping"] is not None]
        if same_seller:
            buybox["shipping"] = same_seller[0]["shipping"]

    if not tiers or not offers or not buybox["stock"]:
        save_raw_text(url, raw["buybox_text"], offers_text)

    description, notes = parsing.split_description(raw["description_raw"])
    notes = notes or raw["notes_raw"]

    documents = [{"title": d["title"], "url": d["url"]} for d in raw["documents"]]
    unique_docs = list({(d["url"] or d["title"]): d for d in documents}.values())

    return {
        "url": url,
        "title": raw["title"],
        "breadcrumb": raw["breadcrumb"],
        **ids,
        "images": raw["images"],
        **buybox,
        "price_tiers": tiers,
        "offers": offers,
        "documents": unique_docs,
        "highlights": raw["highlights"],
        "tech_data": dict(raw["tech_data"]),
        "description": description,
        "notes": notes,
    }
