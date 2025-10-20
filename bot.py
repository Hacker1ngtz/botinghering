import os
import asyncio
from datetime import datetime, time as dtime
from binance import AsyncClient, BinanceSocketManager
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
from dotenv import load_dotenv
import numpy as np

# -------------------- Cargar configuración --------------------
load_dotenv()
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
TRADE_QUANTITY = float(os.getenv("TRADE_QUANTITY", 0.01))
TRADE_PIPS = float(os.getenv("TRADE_PIPS", 64))  # en ticks, ajustable
TRADE_SPREAD = float(os.getenv("TRADE_SPREAD", 0))
SYMBOL = "WALUSDT"
EMA_FAST_PERIOD = 25
EMA_SLOW_PERIOD = 100
PIVOT_PERIOD = 16

# Sesión de scalping
SESSION_START = dtime(8,30)
SESSION_END = dtime(9,30)

# -------------------- Funciones técnicas --------------------
def ema(prices, period):
    if len(prices) < period:
        return prices[-1]
    weights = np.exp(np.linspace(-1., 0., period))
    weights /= weights.sum()
    return np.dot(prices[-period:], weights)

def pivot_high(prices, period):
    if len(prices) < period*2+1:
        return None
    center = prices[-(period+1)]
    if center == max(prices[-(2*period+1):]):
        return center
    return None

def pivot_low(prices, period):
    if len(prices) < period*2+1:
        return None
    center = prices[-(period+1)]
    if center == min(prices[-(2*period+1):]):
        return center
    return None

def in_session():
    now = datetime.utcnow().time()
    return SESSION_START <= now <= SESSION_END

# -------------------- Órdenes --------------------
async def place_order(client, side, quantity, tp_price=None, sl_price=None):
    try:
        order = await client.create_order(
            symbol=SYMBOL,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=quantity
        )
        entry_price = float(order['fills'][0]['price'])
        print(f"{datetime.now()} - Orden ejecutada: {side} {quantity} {SYMBOL} a {entry_price}")

        # Calcular TP y SL
        if tp_price is None:
            if side == SIDE_BUY:
                tp_price = entry_price + TRADE_PIPS + TRADE_SPREAD
                sl_price = entry_price - TRADE_PIPS
            else:
                tp_price = entry_price - (TRADE_PIPS + TRADE_SPREAD)
                sl_price = entry_price + TRADE_PIPS

        return {"side": side, "entry": entry_price, "tp": tp_price, "sl": sl_price, "open": True}
    except Exception as e:
        print(f"{datetime.now()} - Error ejecutando orden: {e}")
        return None

async def check_close_order(client, order, price):
    if not order or not order['open']:
        return
    side = order['side']
    if side == SIDE_BUY:
        if price >= order['tp'] or price <= order['sl']:
            await close_order(client, SIDE_SELL, TRADE_QUANTITY, price)
            order['open'] = False
    elif side == SIDE_SELL:
        if price <= order['tp'] or price >= order['sl']:
            await close_order(client, SIDE_BUY, TRADE_QUANTITY, price)
            order['open'] = False

async def close_order(client, side, quantity, price):
    try:
        await client.create_order(
            symbol=SYMBOL,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=quantity
        )
        print(f"{datetime.now()} - Posición cerrada: {side} {quantity} {SYMBOL} a {price}")
    except Exception as e:
        print(f"{datetime.now()} - Error cerrando posición: {e}")

# -------------------- Estrategia principal --------------------
async def scalping_strategy(client):
    bm = BinanceSocketManager(client)
    async with bm.kline_socket(SYMBOL, interval='1m') as stream:
        print(f"{datetime.now()} - 🚀 Bot scalping realtime activo en {SYMBOL}")
        
        closes, highs, lows = [], [], []
        last_high_pivot, last_low_pivot = None, None
        last_order = None
        active_order = None

        async for msg in stream:
            k = msg['k']
            close = float(k['c'])
            open_p = float(k['o'])
            high = float(k['h'])
            low = float(k['l'])

            closes.append(close)
            highs.append(high)
            lows.append(low)

            ema_fast = ema(closes, EMA_FAST_PERIOD)
            ema_slow = ema(closes, EMA_SLOW_PERIOD)

            ph = pivot_high(highs, PIVOT_PERIOD)
            pl = pivot_low(lows, PIVOT_PERIOD)

            if ph is not None:
                last_high_pivot = ph
            if pl is not None:
                last_low_pivot = pl

            if not in_session():
                continue

            # Revisar cierre de posición activa
            if active_order:
                await check_close_order(client, active_order, close)
                if not active_order['open']:
                    active_order = None

            # Estrategia de compra
            if ema_fast > ema_slow and last_high_pivot is not None:
                if close > last_high_pivot and open_p < last_high_pivot and last_order != "BUY" and not active_order:
                    active_order = await place_order(client, SIDE_BUY, TRADE_QUANTITY)
                    last_order = "BUY"
                    last_high_pivot = None

            # Estrategia de venta
            if ema_fast < ema_slow and last_low_pivot is not None:
                if close < last_low_pivot and open_p > last_low_pivot and last_order != "SELL" and not active_order:
                    active_order = await place_order(client, SIDE_SELL, TRADE_QUANTITY)
                    last_order = "SELL"
                    last_low_pivot = None

# -------------------- Main --------------------
async def main():
    client = await AsyncClient.create(API_KEY, API_SECRET)
    try:
        await scalping_strategy(client)
    finally:
        await client.close_connection()

if __name__ == "__main__":
    asyncio.run(main())

