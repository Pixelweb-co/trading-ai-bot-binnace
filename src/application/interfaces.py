from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, Tuple
import pandas as pd
from ..domain.models import MarketData, QuantData, Signal, Position, OrderSide, SessionStats, TradeResult

class IMarketDataService(ABC):
    @abstractmethod
    def get_candles(self, symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
        pass

    @abstractmethod
    def get_price(self, symbol: str) -> float:
        pass

    @abstractmethod
    def get_obi(self, symbol: str, levels: int = 10) -> float:
        pass

    @abstractmethod
    def get_quant_data(self, symbol: str) -> QuantData:
        pass

    @abstractmethod
    def get_top_symbols(self, n: int = 20, min_volume_usdt: float = 200_000_000) -> list:
        """Returns list of top N symbols sorted by scalping opportunity score."""
        pass

class ITradingService(ABC):
    @abstractmethod
    def place_order(self, symbol: str, side: str, quantity: float) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_active_positions(self) -> List[Position]:
        pass

    @abstractmethod
    def get_quantity(self, symbol: str, usdt_amount: float, price: float, leverage: int) -> float:
        pass

    @abstractmethod
    def close_position(self, symbol: str, side: OrderSide, quantity: float) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    def change_leverage(self, symbol: str, leverage: int):
        pass

    @abstractmethod
    def change_margin_type(self, symbol: str, margin_type: str):
        pass

class IAIService(ABC):
    @abstractmethod
    def analyze_setup(self, strategy_name: str, symbol: str, signal: Signal) -> Tuple[bool, str, str, str]:
        """Returns (is_ok, confidence, reason, quality)"""
        pass

    @abstractmethod
    def analyze_bulk_positions(self, positions_data: List[Dict[str, Any]], market_context: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        """Returns map of symbol to action (CLOSE_NOW, REDUCE_RISK, etc)"""
        pass

    @abstractmethod
    def get_market_insight(self, symbols: List[str]) -> str:
        pass

    @abstractmethod
    def decide_martingale(self, symbol: str, signal: Signal, quality: str, consecutive_losses: int) -> Tuple[bool, float, str]:
        """Returns (should_allow, multiplier, reason)"""
        pass

    @abstractmethod
    def pick_best_symbols(self, candidates: List[Dict], n: int = 5) -> List[str]:
        """Given a list of scored symbol dicts, AI picks the best N for scalping."""
        pass

class IPersistenceService(ABC):
    @abstractmethod
    def save_stats(self, stats: SessionStats):
        pass

    @abstractmethod
    def load_stats(self) -> SessionStats:
        pass
