import requests
import time
import numpy as np
from datetime import datetime

# ============================================
# CONFIG
# ============================================
BOT_TOKEN = "8795832063:AAF8XUc85R5DT5Fx_VRQog-bE4jnQvKBzXQ"
CHAT_ID = "7868265059"
CHECK_INTERVAL = 60
TWELVEDATA_API_KEY = "9872a424475d447aba65b89866498ae5"

PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY",
    "AUD/USD", "EUR/GBP", "USD/CAD",
    "NZD/USD", "EUR/JPY"
]

# ============================================
# TELEGRAM
# ============================================
def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

# ============================================
# DATA FETCH
# ============================================
def get_candles(pair, interval="1min", outputsize=60):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": pair,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVEDATA_API_KEY
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if "values" not in data:
            print(f"No data for {pair}: {data.get('message', 'unknown')}")
            return None
        values = data["values"]
        opens  = np.array([float(v["open"])  for v in reversed(values)])
        highs  = np.array([float(v["high"])  for v in reversed(values)])
        lows   = np.array([float(v["low"])   for v in reversed(values)])
        closes = np.array([float(v["close"]) for v in reversed(values)])
        return {"open": opens, "high": highs, "low": lows, "close": closes}
    except Exception as e:
        print(f"Data error {pair}: {e}")
        return None

# ============================================
# ZIGZAG
# ============================================
def calculate_zigzag(highs, lows, deviation=0.5, depth=12):
    n = len(highs)
    zigzag = np.zeros(n)
    last_high = highs[0]
    last_low = lows[0]
    last_high_idx = 0
    last_low_idx = 0
    trend = 0
    dev = deviation / 100.0

    for i in range(depth, n):
        local_high = max(highs[max(0, i-depth):i+1])
        local_low  = min(lows[max(0, i-depth):i+1])

        if trend == 0:
            trend = 1
            last_high = local_high
            last_high_idx = i

        if trend == 1:
            if local_high >= last_high:
                last_high = local_high
                last_high_idx = i
            elif local_low <= last_high * (1 - dev):
                zigzag[last_high_idx] = last_high
                trend = -1
                last_low = local_low
                last_low_idx = i

        elif trend == -1:
            if local_low <= last_low:
                last_low = local_low
                last_low_idx = i
            elif local_high >= last_low * (1 + dev):
                zigzag[last_low_idx] = -last_low
                trend = 1
                last_high = local_high
                last_high_idx = i

    return zigzag

# ============================================
# RSI
# ============================================
def calculate_rsi(closes, period=14):
    n = len(closes)
    rsi = np.full(n, np.nan)
    if n < period + 1:
        return rsi
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, n-1):
        avg_gain = (avg_gain * (period-1) + gains[i]) / period
        avg_loss = (avg_loss * (period-1) + losses[i]) / period
        if avg_loss == 0:
            rsi[i+1] = 100
        else:
            rs = avg_gain / avg_loss
            rsi[i+1] = 100 - (100 / (1 + rs))
    return rsi

# ============================================
# STRATEGY
# ============================================
def analyze_pair(pair, data):
    if data is None or len(data["close"]) < 35:
        return None

    opens  = data["open"]
    highs  = data["high"]
    lows   = data["low"]
    closes = data["close"]

    rsi    = calculate_rsi(closes)
    zigzag = calculate_zigzag(highs, lows)

    last_idx = len(closes) - 1
    prev_idx = last_idx - 1
    zz_idx   = last_idx - 2

    last_rsi = rsi[last_idx]
    if np.isnan(last_rsi):
        return None

    # Candle helpers
    def is_bullish(i): return closes[i] > opens[i]
    def is_bearish(i): return closes[i] < opens[i]
    def body(i): return abs(closes[i] - opens[i])
    avg_body = np.mean([body(i) for i in range(len(closes))])
    def is_significant(i): return body(i) >= avg_body * 0.6

    signal = None
    strength = 0
    reasons = []

    # SELL: Higher High → bearish confirmation
    if zigzag[zz_idx] > 0:
        if is_bearish(prev_idx) and is_significant(prev_idx):
            signal = "SELL ⬇️"
            strength += 40
            reasons.append("ZigZag Higher High Reversal")
            if last_rsi > 65:
                strength += 30
                reasons.append(f"RSI Overbought ({last_rsi:.1f})")
            elif last_rsi > 50:
                strength += 15
                reasons.append(f"RSI Neutral-High ({last_rsi:.1f})")
            if is_bearish(last_idx):
                strength += 20
                reasons.append("Current candle bearish")

    # BUY: Lower Low → bullish confirmation
    elif zigzag[zz_idx] < 0:
        if is_bullish(prev_idx) and is_significant(prev_idx):
            signal = "BUY ⬆️"
            strength += 40
            reasons.append("ZigZag Lower Low Reversal")
            if last_rsi < 35:
                strength += 30
                reasons.append(f"RSI Oversold ({last_rsi:.1f})")
            elif last_rsi < 50:
                strength += 15
                reasons.append(f"RSI Neutral-Low ({last_rsi:.1f})")
            if is_bullish(last_idx):
                strength += 20
                reasons.append("Current candle bullish")

    if signal and strength >= 55:
        return {
            "pair": pair, "signal": signal,
            "strength": strength, "rsi": last_rsi,
            "reasons": reasons, "price": closes[last_idx]
        }
    return None

# ============================================
# SEND SIGNAL
# ============================================
def send_signal(result):
    strength = result["strength"]
    if strength >= 80:
        quality = "🔥 STRONG"
        advice = "✅ ট্রেড নাও"
    elif strength >= 65:
        quality = "⚡ MEDIUM"
        advice = "✅ ট্রেড নিতে পারো"
    else:
        quality = "⚠️ WEAK"
        advice = "⛔ Skip করো"

    now = datetime.now().strftime("%H:%M:%S")
    reason_text = "\n• ".join(result["reasons"])
    msg = (
        "📊 <b>TRADING SIGNAL</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        f"💱 <b>Pair:</b> {result['pair']}\n"
        f"📈 <b>Signal:</b> {result['signal']}\n"
        f"💰 <b>Price:</b> {result['price']:.5f}\n"
        f"⏰ <b>Time:</b> {now}\n"
        f"⏳ <b>Expiry:</b> 1 মিনিট\n\n"
        f"📊 <b>Analysis:</b>\n"
        f"• RSI: {result['rsi']:.1f}\n"
        f"• {reason_text}\n\n"
        f"💪 <b>Strength:</b> {strength}% — {quality}\n"
        f"🎯 <b>Action:</b> {advice}\n"
        "━━━━━━━━━━━━━━━━\n"
        "⚠️ <i>নিজের বিচারেই ট্রেড নিন</i>"
    )
    send_telegram(msg)

# ============================================
# COMMAND HANDLER
# ============================================
def handle_commands(last_update_id):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": last_update_id + 1, "timeout": 5}
    try:
        r = requests.get(url, params=params, timeout=10)
        updates = r.json().get("result", [])
        for update in updates:
            last_update_id = update["update_id"]
            text = update.get("message", {}).get("text", "").strip()
            if text == "/start":
                send_telegram("🤖 <b>Bot চালু আছে!</b>\n\n/signal — এখনই scan\n/status — bot status\n/help — সাহায্য")
            elif text == "/status":
                send_telegram(f"✅ <b>Bot চলছে</b>\n🕐 {datetime.now().strftime('%H:%M:%S')}\n📊 {len(PAIRS)}টি pair monitor হচ্ছে")
            elif text == "/signal":
                send_telegram("🔍 scan করছি...")
                scan_all_pairs()
            elif text == "/help":
                send_telegram("/start /signal /status /help")
        return last_update_id
    except Exception as e:
        print(f"Command error: {e}")
        return last_update_id

# ============================================
# SCAN
# ============================================
def scan_all_pairs():
    found = 0
    for pair in PAIRS:
        try:
            data = get_candles(pair)
            result = analyze_pair(pair, data)
            if result:
                send_signal(result)
                found += 1
        except Exception as e:
            print(f"Error {pair}: {e}")
        time.sleep(2)
    if found == 0:
        send_telegram("🔍 এই মুহূর্তে কোনো strong signal নেই।")

# ============================================
# MAIN
# ============================================
def main():
    print("🤖 Trading Bot Starting...")
    send_telegram(
        "🚀 <b>Trading Signal Bot চালু!</b>\n\n"
        "✅ ZigZag + RSI Strategy\n"
        "📊 8টি Pair monitor হচ্ছে\n"
        "⏱️ প্রতি 1 মিনিটে scan\n\n"
        "Signal আসলে এখানে দেখাবে! 🔔"
    )
    last_update_id = 0
    last_scan = 0
    while True:
        last_update_id = handle_commands(last_update_id)
        if time.time() - last_scan >= CHECK_INTERVAL:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Scanning...")
            scan_all_pairs()
            last_scan = time.time()
        time.sleep(5)

if __name__ == "__main__":
    main()
