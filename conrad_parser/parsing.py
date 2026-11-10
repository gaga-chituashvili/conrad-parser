"""Plain-text parsing helpers. Kept separate from Playwright so they are easy to test."""
import re

PRICE_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*€")


def to_float(value):
    """'1.234,56' -> 1234.56"""
    if not value:
        return None
    try:
        return float(value.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def first(pattern, text, group=1, flags=re.I):
    match = re.search(pattern, text or "", flags)
    return match.group(group).strip() if match else ""


def split_prices(text):
    """Returns (item prices, shipping price) - '4,95 € Versand' is shipping."""
    prices, shipping = [], ""
    for match in PRICE_RE.finditer(text or ""):
        after = text[match.end():match.end() + 15].lstrip().lower()
        if after.startswith("versand"):
            shipping = shipping or match.group(1)
        else:
            prices.append(match.group(1))
    return prices, shipping


def parse_ids(text):
    return {
        "ean": first(r"EAN:?\s*(\d{8,14})", text),
        "manufacturer_part_no": first(r"Hst\.?\s*-?\s*Teile-?\s*Nr\.?:?\s*([^\s|]+)", text),
        "order_no": first(r"Bestell-?\s*Nr\.?:?\s*(\d[\d ]*(?:-\s*\d+)?)", text),
    }


def parse_delivery(text):
    return first(r"((?:Sofort-)?Lieferung[^\n]*)", text)


def parse_stock(text):
    return first(r"(\d[\d.]*)\s*Stück\b", text)


def parse_tier_rows(rows):
    """rows from the 'Menge (Stück) / Ersparnis / Verpackungspreis' table."""
    tiers = []
    for cells in rows:
        cells = [c.strip() for c in cells if c and c.strip()]
        if not cells or not cells[0].isdigit():
            continue
        prices, _ = split_prices(" ".join(cells))
        if not prices:
            continue
        saving = cells[1] if len(cells) > 2 else ""
        tiers.append({
            "qty": int(cells[0]),
            "saving": "" if saving == "-" else saving,
            "price": to_float(prices[-1]),
        })
    return tiers


def parse_tiers_from_text(text):
    """Fallback for the tier table when it's not a real <table>.
    Handles both one row per line ('5  9% = 11,85 €  22,44 €') and
    one cell per line ('5' / '9% = 11,85 €' / '22,44 €')."""
    lines = [l.replace("\xa0", " ").strip() for l in (text or "").splitlines()]
    start = next((i for i, l in enumerate(lines) if l.startswith("Menge")), None)
    if start is None:
        return []

    rows, current = [], None
    for line in lines[start + 1:]:
        if not line or line.startswith(("Ersparnis", "Verpackungspreis", "(zzgl", "(inkl")):
            continue
        if line.startswith(("Mengenrabatt", "Anzahl", "Gesamt")):
            break
        same_line = re.match(r"^(\d+)\s+(.*?)\s*(\d{1,3}(?:\.\d{3})*,\d{2}\s*€)$", line)
        if same_line:
            rows.append(list(same_line.groups()))
            current = None
        elif re.fullmatch(r"\d+", line):
            current = [line]
        elif current is not None and re.fullmatch(r"\d{1,3}(?:\.\d{3})*,\d{2}\s*€", line):
            rows.append(current + [line] if len(current) > 1 else [current[0], "", line])
            current = None
        elif current is not None:
            current.append(line)
    return parse_tier_rows(rows)


PRICE = r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*€"
OFFER_LABELS = ("Beste Wahl", "Unser Angebot")


def parse_buybox(text):
    text = text or ""
    prices, shipping = split_prices(text)
    # "Gesamt (24,36 € / Stück) 24,36 €", "Gesamt 62,54 €" or with a discount
    # "Gesamt (57,13 € / Stück) 60,50 € -6 % 57,13 €" - the current total is the last price
    per_piece = first(r"Gesamt\s*\(\s*" + PRICE, text)
    total_block = re.search(r"Gesamt(?:\s*\([^)]*\))?(.*?)(?:zzgl|inkl)", text, re.S)
    total_prices = PRICE_RE.findall(total_block.group(1)) if total_block else []
    total = total_prices[-1] if total_prices else ""
    old_price = total_prices[0] if len(total_prices) > 1 else ""

    return {
        "delivery": parse_delivery(text),
        "stock": parse_stock(text),
        "sold_by": first(r"Verkauf(?: und Versand)? durch\s*([^\n]+)", text),
        "shipped_by": first(r"Versand durch\s*([^\n]+)", text),
        "unit_price": to_float(per_piece or total) or (to_float(prices[-1]) if prices else None),
        "total_price": to_float(total),
        "old_price": to_float(old_price),
        "shipping": to_float(shipping),
        "vat_note": first(r"(zzgl\.\s*MwSt\.?|inkl\.\s*MwSt\.?)", text),
    }


def parse_offer(text):
    prices, shipping = split_prices(text)
    head = text.split("Versand durch")[0]
    return {
        "label": next((name for name in OFFER_LABELS if name in head), ""),
        "seller": first(r"Versand durch\s*([^\n]+)", text),
        "delivery": parse_delivery(text),
        "stock": parse_stock(text),
        # a discounted offer shows the old price first - the current one is the last
        "price": to_float(prices[-1]) if prices else None,
        "old_price": to_float(prices[0]) if len(prices) > 1 else None,
        "shipping": to_float(shipping),
        "min_qty": first(r"Min\.?:?\s*(\d+)", text),
        "multiple_of": first(r"Vielfache:?\s*(\d+)", text),
    }


def split_offer_cards(text):
    """Fallback when the cards can't be found in the DOM: every offer starts with
    'Versand durch', an optional label ('Beste Wahl') sits on the line before it."""
    cards, current, label = [], None, ""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped in OFFER_LABELS:
            label = stripped
            continue
        if stripped.startswith("Versand durch"):
            if current:
                cards.append("\n".join(current))
            current = ([label] if label else []) + [stripped]
            label = ""
        elif current is not None:
            current.append(stripped)
    if current:
        cards.append("\n".join(current))
    return [c for c in cards if PRICE_RE.search(c)]


def split_description(text):
    """'Beschreibung' block -> (description, notes under 'Hinweise')."""
    text = re.sub(r"^\s*Beschreibung\s*", "", text or "")
    parts = re.split(r"\n\s*Hinweise\s*\n", text, maxsplit=1)
    description = parts[0].strip()
    notes = parts[1].strip() if len(parts) > 1 else ""
    return description, notes
