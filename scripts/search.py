#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Поиск по каталогу Stroy-Materiali-24.

Работает с локальным снимком data/products.jsonl (без интернета).

Примеры:
    python scripts/search.py штукатурка гипсовая --limit 5
    python scripts/search.py --category "Фанера ФСФ" --max-price 2000
    python scripts/search.py --id 308723241
    python scripts/search.py --categories "Сухие смеси"
    python scripts/search.py --stats
    python scripts/search.py клей для плитки --json

Коды возврата: 0 — есть результаты, 1 — ничего не найдено, 2 — ошибка данных.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Синонимы и частые сокращения (приводятся к виду, который есть в названиях).
ALIASES = {
    "гкл": "гипсокартон",
    "гклв": "гипсокартон",
    "гвл": "гвл",
    "гвлв": "гвл",
    "осб": "осб",
    "osb": "осб",
    "эппс": "пеноплекс",
    "xps": "пеноплекс",
    "минвата": "роквол",
    "шпатлевка": "шпаклевка",
    "штукатурка": "штукатур",
    "стяжка": "стяжк",
    "свп": "система выравнивания",
}

WORD_RE = re.compile(r"[0-9a-zа-я]+")


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е").replace("osb", "осб")
    return " ".join(WORD_RE.findall(text))


def tokens(text: str) -> List[str]:
    return normalize(text).split()


def stem(token: str) -> str:
    """Грубый стеммер: для длинных слов достаточно первых символов."""
    if token in ALIASES:
        return ALIASES[token]
    if len(token) <= 4:
        return token
    return token[: max(4, len(token) - 2)]


def expand_query(query: str) -> List[str]:
    result: List[str] = []
    for raw in tokens(query):
        mapped = ALIASES.get(raw, raw)
        for part in mapped.split():
            if part not in result:
                result.append(part)
    # «12.5» распадается на «12» и «5»; одиночные цифры в таком запросе только шумят.
    numeric = [token for token in result if token.isdigit()]
    if len(numeric) >= 2:
        result = [token for token in result if not (token.isdigit() and len(token) == 1)]
    return result


def load_products() -> List[Dict[str, Any]]:
    path = DATA / "products.jsonl"
    if not path.exists():
        raise SystemExit(
            f"Нет файла {path}. Запустите: python scripts/sync.py"
        )
    items: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def load_json(name: str, default: Any = None) -> Any:
    path = DATA / name
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def money(value: Optional[float]) -> str:
    if value is None:
        return "цена по запросу"
    if float(value).is_integer():
        value = int(value)
    return f"{value:,}".replace(",", " ") + " ₽"


class Index:
    def __init__(self, products: Iterable[Dict[str, Any]]) -> None:
        self.products = list(products)
        self.rows: List[Tuple[Dict[str, Any], str, List[str]]] = []
        for item in self.products:
            haystack = normalize(
                " ".join(
                    [
                        item.get("name", ""),
                        item.get("category", ""),
                        item.get("description", ""),
                    ]
                )
            )
            self.rows.append((item, haystack, haystack.split()))

    @staticmethod
    def _match(stem: str, word: str) -> bool:
        if stem.isdigit():
            return word == stem
        return word.startswith(stem)

    def search(self, query: str, category: Optional[str] = None,
               min_price: Optional[float] = None, max_price: Optional[float] = None,
               sort: str = "relevance") -> List[Dict[str, Any]]:
        stems = [stem(token) for token in expand_query(query)] if query else []
        cat_stems = [stem(token) for token in tokens(category)] if category else []

        scored: List[Tuple[int, int, bool, Dict[str, Any]]] = []
        for item, _haystack, words in self.rows:
            if min_price is not None and (item.get("price") is None or item["price"] < min_price):
                continue
            if max_price is not None and (item.get("price") is None or item["price"] > max_price):
                continue

            name_words = tokens(item.get("name", ""))
            cat_words = tokens(item.get("category", ""))

            if cat_stems and not all(
                any(self._match(s, w) for w in cat_words) for s in cat_stems
            ):
                continue

            if not stems:
                scored.append((0, 0, True, item))
                continue

            name_hits = sum(
                1 for s in stems if any(self._match(s, w) for w in name_words)
            )
            cat_hits = sum(
                1 for s in stems if any(self._match(s, w) for w in cat_words)
            )
            any_hits = sum(1 for s in stems if any(self._match(s, w) for w in words))
            if not any_hits:
                continue

            score = name_hits * 6 + cat_hits * 2 + any_hits
            all_match = any_hits == len(stems)
            if all_match:
                score += 5 + name_hits * 2  # все слова запроса найдены
            scored.append((score, name_hits, all_match, item))

        if not scored:
            return []

        # Если есть позиции, где найдены все слова запроса, отбрасываем частичные совпадения.
        if any(flag for _score, _hits, flag, _item in scored):
            scored = [row for row in scored if row[2]]

        if sort == "price":
            scored.sort(key=lambda x: (x[3].get("price") is None, x[3].get("price") or 0))
        elif sort == "name":
            scored.sort(key=lambda x: x[3].get("name", ""))
        else:
            scored.sort(
                key=lambda x: (
                    -x[0],
                    -x[1],
                    x[3].get("price") is None,
                    x[3].get("price") or 0,
                )
            )
        return [item for _score, _hits, _flag, item in scored]

    def by_id(self, product_id: str) -> Optional[Dict[str, Any]]:
        for item in self.products:
            if str(item.get("id")) == str(product_id):
                return item
        return None


def print_item(item: Dict[str, Any], position: int) -> None:
    print(f"{position}. {item['name']} — {money(item.get('price'))}")
    print(f"   {item.get('category') or 'без категории'}")
    if item.get("weight"):
        print(f"   Вес: {item['weight']:g} кг")
    print(f"   {item.get('url')}")


def describe(item: Dict[str, Any]) -> None:
    print(item["name"])
    print(f"Цена: {money(item.get('price'))} / {item.get('currency', 'RUB')}")
    print(f"Категория: {item.get('category') or '—'}")
    print(f"Ссылка: {item.get('url')}")
    print(f"ID: {item.get('id')}   В наличии (по фиду): {item.get('available')}")
    if item.get("weight"):
        print(f"Вес: {item['weight']:g} кг")
    if item.get("description"):
        print()
        print(item["description"])


def show_categories(query: Optional[str]) -> int:
    data = load_json("categories.json") or {}
    tree = data.get("tree") or []
    stems = [stem(token) for token in tokens(query)] if query else []
    rows: List[Tuple[int, Dict[str, Any]]] = []

    def walk(nodes: List[Dict[str, Any]], depth: int) -> None:
        for node in nodes:
            if stems:
                hay = tokens(node["path"])
                if not all(any(w.startswith(s) for w in hay) for s in stems):
                    walk(node.get("children") or [], depth)
                    continue
            rows.append((depth, node))
            walk(node.get("children") or [], depth + 1)

    walk(tree, 0)
    if not rows:
        print("Категории не найдены.")
        return 1
    for depth, node in rows:
        prices = ""
        if node.get("product_count") and node.get("price_min") is not None:
            lo, hi = node["price_min"], node["price_max"]
            prices = f", {money(lo)}" if lo == hi else f", {money(lo)} – {money(hi)}"
        print(f"{'  ' * depth}{node['name']} — {node['product_count']} тов.{prices}")
    return 0


def show_stats() -> int:
    meta = load_json("meta.json") or {}
    if not meta:
        print("Нет data/meta.json. Запустите: python scripts/sync.py")
        return 2
    print(f"Магазин: {meta.get('shop')} ({meta.get('site')})")
    print(f"Источник: {meta.get('source')}")
    print(f"Дата фида: {(meta.get('feed_date') or '—')[:10]}")
    print(f"Товаров: {meta.get('offers')}, категорий: {meta.get('categories')}")
    print(f"Цены (без доставки): {money(meta.get('price_min'))} – {money(meta.get('price_max'))}")
    print(f"Доступно по фиду: {meta.get('available_true')}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Поиск по каталогу Stroy-Materiali-24",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("query", nargs="*", help="поисковый запрос (название, бренд, размер)")
    parser.add_argument("--category", "-c", help="фильтр по категории (частичное совпадение)")
    parser.add_argument("--min-price", type=float, help="минимальная цена, ₽")
    parser.add_argument("--max-price", type=float, help="максимальная цена, ₽")
    parser.add_argument("--limit", "-n", type=int, default=10, help="сколько позиций показать (по умолчанию 10)")
    parser.add_argument("--sort", choices=["relevance", "price", "name"], default="relevance")
    parser.add_argument("--json", action="store_true", help="вывести результат в JSON")
    parser.add_argument("--id", help="показать карточку товара по ID")
    parser.add_argument("--categories", nargs="?", const="", metavar="ЗАПРОС",
                        help="показать дерево категорий (можно с фильтром)")
    parser.add_argument("--stats", action="store_true", help="сводка по каталогу")
    args = parser.parse_args(argv)

    if args.stats:
        return show_stats()

    if args.categories is not None:
        return show_categories(args.categories or None)

    products = load_products()
    index = Index(products)

    if args.id:
        item = index.by_id(args.id)
        if not item:
            print(f"Товар с ID {args.id} не найден.", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(item, ensure_ascii=False, indent=2))
        else:
            describe(item)
        return 0

    query = " ".join(args.query).strip()
    if not query and not args.category and args.min_price is None and args.max_price is None:
        parser.print_help()
        return 2

    results = index.search(
        query,
        category=args.category,
        min_price=args.min_price,
        max_price=args.max_price,
        sort=args.sort,
    )

    if not results:
        print(f"Ничего не найдено по запросу: {query or '(фильтры)'}", file=sys.stderr)
        print("Попробуйте более общий запрос или посмотрите references/catalog.md.", file=sys.stderr)
        return 1

    limit = max(1, args.limit)
    shown = results[:limit]
    if args.json:
        print(json.dumps(shown, ensure_ascii=False, indent=2))
        return 0

    print(f"Найдено: {len(results)}. Показаны {len(shown)}.")
    print()
    for position, item in enumerate(shown, start=1):
        print_item(item, position)
    if len(results) > limit:
        print()
        print(f"…ещё {len(results) - limit}. Увеличьте --limit или уточните запрос.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
