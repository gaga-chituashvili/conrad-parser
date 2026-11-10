import argparse
import logging
from pathlib import Path

from playwright.sync_api import sync_playwright

from conrad_parser import config
from conrad_parser.auth import ensure_login
from conrad_parser.browser import BlockedError, open_browser, pause
from conrad_parser.collector import collect_product_urls
from conrad_parser.export import append_jsonl, load_jsonl, write_csv
from conrad_parser.product import scrape_product

log = logging.getLogger("conrad")


def parse_args():
    parser = argparse.ArgumentParser(description="conrad.de product parser")
    parser.add_argument("--brand", default="balluff")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--urls-file", help="txt file with product urls, one per line (skips the search step)")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--chrome", action="store_true", help="use installed Google Chrome instead of Chromium")
    parser.add_argument("--login-only", action="store_true", help="just log in and save the session")
    parser.add_argument("--retries", type=int, default=2)
    return parser.parse_args()


def save_debug(page, out_dir: Path, name: str) -> None:
    debug_dir = out_dir / "debug"
    debug_dir.mkdir(exist_ok=True)
    try:
        (debug_dir / f"{name}.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(debug_dir / f"{name}.png"), full_page=True)
    except Exception:
        pass


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    out_dir = Path(config.OUTPUT_DIR)
    out_dir.mkdir(exist_ok=True)
    jsonl_path = out_dir / "products.jsonl"
    urls_path = out_dir / "product_urls.txt"

    with sync_playwright() as pw:
        context = open_browser(pw, headless=args.headless, use_chrome=args.chrome)
        page = context.pages[0] if context.pages else context.new_page()

        try:
            ensure_login(page)
            if args.login_only:
                return

            if args.urls_file:
                urls = [u.strip() for u in Path(args.urls_file).read_text().splitlines() if u.strip()]
            elif urls_path.exists():
                urls = urls_path.read_text().split()
                log.info("Using %d urls from %s", len(urls), urls_path)
            else:
                urls = collect_product_urls(page, args.brand, args.limit)
                urls_path.write_text("\n".join(urls))
            urls = urls[:args.limit]

            # resume: skip what's already in products.jsonl
            done = {item["url"] for item in load_jsonl(jsonl_path)}
            todo = [u for u in urls if u not in done]
            log.info("%d urls total, %d already done, %d to go", len(urls), len(done), len(todo))

            for n, url in enumerate(todo, 1):
                for attempt in range(1, args.retries + 2):
                    try:
                        item = scrape_product(page, url)
                        append_jsonl(jsonl_path, item)
                        log.info("[%d/%d] %s - %s EUR", n, len(todo), item["title"][:60], item["unit_price"])
                        break
                    except BlockedError:
                        raise
                    except Exception as exc:
                        log.warning("Attempt %d failed for %s: %s", attempt, url, exc)
                        save_debug(page, out_dir, url.rstrip("/").split("/")[-1].replace(".html", ""))
                        pause(5, 10)
                pause()
        except BlockedError as exc:
            log.error("%s. Wait a while and run again - it continues where it stopped.", exc)
        finally:
            context.close()

    items = load_jsonl(jsonl_path)
    csv_path = out_dir / "balluff_products.csv"
    write_csv(items, csv_path)
    log.info("Saved %d products to %s", len(items), csv_path)


if __name__ == "__main__":
    main()
