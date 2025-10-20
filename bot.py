import os, asyncio, pandas as pd, numpy as np
from binance import AsyncClient, BinanceSocketManager
from binance.enums import *
from dotenv import load_dotenv

load_dotenv()

# ================= CONFIG =================
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

# ================= FUNCIONES =================

async def send_order(client, side, qty, tp, sl):
    """
    Crea orden de mercado y TP/SL OCO.
    """
    try:
        print(f"🟢 {side} | TP {tp:.4f} | SL {sl:.4f}")
        # Crear orden de mercado
        order = await client.create_order(
            symbol=SYMBOL,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=qty
        )
        # Crear orden OCO TP/SL
        if side == SIDE_BUY:
            await client.create_oco_order(import os, asyncio, pandas as pd, numpy as np
from binance import AsyncClient, BinanceSocketManager
from binance.enums import *
from dotenv import load_dotenv

load_dotenv()

# ================= CONFIG =================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

SYMBOL = "WALUSDT"
INTERVAL = "1m"
EMA_FAST = 25
EMA_SLOW = 100
PIPS = 64
QTY = 10

# Tiempo para pivot lookback
PIVOT_LEFT = 3
PIVOT_RIGHT = 3

# ================= FUNCIONES =================

async def send_order(client, side, qty, tp, sl):
    """
    Crea orden de mercado y TP/SL OCO.
    """
    try:
        print(f"🟢 {side} | TP {tp:.4f} | SL {sl:.4f}")
        # Crear orden de mercado
        order = await client.create_order(
            symbol=SYMBOL,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=qty
        )
        # Crear orden OCO TP/SL
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
        print(f"⚠️ Error al enviar orden: {e}")

def calculate_indicators(df):
    df['ema_fast'] = df['close'].ewm(span=EMA_FAST).mean()
    df['ema_slow'] = df['close'].ewm(span=EMA_SLOW).mean()
    df['trend'] = np.where(df['ema_fast'] > df['ema_slow'], 1, -1)
    return df

def get_last_pivots(df):
    """Devuelve último pivot alto y bajo simples."""
    last_high = df["high"].iloc[-PIVOT_LEFT-1:-1].max() if len(df) > PIVOT_LEFT+1 else None
    last_low = df["low"].iloc[-PIVOT_LEFT-1:-1].min() if len(df) > PIVOT_LEFT+1 else None
    return last_high, last_low

# ================= STRATEGY REALTIME =================
async def main():
    client = await AsyncClient.create(API_KEY, API_SECRET)
    bm = BinanceSocketManager(client)
    socket = bm.kline_socket(symbol=SYMBOL, interval=INTERVAL)
    
    df = pd.DataFrame(columns=["open","high","low","close"])
    position_open = None  # trackea posición abierta

    try:
        print(f"🚀 Bot scalping realtime activo en {SYMBOL}")
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
                        trend = df['trend'].iloc[-1]
                        last_high, last_low = get_last_pivots(df)

                        # --- Lógica de entradas ---
                        if trend == 1 and close > last_high and position_open != SIDE_BUY:
                            tp = close + (PIPS * 0.0001)
                            sl = close - (PIPS * 0.0001)
                            await send_order(client, SIDE_BUY, QTY, tp, sl)
                            position_open = SIDE_BUY

                        elif trend == -1 and close < last_low and position_open != SIDE_SELL:
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
        print("🛑 Conexión Binance cerrada.")

# ================= RUN BOT =================
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot detenido manualmente.")

# ================= RUN BOT =================
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot detenido manualmente.")
