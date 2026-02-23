import os
import logging
from dotenv import load_dotenv
from ...infrastructure.binance_service import BinanceService
from ...infrastructure.openai_adapter import OpenAIAdapter
from ...infrastructure.json_persistence import JsonPersistenceService
from ...domain.risk_manager import RiskManager
from ...domain.strategies import (
    EMATrapStrategy, ZSStrategy5m, ZSStrategy1m, 
    VWAPOrderFlowStrategy, LiquidationCascadeStrategy,
    TrendAnticipatorStrategy
)
from ...application.use_cases import TradeExecutor, PositionManager, TradingBotApp

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler("refactored_bot.log", encoding="utf-8"),
            logging.StreamHandler()
        ]
    )

def run():
    load_dotenv()
    setup_logging()
    log = logging.getLogger("BotLauncher")

    # Config
    API_KEY = os.getenv("BINANCE_API_KEY")
    API_SECRET = os.getenv("BINANCE_API_SECRET")
    USE_TESTNET = os.getenv("TRADING_ENV", "SANDBOX") == "SANDBOX"
    
    SYMBOLS = ["BNBUSDT", "XRPUSDT", "SOLUSDT", "ADAUSDT", "DOGEUSDT"]
    USDT_PER_TRADE = 25
    RISK_REWARD = 1.5
    MAX_LOSSES_ROW = 3
    LEVERAGE = 10
    LOOP_SECONDS = 30

    # Infrastructure
    binance = BinanceService(API_KEY, API_SECRET, use_testnet=USE_TESTNET)
    ai = OpenAIAdapter()
    persistence = JsonPersistenceService("session_stats_v2.json")

    # Domain
    risk = RiskManager(USDT_PER_TRADE, RISK_REWARD, MAX_LOSSES_ROW)
    strategies = [
        EMATrapStrategy(),
        ZSStrategy5m(),
        ZSStrategy1m(),
        VWAPOrderFlowStrategy(),
        LiquidationCascadeStrategy(),
        TrendAnticipatorStrategy()
    ]

    # Application
    executor = TradeExecutor(binance, binance, ai, risk)
    manager = PositionManager(binance, binance, ai, risk, persistence)
    bot = TradingBotApp(executor, manager, persistence, binance, binance, ai, risk, SYMBOLS, strategies, leverage=LEVERAGE, use_testnet=USE_TESTNET)

    # Start
    log.info("🚀 Refactored Bot starting...")
    bot.run(LOOP_SECONDS)

if __name__ == "__main__":
    run()
