import re
import unicodedata
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

from .extractor import Extractor
from .scraper import build_scrapers
from .utils.log import logger


def clean_word(word: str):
    word = word.lower()

    # remove acentos
    word = unicodedata.normalize("NFKD", word)
    word = "".join(c for c in word if not unicodedata.combining(c))

    # troca espaços por underscore
    word = re.sub(r"\s+", "_", word.strip())

    return word


def load_yaml(path: Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_keywords(store_cfg: dict, products_cfg_path: Path | None) -> list[str]:
    if "keywords" in store_cfg:
        raw = store_cfg.get("keywords", [])
    elif products_cfg_path and Path(products_cfg_path).exists():
        raw = load_yaml(products_cfg_path).get("keywords", [])
    else:
        raw = []

    keywords = []
    for item in raw:
        if isinstance(item, dict) and "name" in item:
            keywords.append(item["name"])
        else:
            keywords.append(str(item))
    return keywords


def search_products(
    store_config_file: Path,
    output_dir: Path,
    products_config_file: Path | None = None,
):
    extractor = Extractor()
    output_dir = Path(output_dir)

    store_cfg = load_yaml(store_config_file)
    stores = store_cfg.get("stores", [])
    if not stores:
        logger.warning("Nenhuma loja encontrada em %s", store_config_file)
        return

    keywords = load_keywords(store_cfg, products_config_file)
    if not keywords:
        logger.warning("Nenhuma keyword encontrada")
        return

    scrapers = build_scrapers(stores, extractor)
    if not scrapers:
        logger.warning("Nenhum scraper ativo")
        return

    dt = datetime.now().strftime("%Y-%m-%d")
    for scraper in scrapers:
        for keyword in keywords:
            products = scraper.search(keyword)
            if not products:
                continue

            df = pd.DataFrame(products)
            df["keyword"] = keyword
            df["extracted_at"] = dt

            keyword_clean = clean_word(keyword)
            filepath = output_dir / f"{scraper.store_id}_{keyword_clean}_{dt}.csv"
            df.to_csv(filepath, sep=";", index=False)
            logger.info("Saved %d rows to %s", len(df), filepath)


if __name__ == "__main__":
    output = "/media/lucas/Files/2.Projetos/0.mylake/raw/inflation"

    store_config = "src/store_config.yml"
    products_config = "src/products_config.yml"

    search_products(
        store_config_file=store_config,
        products_config_file=products_config,
        output_dir=output,
    )
