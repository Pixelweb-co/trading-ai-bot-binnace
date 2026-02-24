from .models import Position, OrderSide, SessionStats, Signal
from typing import Tuple

class RiskManager:
    def __init__(self, usdt_per_trade: float, risk_reward_ratio: float, max_losses_row: int):
        self.usdt_per_trade = usdt_per_trade
        self.risk_reward_ratio = risk_reward_ratio
        self.max_losses_row = max_losses_row

    def calculate_tp_sl(self, side: OrderSide, entry_price: float, sl_percent: float = 0.015) -> Tuple[float, float]:
        """Calcula niveles de TP y SL básicos si la estrategia no los provee."""
        if side == OrderSide.LONG:
            sl = entry_price * (1 - sl_percent)
            tp = entry_price + (entry_price - sl) * self.risk_reward_ratio
        else:
            sl = entry_price * (1 + sl_percent)
            tp = entry_price - (sl - entry_price) * self.risk_reward_ratio
        return tp, sl

    def should_allow_trade(self, symbol: str, session_stats: SessionStats) -> bool:
        """Verifica si se permite operar según el estado de la sesión."""
        losses = session_stats.consecutive_losses.get(symbol, 0)
        if losses >= self.max_losses_row:
            return False
        return True

    def calculate_trailing_stop(self, position: Position, current_price: float) -> float:
        """
        Determina el nuevo Stop Loss basado en el progreso del trade.
        Implementa Break-Even Agresivo: +0.25% ROI -> SL a Precio Entrada + Pequeño margen.
        """
        roi = 0
        if position.side == OrderSide.LONG:
            roi = (current_price / position.entry_price) - 1
        else:
            roi = 1 - (current_price / position.entry_price)
        
        # Aggressive Break-Even at +0.25%
        if roi >= 0.0025:
            # Move to entry price (protected)
            new_sl = position.entry_price
            if position.side == OrderSide.LONG:
                return max(position.sl, new_sl)
            else:
                return min(position.sl, new_sl)
                
        return position.sl

    def is_tp_hit(self, position: Position, current_price: float) -> bool:
        """
        Check TP with Dynamic extension for high-trend (ADX) scenarios.
        """
        target = position.tp_levels[0]
        # Potentially extend target logic could go here, but for now we follow the initial signal TP
        if position.side == OrderSide.LONG:
            return current_price >= target
        else:
            return current_price <= target

    def is_sl_hit(self, position: Position, current_price: float) -> bool:
        if position.side == OrderSide.LONG:
            return current_price <= position.sl
        else:
            return current_price >= position.sl
