import os, asyncio, json, pandas as pd, numpy as np
from binance import AsyncClient, BinanceSocketManager
from binance.enums import *
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

SYMBOL = "WALUSDT"
INTERVAL = "1m"
EMA_FAST = 25
EMA_SLOW = 100
PIPS = 64
QTY = 10

# ----------------------------- #
async def main():
    client = await AsyncClient.create(API_KEY, API_SECRET)
    bm = BinanceSocketManager(client)
    socket = bm.kline_socket(symbol=SYMBOL, interval=INTERVAL)

    df = pd.DataFrame(columns=["open","high","low","close"])

    async with socket as stream:
        print(f"🚀 Bot scalping realtime activo en {SYMBOL}")
        async for msg in stream:
            data = msg["k"]
            closed = data["x"]             # vela cerrada?
            close = float(data["c"])
            high = float(data["h"])
            low  = float(data["l"])
            openp = float(data["o"])

            if closed:
                df.loc[len(df)] = [openp, high, low, close]

                # mantener sólo las últimas 500 velas
                df = df.tail(500)

                if len(df) > EMA_SLOW:
                    df["ema_fast"] = df["close"].ewm(span=EMA_FAST).mean()
                    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW).mean()
                    trend = 1 if df["ema_fast"].iloc[-1] > df["ema_slow"].iloc[-1] else -1

                    # pivotes simples
                    last_high = df["high"].iloc[-5:-1].max()
                    last_low  = df["low"].iloc[-5:-1].min()

                    # señales
                    if trend == 1 and close > last_high:
                        tp = close + (PIPS * 0.0001)
                        sl = close - (PIPS * 0.0001)
                        await send_order(client, SIDE_BUY, QTY, tp, sl)
                    elif trend == -1 and close < last_low:
                        tp = close - (PIPS * 0.0001)
                        sl = close + (PIPS * 0.0001)
                        await send_order(client, SIDE_SELL, QTY, tp, sl)

    await client.close_connection()

# ----------------------------- #
async def send_order(client, side, qty, tp, sl):
    try:
        print(f"🟢 {side} | TP {tp:.4f} | SL {sl:.4f}")
        # Descomenta para órdenes reales:
        # await client.create_order(
        #     symbol=SYMBOL,
        #     side=side,
        #     type=ORDER_TYPE_MARKET,
        #     quantity=qty
        # )
    except Exception as e:
        print(f"⚠️ Error al enviar orden: {e}")

# ----------------------------- #
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Detenido manualmente.")
import os, asyncio, json, pandas as pd, numpy as np
from binance import AsyncClient, BinanceSocketManager
from binance.enums import *
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

SYMBOL = "WALUSDT"
INTERVAL = "1m"
EMA_FAST = 25
EMA_SLOW = 100
PIPS = 64
QTY = 10

# ----------------------------- #
async def main():
    client = await AsyncClient.create(API_KEY, API_SECRET)
    bm = BinanceSocketManager(client)
    socket = bm.kline_socket(symbol=SYMBOL, interval=INTERVAL)

    df = pd.DataFrame(columns=["open","high","low","close"])

    async with socket as stream:
        print(f"🚀 Bot scalping realtime activo en {SYMBOL}")
        async for msg in stream:
            data = msg["k"]
            closed = data["x"]             # vela cerrada?
            close = float(data["c"])
            high = float(data["h"])
            low  = float(data["l"])
            openp = float(data["o"])

            if closed:
                df.loc[len(df)] = [openp, high, low, close]

                # mantener sólo las últimas 500 velas
                df = df.tail(500)

                if len(df) > EMA_SLOW:
                    df["ema_fast"] = df["close"].ewm(span=EMA_FAST).mean()
                    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW).mean()
                    trend = 1 if df["ema_fast"].iloc[-1] > df["ema_slow"].iloc[-1] else -1

                    # pivotes simples
                    last_high = df["high"].iloc[-5:-1].max()
                    last_low  = df["low"].iloc[-5:-1].min()

                    # señales
                    if trend == 1 and close > last_high:
                        tp = close + (PIPS * 0.0001)
                        sl = close - (PIPS * 0.0001)
                        await send_order(client, SIDE_BUY, QTY, tp, sl)
                    elif trend == -1 and close < last_low:
                        tp = close - (PIPS * 0.0001)
                        sl = close + (PIPS * 0.0001)
                        await send_order(client, SIDE_SELL, QTY, tp, sl)

    await client.close_connection()

# ----------------------------- #
async def send_order(client, side, qty, tp, sl):
    try:
        print(f"🟢 {side} | TP {tp:.4f} | SL {sl:.4f}")
        # Descomenta para órdenes reales:
        # await client.create_order(
        #     symbol=SYMBOL,
        #     side=side,
        #     type=ORDER_TYPE_MARKET,
        #     quantity=qty
        # )
    except Exception as e:
        print(f"⚠️ Error al enviar orden: {e}")

# ----------------------------- #
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Detenido manualmente.")


