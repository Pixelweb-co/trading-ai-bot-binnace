"""
Email Notification Service for the Trading Bot.
Sends alerts for: bot start, trade open/close, P&L summary, and daily market analysis.
"""

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

# ── SMTP Configuration ──────────────────────────────────────────────────────
SMTP_HOST  = "mail.pulguerovirtual.com"
SMTP_PORT  = 465          # SSL
EMAIL_FROM = "ventas1@pulguerovirtual.com"
EMAIL_TO   = "egbmaster2007@gmail.com"
EMAIL_PASS = "Elian2020#"


class EmailNotifier:
    """Simple SMTP email notifier with HTML templates for trading events."""

    def __init__(self):
        self.enabled = True
        self._test_connection()

    def _ssl_context(self):
        """Returns an SSL context that accepts self-signed certificates (hosting servers)."""
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _test_connection(self):
        try:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=self._ssl_context(), timeout=10) as s:
                s.login(EMAIL_FROM, EMAIL_PASS)
            log.info("📧 Email notifier connected OK")
        except Exception as e:
            log.warning(f"📧 Email notifier unavailable (will retry per send): {e}")

    def _send(self, subject: str, html_body: str):
        """Send a single email. Fails silently so it never blocks the bot."""
        if not self.enabled:
            return
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = EMAIL_FROM
            msg["To"]      = EMAIL_TO
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=self._ssl_context(), timeout=15) as s:
                s.login(EMAIL_FROM, EMAIL_PASS)
                s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
            log.info(f"📧 Email sent: {subject}")
        except Exception as e:
            log.warning(f"📧 Email failed ({subject}): {e}")

    # ── Public Event Methods ────────────────────────────────────────────────

    def notify_bot_started(self, symbols: list):
        ts = datetime.now().strftime("%d/%m/%Y %H:%M")
        body = f"""
        <html><body style="font-family:Arial;background:#0d0d0d;color:#e0e0e0;padding:20px">
        <h2 style="color:#00e5ff">🚀 Bot de Trading Iniciado</h2>
        <table style="border-collapse:collapse;width:100%">
          <tr><td style="color:#aaa;padding:6px">Hora:</td><td style="padding:6px">{ts}</td></tr>
          <tr><td style="color:#aaa;padding:6px">Símbolos:</td><td style="padding:6px">{", ".join(symbols)}</td></tr>
          <tr><td style="color:#aaa;padding:6px">Estado:</td><td style="padding:6px;color:#00e5ff">🟢 ACTIVO</td></tr>
        </table>
        </body></html>"""
        self._send(f"🚀 Bot iniciado — {ts}", body)

    def notify_trade_opened(self, symbol: str, side: str, entry: float, qty: float,
                            sl: float, tp: float, strategy: str):
        ts = datetime.now().strftime("%d/%m/%Y %H:%M")
        color = "#00e676" if side == "LONG" else "#ff5252"
        arrow = "📈" if side == "LONG" else "📉"
        notional = entry * qty
        rr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
        body = f"""
        <html><body style="font-family:Arial;background:#0d0d0d;color:#e0e0e0;padding:20px">
        <h2 style="color:{color}">{arrow} Posición ABIERTA: {symbol}</h2>
        <table style="border-collapse:collapse;width:100%">
          <tr><td style="color:#aaa;padding:6px">Hora:</td><td>{ts}</td></tr>
          <tr><td style="color:#aaa;padding:6px">Dirección:</td><td style="color:{color};font-weight:bold">{side}</td></tr>
          <tr><td style="color:#aaa;padding:6px">Estrategia:</td><td>{strategy}</td></tr>
          <tr><td style="color:#aaa;padding:6px">Entrada:</td><td>${entry:.4f}</td></tr>
          <tr><td style="color:#aaa;padding:6px">Stop Loss:</td><td style="color:#ff5252">${sl:.4f}</td></tr>
          <tr><td style="color:#aaa;padding:6px">Take Profit:</td><td style="color:#00e676">${tp:.4f}</td></tr>
          <tr><td style="color:#aaa;padding:6px">Cantidad:</td><td>{qty}</td></tr>
          <tr><td style="color:#aaa;padding:6px">Nocional:</td><td>${notional:.2f} USDT</td></tr>
          <tr><td style="color:#aaa;padding:6px">R:R:</td><td>{rr:.2f}:1</td></tr>
        </table>
        </body></html>"""
        self._send(f"{arrow} Abierto {symbol} {side} @ ${entry:.4f}", body)

    def notify_trade_closed(self, symbol: str, side: str, entry: float,
                             close_price: float, pnl: float, reason: str):
        ts = datetime.now().strftime("%d/%m/%Y %H:%M")
        pnl_color = "#00e676" if pnl >= 0 else "#ff5252"
        pnl_icon  = "✅" if pnl >= 0 else "❌"
        pct       = ((close_price - entry) / entry * 100) * (1 if side == "LONG" else -1)
        body = f"""
        <html><body style="font-family:Arial;background:#0d0d0d;color:#e0e0e0;padding:20px">
        <h2 style="color:{pnl_color}">{pnl_icon} Posición CERRADA: {symbol}</h2>
        <table style="border-collapse:collapse;width:100%">
          <tr><td style="color:#aaa;padding:6px">Hora:</td><td>{ts}</td></tr>
          <tr><td style="color:#aaa;padding:6px">Dirección:</td><td>{side}</td></tr>
          <tr><td style="color:#aaa;padding:6px">Razón cierre:</td><td style="color:#ffab40">{reason}</td></tr>
          <tr><td style="color:#aaa;padding:6px">Entrada:</td><td>${entry:.4f}</td></tr>
          <tr><td style="color:#aaa;padding:6px">Cierre:</td><td>${close_price:.4f} ({pct:+.2f}%)</td></tr>
          <tr><td style="color:#aaa;padding:6px;font-weight:bold">PnL:</td>
              <td style="color:{pnl_color};font-size:18px;font-weight:bold">{pnl:+.2f} USDT</td></tr>
        </table>
        </body></html>"""
        self._send(f"{pnl_icon} Cerrado {symbol} | PnL: {pnl:+.2f} USDT", body)

    def notify_pnl_summary(self, wins: int, losses: int, total_pnl: float,
                            win_rate: float, best: float, worst: float,
                            active_positions: list):
        ts = datetime.now().strftime("%d/%m/%Y %H:%M")
        pnl_color = "#00e676" if total_pnl >= 0 else "#ff5252"
        positions_html = "".join(
            f"<li>{p}</li>" for p in active_positions
        ) if active_positions else "<li>Ninguna</li>"
        body = f"""
        <html><body style="font-family:Arial;background:#0d0d0d;color:#e0e0e0;padding:20px">
        <h2 style="color:#00e5ff">📊 Resumen de Ganancias y Pérdidas</h2>
        <p style="color:#aaa">{ts}</p>
        <table style="border-collapse:collapse;width:100%">
          <tr><td style="color:#aaa;padding:6px">Trades ganados:</td><td style="color:#00e676">{wins} ✅</td></tr>
          <tr><td style="color:#aaa;padding:6px">Trades perdidos:</td><td style="color:#ff5252">{losses} ❌</td></tr>
          <tr><td style="color:#aaa;padding:6px">Win Rate:</td><td>{win_rate:.1f}%</td></tr>
          <tr><td style="color:#aaa;padding:6px">Mejor trade:</td><td style="color:#00e676">+{best:.2f} USDT</td></tr>
          <tr><td style="color:#aaa;padding:6px">Peor trade:</td><td style="color:#ff5252">{worst:.2f} USDT</td></tr>
          <tr><td style="color:#aaa;padding:6px;font-size:16px;font-weight:bold">PnL Total:</td>
              <td style="color:{pnl_color};font-size:20px;font-weight:bold">{total_pnl:+.2f} USDT</td></tr>
        </table>
        <h3 style="color:#00e5ff">Posiciones activas:</h3>
        <ul>{positions_html}</ul>
        </body></html>"""
        self._send(f"📊 Resumen P&L: {total_pnl:+.2f} USDT | WR: {win_rate:.0f}%", body)

    def notify_market_analysis(self, btc_change_4h: float, avg_adx: float,
                                 funding: float, market_ok: bool, reason: str,
                                 top_symbols: list, ai_prediction: str):
        ts = datetime.now().strftime("%d/%m/%Y %H:%M")
        gate_color = "#00e676" if market_ok else "#ff5252"
        gate_text  = "✅ FAVORABLE" if market_ok else "🚫 DESFAVORABLE"
        top_html   = "".join(f"<li>{s}</li>" for s in top_symbols)
        body = f"""
        <html><body style="font-family:Arial;background:#0d0d0d;color:#e0e0e0;padding:20px">
        <h2 style="color:#ffab40">🌍 Análisis de Mercado Diario</h2>
        <p style="color:#aaa">{ts}</p>
        <table style="border-collapse:collapse;width:100%">
          <tr><td style="color:#aaa;padding:6px">BTC 4h cambio:</td><td>{btc_change_4h:+.2f}%</td></tr>
          <tr><td style="color:#aaa;padding:6px">ADX promedio:</td><td>{avg_adx:.1f}</td></tr>
          <tr><td style="color:#aaa;padding:6px">Funding BTC:</td><td>{funding:.4f}%</td></tr>
          <tr><td style="color:#aaa;padding:6px">Estado:</td>
              <td style="color:{gate_color};font-weight:bold">{gate_text}</td></tr>
          <tr><td style="color:#aaa;padding:6px">Motivo:</td><td style="color:#ffab40">{reason}</td></tr>
        </table>
        <h3 style="color:#00e5ff">Top símbolos hoy:</h3>
        <ul>{top_html}</ul>
        <h3 style="color:#00e5ff">🤖 Predicción IA:</h3>
        <p style="background:#1a1a1a;padding:12px;border-radius:6px;border-left:4px solid #00e5ff">{ai_prediction}</p>
        </body></html>"""
        self._send(f"🌍 Análisis Diario | BTC {btc_change_4h:+.2f}% | {gate_text}", body)
