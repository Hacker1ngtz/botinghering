# bot_scalper.py
import os
import time
import math
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException

# ==============================
# CONFIGURACIÓN
# ==============================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

SYMBOL = "BTCUSDT"       # Cambia por el par que operas
LEVERAGE = 50            # Apalancamiento
INTERVAL = "1m"          # Intervalo (scalping: 15s, 30s, 1m, 3m)
TAKE_PROFIT_PCT = 0.2    # 0.2% de ganancia
STOP_LOSS_PCT = 0.15     # 0.15% de pérdida
COOLDOWN = 10            # segundos entre ciclos
QUANTITY_PERCENT = 100   # usar 100% del saldo disponible

# ==============================
# INICIALIZAR CLIENTE
# ==============================
client = Client(API_KEY, API_SECRET)

def get_balance(symbol="USDT"):
    balance = client.futures_account_balance()
    for b in balance:
        if b["asset"] == symbol:
            return float(b["balance"])
    return 0.0

def get_price(symbol):
    return float(client.futures_symbol_ticker(symbol=symbol)["price"])

def get_quantity(symbol, balance, price):
    # Convierte el balance a cantidad de contrato
    usdt_to_use = balance * (QUANTITY_PERCENT / 100)
    qty = (usdt_to_use * LEVERAGE) / price
    return math.floor(qty * 1000) / 1000  # redondear a 3 decimales

def position_open(symbol):
    positions = client.futures_position_information(symbol=symbol)
    pos_amt = float(positions[0]["positionAmt"])
    return pos_amt != 0.0

def close_position(symbol):
    try:
        pos_info = client.futures_position_information(symbol=symbol)
        amt = float(pos_info[0]["positionAmt"])
        if amt > 0:
            client.futures_create_order(symbol=symbol, side="SELL", type="MARKET", quantity=abs(amt))
        elif amt < 0:
            client.futures_create_order(symbol=symbol, side="BUY", type="MARKET", quantity=abs(amt))
        print("✅ Posición cerrada")
    except BinanceAPIException as e:
        print(f"⚠️ Error al cerrar posición: {e}")

def set_leverage(symbol, leverage):
    try:
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
    except BinanceAPIException:
        pass

# ==============================
# ESTRATEGIA SIMPLE SCALPING
# ==============================
def estrategia_scalping(symbol):
    klines = client.futures_klines(symbol=symbol, interval=INTERVAL, limit=5)
    closes = [float(k[4]) for k in klines]

    if len(closes) < 3:
        return "NO_SIGNAL"

    # Señales básicas: momentum simple
    if closes[-1] > closes[-2] > closes[-3]:
        return "LONG"
    elif closes[-1] < closes[-2] < closes[-3]:
        return "SHORT"
    else:
        return "NO_SIGNAL"

# ==============================
# MAIN LOOP
# ==============================
def run_bot():
    print("🚀 Iniciando bot scalper en Binance Futures...")
    set_leverage(SYMBOL, LEVERAGE)

    while True:
        try:
            if position_open(SYMBOL):
                print("⏸ Ya hay una posición abierta. Esperando TP o SL...")
                time.sleep(COOLDOWN)
                continue

            signal = estrategia_scalping(SYMBOL)
            price = get_price(SYMBOL)
            balance = get_balance()
            qty = get_quantity(SYMBOL, balance, price)

            if qty * price < 5:
                print("⚠️ Cantidad demasiado baja, ajustando...")
                continue

            if signal == "LONG":
                print(f"📈 Señal LONG detectada — qty={qty}")
                order = client.futures_create_order(
                    symbol=SYMBOL,
                    side="BUY",
                    type="MARKET",
                    quantity=qty
                )
                entry_price = float(order["fills"][0]["price"])
                tp = round(entry_price * (1 + TAKE_PROFIT_PCT / 100), 2)
                sl = round(entry_price * (1 - STOP_LOSS_PCT / 100), 2)
                client.futures_create_order(symbol=SYMBOL, side="SELL", type="TAKE_PROFIT_MARKET", stopPrice=tp, closePosition=True)
                client.futures_create_order(symbol=SYMBOL, side="SELL", type="STOP_MARKET", stopPrice=sl, closePosition=True)
                print(f"🎯 TP={tp} | 🛑 SL={sl}")

            elif signal == "SHORT":
                print(f"📉 Señal SHORT detectada — qty={qty}")
                order = client.futures_create_order(
                    symbol=SYMBOL,
                    side="SELL",
                    type="MARKET",
                    quantity=qty
                )
                entry_price = float(order["fills"][0]["price"])
                tp = round(entry_price * (1 - TAKE_PROFIT_PCT / 100), 2)
                sl = round(entry_price * (1 + STOP_LOSS_PCT / 100), 2)
                client.futures_create_order(symbol=SYMBOL, side="BUY", type="TAKE_PROFIT_MARKET", stopPrice=tp, closePosition=True)
                client.futures_create_order(symbol=SYMBOL, side="BUY", type="STOP_MARKET", stopPrice=sl, closePosition=True)
                print(f"🎯 TP={tp} | 🛑 SL={sl}")

            else:
                print("🤷‍♀️ Sin señal clara (mercado lateral)")

            time.sleep(COOLDOWN)

        except BinanceAPIException as e:
            print(f"⚠️ Error API: {e}")
            time.sleep(5)
        except Exception as e:
            print(f"⚠️ Error general: {e}")
            time.sleep(5)

# ==============================
# EJECUCIÓN
# ==============================
if __name__ == "__main__":
    run_bot()
