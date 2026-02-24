
import os
import logging
from src.infrastructure.binance_service import BinanceService
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
load_dotenv()

api_key = os.getenv("BINANCE_API_KEY")
api_secret = os.getenv("BINANCE_API_SECRET")

service = BinanceService(api_key, api_secret, use_testnet=True)
positions = service.get_active_positions()

print("\n--- POSICIONES ACTIVAS ---")
for p in positions:
    print(f"Símbolo: {p.symbol} | Lado: {p.side} | Cantidad: {p.quantity} | Entrada: {p.entry_price}")

# Check balance
try:
    balance = service.client.futures_account_balance()
    for b in balance:
        if b['asset'] == 'USDT':
            print(f"\nBalance USDT: {b['balance']} (Disponible: {b['availableBalance']})")
except Exception as e:
    print(f"Error checking balance: {e}")
