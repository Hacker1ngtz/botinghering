import os
import time
import math
import pandas as pd
from binance.client import Client
from binance.enums import *

# ==============================
# VARIABLES DE ENTORNO
# ==============================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "BNBUSDT").upper()
LEVERAGE = int(os.getenv("LEVERAGE", 10))
SLEEP_SECONDS = float(os.getenv("SLEEP_SECONDS", 1))  # 1 segundo para scalping

# Indicadores scalping
EMA_FAST = 8
EMA_SLOW = 21
RSI_PERIOD = 5
ATR_LEN = 7
ATR_MULT = 1.2  # Stop/TP factor

# ==============================
# CLIENTE BINANCE FUTURES
# ==============================
client = Client(API_KEY, API_SECRET)

# ==============================
# FUNCIONES AUXILIARES
# ==============================
def round_step(quantity, step):
    precision = max(0, int(round(-math.log10(step))))
    qty = math.floor(quantity / step) * step
    return round(qty, precision)

def round_price(price, tick):
    precision = max(0, int(round(-math.log10(tick))))
    return round(price, precision)

def get_symbol_rules(symbol):
    info = client.futures_exchange_info()
    s_info = next((s for s in info['symbols'] if s['symbol']==symbol), None)
    step_size = float(next(f['stepSize'] for f in s_info['filters'] if f['filterType']=='LOT_SIZE'))
    tick_size = float(next(f['tickSize'] for f in s_info['filters'] if f['filterType']=='PRICE_FILTER'))
    min_notional = float(next(f.get('minNotional',5.0) for f in s_info['filters'] if f['filterType']=='MIN_NOTIONAL'))
    min_qty = float(next(f['minQty'] for f in s_info['filters'] if f['filterType']=='LOT_SIZE'))
    return step_size, tick_size, min_notional, min_qty

def calculate_qty_full_balance(symbol, leverage):
    balances = client.futures_account_balance()
    usdt_balance = next((float(b['balance']) for b in balances if b['asset']=='USDT'),0.0)
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    raw_qty = (usdt_balance * leverage) / price
    step_size, _, _, min_qty = get_symbol_rules(symbol)
    qty = max(round_step(raw_qty, step_size), min_qty)
    return qty

def get_futures_klines(symbol, interval='1m', limit=200):
    klines = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=[
        'open_time','open','high','low','close','volume','close_time',
        'quote_asset_volume','num_trades','taker_buy_base','taker_buy_quote','ignore'
    ])
    for c in ['open','high','low','close','volume']:
        df[c] = df[c].astype(float)
    return df

# ==============================
# CALCULO DE INDICADORES
# ==============================
def calculate_indicators(df):
    df = df.copy()
    df['ema_fast'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()
    # ATR
    df['hl'] = df['high'] - df['low']
    df['hc'] = (df['high'] - df['close'].shift(1)).abs()
    df['lc'] = (df['low'] - df['close'].shift(1)).abs()
    df['tr'] = df[['hl','hc','lc']].max(axis=1)
    df['atr'] = df['tr'].rolling(ATR_LEN).mean()
    # RSI
    delta = df['close'].diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.rolling(RSI_PERIOD).mean()
    roll_down = down.rolling(RSI_PERIOD).mean()
    df['rsi'] = 100 - (100 / (1 + roll_up / roll_down))
    # Tendencia
    df['trend'] = 'NEUTRAL'
    df.loc[(df['ema_fast']>df['ema_slow']) & (df['rsi']>50), 'trend']='LONG'
    df.loc[(df['ema_fast']<df['ema_slow']) & (df['rsi']<50), 'trend']='SHORT'
    return df

def check_signals(df):
    last = df.iloc[-1]
    if last['trend']=='LONG':
        sl = last['close'] - last['atr']*ATR_MULT
        tp = last['close'] + last['atr']*ATR_MULT*2
        return 'LONG', sl, tp
    elif last['trend']=='SHORT':
        sl = last['close'] + last['atr']*ATR_MULT
        tp = last['close'] - last['atr']*ATR_MULT*2
        return 'SHORT', sl, tp
    else:
        return None, None, None

# ==============================
# GESTION DE POSICIONES
# ==============================
def get_current_position(symbol):
    pos = client.futures_position_information(symbol=symbol)
    for p in pos:
        if p['symbol']==symbol:
            return float(p['positionAmt'])
    return 0.0

def close_opposite_if_needed(symbol, target_side):
    amt = get_current_position(symbol)
    if amt==0: return True
    existing_side = 'LONG' if amt>0 else 'SHORT'
    if existing_side==target_side: return True
    side_for_close = SIDE_SELL if amt>0 else SIDE_BUY
    client.futures_create_order(symbol=symbol, side=side_for_close,
                                type=FUTURE_ORDER_TYPE_MARKET, quantity=abs(amt), reduceOnly=True)
    return True

def open_position(symbol, side, sl_price, tp_price):
    close_opposite_if_needed(symbol, side)
    qty = calculate_qty_full_balance(symbol, LEVERAGE)
    step_size, tick_size, _, _ = get_symbol_rules(symbol)
    if qty<=0: 
        print("⚠️ Qty inválida")
        return None

    # Abrir market
    order = client.futures_create_order(
        symbol=symbol,
        side=SIDE_BUY if side=='LONG' else SIDE_SELL,
        type=FUTURE_ORDER_TYPE_MARKET,
        quantity=qty
    )

    # SL
    try:
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL if side=='LONG' else SIDE_BUY,
            type=ORDER_TYPE_STOP_MARKET,
            stopPrice=round_price(sl_price, tick_size),
            quantity=qty,
            reduceOnly=True
        )
    except: print("⚠️ No se pudo colocar SL")

    # TP
    try:
        client.futures_create_order(
            symbol=symbol,
            side=SIDE_SELL if side=='LONG' else SIDE_BUY,
            type=ORDER_TYPE_TAKE_PROFIT_MARKET,
            stopPrice=round_price(tp_price, tick_size),
            quantity=qty,
            reduceOnly=True
        )
    except: print("⚠️ No se pudo colocar TP")

    return order

# ==============================
# LOOP PRINCIPAL SCALPING
# ==============================
if __name__=="__main__":
    client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
    print(f"🚀 Bot scalping iniciado para {SYMBOL} con apalancamiento x{LEVERAGE}")

    current_side = None
    current_qty = 0
    current_sl = 0
    current_tp = 0

    while True:
        try:
            df = get_futures_klines(SYMBOL, interval='1m', limit=200)
            df = calculate_indicators(df)
            signal, sl, tp = check_signals(df)

            if signal:
                qty = calculate_qty_full_balance(SYMBOL, LEVERAGE)
                # Calcula ganancia potencial simple
                price_now = float(client.futures_symbol_ticker(symbol=SYMBOL)['price'])
                potential_new = (tp - price_now)*qty if signal=='LONG' else (price_now - tp)*qty
                potential_current = (current_tp - price_now)*current_qty if current_side=='LONG' else (price_now - current_tp)*current_qty if current_side else 0

                if potential_new>potential_current:
                    print(f"🔄 Cambiando/abriendo posición: {signal}")
                    order = open_position(SYMBOL, signal, sl, tp)
                    if order:
                        current_side = signal
                        current_qty = qty
                        current_sl = sl
                        current_tp = tp
                        print(f"✅ Posición {signal} abierta correctamente")
                else:
                    print(f"⏳ Manteniendo posición {current_side}")
            else:
                print("⏳ Sin señal clara")

        except Exception as e:
            print(f"⚠️ Error inesperado: {e}")

        time.sleep(SLEEP_SECONDS)

