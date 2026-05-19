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

    assert not main.is_supported_knockout_product(
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


def test_leveraged_search_terms_adds_clean_company_name_for_web_query() -> None:
    main = load_api_main()

    assert main.leveraged_search_terms({"symbol": "AAPL", "name": "Apple Inc"}) == [
        "AAPL",
        "Apple Inc",
        "Apple",
    ]


def test_build_leveraged_ko_query_request_uses_underlying_id_without_offset() -> None:
    main = load_api_main()

    request = main.build_leveraged_ko_query_request(
        underlying_product_id=1140400,
        action="LONG",
        min_leverage=4.0,
        max_leverage=6.0,
        limit=50,
    )
    params = request.model_dump(by_alias=True, exclude_none=True, mode="json")

    assert params["productType"] == 560
    assert params["underlyingProductId"] == 1140400
    assert params["subProductType"] == 14
    assert params["instrumentTypeId"] == 11
    assert params["shortlong"] == "1"
    assert "minLeverage" not in params
    assert "maxLeverage" not in params
    assert "offset" not in params
    assert "searchText" not in params


def test_build_leveraged_ko_query_request_keeps_search_text_fallback_without_offset() -> None:
    main = load_api_main()

    request = main.build_leveraged_ko_query_request(
        "apple",
        action="LONG",
        min_leverage=4.0,
        max_leverage=6.0,
        limit=50,
    )
    params = request.model_dump(by_alias=True, exclude_none=True, mode="json")

    assert params["productType"] == 560
    assert params["searchText"] == "apple"
    assert params["subProductType"] == 14
    assert params["instrumentTypeId"] == 11
    assert params["minLeverage"] == 4.0
    assert params["maxLeverage"] == 6.0
    assert params["shortlong"] == "1"
    assert "offset" not in params
    assert "underlyingProductId" not in params


def test_rank_and_limit_leveraged_products_keeps_highest_after_broad_filtering() -> None:
    main = load_api_main()
    products = [
        {"id": "low-1", "_effective_leverage": 1.9},
        {"id": "low-2", "_effective_leverage": 2.1},
        {"id": "ifx-5x", "_effective_leverage": 5.0},
        {"id": "mid", "_effective_leverage": 3.2},
    ]

    ranked = main.rank_and_limit_leveraged_products(products, 2)

    assert [product["id"] for product in ranked] == ["ifx-5x", "mid"]


def test_rank_and_limit_leveraged_products_clamps_invalid_limit() -> None:
    main = load_api_main()
    products = [
        {"id": "low", "_effective_leverage": 1.9},
        {"id": "high", "_effective_leverage": 5.0},
    ]

    ranked = main.rank_and_limit_leveraged_products(products, -10)

    assert [product["id"] for product in ranked] == ["high"]



def test_dynamic_leveraged_search_uses_underlying_id_first(monkeypatch) -> None:
    main = load_api_main()

    class FakeAPI:
        def __init__(self) -> None:
            self.requests = []

        def product_search(self, product_request, raw=False):
            self.requests.append(product_request)
            return {
                "products": [
                    {
                        "id": "abc",
                        "name": "Apple Turbo BEST Open-End Call 5.1",
                        "leverage": 5.0,
                        "shortlong": "L",
                        "tradable": True,
                    }
                ]
            }

    monkeypatch.setattr(main, "get_real_prices_batch", lambda _ids: {})
    api = FakeAPI()
    request = main.ProductSearchRequest(
        q="apple",
        underlying_id=123,
        action="LONG",
        min_leverage=4.0,
        max_leverage=6.0,
        limit=10,
    )

    products = main.search_leveraged_products_dynamic(api, None, request)
    params = api.requests[0].model_dump(by_alias=True, exclude_none=True, mode="json")

    assert len(products) == 1
    assert len(api.requests) == 1
    assert params["underlyingProductId"] == 123
    assert params["productType"] == 560
    assert params["subProductType"] == 14
    assert params["instrumentTypeId"] == 11
    assert "minLeverage" not in params
    assert "maxLeverage" not in params
    assert "offset" not in params
    assert "searchText" not in params


def test_dynamic_leveraged_search_falls_back_to_text_only_when_underlying_id_returns_empty(monkeypatch) -> None:
    main = load_api_main()

    class FakeAPI:
        def __init__(self) -> None:
            self.requests = []

        def product_search(self, product_request, raw=False):
            self.requests.append(product_request)
            params = product_request.model_dump(by_alias=True, exclude_none=True, mode="json")
            if "underlyingProductId" in params:
                return {"products": []}
            return {
                "products": [
                    {
                        "id": "abc",
                        "name": "Apple Turbo BEST Open-End Call 5.1",
                        "leverage": 5.0,
                        "shortlong": "L",
                        "tradable": True,
                    }
                ]
            }

    monkeypatch.setattr(main, "get_real_prices_batch", lambda _ids: {})
    api = FakeAPI()
    request = main.ProductSearchRequest(
        q="apple",
        underlying_id=123,
        action="LONG",
        min_leverage=4.0,
        max_leverage=6.0,
        limit=10,
    )

    products = main.search_leveraged_products_dynamic(api, None, request)
    first_params = api.requests[0].model_dump(by_alias=True, exclude_none=True, mode="json")
    second_params = api.requests[1].model_dump(by_alias=True, exclude_none=True, mode="json")

    assert len(products) == 1
    assert first_params["underlyingProductId"] == 123
    assert "searchText" not in first_params
    assert second_params["searchText"] == "apple"
    assert "underlyingProductId" not in second_params
    assert "offset" not in first_params
    assert "offset" not in second_params
