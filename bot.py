import os
import asyncio
from binance import AsyncClient, BinanceSocketManager
import pandas as pd
import numpy as np

# =========================
# CONFIGURACIÓN
# =========================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = "WALUSDT"
TRADE_QUANTITY = float(os.getenv("TRADE_QUANTITY", "1"))  # Cantidad base
PIVOT_PERIOD = 16
EMA_FAST = 25
EMA_SLOW = 100
ATR_PERIOD = 14
ATR_MULT = 1.5

# =========================
# FUNCIONES DE INDICADORES
# =========================
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def atr(df, period):
    df['H-L'] = df['high'] - df['low']
    df['H-C'] = abs(df['high'] - df['close'].shift())
    df['L-C'] = abs(df['low'] - df['close'].shift())
    tr = df[['H-L', 'H-C', 'L-C']].max(axis=1)
    return tr.rolling(period).mean()

def pivot_high(df, period):
    return df['high'].rolling(period*2+1, center=True).max() == df['high']

def pivot_low(df, period):
    return df['low'].rolling(period*2+1, center=True).min() == df['low']

# =========================
# BOT ASÍNCRONO
# =========================
async def main():
    client = await AsyncClient.create(API_KEY, API_SECRET)
    bm = BinanceSocketManager(client)
    stream = bm.kline_socket(symbol=SYMBOL, interval='1m')  # velas de 1 min

    aux_high = None
    aux_low = None
    last_trade = None  # Evita señales repetidas

    async with stream as kline_socket:
        async for msg in kline_socket:
            k = msg['k']
            close = float(k['c'])
            high = float(k['h'])
            low = float(k['l'])
            open_price = float(k['o'])

            # Crear DataFrame temporal
            df = pd.DataFrame([{
                'open': open_price,
                'high': high,
                'low': low,
                'close': close
            }])

            # Calculamos indicadores
            df['ema_fast'] = ema(df['close'], EMA_FAST)
            df['ema_slow'] = ema(df['close'], EMA_SLOW)
            df['atr'] = atr(df, ATR_PERIOD)

            ema_fast = df['ema_fast'].iloc[-1]
            ema_slow = df['ema_slow'].iloc[-1]
            atr_value = df['atr'].iloc[-1] if not df['atr'].isna().all() else 0

            # Pivot points (simplificado)
            ph = high if pivot_high(df, PIVOT_PERIOD).iloc[-1] else None
            pl = low if pivot_low(df, PIVOT_PERIOD).iloc[-1] else None

            # =========================
            # Lógica de compras
            # =========================
            if ema_fast > ema_slow and aux_high and last_trade != 'BUY':
                if close > aux_high and open_price < aux_high:
                    tp = close + ATR_MULT * atr_value
                    sl = close - ATR_MULT * atr_value
                    print(f"[BUY] Precio: {close:.4f}, TP: {tp:.4f}, SL: {sl:.4f}")
                    # await client.futures_create_order(...)  # Para orden real
                    last_trade = 'BUY'
                    aux_high = None  # Reset pivot

            # Lógica de ventas
            if ema_fast < ema_slow and aux_low and last_trade != 'SELL':
                if close < aux_low and open_price > aux_low:
                    tp = close - ATR_MULT * atr_value
                    sl = close + ATR_MULT * atr_value
                    print(f"[SELL] Precio: {close:.4f}, TP: {tp:.4f}, SL: {sl:.4f}")
                    # await client.futures_create_order(...)  # Para orden real
                    last_trade = 'SELL'
                    aux_low = None  # Reset pivot

            # Actualizamos pivotes solo si no hay señal pendiente
            if ema_fast > ema_slow and ph:
                aux_high = ph
            if ema_fast < ema_slow and pl:
                aux_low = pl

# =========================
# EJECUTAR BOT
# =========================
if __name__ == "__main__":
    print("🚀 Bot scalping WALUSDT activo (funcionando continuo hasta detener manualmente)")
    asyncio.run(main())
