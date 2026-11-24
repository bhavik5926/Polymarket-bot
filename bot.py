import os
import csv
import time
import json
import threading
import requests
from flask import Flask
from datetime import datetime, timezone

# ================= TELEGRAM सेटिंग्स =================
TELEGRAM_BOT_TOKEN = "9256114967:AAFQ4MsUQbb26EGjtZM1H9P1VX5uTFaMzIc"
TELEGRAM_CHAT_ID = "8466057689"
# ====================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "Polymarket AI Bot is Running 24/7!"

SETTINGS_FILE = "settings.json"
CSV_REPORT_FILE = "trades_report.csv"

DEFAULT_SETTINGS = {
    "take_profit_pct": 25.0,
    "stop_loss_pct": -10.0,
    "trade_amount": 100.0,
    "max_open_trades": 4,
    "min_ai_score": 75,
    "min_volume_usd": 75000
}

balance = 1000.0
positions = {}
stats = {"wins": 0, "losses": 0, "total_pnl": 0.0}
last_update_id = 0

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "w") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=4)
        return DEFAULT_SETTINGS.copy()
    try:
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return DEFAULT_SETTINGS.copy()

def save_settings(cfg):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(cfg, f, indent=4)

def send_tg(msg):
    if "YOUR_TELEGRAM" in TELEGRAM_BOT_TOKEN:
        return
    try:
        api = "https://telegram.org" + TELEGRAM_BOT_TOKEN + "/sendMessage"
        requests.post(api, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=8)
    except Exception:
        pass

def check_tg():
    global last_update_id
    if "YOUR_TELEGRAM" in TELEGRAM_BOT_TOKEN:
        return
    try:
        api = "https://telegram.org" + TELEGRAM_BOT_TOKEN + "/getUpdates?offset=" + str(last_update_id + 1) + "&timeout=1"
        res = requests.get(api, timeout=5).json()
        for item in res.get("result", []):
            last_update_id = item["update_id"]
            msg_obj = item.get("message", {})
            text = msg_obj.get("text", "").strip().lower()
            chat_id = str(msg_obj.get("chat", {}).get("id", ""))
            if chat_id != str(TELEGRAM_CHAT_ID):
                continue

            cfg = load_settings()
            if text.startswith("/tp "):
                cfg["take_profit_pct"] = abs(float(text.split()))
                save_settings(cfg)
                send_tg(f"✅ *TP अपडेट:* `+{cfg['take_profit_pct']}%`")
            elif text.startswith("/sl "):
                cfg["stop_loss_pct"] = -abs(float(text.split()))
                save_settings(cfg)
                send_tg(f"🛑 *SL अपडेट:* `{cfg['stop_loss_pct']}%`")
            elif text == "/status":
                tot = stats["wins"] + stats["losses"]
                wr = (stats["wins"] / tot * 100) if tot > 0 else 0.0
                send_tg(
                    f"📊 *स्टेटस (Render Cloud)*\n💰 कैश: `${balance:.2f}`\n📈 PnL: `${stats['total_pnl']:+.2f}`\n"
                    f"🎯 Win Rate: `{wr:.1f}%` ({stats['wins']}W/{stats['losses']}L)\n"
                    f"⚙️ TP: `+{cfg['take_profit_pct']}%` | SL: `{cfg['stop_loss_pct']}%`"
                )
    except Exception:
        pass

def get_yes_no_prices(market):
    try:
        prices = json.loads(market.get("outcomePrices", "[]"))
        if len(prices) >= 2:
            return float(prices), float(prices)
    except Exception:
        pass
    return None, None

def ai_score_market(market, cfg):
    yes_p, no_p = get_yes_no_prices(market)
    if yes_p is None:
        return 0, None, ""

    vol = float(market.get("volumeNum", 0))
    vol_24h = float(market.get("volume24hr", 0))
    chg_24h = float(market.get("oneDayPriceChange", 0))
    spread = abs(1.0 - (yes_p + no_p))

    if vol < cfg["min_volume_usd"] or not (0.25 <= yes_p <= 0.68):
        return 0, None, ""

    end_str = market.get("endDate")
    if end_str:
        try:
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            days = (end_dt - datetime.now(timezone.utc)).days
            if days < 2 or days > 45:
                return 0, None, ""
        except Exception:
            pass

    score = 0
    reasons = []
    if vol_24h > 25000:
        score += 30
        reasons.append("High 24h Vol")
    elif vol_24h > 10000:
        score += 20

    if (yes_p + no_p) < 0.985:
        score += 25
        reasons.append("Arbitrage")
    elif spread <= 0.02:
        score += 20
        reasons.append("Tight Spread")

    if 0.03 <= chg_24h <= 0.12:
        score += 30
        reasons.append(f"Uptrend +{chg_24h*100:.1f}%")
    elif chg_24h > 0.12:
        score += 15

    if 0.35 <= yes_p <= 0.58:
        score += 15
        reasons.append("Sweet-Spot Odds")

    return score, yes_p, ", ".join(reasons)

def bot_loop():
    global balance
    cfg = load_settings()
    send_tg(f"🚀 *Polymarket AI Bot (Render 24/7) चालू!*\n💰 बैलेंस: `${balance:.2f}`\n🎯 TP: `+{cfg['take_profit_pct']}%` | 🛑 SL: `{cfg['stop_loss_pct']}%`")

    api_url = "https://polymarket.com"

    while True:
        try:
            check_tg()
            cfg = load_settings()
            tp_pct = cfg["take_profit_pct"]
            sl_pct = cfg["stop_loss_pct"]

            markets = requests.get(api_url, timeout=15).json()

            live_prices = {}
            for m in markets:
                yp, _ = get_yes_no_prices(m)
                if yp is not None:
                    live_prices[m.get("id")] = yp

            closed = []
            for m_id, pos in positions.items():
                curr_p = live_prices.get(m_id, pos["entry"])
                pnl_pct = ((curr_p - pos["entry"]) / pos["entry"]) * 100
                pnl_usd = (curr_p - pos["entry"]) * pos["shares"]

                if pnl_pct >= tp_pct:
                    balance += pos["shares"] * curr_p
                    stats["wins"] += 1
                    stats["total_pnl"] += pnl_usd
                    send_tg(f"✅ *TP HIT:* {pos['q']} | +${pnl_usd:.2f} (+{pnl_pct:.1f}%)")
                    closed.append(m_id)

                elif pnl_pct <= sl_pct:
                    balance += pos["shares"] * curr_p
                    stats["losses"] += 1
                    stats["total_pnl"] += pnl_usd
                    send_tg(f"🔻 *SL HIT:* {pos['q']} | ${pnl_usd:.2f} ({pnl_pct:.1f}%)")
                    closed.append(m_id)

            for m_id in closed:
                del positions[m_id]

            if len(positions) < cfg["max_open_trades"] and balance >= cfg["trade_amount"]:
                for m in markets:
                    m_id = m.get("id")
                    if m_id in positions:
                        continue
                    score, price, reason = ai_score_market(m, cfg)
                    if score >= cfg["min_ai_score"] and price and price > 0:
                        shares = cfg["trade_amount"] / price
                        balance -= cfg["trade_amount"]
                        q_short = m.get("question", "")[:42]
                        positions[m_id] = {"q": q_short, "entry": price, "shares": shares, "score": score}
                        send_tg(f"🟢 *BUY (Score {score}):* {q_short} @ ${price:.3f} ({reason})")
                        if len(positions) >= cfg["max_open_trades"]:
                            break

            time.sleep(20)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
