import os
import asyncio
import pandas as pd
import numpy as np
from binance import AsyncClient, BinanceSocketManager
from binance.enums import *
from dotenv import load_dotenv
from datetime import datetime

# ===== Cargar variables de entorno =====
load_dotenv()
API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT", 0))
TRADE_QUANTITY = float(os.getenv("TRADE_QUANTITY", 0))
PIVOT_PERIOD = int(os.getenv("PIVOT_PERIOD", 16))
PIPS = int(os.getenv("PIPS", 64))
SPREAD = int(os.getenv("SPREAD", 0))
SYMBOL = "WALUSDT"
TIMEFRAME = "1m"  # Velas de 1 minuto, puedes cambiar

# ===== Funciones de estrategia =====
def calculate_ema(prices, period):
    return prices.ewm(span=period, adjust=False).mean()

def pivot_high(df, period):
    return df['high'].rolling(window=period, center=True).max()

def pivot_low(df, period):
    return df['low'].rolling(window=period, center=True).min()

async def fetch_klines(client, symbol, interval, limit=500):
    klines = await client.get_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=[
        'open_time','open','high','low','close','volume','close_time','quote_av','trades','tb_base_av','tb_quote_av','ignore'
    ])
    df['open'] = df['open'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['close'] = df['close'].astype(float)
    return df

# ===== Estrategia de scalping =====
def check_signals(df):
    df['ema100'] = calculate_ema(df['close'], 100)
    df['ema25'] = calculate_ema(df['close'], 25)
    df['ph'] = pivot_high(df, PIVOT_PERIOD)
    df['pl'] = pivot_low(df, PIVOT_PERIOD)

    last_row = df.iloc[-1]
    prev_row = df.iloc[-2]

    # Señal de compra
    if last_row['ema25'] > last_row['ema100'] and prev_row['close'] < prev_row['ph'] and last_row['close'] > last_row['ph']:
        return "BUY"
    # Señal de venta
    if last_row['ema25'] < last_row['ema100'] and prev_row['close'] > prev_row['pl'] and last_row['close'] < last_row['pl']:
        return "SELL"
    return None

# ===== Función principal =====
async def main():
    client = await AsyncClient.create(API_KEY, API_SECRET)
    bsm = BinanceSocketManager(client)

    print(f"🚀 Bot scalping realtime activo en {SYMBOL}")

    while True:
        try:
            # Obtener velas recientes
            df = await fetch_klines(client, SYMBOL, TIMEFRAME, limit=500)
            signal = check_signals(df)
            price = float(df['close'].iloc[-1])
            print(f"{datetime.now()} - Precio: {price} - Señal: {signal}")

            if signal == "BUY":
                # Aquí colocas tu orden de compra
                print(f"📈 Ejecutar BUY - Cantidad: {TRADE_QUANTITY}")
                # await client.create_order(symbol=SYMBOL, side=SIDE_BUY, type=ORDER_TYPE_MARKET, quantity=TRADE_QUANTITY)

            elif signal == "SELL":
                # Aquí colocas tu orden de venta
                print(f"📉 Ejecutar SELL - Cantidad: {TRADE_QUANTITY}")
                # await client.create_order(symbol=SYMBOL, side=SIDE_SELL, type=ORDER_TYPE_MARKET, quantity=TRADE_QUANTITY)

            # Esperar 1 minuto antes de la siguiente verificación
            await asyncio.sleep(60)

        except Exception as e:
            print(f"⚠ Error: {e}, reconectando en 5 segundos...")
            await asyncio.sleep(5)

    await client.close_connection()

if __name__ == "__main__":
    asyncio.run(main())
