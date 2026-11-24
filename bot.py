import os
import time
import json
import threading
import requests
from flask import Flask
from datetime import datetime, timezone

TELEGRAM_BOT_TOKEN = "9256114967:AAFQ4MsUQbb26EGjtZM1H9P1VX5uTFaMzIc"
TELEGRAM_CHAT_ID = "8466057689"

S = chr(47)
app = Flask(__name__)

@app.route(S)
def home():
    return "Polymarket AI Bot is Running 24x7!"

SETTINGS_FILE = "settings.json"
DEFAULT_SETTINGS = {
    "take_profit_pct": 20.0,
    "stop_loss_pct": -8.0,
    "risk_per_trade_pct": 15.0,
    "max_open_trades": 4,
    "min_ai_score": 75,
    "min_volume_usd": 75000
}

balance = 100.0
positions = {}
stats = {"wins": 0, "losses": 0, "total_pnl": 0.0}
last_update_id = 0

def tg_url(method):
    return "https:" + S + S + "api.telegram.org" + S + "bot" + TELEGRAM_BOT_TOKEN.strip() + S + method

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

def get_total_equity(live_prices=None):
    open_val = 0.0
    for m_id, pos in positions.items():
        p = live_prices.get(m_id, pos["entry"]) if live_prices else pos["entry"]
        open_val += pos["shares"] * p
    return balance + open_val

def send_tg(msg):
    try:
        r = requests.post(
            tg_url("sendMessage"),
            json={"chat_id": str(TELEGRAM_CHAT_ID).strip(), "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
        if r.status_code != 200:
            print("TG Send Fail:", r.status_code, r.text[:100], flush=True)
    except Exception as e:
        print("TG Send Err:", e, flush=True)

def check_tg():
    global last_update_id
    try:
        r = requests.get(
            tg_url("getUpdates"),
            params={"offset": last_update_id + 1, "timeout": 1},
            timeout=8
        )
        if r.status_code != 200:
            print("TG Poll Fail:", r.status_code, r.text[:100], flush=True)
            return
        res = r.json()
        for item in res.get("result", []):
            last_update_id = item["update_id"]
            msg_obj = item.get("message", {})
            text = msg_obj.get("text", "").strip().lower()
            incoming_id = str(msg_obj.get("chat", {}).get("id", ""))
            if incoming_id != str(TELEGRAM_CHAT_ID).strip():
                continue

            cfg = load_settings()
            if text.startswith(S + "tp "):
                cfg["take_profit_pct"] = abs(float(text.split()[1]))
                save_settings(cfg)
                send_tg(f"✅ *TP अपडेट:* `+{cfg['take_profit_pct']}%`")
            elif text.startswith(S + "sl "):
                cfg["stop_loss_pct"] = -abs(float(text.split()[1]))
                save_settings(cfg)
                send_tg(f"🛑 *SL अपडेट:* `{cfg['stop_loss_pct']}%`")
            elif text.startswith(S + "risk "):
                cfg["risk_per_trade_pct"] = abs(float(text.split()[1]))
                save_settings(cfg)
                send_tg(f"⚖️ *Auto-Equity साइज:* `{cfg['risk_per_trade_pct']}%`")
            elif text in [S + "status", S + "start"]:
                tot = stats["wins"] + stats["losses"]
                wr = (stats["wins"] / tot * 100) if tot > 0 else 0.0
                eq = get_total_equity()
                next_trade = eq * (cfg["risk_per_trade_pct"] / 100.0)
                send_tg(
                    f"📊 *POLYMARKET AI STATUS*\n"
                    f"💎 कुल इक्विटी: `${eq:.2f}` (कैश: `${balance:.2f}`)\n"
                    f"💵 अगला ट्रेड ({cfg['risk_per_trade_pct']}%): `${next_trade:.2f}`\n"
                    f"📈 कुल PnL: `${stats['total_pnl']:+.2f}`\n"
                    f"🎯 Win Rate: `{wr:.1f}%` ({stats['wins']}W / {stats['losses']}L)\n"
                    f"⚙️ TP: `+{cfg['take_profit_pct']}%` | SL: `{cfg['stop_loss_pct']}%`\n"
                    f"📂 ओपन ट्रेड्स: `{len(positions)}/{cfg['max_open_trades']}`"
                )
    except Exception as e:
        print("Check TG Err:", e, flush=True)

def get_yes_no_prices(market):
    try:
        prices = json.loads(market.get("outcomePrices", "[]"))
        if len(prices) >= 2:
            return float(prices[0]), float(prices[1])
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

    if vol < cfg["min_volume_usd"] or not (0.35 <= yes_p <= 0.65):
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

    if 0.40 <= yes_p <= 0.60:
        score += 15
        reasons.append("Safe Odds")

    return score, yes_p, ", ".join(reasons)

def bot_loop():
    global balance
    print("Bot Started Cleanly!", flush=True)
    cfg = load_settings()
    send_tg(
        f"🚀 *Polymarket AI Bot (24x7 Cloud) चालू!*\n"
        f"💎 शुरुआती इक्विटी: `${balance:.2f}`\n"
        f"⚖️ प्रति ट्रेड रिस्क: `{cfg['risk_per_trade_pct']}%`\n"
        f"🎯 TP: `+{cfg['take_profit_pct']}%` | 🛑 SL: `{cfg['stop_loss_pct']}%`"
    )

        poly_url = "https://" + "gamma" + "-" + "://polymarket.com" + S + "markets"
    params = {"closed": "false", "active": "true", "limit": 50, "order": "volume24hr", "ascending": "false"}

    while True:
        try:
            check_tg()
            cfg = load_settings()
            tp_pct = cfg["take_profit_pct"]
            sl_pct = cfg["stop_loss_pct"]

            res = requests.get(poly_url, params=params, timeout=15)
            if res.status_code != 200:
                print("Poly Fail:", res.status_code, flush=True)
                time.sleep(15)
                continue

            markets = res.json()
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
                    send_tg(f"✅ *TP HIT:* {pos['q']} | `+${pnl_usd:.2f}` (`+{pnl_pct:.1f}%`) | कैश: `${balance:.2f}`")
                    closed.append(m_id)

                elif pnl_pct <= sl_pct:
                    balance += pos["shares"] * curr_p
                    stats["losses"] += 1
                    stats["total_pnl"] += pnl_usd
                    send_tg(f"🔻 *SL HIT:* {pos['q']} | `${pnl_usd:.2f}` (`{pnl_pct:.1f}%`) | कैश: `${balance:.2f}`")
                    closed.append(m_id)

            for m_id in closed:
                del positions[m_id]

            total_eq = get_total_equity(live_prices)
            trade_amt = round(total_eq * (cfg["risk_per_trade_pct"] / 100.0), 2)

            if len(positions) < cfg["max_open_trades"] and balance >= trade_amt and trade_amt >= 2.0:
                for m in markets:
                    m_id = m.get("id")
                    if m_id in positions:
                        continue
                    score, price, reason = ai_score_market(m, cfg)
                    if score >= cfg["min_ai_score"] and price and price > 0:
                        shares = trade_amt / price
                        balance -= trade_amt
                        q_short = m.get("question", "")[:42]
                        positions[m_id] = {"q": q_short, "entry": price, "shares": shares, "score": score}
                        send_tg(
                            f"🟢 *BUY (Score {score}):* {q_short}\n"
                            f"💲 एंट्री: `${price:.3f}` | निवेश: `${trade_amt:.2f}` ({cfg['risk_per_trade_pct']}%)\n"
                            f"🧠 कारण: {reason}"
                        )
                        if len(positions) >= cfg["max_open_trades"]:
                            break

            print(f"Scan OK | Cash: ${balance:.2f} | Open: {len(positions)}", flush=True)
            time.sleep(15)
        except Exception as e:
            print("Loop Err:", e, flush=True)
            time.sleep(10)

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
