import json
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

from .extractor import Extractor
from .utils.log import logger


@dataclass
class StoreConfig:
    id: str
    adapter: str
    raw: dict


class StoreScraper(Protocol):
    store_id: str

    def search(self, keyword: str) -> list[dict]: ...


class BaseScraper:
    def __init__(self, store_cfg: StoreConfig, extractor: Extractor):
        self.store_id = store_cfg.id
        self.cfg = store_cfg.raw
        self.extractor = extractor
        self.logger = logger


class AtacadaoScraper(BaseScraper):
    def search(self, keyword: str) -> list[dict]:
        url = self._build_url(keyword)
        data = self.extractor.make_request(url=url, mode="json")
        return self._parse_products(data)

    def _build_url(self, keyword: str) -> str:
        variables = {
            "first": 100,
            "after": "0",
            "sort": "score_desc",
            "term": keyword,
            "selectedFacets": [
                {"key": "region-id", "value": self.cfg["region_id"]},
                {
                    "key": "channel",
                    "value": json.dumps(
                        {
                            "salesChannel": self.cfg["sales_channel"],
                            "seller": self.cfg["seller"],
                            "regionId": self.cfg["region_id"],
                        }
                    ),
                },
                {"key": "locale", "value": self.cfg["locale"]},
            ],
        }

        params = {
            "operationName": self.cfg["operation"],
            "variables": json.dumps(variables, ensure_ascii=False),
        }
        return f"{self.cfg['search_url']}?{urlencode(params)}"

    def _parse_products(self, data: dict | None) -> list[dict]:
        if not data:
            return []

        products = []
        edges = (
            data.get("data", {}).get("search", {}).get("products", {}).get("edges", [])
        )

        for edge in edges:
            node = edge.get("node", {})
            breadcrumb = (node.get("breadcrumbList") or {}).get("itemListElement", [])
            category = breadcrumb[0]["name"] if len(breadcrumb) > 0 else None
            sub_category = breadcrumb[1]["name"] if len(breadcrumb) > 1 else None

            products.append(
                {
                    "store_id": self.store_id,
                    "sku": node.get("sku"),
                    "category": category,
                    "sub_category": sub_category,
                    "product_name": node.get("name"),
                    "brand_name": (node.get("brand") or {}).get("brandName"),
                    "high_price": (node.get("offers") or {}).get("highPrice"),
                    "low_price": (node.get("offers") or {}).get("lowPrice"),
                }
            )

        return products


class CarrefourScraper(BaseScraper):
    def search(self, keyword: str) -> list[dict]:
        search_page = self._build_search_page(keyword)
        html = self._warm_up_session(search_page)

        std_items = self._extract_std_items_from_html(html)
        if std_items:
            return self._parse_std_items(std_items)
        self._debug_html(html, search_page)

        endpoint = f"{self.cfg.get('api_base_url', self.cfg['base_url'])}{self.cfg['search_endpoint']}"
        headers = self._build_headers(search_page)
        data = {"distributor": self.cfg["distributor"], "url": search_page}
        if self.cfg.get("hash"):
            data["hash"] = self.cfg["hash"]

        response = self.extractor.make_request(
            url=endpoint,
            method="POST",
            headers=headers,
            data=data,
            mode="auto",
        )
        response = self._ensure_json(response)
        if not response:
            query = urlencode(data)
            fallback_url = f"{endpoint}?{query}"
            self.logger.info("Carrefour fallback to GET on catchtagSearch")
            response = self.extractor.make_request(
                url=fallback_url,
                method="GET",
                headers=headers,
                mode="text",
            )
            response = self._ensure_json(response)
        return self._parse_products(response)

    def _build_search_page(self, keyword: str) -> str:
        template = self.cfg.get("search_page_template", "/busca/{keyword}")
        return f"{self.cfg['base_url']}{template.format(keyword=keyword)}"

    def _warm_up_session(self, search_page: str) -> str:
        self.extractor.make_request(
            url=self.cfg["base_url"],
            method="GET",
            mode="text",
        )
        html = self.extractor.make_request(
            url=search_page,
            method="GET",
            mode="text",
        )
        return html or ""

    def _extract_std_items_from_html(self, html: str) -> list[dict] | None:
        if not html:
            return None

        if "Just a moment" in html or "cf-challenge" in html:
            self.logger.warning("Carrefour page seems blocked by Cloudflare")
            return None

        match = re.search(r'"stdItems"\s*:\s*\[', html)
        if not match:
            match = re.search(r"stdItems\s*=\s*\[", html)
            if not match:
                string_match = re.search(r'"stdItems"\s*:\s*"', html)
                if not string_match:
                    string_match = re.search(r"stdItems\s*=\s*JSON\.parse\(\"", html)
                if not string_match:
                    return None

                string_start = string_match.end() - 1
                string_value = self._extract_json_string(html, string_start)
                if not string_value:
                    return None
                unescaped = self._unescape_js_string(string_value)
                if not unescaped or not unescaped.startswith("["):
                    return None
                try:
                    return json.loads(unescaped)
                except json.JSONDecodeError:
                    return None

        start = match.end() - 1
        array_text = self._extract_json_array(html, start)
        if not array_text:
            return None

        try:
            return json.loads(array_text)
        except json.JSONDecodeError:
            return None

    def _extract_json_string(self, text: str, start: int) -> str | None:
        if start >= len(text) or text[start] != '"':
            return None

        i = start + 1
        escape = False
        for j in range(i, len(text)):
            ch = text[j]
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                return text[i:j]
        return None

    def _unescape_js_string(self, value: str) -> str:
        try:
            return bytes(value, "utf-8").decode("unicode_escape")
        except UnicodeDecodeError:
            return value

    def _extract_json_array(self, text: str, start: int) -> str | None:
        depth = 0
        in_string = False
        escape = False

        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        return text[start : i + 1]

        return None

    def _debug_html(self, html: str, url: str) -> None:
        if not html:
            self.logger.warning("Carrefour HTML empty for %s", url)
            return

        if self.cfg.get("debug_html_path"):
            path = Path(self.cfg["debug_html_path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")
            self.logger.info("Carrefour HTML saved to %s", path)
        else:
            self.logger.info("Carrefour HTML length: %d", len(html))

    def _build_headers(self, referer: str) -> dict:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self.cfg["base_url"],
            "Referer": referer,
        }
        api_key = self.cfg.get("api_key")
        if api_key:
            headers["apiKey"] = api_key
        return headers

    def _ensure_json(self, data: Any | None) -> Any | None:
        if data is None:
            return None
        if isinstance(data, (list, dict)):
            return data
        if isinstance(data, str):
            text = data.strip()
            if not text:
                return None
            if text[0] in "{[":
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return None
        return None

    def _parse_products(self, data: object | None) -> list[dict]:
        if not data:
            return []

        if isinstance(data, dict) and isinstance(data.get("products"), list):
            items = data["products"]
        elif isinstance(data, list):
            items = data
        else:
            return []

        products = []
        for item in items:
            offer = (
                item.get("items", [{}])[0]
                .get("sellers", [{}])[0]
                .get("commertialOffer", {})
            )

            product_id = item.get("productId") or item.get("sku") or item.get("id")
            product_name = (
                item.get("productName") or item.get("product") or item.get("name")
            )
            brand_name = (
                item.get("brand")
                or item.get("brandName")
                or (item.get("brand") or {}).get("brandName")
                or ""
            )

            high_price = (
                offer.get("ListPrice")
                or offer.get("listPrice")
                or item.get("priceFull")
            )
            low_price = (
                offer.get("Price") or offer.get("price") or item.get("price")
            )

            products.append(
                {
                    "store_id": self.store_id,
                    "sku": product_id,
                    "product_name": product_name,
                    "brand_name": brand_name,
                    "high_price": high_price,
                    "low_price": low_price,
                }
            )

        return products

    def _parse_std_items(self, items: list[dict]) -> list[dict]:
        products = []
        for item in items:
            products.append(
                {
                    "store_id": self.store_id,
                    "sku": item.get("sku"),
                    "product_name": item.get("product"),
                    "brand_name": "",
                    "high_price": item.get("priceFull"),
                    "low_price": item.get("price"),
                    "available": item.get("available"),
                    "url": item.get("urlLink"),
                    "image": item.get("imgThumb"),
                    "search_position": item.get("searchPosition"),
                }
            )
        return products


SCRAPER_REGISTRY = {
    "atacadao": AtacadaoScraper,
    "carrefour": CarrefourScraper,
}


def build_scrapers(stores_cfg: list[dict], extractor: Extractor) -> list[StoreScraper]:
    scrapers: list[StoreScraper] = []
    for raw in stores_cfg:
        if not raw.get("enabled", True):
            continue
        adapter = raw["adapter"]
        scraper_cls = SCRAPER_REGISTRY.get(adapter)
        if not scraper_cls:
            logger.warning("Adapter nao encontrado: %s", adapter)
            continue
        store_cfg = StoreConfig(id=raw["id"], adapter=adapter, raw=raw)
        scrapers.append(scraper_cls(store_cfg, extractor))
    return scrapers
