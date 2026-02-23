from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Dict, Optional, Any
import pandas as pd

class OrderSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    BUY = "BUY"
    SELL = "SELL"

@dataclass
class MarketData:
    symbol: str
    interval: str
    df: pd.DataFrame
    last_price: float

@dataclass
class QuantData:
    funding_rate: float
    oi_value: float
    oi_change: float
    ls_ratio: float
    liquidations_usd: float
    liquidation_sides: Dict[str, float]

@dataclass
class Signal:
    symbol: str
    strategy_name: str
    side: OrderSide
    entry_price: float
    tp_levels: List[float]
    sl: float
    quality: str = "B"
    meta: Optional[Dict[str, Any]] = None
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class Position:
    symbol: str
    side: OrderSide
    entry_price: float
    quantity: float
    tp_levels: List[float]
    sl: float
    strategy_name: str
    start_time: datetime = field(default_factory=datetime.now)
    current_price: float = 0.0
    tp1_hit: bool = False
    qty_remaining: float = 0.0

    def __post_init__(self):
        self.qty_remaining = self.quantity
        self.current_price = self.entry_price

@dataclass
class TradeResult:
    symbol: str
    side: OrderSide
    entry_price: float
    exit_price: float
    quantity: float
    pnl_usdt: float
    strategy_name: str
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class SessionStats:
    total_gain: float = 0.0
    total_loss: float = 0.0
    total_wins: int = 0
    total_losses: int = 0
    consecutive_losses: Dict[str, int] = field(default_factory=dict)

    @property
    def net_pnl(self) -> float:
        return self.total_gain - self.total_loss
