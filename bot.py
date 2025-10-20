import os
import asyncio
import pandas as pd
import numpy as np
from binance import AsyncClient, BinanceSocketManager
from binance.enums import *
from dotenv import load_dotenv
import sys
from colorama import init, Fore, Style
from datetime import datetime

# ---------- INICIALIZACIÓN ----------
load_dotenv()
init(autoreset=True)  # Colorama para colores en consola

# ---------- CONFIG ----------
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

SYMBOL = "WALUSDT"
INTERVAL = "1m"
EMA_FAST = 25
EMA_SLOW = 100
PIPS = 64
QTY = 10

# Parámetros pivotes
PIVOT_LEFT = 5
PIVOT_RIGHT = 5

# ---------- FUNCIONES DE LOG ----------
def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log_info(msg):
    print(Fore.CYAN + f"{timestamp()} ℹ️ {msg}")

def log_success(msg):
    print(Fore.GREEN + f"{timestamp()} ✅ {msg}")

def log_warning(msg):
    print(Fore.YELLOW + f"{timestamp()} ⚠️ {msg}")

def log_error(msg):
    print(Fore.RED + f"{timestamp()} ❌ {msg}", file=sys.stderr)

# ---------- FUNCIONES DEL BOT ----------
async def send_order(client, side, qty, tp, sl):
    """Crea orden de mercado y TP/SL OCO"""
    try:
        log_success(f"🟢 {side} | TP {tp:.4f} | SL {sl:.4f}")
        order = await client.create_order(
            symbol=SYMBOL,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=qty
        )
        if side == SIDE_BUY:
            await client.create_oco_order(
                symbol=SYMBOL,
                side=SIDE_SELL,
                quantity=qty,
                price=str(round(tp, 4)),
                stopPrice=str(round(sl, 4)),
                stopLimitPrice=str(round(sl, 4)),
                stopLimitTimeInForce=TIME_IN_FORCE_GTC
            )
        elif side == SIDE_SELL:
            await client.create_oco_order(
                symbol=SYMBOL,
                side=SIDE_BUY,
                quantity=qty,
                price=str(round(tp, 4)),
                stopPrice=str(round(sl, 4)),
                stopLimitPrice=str(round(sl, 4)),
                stopLimitTimeInForce=TIME_IN_FORCE_GTC
            )
    except Exception as e:
        log_error(f"⚠️ Error al enviar orden: {e}")

def calculate_indicators(df):
    df['ema_fast'] = df['close'].ewm(span=EMA_FAST).mean()
    df['ema_slow'] = df['close'].ewm(span=EMA_SLOW).mean()
    df['trend'] = np.where(df['ema_fast'] > df['ema_slow'], 1, -1)
    return df

def detect_pivots(df, left=PIVOT_LEFT, right=PIVOT_RIGHT):
    pivots_high = [np.nan]*len(df)
    pivots_low  = [np.nan]*len(df)
    for i in range(left, len(df)-right):
        window_high = df['high'].iloc[i-left:i+right+1]
        if df['high'].iloc[i] == window_high.max():
            pivots_high[i] = df['high'].iloc[i]
        window_low = df['low'].iloc[i-left:i+right+1]
        if df['low'].iloc[i] == window_low.min():
            pivots_low[i] = df['low'].iloc[i]
    df['pivot_high'] = pivots_high
    df['pivot_low'] = pivots_low
    return df

# ---------- STRATEGY REALTIME ----------
async def main():
    client = await AsyncClient.create(API_KEY, API_SECRET)
    bm = BinanceSocketManager(client)
    socket = bm.kline_socket(symbol=SYMBOL, interval=INTERVAL)
    
    df = pd.DataFrame(columns=["open","high","low","close"])
    position_open = None  # Trackea posición abierta

    try:
        log_success(f"🚀 Bot scalping realtime activo en {SYMBOL}")
        async with socket as s:
            while True:
                msg = await s.recv()
                k = msg['k']
                if k['x']:  # vela cerrada
                    close = float(k['c'])
                    high = float(k['h'])
                    low = float(k['l'])
                    openp = float(k['o'])

                    df.loc[len(df)] = [openp, high, low, close]
                    df = df.tail(500)

                    if len(df) > EMA_SLOW:
                        df = calculate_indicators(df)
                        df = detect_pivots(df)
                        trend = df['trend'].iloc[-1]

                        # Últimos pivotes válidos
                        last_high = df['pivot_high'].dropna().iloc[-1] if not df['pivot_high'].dropna().empty else None
                        last_low  = df['pivot_low'].dropna().iloc[-1]  if not df['pivot_low'].dropna().empty else None

                        # --- Entradas ---
                        if trend == 1 and last_high and close > last_high and position_open != SIDE_BUY:
                            tp = close + (PIPS * 0.0001)
                            sl = close - (PIPS * 0.0001)
                            await send_order(client, SIDE_BUY, QTY, tp, sl)
                            position_open = SIDE_BUY

                        elif trend == -1 and last_low and close < last_low and position_open != SIDE_SELL:
                            tp = close - (PIPS * 0.0001)
                            sl = close + (PIPS * 0.0001)
                            await send_order(client, SIDE_SELL, QTY, tp, sl)
                            position_open = SIDE_SELL

                        # --- Cierra la posición si cambia tendencia ---
                        if position_open == SIDE_BUY and trend == -1:
                            position_open = None
                        elif position_open == SIDE_SELL and trend == 1:
                            position_open = None

    finally:
        await client.close_connection()
        log_warning("🛑 Conexión Binance cerrada.")

# ---------- RUN BOT ----------
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log_warning("🛑 Bot detenido manualmente.")
