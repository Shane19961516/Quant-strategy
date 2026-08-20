"""China commodity option universe registry (full product list)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

Source = Literal["sina", "czce", "shfe", "gfex", "dce"]

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "option_universe.json"

# Sina scrape names → canonical registry (28 active on Sina as of 2026-08)
SINA_CN_NAMES: list[str] = [
    "豆粕期权",
    "玉米期权",
    "铁矿石期权",
    "棉花期权",
    "白糖期权",
    "PTA期权",
    "菜籽油期权",
    "花生期权",
    "甲醇期权",
    "橡胶期权",
    "沪铜期权",
    "黄金期权",
    "菜籽粕期权",
    "液化石油气期权",
    "动力煤期权",
    "黄大豆1号期权",
    "黄大豆2号期权",
    "豆油期权",
    "白银期权",
    "螺纹钢期权",
    "工业硅期权",
    "乙二醇期权",
    "苯乙烯期权",
    "碳酸锂期权",
    "丁二烯橡胶期权",
    "沪铝期权",
    "二甲苯期权",
    "烧碱期权",
]

# Exchange-only supplements (not on Sina or DCE API blocked)
EXCHANGE_ONLY: list[dict[str, str]] = [
    # CZCE
    {"product": "AP", "name": "苹果", "exchange": "CZCE", "cn_name": "苹果期权", "source": "czce"},
    {"product": "CJ", "name": "红枣", "exchange": "CZCE", "cn_name": "红枣期权", "source": "czce"},
    {"product": "FG", "name": "玻璃", "exchange": "CZCE", "cn_name": "玻璃期权", "source": "czce"},
    {"product": "SA", "name": "纯碱", "exchange": "CZCE", "cn_name": "纯碱期权", "source": "czce"},
    {"product": "UR", "name": "尿素", "exchange": "CZCE", "cn_name": "尿素期权", "source": "czce"},
    {"product": "SF", "name": "硅铁", "exchange": "CZCE", "cn_name": "硅铁期权", "source": "czce"},
    {"product": "SM", "name": "锰硅", "exchange": "CZCE", "cn_name": "锰硅期权", "source": "czce"},
    {"product": "PF", "name": "短纤", "exchange": "CZCE", "cn_name": "短纤期权", "source": "czce"},
    {"product": "PR", "name": "瓶片", "exchange": "CZCE", "cn_name": "瓶片期权", "source": "czce"},
    {"product": "PL", "name": "丙烯", "exchange": "CZCE", "cn_name": "丙烯期权", "source": "czce"},
    # SHFE / INE
    {"product": "sn", "name": "锡", "exchange": "SHFE", "cn_name": "锡期权", "source": "shfe"},
    {"product": "ni", "name": "镍", "exchange": "SHFE", "cn_name": "镍期权", "source": "shfe"},
    {"product": "zn", "name": "锌", "exchange": "SHFE", "cn_name": "锌期权", "source": "shfe"},
    {"product": "pb", "name": "铅", "exchange": "SHFE", "cn_name": "铅期权", "source": "shfe"},
    {"product": "ao", "name": "氧化铝", "exchange": "SHFE", "cn_name": "氧化铝期权", "source": "shfe"},
    {"product": "sc", "name": "原油", "exchange": "INE", "cn_name": "原油期权", "source": "shfe"},
    {"product": "sp", "name": "纸浆", "exchange": "SHFE", "cn_name": "纸浆期权", "source": "shfe"},
    {"product": "nr", "name": "20号胶", "exchange": "INE", "cn_name": "20号胶期权", "source": "shfe"},
    # GFEX
    {"product": "ps", "name": "多晶硅", "exchange": "GFEX", "cn_name": "多晶硅", "source": "gfex"},
    # DCE (may fail when exchange API blocked; still attempted)
    {"product": "pp", "name": "聚丙烯", "exchange": "DCE", "cn_name": "聚丙烯期权", "source": "dce"},
    {"product": "l", "name": "塑料", "exchange": "DCE", "cn_name": "聚乙烯期权", "source": "dce"},
    {"product": "v", "name": "PVC", "exchange": "DCE", "cn_name": "聚氯乙烯期权", "source": "dce"},
    {"product": "p", "name": "棕榈", "exchange": "DCE", "cn_name": "棕榈油期权", "source": "dce"},
    {"product": "cs", "name": "玉米淀粉", "exchange": "DCE", "cn_name": "玉米淀粉期权", "source": "dce"},
    {"product": "lh", "name": "生猪", "exchange": "DCE", "cn_name": "生猪期权", "source": "dce"},
    {"product": "jd", "name": "鸡蛋", "exchange": "DCE", "cn_name": "鸡蛋期权", "source": "dce"},
    {"product": "lg", "name": "原木", "exchange": "DCE", "cn_name": "原木期权", "source": "dce"},
    {"product": "j", "name": "焦炭", "exchange": "DCE", "cn_name": "焦炭期权", "source": "dce"},
    {"product": "jm", "name": "焦煤", "exchange": "DCE", "cn_name": "焦煤期权", "source": "dce"},
]

# Sina cn_name → product metadata
SINA_PRODUCT_META: dict[str, dict[str, Any]] = {
    "豆粕期权": {"product": "m", "name": "豆粕", "exchange": "DCE"},
    "玉米期权": {"product": "c", "name": "玉米", "exchange": "DCE"},
    "铁矿石期权": {"product": "i", "name": "铁矿", "exchange": "DCE"},
    "棉花期权": {"product": "CF", "name": "棉花", "exchange": "CZCE"},
    "白糖期权": {"product": "SR", "name": "白糖", "exchange": "CZCE"},
    "PTA期权": {"product": "TA", "name": "PTA", "exchange": "CZCE"},
    "菜籽油期权": {"product": "OI", "name": "菜油", "exchange": "CZCE"},
    "花生期权": {"product": "PK", "name": "花生", "exchange": "CZCE"},
    "甲醇期权": {"product": "MA", "name": "甲醇", "exchange": "CZCE"},
    "橡胶期权": {"product": "ru", "name": "橡胶", "exchange": "SHFE"},
    "沪铜期权": {"product": "cu", "name": "铜", "exchange": "SHFE"},
    "黄金期权": {"product": "au", "name": "黄金", "exchange": "SHFE"},
    "菜籽粕期权": {"product": "RM", "name": "菜粕", "exchange": "CZCE"},
    "液化石油气期权": {"product": "pg", "name": "LPG", "exchange": "DCE"},
    "动力煤期权": {"product": "ZC", "name": "动力煤", "exchange": "CZCE"},
    "黄大豆1号期权": {"product": "a", "name": "豆一", "exchange": "DCE"},
    "黄大豆2号期权": {"product": "b", "name": "豆二", "exchange": "DCE"},
    "豆油期权": {"product": "y", "name": "豆油", "exchange": "DCE"},
    "白银期权": {"product": "ag", "name": "白银", "exchange": "SHFE"},
    "螺纹钢期权": {"product": "rb", "name": "螺纹", "exchange": "SHFE"},
    "工业硅期权": {"product": "si", "name": "工业硅", "exchange": "GFEX"},
    "乙二醇期权": {"product": "eg", "name": "乙二醇", "exchange": "DCE"},
    "苯乙烯期权": {"product": "eb", "name": "苯乙烯", "exchange": "DCE"},
    "碳酸锂期权": {"product": "lc", "name": "碳酸锂", "exchange": "GFEX"},
    "丁二烯橡胶期权": {"product": "br", "name": "丁二烯橡胶", "exchange": "SHFE"},
    "沪铝期权": {"product": "al", "name": "铝", "exchange": "SHFE"},
    "二甲苯期权": {"product": "PX", "name": "二甲苯", "exchange": "CZCE", "fetch_symbol": "对二甲苯期权"},
    "烧碱期权": {"product": "SH", "name": "烧碱", "exchange": "CZCE"},
}


@dataclass(frozen=True)
class OptionProduct:
    product: str
    name: str
    exchange: str
    cn_name: str
    source: Source
    fetch_symbol: str  # symbol passed to data API (may differ from cn_name)

    @property
    def product_key(self) -> str:
        return self.product.upper()


def _build_default_universe() -> list[OptionProduct]:
    items: dict[str, OptionProduct] = {}
    for cn in SINA_CN_NAMES:
        meta = SINA_PRODUCT_META[cn]
        prod = meta["product"]
        fetch_sym = meta.get("fetch_symbol", cn)
        items[prod.upper()] = OptionProduct(
            product=prod,
            name=meta["name"],
            exchange=meta["exchange"],
            cn_name=cn,
            source="sina",
            fetch_symbol=cn,
        )
        # CZCE exchange fetch fallback symbol for IV history etc.
        if meta["exchange"] == "CZCE" and fetch_sym != cn:
            pass
    for row in EXCHANGE_ONLY:
        prod = row["product"]
        key = prod.upper()
        if key in items:
            continue
        items[key] = OptionProduct(
            product=prod,
            name=row["name"],
            exchange=row["exchange"],
            cn_name=row["cn_name"],
            source=row["source"],  # type: ignore[arg-type]
            fetch_symbol=row["cn_name"],
        )
    return sorted(items.values(), key=lambda x: (x.exchange, x.product_key))


@lru_cache(maxsize=1)
def load_universe() -> list[OptionProduct]:
    """Load full option universe; falls back to built-in registry."""
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            out: list[OptionProduct] = []
            for row in raw.get("products", []):
                out.append(
                    OptionProduct(
                        product=row["product"],
                        name=row["name"],
                        exchange=row["exchange"],
                        cn_name=row["cn_name"],
                        source=row["source"],
                        fetch_symbol=row.get("fetch_symbol", row["cn_name"]),
                    )
                )
            if out:
                return out
        except Exception:
            logger.exception("failed to load %s", CONFIG_PATH)
    return _build_default_universe()


def universe_product_codes() -> list[str]:
    return [p.product for p in load_universe()]


def scrape_sina_active_names() -> list[str]:
    """Scrape Sina options page for currently listed products."""
    import requests
    from bs4 import BeautifulSoup

    url = "https://stock.finance.sina.com.cn/futures/view/optionsDP.php/pg_o/dce"
    r = requests.get(url, timeout=30)
    soup = BeautifulSoup(r.text, "lxml")
    return [
        item.find("a").text
        for item in soup.find_all("li", attrs={"class": "active"})
        if item.find("a") is not None
    ]


def write_universe_json(path: Path | None = None) -> Path:
    path = path or CONFIG_PATH
    products = _build_default_universe()
    payload = {
        "version": "1.0.0",
        "description": "China commodity option universe for short-strangle screener",
        "products": [
            {
                "product": p.product,
                "name": p.name,
                "exchange": p.exchange,
                "cn_name": p.cn_name,
                "source": p.source,
                "fetch_symbol": p.fetch_symbol,
            }
            for p in products
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
