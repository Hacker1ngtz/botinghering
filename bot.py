# bot_scalping_mejorado.py
import os
import time
import math
import pandas as pd
from datetime import datetime
from binance.client import Client
from binance.enums import *

# ==============================
# CONFIG (variables de entorno recomendadas)
# ==============================
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")
SYMBOL = os.getenv("SYMBOL", "BNBUSDT").upper()
LEVERAGE = int(os.getenv("LEVERAGE", 10))
SLEEP_SECONDS = float(os.getenv("SLEEP_SECONDS", 1.5))

# Indicadores / filtros
EMA_FAST = int(os.getenv("EMA_FAST", 8))
EMA_SLOW = int(os.getenv("EMA_SLOW", 21))
RSI_LEN = int(os.getenv("RSI_LEN", 5))
ATR_LEN = int(os.getenv("ATR_LEN", 7))
VWAP_LEN = int(os.getenv("VWAP_LEN", 50))    # número de barras para VWAP local
VOLUME_SPIKE_MULT = float(os.getenv("VOLUME_SPIKE_MULT", 1.8))  # volumen actual > mult * avg_volume

# Riesgo / ordenes
USDT_USAGE_FACTOR = float(os.getenv("USDT_USAGE_FACTOR", 0.6))  # % del balance a usar (0..1)
MIN_TICK_DISTANCE_MULT = float(os.getenv("MIN_TICK_DISTANCE_MULT", 2))  # min ticks away
TRAILING_SL_STEP = float(os.getenv("TRAILING_SL_STEP", 0.6))  # porcentaje del ATR para trailing
ATR_MULT_SL = float(os.getenv("ATR_MULT_SL", 1.0))

# Orderbook filter
OB_IMBALANCE_THRESHOLD = float(os.getenv("OB_IMBALANCE_THRESHOLD", 1.3))  # bid_vol/ask_vol or viceversa

# ==============================
# CLIENTE
# ==============================
if not API_KEY or not API_SECRET:
    raise EnvironmentError("Necesitas configurar BINANCE_API_KEY y BINANCE_API_SECRET en variables de entorno.")

client = Client(API_KEY, API_SECRET)

# ==============================
# UTIL (cache reglas símbolo)
# ==============================
symbol_rules_cache = {}
def get_symbol_rules(symbol):
    if symbol in symbol_rules_cache:
        return symbol_rules_cache[symbol]
    info = client.futures_exchange_info()
    s_info = next((s for s in info['symbols'] if s['symbol'] == symbol), None)
    if not s_info:
        raise ValueError("Símbolo no encontrado en futures_exchange_info")
    step_size = float(next(f['stepSize'] for f in s_info['filters'] if f['filterType'] == 'LOT_SIZE'))
    tick_size = float(next(f['tickSize'] for f in s_info['filters'] if f['filterType'] == 'PRICE_FILTER'))
    min_notional = float(next((f.get('minNotional') for f in s_info['filters'] if f['filterType'] == 'MIN_NOTIONAL'), 5.0))
    min_qty = float(next(f['minQty'] for f in s_info['filters'] if f['filterType'] == 'LOT_SIZE'))
    symbol_rules_cache[symbol] = (step_size, tick_size, min_notional, min_qty)
    return symbol_rules_cache[symbol]

def round_step(quantity, step):
    precision = max(0, int(round(-math.log10(step))))
    qty = math.floor(quantity / step) * step
    return round(qty, precision)

def round_price(price, tick):
    precision = max(0, int(round(-math.log10(tick))))
    return round(price, precision)

# ==============================
# DATOS (klines y VWAP)
# ==============================
def get_klines_dataframe(symbol, interval='1m', limit=200):
    klines = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
    df = pd.DataFrame(klines, columns=[
        'open_time','open','high','low','close','volume','close_time',
        'quote_asset_volume','num_trades','taker_buy_base','taker_buy_quote','ignore'
    ])
    for c in ['open','high','low','close','volume']:
        df[c] = df[c].astype(float)
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    return df

def vwap(df, period=VWAP_LEN):
    # devuelve el VWAP de las últimas `period` barras (simple, intraperiod)
    df2 = df.copy().iloc[-period:]
    df2['typ'] = (df2['high'] + df2['low'] + df2['close'])/3
    df2['pv'] = df2['typ'] * df2['volume']
    cum_pv = df2['pv'].sum()
    cum_v = df2['volume'].sum()
    return cum_pv / cum_v if cum_v > 0 else df2['close'].iloc[-1]

# ==============================
# INDICADORES
# ==============================
def compute_indicators(df):
    df = df.copy()
    df['ema_fast'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()
    delta = df['close'].diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.rolling(RSI_LEN).mean()
    roll_down = down.rolling(RSI_LEN).mean()
    df['rsi'] = 100 - (100 / (1 + roll_up / roll_down))
    # ATR
    df['hl'] = df['high'] - df['low']
    df['hc'] = (df['high'] - df['close'].shift(1)).abs()
    df['lc'] = (df['low'] - df['close'].shift(1)).abs()
    df['tr'] = df[['hl','hc','lc']].max(axis=1)
    df['atr'] = df['tr'].rolling(ATR_LEN).mean()
    # volume rolling mean short
    df['vol_avg_short'] = df['volume'].rolling(20).mean()
    return df

# ==============================
# ORDERBOOK IMBALANCE
# ==============================
def orderbook_imbalance(symbol, depth=10):
    try:
        depth_data = client.futures_depth(symbol=symbol, limit=depth)
        bids = depth_data['bids']
        asks = depth_data['asks']
        bid_vol = sum(float(b[1]) for b in bids)
        ask_vol = sum(float(a[1]) for a in asks)
        if ask_vol == 0:
            return float('inf')
        return bid_vol / ask_vol
    except Exception:
        return 1.0

# ==============================
# QTY SAFE (usar % del saldo, ajustar por margen)
# ==============================
def calculate_qty_safe(symbol, leverage, usage_factor=USDT_USAGE_FACTOR):
    try:
        balances = client.futures_account_balance()
        usdt_balance = next((float(b['balance']) for b in balances if b['asset']=='USDT'), 0.0)
        if usdt_balance <= 0:
            return 0.0
        price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
        step_size, tick_size, min_notional, min_qty = get_symbol_rules(symbol)

        # usar factor configurable y dejar buffer
        raw_qty = (usdt_balance * usage_factor * leverage * 0.98) / price

        # si el notional es menor que min_notional forzamos
        if raw_qty * price < min_notional:
            raw_qty = min_notional / price

        qty = max(round_step(raw_qty, step_size), min_qty)
        # safety: check approx margin requirement: initial margin ~= (notional / leverage)
        # Ensure not to exceed wallet balance
        notional = qty * price
        approx_margin = notional / leverage
        if approx_margin > usdt_balance * 0.995:
            # ajustar qty hacia abajo
            raw_qty2 = ((usdt_balance * 0.995) * leverage) / price
            qty = max(round_step(raw_qty2, step_size), min_qty)
        return qty
    except Exception as e:
        print("⚠️ Error calculate_qty_safe:", e)
        return 0.0

# ==============================
# HELP: cancelar ordenes y buscar órdenes stop previas (para trailing)
# ==============================
def cancel_all_open_orders(symbol):
    try:
        open_orders = client.futures_get_open_orders(symbol=symbol)
        for o in open_orders:
            client.futures_cancel_order(symbol=symbol, orderId=o['orderId'])
        if open_orders:
            print(f"🧹 Canceladas {len(open_orders)} órdenes abiertas previas")
    except Exception as e:
        print("⚠️ Error cancelando órdenes:", e)

def cancel_stop_orders(symbol):
    # cancela only stop/tp style orders to refresh trailing SL
    try:
        open_orders = client.futures_get_open_orders(symbol=symbol)
        for o in open_orders:
            typ = o.get('type','')
            if typ in ('STOP_MARKET','TAKE_PROFIT_MARKET'):
                client.futures_cancel_order(symbol=symbol, orderId=o['orderId'])
    except Exception as e:
        print("⚠️ Error cancel_stop_orders:", e)

# ==============================
# POSICION: abrir, trailing, revisar estado
# ==============================
def get_current_position_amount(symbol):
    try:
        pos = client.futures_position_information(symbol=symbol)
        for p in pos:
            if p['symbol'] == symbol:
                return float(p['positionAmt'])
    except Exception as e:
        print("⚠️ get_current_position_amount:", e)
    return 0.0

def is_position_open(symbol):
    return abs(get_current_position_amount(symbol)) > 0.0

def place_market_and_sl(symbol, side, qty, sl_price):
    # cancela stop orders existentes y crea market + SL (closePosition=True)
    try:
        cancel_stop_orders(symbol)
        # market open
        client.futures_create_order(symbol=symbol,
                                   side=SIDE_BUY if side=='LONG' else SIDE_SELL,
                                   type=FUTURE_ORDER_TYPE_MARKET,
                                   quantity=qty)
        # place SL
        step_size, tick_size, _, _ = get_symbol_rules(symbol)
        slp = round_price(sl_price, tick_size)
        client.futures_create_order(symbol=symbol,
                                   side=SIDE_SELL if side=='LONG' else SIDE_BUY,
                                   type='STOP_MARKET',
                                   stopPrice=slp,
                                   closePosition=True)
        print(f"✅ OPEN {side} qty={qty} SL={slp}")
        return True
    except Exception as e:
        print("⚠️ Error place_market_and_sl:", e)
        return False

def adjust_trailing_stop(symbol, side, atr):
    # cancela stop orders y coloca uno nuevo más lejos (si la posición se ha movido a favor)
    pos_amt = get_current_position_amount(symbol)
    if pos_amt == 0:
        return
    try:
        price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
        step_size, tick_size, _, _ = get_symbol_rules(symbol)
        if side == 'LONG':
            new_sl = round_price(price - TRAILING_SL_STEP * atr, tick_size)
            # place new stop market (closePosition=True)
            cancel_stop_orders(symbol)
            client.futures_create_order(symbol=symbol,
                                       side=SIDE_SELL,
                                       type='STOP_MARKET',
                                       stopPrice=new_sl,
                                       closePosition=True)
            print(f"🔄 Trailing SL moved LONG -> {new_sl}")
        else:
            new_sl = round_price(price + TRAILING_SL_STEP * atr, tick_size)
            cancel_stop_orders(symbol)
            client.futures_create_order(symbol=symbol,
                                       side=SIDE_BUY,
                                       type='STOP_MARKET',
                                       stopPrice=new_sl,
                                       closePosition=True)
            print(f"🔄 Trailing SL moved SHORT -> {new_sl}")
    except Exception as e:
        print("⚠️ adjust_trailing_stop:", e)

# ==============================
# ESTRATEGIA: reglas de entrada robustas
# ==============================
def strategy_check_and_open(symbol):
    # obtiene datos 1m y 5m, calcula indicadores, decide
    df1 = get_klines_dataframe(symbol, interval='1m', limit=120)
    df5 = get_klines_dataframe(symbol, interval='5m', limit=120)
    df1 = compute_indicators(df1)
    df5 = compute_indicators(df5)

    # señales base
    row1 = df1.iloc[-1]
    row5 = df5.iloc[-1]

    # VWAP local
    vwap1 = vwap(df1, period=VWAP_LEN)

    # volume spike?
    vol_spike = (row1['volume'] > (row1['vol_avg_short'] * VOLUME_SPIKE_MULT))

    # orderbook imbalance
    ob_imb = orderbook_imbalance(symbol, depth=10)

    # decide long/short candidate
    candidate = None
    # LONG conditions: 1m trend, 5m trend same, rsi healthy, above VWAP, volume spike, orderbook supports
    long_cond = (row1['ema_fast'] > row1['ema_slow']) and (row5['ema_fast'] > row5['ema_slow']) \
                and (row1['rsi'] > 50) and (row1['close'] > vwap1) and vol_spike and (ob_imb > OB_IMBALANCE_THRESHOLD)
    short_cond = (row1['ema_fast'] < row1['ema_slow']) and (row5['ema_fast'] < row5['ema_slow']) \
                 and (row1['rsi'] < 50) and (row1['close'] < vwap1) and vol_spike and (ob_imb < 1.0/OB_IMBALANCE_THRESHOLD)

    if long_cond:
        candidate = 'LONG'
    elif short_cond:
        candidate = 'SHORT'
    else:
        candidate = None

    if candidate is None:
        return None, None, None, df1

    # ATR y SL
    atr = row1['atr'] if not math.isnan(row1['atr']) else 0
    price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
    # SL a ATR_MULT_SL * ATR, respetando ticks
    step_size, tick_size, _, _ = get_symbol_rules(symbol)
    if candidate == 'LONG':
        sl_price = max(price - ATR_MULT_SL * atr, price - tick_size * MIN_TICK_DISTANCE_MULT)
    else:
        sl_price = min(price + ATR_MULT_SL * atr, price + tick_size * MIN_TICK_DISTANCE_MULT)

    # qty seguro
    qty = calculate_qty_safe(symbol, LEVERAGE)

    # final check guard rails
    if qty <= 0:
        print("⚠️ Qty 0 o insuficiente, no abrimos")
        return None, None, None, df1

    # final candidate return
    return candidate, qty, sl_price, df1

# ==============================
# LOOP PRINCIPAL
# ==============================
def main_loop():
    client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)
    print(f"🚀 Scalping mejorado iniciado {SYMBOL} x{LEVERAGE} - {datetime.utcnow().isoformat()}")

    active_side = None

    while True:
        try:
            # si hay posición abierta -> ajustar trailing y esperar cierre
            if is_position_open(SYMBOL):
                # obtenemos atr para ajustar trailing
                df1 = get_klines_dataframe(SYMBOL, interval='1m', limit=120)
                df1 = compute_indicators(df1)
                atr = df1['atr'].iloc[-1] if not df1['atr'].isna().all() else 0
                # ser prudente: solo mover trailing si hay ATR calculado
                if active_side:
                    adjust_trailing_stop(SYMBOL, active_side, atr)
                print("⏳ Posición abierta. Ajustando trailing y monitoreando...")
            else:
                # intentar abrir según estrategia robusta
                candidate, qty, sl_price, df1 = strategy_check_and_open(SYMBOL)
                if candidate:
                    # re-check last instant orderbook imbalance (última verificación antes de abrir)
                    ob_imb = orderbook_imbalance(SYMBOL, depth=8)
                    if candidate == 'LONG' and ob_imb < OB_IMBALANCE_THRESHOLD:
                        print("❌ Rechazado LONG por imbalance final")
                    elif candidate == 'SHORT' and ob_imb > 1.0/OB_IMBALANCE_THRESHOLD:
                        print("❌ Rechazado SHORT por imbalance final")
                    else:
                        success = place_market_and_sl(SYMBOL, candidate, qty, sl_price)
                        if success:
                            active_side = candidate
                else:
                    print("— Sin señal confirmada (multi-timeframe/volume/vwap/OB) —")
        except Exception as e:
            print("⚠️ Error main_loop:", e)

        time.sleep(SLEEP_SECONDS)

if __name__ == "__main__":
    main_loop()



