import os
import time
import json
import threading
import requests
from flask import Flask
from datetime import datetime, timezone, timedelta

# ================= सेटिंग्स और टोकन =================
TELEGRAM_BOT_TOKEN = os.environ.get("TG_TOKEN", "9256114967:AAFQ4MsUQbb26EGjtZM1H9P1VX5uTFaMzIc")
TELEGRAM_CHAT_ID = "8466057689"
VIP_CHANNEL_ID = ""  # भविष्य में अपने VIP चैनल की ID (जैसे "-1001234567890") यहाँ डालें
# ====================================================

S = chr(47)
IST = timezone(timedelta(hours=5, minutes=30))
app = Flask(__name__)

@app.route(S)
def home():
    return "Polymarket AI Pro Journal Bot is Running 24x7!"

SETTINGS_FILE = "settings.json"
DEFAULT_SETTINGS = {
    "take_profit_pct": 15.0,      # आपका नया TP (+15%)
    "stop_loss_pct": -8.0,        # Stop-Loss (-8%)
    "breakeven_trigger": 10.0,    # +10% मुनाफे पर SL को +0.5% पर लॉक करेगा
    "daily_loss_limit_pct": 10.0, # एक दिन में अधिकतम 10% नुकसान सीमा
    "risk_per_trade_pct": 15.0,   # कुल इक्विटी का 15% प्रति ट्रेड
    "max_hold_hours": 24.0,       # अधिकतम 24 घंटे होल्डिंग
    "max_open_trades": 4,
    "min_ai_score": 75,
    "min_volume_usd": 75000
}

balance = 100.0
positions = {}
trade_journal = []
stats = {"wins": 0, "losses": 0, "total_pnl": 0.0}
last_update_id = 0

# दैनिक और साप्ताहिक ट्रैकिंग
current_day_str = datetime.now(IST).strftime("%Y-%m-%d")
day_start_equity = 100.0
daily_paused = False
last_weekly_report_week = ""

def tg_url(method):
    return "https:" + S + S + "api.telegram.org" + S + "bot" + TELEGRAM_BOT_TOKEN.strip() + S + method

def poly_base_url():
    sub = "".join([chr(103), chr(97), chr(109), chr(109), chr(97), chr(45), chr(97), chr(112), chr(105)])
    return "https:" + S + S + sub + ".polymarket.com" + S + "markets"

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "w") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=4)
        return DEFAULT_SETTINGS.copy()
    try:
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
            for k, v in DEFAULT_SETTINGS.items():
                if k not in data:
                    data[k] = v
            return data
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

def send_tg(msg, target_chat=None):
    chat = target_chat if target_chat else str(TELEGRAM_CHAT_ID).strip()
    if not chat:
        return
    try:
        requests.post(
            tg_url("sendMessage"),
            json={"chat_id": chat, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        print("TG Send Err:", e, flush=True)

def send_vip_signal(msg):
    if VIP_CHANNEL_ID.strip():
        send_tg(msg, target_chat=VIP_CHANNEL_ID.strip())

def format_duration(seconds):
    mins = int(seconds // 60)
    hrs = mins // 60
    rem_mins = mins % 60
    return f"{hrs}h {rem_mins}m" if hrs > 0 else f"{rem_mins}m"

def build_weekly_report():
    now_ist = datetime.now(IST)
    week_ago = now_ist - timedelta(days=7)
    weekly_trades = [t for t in trade_journal if t["close_dt"] >= week_ago]
    
    if not weekly_trades:
        return "📅 *WEEKLY PERFORMANCE REPORT*\nइस हफ्ते अभी तक कोई ट्रेड बंद नहीं हुआ है।"

    w_wins = sum(1 for t in weekly_trades if t["pnl_usd"] >= 0)
    w_losses = len(weekly_trades) - w_wins
    w_pnl = sum(t["pnl_usd"] for t in weekly_trades)
    w_wr = (w_wins / len(weekly_trades)) * 100
    best_t = max(weekly_trades, key=lambda x: x["pnl_usd"])
    worst_t = min(weekly_trades, key=lambda x: x["pnl_usd"])
    eq = get_total_equity()

    return (
        f"📅 *WEEKLY PERFORMANCE REPORT (7 Days)*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 कुल बंद ट्रेड्स: `{len(weekly_trades)}`\n"
        f"🎯 साप्ताहिक Win Rate: `{w_wr:.1f}%` ({w_wins}W / {w_losses}L)\n"
        f"💵 साप्ताहिक नेट PnL: `${w_pnl:+.2f}`\n"
        f"💎 वर्तमान कुल इक्विटी: `${eq:.2f}`\n"
        f"🏆 बेस्ट ट्रेड: `{best_t['q'][:28]}...` (`+${best_t['pnl_usd']:.2f}`)\n"
        f"⚠️ सबसे खराब ट्रेड: `{worst_t['q'][:28]}...` (`${worst_t['pnl_usd']:+.2f}`)\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

def build_journal_summary():
    if not trade_journal:
        return "📓 *TRADE JOURNAL*\nअभी तक कोई ट्रेड बंद नहीं हुआ है। जैसे ही पहला ट्रेड TP/SL पर बंद होगा, यहाँ पूरा रिकॉर्ड दिखेगा!"
    
    recent = trade_journal[-8:]
    lines = ["📓 *TRADE JOURNAL (आखिरी ट्रेड्स)*\n━━━━━━━━━━━━━━━━━━━━"]
    for idx, t in enumerate(reversed(recent), 1):
        icon = "✅" if t["pnl_usd"] >= 0 else "🔻"
        lines.append(
            f"{idx}. {icon} *{t['q'][:30]}...*\n"
            f"   Score: `{t['score']}` | Entry: `${t['entry']:.3f}` ➔ Exit: `${t['exit']:.3f}`\n"
            f"   PnL: `${t['pnl_usd']:+.2f}` (`{t['pnl_pct']:+.1f}%`) | ⏱ `{t['duration']}` | `{t['reason']}`"
        )
    return "\n".join(lines)

def check_tg():
    global last_update_id, daily_paused
    try:
        r = requests.get(
            tg_url("getUpdates"),
            params={"offset": last_update_id + 1, "timeout": 1},
            timeout=8
        )
        if r.status_code != 200:
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
                cfg["take_profit_pct"] = abs(float(text.split()))
                save_settings(cfg)
                send_tg(f"✅ *TP अपडेट:* `+{cfg['take_profit_pct']}%`")
            elif text.startswith(S + "sl "):
                cfg["stop_loss_pct"] = -abs(float(text.split()))
                save_settings(cfg)
                send_tg(f"🛑 *SL अपडेट:* `{cfg['stop_loss_pct']}%`")
            elif text.startswith(S + "risk "):
                cfg["risk_per_trade_pct"] = abs(float(text.split()))
                save_settings(cfg)
                send_tg(f"⚖️ *Auto-Equity साइज:* `{cfg['risk_per_trade_pct']}%`")
            elif text.startswith(S + "hold "):
                cfg["max_hold_hours"] = abs(float(text.split()))
                save_settings(cfg)
                send_tg(f"⏳ *Max Hold टाइम:* `{cfg['max_hold_hours']} घंटे`")
            elif text == S + "resume":
                daily_paused = False
                send_tg("▶️ *Daily Loss Pause हटाया गया:* बॉट फिर से नए ट्रेड्स लेगा।")
            elif text == S + "journal":
                send_tg(build_journal_summary())
            elif text == S + "weekly":
                send_tg(build_weekly_report())
            elif text in [S + "status", S + "start"]:
                tot = stats["wins"] + stats["losses"]
                wr = (stats["wins"] / tot * 100) if tot > 0 else 0.0
                eq = get_total_equity()
                next_trade = eq * (cfg["risk_per_trade_pct"] / 100.0)
                daily_chg = ((eq - day_start_equity) / day_start_equity) * 100 if day_start_equity > 0 else 0.0
                status_flag = "⏸ PAUSED (Daily Limit)" if daily_paused else "🟢 ACTIVE"
                send_tg(
                    f"📊 *POLYMARKET AI PRO STATUS* ({status_flag})\n"
                    f"💎 कुल इक्विटी: `${eq:.2f}` (कैश: `${balance:.2f}`)\n"
                    f"📆 आज का बदलाव: `{daily_chg:+.2f}%` (Limit: `-{cfg['daily_loss_limit_pct']}%`)\n"
                    f"💵 अगला ट्रेड ({cfg['risk_per_trade_pct']}%): `${next_trade:.2f}`\n"
                    f"📈 कुल PnL: `${stats['total_pnl']:+.2f}` | 🎯 Win Rate: `{wr:.1f}%` ({stats['wins']}W/{stats['losses']}L)\n"
                    f"⚙️ TP: `+{cfg['take_profit_pct']}%` | SL: `{cfg['stop_loss_pct']}%` | 🔒 BE: `+{cfg['breakeven_trigger']}%`\n"
                    f"📂 ओपन ट्रेड्स: `{len(positions)}/{cfg['max_open_trades']}`"
                )
    except Exception as e:
        print("Check TG Err:", e, flush=True)

def get_yes_no_prices(market):
    try:
        prices = json.loads(market.get("outcomePrices", "[]"))
        if len(prices) >= 2:
            return float(prices), float(prices)
    except Exception:
        pass
    return None, None

def fetch_single_market_status(m_id):
    try:
        url = poly_base_url() + S + str(m_id)
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            m = r.json()
            yp, _ = get_yes_no_prices(m)
            return yp, bool(m.get("closed", False))
    except Exception:
        pass
    return None, False

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
1
