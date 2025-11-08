import time
import ccxt

# ============================================
# CONFIGURACIÓN GENERAL
# ============================================

API_KEY = "TU_API_KEY"
API_SECRET = "TU_API_SECRET"
EXCHANGE = "binanceusdm"  # o "bybit", "okx", etc.
SYMBOL = "BTC/USDT"
TIMEFRAME = "5m"
LEVERAGE = 10
RISK_PERCENT = 0.25  # 25% Stop Loss
SLEEP_TIME = 60 * 5  # Cada 5 minutos

# ============================================
# CONEXIÓN CON EL EXCHANGE
# ============================================

exchange = getattr(ccxt, EXCHANGE)({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "enableRateLimit": True
})
exchange.load_markets()
print(f"[{time.strftime('%H:%M:%S')}] Conectado a {EXCHANGE} con símbolo {SYMBOL}")

# ============================================
# FUNCIONES AUXILIARES
# ============================================

def get_candles(symbol, timeframe, limit=100):
    candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    return candles

def ema(values, length):
    k = 2 / (length + 1)
    ema_val = []
    for i, val in enumerate(values):
        if i == 0:
            ema_val.append(val)
        else:
            ema_val.append(val * k + ema_val[-1] * (1 - k))
    return ema_val

def rsi(values, length=14):
    gains, losses = [], []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))
    avg_gain = sum(gains[:length]) / length
    avg_loss = sum(losses[:length]) / length
    rs = avg_gain / avg_loss if avg_loss != 0 else 0
    rsi_list = [100 - (100 / (1 + rs))]
    for i in range(length, len(values)):
        avg_gain = (avg_gain * (length - 1) + gains[i]) / length
        avg_loss = (avg_loss * (length - 1) + losses[i]) / length
        rs = avg_gain / avg_loss if avg_loss != 0 else 0
        rsi_list.append(100 - (100 / (1 + rs)))
    return [None] * (len(values) - len(rsi_list)) + rsi_list

# ============================================
# LÓGICA DE SEÑALES
# ============================================

def check_signals():
    candles = get_candles(SYMBOL, TIMEFRAME, 100)
    closes = [c[4] for c in candles]

    ema_fast = ema(closes, 9)
    ema_slow = ema(closes, 21)
    rsi_vals = rsi(closes, 14)

    last_close = closes[-1]
    last_ema_fast = ema_fast[-1]
    last_ema_slow = ema_slow[-1]
    last_rsi = rsi_vals[-1]

    buy_signal = (last_ema_fast > last_ema_slow and last_rsi < 60)
    sell_signal = (last_ema_fast < last_ema_slow and last_rsi > 40)

    return buy_signal, sell_signal, last_close

# ============================================
# EJECUCIÓN DE ÓRDENES
# ============================================

def get_position():
    positions = exchange.fetch_positions([SYMBOL])
    for p in positions:
        if float(p["contracts"]) > 0:
            return p
    return None

def close_position(position):
    side = "sell" if position["side"] == "long" else "buy"
    amount = abs(float(position["contracts"]))
    print(f"[{time.strftime('%H:%M:%S')}] Cerrando posición {position['side']} de {amount} contratos")
    exchange.create_market_order(SYMBOL, side, amount)

def open_position(side, amount, price):
    sl_price = price * (1 - RISK_PERCENT) if side == "buy" else price * (1 + RISK_PERCENT)
    print(f"[{time.strftime('%H:%M:%S')}] Abrir {side.upper()} - Precio: {price}, SL: {sl_price}")
    order = exchange.create_market_order(SYMBOL, side, amount)
    # Colocar stop loss
    exchange.create_order(SYMBOL, "stop_market",
                          "sell" if side == "buy" else "buy",
                          amount,
                          sl_price,
                          {"reduceOnly": True})
    return order

# ============================================
# LOOP PRINCIPAL
# ============================================

while True:
    try:
        buy_signal, sell_signal, last_price = check_signals()
        position = get_position()

        if buy_signal and (not position or position["side"] == "short"):
            if position:
                close_position(position)
            open_position("buy", 0.001, last_price)

        elif sell_signal and (not position or position["side"] == "long"):
            if position:
                close_position(position)
            open_position("sell", 0.001, last_price)

        # Logs
        print(f"[{time.strftime('%H:%M:%S')}] Señales -> Buy: {buy_signal}, Sell: {sell_signal}")
        if position:
            print(f"[{time.strftime('%H:%M:%S')}] Posición actual: {position['side']} - {position['contracts']} contratos")
        else:
            print(f"[{time.strftime('%H:%M:%S')}] No hay posición abierta")

        time.sleep(SLEEP_TIME)

    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] Error:", e)
        time.sleep(60)
