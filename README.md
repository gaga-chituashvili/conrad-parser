# conrad.de parser

Collects product data for Balluff products from conrad.de while logged in to a customer account.
Python + Playwright.

## Run

Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env             # put your conrad.de email and password here
python main.py
```

The browser opens in a visible window. On the first run the script logs in; if conrad shows a captcha
or the login form changes, it waits - just log in in that window and press Enter in the terminal.
The session is saved in `.browser_profile/`, so the next runs start already logged in.

Price tiers, other sellers' offers and the shipping cost are shown by conrad only in the business
view (prices zzgl. MwSt.). The script switches to it with the VAT switch in the header; if your
account can't switch, it prints a warning - then use a business account (Geschäftskunde).

Results:

- `output/balluff_products.csv` - final CSV (one row per product)
- `output/products.jsonl` - raw data, one product per line
- `output/product_urls.txt` - collected product links
- `output/debug/` - html + screenshot of pages that failed

Useful options:

```bash
python main.py --limit 10              # quick test on 10 products
python main.py --login-only            # only log in and save the session
python main.py --urls-file urls.txt    # parse your own list of product urls
python main.py --chrome                # use installed Google Chrome instead of Chromium
```

If the run stops (network, block, Ctrl+C), just start it again - products already in
`products.jsonl` are skipped.

## CSV columns

| column | what it is |
|---|---|
| url, title, breadcrumb | product page, name, category path |
| order_no, ean, manufacturer_part_no | Bestell-Nr., EAN, Hst.-Teile-Nr. |
| images | image urls, separated by ` \| ` |
| delivery, stock | e.g. "Sofort-Lieferung Do., 29. Okt.", "72" |
| sold_by, shipped_by | seller info from the buy box |
| unit_price, total_price, old_price, shipping, vat_note | price for 1 pc, total, crossed-out price if discounted, shipping cost, "zzgl. MwSt." |
| price_tiers | JSON: quantity / saving / price |
| offers | JSON: all offers from the "Alle Angebote" window (seller, delivery, stock, price, shipping, min qty) |
| datasheet_url, safety_info_url, documents | Datenblatt, Bedienungs- und Sicherheitshinweise, all documents as JSON |
| highlights, description, notes | "Highlights & Details", "Beschreibung", "Hinweise" |
| tech: ... | one column per row of "Technische Daten" |

Prices are stored as numbers with a dot (24.81).

## How it works

**Login.** I use Playwright with a persistent browser profile. The script opens the account page and checks
if the session is still valid. If not, it fills in email and password from `.env` (typed with a small delay,
like a person) and checks again. If something blocks the automatic login (captcha, 2FA), it falls back to a
manual login in the same window. After that the cookies stay in the profile, so all the parsing runs
under the logged-in account.

**Data collection.** First the script searches for "balluff" and goes through the result pages, collecting
product links whose slug starts with `balluff-` (filters out other brands' accessories). Then it opens each
product, scrolls the page so the lazy blocks load, expands "mehr anzeigen" buttons and reads the data with one
JS function in the page. I didn't rely on CSS classes - they are generated and change often. Instead, sections
are found by their visible headings ("Technische Daten", "Beschreibung", "Dokumente & Downloads"), and values
like EAN or Bestell-Nr. by their labels. JSON-LD is used as a backup for EAN, MPN and images. Before parsing, the page is switched to the business view, because only there conrad shows price
tiers and marketplace offers. The "Alle Angebote" window is opened to get every seller's offer. Each product is written to `products.jsonl` right away, so a crash
doesn't lose data, and the CSV is built from it at the end. There are random pauses between pages to keep the
load low.
