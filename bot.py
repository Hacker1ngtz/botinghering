<?php
require 'vendor/autoload.php';

use Binance\API;

// ========================
// CONFIGURACIÓN
// ========================
$apiKey = getenv('BINANCE_API_KEY');
$apiSecret = getenv('BINANCE_API_SECRET');
$symbol = getenv('SYMBOL') ?: 'AIAUSDT';
$leverage = getenv('LEVERAGE') ?: 10;

$api = new Binance\API($apiKey, $apiSecret);
$api->useServerTime();

// ========================
// PARÁMETROS DE OPERACIÓN
// ========================
$amount = 1;              // USD por operación
$stopLossPct = 0.35;      // 35%
$takeProfitPct = 0.60;    // 60%
$sleepSeconds = getenv('SLEEP_SECONDS') ?: 60;

// ========================
// FUNCIÓN PRINCIPAL
// ========================
while (true) {
    $positions = $api->futuresPositionRisk();
    $openPosition = array_filter($positions, fn($p) => $p['symbol'] === $symbol && abs(floatval($p['positionAmt'])) > 0);

    // Si hay una operación abierta, esperamos
    if (!empty($openPosition)) {
        echo "⏸ Ya hay una operación abierta. Esperando...\n";
        sleep($sleepSeconds);
        continue;
    }

    // Obtener precios y medias
    $candles = $api->futuresCandlesticks($symbol, '1m', 50);
    $closes = array_column($candles, 4);

    $ema9 = trader_ema($closes, 9);
    $ema21 = trader_ema($closes, 21);
    $rsi = trader_rsi($closes, 14);

    $emaFast = end($ema9);
    $emaSlow = end($ema21);
    $rsiNow = end($rsi);

    // Señales de compra/venta
    $buySignal  = $emaFast > $emaSlow && $rsiNow < 30;
    $sellSignal = $emaFast < $emaSlow && $rsiNow > 70;

    $price = floatval($api->futuresPrice($symbol)['price']);
    $quantity = round(($amount * $leverage) / $price, 3);

    if ($buySignal) {
        echo "🟢 Señal de COMPRA detectada a $price\n";
        $api->futuresOrder('BUY', $symbol, $quantity, null, ['type' => 'MARKET']);
        $tp = $price * (1 + $takeProfitPct);
        $sl = $price * (1 - $stopLossPct);
        $api->futuresOrder('SELL', $symbol, $quantity, $tp, ['type' => 'TAKE_PROFIT_MARKET', 'stopPrice' => $tp]);
        $api->futuresOrder('SELL', $symbol, $quantity, $sl, ['type' => 'STOP_MARKET', 'stopPrice' => $sl]);
    }

    if ($sellSignal) {
        echo "🔴 Señal de VENTA detectada a $price\n";
        $api->futuresOrder('SELL', $symbol, $quantity, null, ['type' => 'MARKET']);
        $tp = $price * (1 - $takeProfitPct);
        $sl = $price * (1 + $stopLossPct);
        $api->futuresOrder('BUY', $symbol, $quantity, $tp, ['type' => 'TAKE_PROFIT_MARKET', 'stopPrice' => $tp]);
        $api->futuresOrder('BUY', $symbol, $quantity, $sl, ['type' => 'STOP_MARKET', 'stopPrice' => $sl]);
    }

    sleep($sleepSeconds);
}

