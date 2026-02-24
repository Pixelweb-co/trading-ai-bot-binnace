import os, sys, time, logging
logging.basicConfig(level=logging.WARNING)

# Add src to path
sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv()

from src.infrastructure.binance_service import BinanceService

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

service = BinanceService(API_KEY, API_SECRET, use_testnet=True)

# Get positions
positions = service.get_active_positions()
print("\n=== POSICIONES ABIERTAS ===")
if not positions:
    print("  (Sin posiciones abiertas)")
else:
    for p in positions:
        price = service.get_price(p.symbol)
        if p.side.value == "LONG":
            roi_pct = ((price - p.entry_price) / p.entry_price) * 100
            pnl = (price - p.entry_price) * p.quantity
        else:
            roi_pct = ((p.entry_price - price) / p.entry_price) * 100
            pnl = (p.entry_price - price) * p.quantity
        notional = p.quantity * p.entry_price
        print(f"  {p.symbol} | {p.side.value} | Qty: {p.quantity:.4f}")
        print(f"    Entry: {p.entry_price:.4f} | Current: {price:.4f}")
        print(f"    ROI: {roi_pct:+.2f}% | PnL: {pnl:+.4f} USDT")
        print(f"    Notional: {notional:.2f} USDT")
        print()

# Balance
try:
    balance = service.client.futures_account_balance()
    for b in balance:
        if b['asset'] == 'USDT':
            print(f"Balance USDT: {float(b['balance']):.2f}")
            print(f"Disponible:   {float(b['availableBalance']):.2f}")
except Exception as e:
    print(f"Error balance: {e}")
