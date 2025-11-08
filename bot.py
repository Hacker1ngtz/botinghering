import os
import time
from binance.client import Client
import numpy as np

# =========================================
# CONFIGURACIÓN
# =========================================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "AIAUSDT")
LEVERAGE = int(os.getenv("LEVERAGE", 10))
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", 60))

client = Client(API_KEY, API_SECRET)
client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)

# =========================================
# PARÁMETROS DE OPERACIÓN
# =========================================
AMOUNT = 1                # USD por operación
STOP_LOSS_PCT = 0.35      # 35%
TAKE_PROFIT_PCT = 0.60    # 60%

print(f"🚀 Bot iniciado en {SYMBOL} con apalancamiento {LEVERAGE}x")

# =========================================
# FUNCIONES AUXILIARES
# =========================================
def get_klines(symbol, interval="1m", limit=50):
    data = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
    closes = np.array([float(k[4]) for k in data])
    return closes

def ema(values, period):
    return np.convolve(values, np.ones(period)/period, mode='valid')

def rsi(values, period=14):
    deltas = np.diff(values)
    seed = deltas[:period]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down if down != 0 else 0
    rsi_series = np.zeros_like(values)
    rsi_series[:period] = 100. - 100. / (1. + rs)

    for i in range(period, len(values)):
        delta = deltas[i - 1]
        upval = max(delta, 0)
        downval = -min(delta, 0)
        up = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        rs = up / down if down != 0 else 0
        rsi_series[i] = 100. - 100. / (1. + rs)
    return rsi_series

def get_open_position(symbol):
    positions = client.futures_position_information()
    for pos in positions:
        if pos["symbol"] == symbol and float(pos["positionAmt"]) != 0:
            return pos
    return None

# =========================================
# LOOP PRINCIPAL
# =========================================
while True:
    try:
        # Verifica si hay operación abierta
        position = get_open_position(SYMBOL)
        if position:
            print("⏸ Operación abierta, esperando que se cierre...")
            time.sleep(SLEEP_SECONDS)
            continue

        closes = get_klines(SYMBOL)
        ema_fast = ema(closes, 9)
        ema_slow = ema(closes, 21)
        rsi_values = rsi(closes)

        ema_fast_val = ema_fast[-1]
        ema_slow_val = ema_slow[-1]
        rsi_now = rsi_values[-1]

        buy_signal = ema_fast_val > ema_slow_val and rsi_now < 30
        sell_signal = ema_fast_val < ema_slow_val and rsi_now > 70

        ticker = client.futures_symbol_ticker(symbol=SYMBOL)
        price = float(ticker["price"])
        quantity = round((AMOUNT * LEVERAGE) / price, 3)

        if buy_signal:
            print(f"🟢 Señal de COMPRA detectada a {price}")
            client.futures_create_order(symbol=SYMBOL, side="BUY", type="MARKET", quantity=quantity)

            tp = round(price * (1 + TAKE_PROFIT_PCT), 4)
            sl = round(price * (1 - STOP_LOSS_PCT), 4)

            client.futures_create_order(
                symbol=SYMBOL, side="SELL", type="TAKE_PROFIT_MARKET",
                stopPrice=tp, closePosition=True
            )
            client.futures_create_order(
                symbol=SYMBOL, side="SELL", type="STOP_MARKET",
                stopPrice=sl, closePosition=True
            )
            print(f"📈 TP en {tp} | 🛑 SL en {sl}")

        elif sell_signal:
            print(f"🔴 Señal de VENTA detectada a {price}")
            client.futures_create_order(symbol=SYMBOL, side="SELL", type="MARKET", quantity=quantity)

            tp = round(price * (1 - TAKE_PROFIT_PCT), 4)
            sl = round(price * (1 + STOP_LOSS_PCT), 4)

            client.futures_create_order(
                symbol=SYMBOL, side="BUY", type="TAKE_PROFIT_MARKET",
                stopPrice=tp, closePosition=True
            )
            client.futures_create_order(
                symbol=SYMBOL, side="BUY", type="STOP_MARKET",
                stopPrice=sl, closePosition=True
            )
            print(f"📉 TP en {tp} | 🛑 SL en {sl}")

        time.sleep(SLEEP_SECONDS)

    except Exception as e:
        print(f"⚠️ Error: {e}")
        time.sleep(SLEEP_SECONDS)
