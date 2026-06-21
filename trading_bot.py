import requests
import time
import pandas as pd
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
    data = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
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
            print(f"No data for {pair}: {data.get('message', 'unknown error')}")
            return None
        df = pd.DataFrame(data["values"])
        df = df.rename(columns={
            "open": "Open", "high": "High",
            "low": "Low", "close": "Close",
            "datetime": "Time"
        })
        df = df.astype({
            "Open": float, "High": float,
            "Low": float, "Close": float
        })
        # Oldest first
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        print(f"Data error {pair}: {e}")
        return None

# ============================================
# ZIGZAG INDICATOR
# Quotex default: deviation=5, depth=12, backstep=3
# ============================================
def calculate_zigzag(df, deviation=0.5, depth=12, backstep=3):
    """
    Quotex ZigZag: deviation=5 means 5 pips (0.0005 for forex), NOT 5%.
    We use 0.5% price change as threshold — realistic for 1min forex.
    """
    highs = df["High"].values
    lows = df["Low"].values
    n = len(highs)

    zigzag = np.zeros(n)
    last_high = highs[0]
    last_low = lows[0]
    last_high_idx = 0
    last_low_idx = 0
    trend = 0  # 0=undefined, 1=uptrend, -1=downtrend

    # deviation as fraction (0.5% = 0.005)
    dev = deviation / 100.0

    for i in range(depth, n):
        local_high = max(highs[max(0, i - depth):i + 1])
        local_low = min(lows[max(0, i - depth):i + 1])

        if trend == 0:
            trend = 1
            last_high = local_high
            last_high_idx = i

        if trend == 1:
            if local_high >= last_high:
                last_high = local_high
                last_high_idx = i
            elif local_low <= last_high * (1 - dev):
                zigzag[last_high_idx] = last_high  # Higher High marked
                trend = -1
                last_low = local_low
                last_low_idx = i

        elif trend == -1:
            if local_low <= last_low:
                last_low = local_low
                last_low_idx = i
            elif local_high >= last_low * (1 + dev):
                zigzag[last_low_idx] = -last_low  # Lower Low marked (negative)
                trend = 1
                last_high = local_high
                last_high_idx = i

    return zigzag

# ============================================
# RSI
# ============================================
def calculate_rsi(closes, period=14):
    s = pd.Series(closes)
    delta = s.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / (loss + 1e-10)  # avoid division by zero
    rsi = 100 - (100 / (1 + rs))
    return rsi.values

# ============================================
# CANDLE HELPERS
# ============================================
def body_size(row):
    return abs(row["Close"] - row["Open"])

def is_bullish(row):
    return row["Close"] > row["Open"]

def is_bearish(row):
    return row["Close"] < row["Open"]

def is_significant(row, df):
    avg = df.apply(body_size, axis=1).mean()
    return body_size(row) >= avg * 0.6  # অন্তত average এর ৬০%

# ============================================
# STRATEGY ANALYSIS
# ZigZag Higher High → Confirmation Bearish → SELL
# ZigZag Lower Low  → Confirmation Bullish → BUY
# ============================================
def analyze_pair(pair, df):
    if df is None or len(df) < 35:
        return None

    closes = df["Close"].values
    rsi = calculate_rsi(closes)
    zigzag = calculate_zigzag(df)

    last_idx = len(df) - 1
    prev_idx = last_idx - 1      # Confirmation candle
    zz_idx = last_idx - 2        # ZigZag touch candle

    last_rsi = rsi[last_idx]
    conf_candle = df.iloc[prev_idx]
    curr_candle = df.iloc[last_idx]

    # NaN check
    if np.isnan(last_rsi):
        return None

    signal = None
    strength = 0
    reasons = []

    # ---- SELL Signal ----
    # ZigZag Higher High detected → confirmation candle bearish → SELL
    if zigzag[zz_idx] > 0:  # Higher High
        if is_bearish(conf_candle) and is_significant(conf_candle, df):
            signal = "SELL ⬇️"
            strength += 40
            reasons.append("ZigZag Higher High Reversal")

            if last_rsi > 65:
                strength += 30
                reasons.append(f"RSI Overbought ({last_rsi:.1f})")
            elif last_rsi > 50:
                strength += 15
                reasons.append(f"RSI Neutral-High ({last_rsi:.1f})")

            if is_bearish(curr_candle):
                strength += 20
                reasons.append("Current candle confirms bearish")

    # ---- BUY Signal ----
    # ZigZag Lower Low detected → confirmation candle bullish → BUY
    elif zigzag[zz_idx] < 0:  # Lower Low (stored as negative)
        if is_bullish(conf_candle) and is_significant(conf_candle, df):
            signal = "BUY ⬆️"
            strength += 40
            reasons.append("ZigZag Lower Low Reversal")

            if last_rsi < 35:
                strength += 30
                reasons.append(f"RSI Oversold ({last_rsi:.1f})")
            elif last_rsi < 50:
                strength += 15
                reasons.append(f"RSI Neutral-Low ({last_rsi:.1f})")

            if is_bullish(curr_candle):
                strength += 20
                reasons.append("Current candle confirms bullish")

    if signal and strength >= 55:
        return {
            "pair": pair,
            "signal": signal,
            "strength": strength,
            "rsi": last_rsi,
            "reasons": reasons,
            "price": closes[last_idx]
        }

    return None

# ============================================
# SEND SIGNAL MESSAGE
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
            msg = update.get("message", {})
            text = msg.get("text", "").strip()

            if text == "/start":
                send_telegram(
                    "🤖 <b>Trading Signal Bot চালু আছে!</b>\n\n"
                    "📊 ZigZag + RSI Strategy Active\n"
                    "⏱️ প্রতি ১ মিনিটে auto scan হবে\n\n"
                    "Commands:\n"
                    "/signal — এখনই signal খোঁজো\n"
                    "/status — bot status দেখো\n"
                    "/help — সাহায্য"
                )
            elif text == "/status":
                send_telegram(
                    f"✅ <b>Bot চলছে</b>\n\n"
                    f"🕐 Time: {datetime.now().strftime('%H:%M:%S')}\n"
                    f"📊 Pairs: {len(PAIRS)}টি monitor হচ্ছে\n"
                    f"⏱️ Scan interval: প্রতি {CHECK_INTERVAL}s"
                )
            elif text == "/signal":
                send_telegram("🔍 সব pairs স্ক্যান করছি...")
                scan_all_pairs()
            elif text == "/help":
                send_telegram(
                    "📖 <b>Help</b>\n\n"
                    "/start — bot চালু\n"
                    "/signal — এখনই signal খোঁজো\n"
                    "/status — bot এর অবস্থা\n"
                    "/help — সাহায্য\n\n"
                    "⚠️ Signal এলেই Quotex এ গিয়ে ট্রেড নাও!"
                )

        return last_update_id
    except Exception as e:
        print(f"Command handler error: {e}")
        return last_update_id

# ============================================
# SCAN ALL PAIRS
# ============================================
def scan_all_pairs():
    found = 0
    for pair in PAIRS:
        try:
            df = get_candles(pair)
            result = analyze_pair(pair, df)
            if result:
                send_signal(result)
                found += 1
        except Exception as e:
            print(f"Error analyzing {pair}: {e}")
        time.sleep(2)  # API rate limit

    if found == 0:
        send_telegram("🔍 এই মুহূর্তে কোনো strong signal নেই।\nপরের scan এ দেখো...")

# ============================================
# MAIN LOOP
# ============================================
def main():
    print("🤖 Trading Bot Starting...")
    send_telegram(
        "🚀 <b>Trading Signal Bot চালু হয়েছে!</b>\n\n"
        "✅ ZigZag + RSI Strategy Active\n"
        "📊 8টি Currency Pair monitor হচ্ছে\n"
        "⏱️ প্রতি 1 মিনিটে auto scan\n\n"
        "Signal আসলে এখানে notification পাবে! 🔔"
    )

    last_update_id = 0
    last_scan = 0

    while True:
        last_update_id = handle_commands(last_update_id)

        if time.time() - last_scan >= CHECK_INTERVAL:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Scanning {len(PAIRS)} pairs...")
            scan_all_pairs()
            last_scan = time.time()

        time.sleep(5)

if __name__ == "__main__":
    main()
