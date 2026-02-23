"""
╔══════════════════════════════════════════════════╗
║       EMERGENCY RESET — Close All Positions      ║
║  Cierra TODAS las posiciones abiertas en         ║
║  Binance Futures y reinicia las estadísticas     ║
║  para empezar de cero.                            ║
║                                                  ║
║  USO:                                            ║
║    python close_all_and_reset.py                 ║
║                                                  ║
║  ⚠️  DETÉN EL BOT ANTES DE EJECUTAR ESTO        ║
╚══════════════════════════════════════════════════╝
"""

import os
import json
import time
import logging
import numpy as np
from dotenv import load_dotenv
from binance.client import Client

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("RESET")

STATS_FILE   = "session_stats_v2.json"
LOG_FILE     = "refactored_bot.log"

def get_step_size(client: Client, symbol: str) -> float:
    try:
        info = client.futures_exchange_info()
        sym_info = next(s for s in info["symbols"] if s["symbol"] == symbol)
        step = next(float(f["stepSize"]) for f in sym_info["filters"] if f["filterType"] == "MARKET_LOT_SIZE")
        return step
    except Exception as e:
        log.warning(f"Could not get step_size for {symbol}: {e}. Using 0.001 as fallback.")
        return 0.001

def floor_qty(qty: float, step_size: float) -> float:
    precision = max(0, int(round(-np.log10(step_size))))
    return round(qty - (qty % step_size), precision)


def close_all_positions(client: Client):
    log.info("📡 Fetching all open positions...")
    positions = client.futures_position_information()
    open_pos = [p for p in positions if float(p["positionAmt"]) != 0]

    if not open_pos:
        log.info("✅ No open positions found.")
        return

    log.info(f"🔍 Found {len(open_pos)} open position(s):")
    for p in open_pos:
        symbol = p["symbol"]
        qty    = float(p["positionAmt"])
        entry  = float(p["entryPrice"])
        side   = "LONG" if qty > 0 else "SHORT"
        log.info(f"   → {symbol} | {side} | Qty: {qty} | Entry: {entry}")

    confirm = input("\n⚠️  ¿Cerrar TODAS las posiciones? Escribe 'SI' para confirmar: ")
    if confirm.strip().upper() != "SI":
        log.info("❌ Cancelado por el usuario.")
        return

    for p in open_pos:
        symbol   = p["symbol"]
        qty      = float(p["positionAmt"])
        side_str = "SELL" if qty > 0 else "BUY"  # close LONG → SELL, close SHORT → BUY
        abs_qty  = abs(qty)

        step = get_step_size(client, symbol)
        abs_qty = floor_qty(abs_qty, step)

        if abs_qty <= 0:
            log.warning(f"⚠️  {symbol}: qty after rounding is 0, skipping.")
            continue

        try:
            order = client.futures_create_order(
                symbol=symbol,
                side=side_str,
                type=Client.FUTURE_ORDER_TYPE_MARKET,
                quantity=abs_qty,
                reduceOnly=True  # Safety: only reduces, never opens new
            )
            log.info(f"✅ Closed {symbol} ({side_str}) qty={abs_qty} | OrderId: {order['orderId']}")
        except Exception as e:
            log.error(f"❌ Error closing {symbol}: {e}")

        time.sleep(0.3)  # Avoid rate limits


def reset_stats():
    empty_stats = {
        "total_gain": 0.0,
        "total_loss": 0.0,
        "total_wins": 0,
        "total_losses": 0,
        "consecutive_losses": {}
    }
    with open(STATS_FILE, "w") as f:
        json.dump(empty_stats, f, indent=2)
    log.info(f"🔄 {STATS_FILE} — stats reset to zero.")


def clear_log():
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("")
    log.info(f"🧹 {LOG_FILE} — log cleared.")


def main():
    api_key    = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    use_testnet = os.getenv("TRADING_ENV", "SANDBOX") == "SANDBOX"

    if not api_key or not api_secret:
        log.error("❌ BINANCE_API_KEY / BINANCE_API_SECRET not found in .env")
        return

    client = Client(api_key, api_secret, testnet=use_testnet)

    # Sync time
    try:
        server_time = client.futures_time()["serverTime"]
        client.timestamp_offset = server_time - int(time.time() * 1000)
    except Exception as e:
        log.warning(f"Time sync failed: {e}")

    mode = "🧪 TESTNET" if use_testnet else "💰 REAL"
    log.info(f"Conectado en modo {mode}")
    print()

    # Step 1: Close all positions
    close_all_positions(client)
    print()

    # Step 2: Reset stats
    reset_confirm = input("🔄 ¿Resetear session_stats_v2.json a cero? (SI/no): ")
    if reset_confirm.strip().upper() == "SI":
        reset_stats()

    # Step 3: Clear log
    log_confirm = input("🧹 ¿Limpiar el archivo de log? (SI/no): ")
    if log_confirm.strip().upper() == "SI":
        clear_log()

    print()
    log.info("✅ Reset completo. Puedes arrancar el bot limpio con: python run_refactored.py")


if __name__ == "__main__":
    main()
