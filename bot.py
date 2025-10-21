# bot_scalping.py — Micro Momentum Breakout (MMB) para scalping en minutos/segundos
import os, time, math, pandas as pd
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException

# =============== CONFIG ===============
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "BNBUSDT").upper()
LEVERAGE = int(os.getenv("LEVERAGE", 15))
DEBUG = os.getenv("DEBUG", "False").lower() in ("1","true","yes")

INTERVAL = os.getenv("INTERVAL", "15s")   # scalping puro: 15s, 30s o 1m
SLEEP_SECONDS = float(os.getenv("SLEEP_SECONDS", 3))
ATR_LEN = int(os.getenv("ATR_LEN", 3))
EMA_FAST = int(os.getenv("EMA_FAST", 5))
VOL_MULT = float(os.getenv("VOL_MULT", 1.3))
USDT_USAGE_FACTOR = float(os.getenv("USDT_USAGE_FACTOR", 0.3))
ATR_MULT_SL = float(os.getenv("ATR_MULT_SL", 1.0))
ATR_MULT_TP = float(os.getenv("ATR_MULT_TP", 1.2))

client = Client(API_KEY, API_SECRET)
client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)

# =============== UTILIDADES ===============
def get_symbol_rules(symbol):
    info = client.futures_exchange_info()
    s_info = next(s for s in info['symbols'] if s['symbol']==symbol)
    step = float(next(f['stepSize'] for f in s_info['filters'] if f['filterType']=='LOT_SIZE'))
    tick = float(next(f['tickSize'] for f in s_info['filters'] if f['filterType']=='PRICE_FILTER'))
    min_qty = float(next(f['minQty'] for f in s_info['filters'] if f['filterType']=='LOT_SIZE'))
    return step, tick, min_qty

def round_step(q, step):
    p = max(0, int(round(-math.log10(step))))
    return round(math.floor(q/step)*step, p)

def round_price(p, tick):
    pr = max(0, int(round(-math.log10(tick))))
    return round(p, pr)

def get_klines(symbol, interval, limit=60):
    kl = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(kl, columns=[
        't','o','h','l','c','v','ct','qv','nt','tb','tq','ignore'
    ])
    for c in ['o','h','l','c','v']:
        df[c] = df[c].astype(float)
    return df

def calc_indicators(df):
    df['ema'] = df['c'].ewm(span=EMA_FAST, adjust=False).mean()
    df['hl'] = df['h'] - df['l']
    df['hc'] = abs(df['h'] - df['c'].shift(1))
    df['lc'] = abs(df['l'] - df['c'].shift(1))
    df['tr'] = df[['hl','hc','lc']].max(axis=1)
    df['atr'] = df['tr'].rolling(ATR_LEN).mean()
    df['vol_mean'] = df['v'].rolling(10).mean()
    return df

def qty_safe(symbol, leverage):
    bal = client.futures_account_balance()
    usdt = next((float(x['balance']) for x in bal if x['asset']=='USDT'), 0)
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    step, tick, min_qty = get_symbol_rules(symbol)
    raw = (usdt * leverage * USDT_USAGE_FACTOR * 0.98) / price
    qty = max(round_step(raw, step), min_qty)
    return qty

def cancel_all(symbol):
    try:
        client.futures_cancel_all_open_orders(symbol=symbol)
    except Exception:
        pass

# =============== MAIN ===============
if __name__ == "__main__":
    print("🚀 Scalping bot iniciado. DEBUG =", DEBUG)
    position = None  # LONG / SHORT
    while True:
        try:
            df = get_klines(SYMBOL, INTERVAL, 60)
            df = calc_indicators(df)
            row, prev = df.iloc[-1], df.iloc[-2]

            # señales
            long_signal = row['c'] > row['ema'] and prev['c'] <= prev['ema'] and row['v'] > row['vol_mean'] * VOL_MULT
            short_signal = row['c'] < row['ema'] and prev['c'] >= prev['ema'] and row['v'] > row['vol_mean'] * VOL_MULT

            if not position:
                if long_signal:
                    atr = row['atr']
                    price = row['c']
                    sl = round_price(price - ATR_MULT_SL*atr, 0.01)
                    tp = round_price(price + ATR_MULT_TP*atr, 0.01)
                    qty = qty_safe(SYMBOL, LEVERAGE)
                    print(f"🟢 LONG {SYMBOL} | qty={qty} | SL={sl} | TP={tp}")
                    if not DEBUG:
                        cancel_all(SYMBOL)
                        client.futures_create_order(symbol=SYMBOL, side="BUY", type="MARKET", quantity=qty)
                        client.futures_create_order(symbol=SYMBOL, side="SELL", type="TAKE_PROFIT_MARKET", stopPrice=tp, closePosition=True)
                        client.futures_create_order(symbol=SYMBOL, side="SELL", type="STOP_MARKET", stopPrice=sl, closePosition=True)
                    position = 'LONG'

                elif short_signal:
                    atr = row['atr']
                    price = row['c']
                    sl = round_price(price + ATR_MULT_SL*atr, 0.01)
                    tp = round_price(price - ATR_MULT_TP*atr, 0.01)
                    qty = qty_safe(SYMBOL, LEVERAGE)
                    print(f"🔴 SHORT {SYMBOL} | qty={qty} | SL={sl} | TP={tp}")
                    if not DEBUG:
                        cancel_all(SYMBOL)
                        client.futures_create_order(symbol=SYMBOL, side="SELL", type="MARKET", quantity=qty)
                        client.futures_create_order(symbol=SYMBOL, side="BUY", type="TAKE_PROFIT_MARKET", stopPrice=tp, closePosition=True)
                        client.futures_create_order(symbol=SYMBOL, side="BUY", type="STOP_MARKET", stopPrice=sl, closePosition=True)
                    position = 'SHORT'
                else:
                    print("⏸ sin señal (esperando ruptura).")

            else:
                # verifica si ya se cerró posición
                pos_data = client.futures_position_information(symbol=SYMBOL)
                amt = float(pos_data[0]['positionAmt'])
                if amt == 0:
                    print(f"✅ {position} cerrada — listo para nueva entrada.")
                    position = None

        except BinanceAPIException as e:
            print("⚠️ API:", e.message)
        except Exception as e:
            print("⚠️ Error:", e)
        time.sleep(SLEEP_SECONDS)



if __name__ == "__main__":
    main()

