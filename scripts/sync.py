#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Синхронизация каталога Stroy-Materiali-24 из YML-фида.

Скачивает https://stroy-materiali-24.ru/marketplace/101282.xml, пересобирает
data/products.jsonl, data/categories.json, data/meta.json и
references/catalog.md.

Использование:
    python scripts/sync.py                 # скачать свежий фид и обновить всё
    python scripts/sync.py --file feed.xml # взять локальный файл
    python scripts/sync.py --check         # только проверить доступность фида

Зависимости: только стандартная библиотека Python 3.8+.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = "https://stroy-materiali-24.ru/marketplace/101282.xml"
UA = "Mozilla/5.0 (compatible; ST24-catalog-sync/1.0; +https://stroy-materiali-24.ru)"


# --------------------------------------------------------------------------
# Загрузка и разбор
# --------------------------------------------------------------------------
def fetch(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def text(node: Optional[ET.Element]) -> str:
    if node is None or node.text is None:
        return ""
    return re.sub(r"\s+", " ", node.text).strip()


def parse_offer(offer: ET.Element, cats: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    category_id = text(offer.find("categoryId"))
    price_raw = text(offer.find("price"))
    try:
        price: Optional[float] = float(price_raw) if price_raw else None
    except ValueError:
        price = None

    weight_raw = text(offer.find("weight"))
    try:
        weight = float(weight_raw) if weight_raw else None
    except ValueError:
        weight = None

    def flag(name: str) -> Optional[bool]:
        value = offer.find(name)
        if value is None:
            return None
        return (value.text or "").strip().lower() in ("true", "1", "yes")

    available_raw = (offer.get("available") or "").strip().lower()
    available = True if available_raw in ("true", "1") else False if available_raw in ("false", "0") else None

    return {
        "id": offer.get("id", ""),
        "name": text(offer.find("name")),
        "price": int(price) if price is not None and price.is_integer() else price,
        "currency": text(offer.find("currencyId")) or "RUB",
        "category_id": category_id,
        "category": category_path(category_id, cats),
        "url": text(offer.find("url")),
        "description": text(offer.find("description")),
        "weight": weight,
        "dimensions": text(offer.find("dimensions")) or None,
        "pictures": [p.text.strip() for p in offer.findall("picture") if p.text],
        "available": available,
        "delivery": flag("delivery"),
        "pickup": flag("pickup"),
        "store": flag("store"),
    }


def category_path(category_id: str, cats: Dict[str, Dict[str, Any]]) -> str:
    parts: List[str] = []
    seen = set()
    current = category_id
    while current and current in cats and current not in seen:
        seen.add(current)
        parts.append(cats[current]["name"])
        current = cats[current]["parent_id"]
    return " / ".join(reversed(parts))


def build_categories(shop: ET.Element) -> Dict[str, Dict[str, Any]]:
    cats: Dict[str, Dict[str, Any]] = {}
    node = shop.find("categories")
    if node is None:
        return cats
    for category in node.findall("category"):
        cid = category.get("id", "")
        cats[cid] = {
            "id": cid,
            "name": text(category),
            "parent_id": category.get("parentId") or "",
        }
    return cats


def walk_category(cid: str, cats: Dict[str, Dict[str, Any]], stats: Dict[str, Dict[str, Any]],
                  children: Dict[str, List[str]], depth: int = 0) -> Dict[str, Any]:
    node_stats = stats.get(cid, {"direct": 0, "total": 0, "min": None, "max": None})
    result = {
        "id": cid,
        "name": cats[cid]["name"],
        "parent_id": cats[cid]["parent_id"],
        "path": category_path(cid, cats),
        "direct_count": node_stats["direct"],
        "product_count": node_stats["total"],
        "price_min": node_stats["min"],
        "price_max": node_stats["max"],
        "children": [],
    }
    for child in children.get(cid, []):
        result["children"].append(walk_category(child, cats, stats, children, depth + 1))
    return result


# --------------------------------------------------------------------------
# Запись данных
# --------------------------------------------------------------------------
def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            fh.write("\n")


def as_price(value: Optional[float]) -> str:
    if value is None:
        return "—"
    value = int(value) if float(value).is_integer() else value
    return f"{value:,}".replace(",", " ") + " ₽"


def render_catalog(meta: Dict[str, Any], tree: List[Dict[str, Any]],
                   products: List[Dict[str, Any]]) -> str:
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for item in products:
        by_cat.setdefault(item["category_id"], []).append(item)

    updated = meta["feed_date"][:10] if meta.get("feed_date") else meta["generated_at"][:10]
    lines: List[str] = [
        "# Каталог Stroy-Materiali-24 (снимок фида)",
        "",
        f"Дата фида: **{updated}**. Товаров: **{meta['offers']}**. Категорий: **{meta['categories']}**. "
        f"Цены: {as_price(meta['price_min'])} – {as_price(meta['price_max'])}. Валюта: RUB.",
        "",
        "Это карта ассортимента для навигации. Точные позиции и актуальные цены ищите",
        "через `scripts/search.py` (полные данные — `data/products.jsonl`), а этот файл",
        "используйте, чтобы понять, в какой категории искать и что в ней есть.",
        "",
        "Обозначения: `N` — число товаров в категории с подкатегориями, `напр.` — примеры",
        "позиций с ценой снимка, `—` — категория без собственных товаров.",
        "",
        "## Дерево категорий",
        "",
    ]

    def render_node(node: Dict[str, Any], depth: int) -> None:
        count = node["product_count"]
        price = ""
        if count:
            lo, hi = node["price_min"], node["price_max"]
            price = f", {as_price(lo)}" if lo == hi else f", {as_price(lo)} – {as_price(hi)}"
        lines.append(f"{'  ' * depth}- **{node['name']}** — {count} тов.{price}")
        direct = node.get("_direct")
        if direct and not node["children"]:
            examples = sorted(direct, key=lambda x: (x["price"] is None, x["price"] or 0))[:2]
            if examples:
                sample = "; ".join(
                    f"{item['name']} — {as_price(item['price'])}" for item in examples
                )
                lines.append(f"{'  ' * (depth + 1)}- напр. {sample}")
        for child in node["children"]:
            render_node(child, depth + 1)

    def annotate(node: Dict[str, Any]) -> None:
        node["_direct"] = by_cat.get(node["id"], [])
        for child in node["children"]:
            annotate(child)

    for root in tree:
        annotate(root)
        render_node(root, 0)

    flat: List[Dict[str, Any]] = []

    def flatten(node: Dict[str, Any]) -> None:
        flat.append(node)
        for child in node["children"]:
            flatten(child)

    for root in tree:
        flatten(root)

    top = sorted((node for node in flat if node["product_count"]), key=lambda n: n["product_count"],
                 reverse=True)[:10]
    lines += [
        "",
        "## Самые крупные разделы",
        "",
    ]
    for node in top:
        lines.append(f"- {node['path']} — {node['product_count']} тов.")
    lines.append("")

    # Товары, лежащие непосредственно в корне «Все товары» (без подкатегории)
    loose = sorted(by_cat.get("21374777", []), key=lambda x: x["name"])
    if loose:
        lines += [
            "## Товары вне подкатегорий (в корне «Все товары»)",
            "",
        ]
        for item in loose:
            lines.append(f"- {item['name']} — {as_price(item['price'])}")
        lines.append("")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Основной сценарий
# --------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Обновить каталог Stroy-Materiali-24 из YML-фида")
    parser.add_argument("--url", default=DEFAULT_URL, help="URL YML-фида")
    parser.add_argument("--file", help="локальный XML вместо скачивания")
    parser.add_argument("--check", action="store_true", help="только проверить доступность фида")
    parser.add_argument("--out", default=str(ROOT), help="корень проекта")
    args = parser.parse_args(argv)

    out = Path(args.out).resolve()
    if args.file:
        raw = Path(args.file).read_bytes()
        source = DEFAULT_URL
        source_note = f"локальный файл {Path(args.file).resolve()}"
    else:
        try:
            raw = fetch(args.url)
        except Exception as exc:  # noqa: BLE001
            print(f"Не удалось скачать фид: {exc}", file=sys.stderr)
            return 1
        source = args.url
        source_note = "скачано из сети"

    if args.check:
        print(f"Фид доступен: {len(raw):,} байт".replace(",", " "))
        return 0

    root = ET.fromstring(raw)
    shop = root.find("shop")
    if shop is None:
        print("В фиде нет элемента shop", file=sys.stderr)
        return 1

    cats = build_categories(shop)
    offers_node = shop.find("offers")
    offers = offers_node.findall("offer") if offers_node is not None else []
    products = [parse_offer(offer, cats) for offer in offers]
    products.sort(key=lambda item: (item["category"], item["name"]))

    # Статистика по категориям (свои товары + суммарно с детьми)
    direct_stats: Dict[str, Dict[str, Any]] = {}
    for item in products:
        cid = item["category_id"]
        entry = direct_stats.setdefault(cid, {"count": 0, "min": None, "max": None})
        entry["count"] += 1
        if item["price"] is not None and item["category"] != "Все товары / Доставка":
            entry["min"] = item["price"] if entry["min"] is None else min(entry["min"], item["price"])
            entry["max"] = item["price"] if entry["max"] is None else max(entry["max"], item["price"])

    stats: Dict[str, Dict[str, Any]] = {
        cid: {"direct": entry["count"], "total": 0, "min": entry["min"], "max": entry["max"]}
        for cid, entry in direct_stats.items()
    }
    children: Dict[str, List[str]] = {}
    for cid, cat in cats.items():
        children.setdefault(cat["parent_id"], []).append(cid)

    def totalize(cid: str) -> Dict[str, Any]:
        own = stats.get(cid, {"direct": 0, "total": 0, "min": None, "max": None})
        own["total"] = own["direct"]
        for child in children.get(cid, []):
            child_stats = totalize(child)
            own["total"] += child_stats["total"]
            for key in ("min", "max"):
                if child_stats[key] is not None:
                    own[key] = child_stats[key] if own[key] is None else (
                        min(own[key], child_stats[key]) if key == "min" else max(own[key], child_stats[key])
                    )
        stats[cid] = own
        return own

    roots = [cid for cid, cat in cats.items() if not cat["parent_id"] or cat["parent_id"] not in cats]
    for cid in sorted(roots, key=lambda c: cats[c]["name"]):
        totalize(cid)

    tree = [walk_category(cid, cats, stats, children) for cid in sorted(roots, key=lambda c: cats[c]["name"])]

    prices = [item["price"] for item in products if item["price"] is not None]
    goods_prices = [
        item["price"] for item in products
        if item["price"] is not None and item["category"] != "Все товары / Доставка"
    ]
    feed_date = root.get("date") or ""
    meta = {
        "shop": text(shop.find("name")) or "Stroy-Materiali-24",
        "legal_name": text(shop.find("company")),
        "site": text(shop.find("url")),
        "platform": text(shop.find("platform")),
        "currency": "RUB",
        "source": source,
        "source_note": source_note,
        "feed_date": feed_date,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "offers": len(products),
        "categories": len(cats),
        "price_min": min(goods_prices) if goods_prices else None,
        "price_max": max(goods_prices) if goods_prices else None,
        "available_true": sum(1 for item in products if item["available"]),
        "images": sum(len(item["pictures"]) for item in products),
    }

    write_jsonl(out / "data" / "products.jsonl", products)
    write_json(out / "data" / "categories.json", {
        "generated_at": meta["generated_at"],
        "feed_date": feed_date,
        "count": len(cats),
        "tree": tree,
    })
    write_json(out / "data" / "meta.json", meta)

    catalog_path = out / "references" / "catalog.md"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(render_catalog(meta, tree, products), encoding="utf-8", newline="\n")

    print(
        f"Готово: {len(products)} товаров, {len(cats)} категорий, "
        f"{len(prices)} цен. Фиксация: {feed_date or 'без даты'}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
