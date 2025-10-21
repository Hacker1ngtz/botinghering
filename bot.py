# =======================================================
# BOT SCALPING FUTUROS BINANCE – "Pulse Reversal Pro"
# Estrategia reactiva con momentum, reversión y volumen
# =======================================================

import os
import time
import math
import pandas as pd
import numpy as np
from binance.client import Client
from binance.exceptions import BinanceAPIException

# ==============================
# CONFIGURACIÓN BASE
# ==============================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
LEVERAGE = int(os.getenv("LEVERAGE", 20))
INTERVAL = os.getenv("INTERVAL", "1m")
LIMIT = 300
SLEEP_SECONDS = 8
RISK = 0.02

client = Client(API_KEY, API_SECRET)
client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)

# ==============================
# FUNCIONES AUXILIARES
# ==============================

def get_balance_usdt():
    balance = client.futures_account_balance()
    usdt = [x for x in balance if x['asset'] == 'USDT']
    return float(usdt[0]['balance'])

def get_klines(symbol, interval, limit):
    data = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(data, columns=[
        'time','open','high','low','close','volume','close_time','qav','trades','tbbav','tbqav','ignore'])
    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['volume'] = df['volume'].astype(float)
    return df

def calc_indicators(df):
    # EMA rápida y lenta
    df['ema_fast'] = df['close'].ewm(span=8, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=21, adjust=False).mean()

    # RSI corto y largo
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    rs3 = gain.rolling(3).mean() / loss.rolling(3).mean()
    rs14 = gain.rolling(14).mean() / loss.rolling(14).mean()
    df['rsi3'] = 100 - (100 / (1 + rs3))
    df['rsi14'] = 100 - (100 / (1 + rs14))

    # ADX
    df['up_move'] = df['high'] - df['high'].shift(1)
    df['down_move'] = df['low'].shift(1) - df['low']
    df['plus_dm'] = np.where((df['up_move'] > df['down_move']) & (df['up_move'] > 0), df['up_move'], 0)
    df['minus_dm'] = np.where((df['down_move'] > df['up_move']) & (df['down_move'] > 0), df['down_move'], 0)
    tr1 = df['high'] - df['low']
    tr2 = abs(df['high'] - df['close'].shift(1))
    tr3 = abs(df['low'] - df['close'].shift(1))
    df['tr'] = np.max([tr1, tr2, tr3], axis=0)
    df['atr'] = df['tr'].rolling(14).mean()
    df['plus_di'] = 100 * (df['plus_dm'].ewm(alpha=1/14).mean() / df['atr'])
    df['minus_di'] = 100 * (df['minus_dm'].ewm(alpha=1/14).mean() / df['atr'])
    df['adx'] = abs(df['plus_di'] - df['minus_di']).ewm(alpha=1/14).mean()

    # VWAP
    df['typical'] = (df['high'] + df['low'] + df['close']) / 3
    df['vwap'] = (df['typical'] * df['volume']).cumsum() / df['volume'].cumsum()

    return df

def calculate_qty(symbol, leverage):
    balance = get_balance_usdt()
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    qty = (balance * RISK * leverage) / price
    return round(qty, 3)

# ==============================
# SEÑALES – Pulse Reversal Logic
# ==============================

def generate_signal(df):
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    signal = None
    reason = []

    # Condición de tendencia
    if latest['ema_fast'] > latest['ema_slow']:
        trend = "LONG"
    elif latest['ema_fast'] < latest['ema_slow']:
        trend = "SHORT"
    else:
        trend = None

    # Reversión RSI y fuerza ADX
    if latest['rsi3'] < 25 and latest['rsi14'] < 40 and latest['adx'] > 20:
        signal = "LONG"
        reason.append("RSI sobreventa + ADX fuerte")
    elif latest['rsi3'] > 75 and latest['rsi14'] > 60 and latest['adx'] > 20:
        signal = "SHORT"
        reason.append("RSI sobrecompra + ADX fuerte")

    # Confirmación con VWAP y tendencia
    if signal == "LONG" and latest['close'] > latest['vwap'] and trend == "LONG":
        reason.append("Confirmación VWAP y EMA")
    elif signal == "SHORT" and latest['close'] < latest['vwap'] and trend == "SHORT":
        reason.append("Confirmación VWAP y EMA")
    else:
        signal = None
        reason.append("Sin confirmación VWAP/tendencia")

    return signal, reason

# ==============================
# GESTIÓN DE OPERACIONES
# ==============================

def close_all_orders(symbol):
    try:
        client.futures_cancel_all_open_orders(symbol=symbol)
    except Exception as e:
        print(f"⚠️ Error cancelando órdenes: {e}")

def open_position(symbol, side, qty, atr):
    close_all_orders(symbol)
    mark_price = float(client.futures_mark_price(symbol=symbol)['markPrice'])

    if side == "LONG":
        stop_loss = mark_price - (atr * 1.2)
        take_profit = mark_price + (atr * 2.5)
        position_side = "BUY"
    else:
        stop_loss = mark_price + (atr * 1.2)
        take_profit = mark_price - (atr * 2.5)
        position_side = "SELL"

    print(f"🚀 {side} | Qty={qty} | Entrada={mark_price}")
    print(f"🛑 SL={stop_loss} | 🎯 TP={take_profit}")

    try:
        client.futures_create_order(
            symbol=symbol,
            side=position_side,
            type="MARKET",
            quantity=qty
        )

        client.futures_create_order(
            symbol=symbol,
            side="SELL" if side == "LONG" else "BUY",
            type="STOP_MARKET",
            stopPrice=round(stop_loss, 2),
            closePosition=True
        )

        client.futures_create_order(
            symbol=symbol,
            side="SELL" if side == "LONG" else "BUY",
            type="TAKE_PROFIT_MARKET",
            stopPrice=round(take_profit, 2),
            closePosition=True
        )

    except BinanceAPIException as e:
        print(f"⚠️ Error abriendo posición: {e.message}")

# ==============================
# LOOP PRINCIPAL
# ==============================

def main():
    print(f"💹 Bot iniciado en {SYMBOL} [{INTERVAL}] con apalancamiento {LEVERAGE}x")
    while True:
        try:
            df = get_klines(SYMBOL, INTERVAL, LIMIT)
            df = calc_indicators(df)
            signal, reason = generate_signal(df)
            atr = df['atr'].iloc[-1]

            print(f"\n🕐 {pd.Timestamp.now()} | Señal: {signal} | {' | '.join(reason)}")

            positions = client.futures_position_information(symbol=SYMBOL)
            pos = [p for p in positions if float(p['positionAmt']) != 0]

            if len(pos) > 0:
                print("📊 Posición activa, esperando cierre...")
            elif signal:
                qty = calculate_qty(SYMBOL, LEVERAGE)
                open_position(SYMBOL, signal, qty, atr)
            else:
                print("⏸ Sin confirmación. Esperando...")

        except Exception as e:
            print(f"⚠️ Error en loop principal: {e}")

        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
