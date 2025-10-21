# ==============================
# NUEVA LÓGICA DE SEÑALES (Momentum Fusion Scalper)
# ==============================
def get_best_signal(df):
    row = df.iloc[-1]
    prev = df.iloc[-2]
    
    long_score = 0
    short_score = 0

    # ====== 1. Tendencia principal (EMA crossover + pendiente) ======
    ema_fast_slope = row['ema_fast'] - prev['ema_fast']
    ema_slow_slope = row['ema_slow'] - prev['ema_slow']
    
    if row['ema_fast'] > row['ema_slow'] and ema_fast_slope > ema_slow_slope:
        long_score += 2  # Fuerte momentum alcista
    elif row['ema_fast'] < row['ema_slow'] and ema_fast_slope < ema_slow_slope:
        short_score += 2  # Fuerte momentum bajista

    # ====== 2. Confirmación de fuerza (RSI y MACD) ======
    if row['rsi'] > 55 and row['macd'] > row['macd_signal']:
        long_score += 2
    elif row['rsi'] < 45 and row['macd'] < row['macd_signal']:
        short_score += 2

    # ====== 3. Filtro anti-lateralidad ======
    ema_distance = abs(row['ema_fast'] - row['ema_slow']) / row['close']
    if ema_distance < 0.001:  # Menos del 0.1% = rango lateral
        return None  # Evitar operar en rango

    # ====== 4. Confirmación por vela actual ======
    body = abs(row['close'] - row['open'])
    candle_strength = body / (row['high'] - row['low'] + 1e-6)
    
    if candle_strength > 0.6:  # vela dominante
        if row['close'] > row['open']:
            long_score += 1
        else:
            short
