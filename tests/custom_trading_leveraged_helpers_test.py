from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path


def load_api_main():
    os.environ.setdefault("TRADING_API_KEY", "test-key")
    sys.modules.setdefault("pytz", types.SimpleNamespace(timezone=lambda _name: None))
    sys.modules.setdefault("orjson", types.SimpleNamespace(loads=json.loads))
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    path = repo_root / "custom-trading" / "api" / "main.py"
    spec = importlib.util.spec_from_file_location("custom_trading_api_main", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_all_subtype_does_not_filter_products() -> None:
    main = load_api_main()
    products = [{"name": "Apple Turbo BEST Open-End Call"}, {"name": "Apple Mini Long"}]

    assert main.filter_by_product_subtype(products, "all") == products


def test_supported_knockout_filter_keeps_long_turbo_and_rejects_put_short_optionsschein() -> None:
    main = load_api_main()

    assert main.is_supported_knockout_product(
        {"name": "SG Apple Faktor Long HB 4", "shortlong": 1, "tradable": True},
        action="LONG",
    )
    assert main.is_supported_knockout_product(
        {"name": "Apple Turbo BEST Open-End Call 5.1", "shortlong": "1", "tradable": True},
        action="LONG",
    )
    assert not main.is_supported_knockout_product(
        {"name": "Apple Turbo BEST Open-End Put 5.1", "shortlong": "L", "tradable": True},
        action="LONG",
    )
    assert not main.is_supported_knockout_product(
        {"name": "Apple Optionsschein Call 5.1", "shortlong": "L", "tradable": True},
        action="LONG",
    )
    assert not main.is_supported_knockout_product(
        {"name": "Apple Turbo BEST Open-End Short 5.1", "shortlong": "S", "tradable": True},
        action="LONG",
    )


def test_product_fallback_price_uses_search_metadata_when_realtime_is_missing() -> None:
    main = load_api_main()

    price, source = main.product_fallback_price({"closePrice": "8,42"})

    assert price.last == 8.42
    assert source == "closePrice"



def test_product_fallback_price_uses_nested_quote_metadata() -> None:
    main = load_api_main()

    price, source = main.product_fallback_price({"quote": {"askPrice": "1.23", "bidPrice": "1.21"}})

    assert price.ask == 1.23
    assert price.bid == 1.21
    assert source == "quote.askPrice"


def test_normalize_shortlong_handles_degiro_variants() -> None:
    main = load_api_main()

    assert main.normalize_shortlong(1) == "L"
    assert main.normalize_shortlong("LONG") == "L"
    assert main.normalize_shortlong(0) == "S"
    assert main.normalize_shortlong("SHORT") == "S"
