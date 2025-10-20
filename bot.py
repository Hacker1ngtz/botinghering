import os
import time
import math
import pandas as pd
from binance.client import Client
from binance.enums import *

# ==============================
# CONFIGURACIÓN DESDE VARIABLES DE ENTORNO
# ==============================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "BNBUSDT").upper()  # moneda por defecto
LEVERAGE = int(os.getenv("LEVERAGE", 10))
SLEEP_SECONDS = int(os.getenv("SLEEP_SECONDS", 5))  # loop más rápido

# Indicadores
ATR_LEN = int(os.getenv("ATR_LEN", 14))
ATR_MULT = float(os.getenv("ATR_MULT", 1.0))
SHORT_EMA = int(os.getenv("SHORT_EMA", 21))
LONG_EMA = int(os.getenv("LONG_EMA", 65))
RSI_FAST = int(os.getenv("RSI_FAST", 25))
RSI_SLOW = int(os.getenv("RSI_SLOW", 100))

# ==============================
# CLIENTE BINANCE FUTURES
# ==============================
client = Client(API_KEY, API_SECRET)

# ==============================
# FUNCIONES AUXILIARES# ================================================
# BOT DE TRADING FUTUROS BINANCE - OPTIMIZADO
# ================================================
import os
import time
import math
import pandas as pd
from binance.client import Client
from binance.enums import *
from datetime import datetime

# ================================================
# CONFIGURACIÓN GENERAL
# ================================================
API_KEY = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")
SYMBOL = os.getenv("SYMBOL", "BNBUSDT").upper()
LEVERAGE = int(os.getenv("LEVERAGE", 10))
SLEEP_SECONDS = 5  # Intervalo de verificación

client = Client(API_KEY, API_SECRET)

# ================================================
# FUNCIONES AUXILIARES
# ================================================
def round_step(value, step):
    precision = int(round(-math.log(step, 10), 0))
    return round(math.floor(value / step) * step, precision)

def get_symbol_rules(symbol):
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            step_size = float(s["filters"][1]["stepSize"])
            tick_size = float(s["filters"][0]["tickSize"])
            min_qty = float(s["filters"][1]["minQty"])
            return step_size, tick_size, s["filters"], min_qty
    raise ValueError(f"No se encontraron reglas para {symbol}")

def get_futures_klines(symbol, interval="1m", limit=200):
    klines = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    df["close"] = df["close"].astype(float)
    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df

# ================================================
# CÁLCULO DE INDICADORES (SIMPLE EJEMPLO)
# ================================================
def calculate_indicators(df):
    df["ema_short"] = df["close"].ewm(span=9).mean()
    df["ema_long"] = df["close"].ewm(span=21).mean()
    return df

def check_signals(df):
    if df["ema_short"].iloc[-2] < df["ema_long"].iloc[-2] and df["ema_short"].iloc[-1] > df["ema_long"].iloc[-1]:
        return "BUY", df["low"].iloc[-2], df["high"].iloc[-2]
    elif df["ema_short"].iloc[-2] > df["ema_long"].iloc[-2] and df["ema_short"].iloc[-1] < df["ema_long"].iloc[-1]:
        return "SELL", df["high"].iloc[-2], df["low"].iloc[-2]
    else:
        return None, None, None

# ================================================
# FUNCIÓN DE CANTIDAD SEGURA
# ================================================
def calculate_qty(symbol, leverage):
    balances = client.futures_account_balance()
    usdt_balance = next((float(b["balance"]) for b in balances if b["asset"] == "USDT"), 0.0)

    if usdt_balance <= 0:
        return 0.0

    price = float(client.futures_symbol_ticker(symbol=symbol)["price"])

    # Usar 90% del saldo con margen de seguridad
    usable_balance = usdt_balance * 0.9
    raw_qty = (usable_balance * leverage) / price

    qty = max(round_step(raw_qty, step_size), min_qty)
    return qty

# ================================================
# POSICIÓN Y ÓRDENES
# ================================================
def close_open_positions(symbol):
    positions = client.futures_position_information(symbol=symbol)
    for pos in positions:
        if float(pos["positionAmt"]) != 0:
            side = SIDE_SELL if float(pos["positionAmt"]) > 0 else SIDE_BUY
            client.futures_create_order(
                symbol=symbol,
                side=side,
                type=ORDER_TYPE_MARKET,
                quantity=abs(float(pos["positionAmt"]))
            )
            print(f"🔻 Posición {side} cerrada.")

def open_position_with_tp_sl(symbol, signal, stop_loss, take_profit):
    close_open_positions(symbol)

    qty = calculate_qty(symbol, LEVERAGE)
    if qty <= 0:
        print("⚠️ No hay suficiente margen para abrir posición.")
        return

    side = SIDE_BUY if signal == "BUY" else SIDE_SELL
    opposite_side = SIDE_SELL if signal == "BUY" else SIDE_BUY
    order = client.futures_create_order(
        symbol=symbol,
        side=side,
        type=ORDER_TYPE_MARKET,
        quantity=qty
    )
    print(f"✅ {signal} ejecutado: Cantidad={qty}")

    # Calcular TP y SL
    entry_price = float(client.futures_position_information(symbol=symbol)[0]["entryPrice"])
    tp_price = round_step(entry_price * (1.01 if signal == "BUY" else 0.99), tick_size)
    sl_price = round_step(entry_price * (0.99 if signal == "BUY" else 1.01), tick_size)

    client.futures_create_order(
        symbol=symbol,
        side=opposite_side,
        type=ORDER_TYPE_TAKE_PROFIT_MARKET,
        stopPrice=tp_price,
        closePosition=True
    )
    client.futures_create_order(
        symbol=symbol,
        side=opposite_side,
        type=ORDER_TYPE_STOP_MARKET,
        stopPrice=sl_price,
        closePosition=True
    )
    print(f"🎯 TP: {tp_price} | 🛑 SL: {sl_price}")

# ================================================
# LOOP PRINCIPAL
# ================================================
if __name__ == "__main__":
    step_size, tick_size, _, min_qty = get_symbol_rules(SYMBOL)
    client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)

    print(f"🚀 Bot iniciado para {SYMBOL} con apalancamiento x{LEVERAGE}")

    while True:
        try:
            df = get_futures_klines(SYMBOL)
            df = calculate_indicators(df)
            signal, sl, tp = check_signals(df)

            if signal:
                print(f"📈 Señal detectada: {signal}")
                open_position_with_tp_sl(SYMBOL, signal, sl, tp)
            else:
                print("⏳ Esperando señal...")

            time.sleep(SLEEP_SECONDS)
        except Exception as e:
            print(f"⚠️ Error en el loop principal: {e}")
            time.sleep(10)
# ================================================
# BOT DE TRADING FUTUROS BINANCE - OPTIMIZADO
# ================================================
import os
import time
import math
import pandas as pd
from binance.client import Client
from binance.enums import *
from datetime import datetime

# ================================================
# CONFIGURACIÓN GENERAL
# ================================================
API_KEY = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")
SYMBOL = os.getenv("SYMBOL", "BNBUSDT").upper()
LEVERAGE = int(os.getenv("LEVERAGE", 10))
SLEEP_SECONDS = 5  # Intervalo de verificación

client = Client(API_KEY, API_SECRET)

# ================================================
# FUNCIONES AUXILIARES
# ================================================
def round_step(value, step):
    precision = int(round(-math.log(step, 10), 0))
    return round(math.floor(value / step) * step, precision)

def get_symbol_rules(symbol):
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            step_size = float(s["filters"][1]["stepSize"])
            tick_size = float(s["filters"][0]["tickSize"])
            min_qty = float(s["filters"][1]["minQty"])
            return step_size, tick_size, s["filters"], min_qty
    raise ValueError(f"No se encontraron reglas para {symbol}")

def get_futures_klines(symbol, interval="1m", limit=200):
    klines = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    df["close"] = df["close"].astype(float)
    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df

# ================================================
# CÁLCULO DE INDICADORES (SIMPLE EJEMPLO)
# ================================================
def calculate_indicators(df):
    df["ema_short"] = df["close"].ewm(span=9).mean()
    df["ema_long"] = df["close"].ewm(span=21).mean()
    return df

def check_signals(df):
    if df["ema_short"].iloc[-2] < df["ema_long"].iloc[-2] and df["ema_short"].iloc[-1] > df["ema_long"].iloc[-1]:
        return "BUY", df["low"].iloc[-2], df["high"].iloc[-2]
    elif df["ema_short"].iloc[-2] > df["ema_long"].iloc[-2] and df["ema_short"].iloc[-1] < df["ema_long"].iloc[-1]:
        return "SELL", df["high"].iloc[-2], df["low"].iloc[-2]
    else:
        return None, None, None

# ================================================
# FUNCIÓN DE CANTIDAD SEGURA
# ================================================
def calculate_qty(symbol, leverage):
    balances = client.futures_account_balance()
    usdt_balance = next((float(b["balance"]) for b in balances if b["asset"] == "USDT"), 0.0)

    if usdt_balance <= 0:
        return 0.0

    price = float(client.futures_symbol_ticker(symbol=symbol)["price"])

    # Usar 90% del saldo con margen de seguridad
    usable_balance = usdt_balance * 0.9
    raw_qty = (usable_balance * leverage) / price

    qty = max(round_step(raw_qty, step_size), min_qty)
    return qty

# ================================================
# POSICIÓN Y ÓRDENES
# ================================================
def close_open_positions(symbol):
    positions = client.futures_position_information(symbol=symbol)
    for pos in positions:
        if float(pos["positionAmt"]) != 0:
            side = SIDE_SELL if float(pos["positionAmt"]) > 0 else SIDE_BUY
            client.futures_create_order(
                symbol=symbol,
                side=side,
                type=ORDER_TYPE_MARKET,
                quantity=abs(float(pos["positionAmt"]))
            )
            print(f"🔻 Posición {side} cerrada.")

def open_position_with_tp_sl(symbol, signal, stop_loss, take_profit):
    close_open_positions(symbol)

    qty = calculate_qty(symbol, LEVERAGE)
    if qty <= 0:
        print("⚠️ No hay suficiente margen para abrir posición.")
        return

    side = SIDE_BUY if signal == "BUY" else SIDE_SELL
    opposite_side = SIDE_SELL if signal == "BUY" else SIDE_BUY
    order = client.futures_create_order(
        symbol=symbol,
        side=side,
        type=ORDER_TYPE_MARKET,
        quantity=qty
    )
    print(f"✅ {signal} ejecutado: Cantidad={qty}")

    # Calcular TP y SL
    entry_price = float(client.futures_position_information(symbol=symbol)[0]["entryPrice"])
    tp_price = round_step(entry_price * (1.01 if signal == "BUY" else 0.99), tick_size)
    sl_price = round_step(entry_price * (0.99 if signal == "BUY" else 1.01), tick_size)

    client.futures_create_order(
        symbol=symbol,
        side=opposite_side,
        type=ORDER_TYPE_TAKE_PROFIT_MARKET,
        stopPrice=tp_price,
        closePosition=True
    )
    client.futures_create_order(
        symbol=symbol,
        side=opposite_side,
        type=ORDER_TYPE_STOP_MARKET,
        stopPrice=sl_price,
        closePosition=True
    )
    print(f"🎯 TP: {tp_price} | 🛑 SL: {sl_price}")

# ================================================
# LOOP PRINCIPAL
# ================================================
if __name__ == "__main__":
    step_size, tick_size, _, min_qty = get_symbol_rules(SYMBOL)
    client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)

    print(f"🚀 Bot iniciado para {SYMBOL} con apalancamiento x{LEVERAGE}")

    while True:
        try:
            df = get_futures_klines(SYMBOL)
            df = calculate_indicators(df)
            signal, sl, tp = check_signals(df)

            if signal:
                print(f"📈 Señal detectada: {signal}")
                open_position_with_tp_sl(SYMBOL, signal, sl, tp)
            else:
                print("⏳ Esperando señal...")

            time.sleep(SLEEP_SECONDS)
        except Exception as e:
            print(f"⚠️ Error en el loop principal: {e}")
            time.sleep(10)
