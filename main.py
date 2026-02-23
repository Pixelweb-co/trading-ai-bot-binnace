"""
╔══════════════════════════════════════════════════════╗
║        SCALPING BOT — EMA TRAP 5M (Binance)         ║
║        Estrategia: EMA 9/21 + RSI + Volumen          ║
╚══════════════════════════════════════════════════════╝

Instalación:
    pip install python-binance pandas numpy ta

Configuración:
    - Pon tus API Keys en las variables API_KEY y API_SECRET
    - Ajusta el SYMBOL y el capital (USDT_PER_TRADE)
    - Modo TESTNET activado por defecto (safe)
"""

import os
import time
import logging
import requests
from openai import OpenAI
import threading
import pandas as pd
import numpy as np
from binance.client import Client
from binance.exceptions import BinanceAPIException
import ta  # librería de indicadores técnicos
from dotenv import load_dotenv
import json

# Importar estrategias e IA
from strategies import EMATrapStrategy, RSIScalpStrategy, ZSStrategy5m, ZSStrategy1m, VWAPOrderFlowStrategy, LiquidationCascadeStrategy
from ai_agent import AIAgent

# Cargar variables de entorno
load_dotenv()

# ──────────────────────────────────────────────
#  CONFIGURACIÓN
# ──────────────────────────────────────────────
API_KEY    = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

#SYMBOLS         = ["HBARUSDT","ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT", "ADAUSDT","VANAUSDT","TRXUSDT","GIGGLEUSDT", "DOGEUSDT", "DOTUSDT"]  # Lista de pares a operar
SYMBOLS         = ["BNBUSDT", "XRPUSDT", "SOLUSDT", "ADAUSDT", "DOGEUSDT"]  # Lista de pares a operar

TIMEFRAME_1M    = Client.KLINE_INTERVAL_1MINUTE
TIMEFRAME_3M    = Client.KLINE_INTERVAL_3MINUTE
TIMEFRAME_5M    = Client.KLINE_INTERVAL_5MINUTE
TIMEFRAME_15M   = Client.KLINE_INTERVAL_15MINUTE

USDT_PER_TRADE  = 25              # Capital por operación en USDT
RISK_REWARD     = 1.5             # TP = SL * RISK_REWARD
MAX_TRADES_DAY  = 10              # Máximo de trades totales por día
MAX_LOSSES_ROW  = 3               # Stop si X pérdidas seguidas
LOOP_SECONDS    = 30              # Cada cuántos segundos revisa señales
LEVERAGE        = 10              # Apalancamiento para Futuros
MARGIN_TYPE     = "ISOLATED"      # Tipo de margen: ISOLATED o CROSSED
HARVEST_ROI_THRESHOLD = 1.0       # Cosechar ganancias si ROI > X% cada 5 min

# Determinar entorno
USE_TESTNET = os.getenv("TRADING_ENV", "SANDBOX") == "SANDBOX"

# Caché para Datos Quant (para evitar 429 Rate Limit)
quant_cache = {}  # {symbol: {'funding': (val, ts), 'oi': (val, change, ts), ...}}
QUANT_CACHE_DURATION = 300 # 5 minutos

# ──────────────────────────────────────────────
#  LOGGING
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("scalping_bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
#  CONEXIÓN BINANCE
# ──────────────────────────────────────────────
def get_client():
    if not API_KEY or not API_SECRET:
        log.error("❌ API_KEY o API_SECRET no encontrados. Verifica tu archivo .env")

    # Aumentar recvWindow para manejar latencia y desfase temporal
    client_params = {
        "api_key": API_KEY,
        "api_secret": API_SECRET,
        "requests_params": {'timeout': 20}
    }

    if USE_TESTNET:
        client = Client(**client_params, testnet=True)
        log.info(f"🧪 Modo TESTNET activado")
    else:
        client = Client(**client_params)
        log.info(f"💰 Modo REAL activado")
    
    # Sincronizar tiempo con Binance para evitar APIError -1021
    try:
        server_time = client.futures_time()['serverTime']
        local_time = int(time.time() * 1000)
        client.timestamp_offset = server_time - local_time
        log.info(f"⏱️ Tiempo sincronizado (Offset: {client.timestamp_offset}ms)")
    except Exception as e:
        log.warning(f"⚠️ No se pudo sincronizar tiempo: {e}")

    # Optimizar el pool de conexiones de la sesión EXISTENTE
    adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
    client.session.mount('https://', adapter)
    client.session.mount('http://', adapter)
    
    return client

# ──────────────────────────────────────────────
#  OBTENER VELAS (OHLCV) - FUTUROS
# ──────────────────────────────────────────────
def get_candles(client, symbol, interval, limit=100):
    raw = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(raw, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","quote_vol","trades","taker_base","taker_quote","ignore"
    ])
    for col in ["open", "high", "low", "close", "volume", "quote_vol", "taker_base", "taker_quote"]:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df

# ──────────────────────────────────────────────
#  CONFIGURACIÓN DE ESTRATEGIAS
# ──────────────────────────────────────────────
ACTIVE_STRATEGIES = [
    EMATrapStrategy(),
    ZSStrategy5m(),
    ZSStrategy1m(),
    VWAPOrderFlowStrategy(),
    LiquidationCascadeStrategy()
]

# ──────────────────────────────────────────────
#  OBTENER PRECIO Y OBI
# ──────────────────────────────────────────────
def get_price(client, symbol):
    ticker = client.futures_symbol_ticker(symbol=symbol)
    return float(ticker["price"])

def get_obi(client, symbol, levels=10):
    try:
        book = client.futures_order_book(symbol=symbol, limit=levels)
        if not book or "bids" not in book or "asks" not in book:
            return 0.5
        bid_vol = sum(float(b[1]) for b in book["bids"])
        ask_vol = sum(float(a[1]) for a in book["asks"])
        total = bid_vol + ask_vol
        return bid_vol / total if total > 0 else 0.5
    except Exception as e:
        log.error(f"❌ Error OBI {symbol}: {e}")
        return 0.5

# ──────────────────────────────────────────────
#  DATOS QUANTS (FUTUROS) - CON CACHÉ
# ──────────────────────────────────────────────
def get_funding_rate(client, symbol):
    now = time.time()
    if symbol in quant_cache and 'funding' in quant_cache[symbol]:
        val, ts, l_err = quant_cache[symbol]['funding']
        if now - ts < QUANT_CACHE_DURATION: return val
    try:
        data = client.futures_funding_rate(symbol=symbol, limit=1)
        rate = float(data[-1]["fundingRate"]) if data else 0.0
        if symbol not in quant_cache: quant_cache[symbol] = {}
        # Guardar (valor, timestamp, last_error_ts)
        quant_cache[symbol]['funding'] = (rate, now, 0)
        return rate
    except Exception as e:
        if symbol in quant_cache and 'funding' in quant_cache[symbol]:
            val, ts, l_err = quant_cache[symbol]['funding']
            if now - l_err > 600:
                log.warning(f"⚠️ Error Funding {symbol} (503?). Usando caché: {val}")
                quant_cache[symbol]['funding'] = (val, ts, now)
            return val
        return 0.0

def get_open_interest(client, symbol):
    now = time.time()
    if symbol in quant_cache and 'oi' in quant_cache[symbol]:
        val, change, ts, l_err = quant_cache[symbol]['oi']
        if now - ts < QUANT_CACHE_DURATION: return val, change
    try:
        data = client.futures_open_interest_hist(symbol=symbol, period="5m", limit=6)
        if not data or len(data) < 2: return 0.0, 0.0
        oi_current = float(data[-1]["sumOpenInterestValue"])
        oi_prev    = float(data[0]["sumOpenInterestValue"])
        oi_change  = (oi_current - oi_prev) / oi_prev if oi_prev else 0.0
        if symbol not in quant_cache: quant_cache[symbol] = {}
        quant_cache[symbol]['oi'] = (oi_current, oi_change, now, 0)
        return oi_current, oi_change
    except Exception as e:
        if symbol in quant_cache and 'oi' in quant_cache[symbol]:
            val, change, ts, l_err = quant_cache[symbol]['oi']
            if now - l_err > 600:
                log.warning(f"⚠️ Error OI {symbol}. Usando caché.")
                quant_cache[symbol]['oi'] = (val, change, ts, now)
            return val, change
        return 0.0, 0.0

def get_long_short_ratio(client, symbol):
    now = time.time()
    if symbol in quant_cache and 'ls_ratio' in quant_cache[symbol]:
        val, ts, l_err = quant_cache[symbol]['ls_ratio']
        if now - ts < QUANT_CACHE_DURATION: return val
    try:
        data = client.futures_global_longshort_ratio(symbol=symbol, period="5m", limit=1)
        ratio = float(data[-1]["longShortRatio"]) if data else 1.0
        if symbol not in quant_cache: quant_cache[symbol] = {}
        quant_cache[symbol]['ls_ratio'] = (ratio, now, 0)
        return ratio
    except Exception as e:
        if symbol in quant_cache and 'ls_ratio' in quant_cache[symbol]:
            val, ts, l_err = quant_cache[symbol]['ls_ratio']
            if now - l_err > 600:
                log.warning(f"⚠️ Error L/S Ratio {symbol}. Usando caché.")
                quant_cache[symbol]['ls_ratio'] = (val, ts, now)
            return val
        return 1.0

def get_recent_liquidations(client, symbol):
    now = time.time()
    if symbol in quant_cache and 'liq' in quant_cache[symbol]:
        total, sides, ts, l_err = quant_cache[symbol]['liq']
        if now - ts < 60: return total, sides
    try:
        data = client.futures_liquidation_orders(symbol=symbol, limit=20)
        total_usd = sum(float(l["origQty"]) * float(l["price"]) for l in data)
        sides = {"BUY": 0.0, "SELL": 0.0}
        for l in data:
            val = float(l["origQty"]) * float(l["price"])
            sides[l["side"]] += val
        if symbol not in quant_cache: quant_cache[symbol] = {}
        quant_cache[symbol]['liq'] = (total_usd, sides, now, 0)
        return total_usd, sides
    except:
        if symbol in quant_cache and 'liq' in quant_cache[symbol]:
            total, sides, ts, l_err = quant_cache[symbol]['liq']
            return total, sides
        return 0.0, {"BUY": 0.0, "SELL": 0.0}

# ──────────────────────────────────────────────
#  GESTIÓN DE ÓRDENES
# ──────────────────────────────────────────────
def get_quantity(client, symbol, usdt_amount, price):
    info = client.futures_exchange_info()
    symbol_info = next(i for i in info["symbols"] if i["symbol"] == symbol)
    step_size = next(float(f["stepSize"]) for f in symbol_info["filters"] if f["filterType"] == "MARKET_LOT_SIZE")
    qty = (usdt_amount * LEVERAGE) / price
    precision = int(round(-np.log10(step_size)))
    qty = round(qty - (qty % step_size), precision)
    if qty * price < 101:
        qty = round((101 / price) + step_size, precision)
    return qty

def place_order(client, symbol, side, quantity):
    try:
        order = client.futures_create_order(symbol=symbol, side=side, type=Client.FUTURE_ORDER_TYPE_MARKET, quantity=quantity, recvWindow=10000)
        log.info(f"✅ Orden FUTUROS {side} ejecutada: {quantity} {symbol}")
        return order
    except Exception as e:
        if hasattr(e, 'code') and e.code == -2027:
            log.warning(f"⚠️ [LIMITE DE RIESGO] No se pudo abrir {symbol}: Has excedido la posición máxima permitida por Binance para x{LEVERAGE}. Libera margen o reduce el apalancamiento.")
        else:
            log.error(f"❌ Error orden {symbol}: {e}")
        return None

# Estado Global
active_symbols = set()
active_symbols_lock = threading.Lock()
total_session_gain = 0
total_session_loss = 0
total_session_wins = 0
total_session_losses = 0
session_pnl_lock = threading.Lock()
consecutive_losses_per_symbol = {}
martingale_lock = threading.Lock()

# Persistencia de Sesión
STATS_FILE = "session_stats.json"

def save_session_stats():
    try:
        data = {
            "gain": total_session_gain,
            "loss": total_session_loss,
            "wins": total_session_wins,
            "losses": total_session_losses,
            "martingale_losses": consecutive_losses_per_symbol
        }
        with open(STATS_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e: log.error(f"❌ Error guardando stats: {e}")

def load_session_stats():
    global total_session_gain, total_session_loss, total_session_wins, total_session_losses, consecutive_losses_per_symbol
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r") as f:
                data = json.load(f)
                total_session_gain = data.get("gain", 0)
                total_session_loss = data.get("loss", 0)
                total_session_wins = data.get("wins", 0)
                total_session_losses = data.get("losses", 0)
                consecutive_losses_per_symbol.update(data.get("martingale_losses", {}))
            log.info(f"📊 Stats cargadas: +{total_session_gain:.2f} | -{total_session_loss:.2f} | W:{total_session_wins} L:{total_session_losses}")
        except Exception as e: log.error(f"❌ Error cargando stats: {e}")

# Caché Global para Consejos de IA (Bulk)
bulk_ai_advice = {} # {symbol: action}
bulk_ai_lock = threading.Lock()

# ──────────────────────────────────────────────
#  MONITOREO DE TRADE
# ──────────────────────────────────────────────
def monitor_trade(client, ai, symbol, strategy_name, signal, entry_price, tp_levels, sl, quantity):
    global total_session_gain, total_session_loss, total_session_wins, total_session_losses
    entry_price, sl, quantity = float(entry_price), float(sl), float(quantity)
    tp1 = float(tp_levels[0])
    log.info(f"🧵 [HILO {symbol}] {strategy_name} | Entry: {entry_price} | TP1: {tp1}")
    
    tp1_hit = False
    current_sl = sl
    qty_remaining = quantity
    total_pnl_usdt = 0.0
    ai_timer = 0

    while True:
        try:
            price = get_price(client, symbol)
            roi = ((price - entry_price) / entry_price * 100) if signal == "LONG" else ((entry_price - price) / entry_price * 100)

            # 1. Consultar Consejo de IA desde la Caché Bulk (Centralizada)
            with bulk_ai_lock:
                advice = bulk_ai_advice.get(symbol)
                if advice:
                    if advice == "CLOSE_NOW" and roi > 0.1: # Guard: Only close via AI if in profit
                        log.warning(f"⚠️ [IA {symbol}] Cierre de emergencia por IA con ganancia.")
                        del bulk_ai_advice[symbol] 
                        break
                    if advice == "REDUCE_RISK":
                        current_sl = entry_price
                        del bulk_ai_advice[symbol]

            # 2. Equilibrado de Sesión
            with session_pnl_lock:
                if (total_session_gain - total_session_loss) < -10 and roi > 0.5: # Guard: ROI threshold
                    log.warning(f"⚖️ [EQUILIBRADO {symbol}] Cerrando para compensar sesión.")
                    break

            # 3. Salida RSI (Agotamiento)
            if int(time.time()) % 60 < 5: 
                df_rsi = get_candles(client, symbol, Client.KLINE_INTERVAL_1MINUTE, limit=20)
                rsi = ta.momentum.RSIIndicator(df_rsi["close"], window=14).rsi().iloc[-1]
                if ((signal == "LONG" and rsi > 78) or (signal == "SHORT" and rsi < 22)) and roi > 0.1:
                    log.info(f"📈 [RSI {symbol}] Salida por agotamiento con ganancia.")
                    break

            # Lógica TP/SL
            if signal == "LONG":
                if not tp1_hit and price >= tp1:
                    p_qty = get_quantity(client, symbol, (USDT_PER_TRADE * 0.75), price)
                    place_order(client, symbol, Client.SIDE_SELL, p_qty)
                    total_pnl_usdt += (price - entry_price) * p_qty
                    qty_remaining -= p_qty; current_sl = entry_price; tp1_hit = True
                if price <= current_sl: break
            else:
                if not tp1_hit and price <= tp1:
                    p_qty = get_quantity(client, symbol, (USDT_PER_TRADE * 0.75), price)
                    place_order(client, symbol, Client.SIDE_BUY, p_qty)
                    total_pnl_usdt += (entry_price - price) * p_qty
                    qty_remaining -= p_qty; current_sl = entry_price; tp1_hit = True
                if price >= current_sl: break

            time.sleep(5)
        except:
            time.sleep(10)

    # Cierre Final
    exit_p = get_price(client, symbol)
    pnl_f = (exit_p - entry_price) * qty_remaining if signal == "LONG" else (entry_price - exit_p) * qty_remaining
    total_pnl_usdt += pnl_f
    place_order(client, symbol, Client.SIDE_SELL if signal == "LONG" else Client.SIDE_BUY, qty_remaining)
    
    with active_symbols_lock: active_symbols.discard(symbol)
    with session_pnl_lock:
        if total_pnl_usdt > 0:
            total_session_gain += total_pnl_usdt
            total_session_wins += 1
        else:
            total_session_loss += abs(total_pnl_usdt)
            total_session_losses += 1
        save_session_stats()
    
    with martingale_lock:
        consecutive_losses_per_symbol[symbol] = (consecutive_losses_per_symbol.get(symbol,0) + 1) if total_pnl_usdt < 0 else 0
        save_session_stats()

    log.info(f"🏁 [HILO {symbol}] Finalizado | PnL Total: {total_pnl_usdt:.2f} USDT")

def sync_existing_positions(client, ai, harvest=False):
    """Sincroniza posiciones de Binance con el estado interno del bot.
    Si harvest=True, cierra posiciones que superen el 2% de ROI para liberar margen."""
    try:
        positions = client.futures_position_information(recvWindow=10000)
        current_on_exchange = set()
        
        for p in positions:
            symbol, qty = p['symbol'], float(p['positionAmt'])
            if qty == 0: continue
            
            current_on_exchange.add(symbol)
            side = "LONG" if qty > 0 else "SHORT"
            entry = float(p['entryPrice'])
            unrealized_pnl = float(p['unRealizedProfit'])
            # Calcular ROI simple (Margen aproximado basado en apalancamiento)
            # Nota: unRealizedProfit / (qty * entry / leverage) * 100
            margin_used = (abs(qty) * entry) / LEVERAGE
            roi = (unrealized_pnl / margin_used * 100) if margin_used > 0 else 0

            # 1. PECHOCHAS (Harvesting de Ganancias)
            if harvest and roi > HARVEST_ROI_THRESHOLD:
                log.info(f"💰 [HARVEST] {symbol} con ROI {roi:.2f}%. Cerrando para asegurar ganancias y liberar margen.")
                place_order(client, symbol, Client.SIDE_SELL if side == "LONG" else Client.SIDE_BUY, abs(qty))
                continue

            # 2. Sincronizar Nuevos Huérfanos
            with active_symbols_lock:
                if symbol not in active_symbols:
                    active_symbols.add(symbol)
                    sl = entry * (0.985 if side == "LONG" else 1.015)
                    tp = entry * (1.025 if side == "LONG" else 0.975)
                    log.info(f"🔗 [AUDITOR] Detectada posición externa en {symbol}. Iniciando monitoreo.")
                    threading.Thread(target=monitor_trade, args=(client, ai, symbol, "SYNC", side, entry, [tp], sl, abs(qty)), daemon=True).start()

        # 3. Limpieza de Fantasmas (Cerrados externamente)
        with active_symbols_lock:
            to_remove = [s for s in active_symbols if s not in current_on_exchange]
            for s in to_remove:
                log.info(f"🧹 [AUDITOR] {s} ya no existe en el exchange. Limpiando estado interno.")
                active_symbols.discard(s)

    except Exception as e: log.error(f"❌ Error en Auditoría: {e}")

# ──────────────────────────────────────────────
#  LOOP PRINCIPAL
# ──────────────────────────────────────────────
def run_bot():
    client = get_client()
    ai = AIAgent()
    log.info(f"🤖 Bot iniciado | Pares: {SYMBOLS} | Estrategias: {[s.name for s in ACTIVE_STRATEGIES]}")

    for symbol in SYMBOLS:
        try:
            client.futures_change_margin_type(symbol=symbol, marginType=MARGIN_TYPE)
            client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
        except: pass
    
    load_session_stats()
    sync_existing_positions(client, ai)
    iteration = 0

    while True:
        try:
            for symbol in SYMBOLS:
                with active_symbols_lock:
                    if symbol in active_symbols: continue

                # Data Pipeline
                r1, r3, r5, r15 = (get_candles(client, symbol, t) for t in [TIMEFRAME_1M, TIMEFRAME_3M, TIMEFRAME_5M, TIMEFRAME_15M])
                f_rate = get_funding_rate(client, symbol)
                oi_v, oi_c = get_open_interest(client, symbol)
                ls_r = get_long_short_ratio(client, symbol)
                l_v, l_s = get_recent_liquidations(client, symbol)
                q_data = {'funding': f_rate, 'oi_change': oi_c, 'ls_ratio': ls_r, 'liq_sides': l_s}

                for strategy in ACTIVE_STRATEGIES:
                    if "1M" in strategy.name: df_p, df_s = r1.copy(), r5.copy()
                    elif "3M" in strategy.name: df_p, df_s = r3.copy(), r15.copy()
                    else: df_p, df_s = r5.copy(), r15.copy()

                    df_p, df_s = strategy.add_indicators(df_p), strategy.add_indicators(df_s)
                    
                    if "VWAP_ORDERFLOW" in strategy.name: res = strategy.check_signal(df_p, df_s, obi=get_obi(client, symbol))
                    elif "LIQ_CASCADE" in strategy.name: res = strategy.check_signal(df_p, df_s, quant_data=q_data)
                    else: res = strategy.check_signal(df_p, df_s)

                    if not res or not res[0]: continue
                    
                    # Desempaquetar
                    if len(res) == 4: sig, candle, tps, sl, meta = res[0], res[1], res[2], res[3], None
                    else: sig, candle, tps, sl, meta = res

                    if sig:
                        multiplier, quality = 1.0, "B"
                        
                        # Validación IA
                        if meta:
                            log.info(f"🧠 Validando {strategy.name} en {symbol}...")
                            if "LIQ_CASCADE" in strategy.name:
                                ok, conf, reason, quality = ai.analyze_cascade_setup(symbol, sig, get_price(client, symbol), tps[0], sl, meta)
                            else:
                                ok, conf, reason, quality = ai.analyze_vwap_setup(symbol, sig, get_price(client, symbol), tps[0], sl, meta)
                            
                            if not ok or quality == "C":
                                log.info(f"🤖 Rechazado: {reason}")
                                continue

                        # Martingala
                        losses = consecutive_losses_per_symbol.get(symbol, 0)
                        if losses > 0:
                            ok, m, reason = ai.decide_martingale(symbol, sig, quality, losses)
                            if ok: multiplier = m

                        # Operar
                        with active_symbols_lock: active_symbols.add(symbol)
                        price = get_price(client, symbol)
                        qty = get_quantity(client, symbol, USDT_PER_TRADE * multiplier, price)
                        
                        if place_order(client, symbol, Client.SIDE_BUY if sig=="LONG" else Client.SIDE_SELL, qty):
                            threading.Thread(target=monitor_trade, args=(client, ai, symbol, strategy.name, sig, price, tps, sl, qty), daemon=True).start()
                            time.sleep(5); break
                        else:
                            with active_symbols_lock: active_symbols.remove(symbol)
                
                log.info(f"🔍 [{symbol}] Escaneo OK.")
                time.sleep(1)

            iteration += 1
            
            # --- SECCIÓN DE OPTIMIZACIÓN DE TOKENS IA ---
            
            # 1. Análisis en BULK de Cartera (Cada 20 iteraciones ~10 mins)
            if iteration % 20 == 0:
                with active_symbols_lock:
                    subjects = list(active_symbols)
                
                if subjects:
                    log.info(f"🧠 [IA BULK] Analizando salud de {len(subjects)} posiciones activas...")
                    positions_data = []
                    # Sincronizar datos reales
                    pos_info = client.futures_position_information()
                    for p in pos_info:
                        s = p['symbol']
                        if s in subjects:
                            amt = float(p['positionAmt'])
                            entry = float(p['entryPrice'])
                            curr = get_price(client, s)
                            pnl = ((curr - entry)/entry*100) if amt > 0 else ((entry - curr)/entry*100)
                            positions_data.append({
                                'symbol': s, 'side': ('LONG' if amt > 0 else 'SHORT'),
                                'entry': entry, 'price': curr, 'pnl': pnl
                            })
                    
                    if positions_data:
                        advice_map = ai.analyze_bulk_positions(positions_data)
                        with bulk_ai_lock:
                            bulk_ai_advice.update(advice_map)
                        log.info(f"✅ [IA BULK] Recomendaciones recibidas: {advice_map}")

            # 2. Market Insight Macro (Cada 60 iteraciones ~30 mins)
            if iteration % 60 == 0:
                insight = ai.get_market_insight(SYMBOLS)
                log.info(f"💡 Market Insight: {insight}")

            # 3. Informe de Sesión Periódico (Cada 10 iteraciones ~5 mins)
            if iteration % 10 == 0:
                # 3.1 Calcular PnL Flotante (Unrealized)
                float_pnl = 0.0
                try:
                    pos_info = client.futures_position_information()
                    for p in pos_info:
                        if float(p['positionAmt']) != 0:
                            float_pnl += float(p['unRealizedProfit'])
                except: pass

                with session_pnl_lock:
                    net_realized = total_session_gain - total_session_loss
                    total_net = net_realized + float_pnl
                    log.info(f"📊 [INFORME GENERAL]")
                    log.info(f"   💰 Realizado: +{total_session_gain:.2f} | -{total_session_loss:.2f} (Neto: {net_realized:.2f} USDT)")
                    log.info(f"   🌊 Flotante (Abierto): {float_pnl:.2f} USDT")
                    log.info(f"   🏆 Récord: {total_session_wins} Victorias | {total_session_losses} Derrotas")
                    log.info(f"   🚀 BALANCE TOTAL SESIÓN: {total_net:.2f} USDT")

            # 4. Auditoría de Seguridad & Harvest (Cada 10 iteraciones ~5 mins)
            if iteration % 10 == 0:
                log.info("🛡️ [AUDITOR] Ejecutando revisión periódica de posiciones...")
                sync_existing_positions(client, ai, harvest=True)

            time.sleep(LOOP_SECONDS)
        except Exception as e:
            log.error(f"❌ Error Loop: {e}")
            time.sleep(60)

if __name__ == "__main__":
    run_bot()