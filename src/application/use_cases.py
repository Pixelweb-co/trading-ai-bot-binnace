import logging
import time
import threading
from typing import List, Dict, Any, Optional
from ..domain.models import Signal, Position, OrderSide, SessionStats, MarketData, QuantData
from ..domain.risk_manager import RiskManager
from ..domain.strategies import IStrategy
from .interfaces import IMarketDataService, ITradingService, IAIService, IPersistenceService
from ..infrastructure.email_notifier import EmailNotifier

log = logging.getLogger(__name__)

class TradeExecutor:
    def __init__(self, market_data: IMarketDataService, trading: ITradingService, ai: IAIService, risk: RiskManager):
        self.market_data = market_data
        self.trading = trading
        self.ai = ai
        self.risk = risk

    def scan_for_signal(self, symbol: str, strategies: List[IStrategy], session_stats: SessionStats, active_symbols: set, lock: threading.Lock) -> Optional[tuple[Signal, float]]:
        with lock:
            if symbol in active_symbols: return None
            if not self.risk.should_allow_trade(symbol, session_stats): return None

        df1 = self.market_data.get_candles(symbol, "1m")
        df3 = self.market_data.get_candles(symbol, "3m")
        df5 = self.market_data.get_candles(symbol, "5m")
        df15 = self.market_data.get_candles(symbol, "15m")
        
        quant = self.market_data.get_quant_data(symbol)
        obi = self.market_data.get_obi(symbol)

        for strategy in strategies:
            if "1M" in strategy.name:   p_df, s_df = df1, df5
            elif "3M" in strategy.name: p_df, s_df = df3, df15
            elif "15M" in strategy.name: p_df, s_df = df15, df5  # TrendAnticipator: 15m structure, 5m trigger
            else:                        p_df, s_df = df5, df15

            p_df = strategy.add_indicators(p_df)
            s_df = strategy.add_indicators(s_df)
            
            signal = strategy.check_signal(p_df, s_df, obi=obi, quant_data=quant)
            if not signal: continue
            
            signal.symbol = symbol
            
            ok, conf, reason, quality = self.ai.analyze_setup(strategy.name, symbol, signal)
            if not ok or quality == "C":
                log.info(f"🤖 AI Rejected {symbol} ({strategy.name}): {reason}")
                continue

            multiplier = 1.0
            losses = session_stats.consecutive_losses.get(symbol, 0)
            if losses > 0:
                m_ok, m_val, m_reason = self.ai.decide_martingale(symbol, signal, quality, losses)
                if m_ok: multiplier = m_val

            return signal, multiplier
        return None

class PositionManager:
    def __init__(self, market_data: IMarketDataService, trading: ITradingService, ai: IAIService, risk: RiskManager, persistence: IPersistenceService):
        self.market_data = market_data
        self.trading = trading
        self.ai = ai
        self.risk = risk
        self.persistence = persistence

    def sync_positions(self, active_symbols: set, lock: threading.Lock, bot_app: Any, harvest: bool = False, harvest_threshold: float = 1.0, leverage: int = 10):
        try:
            on_exchange = self.trading.get_active_positions()
            current_symbols = {p.symbol for p in on_exchange}
            
            for p in on_exchange:
                # Harvesting logic
                # ... (Simplified for now)
                
                with lock:
                    if p.symbol not in active_symbols:
                        # Skip dust positions (< 1.0 USDT notional)
                        notional = p.entry_price * p.quantity
                        if notional < 1.0:
                            log.debug(f"🧹 [AUDITOR] Skipping dust position in {p.symbol} (${notional:.2f})")
                            continue

                        active_symbols.add(p.symbol)
                        # Default tp/sl if orphaned
                        p.sl = p.entry_price * (0.985 if p.side == OrderSide.LONG else 1.015)
                        p.tp_levels = [p.entry_price * (1.025 if p.side == OrderSide.LONG else 0.975)]
                        log.info(f"🔗 [AUDITOR] Detected external position in {p.symbol}. Starting monitor.")
                        threading.Thread(target=bot_app._monitor_trade, args=(p,), daemon=True).start()

            with lock:
                to_remove = [s for s in active_symbols if s not in current_symbols]
                for s in to_remove:
                    active_symbols.discard(s)

        except Exception as e:
            log.error(f"Error in Sync: {e}")

class TradingBotApp:
    def __init__(self, executor: TradeExecutor, manager: PositionManager, persistence: IPersistenceService, 
                 trading: ITradingService, market: IMarketDataService, ai: IAIService, risk: RiskManager,
                 symbols: List[str], strategies: List[IStrategy], leverage: int = 10, use_testnet: bool = True):
        self.executor = executor
        self.manager = manager
        self.persistence = persistence
        self.trading = trading
        self.market = market
        self.ai = ai
        self.risk = risk
        self.symbols = symbols
        self.strategies = strategies
        self.leverage = leverage
        self.use_testnet = use_testnet
        self.session_stats = SessionStats()
        self.active_symbols = set()
        self.lock = threading.Lock()
        self.bulk_advice = {}
        self.bulk_lock = threading.Lock()
        self._warmup_iterations = 2  # Skip opening new trades for first N cycles on startup
        self._cooldowns: Dict[str, float] = {}  # symbol -> timestamp of last close
        self.COOLDOWN_SECONDS = 300  # 5 min cooldown after any close
        self._last_daily_email: Optional[str] = None  # date string of last daily analysis email
        self.PORTFOLIO_USDT = 100.0  # Dedicated capital for Flash Growth
        self.PERCENT_PER_TRADE = 0.30  # 30% of portfolio per trade
        # Email notifier — instantiated once, all calls fail silently
        try:
            self.notifier = EmailNotifier()
        except Exception:
            self.notifier = None

    MIN_NOTIONAL = 5.0  # Binance minimum order notional in USDT

    def _monitor_trade(self, pos: Position):
        log.info(f"🧵 [THREAD {pos.symbol}] {pos.strategy_name} | Entry: {pos.entry_price}")
        while True:
            try:
                price = self.market.get_price(pos.symbol)
                pos.current_price = price
                
                # Check AI bulk advice
                with self.bulk_lock:
                    advice = self.bulk_advice.get(pos.symbol)
                    if advice == "CLOSE_NOW":
                        log.warning(f"⚠️ [IA {pos.symbol}] Emergency close via AI.")
                        self.bulk_advice.pop(pos.symbol, None)  # clear advice so it doesn't loop
                        break
                    elif advice == "MOVE_SL_TO_BE" and not pos.tp1_hit:
                        pos.sl = pos.entry_price
                        self.bulk_advice.pop(pos.symbol, None)
                        log.info(f"🛡️ [IA {pos.symbol}] SL moved to Break-Even by AI.")
                    elif advice == "REDUCE_RISK" and not pos.tp1_hit:
                        pos.sl = pos.entry_price
                        self.bulk_advice.pop(pos.symbol, None)
                        log.info(f"🛡️ [IA {pos.symbol}] Risk reduced to Break-Even.")

                # TP/SL check
                if self.risk.is_tp_hit(pos, price):
                    if not pos.tp1_hit:
                        qty_to_close = self.trading.get_quantity(pos.symbol, self.risk.usdt_per_trade * 0.75, price, self.leverage)
                        if qty_to_close * price >= self.MIN_NOTIONAL:
                            self.trading.close_position(pos.symbol, pos.side, qty_to_close)
                            pos.qty_remaining -= qty_to_close
                        pos.tp1_hit = True
                        pos.sl = pos.entry_price
                        log.info(f"💰 [TP1 {pos.symbol}] 75% closed. SL moved to BE.")
                    else:
                        break # Close remaining
                elif self.risk.is_sl_hit(pos, price):
                    break

                time.sleep(5)
            except:
                time.sleep(10)

        # Final close — only if notional is above Binance minimum
        if pos.qty_remaining * self.market.get_price(pos.symbol) >= self.MIN_NOTIONAL:
            self.trading.close_position(pos.symbol, pos.side, pos.qty_remaining)
        else:
            log.warning(f"⚠️ [{pos.symbol}] Notional too small to close (qty={pos.qty_remaining:.6f}). Skipping API call.")
        exit_price = self.market.get_price(pos.symbol)
        pnl = (exit_price - pos.entry_price) * pos.qty_remaining if pos.side == OrderSide.LONG else (pos.entry_price - exit_price) * pos.qty_remaining
        
        with self.lock:
            self.active_symbols.discard(pos.symbol)
            if pnl > 0:
                self.session_stats.total_gain += pnl
                self.session_stats.total_wins += 1
                self.session_stats.consecutive_losses[pos.symbol] = 0
            else:
                self.session_stats.total_loss += abs(pnl)
                self.session_stats.total_losses += 1
                self.session_stats.consecutive_losses[pos.symbol] = self.session_stats.consecutive_losses.get(pos.symbol, 0) + 1
            self.persistence.save_stats(self.session_stats)
        
        log.info(f"🏁 [HILO {pos.symbol}] Finished | PnL: {pnl:.2f}")
        # Email notification — trade closed
        if self.notifier:
            side_str = "LONG" if pos.side == OrderSide.LONG else "SHORT"
            close_reason = "TP/SL/AI"
            threading.Thread(
                target=self.notifier.notify_trade_closed,
                args=(pos.symbol, side_str, pos.entry_price, exit_price, pnl, close_reason),
                daemon=True
            ).start()
        # Set cooldown so the symbol isn't re-entered immediately
        with self.lock:
            self._cooldowns[pos.symbol] = time.time()
            log.info(f"⏸️ [{pos.symbol}] Cooldown active for {self.COOLDOWN_SECONDS}s")

    def _select_symbols(self, n: int = 5) -> list:
        """Fetches top candidates from Binance and uses AI to pick the best N.
        Skips dynamic selection on testnet (ticker volumes are unreliable).
        """
        if self.use_testnet:
            log.info(f"🧪 [TESTNET] Using curated symbol list: {self.symbols}")
            return self.symbols

        log.info("🔍 Selecting best symbols from Binance (REAL mode)...")
        try:
            candidates = self.market.get_top_symbols(n=20, min_volume_usdt=200_000_000)
            if candidates:
                symbols = self.ai.pick_best_symbols(candidates, n=n)
                if symbols:
                    log.info(f"✅ Dynamic symbols selected: {symbols}")
                    return symbols
        except Exception as e:
            log.error(f"❌ Symbol selection error: {e}")
        log.warning(f"↩️ Fallback to default symbols: {self.symbols}")
        return self.symbols

    def _check_market_conditions(self) -> tuple:
        """
        Validates market conditions before allowing new trades.
        Returns (is_favorable, reason_string).
        Checks: BTC momentum, ADX trend strength, funding rate, trading session.
        """
        import ta
        from datetime import datetime, timezone
        reasons_ok = []
        reasons_bad = []

        # --- 1. BTC 4h momentum ---
        try:
            btc_df = self.market.get_candles("BTCUSDT", "4h", limit=6)
            btc_change = (btc_df["close"].iloc[-2] - btc_df["close"].iloc[-3]) / btc_df["close"].iloc[-3] * 100
            self._btc_change = round(btc_change, 2)  # Cache for AI bulk context
            if abs(btc_change) >= 0.8:
                reasons_ok.append(f"BTC 4h: {btc_change:+.2f}% ✅")
            else:
                reasons_bad.append(f"BTC 4h plano: {btc_change:+.2f}% (necesita ±0.8%)")
        except Exception as e:
            log.debug(f"BTC check skipped: {e}")

        # --- 2. ADX trend strength (avg across main symbols) ---
        try:
            adx_values = []
            for sym in self.symbols[:3]:  # Check first 3 symbols to avoid rate limiting
                df = self.market.get_candles(sym, "15m", limit=30)
                adx = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14).adx()
                adx_values.append(adx.iloc[-2])
            avg_adx = sum(adx_values) / len(adx_values) if adx_values else 0
            self._avg_adx = round(avg_adx, 1)  # Cache for AI bulk context
            if avg_adx >= 20:
                reasons_ok.append(f"ADX promedio: {avg_adx:.1f} ✅")
            else:
                reasons_bad.append(f"Mercado lateral: ADX={avg_adx:.1f} (necesita ≥20)")
        except Exception as e:
            log.debug(f"ADX check skipped: {e}")

        # --- 3. Funding rate not extreme ---
        try:
            quant = self.market.get_quant_data("BTCUSDT")
            funding = abs(quant.funding_rate)
            if funding <= 0.08:
                reasons_ok.append(f"Funding BTC: {funding:.4f}% ✅")
            else:
                reasons_bad.append(f"Funding extremo: {funding:.4f}% (mercado sobrecalentado)")
        except Exception as e:
            log.debug(f"Funding check skipped: {e}")

        # --- 4. Active trading session (not 20:00-23:59 UTC dead zone) ---
        try:
            utc_hour = datetime.now(timezone.utc).hour
            if not (20 <= utc_hour <= 23):
                reasons_ok.append(f"Sesión activa: hora UTC={utc_hour} ✅")
            else:
                reasons_bad.append(f"Hora UTC {utc_hour}: volumen bajo (20-23 UTC)")
        except Exception as e:
            log.debug(f"Session check skipped: {e}")

        is_favorable = len(reasons_bad) == 0
        summary = " | ".join(reasons_ok + [f"⚠️ {r}" for r in reasons_bad])
        return is_favorable, summary

    def run(self, loop_seconds: int):
        self.session_stats = self.persistence.load_stats()
        log.info("Starting Trading Bot App...")

        # --- Dynamic symbol selection at startup ---
        self.symbols = self._select_symbols(n=5)

        # Initial config for selected symbols
        for s in self.symbols:
            self.trading.change_margin_type(s, "ISOLATED")
            self.trading.change_leverage(s, self.leverage)

        self.manager.sync_positions(self.active_symbols, self.lock, self)

        # Email: bot started
        if self.notifier:
            threading.Thread(
                target=self.notifier.notify_bot_started,
                args=(self.symbols,), daemon=True
            ).start()
        
        iteration = 0
        while True:
            try:
                # 1. Scan for new signals — skip during warmup
                if iteration <= self._warmup_iterations:
                    remaining = (self._warmup_iterations - iteration + 1) * loop_seconds
                    log.info(f"⏳ [WARMUP] Watching only. New trades in ~{remaining}s...")
                else:
                    # Market condition gate — evaluate every 4 iterations to avoid excess API calls
                    if iteration % 4 == 0:
                        self._market_ok, self._market_reason = self._check_market_conditions()
                        if self._market_ok:
                            log.info(f"🌍 [MERCADO OK] {self._market_reason}")
                        else:
                            log.warning(f"🚫 [MERCADO DESFAV.] {self._market_reason} — Pausando nuevas entradas")

                        # Daily market analysis email — once per calendar day
                        from datetime import datetime, timezone
                        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        if self.notifier and self._last_daily_email != today:
                            self._last_daily_email = today
                            try:
                                btc_df   = self.market.get_candles("BTCUSDT", "4h", limit=6)
                                btc_chg  = (btc_df["close"].iloc[-2] - btc_df["close"].iloc[-3]) / btc_df["close"].iloc[-3] * 100
                                quant    = self.market.get_quant_data("BTCUSDT")
                                funding  = abs(quant.funding_rate)
                                import ta
                                adx_vals = []
                                for sym in self.symbols[:3]:
                                    df = self.market.get_candles(sym, "15m", limit=30)
                                    adx_vals.append(ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14).adx().iloc[-2])
                                avg_adx = sum(adx_vals) / len(adx_vals) if adx_vals else 0
                                ai_pred = self.ai.analyze_setup.__doc__ or "Sin predicción disponible"
                                try:
                                    ai_pred = self.ai.pick_best_symbols(
                                        [{"symbol": s, "volume": 0, "price_chg": 0, "count": 0, "score": 0} for s in self.symbols],
                                        n=len(self.symbols)
                                    )
                                    ai_pred = f"Mejores pares hoy: {', '.join(ai_pred)}"
                                except Exception:
                                    ai_pred = "Predicción no disponible (sin datos suficientes)"
                                threading.Thread(
                                    target=self.notifier.notify_market_analysis,
                                    args=(btc_chg, avg_adx, funding, self._market_ok,
                                          self._market_reason, self.symbols, ai_pred),
                                    daemon=True
                                ).start()
                            except Exception as e:
                                log.debug(f"Daily email skipped: {e}")

                    if not getattr(self, '_market_ok', True):
                        pass  # Skip new trades, existing positions still monitored by threads
                    else:
                        for symbol in self.symbols:
                            # Check per-symbol cooldown
                            with self.lock:
                                last_close = self._cooldowns.get(symbol, 0)
                                cooldown_remaining = self.COOLDOWN_SECONDS - (time.time() - last_close)
                            if cooldown_remaining > 0:
                                log.debug(f"⏸️ [{symbol}] In cooldown ({cooldown_remaining:.0f}s left)")
                                continue

                            res = self.executor.scan_for_signal(symbol, self.strategies, self.session_stats, self.active_symbols, self.lock)
                            if res:
                                signal, multiplier = res
                                
                                # AI Confidence Check for Flash Growth
                                approve, confidence, reason, quality = self.ai.analyze_setup(signal.strategy_name, symbol, signal)
                                
                                if approve:
                                    # Aggressive Leverage Scaling (15x base, 20x for high-confidence)
                                    final_leverage = 15
                                    if int(confidence) >= 90:
                                        final_leverage = 20
                                        log.info(f"💎 [PREMIUM SIGNAL] {symbol} | Confidence: {confidence}% | Upscaling Leverage to {final_leverage}x")
                                    
                                    # Ensure exchange leverage matches
                                    self.trading.change_leverage(symbol, final_leverage)
                                    
                                    # Dynamic Quantity Calculation (30% of 100 USDT balance + session pnl)
                                    virtual_balance = self.PORTFOLIO_USDT + (self.session_stats.total_gain - self.session_stats.total_loss)
                                    usdt_to_invest = max(virtual_balance * self.PERCENT_PER_TRADE, self.MIN_NOTIONAL)
                                    
                                    price = self.market.get_price(symbol)
                                    qty = self.trading.get_quantity(symbol, usdt_to_invest, price, final_leverage)
                                    
                                    side_str = "BUY" if signal.side == OrderSide.LONG else "SELL"
                                    order = self.trading.place_order(symbol, side_str, qty)
                                    if order:
                                        pos = Position(symbol, signal.side, price, qty, signal.tp_levels, signal.sl, signal.strategy_name)
                                        with self.lock: self.active_symbols.add(symbol)
                                        threading.Thread(target=self._monitor_trade, args=(pos,), daemon=True).start()
                                        
                                        # Email: trade opened
                                        if self.notifier:
                                            tp_val = signal.tp_levels[0] if signal.tp_levels else 0
                                            threading.Thread(
                                                target=self.notifier.notify_trade_opened,
                                                args=(symbol, signal.side.value, price, qty, signal.sl, tp_val, signal.strategy_name),
                                                daemon=True
                                            ).start()
                                            
                                        # Small delay to avoid rapid-fire orders on multiple symbols
                                        time.sleep(5)


                
                # 2. Maintenance
                iteration += 1
                if iteration % 20 == 0:
                    with self.lock:
                        subjects = list(self.active_symbols)
                    if subjects:
                        log.info(f"🧠 [IA BULK] Analyzing {len(subjects)} positions...")
                    # Email: P&L summary every 20 cycles (~10 min)
                    if self.notifier:
                        stats = self.session_stats
                        total = stats.total_gain - stats.total_loss
                        total_trades = stats.total_wins + stats.total_losses
                        wr = (stats.total_wins / total_trades * 100) if total_trades > 0 else 0
                        with self.lock:
                            active = list(self.active_symbols)

                        # Enrich position data with technical indicators for smarter AI decisions
                        import ta as ta_lib
                        pos_info = []
                        positions = self.trading.get_active_positions()
                        for p in positions:
                            if p.symbol in subjects:
                                curr = self.market.get_price(p.symbol)
                                pnl = ((curr - p.entry_price)/p.entry_price*100) if p.side == OrderSide.LONG else ((p.entry_price - curr)/p.entry_price*100)
                                entry = {'symbol': p.symbol, 'side': p.side.value, 'entry': p.entry_price, 'price': curr, 'pnl': pnl}
                                # Compute ADX and RSI from 15m candles
                                try:
                                    df = self.market.get_candles(p.symbol, "15m", limit=30)
                                    adx = ta_lib.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14).adx().iloc[-2]
                                    rsi = ta_lib.momentum.RSIIndicator(df["close"], window=14).rsi().iloc[-2]
                                    ema21 = ta_lib.trend.EMAIndicator(df["close"], window=21).ema_indicator().iloc[-2]
                                    trend = "UP" if curr > ema21 else "DOWN"
                                    entry.update({'adx': adx, 'rsi': rsi, 'trend': trend})
                                except Exception:
                                    pass  # Proceed without technicals if fetching fails
                                pos_info.append(entry)
                        
                        # Build market context for AI
                        market_ctx = None
                        try:
                            market_ctx = {
                                'btc_momentum': f"{getattr(self, '_btc_change', 'N/A')}",
                                'avg_adx': f"{getattr(self, '_avg_adx', 'N/A')}",
                                'market_ok': getattr(self, '_market_ok', True)
                            }
                        except Exception:
                            pass

                        advice = {}
                        if pos_info:
                            advice = self.ai.analyze_bulk_positions(pos_info, market_context=market_ctx)
                            with self.bulk_lock:
                                self.bulk_advice.update(advice)
                            log.info(f"✅ [IA BULK] Recommendations: {advice}")

                            # Email: AI decision (only if actionable)
                            if self.notifier and advice:
                                threading.Thread(
                                    target=self.notifier.notify_ai_bulk_decision,
                                    args=(advice, pos_info, market_ctx),
                                    daemon=True
                                ).start()

                        # Email: P&L summary (now includes market context and AI recs)
                        last_advice = getattr(self, '_last_bulk_advice', None)
                        if advice:
                            self._last_bulk_advice = advice
                            last_advice = advice
                        threading.Thread(
                            target=self.notifier.notify_pnl_summary,
                            args=(stats.total_wins, stats.total_losses, total, wr,
                                  stats.total_gain, -stats.total_loss, active),
                            kwargs={'market_context': market_ctx,
                                    'ai_recommendations': last_advice},
                            daemon=True
                        ).start()

                # Refresh symbols every 120 iterations (~1h with 30s loops)
                if iteration % 120 == 0 and iteration > 0:
                    new_symbols = self._select_symbols(n=5)
                    if new_symbols != self.symbols:
                        log.info(f"🔄 Symbols updated: {self.symbols} → {new_symbols}")
                        self.symbols = new_symbols
                        for s in self.symbols:
                            self.trading.change_margin_type(s, "ISOLATED")
                            self.trading.change_leverage(s, self.leverage)

                if iteration % 60 == 0:
                    insight = self.ai.get_market_insight(self.symbols)
                    log.info(f"💡 Market Insight: {insight}")

                if iteration % 10 == 0:
                    self.manager.sync_positions(self.active_symbols, self.lock, self, harvest=True)

                time.sleep(loop_seconds)
            except Exception as e:
                log.error(f"Loop error: {e}")
                time.sleep(60)
