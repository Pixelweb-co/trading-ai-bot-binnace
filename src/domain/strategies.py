from abc import ABC, abstractmethod
import pandas as pd
import numpy as np
import ta
from typing import Optional, List, Dict, Any
from .models import Signal, OrderSide, MarketData, QuantData

class IStrategy(ABC):
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        pass

    @abstractmethod
    def check_signal(self, primary_df: pd.DataFrame, secondary_df: pd.DataFrame, **kwargs) -> Optional[Signal]:
        pass

    def calculate_pivots(self, df: pd.DataFrame) -> Dict[str, float]:
        prev = df.iloc[-2]
        p = (prev['high'] + prev['low'] + prev['close']) / 3
        r1 = (2 * p) - prev['low']
        s1 = (2 * p) - prev['high']
        r2 = p + (prev['high'] - prev['low'])
        s2 = p - (prev['high'] - prev['low'])
        return {'P': p, 'R1': r1, 'S1': s1, 'R2': r2, 'S2': s2}

class EMATrapStrategy(IStrategy):
    def __init__(self, ema_fast=9, ema_slow=21, rsi_period=14, rsi_min=40, rsi_max=60):
        super().__init__("EMA_TRAP_5M")
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.rsi_min = rsi_min
        self.rsi_max = rsi_max

    def add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["ema_fast"] = ta.trend.EMAIndicator(df["close"], window=self.ema_fast).ema_indicator()
        df["ema_slow"] = ta.trend.EMAIndicator(df["close"], window=self.ema_slow).ema_indicator()
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=self.rsi_period).rsi()
        df["adx"] = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14).adx()
        df["vol_avg"] = df["volume"].rolling(20).mean()
        df["atr"] = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
        return df

    def check_signal(self, primary_df: pd.DataFrame, secondary_df: pd.DataFrame, **kwargs) -> Optional[Signal]:
        c5  = primary_df.iloc[-2]
        p5  = primary_df.iloc[-3]
        c15 = secondary_df.iloc[-2]

        trend_up   = c15["close"] > c15["ema_slow"]
        trend_down = c15["close"] < c15["ema_slow"]

        cross_up   = (p5["ema_fast"] <= p5["ema_slow"]) and (c5["ema_fast"] > c5["ema_slow"])
        cross_down = (p5["ema_fast"] >= p5["ema_slow"]) and (c5["ema_fast"] < c5["ema_slow"])

        rsi_ok     = self.rsi_min <= c5["rsi"] <= self.rsi_max
        vol_ok     = c5["volume"] > c5["vol_avg"]
        adx_ok     = c5["adx"] > 25

        if trend_up and cross_up and rsi_ok and vol_ok and c5["close"] > c5["open"] and adx_ok:
            tp = c5["close"] + (c5["atr"] * 1.5)
            sl = c5["close"] - (c5["atr"] * 1.0)
            return Signal(symbol="", strategy_name=self.name, side=OrderSide.LONG, entry_price=c5["close"], tp_levels=[tp], sl=sl)
            
        if trend_down and cross_down and rsi_ok and vol_ok and c5["close"] < c5["open"]:
            tp = c5["close"] - (c5["atr"] * 1.5)
            sl = c5["close"] + (c5["atr"] * 1.0)
            return Signal(symbol="", strategy_name=self.name, side=OrderSide.SHORT, entry_price=c5["close"], tp_levels=[tp], sl=sl)

        return None

class RSIScalpStrategy(IStrategy):
    def __init__(self, rsi_overbought=70, rsi_oversold=30, rsi_period=14):
        super().__init__("RSI_SCALP")
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.rsi_period = rsi_period

    def add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=self.rsi_period).rsi()
        df["atr"] = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
        return df

    def check_signal(self, primary_df: pd.DataFrame, secondary_df: pd.DataFrame, **kwargs) -> Optional[Signal]:
        c5 = primary_df.iloc[-2]
        if c5["rsi"] < self.rsi_oversold:
            tp = c5["close"] + (c5["atr"] * 2)
            sl = c5["close"] - (c5["atr"] * 1)
            return Signal(symbol="", strategy_name=self.name, side=OrderSide.LONG, entry_price=c5["close"], tp_levels=[tp], sl=sl)
        if c5["rsi"] > self.rsi_overbought:
            tp = c5["close"] - (c5["atr"] * 2)
            sl = c5["close"] + (c5["atr"] * 1)
            return Signal(symbol="", strategy_name=self.name, side=OrderSide.SHORT, entry_price=c5["close"], tp_levels=[tp], sl=sl)
        return None

class ZSStrategy5m(IStrategy):
    def __init__(self, ema_period=12):
        super().__init__("ZS_5M")
        self.ema_period = ema_period

    def add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["ema12"] = ta.trend.EMAIndicator(df["close"], window=self.ema_period).ema_indicator()
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
        df["adx"] = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14).adx()
        df["atr"] = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
        return df

    def check_signal(self, primary_df: pd.DataFrame, secondary_df: pd.DataFrame, **kwargs) -> Optional[Signal]:
        c5 = primary_df.iloc[-2]
        pivots = self.calculate_pivots(primary_df)
        is_near_pivot = abs(c5["close"] - pivots["P"]) / c5["close"] < 0.002
        adx_ok = c5["adx"] > 25

        if c5["close"] > c5["ema12"] and is_near_pivot and adx_ok:
            tp1 = c5["close"] * 1.017
            sl = pivots["S1"] if pivots["S1"] < c5["close"] else c5["close"] * 0.99
            return Signal(symbol="", strategy_name=self.name, side=OrderSide.LONG, entry_price=c5["close"], tp_levels=[tp1], sl=sl)
        if c5["close"] < c5["ema12"] and is_near_pivot:
            tp1 = c5["close"] * 0.983
            sl = pivots["R1"] if pivots["R1"] > c5["close"] else c5["close"] * 1.01
            return Signal(symbol="", strategy_name=self.name, side=OrderSide.SHORT, entry_price=c5["close"], tp_levels=[tp1], sl=sl)
        return None

class ZSStrategy1m(IStrategy):
    def __init__(self, ema_period=15):
        super().__init__("ZS_1M_ADV")
        self.ema_period = ema_period

    def add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["ema15"] = ta.trend.EMAIndicator(df["close"], window=self.ema_period).ema_indicator()
        v = df['volume']
        tp = (df['high'] + df['low'] + df['close']) / 3
        df['vwap'] = (tp * v).cumsum() / v.cumsum()
        df["atr"] = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
        return df

    def check_signal(self, primary_df: pd.DataFrame, secondary_df: pd.DataFrame, **kwargs) -> Optional[Signal]:
        c1 = primary_df.iloc[-2]
        if c1["close"] > c1["ema15"] and c1["close"] < c1["vwap"]:
            tp1, sl = c1["close"] * 1.01, c1["close"] * 0.99
            return Signal(symbol="", strategy_name=self.name, side=OrderSide.LONG, entry_price=c1["close"], tp_levels=[tp1], sl=sl)
        if c1["close"] < c1["ema15"] and c1["close"] > c1["vwap"]:
            tp1, sl = c1["close"] * 0.99, c1["close"] * 1.01
            return Signal(symbol="", strategy_name=self.name, side=OrderSide.SHORT, entry_price=c1["close"], tp_levels=[tp1], sl=sl)
        return None

class VWAPOrderFlowStrategy(IStrategy):
    def __init__(self):
        super().__init__("VWAP_ORDERFLOW_1M")
        self.delta_lookback = 20

    def add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        num_cols = ["open", "high", "low", "close", "volume", "taker_base"]
        for col in num_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
        df["atr"] = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
        df["adx"] = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14).adx()
        df["ema21"] = ta.trend.EMAIndicator(df["close"], window=21).ema_indicator()
        df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
        df["tp_vol"] = df["typical_price"] * df["volume"]
        df["tp_vol2"] = (df["typical_price"] ** 2) * df["volume"]
        df["date"] = pd.to_datetime(df["open_time"]).dt.date
        df["cum_tp_vol"] = df.groupby("date")["tp_vol"].cumsum()
        df["cum_vol"] = df.groupby("date")["volume"].cumsum()
        df["cum_tp_vol2"] = df.groupby("date")["tp_vol2"].cumsum()
        df["vwap"] = df["cum_tp_vol"] / df["cum_vol"]
        df["vwap_var"] = ((df["cum_tp_vol2"] / df["cum_vol"]) - df["vwap"] ** 2).clip(lower=0)
        df["vwap_std"] = np.sqrt(df["vwap_var"])
        df["taker_sell"] = df["volume"] - df["taker_base"]
        df["delta"] = df["taker_base"] - df["taker_sell"]
        df["cum_delta"] = df["delta"].rolling(self.delta_lookback).sum()
        max_d = df["cum_delta"].abs().rolling(50).max().replace(0, 1)
        df["delta_norm"] = df["cum_delta"] / max_d
        return df

    def check_signal(self, primary_df: pd.DataFrame, secondary_df: pd.DataFrame, **kwargs) -> Optional[Signal]:
        obi = kwargs.get('obi', 0.5)
        c1, p1, c5 = primary_df.iloc[-2], primary_df.iloc[-3], secondary_df.iloc[-2]
        price, vwap, vstd = c1["close"], c1["vwap"], c1["vwap_std"]
        adx_ok, rsi_ok = c1["adx"] > 20, 25 < c1["rsi"] < 75
        if not (adx_ok and rsi_ok): return None
        trend_up, trend_down = c5["close"] > c5["ema21"], c5["close"] < c5["ema21"]
        delta_bull, delta_bear = c1["delta_norm"] > 0.2, c1["delta_norm"] < -0.2
        z_score = (price - vwap) / vstd if vstd > 0 else 0
        
        meta = {"vwap": round(vwap, 2), "pos": f"Z:{z_score:.2f}", "obi": round(obi, 3), 
                "delta_norm": round(c1["delta_norm"], 3), "rsi": round(c1["rsi"], 1), "adx": round(c1["adx"], 1)}

        if (z_score > -1.5 and z_score < 0.2) and c1["close"] > p1["close"] and c1["close"] > c1["open"] and obi > 0.62 and delta_bull and trend_up:
            tp = price + (price - (price - c1["atr"]*1.5)) * 2.0
            return Signal(symbol="", strategy_name=self.name, side=OrderSide.LONG, entry_price=price, tp_levels=[tp], sl=price - (c1["atr"] * 1.5), meta=meta)
        if (z_score < 1.5 and z_score > -0.2) and c1["close"] < p1["close"] and c1["close"] < c1["open"] and obi < 0.38 and delta_bear and trend_down:
            tp = price - ((price + c1["atr"]*1.5) - price) * 2.0
            return Signal(symbol="", strategy_name=self.name, side=OrderSide.SHORT, entry_price=price, tp_levels=[tp], sl=price + (c1["atr"] * 1.5), meta=meta)
        return None

class LiquidationCascadeStrategy(IStrategy):
    def __init__(self):
        super().__init__("LIQ_CASCADE_3M")
        self.funding_extreme, self.oi_spike_pct, self.cvd_div_thresh = 0.0008, 0.03, 0.4

    def add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["ema21"] = ta.trend.EMAIndicator(df["close"], window=21).ema_indicator()
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
        df["atr"] = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
        df["taker_sell"] = df["volume"] - df["taker_base"]
        df["delta"] = df["taker_base"] - df["taker_sell"]
        df["cvd"] = df["delta"].cumsum()
        for col, norm_name in [("cvd", "cvd_norm"), ("close", "price_norm")]:
            roll_min, roll_max = df[col].rolling(50).min(), df[col].rolling(50).max()
            df[norm_name] = (df[col] - roll_min) / (roll_max - roll_min + 1e-9)
        df["cvd_div"] = df["price_norm"] - df["cvd_norm"]
        return df

    def check_signal(self, primary_df: pd.DataFrame, secondary_df: pd.DataFrame, **kwargs) -> Optional[Signal]:
        quant_data: QuantData = kwargs.get('quant_data')
        if not quant_data: return None
        c3 = primary_df.iloc[-2]
        price, atr = c3["close"], c3["atr"]
        funding, oi_change, ls_ratio = quant_data.funding_rate, quant_data.oi_change, quant_data.ls_ratio
        liq_sides = quant_data.liquidation_sides
        funding_bull, funding_bear = funding > self.funding_extreme, funding < -self.funding_extreme
        oi_high, bull_trap, bear_trap = oi_change > self.oi_spike_pct, c3["cvd_div"] > self.cvd_div_thresh, c3["cvd_div"] < -self.cvd_div_thresh
        flush_longs, flush_shorts = liq_sides.get("SELL", 0) > 100_000, liq_sides.get("BUY", 0) > 100_000
        meta = {"funding": round(funding, 6), "oi_change": round(oi_change, 4), "ls_ratio": round(ls_ratio, 2), 
                "cvd_div": round(c3["cvd_div"], 4), "liq_sell": round(liq_sides.get("SELL", 0), 0), "liq_buy": round(liq_sides.get("BUY", 0), 0)}

        if funding_bull and ls_ratio > 1.4 and oi_high and flush_longs and c3["rsi"] < 40:
            meta["logic"] = "LONG_AFTER_FLUSH"
            return Signal("", self.name, OrderSide.LONG, price, [price + (atr * 3.0)], price - (atr * 1.5), meta=meta)
        if funding_bear and ls_ratio < 0.7 and oi_high and flush_shorts and c3["rsi"] > 60:
            meta["logic"] = "SHORT_AFTER_SQUEEZE"
            return Signal("", self.name, OrderSide.SHORT, price, [price - (atr * 3.0)], price + (atr * 1.5), meta=meta)
        if funding_bull and bull_trap and c3["rsi"] > 75:
            meta["logic"] = "ANTICIPATE_FLUSH"
            return Signal("", self.name, OrderSide.SHORT, price, [price - (atr * 2.5)], price + (atr * 1.5), meta=meta)
        if funding_bear and bear_trap and c3["rsi"] < 25:
            meta["logic"] = "ANTICIPATE_SQUEEZE"
            return Signal("", self.name, OrderSide.LONG, price, [price + (atr * 2.5)], price - (atr * 1.5), meta=meta)
        return None

class TrendAnticipatorStrategy(IStrategy):
    """
    Anticipates trend direction BEFORE the move happens.
    Primary timeframe: 15m (trend structure)
    Confirmation: 5m (entry trigger via MACD cross)

    Signals based on:
    1. Break of Structure (BOS) — new swing high/low breaking prior structure
    2. Hidden RSI Divergence   — confirms trend continuation, not reversal
    3. MACD histogram cross    — entry momentum trigger on 5m
    4. POC proximity            — price near volume-weighted gravity zone
    """

    def __init__(self):
        super().__init__("TREND_ANTICIPATOR_15M")

    def add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['swing_high'] = df['high'].rolling(5, center=True).max()
        df['swing_low']  = df['low'].rolling(5, center=True).min()
        df['rsi']        = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
        macd_obj         = ta.trend.MACD(df['close'], window_slow=26, window_fast=12, window_sign=9)
        df['macd_diff']  = macd_obj.macd_diff()
        df['atr']        = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range()
        # Volume POC approximation: VWMA over 20 bars
        df['poc']        = (df['close'] * df['volume']).rolling(20).sum() / df['volume'].rolling(20).sum()
        return df

    def check_signal(self, primary_df: pd.DataFrame, secondary_df: pd.DataFrame, **kwargs) -> Optional[Signal]:
        """primary_df=15m (structure), secondary_df=5m (entry trigger)"""
        if len(primary_df) < 10 or len(secondary_df) < 5:
            return None

        c15  = primary_df.iloc[-2]
        p15  = primary_df.iloc[-3]
        pp15 = primary_df.iloc[-4]
        c5   = secondary_df.iloc[-2]
        p5   = secondary_df.iloc[-3]

        # 1. Break of Structure
        bos_long  = (c15['high'] > p15['swing_high']) and (p15['close'] > pp15['close'])
        bos_short = (c15['low']  < p15['swing_low'])  and (p15['close'] < pp15['close'])

        # 2. Hidden RSI divergence (trend continuation, not reversal)
        hidden_div_long  = (c15['low']  > p15['low'])  and (c15['rsi'] > p15['rsi']) and (c15['rsi'] < 60)
        hidden_div_short = (c15['high'] < p15['high']) and (c15['rsi'] < p15['rsi']) and (c15['rsi'] > 40)

        # 3. MACD histogram cross on 5m (entry trigger)
        macd_bull = (p5['macd_diff'] < 0) and (c5['macd_diff'] > 0)
        macd_bear = (p5['macd_diff'] > 0) and (c5['macd_diff'] < 0)

        # 4. Near POC (volume gravity zone ±0.4%)
        near_poc = abs(c15['close'] - c15['poc']) / c15['close'] < 0.004

        price = c15['close']
        atr   = c15['atr']

        if bos_long and hidden_div_long and macd_bull and near_poc:
            tp = price + atr * 2.5
            sl = min(c15['low'], p15['low']) - atr * 0.3
            return Signal(
                symbol="", strategy_name=self.name,
                side=OrderSide.LONG,
                entry_price=price,
                tp_levels=[tp], sl=sl,
                quality="A"
            )

        if bos_short and hidden_div_short and macd_bear and near_poc:
            tp = price - atr * 2.5
            sl = max(c15['high'], p15['high']) + atr * 0.3
            return Signal(
                symbol="", strategy_name=self.name,
                side=OrderSide.SHORT,
                entry_price=price,
                tp_levels=[tp], sl=sl,
                quality="A"
            )

        return None
