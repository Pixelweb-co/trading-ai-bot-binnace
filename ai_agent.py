import os
import logging
from openai import OpenAI

log = logging.getLogger(__name__)

class AIAgent:
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            log.warning("⚠️ OPENAI_API_KEY no encontrada en .env. IA desactivada.")
            self.client = None
        else:
            self.client = OpenAI(api_key=self.api_key)
            self.model = "gpt-4o-mini"
            self._last_market_insight = None
            self._last_insight_time = 0

    def analyze_bot_health(self, last_trades, daily_profit):
        """Analiza el rendimiento reciente y da una recomendación."""
        if not self.client: return "IA Desactivada"

        prompt = f"""
        Actúa como un experto en trading algorítmico.
        Rendimiento diario: {daily_profit} USDT
        Últimos trades: {last_trades}
        
        Analiza si hay algún patrón de error o si la estrategia está fallando debido a las condiciones del mercado.
        Responde de forma muy concisa (máximo 2 frases).
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Error en IA: {e}"

    def get_market_insight(self, symbols):
        """Obtiene sentimiento del mercado con cache de 30 minutos."""
        if not self.client: return "IA Desactivada"

        import time
        now = time.time()
        if now - self._last_insight_time < 1800 and self._last_market_insight:
            return f"{self._last_market_insight} (Cached)"

        prompt = f"""
        Analiza brevemente el sentimiento actual para {symbols}.
        ¿Hay alguna noticia macroeconómica importante hoy que afecte a estos activos?
        Responde en una sola frase.
        """
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

    def analyze_active_position(self, symbol, side, entry, price, tp, sl):
        """Analiza una posición abierta y decide si mantener, cerrar o reducir riesgo."""
        if not self.client: return "HOLD"

        p_diff = ((price - entry) / entry) * 100
        prompt = f"""
        Actúa como un gestor de riesgos senior.
        Símbolo: {symbol} | Dirección: {side}
        Entrada: {entry} | Precio Actual: {price} | PNL: {p_diff:.2f}%
        TP1: {tp} | SL: {sl}

        Basado en la acción del precio (PNL actual), responde ÚNICAMENTE con una de estas tres palabras:
        HOLD - Seguir con el trade si la estructura es sólida.
        REDUCE_RISK - Mover SL a Break-Even de INMEDIATO si el PNL es > 0.5% o si el precio está estancado.
        CLOSE_NOW - Cerrar posición si el PNL es < -4% o hay riesgo de reversión inminente.
        
        Respuesta:"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=10
            )
            advice = response.choices[0].message.content.strip().upper()
            return advice if any(k in advice for k in ["HOLD", "REDUCE_RISK", "CLOSE_NOW"]) else "HOLD"
        except Exception as e:
            log.error(f"Error IA analizando posición: {e}")
            return "HOLD"

    def analyze_bulk_positions(self, positions_data):
        """Analiza múltiples posiciones en UNA sola llamada para ahorrar tokens."""
        if not self.client or not positions_data: return {}

        positions_str = ""
        for p in positions_data:
            positions_str += f"- {p['symbol']} ({p['side']}): Entry {p['entry']}, Price {p['price']}, PNL {p['pnl']:.2f}%\n"

        prompt = f"""
        Actúa como un gestor de riesgos senior para una cartera de cripto-scalping.
        Analiza estas posiciones abiertas:
        {positions_str}

        Para cada símbolo, decide la acción: HOLD, REDUCE_RISK o CLOSE_NOW.
        Responde estrictamente en formato JSON plano:
        {{
            "SYMBOL": "ACTION",
            ...
        }}
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                response_format={ "type": "json_object" }
            )
            import json
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            log.error(f"Error IA en análisis Bulk: {e}")
            return {}

    def decide_martingale(self, symbol, signal, quality, consecutive_losses):
        """La IA decide si es seguro aplicar Martingala (doblar posición) tras una pérdida."""
        if not self.client: return False
        
        prompt = f"""
        Actúa como un gestor de capital institucional.
        Un bot de trading ha tenido {consecutive_losses} pérdidas consecutivas en {symbol}.
        Ha aparecido una nueva señal de {signal} con calidad de setup: {quality}.
        
        Reglas de Decisión:
        1. Solo permitir doblar la posición (True) si la calidad es 'A'.
        2. Si la racha de pérdidas es >= 3, ser extremadamente conservador y sugerir False a menos que sea un setup perfecto.
        3. No perseguir el mercado si la volatilidad es errática.
        
        Responde estrictamente en formato JSON:
        {{
            "aprobar_recuperacion": bool,
            "multiplicador": float,
            "razon": "string corta"
        }}
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                response_format={ "type": "json_object" }
            )
            import json
            r = json.loads(response.choices[0].message.content)
            return r.get("aprobar_recuperacion", False), r.get("multiplicador", 1.0), r.get("razon", "Sin razón")
        except Exception as e:
            log.error(f"Error IA en decisión Martingala: {e}")
            return False, 1.0, "Error en IA, modo seguro"

    def analyze_vwap_setup(self, symbol, signal, price, tp, sl, meta):
        """Veredicto final de la IA para setups de VWAP + Order Flow."""
        if not self.client: return True, 100, "IA no disponible"

        prompt = f"""
        Actúa como un trader cuantitativo experto en scalping institucional.
        Analiza este setup de {symbol} ({signal}):
        
        Precio: {price} | TP: {tp} | SL: {sl}
        VWAP: {meta['vwap']} | Posición: {meta['pos']}
        OBI (Imbalance): {meta['obi']} (0-1, >0.65 long, <0.35 short)
        Delta Normalizado: {meta['delta_norm']} (-1 a 1)
        RSI: {meta['rsi']} | ADX: {meta['adx']}
        
        Responde estrictamente en formato JSON:
        {{
            "decision": bool,
            "confianza": int,
            "razon": "string corta",
            "setup_quality": "A/B/C"
        }}
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                response_format={ "type": "json_object" }
            )
            import json
            r = json.loads(response.choices[0].message.content)
            return r.get("decision", False), r.get("confianza", 0), r.get("razon", "Sin razón"), r.get("setup_quality", "C")
        except Exception as e:
            log.error(f"Error IA analizando VWAP setup: {e}")
            return True, 50, "Error en IA, procediendo por precaución", "B"
    def analyze_cascade_setup(self, symbol, signal, price, tp, sl, meta):
        """Veredicto final de la IA para setups de Liquidation Cascade."""
        if not self.client: return True, 100, "IA no disponible"

        prompt = f"""
        Actúa como un trader cuantitativo experto en 'Order Flow' y liquidaciones.
        Analiza este setup de {symbol} ({signal}):
        
        Precio: {price} | TP: {tp} | SL: {sl}
        Lógica: {meta.get('logic', 'N/A')}
        Funding Rate: {meta['funding']} (Extreme: >0.0008 o <-0.0008)
        OI Change: {meta['oi_change']*100:.2f}% (Spike: >3%)
        L/S Ratio: {meta['ls_ratio']} (>1.4 Long biased, <0.7 Short biased)
        CVD Divergence: {meta['cvd_div']} (>0.4 Bull Trap, <-0.4 Bear Trap)
        Liq Confirmada ($): {meta['liq_sell'] if signal == 'LONG' else meta['liq_buy']}
        
        Responde estrictamente en formato JSON:
        {{
            "decision": bool,
            "confianza": int,
            "razon": "string corta",
            "cascade_strength": "FUERTE/MEDIA/DÉBIL",
            "setup_quality": "A/B/C"
        }}
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=250,
                response_format={ "type": "json_object" }
            )
            import json
            r = json.loads(response.choices[0].message.content)
            return (r.get("decision", False), 
                    r.get("confianza", 0), 
                    f"{r.get('razon')} [Strength: {r.get('cascade_strength')}]", 
                    r.get("setup_quality", "C"))
        except Exception as e:
            log.error(f"Error IA analizando Cascade setup: {e}")
            return True, 50, "Error en IA, procediendo con cautela", "B"
