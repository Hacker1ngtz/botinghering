<?php
// scalper_binance_futures.php
// Ejecuta trades reales en Binance Futures (USDT-M) según tu PineScript:
// EMA(9,21) crossover + RSI(14) sobrevendido/sobrecomprado + MACD(12,26,9) crossover
//
// Requisitos: PHP 7.4+, ext-curl enabled
// WARNING: Operaciones reales. Usa dryRun=true para pruebas en producción antes de arriesgar capital.

// ---------------- CONFIG ----------------
const API_KEY    = 'TU_API_KEY_AQUI';
const API_SECRET = 'TU_API_SECRET_AQUI';
$dryRun = true;               // true = no ejecuta órdenes; false = ejecuta en fapi.binance.com (no testnet)
$symbol  = 'BTCUSDT';         // Par
$interval = '1m';             // Timeframe de velas
$klinesLimit = 200;           // cuántas velas bajar para calcular indicadores
$orderSizeUSDT = 20.0;        // USDT por operación (ajusta)
$recvWindow = 5000;           // recvWindow para firmas
$leverage = 20;               // leva deseada (no cambia la leva de la cuenta aquí)
// -----------------------------------------

$baseApi = $dryRun ? 'https://fapi.binance.com' : 'https://fapi.binance.com'; // same host; dryRun toggles execution in code

// ---------- Helpers Binance signed request ----------
function hmacSha256($data, $secret) {
    return hash_hmac('sha256', $data, $secret);
}
function httpRequest($method, $endpoint, $params = [], $signed = false) {
    global $baseApi;
    $url = $baseApi . $endpoint;
    $headers = ['X-MBX-APIKEY: ' . API_KEY];
    if ($signed) {
        $ts = round(microtime(true) * 1000);
        $params['timestamp'] = $ts;
        if (!isset($params['recvWindow'])) $params['recvWindow'] = 5000;
        $qs = http_build_query($params, '', '&', PHP_QUERY_RFC1738);
        $sig = hmacSha256($qs, API_SECRET);
        $qs .= '&signature=' . $sig;
        if ($method === 'GET') $url .= '?' . $qs;
    } else {
        if (!empty($params) && $method === 'GET') $url .= '?' . http_build_query($params);
    }

    $ch = curl_init();
    curl_setopt($ch, CURLOPT_URL, $url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
    curl_setopt($ch, CURLOPT_TIMEOUT, 20);

    if ($signed && $method !== 'GET') {
        // For signed POST/DELETE, send as application/x-www-form-urlencoded body with signature
        $bodyQs = http_build_query($params, '', '&', PHP_QUERY_RFC1738);
        $sig = hmacSha256($bodyQs, API_SECRET);
        $bodyQs .= '&signature=' . $sig;
        curl_setopt($ch, CURLOPT_POSTFIELDS, $bodyQs);
        curl_setopt($ch, CURLOPT_HTTPHEADER, array_merge($headers, ['Content-Type: application/x-www-form-urlencoded']));
    } elseif ($method === 'POST' && !$signed) {
        curl_setopt($ch, CURLOPT_POSTFIELDS, http_build_query($params));
    }

    if ($method === 'DELETE') {
        curl_setopt($ch, CURLOPT_CUSTOMREQUEST, 'DELETE');
    } elseif ($method === 'PUT') {
        curl_setopt($ch, CURLOPT_CUSTOMREQUEST, 'PUT');
    } elseif ($method !== 'GET' && $signed) {
        curl_setopt($ch, CURLOPT_CUSTOMREQUEST, 'POST');
    }

    $res = curl_exec($ch);
    $err = curl_error($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if ($res === false) throw new Exception("cURL error: $err");

    $json = json_decode($res, true);
    if ($json === null) {
        // return raw if not json
        return ['code' => $code, 'raw' => $res];
    }
    return ['code' => $code, 'body' => $json];
}

// ---------- Market data ----------
function getKlines($symbol, $interval, $limit = 200) {
    $endpoint = '/fapi/v1/klines';
    $params = ['symbol' => $symbol, 'interval' => $interval, 'limit' => $limit];
    $resp = httpRequest('GET', $endpoint, $params, false);
    if ($resp['code'] !== 200) throw new Exception("Error getKlines: " . json_encode($resp));
    return $resp['body']; // array of arrays
}

// ---------- Account / Balance ----------
function getFutureBalance() {
    $endpoint = '/fapi/v2/balance';
    $resp = httpRequest('GET', $endpoint, [], true);
    if ($resp['code'] !== 200) throw new Exception("Error balance: " . json_encode($resp));
    return $resp['body']; // array of balances
}

// ---------- Exchange Info (for filters) ----------
function getExchangeInfo() {
    $endpoint = '/fapi/v1/exchangeInfo';
    $resp = httpRequest('GET', $endpoint, [], false);
    if ($resp['code'] !== 200) throw new Exception("Error exchangeInfo: " . json_encode($resp));
    return $resp['body'];
}

// ---------- Order placement ----------
function placeMarketOrder($symbol, $side, $quantity, $reduceOnly = false) {
    // side: 'BUY' or 'SELL'
    $endpoint = '/fapi/v1/order';
    $params = [
        'symbol' => $symbol,
        'side' => $side,
        'type' => 'MARKET',
        'quantity' => (string)$quantity,
        'timestamp' => round(microtime(true) * 1000),
        'recvWindow' => 5000
    ];
    if ($reduceOnly) $params['reduceOnly'] = 'true';
    // signature will be added inside httpRequest signed branch
    $resp = httpRequest('POST', $endpoint, $params, true);
    return $resp;
}

// ---------- Utils: EMA, RSI, MACD ----------
function emaArray(array $prices, int $period) {
    $n = count($prices);
    if ($n === 0) return [];
    $out = array_fill(0, $n, null);
    $k = 2 / ($period + 1);
    // seed with SMA first
    $sum = 0.0;
    for ($i = 0; $i < $period && $i < $n; $i++) $sum += $prices[$i];
    $out[$period-1] = $sum / $period;
    for ($i = $period; $i < $n; $i++) {
        $out[$i] = ($prices[$i] - $out[$i-1]) * $k + $out[$i-1];
    }
    // fill leading nulls for i < period-1 with the first computed sma
    for ($i = 0; $i < $period-1 && $i < $n; $i++) $out[$i] = null;
    return $out;
}

function rsiArray(array $prices, int $period = 14) {
    $n = count($prices);
    $out = array_fill(0, $n, null);
    if ($n < $period) return $out;
    $gains = 0.0; $losses = 0.0;
    for ($i = 1; $i <= $period; $i++) {
        $chg = $prices[$i] - $prices[$i-1];
        if ($chg >= 0) $gains += $chg; else $losses += abs($chg);
    }
    $avgGain = $gains / $period;
    $avgLoss = $losses / $period;
    $rs = $avgLoss == 0 ? 1000 : $avgGain / $avgLoss;
    $out[$period] = 100 - (100 / (1 + $rs));
    for ($i = $period+1; $i < $n; $i++) {
        $chg = $prices[$i] - $prices[$i-1];
        $gain = $chg > 0 ? $chg : 0;
        $loss = $chg < 0 ? abs($chg) : 0;
        $avgGain = ($avgGain * ($period - 1) + $gain) / $period;
        $avgLoss = ($avgLoss * ($period - 1) + $loss) / $period;
        $rs = $avgLoss == 0 ? 1000 : $avgGain / $avgLoss;
        $out[$i] = 100 - (100 / (1 + $rs));
    }
    return $out;
}

function macdArrays(array $prices, $fast = 12, $slow = 26, $signal = 9) {
    $emaFast = emaArray($prices, $fast);
    $emaSlow = emaArray($prices, $slow);
    $n = count($prices);
    $macd = array_fill(0, $n, null);
    for ($i = 0; $i < $n; $i++) {
        if ($emaFast[$i] !== null && $emaSlow[$i] !== null) $macd[$i] = $emaFast[$i] - $emaSlow[$i];
        else $macd[$i] = null;
    }
    $signalArr = emaArray(array_map(function($v){return $v===null?0:$v;}, $macd), $signal);
    return [$macd, $signalArr];
}

// ---------- Round quantity to stepSize ----------
function roundToStep($quantity, $stepSize) {
    // stepSize like "0.001" -> round down to nearest step
    $precision = 0;
    if (strpos($stepSize, '1') === 0 && strpos($stepSize, '.') === false) {
        $precision = 0;
    } else {
        $parts = explode('.', $stepSize);
        $precision = isset($parts[1]) ? strlen(rtrim($parts[1], '0')) : 0;
    }
    // floor to step
    $mult = 1 / floatval($stepSize);
    $q = floor($quantity * $mult) / $mult;
    return round($q, $precision);
}

// ---------- Main logic ----------
try {
    echo "[" . date('Y-m-d H:i:s') . "] Descargando velas...\n";
    $klines = getKlines($symbol, $interval, $klinesLimit);

    $close = []; $open = []; $high = []; $low = []; $vol = [];
    foreach ($klines as $k) {
        // k: [ openTime, open, high, low, close, volume, ... ]
        $open[]  = floatval($k[1]);
        $high[]  = floatval($k[2]);
        $low[]   = floatval($k[3]);
        $close[] = floatval($k[4]);
        $vol[]   = floatval($k[5]);
    }

    // Calculate indicators
    $emaFastArr = emaArray($close, 9);
    $emaSlowArr = emaArray($close, 21);
    $rsiArr = rsiArray($close, 14);
    [$macdArr, $signalArr] = macdArrays($close, 12, 26, 9);

    $last = count($close) - 1;
    if ($last < 2) throw new Exception("Pocas velas para calcular indicadores.");

    // Crossovers detection using last and prev
    $emaFast = $emaFastArr[$last];
    $emaSlow = $emaSlowArr[$last];
    $emaFastPrev = $emaFastArr[$last-1];
    $emaSlowPrev = $emaSlowArr[$last-1];

    $macd = $macdArr[$last]; $macdPrev = $macdArr[$last-1];
    $signal = $signalArr[$last]; $signalPrev = $signalArr[$last-1];

    $rsi = $rsiArr[$last];

    $emaCrossover = ($emaFast !== null && $emaSlow !== null && $emaFastPrev !== null && $emaSlowPrev !== null)
        ? ($emaFast > $emaSlow && $emaFastPrev <= $emaSlowPrev) : false;
    $emaCrossunder = ($emaFast !== null && $emaSlow !== null && $emaFastPrev !== null && $emaSlowPrev !== null)
        ? ($emaFast < $emaSlow && $emaFastPrev >= $emaSlowPrev) : false;

    $macdCrossover = ($macd !== null && $signal !== null && $macdPrev !== null && $signalPrev !== null)
        ? ($macd > $signal && $macdPrev <= $signalPrev) : false;
    $macdCrossunder = ($macd !== null && $signal !== null && $macdPrev !== null && $signalPrev !== null)
        ? ($macd < $signal && $macdPrev >= $signalPrev) : false;

    $rsiOversold = ($rsi !== null) ? ($rsi < 30) : false;
    $rsiOverbought = ($rsi !== null) ? ($rsi > 70) : false;

    $buySignal = ($emaCrossover && $rsiOversold && $macdCrossover);
    $sellSignal = ($emaCrossunder && $rsiOverbought && $macdCrossunder);

    echo "Última vela: close={$close[$last]}, RSI={$rsi}\n";
    echo "EMA crossover: ".($emaCrossover?'YES':'no')."  EMA crossunder: ".($emaCrossunder?'YES':'no')."\n";
    echo "MACD cross: ".($macdCrossover?'up':($macdCrossunder?'down':'none'))."\n";
    echo "Señal BUY: ".($buySignal?'YES':'no')."  Señal SELL: ".($sellSignal?'YES':'no')."\n";

    if (!$buySignal && !$sellSignal) {
        echo "No hay señal. Fin.\n";
        exit;
    }

    // Get exchangeInfo to compute stepSize & lot size
    $exInfo = getExchangeInfo();
    // find symbol filters
    $symInfo = null;
    foreach ($exInfo['symbols'] as $s) if ($s['symbol'] === $symbol) { $symInfo = $s; break; }
    if ($symInfo === null) throw new Exception("Symbol info no encontrada en exchangeInfo");

    $lotSizeFilter = null;
    foreach ($symInfo['filters'] as $f) {
        if ($f['filterType'] === 'LOT_SIZE') { $lotSizeFilter = $f; break; }
    }
    $stepSize = $lotSizeFilter['stepSize'] ?? '0.000001';

    // Get balance USDT in futures wallet
    $balances = getFutureBalance();
    $usdtBalance = 0.0;
    foreach ($balances as $b) {
        if ($b['asset'] === 'USDT') { $usdtBalance = floatval($b['balance']); break; }
    }
    echo "FUTURES USDT balance: {$usdtBalance}\n";

    // Compute quantity from $orderSizeUSDT and current price (market)
    $price = $close[$last];
    $rawQty = ($orderSizeUSDT / $price);
    $qty = roundToStep($rawQty, $stepSize);

    if ($qty <= 0) throw new Exception("Quantity calculada <= 0. Ajusta orderSizeUSDT o revisa precio/stepSize.");

    // Before opening new same-direction position, close opposite side (if exists)
    // Query positions
    $accountInfoResp = httpRequest('GET', '/fapi/v2/positionRisk', [], true);
    if ($accountInfoResp['code'] !== 200) throw new Exception("Error fetching positions");
    $positions = $accountInfoResp['body'];
    $posAmt = 0.0;
    foreach ($positions as $p) {
        if ($p['symbol'] === $symbol) { $posAmt = floatval($p['positionAmt']); break; }
    }
    // posAmt > 0 => long open, posAmt < 0 => short open

    // If buySignal and there's a short open, close short first (reduceOnly SELL -> BUY to close)
    if ($buySignal && $posAmt < 0) {
        echo "Cerrando posición short previa (reduceOnly)...\n";
        if (!$dryRun) {
            $respClose = placeMarketOrder($symbol, 'BUY', abs($posAmt), true);
            echo "Close short resp: " . json_encode($respClose) . "\n";
            sleep(1);
        } else {
            echo "[dryRun] Simulo cierre short qty=" . abs($posAmt) . "\n";
        }
    }

    // If sellSignal and there's a long open, close long first
    if ($sellSignal && $posAmt > 0) {
        echo "Cerrando posición long previa (reduceOnly)...\n";
        if (!$dryRun) {
            $respClose = placeMarketOrder($symbol, 'SELL', abs($posAmt), true);
            echo "Close long resp: " . json_encode($respClose) . "\n";
            sleep(1);
        } else {
            echo "[dryRun] Simulo cierre long qty=" . abs($posAmt) . "\n";
        }
    }

    // Place new market order in desired direction
    if ($buySignal) {
        echo "Ejecutando orden MARKET BUY qty={$qty}\n";
        if (!$dryRun) {
            $resp = placeMarketOrder($symbol, 'BUY', $qty, false);
            echo "Order resp: " . json_encode($resp) . "\n";
        } else {
            echo "[dryRun] Simulo BUY qty={$qty}\n";
        }
    } elseif ($sellSignal) {
        echo "Ejecutando orden MARKET SELL qty={$qty}\n";
        if (!$dryRun) {
            $resp = placeMarketOrder($symbol, 'SELL', $qty, false);
            echo "Order resp: " . json_encode($resp) . "\n";
        } else {
            echo "[dryRun] Simulo SELL qty={$qty}\n";
        }
    }

    echo "Fin del script.\n";

} catch (Exception $e) {
    echo "ERROR: " . $e->getMessage() . "\n";
    exit(1);
}
