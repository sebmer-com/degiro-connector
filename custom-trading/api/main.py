#!/usr/bin/env python3
"""
Production DEGIRO Trading API
Complete API for searching products and placing orders with full DEGIRO functionality
"""

import json
import os
import re
from typing import Optional, List, Dict, Any
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Depends, status, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional as TypingOptional
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field

from degiro_connector.trading.api import API as TradingAPI
from degiro_connector.trading.models.credentials import Credentials
from degiro_connector.trading.models.product_search import StocksRequest, LeveragedsRequest
from degiro_connector.trading.models.order import Order

# Load environment variables
# Load environment variables from .env if available
try:
    from dotenv import load_dotenv
    load_dotenv('config/.env')
except ImportError:
    pass

# FastAPI app
app = FastAPI(
    title="DEGIRO Trading API",
    description="Production API for DEGIRO trading: search products, place orders, manage positions",
    version="2.0.0",
    docs_url=None,  # Disable public docs
    redoc_url=None,  # Disable public redoc
    openapi_url=None  # Disable default openapi.json - we use custom auth-protected endpoint
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security (auto_error=False allows query param fallback)
security = HTTPBearer(auto_error=False)

# Configuration
API_KEY = os.getenv("TRADING_API_KEY")
if not API_KEY:
    raise Exception("TRADING_API_KEY environment variable is required")

DEGIRO_CONFIG_PATH = "config/config.json"

# Global DEGIRO connection
trading_api = None

# === MODELS ===

class PriceInfo(BaseModel):
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None

class DirectStock(BaseModel):
    product_id: str
    name: str
    isin: str
    currency: str
    exchange_id: str
    current_price: PriceInfo
    tradable: bool

class LeveragedProduct(BaseModel):
    product_id: str
    name: str
    isin: str
    leverage: float
    direction: str  # LONG/SHORT
    currency: str
    exchange_id: str
    current_price: PriceInfo
    tradable: bool
    expiration_date: Optional[str] = None
    issuer: Optional[str] = None
    price_source: Optional[str] = None

# NEW API MODELS

# Stock Search Models
class StockSearchRequest(BaseModel):
    q: str = Field(..., description="Search query - ISIN, company name, ticker, or symbol")
    limit: int = Field(default=50, description="Maximum number of stocks to return")

class StockOption(BaseModel):
    product_id: str
    name: str
    isin: str
    symbol: Optional[str] = None
    currency: str
    exchange_id: str
    current_price: PriceInfo
    tradable: bool

class StockSearchResponse(BaseModel):
    query: str
    stocks: List[StockOption]
    total_found: int
    timestamp: str

# Leveraged Products Search Models
class LeveragedSearchRequest(BaseModel):
    underlying_id: str = Field(..., description="Stock product ID from stocks search")
    action: str = Field(default="LONG", description="LONG or SHORT")
    min_leverage: float = Field(default=2.0, description="Minimum leverage")
    max_leverage: float = Field(default=10.0, description="Maximum leverage")
    limit: int = Field(default=50, description="Max leveraged products to return")
    issuer_id: Optional[int] = Field(default=None, description="Issuer filter (-1=all)")
    product_subtype: str = Field(default="ALL", description="Product subtype filter: ALL, CALL_PUT (Optionsscheine), MINI (Knockouts), UNLIMITED (Faktor)")

class LeveragedSearchResponse(BaseModel):
    query: Dict[str, Any]
    underlying_stock: Optional[StockOption]
    leveraged_products: List[LeveragedProduct]
    total_found: int
    timestamp: str

# Legacy combined search (deprecated)
class ProductSearchRequest(BaseModel):
    q: str = Field(..., description="Universal search - ISIN, company name, ticker, or symbol")
    action: str = Field(default="LONG", description="LONG or SHORT")
    min_leverage: float = Field(default=2.0, description="Minimum leverage")
    max_leverage: float = Field(default=10.0, description="Maximum leverage")
    limit: int = Field(default=50, description="Max leveraged products to return")
    
    # Enhanced leveraged product parameters
    product_type: Optional[int] = Field(default=None, description="Product type (560=leveraged web search)")
    sub_product_type: Optional[int] = Field(default=None, description="Sub product type (14=KO/turbo leveraged)")
    short_long: Optional[int] = Field(default=None, description="Direction filter (-1=all, 1=LONG, 0=SHORT)")
    issuer_id: Optional[int] = Field(default=None, description="Issuer filter (-1=all)")
    underlying_id: Optional[int] = Field(default=None, description="Underlying stock product ID")

class ProductSearchResponse(BaseModel):
    query: Dict[str, Any]
    direct_stock: Optional[DirectStock]
    leveraged_products: List[LeveragedProduct]
    total_found: Dict[str, int]
    timestamp: str


LEVERAGED_WEB_PRODUCT_TYPE = 560
LEVERAGED_KO_SUB_PRODUCT_TYPE = 14
LEVERAGED_KO_INSTRUMENT_TYPE_ID = 11

# Order Models
class OrderRequest(BaseModel):
    product_id: str = Field(..., description="Product ID to trade")
    action: str = Field(..., description="BUY or SELL")
    order_type: str = Field(default="LIMIT", description="LIMIT, MARKET, STOP_LOSS, STOP_LIMIT")
    quantity: float = Field(..., gt=0, description="Number of shares/units")
    price: Optional[float] = Field(None, gt=0, description="Limit price (required for LIMIT/STOP_LIMIT)")
    stop_price: Optional[float] = Field(None, gt=0, description="Stop price (required for STOP_LOSS/STOP_LIMIT)")
    time_type: str = Field(default="DAY", description="DAY or GTC")

class OrderCheckResponse(BaseModel):
    valid: bool
    confirmation_id: Optional[str] = None
    estimated_fee: Optional[float] = None
    total_cost: Optional[float] = None
    free_space_new: Optional[float] = None
    message: str
    warnings: List[str] = []
    errors: List[str] = []

class OrderResponse(BaseModel):
    success: bool
    order_id: Optional[str] = None
    confirmation_id: Optional[str] = None
    message: str
    product_id: str
    action: str
    order_type: str
    quantity: float
    price: Optional[float] = None
    stop_price: Optional[float] = None
    estimated_fee: Optional[float] = None
    total_cost: Optional[float] = None
    created_at: str

# Volume and Price API Models (matching ORB strategy requirements)
class VolumeResponse(BaseModel):
    symbol: str
    current_time: str
    market_open_time: str
    elapsed_minutes: float
    cumulative_volume: int
    last_volume: int
    volume_rate_per_minute: float
    degiro_vwd_id: str
    degiro_id: str
    current_price: PriceInfo
    timestamp: str

class NasdaqBatchResponse(BaseModel):
    market_open_time: str
    current_time: str
    elapsed_minutes: float
    stocks: List[VolumeResponse]
    total_stocks: int
    timestamp: str

class PriceResponse(BaseModel):
    symbol: str
    current_price: float
    open_price: float
    high_price: float
    low_price: float
    volume: Optional[int] = None
    vwap: float
    market_open_time: str
    current_time: str
    degiro_vwd_id: str

# === AUTHENTICATION ===

def verify_api_key(
    credentials: TypingOptional[HTTPAuthorizationCredentials] = Depends(security),
    api_key: TypingOptional[str] = Query(None, description="API key for authentication")
):
    """
    Verify API key from either:
    1. Authorization: Bearer <token> header (preferred)
    2. ?api_key=<token> query parameter (for browser/docs access)

    Used for documentation endpoints (/, /docs, /openapi.json)
    """
    # Try Bearer token first
    if credentials and credentials.credentials == API_KEY:
        return credentials.credentials

    # Fall back to query parameter
    if api_key and api_key == API_KEY:
        return api_key

    # Neither method provided valid key
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key. Use Authorization: Bearer <token> header or ?api_key=<token> query parameter",
        headers={"WWW-Authenticate": "Bearer"},
    )

def verify_api_key_header_only(
    credentials: TypingOptional[HTTPAuthorizationCredentials] = Depends(security)
):
    """
    Verify API key from Authorization: Bearer <token> header ONLY

    Used for all API endpoints (/api/*)
    Query parameter authentication is NOT supported for API endpoints.
    """
    if credentials and credentials.credentials == API_KEY:
        return credentials.credentials

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key. Use Authorization: Bearer <token> header",
        headers={"WWW-Authenticate": "Bearer"},
    )

# === DEGIRO CONNECTION ===

# Global API instance - reused within single server lifetime
trading_api = None
price_cache: dict[str, dict[str, Any]] = {}

def _build_degiro_credentials() -> Credentials:
    # Build DEGIRO credentials from env, falling back to config.json.
    username = os.getenv('DEGIRO_USERNAME')
    password = os.getenv('DEGIRO_PASSWORD')
    totp_secret_key = os.getenv('DEGIRO_TOTP_SECRET')
    int_account = os.getenv('DEGIRO_INT_ACCOUNT')

    if all([username, password, totp_secret_key]):
        credentials_data = {
            'username': username,
            'password': password,
            'totp_secret_key': totp_secret_key,
        }
        if int_account:
            credentials_data['int_account'] = int(int_account)
        return Credentials(**credentials_data)

    try:
        with open(DEGIRO_CONFIG_PATH, 'r') as f:
            config = json.load(f)
        return Credentials(
            username=config['username'],
            password=config['password'],
            totp_secret_key=config['totp_secret_key'],
            int_account=config['int_account'],
        )
    except FileNotFoundError:
        raise Exception('DEGIRO credentials not found in environment variables or config file')


def get_trading_api(force_reconnect: bool = False):
    # Get or create DEGIRO trading API connection.
    global trading_api

    if force_reconnect:
        trading_api = None

    if trading_api is None:
        try:
            trading_api = TradingAPI(credentials=_build_degiro_credentials())
            trading_api.connect()
        except Exception as e:
            trading_api = None
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f'Failed to connect to DEGIRO: {str(e)}'
            )

    return trading_api


def reset_trading_api():
    # Drop the cached DEGIRO session so the next call logs in again.
    global trading_api
    trading_api = None


def ping_trading_api(api: TradingAPI) -> bool:
    # Cheap authenticated call used to verify the cached DEGIRO session.
    req = StocksRequest(
        search_text='AAPL',
        offset=0,
        limit=1,
        require_total=False,
        sort_columns='name',
        sort_types='asc',
    )
    res = api.product_search(req, raw=True)
    products = (res or {}).get('products') if isinstance(res, dict) else None
    return bool(products)


def get_fresh_trading_api():
    # Return a usable DEGIRO session, reconnecting once if the cached one expired.
    api = get_trading_api()
    try:
        if ping_trading_api(api):
            return api
    except Exception as e:
        if not is_session_expired(str(e)):
            raise

    reset_trading_api()
    api = get_trading_api(force_reconnect=True)
    if not ping_trading_api(api):
        reset_trading_api()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='DEGIRO session invalid after reconnect'
        )
    return api


def call_degiro_with_reconnect(operation):
    # Run a DEGIRO operation and retry once after dropping an expired session.
    api = get_trading_api()
    try:
        return operation(api)
    except Exception as e:
        if not is_session_expired(str(e)):
            raise
        reset_trading_api()
        api = get_trading_api(force_reconnect=True)
        return operation(api)

# === DYNAMIC LEVERAGED SEARCH ===

# === HELPER FUNCTIONS ===

def is_session_expired(error_message: str) -> bool:
    """
    Detect if an error is due to DEGIRO session expiry

    Common session expiry indicators:
    - "401" or "Unauthorized"
    - "session" in error message
    - "login" or "authentication" errors
    """
    error_lower = str(error_message).lower()
    return any([
        '401' in error_lower,
        'unauthorized' in error_lower,
        'session' in error_lower and ('expired' in error_lower or 'invalid' in error_lower),
        'login' in error_lower,
        'authentication' in error_lower,
        'credential' in error_lower
    ])

def reconnect_trading_api():
    """Force reconnection to DEGIRO by resetting the global trading_api"""
    global trading_api
    print("⚠️  DEGIRO session expired - reconnecting...")
    trading_api = None  # Reset global
    return get_trading_api()  # This will create new connection

def extract_leverage_from_name(product_name: str) -> Optional[float]:
    """Extract leverage value from product name"""
    import re
    if not product_name:
        return None
    
    # Look for patterns like "LV 2.44", "Leverage 5.0", etc.
    patterns = [
        r'LV\s+(\d+\.?\d*)',
        r'leverage\s+(\d+\.?\d*)',
        r'x(\d+\.?\d*)',
        r'(\d+\.?\d*)x'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, product_name, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    
    return None

def search_stocks_multiple(api: TradingAPI, query: str, limit: int = 20) -> List[Dict]:
    """Search for multiple stocks - returns ALL matching options with retry logic"""
    import time

    max_retries = 3
    retry_delay = 2  # seconds

    for attempt in range(max_retries):
        try:
            stock_request = StocksRequest(
                search_text=query,
                offset=0,
                limit=limit,
                require_total=True,
                sort_columns="name",
                sort_types="asc"
            )

            search_results = api.product_search(stock_request, raw=True)

            if isinstance(search_results, dict) and 'products' in search_results:
                products = search_results['products']

                # If we got products, return immediately
                if products:
                    if attempt > 0:
                        print(f"✅ Stock search succeeded on attempt {attempt + 1}")
                    return products

                # Empty result - retry if we have attempts left
                if attempt < max_retries - 1:
                    print(f"⚠️  Empty stock search result (attempt {attempt + 1}/{max_retries}), retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 1.5  # Exponential backoff
                    continue
                else:
                    print(f"❌ Stock search returned empty after {max_retries} attempts")
                    return []

            return []

        except Exception as e:
            if attempt < max_retries - 1:
                print(f"⚠️  Stock search error (attempt {attempt + 1}/{max_retries}): {e}, retrying in {retry_delay}s...")
                time.sleep(retry_delay)
                retry_delay *= 1.5  # Exponential backoff
                continue
            else:
                print(f"❌ Stock search failed after {max_retries} attempts: {e}")
                return []

    return []

def search_stock_universal(api: TradingAPI, query: str) -> Optional[Dict]:
    """Universal stock search with multiple strategies (legacy function)"""
    try:
        stock_request = StocksRequest(
            search_text=query,
            offset=0,
            limit=20,
            require_total=True,
            sort_columns="name",
            sort_types="asc"
        )
        
        search_results = api.product_search(stock_request, raw=True)
        
        if isinstance(search_results, dict) and 'products' in search_results:
            products = search_results['products']
            
            if not products:
                return None
            
            # Strategy 1: Exact ISIN match
            for product in products:
                if product.get('isin') == query:
                    return product
            
            # Strategy 2: Exact symbol match
            for product in products:
                if product.get('symbol') == query.upper():
                    return product
            
            # Strategy 3: Name contains query
            query_lower = query.lower()
            for product in products:
                name = product.get('name', '').lower()
                if query_lower in name:
                    return product
            
            # Strategy 4: Return first result
            return products[0]
        
        return None
        
    except Exception as e:
        print(f"Universal stock search failed: {e}")
        return None


def leveraged_direction_query_value(action: str) -> str:
    """DEGIRO web search uses 1 for LONG and 0 for SHORT."""
    return "1" if str(action).upper() == "LONG" else "0"


def leveraged_search_terms(stock_product: Optional[Dict], fallback: str = "") -> List[str]:
    """Return stable searchText candidates for the DEGIRO web leveraged query."""
    candidates: List[str] = []
    if stock_product:
        for key in ("symbol", "name"):
            value = str(stock_product.get(key) or "").strip()
            if value:
                candidates.append(value)
                # DEGIRO's web leveraged search finds Apple products for
                # searchText=apple, but not for AAPL or Apple Inc. Add a cleaned
                # company-name term before falling back to the raw query.
                if key == "name":
                    cleaned = re.sub(
                        r"\b(?:inc|inc\.|corp|corp\.|corporation|nv|n\.v\.|sa|s\.a\.|plc|ag|se|ltd|ltd\.|class\s+[a-z])\b",
                        "",
                        value,
                        flags=re.IGNORECASE,
                    )
                    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", cleaned).strip()
                    if cleaned and cleaned != value:
                        candidates.append(cleaned)
    fallback = str(fallback or "").strip()
    if fallback:
        candidates.append(fallback)

    seen: set[str] = set()
    unique_terms: List[str] = []
    for candidate in candidates:
        normalized = candidate.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique_terms.append(candidate)
    return unique_terms


def build_leveraged_ko_query_request(
    search_text: str | None = None,
    *,
    underlying_product_id: int | None = None,
    action: str,
    min_leverage: float,
    max_leverage: float,
    limit: int,
) -> LeveragedsRequest:
    """Build the DEGIRO web-style KO/turbo query without offset pagination."""
    return LeveragedsRequest(
        search_text=search_text,
        underlying_product_id=underlying_product_id,
        limit=max(1, limit),
        require_total=True,
        product_type=LEVERAGED_WEB_PRODUCT_TYPE,
        sub_product_type=LEVERAGED_KO_SUB_PRODUCT_TYPE,
        instrument_type_id=LEVERAGED_KO_INSTRUMENT_TYPE_ID,
        min_leverage=min_leverage,
        max_leverage=max_leverage,
        shortlong=leveraged_direction_query_value(action),
    )


def _dedupe_products(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    products_by_id: dict[str, Dict[str, Any]] = {}
    for product in products:
        product_id = str(product.get("id") or "")
        key = product_id or f"{product.get('isin', '')}:{product.get('name', '')}"
        if key and key not in products_by_id:
            products_by_id[key] = product
    return list(products_by_id.values())


def fetch_leveraged_products_by_query(
    api: TradingAPI,
    search_terms: List[str],
    *,
    underlying_product_id: int | None = None,
    action: str,
    min_leverage: float,
    max_leverage: float,
    limit: int,
) -> List[Dict[str, Any]]:
    """Fetch KO/turbo products by stored underlying ID first, then text fallbacks."""
    query_limit = max(limit, 100)

    if underlying_product_id:
        leveraged_request = build_leveraged_ko_query_request(
            underlying_product_id=int(underlying_product_id),
            action=action,
            min_leverage=min_leverage,
            max_leverage=max_leverage,
            limit=query_limit,
        )
        search_results = api.product_search(leveraged_request, raw=True)
        products = (search_results or {}).get("products") if isinstance(search_results, dict) else []
        products = products or []
        print(f"DEBUG: Underlying-id leveraged search '{underlying_product_id}' returned {len(products)} products")
        if products:
            return _dedupe_products(products)

    all_products: List[Dict[str, Any]] = []
    for search_text in search_terms:
        leveraged_request = build_leveraged_ko_query_request(
            search_text,
            action=action,
            min_leverage=min_leverage,
            max_leverage=max_leverage,
            limit=query_limit,
        )
        search_results = api.product_search(leveraged_request, raw=True)
        if not isinstance(search_results, dict) or "products" not in search_results:
            continue

        products = search_results.get("products") or []
        print(f"DEBUG: Query-param leveraged search '{search_text}' returned {len(products)} products")
        all_products.extend(products)

    return _dedupe_products(all_products)


def search_leveraged_products_dynamic(api: TradingAPI, stock_product: Optional[Dict], request: ProductSearchRequest) -> List[Dict]:
    """Dynamic leveraged products search - uses stock product ID as underlying ID"""
    try:
        # Use provided underlying_id or get from stock search
        underlying_id = request.underlying_id
        if not underlying_id and stock_product:
            try:
                underlying_id = int(stock_product.get('id'))
            except (ValueError, TypeError):
                return []
        
        if not underlying_id:
            return []
        
        underlying_prices = get_real_prices_batch([str(underlying_id)])
        underlying_price = price_info_value(underlying_prices.get(str(underlying_id)))
        if underlying_price is None and stock_product:
            fallback_underlying_price, _ = product_fallback_price(stock_product)
            underlying_price = price_info_value(fallback_underlying_price)

        suitable_products = []
        seen_ids: set[str] = set()
        products = fetch_leveraged_products_by_query(
            api,
            leveraged_search_terms(stock_product, request.q),
            underlying_product_id=underlying_id,
            action=request.action,
            min_leverage=request.min_leverage,
            max_leverage=request.max_leverage,
            limit=request.limit,
        )

        for product in products:
            product_id = str(product.get('id') or '')
            if product_id and product_id in seen_ids:
                continue
            if product_id:
                seen_ids.add(product_id)
            leverage = approximate_long_leverage(product, underlying_price)
            if leverage is None:
                continue

            if (request.min_leverage <= leverage <= request.max_leverage and
                is_supported_knockout_product(product, action=request.action)):
                product["_effective_leverage"] = leverage
                suitable_products.append(product)

            if len(suitable_products) >= request.limit:
                break

        return suitable_products
        
    except Exception as e:
        return []

def search_leveraged_products(api: TradingAPI, search_term: str, action: str, min_leverage: float, max_leverage: float, limit: int) -> List[Dict]:
    """Search for leveraged products"""
    try:
        products = fetch_leveraged_products_by_query(
            api,
            [search_term],
            action=action,
            min_leverage=min_leverage,
            max_leverage=max_leverage,
            limit=limit,
        )

        suitable_products = []
        target_direction = "L" if action.upper() == "LONG" else "S"

        for product in products:
            leverage = _positive_float(product.get('leverage')) or 0.0
            shortlong = normalize_shortlong(product.get('shortlong'))
            tradable = product.get('tradable', False)

            if (min_leverage <= leverage <= max_leverage and
                shortlong == target_direction and
                tradable):
                suitable_products.append(product)

                if len(suitable_products) >= limit:
                    break

        return suitable_products
        
    except Exception as e:
        print(f"Leveraged search failed: {e}")
        return []

def get_real_prices_batch(product_ids: list[str]) -> dict[str, PriceInfo]:
    """Get real price data for multiple products from DEGIRO using quotecast API"""
    print(f"DEBUG get_real_prices_batch: Called with {len(product_ids)} product IDs")
    try:
        # First get user token from config file
        try:
            with open(DEGIRO_CONFIG_PATH, 'r') as f:
                config_dict = json.load(f)
            user_token = config_dict.get("user_token")
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail=f"Unable to load user token from config: {str(e)}"
            )
        
        if not user_token:
            raise HTTPException(
                status_code=503,
                detail="No valid user token found in config for real-time pricing"
            )

        # Get trading API instance to fetch product metadata
        api = get_fresh_trading_api()
        
        # Get product info for all products to determine vwdIds
        try:
            product_list_int = [int(pid) for pid in product_ids]
            print(f"DEBUG: Calling get_products_info with {len(product_list_int)} IDs: {product_list_int[:3]}")
            product_info = api.get_products_info(
                product_list=product_list_int,
                raw=True
            )
            print(f"DEBUG: get_products_info returned: {type(product_info)}")
        except Exception as e:
            # If metadata fetch fails (rate limiting, session issues), return empty pricing
            print(f"❌ Product metadata fetch failed: {e}")
            import traceback
            traceback.print_exc()
            return {}

        if not isinstance(product_info, dict) or 'data' not in product_info:
            # Return empty pricing instead of throwing error
            print(f"⚠️ Product info invalid format: {type(product_info)}")
            if isinstance(product_info, dict):
                print(f"⚠️ Product info keys: {list(product_info.keys())}")
                print(f"⚠️ Product info content: {product_info}")
            return {}
        
        # Build vwdId mapping for products that support real-time pricing
        vwd_id_to_product_id = {}
        valid_product_ids = []
        
        for product_id in product_ids:
            product_data = product_info['data'].get(str(product_id))
            if product_data:
                vwd_id = product_data.get('vwdId')
                if vwd_id:
                    vwd_id_to_product_id[vwd_id] = product_id
                    valid_product_ids.append(product_id)
        
        if not vwd_id_to_product_id:
            print(f"⚠️ No products with vwdIds found")
            return {}  # No products support real-time pricing

        print(f"✅ Found {len(vwd_id_to_product_id)} products with vwdIds: {list(vwd_id_to_product_id.keys())}")
        
        # Import quotecast components
        from degiro_connector.quotecast.models.ticker import TickerRequest
        from degiro_connector.quotecast.tools.ticker_fetcher import TickerFetcher
        from degiro_connector.quotecast.tools.ticker_to_df import TickerToDF
        import pandas as pd
        
        # Build session and get session ID
        session = TickerFetcher.build_session()
        session_id = TickerFetcher.get_session_id(user_token=user_token)
        
        if not session_id:
            raise HTTPException(
                status_code=503,
                detail="Unable to establish quotecast session"
            )
        
        # Create ticker request for all products with vwdIds
        request_map = {}
        for vwd_id in vwd_id_to_product_id.keys():
            request_map[vwd_id] = ["LastPrice", "BidPrice", "AskPrice"]
        
        ticker_request = TickerRequest(
            request_type="subscription",
            request_map=request_map
        )
        
        # Subscribe and fetch ticker data
        logger = TickerFetcher.build_logger()
        
        TickerFetcher.subscribe(
            ticker_request=ticker_request,
            session_id=session_id,
            session=session,
            logger=logger,
        )
        
        ticker = TickerFetcher.fetch_ticker(
            session_id=session_id,
            session=session,
            logger=logger,
        )
        
        if not ticker:
            print(f"⚠️ No ticker data received")
            return {}  # No real-time data available

        print(f"✅ Received ticker data")
        
        # Parse ticker data
        ticker_to_df = TickerToDF()
        df = ticker_to_df.parse(ticker=ticker)
        
        if df is None or len(df) == 0:
            print(f"⚠️ Empty price data from ticker")
            return {}  # Empty price data

        print(f"✅ Parsed {len(df)} price records")
        
        # Extract prices for each product.
        # NOTE: TickerToDF returns a Polars DF indexed by `product_id` which in this workflow is the VWD id
        # used in the ticker request. We must map VWD id -> DeGiro product_id.
        results: dict[str, PriceInfo] = {}

        def _as_float(x: Any) -> Optional[float]:
            try:
                if x is None:
                    return None
                v = float(x)
                # guard against nan/inf without importing numpy
                if v != v or v == float("inf") or v == float("-inf"):
                    return None
                return v
            except Exception:
                return None

        # Avoid `.to_pandas()` (requires `pyarrow`); iterate via dicts instead.
        for rec in df.to_dicts():
            vwd_id = str(rec.get("product_id") or "")
            if not vwd_id:
                continue
            degiro_pid = vwd_id_to_product_id.get(vwd_id)
            if not degiro_pid:
                continue

            last = _as_float(rec.get("LastPrice"))
            bid = _as_float(rec.get("BidPrice"))
            ask = _as_float(rec.get("AskPrice"))
            if last is None:
                continue

            results[str(degiro_pid)] = PriceInfo(
                bid=round(bid, 2) if bid is not None else None,
                ask=round(ask, 2) if ask is not None else None,
                last=round(last, 2) if last is not None else None,
            )

        if results:
            cache_time = datetime.now().isoformat()
            for cache_product_id, cache_price in results.items():
                price_cache[str(cache_product_id)] = {"price": cache_price, "timestamp": cache_time}

        print(f"✅ Successfully got prices for {len(results)} products")
        return results
        
    except HTTPException:
        raise  # Re-raise HTTPException as-is
    except Exception as e:
        print(f"Batch price fetch failed: {e}")
        import traceback
        traceback.print_exc()
        return {}  # Return empty dict instead of raising error

def get_real_price(product_id: str) -> PriceInfo:
    """Get real price data from DEGIRO using quotecast API"""
    try:
        # First get user token from trading API session
        api = get_fresh_trading_api()
        
        # Get product info to determine the correct vwdId for quotecast
        product_info = api.get_products_info(
            product_list=[int(product_id)],
            raw=True
        )
        
        if not isinstance(product_info, dict) or 'data' not in product_info:
            raise HTTPException(
                status_code=503,
                detail=f"Unable to fetch product metadata for {product_id}"
            )
            
        product_data = product_info['data'].get(str(product_id))
        if not product_data:
            raise HTTPException(
                status_code=404,
                detail=f"Product {product_id} not found"
            )
        
        # Check if product has a vwdId for real-time pricing
        vwd_id = product_data.get('vwdId')
        if not vwd_id:
            raise HTTPException(
                status_code=503,
                detail=f"Product {product_id} does not support real-time pricing (no vwdId)"
            )
        
        # Import quotecast components
        from degiro_connector.quotecast.models.ticker import TickerRequest
        from degiro_connector.quotecast.tools.ticker_fetcher import TickerFetcher
        from degiro_connector.quotecast.tools.ticker_to_df import TickerToDF
        import pandas as pd
        
        # Get user token from config file
        try:
            with open(DEGIRO_CONFIG_PATH, 'r') as f:
                config_dict = json.load(f)
            user_token = config_dict.get("user_token")
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail=f"Unable to load user token from config: {str(e)}"
            )
        
        if not user_token:
            raise HTTPException(
                status_code=503,
                detail="No valid user token found in config for real-time pricing"
            )
        
        # Build session and get session ID
        session = TickerFetcher.build_session()
        session_id = TickerFetcher.get_session_id(user_token=user_token)
        
        if not session_id:
            raise HTTPException(
                status_code=503,
                detail="Unable to establish quotecast session"
            )
        
        # Create ticker request for this product
        ticker_request = TickerRequest(
            request_type="subscription",
            request_map={
                vwd_id: [
                    "LastPrice",
                    "BidPrice", 
                    "AskPrice"
                ]
            }
        )
        
        # Subscribe and fetch ticker data
        logger = TickerFetcher.build_logger()
        
        TickerFetcher.subscribe(
            ticker_request=ticker_request,
            session_id=session_id,
            session=session,
            logger=logger,
        )
        
        ticker = TickerFetcher.fetch_ticker(
            session_id=session_id,
            session=session,
            logger=logger,
        )
        
        if not ticker:
            raise HTTPException(
                status_code=503,
                detail=f"No real-time data available for product {product_id}"
            )
        
        # Parse ticker data
        ticker_to_df = TickerToDF()
        df = ticker_to_df.parse(ticker=ticker)
        
        if df is None or df.empty:
            raise HTTPException(
                status_code=503,
                detail=f"Empty price data received for product {product_id}"
            )
        
        # Extract price data from dataframe (we only requested one product)
        if len(df) == 0:
            raise HTTPException(
                status_code=503,
                detail=f"No price data returned for product {product_id}"
            )
        
        # Get first row data
        row_dict = df.to_pandas().iloc[0].to_dict()
        last_price = row_dict.get('LastPrice', None)
        bid_price = row_dict.get('BidPrice', None) 
        ask_price = row_dict.get('AskPrice', None)
        
        # Validate that we have at least a last price
        if last_price is None or pd.isna(last_price):
            raise HTTPException(
                status_code=503,
                detail=f"No valid last price available for product {product_id}"
            )
        
        # Convert to float and handle missing bid/ask - NO FAKE DATA
        last = float(last_price) if last_price is not None and not pd.isna(last_price) else None
        bid = float(bid_price) if bid_price is not None and not pd.isna(bid_price) else None
        ask = float(ask_price) if ask_price is not None and not pd.isna(ask_price) else None
        
        return PriceInfo(
            bid=round(bid, 2) if bid is not None else None,
            ask=round(ask, 2) if ask is not None else None,
            last=round(last, 2) if last is not None else None
        )
        
    except HTTPException:
        raise  # Re-raise HTTPException as-is
    except Exception as e:
        print(f"Real price fetch failed for {product_id}: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=503, 
            detail=f"Unable to fetch real-time price for product {product_id}. Error: {str(e)}"
        )

def extract_issuer(product_name: str) -> str:
    """Extract issuer from product name"""
    if product_name.startswith("BNP"):
        return "BNP"
    elif product_name.startswith("SG"):
        return "SG"
    else:
        return "Unknown"

def load_nasdaq_mapping() -> dict:
    """Load NASDAQ 100 mapping for symbol lookups"""
    try:
        # Get the directory of this file and build relative path
        current_dir = os.path.dirname(os.path.abspath(__file__))
        mapping_path = os.path.join(current_dir, '..', 'docs', 'nasdaq100_degiro_mapping.json')
        mapping_path = os.path.normpath(mapping_path)
        
        with open(mapping_path, 'r') as f:
            data = json.load(f)
        
        # Create symbol to stock mapping
        symbol_map = {}
        for stock in data.get('all_stocks', []):
            if stock.get('symbol') and stock.get('degiro_id'):
                symbol_map[stock['symbol']] = stock
        return symbol_map
    except Exception as e:
        print(f"Warning: Could not load NASDAQ mapping: {e}")
        return {}

def get_volume_data(symbol: str, degiro_id: str, vwd_id: str, _retry_depth: int = 0) -> VolumeResponse:
    """
    Get real-time volume data for a symbol using DEGIRO quotecast API

    Args:
        _retry_depth: Internal counter to prevent infinite retry loops (max 1 retry)
    """
    try:
        from degiro_connector.quotecast.models.ticker import TickerRequest
        from degiro_connector.quotecast.tools.ticker_fetcher import TickerFetcher
        from degiro_connector.quotecast.tools.ticker_to_df import TickerToDF
        
        # Use the existing trading API session to get user token
        api = get_trading_api()  # This ensures we have an active session
        
        # Get user token from config using the same method as main API
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', 'config.json')
        config_path = os.path.normpath(config_path)
        with open(config_path, 'r') as f:
            config_dict = json.load(f)
        user_token = config_dict.get("user_token")
        
        if not user_token:
            raise HTTPException(status_code=503, detail="No user token available")
        
        # Build session
        session = TickerFetcher.build_session()
        session_id = TickerFetcher.get_session_id(user_token=user_token)
        
        if not session_id:
            raise HTTPException(status_code=503, detail="Unable to establish quotecast session")
        
        # Create ticker request for volume data
        ticker_request = TickerRequest(
            request_type="subscription",
            request_map={
                vwd_id: [
                    "LastVolume",
                    "CumulativeVolume",
                    "LastTime",
                    "LastDate"
                ]
            }
        )
        
        # Subscribe and fetch with longer timeout for VPS stability
        logger = TickerFetcher.build_logger()
        TickerFetcher.subscribe(
            ticker_request=ticker_request,
            session_id=session_id,
            session=session,
            logger=logger,
        )
        
        # Increased timeout for VPS network conditions
        ticker = TickerFetcher.fetch_ticker(
            session_id=session_id,
            session=session,
            logger=logger,
        )
        
        if not ticker:
            raise HTTPException(status_code=503, detail=f"No volume data available for {symbol}")
        
        # Parse the raw JSON response manually since ticker.data doesn't work properly
        
        try:
            parsed_data = json.loads(ticker.json_text)
            
            # Parse DEGIRO's field mapping and values
            field_map = {}
            values = {}
            
            for item in parsed_data:
                if item['m'] == 'a_req':
                    # Field mapping: field_name -> field_id
                    field_name, field_id = item['v']
                    if field_name.startswith(vwd_id):
                        field_map[field_id] = field_name.split('.')[-1]
                elif item['m'] == 'un':
                    # Numeric value: field_id -> value
                    field_id, value = item['v']
                    values[field_id] = value
                elif item['m'] == 'us':
                    # String value: field_id -> string_value
                    field_id, value = item['v']
                    values[field_id] = value
            
            # Extract volume data
            cumulative_volume = 0
            last_volume = 0
            
            for field_id, field_name in field_map.items():
                if field_name == 'CumulativeVolume' and field_id in values:
                    cumulative_volume = int(values[field_id])
                elif field_name == 'LastVolume' and field_id in values:
                    last_volume = int(values[field_id])
            
            if cumulative_volume == 0 and last_volume == 0:
                raise HTTPException(status_code=503, detail=f"No volume data found for {symbol}")
                
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise HTTPException(status_code=503, detail=f"Failed to parse volume data for {symbol}: {str(e)}")
        
        # Calculate time-based metrics (simplified - always return current daily data)
        et_now = datetime.now(ZoneInfo('America/New_York'))
        market_open = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
        
        # Calculate elapsed minutes from market open
        if et_now < market_open:
            # Before market open - use previous day
            market_open = market_open.replace(day=market_open.day - 1)
        
        elapsed_minutes = max(1, (et_now - market_open).total_seconds() / 60)
        volume_rate = cumulative_volume / elapsed_minutes if elapsed_minutes > 0 else 0
        
        # Get current price using existing price functionality
        price_info = get_real_prices_batch([degiro_id]).get(degiro_id)
        
        if not price_info:
            # No fake data - return None values
            price_info = PriceInfo(bid=None, ask=None, last=None)
        
        return VolumeResponse(
            symbol=symbol,
            current_time=et_now.isoformat(),
            market_open_time=market_open.isoformat(),
            elapsed_minutes=elapsed_minutes,
            cumulative_volume=cumulative_volume,
            last_volume=last_volume,
            volume_rate_per_minute=volume_rate,
            degiro_vwd_id=vwd_id,
            degiro_id=degiro_id,
            current_price=price_info,
            timestamp=datetime.now().isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)

        # Detect session expiry and attempt reconnection (prevent infinite loops)
        if is_session_expired(error_msg) and _retry_depth == 0:
            print(f"⚠️  Session expired during volume fetch for {symbol} - attempting reconnect...")
            try:
                reconnect_trading_api()
                # Retry the volume fetch after reconnection (single retry only via _retry_depth)
                return get_volume_data(symbol, degiro_id, vwd_id, _retry_depth=1)
            except Exception as retry_error:
                raise HTTPException(
                    status_code=503,
                    detail=f"Session expired and reconnect failed for {symbol}: {str(retry_error)}"
                )

        raise HTTPException(status_code=503, detail=f"Volume data fetch failed for {symbol}: {error_msg}")


def filter_by_product_subtype(products: list, subtype: str) -> list:
    """Filter leveraged products by subtype"""
    normalized_subtype = str(subtype or "ALL").strip().upper().replace("-", "_")
    if normalized_subtype == "ALL":
        return products
    
    filtered_products = []
    
    for product in products:
        name = product.get('name', '').lower()
        
        if normalized_subtype == "CALL_PUT":
            # Optionsscheine: Traditional Call/Put options with STR (Strike) pattern
            if ('call str' in name or 'put str' in name) and 'mini' not in name and 'unlimited' not in name:
                filtered_products.append(product)
        
        elif normalized_subtype == "MINI":
            # Knockouts: Mini Long/Short products with Stop Loss
            if 'mini long' in name or 'mini short' in name:
                filtered_products.append(product)
        
        elif normalized_subtype == "UNLIMITED":
            # Faktor: Unlimited Long/Short products (factor certificates)
            if 'unlimited long' in name or 'unlimited short' in name:
                filtered_products.append(product)
    
    return filtered_products


LONG_KNOCKOUT_NAME_RE = re.compile(r"\b(?:LONG|CALL)\b", re.IGNORECASE)
KO_STYLE_NAME_RE = re.compile(r"\b(?:TURBO|MINI|KNOCK[- ]?OUT|OPEN[- ]?END|UNLIMITED|BEST)\b", re.IGNORECASE)
REJECTED_LEVERAGED_NAME_RE = re.compile(
    r"\b(?:SHORT|PUT|FAKTOR|FACTOR|WARRANT|DISCOUNT|OPTIONSSCHEIN(?:E)?|OPTIONS?SCHEIN(?:E)?)\b",
    re.IGNORECASE,
)
KNOCKOUT_BASIS_RE = re.compile(r"\b(?:BP|STR|BAR)\s+([0-9]+(?:[.,][0-9]+)?)\b", re.IGNORECASE)


def is_supported_knockout_product(product: Dict[str, Any], *, action: str) -> bool:
    """Return true for tradable DEGIRO KO/turbo/open-end products only."""
    if not product.get("tradable", False):
        return False

    target_direction = "L" if str(action).upper() == "LONG" else "S"
    shortlong = normalize_shortlong(product.get("shortlong"))
    if shortlong and shortlong != target_direction:
        return False

    name = str(product.get("name") or "")
    if not name or REJECTED_LEVERAGED_NAME_RE.search(name):
        return False
    if str(action).upper() == "LONG" and not LONG_KNOCKOUT_NAME_RE.search(name):
        return False
    return bool(KO_STYLE_NAME_RE.search(name))


def knockout_basis_price(product: Dict[str, Any]) -> Optional[float]:
    """Extract BP/STR/BAR basis from DEGIRO KO product names."""
    name = str(product.get("name") or "")
    matches = KNOCKOUT_BASIS_RE.findall(name)
    if not matches:
        return None
    # Prefer BP when present, then STR, then BAR. The regex scans in name order,
    # and SG turbo names usually include BAR before BP, so choose explicitly.
    for label in ("BP", "STR", "BAR"):
        match = re.search(rf"\b{label}\s+([0-9]+(?:[.,][0-9]+)?)\b", name, re.IGNORECASE)
        if match:
            return _positive_float(match.group(1))
    return _positive_float(matches[0])


def approximate_long_leverage(product: Dict[str, Any], underlying_price: Optional[float]) -> Optional[float]:
    native = _positive_float(product.get("leverage"))
    if native is not None:
        return native
    if underlying_price is None or underlying_price <= 0:
        return None
    basis = knockout_basis_price(product)
    if basis is None or basis <= 0 or basis >= underlying_price:
        return None
    return float(underlying_price) / (float(underlying_price) - basis)


def price_info_value(price: Any) -> Optional[float]:
    for field in ("ask", "last", "bid"):
        value = getattr(price, field, None)
        parsed = _positive_float(value)
        if parsed is not None:
            return parsed
    return None


def normalize_shortlong(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().upper()
    if text in {"1", "L", "LONG", "BUY"}:
        return "L"
    if text in {"0", "S", "SHORT", "SELL"}:
        return "S"
    return text


def product_fallback_price(product: Dict[str, Any]) -> tuple[PriceInfo, str]:
    """Best-effort price from DEGIRO product-search metadata.

    Quotecast often returns no ticks outside market hours. Product search still
    carries usable indicative/close fields for many certificates, which is good
    enough for discovery and dry-run order sizing.
    """
    direct_fields = (
        ("ask", "ask"),
        ("askPrice", "ask"),
        ("bid", "bid"),
        ("bidPrice", "bid"),
        ("last", "last"),
        ("lastPrice", "last"),
        ("price", "last"),
        ("closePrice", "last"),
        ("close", "last"),
        ("previousClose", "last"),
        ("indicativePrice", "last"),
    )
    values: dict[str, float] = {}
    source = ""
    for field, target in direct_fields:
        price = _positive_float(product.get(field))
        if price is None:
            continue
        values.setdefault(target, price)
        source = source or field

    for container_key in ("currentPrice", "current_price", "quote", "priceInfo"):
        nested = product.get(container_key)
        if not isinstance(nested, dict):
            continue
        for field, target in direct_fields:
            price = _positive_float(nested.get(field))
            if price is None:
                continue
            values.setdefault(target, price)
            source = source or f"{container_key}.{field}"

    price = PriceInfo(
        bid=round(values["bid"], 2) if "bid" in values else None,
        ask=round(values["ask"], 2) if "ask" in values else None,
        last=round(values["last"], 2) if "last" in values else None,
    )
    return price, source


def _positive_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        parsed = float(str(value).replace(",", "."))
    except Exception:
        return None
    if parsed <= 0 or parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    return parsed

def create_degiro_order(request: OrderRequest) -> Order:
    """Create DEGIRO Order object from request"""
    from degiro_connector.trading.models.order import Action, OrderType, TimeType
    
    # Action (use buy_sell parameter not action)
    if request.action.upper() == "BUY":
        buy_sell = Action.BUY
    elif request.action.upper() == "SELL":
        buy_sell = Action.SELL
    else:
        raise ValueError(f"Invalid action: {request.action}")
    
    # Order Type
    if request.order_type.upper() == "LIMIT":
        order_type = OrderType.LIMIT
        if request.price is None:
            raise ValueError("Price required for LIMIT orders")
    elif request.order_type.upper() == "MARKET":
        order_type = OrderType.MARKET
    elif request.order_type.upper() == "STOP_LOSS":
        order_type = OrderType.STOP_LOSS
        if request.stop_price is None:
            raise ValueError("Stop price required for STOP_LOSS orders")
    elif request.order_type.upper() == "STOP_LIMIT":
        order_type = OrderType.STOP_LIMIT
        if request.price is None or request.stop_price is None:
            raise ValueError("Both price and stop_price required for STOP_LIMIT orders")
    else:
        raise ValueError(f"Invalid order type: {request.order_type}")
    
    # Time Type
    if request.time_type.upper() == "DAY":
        time_type = TimeType.GOOD_TILL_DAY
    elif request.time_type.upper() == "GTC":
        time_type = TimeType.GOOD_TILL_CANCELED
    else:
        raise ValueError(f"Invalid time type: {request.time_type}")
    
    # Create Order with correct parameter names
    order = Order(
        buy_sell=buy_sell,
        order_type=order_type,
        product_id=int(request.product_id),
        size=request.quantity,
        time_type=time_type
    )
    
    # Set price/stop_price if needed
    if request.price is not None:
        order.price = request.price
    if request.stop_price is not None:
        order.stop_price = request.stop_price
    
    return order

# === API ROUTES ===

@app.get("/")
async def root(api_key: str = Depends(verify_api_key)):
    """
    API information - requires authentication

    Access via:
    - Authorization: Bearer YOUR_API_KEY header
    - Query parameter: /?api_key=YOUR_API_KEY
    """
    return {
        "service": "DEGIRO Trading API",
        "version": "2.0.0",
        "status": "online",
        "features": [
            "Universal product search (ISIN, name, ticker)",
            "Leveraged products discovery",
            "Complete order management (LIMIT, MARKET, STOP_LOSS, STOP_LIMIT)",
            "Order validation and confirmation",
            "Real-time order status",
            "Secure API key authentication"
        ],
        "endpoints": {
            "stock_search": "POST /api/stocks/search",
            "leveraged_search": "POST /api/leveraged/search",
            "legacy_search": "POST /api/products/search",
            "volume_data": "GET /api/volume/opening/{symbol}",
            "price_data": "GET /api/price/current/{symbol}",
            "check_order": "POST /api/orders/check",
            "place_order": "POST /api/orders/place"
        },
        "documentation": "/docs",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html(verified_key: str = Depends(verify_api_key)):
    """
    Swagger UI documentation - requires authentication

    Access via: /docs?api_key=YOUR_API_KEY
    """
    return get_swagger_ui_html(
        openapi_url=f"/openapi.json?api_key={verified_key}",
        title=app.title + " - Swagger UI",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )

@app.get("/openapi.json", include_in_schema=False)
async def get_open_api_endpoint(api_key: str = Depends(verify_api_key)):
    """
    OpenAPI schema - requires authentication

    Access via: /openapi.json?api_key=YOUR_API_KEY
    """
    return get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

# NEW API ENDPOINTS

@app.post("/api/stocks/search", response_model=StockSearchResponse)
async def search_stocks(
    request: StockSearchRequest,
    api_key: str = Depends(verify_api_key_header_only)
):
    """
    Search for stocks - returns ALL matching stock options for disambiguation
    
    - **q**: Search query (ISIN, company name, ticker, symbol)
    - **limit**: Maximum number of stocks to return (default: 50)
    
    Returns list of all matching stocks for user/agent selection.
    """
    
    if not request.q or not request.q.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query parameter 'q' is required"
        )
    
    api = get_fresh_trading_api()
    
    # Search for all matching stocks
    stock_products = search_stocks_multiple(api, request.q.strip(), request.limit)
    
    # Get real prices for all stock products in batch
    stock_product_ids = [str(product.get('id', '')) for product in stock_products if product.get('id')]
    stock_real_prices = get_real_prices_batch(stock_product_ids)
    
    # Convert to response format (pricing is best-effort; may be empty outside market hours)
    stock_options = []
    for product in stock_products:
        product_id = str(product.get('id', ''))

        stock_option = StockOption(
            product_id=product_id,
            name=product.get('name', ''),
            isin=product.get('isin', ''),
            symbol=product.get('symbol'),
            currency=product.get('currency', 'EUR'),
            exchange_id=str(product.get('exchangeId', '')),
            current_price=stock_real_prices.get(product_id, PriceInfo()),
            tradable=product.get('tradable', True),
        )
        stock_options.append(stock_option)
    
    return StockSearchResponse(
        query=request.q,
        stocks=stock_options,
        total_found=len(stock_options),
        timestamp=datetime.now().isoformat()
    )

@app.post("/api/leveraged/search", response_model=LeveragedSearchResponse)
async def search_leveraged_products(
    request: LeveragedSearchRequest,
    api_key: str = Depends(verify_api_key_header_only)
):
    """
    Search for leveraged products based on specific underlying stock
    
    - **underlying_id**: Stock product ID from stocks search
    - **action**: LONG or SHORT (default: LONG)
    - **min_leverage**: Minimum leverage (default: 2.0)
    - **max_leverage**: Maximum leverage (default: 10.0)
    - **limit**: Max leveraged products to return (default: 50)
    - **product_subtype**: Filter by product type:
      - **ALL**: All leveraged products (default)
      - **CALL_PUT**: Optionsscheine (traditional call/put options)
      - **MINI**: Knockouts (mini long/short with stop loss)
      - **UNLIMITED**: Faktor certificates (unlimited long/short)
    
    Returns leveraged products for the specified underlying stock.
    """
    
    api = get_fresh_trading_api()
    
    try:
        # Get the underlying stock info first
        underlying_id_int = int(request.underlying_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid underlying_id format"
        )
    
    # First, try to get the underlying stock info to get symbol/name
    underlying_stock_info = None
    try:
        product_info = api.get_products_info(
            product_list=[underlying_id_int],
            raw=True
        )
        if isinstance(product_info, dict) and 'data' in product_info:
            underlying_stock_info = product_info['data'].get(str(underlying_id_int))
    except Exception as e:
        print(f"Could not fetch underlying stock info: {e}")

    # Get search term from stock info (symbol or name)
    search_term = ""
    if underlying_stock_info:
        # Try to get symbol or name for better search results
        search_term = underlying_stock_info.get('symbol', underlying_stock_info.get('name', ''))
        print(f"DEBUG: Using search term '{search_term}' for leveraged products")

    # DEGIRO web search uses numeric values: 0=SHORT, 1=LONG.
    shortlong_value = leveraged_direction_query_value(request.action)

    print(f"DEBUG: Set shortlong={shortlong_value} for action={request.action}")

    search_terms = leveraged_search_terms(underlying_stock_info)
    print(f"DEBUG: Leveraged query search terms: {search_terms}")

    # Fetch leveraged products using DEGIRO's web query params, without offset pagination.
    all_products = []

    # Fetch a live underlying price before filtering. Turbo/Mini/Unlimited
    # products often have no native DEGIRO leverage field, so we calculate an
    # approximate LONG leverage from the product basis price.
    underlying_prices_for_filter = get_real_prices_batch([str(request.underlying_id)])
    underlying_price_for_filter = price_info_value(underlying_prices_for_filter.get(str(request.underlying_id)))
    if underlying_price_for_filter is None and underlying_stock_info:
        fallback_underlying_price, fallback_source = product_fallback_price(underlying_stock_info)
        underlying_price_for_filter = price_info_value(fallback_underlying_price)
        if underlying_price_for_filter is not None:
            print(f"DEBUG: Using underlying metadata price from {fallback_source}: {underlying_price_for_filter}")

    try:
        all_products = fetch_leveraged_products_by_query(
            api,
            search_terms,
            underlying_product_id=underlying_id_int,
            action=request.action,
            min_leverage=request.min_leverage,
            max_leverage=request.max_leverage,
            limit=request.limit,
        )

        print(f"DEBUG: Fetched {len(all_products)} products from DEGIRO query-param search")

        def filter_leveraged_products(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            filtered: List[Dict[str, Any]] = []

            # Map action to DEGIRO direction value
            target_direction = "L" if request.action.upper() == "LONG" else "S"

            # Debug: Show first product's fields and count by direction
            if products:
                print(f"DEBUG: First product keys: {list(products[0].keys())}")
                print(f"DEBUG: First product sample: leverage={products[0].get('leverage')}, shortlong={products[0].get('shortlong')}, tradable={products[0].get('tradable')}")
                print(f"DEBUG: First product name: {products[0].get('name')}")
                print(f"DEBUG: Target direction: {target_direction}")

                # Count products by direction
                long_count = sum(1 for p in products if p.get('shortlong') == 'L')
                short_count = sum(1 for p in products if p.get('shortlong') == 'S')
                print(f"DEBUG: Product direction counts - LONG: {long_count}, SHORT: {short_count}")

            # Debug: Check first few LONG products
            long_products = [p for p in products if p.get('shortlong') == target_direction]
            if long_products:
                print(f"DEBUG: First 3 LONG products:")
                for i, p in enumerate(long_products[:3], 1):
                    lev = p.get('leverage', 0)
                    print(f"  {i}. {p.get('name')[:50]}, leverage={lev}, tradable={p.get('tradable')}")

            for product in products:
                # Use DEGIRO's native leverage when available. Many KO/turbo
                # products expose only BAR/BP/STR in the name, so estimate LONG
                # leverage from the current underlying price and basis.
                leverage = approximate_long_leverage(product, underlying_price_for_filter)
                if leverage is None:
                    continue

                # Filter by leverage range, direction, tradability, and KO-style product names.
                if (request.min_leverage <= leverage <= request.max_leverage and
                    is_supported_knockout_product(product, action=request.action)):
                    product["_effective_leverage"] = leverage
                    filtered.append(product)
                    if len(filtered) <= 3:
                        print(f"DEBUG: Added product {len(filtered)}: {product.get('name')}, leverage={leverage}, ID={product.get('id')}")

                if len(filtered) >= request.limit:
                    break
            return filtered

        leveraged_products_data = filter_leveraged_products(all_products) if all_products else []

        print(f"DEBUG: After filtering: {len(leveraged_products_data)} products (min_lev={request.min_leverage}, max_lev={request.max_leverage})")
        
        # Filter by product subtype if specified
        if hasattr(request, 'product_subtype') and str(request.product_subtype or "ALL").strip().upper() != "ALL":
            leveraged_products_data = filter_by_product_subtype(leveraged_products_data, request.product_subtype)
        
        # Get underlying stock info for response
        # We need to search for it since we only have the ID
        stock_request = StocksRequest(
            search_text="",  # Search by ID instead
            offset=0,
            limit=1,
            require_total=True,
            sort_columns="name",
            sort_types="asc"
        )
        
        # Get real price for the underlying stock
        underlying_prices = get_real_prices_batch([request.underlying_id])
        
        # Only create underlying stock if we have real pricing data
        underlying_stock = None
        if request.underlying_id in underlying_prices:
            underlying_stock = StockOption(
                product_id=request.underlying_id,
                name=f"Stock ID {request.underlying_id}",
                isin="Unknown",
                symbol=None,
                currency="EUR",
                exchange_id="Unknown",
                current_price=underlying_prices[request.underlying_id],
                tradable=True
            )
        
        # Get real prices for all products in batch
        product_ids = [str(product.get('id', '')) for product in leveraged_products_data if product.get('id')]
        print(f"DEBUG: Filtered {len(leveraged_products_data)} products, attempting to get prices for {len(product_ids)} product IDs")
        if product_ids:
            print(f"DEBUG: First 3 product IDs: {product_ids[:3]}")

        real_prices = get_real_prices_batch(product_ids)

        print(f"DEBUG: Got real prices for {len(real_prices)} / {len(product_ids)} leveraged products")

        product_metadata: Dict[str, Dict[str, Any]] = {}
        missing_price_ids = [product_id for product_id in product_ids if product_id not in real_prices]
        if missing_price_ids:
            try:
                info = api.get_products_info(product_list=[int(pid) for pid in missing_price_ids], raw=True)
                if isinstance(info, dict) and isinstance(info.get("data"), dict):
                    product_metadata = {str(pid): data for pid, data in info["data"].items() if isinstance(data, dict)}
            except Exception as e:
                print(f"DEBUG: Product metadata fallback fetch failed: {e}")

        # Convert to response format. Realtime quotecast is best-effort; after
        # hours, keep valid products if product-search/product-info metadata
        # exposes a usable indicative or close price.
        leveraged_products = []
        for product in leveraged_products_data:
            product_id = str(product.get('id', ''))
            price_source = "quotecast"
            current_price = real_prices.get(product_id)
            if current_price is None:
                current_price, price_source = product_fallback_price(product)
            if current_price.last is None and current_price.bid is None and current_price.ask is None:
                current_price, price_source = product_fallback_price(product_metadata.get(product_id, {}))
                if price_source:
                    price_source = f"metadata.{price_source}"
            if current_price.last is None and current_price.bid is None and current_price.ask is None:
                continue

            if price_source != "quotecast":
                price_cache[product_id] = {"price": current_price, "timestamp": datetime.now().isoformat()}

            effective_leverage = _positive_float(product.get("_effective_leverage")) or _positive_float(product.get('leverage')) or 0.0
            leveraged_product = LeveragedProduct(
                product_id=product_id,
                name=product.get('name', ''),
                isin=product.get('isin', ''),
                leverage=effective_leverage,
                direction="LONG" if normalize_shortlong(product.get('shortlong')) == "L" else "SHORT",
                currency=product.get('currency', 'EUR'),
                exchange_id=str(product.get('exchangeId', '')),
                current_price=current_price,
                tradable=product.get('tradable', False),
                expiration_date=product.get('expirationDate'),
                issuer=extract_issuer(product.get('name', '')),
                price_source=price_source
            )
            leveraged_products.append(leveraged_product)
        
        return LeveragedSearchResponse(
            query={
                "underlying_id": request.underlying_id,
                "action": request.action,
                "min_leverage": request.min_leverage,
                "max_leverage": request.max_leverage,
                "limit": request.limit
            },
            underlying_stock=underlying_stock,
            leveraged_products=leveraged_products,
            total_found=len(leveraged_products),
            timestamp=datetime.now().isoformat()
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Leveraged products search failed: {str(e)}"
        )

# LEGACY ENDPOINT (deprecated but maintained for backward compatibility)
@app.post("/api/products/search", response_model=ProductSearchResponse)
async def search_products(
    request: ProductSearchRequest,
    api_key: str = Depends(verify_api_key_header_only)
):
    """
    DEPRECATED: Universal product search - use /api/stocks/search + /api/leveraged/search instead
    
    This endpoint is maintained for backward compatibility but will be removed in future versions.
    Please migrate to the new 3-endpoint workflow:
    1. POST /api/stocks/search - Get list of stocks
    2. POST /api/leveraged/search - Get leveraged products for specific stock
    3. POST /api/orders/check + /api/orders/place - Place orders
    """
    
    if not request.q or not request.q.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query parameter 'q' is required"
        )
    
    api = get_fresh_trading_api()
    
    # Search for underlying stock (unless specific underlying_id provided)
    stock_product = None
    if not request.underlying_id:
        stock_product = search_stock_universal(api, request.q.strip())
    
    # Prepare direct stock info with real pricing
    direct_stock = None
    if stock_product:
        stock_id = str(stock_product.get('id', ''))
        stock_prices = get_real_prices_batch([stock_id])
        
        # Only create DirectStock if we have real pricing data
        if stock_id in stock_prices:
            direct_stock = DirectStock(
                product_id=stock_id,
                name=stock_product.get('name', ''),
                isin=stock_product.get('isin', ''),
                currency=stock_product.get('currency', 'EUR'),
                exchange_id=str(stock_product.get('exchangeId', '')),
                current_price=stock_prices[stock_id],
                tradable=stock_product.get('tradable', True)
            )
    
    # Set short_long parameter based on action
    if request.short_long is None:
        if request.action.upper() == "SHORT":
            request.short_long = 0
        elif request.action.upper() == "LONG":
            request.short_long = 1

    # Dynamic leveraged products search - uses stock ID as underlying ID
    leveraged_products_data = search_leveraged_products_dynamic(
        api, 
        stock_product,
        request
    )
    
    # Filter by product subtype if specified
    if hasattr(request, 'product_subtype') and request.product_subtype != "ALL":
        leveraged_products_data = filter_by_product_subtype(leveraged_products_data, request.product_subtype)
    
    # Get real prices for all leveraged products in batch
    leveraged_product_ids = [str(product.get('id', '')) for product in leveraged_products_data if product.get('id')]
    leveraged_real_prices = get_real_prices_batch(leveraged_product_ids)
    
    # Convert to response format. Realtime quotecast can be empty outside
    # market/session windows; fall back to product-search metadata just like the
    # dedicated /api/leveraged/search endpoint.
    leveraged_products = []
    for product in leveraged_products_data:
        product_id = str(product.get('id', ''))
        current_price = leveraged_real_prices.get(product_id)
        price_source = "quotecast"
        if current_price is None:
            current_price, price_source = product_fallback_price(product)
        if current_price.last is None and current_price.bid is None and current_price.ask is None:
            continue

        effective_leverage = _positive_float(product.get("_effective_leverage")) or _positive_float(product.get('leverage')) or 0.0
        leveraged_product = LeveragedProduct(
            product_id=product_id,
            name=product.get('name', ''),
            isin=product.get('isin', ''),
            leverage=effective_leverage,
            direction="LONG" if normalize_shortlong(product.get('shortlong')) == "L" else "SHORT",
            currency=product.get('currency', 'EUR'),
            exchange_id=str(product.get('exchangeId', '')),
            current_price=current_price,
            tradable=product.get('tradable', False),
            expiration_date=product.get('expirationDate'),
            issuer=extract_issuer(product.get('name', '')),
            price_source=price_source,
        )
        leveraged_products.append(leveraged_product)
    
    return ProductSearchResponse(
        query={
            "q": request.q,
            "action": request.action,
            "min_leverage": request.min_leverage,
            "max_leverage": request.max_leverage,
            "limit": request.limit
        },
        direct_stock=direct_stock,
        leveraged_products=leveraged_products,
        total_found={
            "direct_stock": 1 if direct_stock else 0,
            "leveraged_products": len(leveraged_products)
        },
        timestamp=datetime.now().isoformat()
    )

@app.post("/api/orders/check", response_model=OrderCheckResponse)
async def check_order(
    request: OrderRequest,
    api_key: str = Depends(verify_api_key_header_only)
):
    """
    Validate order before placement - returns estimated costs and confirmation ID
    
    - **product_id**: Product ID to trade
    - **action**: BUY or SELL
    - **order_type**: LIMIT, MARKET, STOP_LOSS, STOP_LIMIT
    - **quantity**: Number of shares/units
    - **price**: Limit price (required for LIMIT/STOP_LIMIT)
    - **stop_price**: Stop price (required for STOP_LOSS/STOP_LIMIT)
    - **time_type**: DAY or GTC
    """
    
    api = get_fresh_trading_api()
    
    try:
        # Create DEGIRO order
        order = create_degiro_order(request)
        
        # Check order with DEGIRO (auto-reconnect on expired sessions)
        try:
            checking_response = api.check_order(order=order)
        except Exception as e:
            if is_session_expired(str(e)) or "connection required" in str(e).lower():
                api = reconnect_trading_api()
                checking_response = api.check_order(order=order)
            else:
                raise
        
        # Parse response
        if checking_response and hasattr(checking_response, 'confirmation_id'):
            return OrderCheckResponse(
                valid=True,
                confirmation_id=checking_response.confirmation_id,
                estimated_fee=getattr(checking_response, 'transaction_fee', None),
                total_cost=None,  # Calculate if needed
                free_space_new=getattr(checking_response, 'free_space_new', None),
                message="Order validation successful"
            )
        else:
            return OrderCheckResponse(
                valid=False,
                message="Order validation failed",
                errors=["Unknown validation error"]
            )
            
    except ValueError as e:
        return OrderCheckResponse(
            valid=False,
            message="Order validation failed",
            errors=[str(e)]
        )
    except Exception as e:
        return OrderCheckResponse(
            valid=False,
            message="Order validation failed",
            errors=[f"DEGIRO error: {str(e)}"]
        )

@app.post("/api/orders/place", response_model=OrderResponse)
async def place_order(
    request: OrderRequest,
    api_key: str = Depends(verify_api_key_header_only)
):
    """
    Place order after validation - requires valid confirmation ID from check_order
    
    This endpoint performs a two-step process:
    1. Validates the order with DEGIRO
    2. Confirms and places the order
    """
    
    api = get_fresh_trading_api()
    
    try:
        # Create DEGIRO order
        order = create_degiro_order(request)
        
        # Step 1: Check order (auto-reconnect on expired sessions)
        try:
            checking_response = api.check_order(order=order)
        except Exception as e:
            if is_session_expired(str(e)) or "connection required" in str(e).lower():
                api = reconnect_trading_api()
                checking_response = api.check_order(order=order)
            else:
                raise
        
        if not checking_response or not hasattr(checking_response, 'confirmation_id'):
            return OrderResponse(
                success=False,
                message="Order validation failed",
                product_id=request.product_id,
                action=request.action,
                order_type=request.order_type,
                quantity=request.quantity,
                price=request.price,
                stop_price=request.stop_price,
                created_at=datetime.now().isoformat()
            )
        
        # Step 2: Confirm order
        try:
            confirmation_response = api.confirm_order(
                confirmation_id=checking_response.confirmation_id,
                order=order
            )
        except Exception as e:
            if is_session_expired(str(e)) or "connection required" in str(e).lower():
                api = reconnect_trading_api()
                confirmation_response = api.confirm_order(
                    confirmation_id=checking_response.confirmation_id,
                    order=order
                )
            else:
                raise
        
        if confirmation_response and hasattr(confirmation_response, 'order_id'):
            return OrderResponse(
                success=True,
                order_id=confirmation_response.order_id,
                confirmation_id=checking_response.confirmation_id,
                message="Order placed successfully",
                product_id=request.product_id,
                action=request.action,
                order_type=request.order_type,
                quantity=request.quantity,
                price=request.price,
                stop_price=request.stop_price,
                estimated_fee=getattr(checking_response, 'transaction_fee', None),
                created_at=datetime.now().isoformat()
            )
        else:
            return OrderResponse(
                success=False,
                message="Order confirmation failed",
                product_id=request.product_id,
                action=request.action,
                order_type=request.order_type,
                quantity=request.quantity,
                price=request.price,
                stop_price=request.stop_price,
                created_at=datetime.now().isoformat()
            )
            
    except ValueError as e:
        return OrderResponse(
            success=False,
            message=f"Invalid order parameters: {str(e)}",
            product_id=request.product_id,
            action=request.action,
            order_type=request.order_type,
            quantity=request.quantity,
            price=request.price,
            stop_price=request.stop_price,
            created_at=datetime.now().isoformat()
        )
    except Exception as e:
        return OrderResponse(
            success=False,
            message=f"Order placement failed: {str(e)}",
            product_id=request.product_id,
            action=request.action,
            order_type=request.order_type,
            quantity=request.quantity,
            price=request.price,
            stop_price=request.stop_price,
            created_at=datetime.now().isoformat()
        )

# VOLUME AND PRICE ENDPOINTS FOR ORB STRATEGY

@app.get("/api/volume/opening/{symbol}", response_model=VolumeResponse)
async def get_volume_opening(
    symbol: str,
    api_key: str = Depends(verify_api_key_header_only)
):
    """
    Get current daily volume data for a NASDAQ stock
    
    Returns real-time volume metrics without time restrictions.
    The caller handles timing logic (e.g., 9:35 AM ORB strategy).
    
    - **symbol**: NASDAQ stock symbol (e.g., AAPL, GOOGL, MSFT)
    
    Returns cumulative daily volume and volume rate calculations.
    """
    
    # Load NASDAQ mapping
    nasdaq_mapping = load_nasdaq_mapping()
    
    symbol_upper = symbol.upper()
    if symbol_upper not in nasdaq_mapping:
        raise HTTPException(
            status_code=404,
            detail=f"Symbol {symbol} not found in NASDAQ 100 mapping"
        )
    
    stock_info = nasdaq_mapping[symbol_upper]
    degiro_id = stock_info.get('degiro_id')
    vwd_id = stock_info.get('degiro_vwd_id')
    
    if not degiro_id or not vwd_id:
        raise HTTPException(
            status_code=503,
            detail=f"No DEGIRO mapping available for {symbol}"
        )
    
    # Get real-time volume data
    return get_volume_data(symbol_upper, degiro_id, vwd_id)

@app.get("/api/volume/nasdaq", response_model=NasdaqBatchResponse)
async def get_nasdaq_batch_volume(
    api_key: str = Depends(verify_api_key_header_only)
):
    """
    Get real-time volume and price data for all 101 NASDAQ 100 stocks
    
    Returns volume metrics and current prices for the entire NASDAQ 100 in one call.
    Optimized for batch processing with concurrent data fetching.
    
    Perfect for market scanners and bulk ORB strategy analysis.
    """
    
    # Use existing trading API session
    api = get_fresh_trading_api()
    
    # Load NASDAQ mapping
    nasdaq_mapping = load_nasdaq_mapping()
    
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    # Time calculations (same for all stocks)
    et_now = datetime.now(ZoneInfo('America/New_York'))
    market_open = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
    
    if et_now < market_open:
        market_open = market_open.replace(day=market_open.day - 1)
    
    elapsed_minutes = max(1, (et_now - market_open).total_seconds() / 60)
    
    # Get all stock prices in batch first (more efficient)
    all_degiro_ids = [stock_info.get('degiro_id') for stock_info in nasdaq_mapping.values() if stock_info.get('degiro_id')]
    batch_prices = get_real_prices_batch(all_degiro_ids)
    
    def get_single_volume_data(symbol_data):
        """Get volume data for a single stock (no retries - handled by get_volume_data)"""
        symbol, stock_info = symbol_data

        try:
            # Add random delay to avoid DEGIRO rate limiting (looks more human)
            import time
            import random
            time.sleep(random.uniform(0.5, 1.5))

            degiro_id = stock_info.get('degiro_id')
            vwd_id = stock_info.get('degiro_vwd_id')

            if not degiro_id or not vwd_id:
                return None

            # Get volume data using existing function (session expiry handled internally)
            volume_response = get_volume_data(symbol, degiro_id, vwd_id)

            # Override price with batch-fetched price for efficiency
            if degiro_id in batch_prices:
                volume_response.current_price = batch_prices[degiro_id]

            return volume_response

        except Exception as e:
            print(f"Failed to get volume data for {symbol}: {e}")
            return None

    # Process all stocks concurrently (reduced workers to avoid DEGIRO rate limiting)
    stocks_data = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        # Submit all tasks
        future_to_symbol = {
            executor.submit(get_single_volume_data, (symbol, stock_info)): symbol 
            for symbol, stock_info in nasdaq_mapping.items()
        }
        
        # Collect results
        for future in as_completed(future_to_symbol):
            result = future.result()
            if result:
                stocks_data.append(result)
    
    # Sort by symbol for consistent ordering
    stocks_data.sort(key=lambda x: x.symbol)
    
    return NasdaqBatchResponse(
        market_open_time=market_open.isoformat(),
        current_time=et_now.isoformat(), 
        elapsed_minutes=elapsed_minutes,
        stocks=stocks_data,
        total_stocks=len(stocks_data),
        timestamp=datetime.now().isoformat()
    )

@app.get("/api/price/current/{symbol}", response_model=PriceResponse)
async def get_price_current(
    symbol: str,
    api_key: str = Depends(verify_api_key_header_only)
):
    """
    Get current price data for a NASDAQ stock using stocks/search relay
    
    This endpoint uses the stocks/search functionality to get DEGIRO product_id
    and current price data, then formats it as OHLC response for ORB strategy.
    
    - **symbol**: NASDAQ stock symbol (e.g., AAPL, GOOGL, MSFT, PYPL)
    
    Returns current price, OHLC data, volume, and VWAP calculations.
    """
    
    api = get_fresh_trading_api()
    
    try:
        # Use stocks/search to find the symbol and get current price
        stock_products = search_stocks_multiple(api, symbol.upper().strip(), 1)
        
        if not stock_products:
            raise HTTPException(
                status_code=404,
                detail=f"Symbol {symbol} not found in DEGIRO"
            )
        
        # Get the first matching product
        stock_product = stock_products[0]
        product_id = str(stock_product.get('id', ''))
        
        # Get real price using existing batch function. Quotecast can return an
        # empty first tick for a fresh session, so retry briefly before falling
        # back to a recently fetched search price from the same product id.
        import time as _time

        real_prices = {}
        for attempt in range(3):
            real_prices = get_real_prices_batch([product_id])
            if product_id in real_prices:
                break
            if attempt < 2:
                _time.sleep(1)

        if product_id in real_prices:
            price_info = real_prices[product_id]
        else:
            cached_price = price_cache.get(product_id)
            if not cached_price:
                raise HTTPException(
                    status_code=503,
                    detail=f"No real-time price data available for {symbol}"
                )
            price_info = cached_price["price"]

        current_price = price_info.last or price_info.bid or price_info.ask

        if current_price is None:
            raise HTTPException(
                status_code=503,
                detail=f"No valid price data available for {symbol}"
            )
        
        # For ORB strategy, we need OHLC data but we don't have historical data
        # NO FAKE DATA - use current price for all OHLC values (real but incomplete)
        open_price = current_price  # We don't know the real open, use current
        high_price = current_price  # We don't know the real high, use current  
        low_price = current_price   # We don't know the real low, use current
        
        # Volume data - try to get from NASDAQ mapping if available
        nasdaq_mapping = load_nasdaq_mapping()
        volume = None
        vwd_id = None
        
        symbol_upper = symbol.upper()
        if symbol_upper in nasdaq_mapping:
            stock_info = nasdaq_mapping[symbol_upper] 
            degiro_id = stock_info.get('degiro_id')
            vwd_id = stock_info.get('degiro_vwd_id')
            
            # Try to get volume data using existing volume function
            if degiro_id and vwd_id:
                try:
                    volume_response = get_volume_data(symbol_upper, degiro_id, vwd_id)
                    volume = volume_response.cumulative_volume
                except:
                    volume = None  # No fake data
        
        # Calculate VWAP (simplified - in reality this requires historical data)
        vwap = (high_price + low_price + current_price) / 3  # Simplified VWAP
        
        # Time calculations
        et_now = datetime.now(ZoneInfo('America/New_York'))
        market_open = et_now.replace(hour=9, minute=30, second=0, microsecond=0)
        
        if et_now < market_open:
            market_open = market_open.replace(day=market_open.day - 1)
        
        return PriceResponse(
            symbol=symbol.upper(),
            current_price=round(current_price, 2),
            open_price=round(open_price, 2),
            high_price=round(high_price, 2), 
            low_price=round(low_price, 2),
            volume=volume,
            vwap=round(vwap, 2),
            market_open_time=market_open.isoformat(),
            current_time=et_now.isoformat(),
            degiro_vwd_id=vwd_id or f"vwd_{product_id}"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Price data fetch failed for {symbol}: {str(e)}"
        )


@app.get('/api/health')
async def health_check(
    api_key: str = Depends(verify_api_key_header_only),
    deep: bool = Query(default=False, description='If true, performs a real DEGIRO API call to verify the session'),
):
    # Extended health check with DEGIRO connection status.
    degiro_status = 'unknown'
    trading_ok: Optional[bool] = None
    trading_error: Optional[str] = None

    try:
        api = get_trading_api()
        degiro_status = 'connected' if api else 'disconnected'

        if deep and api:
            try:
                trading_ok = ping_trading_api(api)
                if not trading_ok:
                    trading_error = 'product_search returned no products'
                    degiro_status = 'session_invalid'
                    reset_trading_api()
            except Exception as e:
                trading_ok = False
                trading_error = str(e)[:200]
                if is_session_expired(str(e)):
                    degiro_status = 'session_invalid'
                    reset_trading_api()

    except Exception as e:
        degiro_status = 'connection_failed'
        if deep:
            trading_ok = False
            trading_error = str(e)[:200]

    if deep and trading_ok is False:
        degiro_status = 'session_invalid'

    return {
        'status': 'healthy',
        'degiro_connection': degiro_status,
        'degiro_trading_ok': trading_ok,
        'degiro_trading_error': trading_error,
        'api_version': '2.0.0',
        'timestamp': datetime.now().isoformat(),
    }

if __name__ == "__main__":
    import uvicorn
    
    if not os.getenv("TRADING_API_KEY"):
        print("⚠️  WARNING: Using default API key. Set TRADING_API_KEY environment variable for production!")
    
    # Use custom port for security
    port = int(os.getenv("API_PORT", 7731))
    
    print("🚀 Starting DEGIRO Trading API v2.0...")
    print(f"📊 API Documentation: http://localhost:{port}/docs")
    print(f"🔑 API Key: {API_KEY[:10]}...")
    print(f"🌐 Port: {port}")
    
    uvicorn.run(app, host="0.0.0.0", port=port)
