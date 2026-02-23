import os
import logging
import json
import time
from typing import List, Dict, Any, Tuple, Optional
from openai import OpenAI
from ..application.interfaces import IAIService
from ..domain.models import Signal, Position, OrderSide

log = logging.getLogger(__name__)

class OpenAIAdapter(IAIService):
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            log.warning("⚠️ OPENAI_API_KEY not found in .env. AI disabled.")
            self.client = None
        else:
            self.client = OpenAI(api_key=self.api_key)
            self.model = "gpt-4o-mini"
            self._last_market_insight = None
            self._last_insight_time = 0

    def analyze_setup(self, strategy_name: str, symbol: str, signal: Signal) -> Tuple[bool, str, str, str]:
        """Routes analysis to the correct prompt based on strategy type."""
        if not self.client:
            return True, "100", "IA no disponible", "B"

        if "LIQ_CASCADE" in strategy_name:
            return self._analyze_cascade_setup(symbol, signal)
        elif "VWAP" in strategy_name:
            return self._analyze_vwap_setup(symbol, signal)
        else:
            # ZS, EMA_TRAP, RSI_SCALP — use a generic technical analysis prompt
            return self._analyze_generic_setup(symbol, strategy_name, signal)

    def _analyze_generic_setup(self, symbol: str, strategy_name: str, signal: Signal) -> Tuple[bool, str, str, str]:
        """Generic analysis for momentum/pivot strategies using R:R and basic stats."""
        entry = signal.entry_price
        tp = signal.tp_levels[0] if signal.tp_levels else 0
        sl = signal.sl
        side = signal.side.value

        # Calculate risk/reward ratio
        if side == "LONG":
            risk = entry - sl if sl and sl > 0 else 1
            reward = tp - entry if tp else 0
        else:
            risk = sl - entry if sl and sl > 0 else 1
            reward = entry - tp if tp else 0

        rr = round(reward / risk, 2) if risk > 0 else 0
        roi_tp = round((reward / entry) * 100, 2) if entry > 0 else 0
        roi_sl = round((risk / entry) * 100, 2) if entry > 0 else 0

        prompt = f"""
Actúa como un gestor de riesgo de trading algorítmico.
Evalúa si esta señal de {strategy_name} para {symbol} tiene sentido ejecutar:

Dirección: {side}
Entrada: {entry} | TP: {tp} | SL: {sl}
Ratio Riesgo/Beneficio calculado: {rr}:1
ROI potencial al TP: +{roi_tp}% | Pérdida potencial al SL: -{roi_sl}%

Criterios de aprobación:
- R:R debe ser >= 1.2
- El ROI al TP debe ser realista para scalping (0.5% - 3%)
- El SL no debe ser >= 3% desde la entrada

Responde estrictamente en formato JSON:
{{
    "decision": true/false,
    "confianza": int entre 0 y 100,
    "razon": "frase corta",
    "setup_quality": "A", "B" o "C"
}}
"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                response_format={"type": "json_object"}
            )
            r = json.loads(response.choices[0].message.content)
            return r.get("decision", True), str(r.get("confianza", 70)), r.get("razon", "Setup válido"), r.get("setup_quality", "B")
        except Exception as e:
            log.warning(f"⚠️ AI generic analysis error for {symbol}: {e}. Approving by default.")
            # If AI fails, approve with B quality to not block all trades
            return True, "60", "Error IA, aprobando por defecto", "B"

    def _analyze_vwap_setup(self, symbol: str, signal: Signal) -> Tuple[bool, str, str, str]:
        meta = signal.meta or {}
        entry = signal.entry_price
        tp = signal.tp_levels[0] if signal.tp_levels else 0
        sl = signal.sl

        prompt = f"""
Actúa como un trader cuantitativo experto en scalping institucional.
Analiza este setup de {symbol} ({signal.side.value}):

Precio: {entry} | TP: {tp} | SL: {sl}
VWAP: {meta.get('vwap', 'N/A')} | Posición Z-Score: {meta.get('pos', 'N/A')}
OBI (Order Book Imbalance): {meta.get('obi', 'N/A')} (>0.65 bullish, <0.35 bearish)
Delta Normalizado: {meta.get('delta_norm', 'N/A')} (-1 a 1)
RSI: {meta.get('rsi', 'N/A')} | ADX: {meta.get('adx', 'N/A')}

Responde estrictamente en formato JSON:
{{
    "decision": true/false,
    "confianza": int,
    "razon": "frase corta",
    "setup_quality": "A", "B" o "C"
}}
"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                response_format={"type": "json_object"}
            )
            r = json.loads(response.choices[0].message.content)
            return r.get("decision", False), str(r.get("confianza", 0)), r.get("razon", "Sin razón"), r.get("setup_quality", "C")
        except Exception as e:
            log.error(f"Error AI analyzing VWAP setup: {e}")
            return True, "50", "Error en IA", "B"

    def _analyze_cascade_setup(self, symbol: str, signal: Signal) -> Tuple[bool, str, str, str]:
        meta = signal.meta or {}
        entry = signal.entry_price
        tp = signal.tp_levels[0] if signal.tp_levels else 0
        sl = signal.sl

        prompt = f"""
Actúa como un trader cuantitativo experto en 'Order Flow' y liquidaciones.
Analiza este setup de {symbol} ({signal.side.value}):

Precio: {entry} | TP: {tp} | SL: {sl}
Lógica: {meta.get('logic', 'N/A')}
Funding Rate: {meta.get('funding', 'N/A')}
OI Change: {meta.get('oi_change', 0)*100:.2f}%
L/S Ratio: {meta.get('ls_ratio', 'N/A')}
CVD Divergence: {meta.get('cvd_div', 'N/A')}
Liquidaciones confirmadas ($): {meta.get('liq_sell', 'N/A') if signal.side == OrderSide.LONG else meta.get('liq_buy', 'N/A')}

Responde estrictamente en formato JSON:
{{
    "decision": true/false,
    "confianza": int,
    "razon": "frase corta",
    "cascade_strength": "FUERTE", "MEDIA" o "DÉBIL",
    "setup_quality": "A", "B" o "C"
}}
"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=250,
                response_format={"type": "json_object"}
            )
            r = json.loads(response.choices[0].message.content)
            return (r.get("decision", False),
                    str(r.get("confianza", 0)),
                    f"{r.get('razon')} [Strength: {r.get('cascade_strength')}]",
                    r.get("setup_quality", "C"))
        except Exception as e:
            log.error(f"Error AI analyzing Cascade setup: {e}")
            return True, "50", "Error en IA", "B"

    def analyze_bulk_positions(self, positions_data: List[Dict[str, Any]]) -> Dict[str, str]:
        if not self.client or not positions_data: return {}

        positions_str = ""
        for p in positions_data:
            positions_str += f"- {p['symbol']} ({p['side']}): Entry {p['entry']}, Price {p['price']}, PNL {p['pnl']:.2f}%\n"

        prompt = f"""
Actúa como un gestor de riesgos senior para una cartera de cripto-scalping.
Analiza estas posiciones abiertas:
{positions_str}

Para cada símbolo, decide: HOLD, REDUCE_RISK o CLOSE_NOW.
Criterios: cierra si PnL < -4% o si está estancado. Mueve SL a BE si PnL > 0.5%.
Responde en JSON: {{ "SYMBOL": "ACTION" }}
"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            log.error(f"Error IA in Bulk analysis: {e}")
            return {}

    def get_market_insight(self, symbols: List[str]) -> str:
        if not self.client: return "IA Desactivada"

        now = time.time()
        if now - self._last_insight_time < 1800 and self._last_market_insight:
            return f"{self._last_market_insight} (Cached)"

        prompt = f"Analiza brevemente el sentimiento actual para {symbols}. Responde en una sola frase."
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}]
            )
            insight = response.choices[0].message.content
            self._last_market_insight = insight
            self._last_insight_time = now
            return insight
        except Exception as e:
            return f"Error en IA: {e}"

    def pick_best_symbols(self, candidates: list, n: int = 5) -> list:
        """AI picks the best N symbols from scored candidates for scalping."""
        if not self.client:
            return [c["symbol"] for c in candidates[:n]]

        # Send top 20 candidates to AI for final selection
        top20 = candidates[:20]
        rows = "\n".join(
            f"- {c['symbol']}: volume=${c['volume']/1e6:.0f}M, change={c['price_chg']:.2f}%, score={c['score']:.3f}"
            for c in top20
        )

        prompt = f"""
Actúa como un trader cuantitativo experto en scalping de criptomonedas en Binance Futures.

Elige los {n} mejores pares para scalping RIGHT NOW de la siguiente lista (datos 24h):
{rows}

Criterios de selección:
1. Alta volatilidad de precio (buenos movimientos rápidos para scalping)
2. Alto volumen (liquidez para entrar/salir sin slippage)
3. Evitar pares con correlación muy alta entre sí (diversificar)
4. Preferir pares con movimiento direccional claro (no laterales)

Responde EXCLUSIVAMENTE con un JSON array de {n} símbolos:
["SYMBOLUSDT", "SYMBOLUSDT", ...]
"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100
            )
            content = response.choices[0].message.content.strip()
            # Extract JSON array from response
            import re
            match = re.search(r'\[.*?\]', content, re.DOTALL)
            if match:
                import json
                symbols = json.loads(match.group())
                # Validate that returned symbols exist in candidates
                valid = {c["symbol"] for c in candidates}
                symbols = [s for s in symbols if s in valid]
                if symbols:
                    log.info(f"🤖 AI selected symbols: {symbols}")
                    return symbols[:n]
        except Exception as e:
            log.warning(f"⚠️ AI symbol selection failed: {e}. Using score-based fallback.")

        # Fallback: top N by score
        return [c["symbol"] for c in candidates[:n]]

    def decide_martingale(self, symbol: str, signal: Signal, quality: str, consecutive_losses: int) -> Tuple[bool, float, str]:
        if not self.client: return False, 1.0, "IA no disponible"

        prompt = f"""
Un bot ha tenido {consecutive_losses} pérdidas seguidas en {symbol}.
Nueva señal de {signal.side.value} con calidad: {quality}.

Regla: solo aprobar si calidad es A y pérdidas < 3.
Responde en JSON: {{ "aprobar_recuperacion": bool, "multiplicador": float, "razon": "string corta" }}
"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                response_format={"type": "json_object"}
            )
            r = json.loads(response.choices[0].message.content)
            return r.get("aprobar_recuperacion", False), r.get("multiplicador", 1.0), r.get("razon", "Sin razón")
        except Exception as e:
            log.error(f"Error IA in Martingale decision: {e}")
            return False, 1.0, "Error en IA"
