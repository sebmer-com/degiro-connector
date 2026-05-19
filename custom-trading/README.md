# Custom DEGIRO Trading API

Production-ready FastAPI server for automated DEGIRO trading with 5-endpoint API design.

## 🚀 Features

- **5-Endpoint Trading API**: Complete workflow - search stocks → find leveraged products → validate → place orders
- **Stock Disambiguation**: Returns ALL matching stocks for precise selection
- **Leveraged Product Discovery**: Dynamic search using specific stock IDs as underlying assets
- **Advanced Filtering**: Leverage range, direction (LONG/SHORT), issuer, product subtype controls
- **Product Type Filtering**: Distinguish between Optionsscheine, Knockouts, and unlimited/factor-style products
- **Real-time Pricing**: Live bid/ask/last prices using DEGIRO's quotecast API
- **Order Management**: Two-step validation (check → confirm) for safety
- **Security**: Bearer token authentication with secure credential management
- **Production Ready**: VPS deployment with auto-restart capabilities

## 📁 Structure
```
custom-trading/
├── api/                 # FastAPI trading server
├── scripts/             # Deployment and utility scripts  
├── config/              # Environment configuration (gitignored)
├── openapi.json         # API specification
└── README.md           # This documentation
```

## ⚡ Quick Start

### 1. Environment Setup
```bash
# Copy and configure environment
cp config/.env.example config/.env
# Edit config/.env with your DEGIRO credentials
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Test DEGIRO Connection
```bash
python ../examples/trading/login_2fa.py
```

### 4. Run API
```bash
# Local development
python api/main.py

# Production deployment
./scripts/deploy_to_vps.sh
```

## 🔐 Authentication

All endpoints (except `/api/health`) require Bearer token authentication:

```bash
curl -H "Authorization: Bearer YOUR_API_KEY" \
     -H "Content-Type: application/json" \
     http://your-server:7731/api/endpoint
```

## 📡 API Endpoints Summary

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/health` | GET | Health check and status |
| `/api/stocks/search` | POST | Search for stocks |
| `/api/leveraged/search` | POST | Find leveraged products |
| `/api/products/search` | POST | Universal search (alternative) |
| `/api/volume/opening/{symbol}` | GET | **Real-time volume & price data for ORB strategy** |
| `/api/volume/nasdaq` | GET | **Batch volume & price data for all 101 NASDAQ stocks** |
| `/api/orders/check` | POST | Validate order before placing |
| `/api/orders/place` | POST | Execute validated order |

### Health Check
```http
GET /api/health
```
**Response:**
```json
{
  "status": "healthy",
  "degiro_connection": "connected",
  "api_version": "2.0.0",
  "timestamp": "2025-01-15T10:30:00.123456"
}
```

## 🎯 API WORKFLOW

### Quick Start: Find Leveraged Products

**Step 1: Search Stock**
```bash
curl -H "Authorization: Bearer YOUR_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"q": "PYPL"}' \
     "http://your-server:7731/api/stocks/search"
```

**Step 2: Find Leveraged Products**
```bash
curl -H "Authorization: Bearer YOUR_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "underlying_id": "7201951",
       "action": "LONG",
       "min_leverage": 2.0,
       "max_leverage": 10.0
     }' \
     "http://your-server:7731/api/leveraged/search"
```

**⚠️ Critical Requirements**: 
- Must include BOTH `min_leverage` AND `max_leverage` parameters
- If you get "503: Unable to fetch product metadata" errors, the API needs restart
- Leveraged search depends on active DEGIRO session

### Agent Workflow: Exclude Faktor and Optionsscheine

Use this workflow when screening for 4-6x long products while excluding `Faktor` certificates and classic Optionsscheine:

1. Call `POST /api/stocks/search` with the ticker or ISIN.
2. Select the exact underlying by ISIN first, then by exact ticker symbol. For alternate listings, prefer the underlying ID that returns leveraged products. Example: `3IW` may need the Invesco `IVZ` underlying rather than the German 3IW listing.
3. Fetch DEGIRO leveraged products with one `LeveragedsRequest` using the web query-parameter path:
   - `product_type=560`
   - `underlying_product_id=<stored degiro_id>` as the primary deterministic filter
   - `search_text=<underlying symbol or name>` only as fallback if the ID query returns no products
   - `sub_product_type=14`
   - `instrument_type_id=11`
   - `min_leverage=<requested min>` and `max_leverage=<requested max>` on text fallback requests; ID requests fetch broadly and filter computed leverage locally
   - `shortlong="1"` for LONG or `"0"` for SHORT
   - no `offset` pagination
4. Exclude products whose name contains `Faktor`, `Factor`, `Optionsschein`, `Warrant`, `Discount`, or plain classic option patterns such as `Call STR` / `Put STR` without turbo/mini/unlimited wording.
5. Keep knockout-style names such as `Turbo`, `Mini`, `Unlimited`, `Open-End`, `BEST`, and `X-Unlimited`.
6. Do not rely only on DEGIRO's `leverage` field for non-factor products. Turbo, Mini, and Unlimited products often return `leverage=None` in product search.
7. For non-factor products, calculate approximate leverage from the live underlying price and the basis/strike/barrier parsed from the product name:

```text
approx_long_leverage = underlying_price / (underlying_price - basis_price)
basis_price = BP if present, otherwise STR, otherwise BAR
```

For a 4-6x LONG search, keep products where `4.0 <= approx_long_leverage <= 6.0`. Always re-check bid/ask, stop loss, barrier, and order validity before placing an order.

### Step 1: Stock Search
```http
POST /api/stocks/search
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json

{
  "q": "AAPL",
  "limit": 10
}
```

**Parameters:**
- `q` (string, required): Search query (ticker symbol like "AAPL", "TSLA", "META" work best)
- `limit` (integer, optional): Maximum stocks to return (default: 50)

**💡 Search Tips:**
- Use ticker symbols: "AAPL", "TSLA", "META", "NVDA"  
- Try company names: "Apple", "Tesla", "Microsoft"
- Use ISINs for exact matches: "US0378331005"

**Response:**
```json
{
  "query": "AAPL",
  "stocks": [
    {
      "product_id": "1533610",
      "name": "Apple Inc",
      "isin": "US0378331005",
      "symbol": "AAPL",
      "currency": "USD",
      "exchange_id": "663",
      "current_price": {
        "bid": 150.83,
        "ask": 151.13,
        "last": 150.98
      },
      "tradable": true
    }
  ],
  "total_found": 1,
  "timestamp": "2025-09-19T17:55:40.675561"
}
```

### Step 2: Leveraged Products Search
```http
POST /api/leveraged/search
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json

{
  "underlying_id": "1533610",
  "action": "LONG",
  "min_leverage": 5.0,
  "max_leverage": 10.0,
  "limit": 5,
  "product_subtype": "MINI"
}
```

**Parameters:**
- `underlying_id` (string, required): Stock product ID from Step 1 
- `action` (string, optional): "LONG" or "SHORT" (default: "LONG")
- `min_leverage` (number, **REQUIRED**): Minimum leverage 
- `max_leverage` (number, **REQUIRED**): Maximum leverage
- `limit` (integer, optional): Max leveraged products to return (default: 50)
- `issuer_id` (integer, optional): Issuer filter (-1=all)
- `product_subtype` (string, optional): Filter by product type (default: "ALL")
  - `"ALL"`: All leveraged products
  - `"CALL_PUT"`: **Optionsscheine** - Traditional call/put options with strike price
  - `"MINI"`: **Knockouts** - Mini long/short products with stop loss
  - `"UNLIMITED"`: Unlimited long/short products by name

Note: `product_subtype` is a name-based convenience filter. It is not enough for the "exclude Faktor and Optionsscheine, everything else is ok" workflow because many turbo/open-end products have no native `leverage` value in DEGIRO search results. Use the agent workflow above when exact non-factor 4-6x screening matters.

**Response:**
```json
{
  "query": {
    "underlying_id": "1533610",
    "action": "LONG",
    "min_leverage": 5.0,
    "max_leverage": 10.0,
    "limit": 5
  },
  "underlying_stock": {
    "product_id": "1533610",
    "name": "Apple Inc",
    "isin": "US0378331005",
    "symbol": "AAPL",
    "currency": "USD",
    "exchange_id": "663",
    "current_price": {
      "bid": 150.64,
      "ask": 150.79,
      "last": 150.64
    },
    "tradable": true
  },
  "leveraged_products": [
    {
      "product_id": "101113656",
      "name": "BNP APPLE Call STR 1000 R 0.100 18/06/2026 LV 5.73",
      "isin": "DE000PL63GN8",
      "leverage": 5.732,
      "direction": "LONG",
      "currency": "EUR",
      "exchange_id": "191",
      "current_price": {
        "bid": 48.36,
        "ask": 48.85,
        "last": 48.61
      },
      "tradable": true,
      "expiration_date": "18-6-2026",
      "issuer": "BNP"
    }
  ],
  "total_found": 5,
  "timestamp": "2025-09-19T18:03:40.794046"
}
```

### Alternative: Universal Product Search
```http
POST /api/products/search
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json

{
  "q": "AAPL"
}
```
Returns both stock info and leveraged products in one call.

### Step 3: Order Validation
```http
POST /api/orders/check
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json

{
  "product_id": "101113656",
  "action": "BUY",
  "order_type": "LIMIT",
  "quantity": 5,
  "price": 48.50,
  "time_type": "DAY"
}
```

**Parameters:**
- `product_id` (string, required): Product ID from leveraged search results
- `action` (string, required): "BUY" or "SELL"
- `order_type` (string, optional): "LIMIT", "MARKET", "STOP_LOSS", "STOP_LIMIT" (default: "LIMIT")
- `quantity` (number, required): Number of shares/units
- `price` (number, optional): Limit price (required for LIMIT orders)
- `stop_price` (number, optional): Stop price (required for STOP_LOSS/STOP_LIMIT)
- `time_type` (string, optional): "DAY", "GTC" (default: "DAY")

**Response:**
```json
{
  "valid": true,
  "confirmation_id": "temp_order_789",
  "estimated_fee": 2.50,
  "total_cost": 245.00,
  "free_space_new": 9750.00,
  "message": "Order validation successful",
  "warnings": [],
  "errors": []
}
```

### Step 4: Order Execution
```http
POST /api/orders/place
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json

{
  "product_id": "101113656",
  "action": "BUY",
  "order_type": "LIMIT",
  "quantity": 5,
  "price": 48.50,
  "time_type": "DAY"
}
```

**Response:**
```json
{
  "success": true,
  "order_id": "real_order_456",
  "confirmation_id": "temp_order_789",
  "message": "Order placed successfully",
  "product_id": "101113656",
  "action": "BUY",
  "order_type": "LIMIT",
  "quantity": 5,
  "price": 48.50,
  "estimated_fee": 2.50,
  "total_cost": 245.00,
  "created_at": "2025-09-19T18:05:00.123456"
}
```

## 📊 Real-Time Data Endpoints (ORB Strategy)

### Volume Data Endpoint
```http
GET /api/volume/opening/{symbol}
Authorization: Bearer YOUR_API_KEY
```

**Purpose:** Get current daily volume data and real-time price for NASDAQ 100 stocks. Designed for Opening Range Breakout (ORB) strategies.

**Parameters:**
- `symbol` (path, required): NASDAQ 100 stock symbol (e.g., "AAPL", "WBD", "TSLA")

**Example Request:**
```bash
curl -H "Authorization: Bearer YOUR_API_KEY" \
     "http://your-server:7731/api/volume/opening/WBD"
```

**Response:**
```json
{
  "symbol": "WBD",
  "current_time": "2025-09-26T14:37:10-04:00",
  "market_open_time": "2025-09-26T09:30:00-04:00",
  "elapsed_minutes": 307.2,
  "cumulative_volume": 25743956,
  "last_volume": 100,
  "volume_rate_per_minute": 83808,
  "degiro_vwd_id": "600236482",
  "degiro_id": "22187048",
  "current_price": {
    "bid": 19.76,
    "ask": 19.78,
    "last": 19.77
  },
  "timestamp": "2025-09-26T18:37:10.569848Z"
}
```

**Response Fields:**
- `cumulative_volume`: Total shares traded today
- `last_volume`: Volume of most recent trade
- `volume_rate_per_minute`: Average volume per minute since market open
- `elapsed_minutes`: Minutes elapsed since 9:30 AM ET
- `current_price`: Real-time bid/ask/last price from DEGIRO
- `degiro_vwd_id`: DEGIRO real-time data identifier

**🎯 ORB Strategy Usage:**
- **No time restrictions**: Returns current daily data anytime
- **Caller controls timing**: API doesn't enforce 9:35 AM logic
- **Real-time updates**: Data refreshes every few seconds during market hours
- **NASDAQ 100 coverage**: Supports all 101 NASDAQ 100 symbols
- **Price included**: Real-time bid/ask/last price from existing products/search functionality

**⚡ Performance:**
- Response time: < 500ms
- Real-time DEGIRO data (no mocks/delays)
- Concurrent requests supported

### Batch NASDAQ Volume Data
```http
GET /api/volume/nasdaq
Authorization: Bearer YOUR_API_KEY
```

**Purpose:** Get real-time volume and price data for all 101 NASDAQ 100 stocks in one call. Optimized for market scanners and bulk ORB strategy analysis.

**Example Request:**
```bash
curl -H "Authorization: Bearer YOUR_API_KEY" \
     "http://your-server:7731/api/volume/nasdaq"
```

**Response:**
```json
{
  "market_open_time": "2025-09-26T09:30:00-04:00",
  "current_time": "2025-09-26T14:37:10-04:00", 
  "elapsed_minutes": 307.2,
  "stocks": [
    {
      "symbol": "AAPL",
      "current_time": "2025-09-26T14:37:10-04:00",
      "market_open_time": "2025-09-26T09:30:00-04:00",
      "elapsed_minutes": 307.2,
      "cumulative_volume": 34281888,
      "last_volume": 100,
      "volume_rate_per_minute": 92697,
      "degiro_vwd_id": "360015751",
      "degiro_id": "331868",
      "current_price": {
        "bid": 227.50,
        "ask": 227.52,
        "last": 227.51
      },
      "timestamp": "2025-09-26T18:37:10.569848Z"
    },
    {
      "symbol": "WBD",
      "current_time": "2025-09-26T14:37:10-04:00",
      "market_open_time": "2025-09-26T09:30:00-04:00", 
      "elapsed_minutes": 307.2,
      "cumulative_volume": 25743956,
      "last_volume": 100,
      "volume_rate_per_minute": 83808,
      "degiro_vwd_id": "600236482",
      "degiro_id": "22187048",
      "current_price": {
        "bid": 19.76,
        "ask": 19.78,
        "last": 19.77
      },
      "timestamp": "2025-09-26T18:37:10.569848Z"
    }
  ],
  "total_stocks": 101,
  "timestamp": "2025-09-26T18:37:10.123456Z"
}
```

**Response Fields:**
- `stocks`: Array of VolumeResponse objects for each NASDAQ stock
- `total_stocks`: Number of stocks successfully processed
- `market_open_time`: Market open time (9:30 AM ET)
- `elapsed_minutes`: Minutes elapsed since market open
- Each stock contains volume metrics and real-time price data

**🎯 Batch Processing Features:**
- **Concurrent fetching**: 10 parallel workers for volume data
- **Batch price lookup**: All prices fetched in one API call
- **Error handling**: Failed stocks excluded, successful ones returned
- **Sorted results**: Stocks ordered alphabetically by symbol
- **Complete coverage**: All 101 NASDAQ 100 stocks supported

**⚡ Batch Performance:**
- Response time: 2-5 seconds (101 stocks)
- Real-time DEGIRO data for all stocks
- Optimized with concurrent processing
- Perfect for market scanners and bulk analysis

## 🚀 Production Deployment

### VPS Configuration
- **Host**: Your VPS IP
- **Port**: 7731 (configurable)
- **SSL**: Not included (use reverse proxy)
- **Auto-restart**: Systemd service + cron job

### Deployment Commands
```bash
# Deploy to VPS
./scripts/deploy_to_vps.sh

# Manual VPS management
ssh -i ~/.ssh/your_key user@your.vps.ip

# Start API
cd /path/to/degiro-trading-api && ./start_api.sh

# Check status
curl -H "Authorization: Bearer YOUR_KEY" http://your.vps.ip:7731/api/health

# View logs
tail -f logs/api.log
```

## 🔧 Configuration

### Environment Variables (.env)
```bash
# API Security
TRADING_API_KEY=your_secure_32_char_token

# DEGIRO Credentials  
DEGIRO_USERNAME=your_username
DEGIRO_PASSWORD=your_password
DEGIRO_TOTP_SECRET=your_2fa_secret
DEGIRO_INT_ACCOUNT=your_account_id

# Application Settings
API_PORT=7731
DEBUG=false
LOG_LEVEL=INFO
MAX_WORKERS=4

# VPS Deployment
VPS_HOST=your.vps.ip
VPS_USER=your_vps_user
VPS_PATH=/home/user/degiro-trading-api
```

## 🔄 Upstream Updates

This fork structure allows seamless updates from the main degiro-connector:

```bash
# Get latest upstream changes
git fetch upstream
git merge upstream/main  # No conflicts with custom-trading/
git push origin main
```

Your custom trading API stays isolated while benefiting from upstream improvements!

## 🆕 Recent API Changes (v2.1.0)

### Enhanced Product Type Filtering

Added `product_subtype` parameter to leverage search endpoints to distinguish between different German financial instruments:

**New Parameter:**
- `product_subtype`: Filter by product type (default: "ALL")
  - `"ALL"`: All leveraged products  
  - `"CALL_PUT"`: **Optionsscheine** - Traditional call/put options with strike price
  - `"MINI"`: **Knockouts** - Mini long/short products with stop loss  
  - `"UNLIMITED"`: Unlimited long/short products by name

**Example Usage:**
```json
{
  "underlying_id": "331868",
  "action": "LONG",
  "product_subtype": "MINI",
  "min_leverage": 2.0,
  "max_leverage": 10.0
}
```

### Real-Time Pricing Implementation

- **✅ Eliminated fake pricing**: Removed hardcoded fallback prices (€100.25) 
- **✅ DEGIRO quotecast integration**: Real-time bid/ask/last prices from DEGIRO
- **✅ Batch pricing optimization**: Up to 50+ products fetched per API call
- **✅ Automatic filtering**: Products without live pricing automatically excluded

### Default Limit Increases

- **Stock search**: Increased from 20 → 50 products
- **Leveraged search**: Increased from 10 → 50 products  
- **Better discovery**: More comprehensive search results by default

## 🛡️ Security Features

- **No Public Documentation**: API docs disabled in production
- **Bearer Token Auth**: Secure 32-character tokens
- **Environment Variables**: No hardcoded credentials
- **Gitignored Secrets**: .env files never committed
- **VPS Firewall**: Custom port with restricted access
- **Two-Step Orders**: Validation before execution

## 📊 Error Handling

Common error responses:

**401 Unauthorized:**
```json
{"detail": "Invalid API key"}
```

**400 Bad Request:**
```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "q"],
      "msg": "Field required"
    }
  ]
}
```

**500 Internal Server Error:**
```json
{
  "detail": "DEGIRO connection failed",
  "error_code": "DEGIRO_CONNECTION_ERROR"
}
```

## 🧪 Testing

```bash
# Test DEGIRO connection
python ../examples/trading/login_2fa.py

# Test all API endpoints
cd _tests && python3 run_all_tests.py

# Health check
curl http://localhost:7731/api/health
```

### Troubleshooting Leveraged Search

If leveraged search returns **"503: Unable to fetch product metadata"**:

1. **Check DEGIRO session**:
   ```bash
   curl -H "Authorization: Bearer YOUR_API_KEY" http://localhost:7731/api/health
   ```

2. **Restart API if needed**:
   ```bash
   # Kill current process
   lsof -ti:7731 | xargs kill -9
   
   # Restart API
   source config/.env && source venv/bin/activate && python api/main.py
   ```

3. **Test with complete parameters**:
   ```bash
   curl -H "Authorization: Bearer YOUR_API_KEY" \
        -H "Content-Type: application/json" \
        -d '{
          "underlying_id": "1153605",
          "action": "LONG",
          "min_leverage": 5.0,
          "max_leverage": 10.0,
          "limit": 10
        }' \
        "http://localhost:7731/api/leveraged/search"
   ```

### Working Example: PYPL Leveraged Products

```bash
# 1. Get PYPL stock ID
curl -H "Authorization: Bearer YOUR_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"q": "PYPL"}' \
     "http://localhost:7731/api/stocks/search"

# 2. Search leveraged products (using product_id from step 1)
curl -H "Authorization: Bearer YOUR_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "underlying_id": "7201951",
       "action": "LONG",
       "min_leverage": 2.0,
       "max_leverage": 10.0,
       "limit": 10
     }' \
     "http://localhost:7731/api/leveraged/search"
```

**Expected Results**: 10 BNP leveraged products with 2x-8x leverage.
