import logging
import time
import pandas as pd
import numpy as np
from binance.client import Client
from typing import List, Optional, Dict, Any
from ..application.interfaces import IMarketDataService, ITradingService
from ..domain.models import MarketData, QuantData, Position, OrderSide

log = logging.getLogger(__name__)

class BinanceService(IMarketDataService, ITradingService):
    def __init__(self, api_key: str, api_secret: str, use_testnet: bool = True):
        self.client = Client(api_key, api_secret, testnet=use_testnet)
        self.use_testnet = use_testnet
        self._quant_cache = {}
        self._cache_duration = 300
        self._sync_time()

    def _sync_time(self):
        try:
            server_time = self.client.futures_time()['serverTime']
            local_time = int(time.time() * 1000)
            self.client.timestamp_offset = server_time - local_time
            log.info(f"⏱️ Binance time synced (Offset: {self.client.timestamp_offset}ms)")
        except Exception as e:
            log.warning(f"⚠️ Failed to sync time: {e}")

    # --- IMarketDataService ---

    def get_candles(self, symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
        raw = self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(raw, columns=[
            "open_time","open","high","low","close","volume",
            "close_time","quote_vol","trades","taker_base","taker_quote","ignore"
        ])
        cols = ["open", "high", "low", "close", "volume", "quote_vol", "taker_base", "taker_quote"]
        for col in cols:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        return df

    def get_price(self, symbol: str) -> float:
        ticker = self.client.futures_symbol_ticker(symbol=symbol)
        return float(ticker["price"])

    def get_obi(self, symbol: str, levels: int = 10) -> float:
        try:
            book = self.client.futures_order_book(symbol=symbol, limit=levels)
            bid_vol = sum(float(b[1]) for b in book["bids"])
            ask_vol = sum(float(a[1]) for a in book["asks"])
            total = bid_vol + ask_vol
            return bid_vol / total if total > 0 else 0.5
        except Exception as e:
            log.error(f"❌ OBI Error {symbol}: {e}")
            return 0.5

    def get_quant_data(self, symbol: str) -> QuantData:
        now = time.time()
        # Simple cache logic
        funding = self._get_cached_item(symbol, 'funding', lambda: float(self.client.futures_funding_rate(symbol=symbol, limit=1)[-1]["fundingRate"]))
        oi_val, oi_change = self._get_cached_oi(symbol)
        ls_ratio = self._get_cached_item(symbol, 'ls_ratio', lambda: float(self.client.futures_global_longshort_ratio(symbol=symbol, period="5m", limit=1)[-1]["longShortRatio"]))
        liq_total, liq_sides = self._get_cached_liq(symbol)
        
        return QuantData(funding, oi_val, oi_change, ls_ratio, liq_total, liq_sides)

    def get_top_symbols(self, n: int = 20, min_volume_usdt: float = 200_000_000) -> list:
        """
        Fetches all active USDT futures tickers and returns the top N symbols
        scored by scalping opportunity: volume + volatility + price momentum.
        """
        try:
            tickers = self.client.futures_ticker()
        except Exception as e:
            log.error(f"❌ Error fetching tickers: {e}")
            return []

        candidates = []
        for t in tickers:
            symbol = t.get("symbol", "")

            # Quality filters ─────────────────────────────────────────────
            # 1. Only USDT-margined pairs
            if not symbol.endswith("USDT"): continue
            # 2. Skip leveraged & structured tokens
            if any(x in symbol for x in ["UP", "DOWN", "BEAR", "BULL", "HALF", "HEDGE", "3L", "3S"]): continue
            # 3. Reject non-ASCII symbols (catches Chinese / emoji tickers)
            try:
                symbol.encode("ascii")
            except UnicodeEncodeError:
                continue
            # 4. Base asset must be ≤ 8 chars (BTCUSDT → BTC = 3 chars; garbage coins often have long names)
            base = symbol.replace("USDT", "")
            if len(base) > 8: continue
            # ──────────────────────────────────────────────────────────────

            try:
                volume    = float(t.get("quoteVolume", 0))   # 24h volume in USDT
                price_chg = abs(float(t.get("priceChangePercent", 0)))  # volatility proxy
                count     = float(t.get("count", 0))          # number of trades
                last_price = float(t.get("lastPrice", 0))     # current price
            except (ValueError, TypeError):
                continue

            # 5. Minimum liquidity (200M to exclude microcaps)
            if volume < min_volume_usdt: continue
            # 6. Minimum coin price — coins under $0.50 have spreads > ATR SL
            if last_price < 0.50: continue
            # 7. Exclude extreme pumps/dumps — too unpredictable for scalping
            if price_chg > 25.0: continue

            candidates.append({
                "symbol":    symbol,
                "volume":    volume,
                "price_chg": price_chg,
                "count":     count,
            })


        if not candidates:
            return []

        # Normalize and score (0-1 per metric)
        max_vol   = max(c["volume"]    for c in candidates) or 1
        max_chg   = max(c["price_chg"] for c in candidates) or 1
        max_count = max(c["count"]     for c in candidates) or 1

        for c in candidates:
            vol_score   = c["volume"]    / max_vol
            chg_score   = c["price_chg"] / max_chg
            count_score = c["count"]     / max_count
            # Weights: volume 40%, volatility 40%, activity 20%
            c["score"] = (vol_score * 0.4) + (chg_score * 0.4) + (count_score * 0.2)

        candidates.sort(key=lambda x: x["score"], reverse=True)
        top = candidates[:n]
        log.info(f"📊 Top {n} symbols by scalping score: {[c['symbol'] for c in top]}")
        return top


    # --- ITradingService ---

    def place_order(self, symbol: str, side: str, quantity: float) -> Optional[Dict[str, Any]]:
        try:
            order = self.client.futures_create_order(
                symbol=symbol, 
                side=side, 
                type=Client.FUTURE_ORDER_TYPE_MARKET, 
                quantity=quantity, 
                recvWindow=10000
            )
            return order
        except Exception as e:
            log.error(f"❌ Order Error {symbol}: {e}")
            return None

    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        info = self.client.futures_exchange_info()
        return next(i for i in info["symbols"] if i["symbol"] == symbol)

    def get_active_positions(self) -> List[Position]:
        positions = self.client.futures_position_information(recvWindow=10000)
        active = []
        for p in positions:
            qty = float(p['positionAmt'])
            if qty == 0: continue
            
            symbol = p['symbol']
            side = OrderSide.LONG if qty > 0 else OrderSide.SHORT
            entry = float(p['entryPrice'])
            
            # Note: This is a partial Position object, should be hydrated or used carefully
            active.append(Position(
                symbol=symbol,
                side=side,
                entry_price=entry,
                quantity=abs(qty),
                tp_levels=[], # Unknown from exchange
                sl=0,
                strategy_name="SYNC"
            ))
        return active

    def get_quantity(self, symbol: str, usdt_amount: float, price: float, leverage: int) -> float:
        info = self.get_symbol_info(symbol)
        step_size = next(float(f["stepSize"]) for f in info["filters"] if f["filterType"] == "MARKET_LOT_SIZE")
        qty = (usdt_amount * leverage) / price
        precision = int(round(-np.log10(step_size)))
        qty = round(qty - (qty % step_size), precision)
        # Minimum notional check (roughly $100 for futures usually, but let's stick to original logic)
        if qty * price < 101:
            qty = round((101 / price) + step_size, precision)
        return qty

    def close_position(self, symbol: str, side: OrderSide, quantity: float) -> Optional[Dict[str, Any]]:
        close_side = Client.SIDE_SELL if side == OrderSide.LONG else Client.SIDE_BUY
        # Always round down to the symbol's step_size to avoid -1111 precision errors
        try:
            info = self.get_symbol_info(symbol)
            step_size = next(float(f["stepSize"]) for f in info["filters"] if f["filterType"] == "MARKET_LOT_SIZE")
            precision = int(round(-np.log10(step_size)))
            quantity = round(quantity - (quantity % step_size), precision)
        except Exception:
            pass  # If we can't get info, just use the raw quantity
        return self.place_order(symbol, close_side, quantity)

    def change_leverage(self, symbol: str, leverage: int):
        try:
            self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
        except Exception as e:
            log.warning(f"Failed to change leverage for {symbol}: {e}")

    def change_margin_type(self, symbol: str, margin_type: str):
        try:
            self.client.futures_change_margin_type(symbol=symbol, marginType=margin_type)
        except Exception as e:
            pass # Usually fails if already set

    # --- Helpers ---

    def _get_cached_item(self, symbol: str, key: str, fetch_func):
        if symbol not in self._quant_cache: self._quant_cache[symbol] = {}
        cached = self._quant_cache[symbol].get(key)
        if cached and time.time() - cached[1] < self._cache_duration:
            return cached[0]
        
        try:
            val = fetch_func()
            self._quant_cache[symbol][key] = (val, time.time())
            return val
        except:
            return cached[0] if cached else 0.0

    def _get_cached_oi(self, symbol: str):
        if symbol not in self._quant_cache: self._quant_cache[symbol] = {}
        cached = self._quant_cache[symbol].get('oi')
        if cached and time.time() - cached[2] < self._cache_duration:
            return cached[0], cached[1]
        
        try:
            data = self.client.futures_open_interest_hist(symbol=symbol, period="5m", limit=6)
            oi_current = float(data[-1]["sumOpenInterestValue"])
            oi_prev = float(data[0]["sumOpenInterestValue"])
            oi_change = (oi_current - oi_prev) / oi_prev if oi_prev else 0.0
            self._quant_cache[symbol]['oi'] = (oi_current, oi_change, time.time())
            return oi_current, oi_change
        except:
            return (cached[0], cached[1]) if cached else (0.0, 0.0)

    def _get_cached_liq(self, symbol: str):
        if symbol not in self._quant_cache: self._quant_cache[symbol] = {}
        cached = self._quant_cache[symbol].get('liq')
        if cached and time.time() - cached[2] < 60:
            return cached[0], cached[1]
        
        try:
            data = self.client.futures_liquidation_orders(symbol=symbol, limit=20)
            total_usd = sum(float(l["origQty"]) * float(l["price"]) for l in data)
            sides = {"BUY": 0.0, "SELL": 0.0}
            for l in data:
                sides[l["side"]] += float(l["origQty"]) * float(l["price"])
            self._quant_cache[symbol]['liq'] = (total_usd, sides, time.time())
            return total_usd, sides
        except:
            return (cached[0], cached[1]) if cached else (0.0, {"BUY": 0.0, "SELL": 0.0})
