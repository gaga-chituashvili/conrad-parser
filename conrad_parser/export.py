import csv
import json
from pathlib import Path

BASE_COLUMNS = [
    "url", "order_no", "ean", "manufacturer_part_no", "title", "breadcrumb",
    "delivery", "stock", "sold_by", "shipped_by", "unit_price", "total_price", "old_price",
    "shipping", "vat_note", "price_tiers", "offers", "datasheet_url",
    "safety_info_url", "documents", "images", "highlights", "description", "notes",
]


def load_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def append_jsonl(path: Path, item: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")


def find_doc(documents: list, word: str) -> str:
    for doc in documents:
        if doc["title"].lower().startswith(word.lower()) and doc["url"]:
            return doc["url"]
    return ""


def to_row(item: dict) -> dict:
    row = {key: item.get(key, "") for key in BASE_COLUMNS}
    row["breadcrumb"] = " > ".join(item.get("breadcrumb", []))
    row["images"] = " | ".join(item.get("images", []))
    row["datasheet_url"] = find_doc(item.get("documents", []), "Datenblatt")
    row["safety_info_url"] = find_doc(item.get("documents", []), "Bedienungs")
    for key in ("price_tiers", "offers", "documents"):
        row[key] = json.dumps(item.get(key, []), ensure_ascii=False)

    # every technical parameter gets its own column
    for name, value in item.get("tech_data", {}).items():
        row[f"tech: {name}"] = value
    return row


def write_csv(items: list, path: Path) -> None:
    rows = [to_row(item) for item in items]

    columns = list(BASE_COLUMNS)
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)

    # utf-8-sig so Excel shows umlauts correctly
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
