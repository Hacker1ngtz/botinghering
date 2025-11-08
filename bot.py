import time
import pandas as pd
import numpy as np
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException

# =========================
# CONFIGURACIÓN
# =========================
API_KEY = "TU_API_KEY"
API_SECRET = "TU_API_SECRET"
SIMBOLO = "BTCUSDT"
INTERVALO = "1m"
CANTIDAD = 0.001
APALANCAMIENTO = 10
SLEEP_SECONDS = 60  # segundos entre análisis
client = Client(API_KEY, API_SECRET)

# =========================
# FUNCIONES TÉCNICAS
# =========================

def obtener_datos():
    """Descarga las últimas velas de Binance Futures"""
    klines = client.futures_klines(symbol=SIMBOLO, interval=INTERVALO, limit=200)
    df = pd.DataFrame(klines, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
    ])
    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    return df


def ema(data, period):
    return data.ewm(span=period, adjust=False).mean()

def sma(data, period):
    return data.rolling(period).mean()

def atr(df, length=10):
    high_low = df["high"] - df["low"]
    high_close = abs(df["high"] - df["close"].shift())
    low_close = abs(df["low"] - df["close"].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    return true_range.rolling(length).mean()

def supertrend(df, length=10, multiplier=4.0):
    atr_val = atr(df, length)
    hl2 = (df["high"] + df["low"]) / 2
    upper_band = hl2 + (multiplier * atr_val)
    lower_band = hl2 - (multiplier * atr_val)
    final_upper_band = upper_band.copy()
    final_lower_band = lower_band.copy()

    direction = [True] * len(df)
    for i in range(1, len(df)):
        if df["close"][i] > final_upper_band[i - 1]:
            direction[i] = True
        elif df["close"][i] < final_lower_band[i - 1]:
            direction[i] = False
        else:
            direction[i] = direction[i - 1]
            if direction[i] and final_lower_band[i] < final_lower_band[i - 1]:
                final_lower_band[i] = final_lower_band[i - 1]
            if not direction[i] and final_upper_band[i] > final_upper_band[i - 1]:
                final_upper_band[i] = final_upper_band[i - 1]
    supertrend = np.where(direction, final_lower_band, final_upper_band)
    return supertrend, direction


def obtener_posicion_actual():
    posiciones = client.futures_position_information(symbol=SIMBOLO)
    for p in posiciones:
        if float(p["positionAmt"]) != 0:
            return float(p["positionAmt"])
    return 0


def ejecutar_orden(direccion):
    try:
        if direccion == "BUY":
            print("🚀 Ejecutando orden de COMPRA...")
            client.futures_create_order(
                symbol=SIMBOLO,
                side=SIDE_BUY,
                type=ORDER_TYPE_MARKET,
                quantity=CANTIDAD
            )
        elif direccion == "SELL":
            print("💣 Ejecutando orden de VENTA...")
            client.futures_create_order(
                symbol=SIMBOLO,
                side=SIDE_SELL,
                type=ORDER_TYPE_MARKET,
                quantity=CANTIDAD
            )
        print("✅ Orden ejecutada correctamente.")
    except BinanceAPIException as e:
        print(f"❌ Error al ejecutar orden: {e.message}")


# =========================
# LOOP PRINCIPAL
# =========================
print("🤖 Iniciando bot EMA+SuperTrend+NovaWave...")

while True:
    try:
        df = obtener_datos()
        close = df["close"]

        # --- EMAs ---
        ema9 = ema(close, 9)
        ema21 = ema(close, 21)
        ema50 = ema(close, 50)
        ema100 = ema(close, 100)
        ema200 = ema(close, 200)

        # --- Supertrend ---
        st, direction = supertrend(df, 10, 4.0)

        # --- NovaWave ---
        nw_fast = ema(close, 9)
        nw_slow = ema(close, 21)
        nw_signal = sma(close, 10)
        nw_bull = nw_fast > nw_slow

        # --- Señales ---
        buy_signal = (nw_fast.iloc[-2] < nw_slow.iloc[-2]) and (nw_fast.iloc[-1] > nw_slow.iloc[-1])
        sell_signal = (nw_fast.iloc[-2] > nw_slow.iloc[-2]) and (nw_fast.iloc[-1] < nw_slow.iloc[-1])

        print("📊 Analizando mercado...")
        print(f"EMA9: {ema9.iloc[-1]:.2f}, EMA21: {ema21.iloc[-1]:.2f}, ST: {st[-1]:.2f}, NovaBull: {nw_bull.iloc[-1]}")
        print(f"Signal: BUY={buy_signal}, SELL={sell_signal}")

        posicion = obtener_posicion_actual()

        if posicion == 0:
            if buy_signal:
                ejecutar_orden("BUY")
            elif sell_signal:
                ejecutar_orden("SELL")
            else:
                print("⏸️ Sin señal clara.")
        else:
            print("⚙️ Ya hay una posición abierta, esperando cierre manual o TP/SL...")

        print("⏳ Esperando próximo ciclo...\n")
        time.sleep(SLEEP_SECONDS)

    except Exception as e:
        print(f"❌ Error general: {e}")
        time.sleep(SLEEP_SECONDS)

