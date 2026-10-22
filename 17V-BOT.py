#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quotex Signal Bot v32 — v26 Logic + API Storage
- 100% original v26 signal logic (unchanged)
- users.txt → API (afridi.bro.bd/vps/api.php)
- Yahoo + curl_cffi
"""

import subprocess
import sys
import importlib.util

if importlib.util.find_spec("curl_cffi") is None:
    print("[*] Installing curl_cffi...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "curl_cffi"])
        print("[+] curl_cffi installed")
    except subprocess.CalledProcessError as e:
        print(f"[!] Install fail: {e}")
        sys.exit(1)

import sys, os, json, time, math, random, threading, traceback
import urllib.parse, urllib.request, ssl
from datetime import datetime

def log(msg, level="info"):
    icon = {"info": "[*]", "ok": "[+]", "warn": "[!]", "err": "[x]"}.get(level, "[*]")
    print(f"{icon} {msg}", flush=True)

if sys.version_info < (3, 8):
    log("Python 3.8+ required", "err"); sys.exit(1)

# ============================================================
#   CONFIG
# ============================================================
BOT_TOKEN = "8733882861:AAFQe4yRuSOvlFq25BhiYvGIXIosg2KyX_4"
_OWNER = 6919881622

API_URL    = "https://afridi.bro.bd/vps/api.php"
API_SECRET = "afridi_bot_2024_xK9mP3nQ7vR2wL8z"
API_TIMEOUT = 15

SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
    "USDCHF", "GBPCAD", "GBPCHF", "CHFJPY",
    "EURAUD", "AUDCAD", "CADJPY", "GBPJPY", "EURGBP",
    "NZDUSD", "EURJPY", "AUDJPY", "EURNZD",
]
YF_MAP = {s: s + "=X" for s in SYMBOLS}

SCAN_INTERVAL     = 300
MIN_CONFIDENCE    = 80
COUNTDOWN_SECONDS = 5
DEDUP_SECONDS     = 300
CACHE_TTL         = 30
MAX_CACHE         = 30

AUTO_FLAG = "auto.flag"

TG = f"https://api.telegram.org/bot{BOT_TOKEN}"

SSL_CTX = ssl.create_default_context()

_CANDLE_CACHE    = {}
_LAST_SIGNAL_MEM = {}
_SIGNAL_STATS    = {"CALL": 0, "PUT": 0, "NO_TRADE": 0}
_USER_CACHE      = {}
_USER_CACHE_TTL  = 5

# ============================================================
#   HTTP — Yahoo via curl_cffi (v26 original)
# ============================================================
try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
    log("curl_cffi loaded — Yahoo TLS bypass active")
except ImportError:
    HAS_CFFI = False
    log("curl_cffi NOT found — run: pip install curl_cffi", "err")
    import urllib.request

def http_get(url, params=None, timeout=12):
    """Yahoo request via curl_cffi Chrome fingerprint"""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    if HAS_CFFI:
        try:
            r = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
            if r.status_code == 200:
                return r.json()
            return None
        except Exception:
            return None
    else:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            })
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            return None

def http_post(url, data, timeout=10):
    """Telegram API — JSON body"""
    try:
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None

def http_post_form(url, data, timeout=15):
    """PHP API — form-urlencoded"""
    try:
        body = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[!] API POST fail: {str(e)[:80]}", flush=True)
        return None

# ============================================================
#   API CLIENT
# ============================================================
def api_get(action, params=None):
    p = dict(params or {}); p["action"] = action; p["key"] = API_SECRET
    return http_get(API_URL, p, timeout=API_TIMEOUT)

def api_post(action, data):
    p = dict(data or {}); p["action"] = action; p["key"] = API_SECRET
    return http_post_form(API_URL, p, timeout=API_TIMEOUT)

def api_get_user(uid):
    now = time.time()
    if uid in _USER_CACHE:
        u, ts = _USER_CACHE[uid]
        if now - ts < _USER_CACHE_TTL:
            return u
    r = http_get(API_URL, {"id": str(uid)}, timeout=API_TIMEOUT)
    if not r or not r.get("ok") or not r.get("exists"):
        return None
    u = r["user"]
    _USER_CACHE[uid] = (u, now)
    return u

# ============================================================
#   USERS — API backed (v26 interface preserved)
# ============================================================
def _ensure_users_file():
    pass  # API-তে auto-create হয়

def load_users():
    r = api_get("list")
    if not r or not r.get("ok"):
        return {}
    out = {}
    for u in r.get("users", []):
        out[str(u["id"])] = u
    return out

def save_users(users):
    pass  # API auto-save করে

def _is_owner(uid):
    try: return int(uid) == int(_OWNER)
    except Exception: return False

def get_role(uid):
    if _is_owner(uid): return "owner"
    u = api_get_user(str(uid))
    if not u: return "normal"
    if not u.get("active", True): return "banned"
    r = u.get("role", "normal")
    if r == "vip": return "vip"
    if r == "pending": return "pending"
    return "normal"

def register(uid, name="", username="", chat_id=None):
    api_post("register", {
        "id": str(uid), "name": name, "username": username,
        "chat_id": str(chat_id or uid)
    })
    _USER_CACHE.pop(str(uid), None)

def set_pending(uid, name="", username=""):
    api_post("register", {
        "id": str(uid), "name": name, "username": username,
        "chat_id": str(uid)
    })
    api_post("set_role", {
        "id": str(uid), "role": "pending",
        "name": name, "username": username
    })
    _USER_CACHE.pop(str(uid), None)

def approve(uid, name="", username="", chat_id=None):
    info = api_get_user(str(uid)) or {}
    api_post("set_role", {
        "id": str(uid), "role": "vip",
        "name": name or info.get("name", ""),
        "username": username or info.get("username", ""),
        "chat_id": str(chat_id or info.get("chat_id") or uid)
    })
    api_post("set_active", {"id": str(uid), "active": "1"})
    _USER_CACHE.pop(str(uid), None)

def revoke(uid):
    api_post("set_role", {"id": str(uid), "role": "normal"})
    _USER_CACHE.pop(str(uid), None)

def ban(uid, state=True):
    api_post("set_active", {"id": str(uid), "active": "0" if state else "1"})
    _USER_CACHE.pop(str(uid), None)

def inc_signals(uid):
    api_post("inc_signals", {"id": str(uid)})

def all_users():
    return load_users()

def approved_users():
    return [u for u in load_users().values()
            if u.get("role") == "vip" and u.get("active", True)]

def pending_users():
    return {k: v for k, v in load_users().items()
            if v.get("role") == "pending" and v.get("active", True)}

# ============================================================
#   YAHOO DATA FETCH (v26 original)
# ============================================================
def fetch_yahoo(symbol, interval="1m", limit=100, debug=False):
    yf = YF_MAP.get(symbol)
    if not yf:
        return None

    range_map = {"1m": ["1d", "5d"], "5m": ["5d", "1mo"], "15m": ["5d", "1mo"]}
    ranges = range_map.get(interval, ["1d", "5d"])
    last_err = "unknown"

    for rng in ranges:
        for attempt in range(2):
            for host in ["query1", "query2"]:
                url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{yf}"
                data = http_get(url, {"interval": interval, "range": rng}, timeout=10)
                if not data:
                    last_err = f"HTTP fail ({host})"
                    continue
                try:
                    r0 = data["chart"]["result"][0]
                    ts = r0.get("timestamp", [])
                    q = r0["indicators"]["quote"][0]
                    o, h = q.get("open", []), q.get("high", [])
                    l, c = q.get("low", []), q.get("close", [])
                    out = []
                    for i in range(len(ts)):
                        if i >= len(o) or None in (o[i], h[i], l[i], c[i]):
                            continue
                        out.append({"t": ts[i], "open": float(o[i]),
                                    "high": float(h[i]), "low": float(l[i]),
                                    "close": float(c[i])})
                    if len(out) >= 25:
                        return out[-limit:]
                    else:
                        last_err = f"only {len(out)} candles"
                except Exception as e:
                    last_err = f"parse: {str(e)[:30]}"
            time.sleep(0.5)
    if debug:
        print(f"[YF] {symbol} FAIL: {last_err}")
    return None

def fetch_live_price(symbol):
    yf = YF_MAP.get(symbol)
    if not yf:
        return None
    data = http_get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{yf}",
        {"interval": "1m", "range": "1d"}, timeout=6)
    if not data:
        return None
    try:
        price = data["chart"]["result"][0].get("meta", {}).get("regularMarketPrice")
        return float(price) if price else None
    except Exception:
        return None

def fetch_candles(symbol, interval="1m", limit=100, debug=False):
    key = f"{symbol}_{interval}_{limit}"
    now = time.time()
    if key in _CANDLE_CACHE:
        data, ts = _CANDLE_CACHE[key]
        if now - ts < CACHE_TTL and data is not None:
            return data

    result = fetch_yahoo(symbol, interval, limit, debug=debug)

    if result:
        _CANDLE_CACHE[key] = (result, now)
        if len(_CANDLE_CACHE) > MAX_CACHE:
            oldest = sorted(_CANDLE_CACHE.items(), key=lambda x: x[1][1])[:MAX_CACHE//2]
            for k, _ in oldest:
                _CANDLE_CACHE.pop(k, None)
    return result

# ============================================================
#   MATH (v26 original)
# ============================================================
def mean(a): return sum(a)/len(a) if a else 0.0
def std(a):
    if len(a) < 2: return 0.0
    m = mean(a)
    return math.sqrt(sum((x-m)**2 for x in a)/len(a))

def rsi(px, p=14):
    if len(px) < p+1: return 50.0
    g = l = 0.0
    for i in range(1, p+1):
        d = px[i]-px[i-1]
        if d > 0: g += d
        else: l += -d
    if l == 0: return 100.0
    return 100 - (100/(1+((g/p)/(l/p))))

def rsi_series(px, p=14):
    n = min(len(px), 50); out = []
    for i in range(p+1, n+1): out.append(rsi(px[:i], p))
    return out

def ema(px, p):
    if not px or len(px) < p: return 0.0
    k = 2/(p+1); e = px[0]
    for x in px[1:]: e = x*k + e*(1-k)
    return e

def atr(candles, p=14):
    if len(candles) < p+1: return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return mean(trs[-p:])

def momentum(prices, p=5):
    if len(prices) < p+1: return 0.0
    return (prices[-1]-prices[-p-1])/prices[-p-1]*100

def stoch(candles, p=14):
    if len(candles) < p: return 50.0
    recent = candles[-p:]
    hi = max(c["high"] for c in recent)
    lo = min(c["low"] for c in recent)
    if hi == lo: return 50.0
    return (candles[-1]["close"]-lo)/(hi-lo)*100

def adx(candles, p=14):
    if len(candles) < p*2: return 0.0
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(candles)):
        up = candles[i]["high"] - candles[i-1]["high"]
        dn = candles[i-1]["low"] - candles[i]["low"]
        plus_dm.append(up if up > dn and up > 0 else 0)
        minus_dm.append(dn if dn > up and dn > 0 else 0)
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    if not trs: return 0.0
    atr_v = mean(trs[-p:])
    if atr_v == 0: return 0.0
    pdi = 100*mean(plus_dm[-p:])/atr_v
    mdi = 100*mean(minus_dm[-p:])/atr_v
    if pdi+mdi == 0: return 0.0
    return 100*abs(pdi-mdi)/(pdi+mdi)

def macd_hist(closes):
    if len(closes) < 35: return 0.0, 0.0
    m_now = ema(closes, 12) - ema(closes, 26)
    m_prev = ema(closes[:-1], 12) - ema(closes[:-1], 26)
    return m_now, m_now - m_prev

# ============================================================
#   PATTERNS (v26 original)
# ============================================================
def bull_engulf(a, b):
    return (a["close"]>a["open"] and b["close"]<b["open"]
            and a["close"]>b["open"] and a["open"]<b["close"])
def bear_engulf(a, b):
    return (a["close"]<a["open"] and b["close"]>b["open"]
            and a["close"]<b["open"] and a["open"]>b["close"])
def bull_pinbar(c):
    rng = c["high"]-c["low"]
    if rng == 0: return False
    body = abs(c["close"]-c["open"])
    lower = min(c["open"],c["close"])-c["low"]
    return lower > body*2 and body/rng < 0.35
def bear_pinbar(c):
    rng = c["high"]-c["low"]
    if rng == 0: return False
    body = abs(c["close"]-c["open"])
    upper = c["high"]-max(c["open"],c["close"])
    return upper > body*2 and body/rng < 0.35
def hammer(c):
    rng = c["high"]-c["low"]
    if rng == 0: return False
    body = abs(c["close"]-c["open"])
    lower = min(c["open"],c["close"])-c["low"]
    upper = c["high"]-max(c["open"],c["close"])
    return lower > rng*0.6 and upper < rng*0.15 and body > 0
def shooting_star(c):
    rng = c["high"]-c["low"]
    if rng == 0: return False
    body = abs(c["close"]-c["open"])
    lower = min(c["open"],c["close"])-c["low"]
    upper = c["high"]-max(c["open"],c["close"])
    return upper > rng*0.6 and lower < rng*0.15 and body > 0

# ============================================================
#   FILTERS (v26 original)
# ============================================================
def fib_levels(candles):
    recent = candles[-30:]
    hi = max(c["high"] for c in recent)
    lo = min(c["low"] for c in recent)
    if hi == lo: return {}
    return {"0.618": hi-(hi-lo)*0.618, "0.382": hi-(hi-lo)*0.382}

def pivot_sr(candles, lookback=40):
    recent = candles[-min(len(candles), lookback):]
    highs = [c["high"] for c in recent]; lows = [c["low"] for c in recent]
    return {"res": max(highs) if highs else 0, "sup": min(lows) if lows else 0}

def rsi_divergence(candles, p=14):
    if len(candles) < 35: return 0
    closes = [c["close"] for c in candles]
    rsi_vals = rsi_series(closes, p)
    if len(rsi_vals) < 12: return 0
    rp = closes[-12:]; rr = rsi_vals[-12:]
    px_up = rp[-1] > rp[0]; rsi_up = rr[-1] > rr[0]
    if px_up and not rsi_up: return -1
    if not px_up and rsi_up: return 1
    return 0

def vwap(candles):
    if not candles: return 0
    tv = vol = 0.0
    for c in candles:
        typ = (c["high"]+c["low"]+c["close"])/3
        v = max(1.0, c["high"]-c["low"])
        tv += typ*v; vol += v
    return tv/vol if vol else 0

def ichimoku_signal(candles):
    if len(candles) < 52: return 0
    closes = [c["close"] for c in candles]
    tenkan = (max(closes[-9:]) + min(closes[-9:]))/2
    kijun = (max(closes[-26:]) + min(closes[-26:]))/2
    if tenkan > kijun: return 1
    if tenkan < kijun: return -1
    return 0

def ema_ribbon(candles):
    closes = [c["close"] for c in candles]
    if len(closes) < 55: return 0
    e8,e13,e21,e34,e55 = [ema(closes,p) for p in (8,13,21,34,55)]
    if e8 > e13 > e21 > e34 > e55: return 1
    if e8 < e13 < e21 < e34 < e55: return -1
    return 0

def breakout_signal(candles, lookback=20):
    if len(candles) < lookback+2: return 0
    recent = candles[-lookback:-1]
    hi = max(c["high"] for c in recent); lo = min(c["low"] for c in recent)
    last = candles[-1]["close"]
    if last > hi: return 1
    if last < lo: return -1
    return 0

def trend_direction(candles):
    closes = [c["close"] for c in candles]
    if len(closes) < 60: return "FLAT"
    e50 = ema(closes, 50); e200 = ema(closes, min(200, len(closes)))
    px = closes[-1]
    if e50 > e200 and px > e50: return "UP"
    if e50 < e200 and px < e50: return "DOWN"
    return "FLAT"

# ============================================================
#   SINGLE-TF ANALYSIS (v26 original)
# ============================================================
def analyze_tf(candles, weight=1.0):
    if not candles or len(candles) < 40:
        return {"call": 0, "put": 0, "sig": [], "det": {}, "valid": False}

    closes = [c["close"] for c in candles]
    last, prev = candles[-1], candles[-2]
    call = put = 0.0
    sig, det = [], {}

    ax = adx(candles, 14)
    trend = trend_direction(candles)
    det["adx"] = round(ax, 1); det["trend"] = trend

    if ax < 18:
        return {"call": 0, "put": 0, "sig": ["Sideway"], "det": det, "valid": False}

    r = rsi(closes, 14); det["rsi"] = round(r, 1)
    if trend == "UP":
        if r < 40: call += 20; sig.append(f"RSI {r:.0f} pullback")
        elif r > 78: put += 10; sig.append(f"RSI {r:.0f} OB")
    elif trend == "DOWN":
        if r > 60: put += 20; sig.append(f"RSI {r:.0f} rally")
        elif r < 22: call += 10; sig.append(f"RSI {r:.0f} OS")
    else:
        if r < 30: call += 15
        elif r > 70: put += 15

    st = stoch(candles, 14); det["stoch"] = round(st, 1)
    if trend == "FLAT":
        if st < 20: call += 10; sig.append(f"Stoch {st:.0f} OS")
        elif st > 80: put += 10; sig.append(f"Stoch {st:.0f} OB")

    macd_now, macd_hist_val = macd_hist(closes)
    det["macd"] = round(macd_now, 5)
    if macd_hist_val > 0 and macd_now > 0: call += 15; sig.append("MACD bull")
    elif macd_hist_val < 0 and macd_now < 0: put += 15; sig.append("MACD bear")

    r20 = closes[-20:]; ma, sd = mean(r20), std(r20)
    if sd > 0:
        if closes[-1] <= ma-2.2*sd: call += 12; sig.append("Extreme lower BB")
        elif closes[-1] >= ma+2.2*sd: put += 12; sig.append("Extreme upper BB")

    e9, e21 = ema(closes,9), ema(closes,21)
    e9p, e21p = ema(closes[:-1],9), ema(closes[:-1],21)
    if e9 > e21 and e9p <= e21p: call += 18; sig.append("EMA bull X")
    elif e9 < e21 and e9p >= e21p: put += 18; sig.append("EMA bear X")
    elif e9 > e21: call += 5
    elif e9 < e21: put += 5

    rib = ema_ribbon(candles); det["ribbon"] = rib
    if rib == 1: call += 20; sig.append("Ribbon bull")
    elif rib == -1: put += 20; sig.append("Ribbon bear")

    if bull_engulf(last, prev): call += 15; sig.append("Bull engulf")
    if bear_engulf(last, prev): put += 15; sig.append("Bear engulf")
    if hammer(last): call += 12; sig.append("Hammer")
    if shooting_star(last): put += 12; sig.append("Shooting star")

    mom = momentum(closes, 5); det["mom"] = round(mom, 3)
    if mom > 0.20 and trend == "UP": call += 12; sig.append(f"Mom +{mom:.2f}%")
    elif mom < -0.20 and trend == "DOWN": put += 12; sig.append(f"Mom {mom:.2f}%")

    ichi = ichimoku_signal(candles); det["ichi"] = ichi
    if ichi == 1 and trend == "UP": call += 15; sig.append("Ichimoku bull")
    elif ichi == -1 and trend == "DOWN": put += 15; sig.append("Ichimoku bear")

    div = rsi_divergence(candles, 14); det["div"] = div
    if div == 1: call += 22; sig.append("Bull div")
    elif div == -1: put += 22; sig.append("Bear div")

    vw = vwap(candles[-30:]); det["vwap"] = round(vw, 5)
    if vw > 0 and closes[-1] > vw*1.0005: call += 10
    elif vw > 0 and closes[-1] < vw*0.9995: put += 10

    bo = breakout_signal(candles, 20); det["breakout"] = bo
    if bo == 1: call += 15; sig.append("Breakout UP")
    elif bo == -1: put += 15; sig.append("Breakout DN")

    if trend == "UP": call += 10
    elif trend == "DOWN": put += 10

    fib = fib_levels(candles); sr = pivot_sr(candles, 40)
    px = closes[-1]
    if fib and "0.618" in fib:
        if abs(px-fib["0.618"]) < fib["0.618"]*0.0006: call += 10; sig.append("Fib bounce")
    if sr:
        if px <= sr["sup"]*1.0005: call += 8; sig.append("Near support")
        elif px >= sr["res"]*0.9995: put += 8; sig.append("Near resistance")

    return {"call": int(call*weight), "put": int(put*weight),
            "sig": sig, "det": det, "valid": True}

# ============================================================
#   MULTI-TF ANALYSIS (v26 original + safety wrapper)
# ============================================================
def analyze(symbol):
    try:
        c1 = fetch_candles(symbol, "1m", 100)
        c5 = fetch_candles(symbol, "5m", 100)
        c15 = fetch_candles(symbol, "15m", 100)

        if not c1 or len(c1) < 40:
            return None

        live = fetch_live_price(symbol)
        price = live if live else c1[-1]["close"]

        r1 = analyze_tf(c1, 1.0)
        r5 = analyze_tf(c5, 1.3) if c5 and len(c5) >= 40 else None
        r15 = analyze_tf(c15, 1.6) if c15 and len(c15) >= 40 else None

        total_call = r1["call"]; total_put = r1["put"]
        tf_votes = []; all_sigs = list(r1["sig"])

        if r1["valid"]:
            if r1["call"] > r1["put"]+5: tf_votes.append(("CALL","1m"))
            elif r1["put"] > r1["call"]+5: tf_votes.append(("PUT","1m"))
        if r5:
            total_call += r5["call"]; total_put += r5["put"]; all_sigs.extend(r5["sig"])
            if r5["valid"]:
                if r5["call"] > r5["put"]+5: tf_votes.append(("CALL","5m"))
                elif r5["put"] > r5["call"]+5: tf_votes.append(("PUT","5m"))
        if r15:
            total_call += r15["call"]; total_put += r15["put"]; all_sigs.extend(r15["sig"])
            if r15["valid"]:
                if r15["call"] > r15["put"]+5: tf_votes.append(("CALL","15m"))
                elif r15["put"] > r15["call"]+5: tf_votes.append(("PUT","15m"))

        call_votes = sum(1 for d,_ in tf_votes if d=="CALL")
        put_votes = sum(1 for d,_ in tf_votes if d=="PUT")

        if call_votes >= 2 and call_votes > put_votes: direction = "CALL"
        elif put_votes >= 2 and put_votes > call_votes: direction = "PUT"
        elif total_call > total_put+15 and call_votes >= put_votes: direction = "CALL"
        elif total_put > total_call+15 and put_votes >= call_votes: direction = "PUT"
        else:
            _SIGNAL_STATS["NO_TRADE"] = _SIGNAL_STATS.get("NO_TRADE",0)+1
            return {"direction":"NO_TRADE","confidence":0,
                    "signals":["Multi-TF disagree","Sideway or weak trend"],
                    "price":price,"duration":"—","dur_reason":"No clear setup",
                    "details":{"1m":r1["det"],"5m":r5["det"] if r5 else {},"15m":r15["det"] if r15 else {}},
                    "atr":atr(c1,14),"symbol":symbol,"quality":"—",
                    "tf_votes":f"C{call_votes}/P{put_votes}"}

        total = total_call+total_put
        if total == 0: return None
        winner = total_call if direction=="CALL" else total_put

        ratio_factor = winner/total
        tf_factor = max(call_votes,put_votes)/max(len(tf_votes),1)
        max_adx = max(r1["det"].get("adx",0),
                      r5["det"].get("adx",0) if r5 else 0,
                      r15["det"].get("adx",0) if r15 else 0)
        adx_factor = min(max_adx/50.0,1.0)
        div_found = any("div" in s.lower() for s in all_sigs)
        div_factor = 1.0 if div_found else 0.5

        score = (ratio_factor*0.40 + tf_factor*0.30 + adx_factor*0.20 + div_factor*0.10)*100
        conf = int(60+(score-50)*0.7) if score > 50 else 55
        conf = max(55,min(95,conf))

        if conf >= 85: quality = "★★★ HIGH"
        elif conf >= 75: quality = "★★★ GOOD"
        elif conf >= 65: quality = "★★ FAIR"
        else: quality = "★ WEAK"

        atr_val = atr(c1,14)
        if conf >= 85 and max_adx >= 28: dur,dur_r = "5 minutes","Strong trend"
        elif conf >= 78: dur,dur_r = "3 minutes","Good setup"
        elif conf >= 70: dur,dur_r = "2 minutes","Balanced"
        else: dur,dur_r = "1 minute","Quick scalp"

        sig_list = list(dict.fromkeys(all_sigs))[:10]
        _SIGNAL_STATS[direction] = _SIGNAL_STATS.get(direction,0)+1

        return {"direction":direction,"confidence":conf,"signals":sig_list,
                "price":price,"duration":dur,"dur_reason":dur_r,
                "details":{"1m":r1["det"],"5m":r5["det"] if r5 else {},"15m":r15["det"] if r15 else {}},
                "atr":atr_val,"symbol":symbol,"quality":quality,
                "tf_votes":f"C{call_votes}/P{put_votes}"}
    except Exception as e:
        print(f"[!] analyze({symbol}) crash: {e}", flush=True)
        traceback.print_exc()
        return None

def scan_auto():
    pool = SYMBOLS.copy(); random.shuffle(pool)
    best = None
    for sym in pool[:10]:
        try:
            r = analyze(sym)
            if not r or r["direction"]=="NO_TRADE": continue
            if r["confidence"] < MIN_CONFIDENCE: continue
            if is_duplicate(r): continue
            if best is None or r["confidence"] > best["confidence"]: best = r
        except Exception: continue
    if best: mark_signal(best)
    return best

def is_duplicate(sig):
    prev = _LAST_SIGNAL_MEM.get(sig["symbol"],{})
    if prev.get("direction")==sig["direction"]:
        if time.time()-prev.get("time",0) < DEDUP_SECONDS: return True
    return False

def mark_signal(sig):
    _LAST_SIGNAL_MEM[sig["symbol"]] = {"direction":sig["direction"],"time":time.time()}

# ============================================================
#   REPORTS (v26 logic but API data)
# ============================================================
def build_stats_report():
    r = api_get("stats")
    s = r.get("stats", {}) if r and r.get("ok") else {}
    return ("📊 <b>BOT STATS</b>\n━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Total users : {s.get('total',0)}\n⭐ VIP users   : {s.get('vip',0)}\n"
            f"🚫 Banned      : {s.get('banned',0)}\n⏳ Pending     : {s.get('pending',0)}\n"
            f"👤 Normal      : {s.get('normal',0)}\n👑 Owner       : {s.get('owner',0)}\n"
            f"🏆 Wins/Losses : {s.get('wins',0)}/{s.get('losses',0)}\n"
            f"📈 Win rate    : <b>{s.get('winrate',0)}%</b>\n"
            f"📞 CALL signals: {_SIGNAL_STATS.get('CALL',0)}\n"
            f"📉 PUT  signals: {_SIGNAL_STATS.get('PUT',0)}\n"
            f"🚫 No-trade    : {_SIGNAL_STATS.get('NO_TRADE',0)}\n"
            "━━━━━━━━━━━━━━━━━━━")

def _fmt_full_member_list():
    users = all_users()
    if not users:
        return "<b>📋 MEMBER LIST</b>\n(empty)"
    order = {"owner":0,"vip":1,"pending":2,"normal":3}
    items = sorted(users.values(), key=lambda u: 9 if not u.get("active",True)
                   else order.get(u.get("role","normal"),5))

    W_NUM, W_USER, W_CHAT, W_NAME, W_ROLE = 2, 16, 12, 12, 9
    sep_top = "┌" + "─"*(W_NUM+2) + "┬" + "─"*(W_USER+2) + "┬" \
              + "─"*(W_CHAT+2) + "┬" + "─"*(W_NAME+2) + "┬" + "─"*(W_ROLE+2) + "┐"
    sep_mid = "├" + "─"*(W_NUM+2) + "┼" + "─"*(W_USER+2) + "┼" \
              + "─"*(W_CHAT+2) + "┼" + "─"*(W_NAME+2) + "┼" + "─"*(W_ROLE+2) + "┤"
    sep_bot = "└" + "─"*(W_NUM+2) + "┴" + "─"*(W_USER+2) + "┴" \
              + "─"*(W_CHAT+2) + "┴" + "─"*(W_NAME+2) + "┴" + "─"*(W_ROLE+2) + "┘"
    def row(cols):
        cells = []
        for val, w in zip(cols, [W_NUM, W_USER, W_CHAT, W_NAME, W_ROLE]):
            s = str(val)
            if len(s) > w: s = s[:w-1] + "…"
            cells.append(" " + s.ljust(w) + " ")
        return "│" + "│".join(cells) + "│"

    lines = ["<b>📋 FULL MEMBER LIST</b>", "", sep_top,
             row(["#", "@USERNAME", "CHAT ID", "NAME", "ROLE"]), sep_mid]
    for i, u in enumerate(items, 1):
        cid = u.get("chat_id") or u.get("id", "?")
        un = u.get("username") or ""
        uf = f"@{un}" if un else "—"
        role = u.get("role", "normal"); act = u.get("active", True)
        if not act: tag = "BANNED"
        elif role == "owner": tag = "OWNER"
        elif role == "vip": tag = "VIP"
        elif role == "pending": tag = "PENDING"
        else: tag = "NORMAL"
        lines.append(row([i, uf, cid, u.get("name") or "—", tag]))
    lines.append(sep_bot)
    lines.append("")
    lines.append(f"Total: <b>{len(items)}</b>")
    return "\n".join(lines)

def _fmt_filtered(title, users_dict):
    if not users_dict: return f"<b>{title}</b>\n(empty)"
    W_NUM, W_USER, W_CHAT, W_NAME, W_ROLE = 2, 16, 12, 12, 9
    sep_top = "┌" + "─"*(W_NUM+2) + "┬" + "─"*(W_USER+2) + "┬" \
              + "─"*(W_CHAT+2) + "┬" + "─"*(W_NAME+2) + "┬" + "─"*(W_ROLE+2) + "┐"
    sep_mid = "├" + "─"*(W_NUM+2) + "┼" + "─"*(W_USER+2) + "┼" \
              + "─"*(W_CHAT+2) + "┼" + "─"*(W_NAME+2) + "┼" + "─"*(W_ROLE+2) + "┤"
    sep_bot = "└" + "─"*(W_NUM+2) + "┴" + "─"*(W_USER+2) + "┴" \
              + "─"*(W_CHAT+2) + "┴" + "─"*(W_NAME+2) + "┴" + "─"*(W_ROLE+2) + "┘"
    def row(cols):
        cells = []
        for val, w in zip(cols, [W_NUM, W_USER, W_CHAT, W_NAME, W_ROLE]):
            s = str(val)
            if len(s) > w: s = s[:w-1] + "…"
            cells.append(" " + s.ljust(w) + " ")
        return "│" + "│".join(cells) + "│"
    lines = [f"<b>📋 {title}</b>", "", sep_top,
             row(["#", "@USERNAME", "CHAT ID", "NAME", "ROLE"]), sep_mid]
    for i, (k, u) in enumerate(users_dict.items(), 1):
        cid = u.get("chat_id") or k
        un = u.get("username") or ""
        uf = f"@{un}" if un else "—"
        lines.append(row([i, uf, cid, u.get("name") or "—", (u.get("role","?") or "?").upper()[:9]]))
    lines.append(sep_bot)
    lines.append("")
    lines.append(f"Total: <b>{len(users_dict)}</b>")
    return "\n".join(lines)

# ============================================================
#   TELEGRAM
# ============================================================
def tg_send(chat_id,text,reply_markup=None):
    p = {"chat_id":chat_id,"text":text,"parse_mode":"HTML","disable_web_page_preview":True}
    if reply_markup: p["reply_markup"]=reply_markup
    return http_post(f"{TG}/sendMessage",p,timeout=10)

def tg_edit(chat_id,message_id,text,reply_markup=None):
    p = {"chat_id":chat_id,"message_id":message_id,"text":text,
         "parse_mode":"HTML","disable_web_page_preview":True}
    if reply_markup: p["reply_markup"]=reply_markup
    return http_post(f"{TG}/editMessageText",p,timeout=10)

def tg_answer(cb_id,text=""):
    return http_post(f"{TG}/answerCallbackQuery",{"callback_query_id":cb_id,"text":text},timeout=5)

def tg_poll(offset):
    r = http_get(f"{TG}/getUpdates",{"timeout":10,"offset":offset or 0},timeout=15)
    return r.get("result",[]) if r else []

# ============================================================
#   UI
# ============================================================
def build_market_keyboard():
    buttons, row = [], []
    for sym in SYMBOLS:
        row.append({"text":f"📈 {sym}","callback_data":f"mkt:{sym}"})
        if len(row)==2: buttons.append(row); row=[]
    if row: buttons.append(row)
    buttons.append([{"text":"🎲 Random","callback_data":"mkt:RANDOM"}])
    return {"inline_keyboard":buttons}

def build_owner_pending_keyboard(uid):
    return {"inline_keyboard":[[
        {"text":"✅ Approve","callback_data":f"apr:{uid}"},
        {"text":"❌ Reject","callback_data":f"rej:{uid}"}]]}

def build_start_text(name,role):
    txt = ("<b>🤖 Quotex Signal Bot v32</b>\n━━━━━━━━━━━━━━━━━━━\n"
           f"👤 {name or 'User'}\n📊 {len(SYMBOLS)} markets\n"
           f"🧠 Multi-TF + 15+ strategies\n"
           f"🎯 {MIN_CONFIDENCE}%+ accuracy\n"
           "━━━━━━━━━━━━━━━━━━━\n\n👇 <b>Select a market:</b>\n")
    txt += "\n/start /markets /signal /test /myid /help"
    if role in ("vip","owner"): txt += "\n/autostart /stop"
    if role == "owner": txt += "\n/stats /users /approved /banned /pending /add /remove /ban /unban /broadcast"
    return txt

def build_countdown_text(symbol,seconds):
    if seconds > 0:
        bt=5; f=bt-seconds; bar="█"*f+"░"*(bt-f)
        return (f"⏳ <b>Analyzing {symbol}...</b>\n━━━━━━━━━━━━━━━━━━━\n"
                f"<code>{bar}</code> <b>{seconds}s</b>\n"
                "━━━━━━━━━━━━━━━━━━━\n🧠 Multi-TF + Trend + Div")
    return f"✅ <b>{symbol}</b> — Signal ready!"

def fmt_signal(sig,uid):
    role = get_role(uid)
    if sig["direction"]=="NO_TRADE":
        return ("⚠️ <b>NO TRADE</b>\n━━━━━━━━━━━━━━━━━━━\n"
                f"🎯 {sig['symbol']}\n💵 {sig['price']:.5f}\n"
                f"📊 Votes: {sig.get('tf_votes','—')}\n"
                "━━━━━━━━━━━━━━━━━━━\n🚫 Wait for clearer setup")
    icon = "🟢" if sig["direction"]=="CALL" else "🔴"
    d = "BUY ⬆️" if sig["direction"]=="CALL" else "SELL ⬇️"
    at = "\n".join(f"  • {s}" for s in sig["signals"][:10])
    txt = (f"🎯 {sig['symbol']}\n📊 {d}\n⏱ {sig['duration']}\n"
           f"🎚 {sig['confidence']}%\n⭐ {sig.get('quality','—')}\n"
           f"🗳️ {sig.get('tf_votes','—')}\n💵 {sig['price']:.5f}\n\n📝 {at}")
    extra = ""
    if role in ("vip","owner"):
        d1=sig.get("details",{}).get("1m",{}); d5=sig.get("details",{}).get("5m",{})
        d15=sig.get("details",{}).get("15m",{})
        extra=(f"\n<b>DEEP:</b>\n"
               f"1m: RSI={d1.get('rsi','-')} ADX={d1.get('adx','-')} T={d1.get('trend','-')}\n"
               f"5m: RSI={d5.get('rsi','-')} ADX={d5.get('adx','-')} T={d5.get('trend','-')}\n"
               f"15m: RSI={d15.get('rsi','-')} ADX={d15.get('adx','-')} T={d15.get('trend','-')}\n")
    return (f"{icon} <b>QUOTEX SIGNAL</b>\n━━━━━━━━━━━━━━━━━━━\n"
            f"<code>{txt}</code>\n{extra}"
            f"━━━━━━━━━━━━━━━━━━━\n💡 <i>{sig.get('dur_reason','')}</i>\n⚠️ Risk 1-2% only")

def send_signal_with_countdown(chat_id,sig):
    symbol = sig.get("symbol","?")
    resp = tg_send(chat_id,build_countdown_text(symbol,COUNTDOWN_SECONDS))
    if not resp or not resp.get("ok"):
        tg_send(chat_id,fmt_signal(sig,chat_id)); inc_signals(chat_id); return
    msg_id = resp["result"]["message_id"]
    for sec in range(COUNTDOWN_SECONDS-1,0,-1):
        time.sleep(1)
        try: tg_edit(chat_id,msg_id,build_countdown_text(symbol,sec))
        except Exception: pass
    time.sleep(0.4)
    try: tg_edit(chat_id,msg_id,fmt_signal(sig,chat_id))
    except Exception: tg_send(chat_id,fmt_signal(sig,chat_id))
    inc_signals(chat_id)

def notify_owner_new_user(uid,name="",username="",chat_id=None):
    try:
        chat_id = chat_id or uid
        uname = f"@{username}" if username else "—"
        txt = ("📊 <b>NEW USER REQUEST</b>\n━━━━━━━━━━━━━━━━━━━\n"
               f"🆔 <code>{chat_id}</code>\n"
               f"📛 {uname}\n"
               f"👤 {name or '—'}\n"
               f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        tg_send(_OWNER, txt, reply_markup=build_owner_pending_keyboard(uid))
    except Exception: pass

# ============================================================
#   CALLBACK (v26 original)
# ============================================================
def handle_callback(cb):
    try:
        chat,user,msg_id,cb_id = cb["message"]["chat"]["id"],cb["from"]["id"],cb["message"]["message_id"],cb["id"]
        data = cb.get("data","")
        name = cb["from"].get("first_name","")
        username = cb["from"].get("username","") or ""
        register(user,name,username,chat)
        role = get_role(user)

        if data.startswith("apr:") or data.startswith("rej:"):
            if not _is_owner(user): tg_answer(cb_id,"Only owner"); return
            try: target = int(data.split(":",1)[1])
            except Exception: tg_answer(cb_id,"Invalid"); return

            info = api_get_user(str(target)) or {}
            tname = info.get("name","")
            tuname = info.get("username","")
            tchat = info.get("chat_id", target)

            if data.startswith("apr:"):
                approve(target, tname, tuname, tchat)
                tg_answer(cb_id,"Approved")
                try: tg_send(target,"🎉 <b>You're approved!</b>\nTry /start now.")
                except Exception: pass
                try:
                    tg_edit(chat, msg_id,
                        f"✅ <b>APPROVED</b>\n\n"
                        f"🆔 <code>{target}</code>\n"
                        f"📛 @{tuname or '—'}\n"
                        f"👤 {tname or '—'}")
                except Exception: pass
            else:
                ban(target,True); tg_answer(cb_id,"Rejected")
                try: tg_send(target,"❌ Access denied by admin.")
                except Exception: pass
                try:
                    tg_edit(chat, msg_id,
                        f"❌ <b>REJECTED</b>\n\n"
                        f"🆔 <code>{target}</code>\n"
                        f"📛 @{tuname or '—'}\n"
                        f"👤 {tname or '—'}")
                except Exception: pass
            return

        if role == "banned": tg_answer(cb_id,"Access denied"); return
        if role in ("normal","pending") and not _is_owner(user):
            tg_answer(cb_id,"⏳ Pending approval"); return
        if not data.startswith("mkt:"): tg_answer(cb_id,""); return

        target = data.split(":",1)[1]
        if target == "RANDOM":
            target = random.choice(SYMBOLS); tg_answer(cb_id,f"🎲 {target}")
        else: tg_answer(cb_id,f"Analyzing {target}...")

        tg_edit(chat,msg_id,f"⏳ <b>Analyzing {target}...</b>\n━━━━━━━━━━━━━━━━━━━\n🧠 Multi-TF...\n🔄 Yahoo (curl_cffi)")
        sig = analyze(target)
        if not sig:
            _CANDLE_CACHE.pop(f"{target}_1m_100",None); time.sleep(1); sig = analyze(target)
        if not sig:
            tg_edit(chat,msg_id,f"⚠️ <b>{target}</b> — data unavailable\n"
                "━━━━━━━━━━━━━━━━━━━\n• Yahoo blocked/timeout\n• Market closed\n\n🔄 Try another or /signal")
            tg_send(chat,"👇 Pick another:",reply_markup=build_market_keyboard()); return
        if sig["direction"]=="NO_TRADE":
            try: tg_edit(chat,msg_id,fmt_signal(sig,chat))
            except Exception: tg_send(chat,fmt_signal(sig,chat))
            tg_send(chat,"👇 Pick another:",reply_markup=build_market_keyboard()); return

        mark_signal(sig)
        for sec in range(COUNTDOWN_SECONDS-1,0,-1):
            try: tg_edit(chat,msg_id,build_countdown_text(target,sec))
            except Exception: pass
            time.sleep(1)
        time.sleep(0.4)
        try: tg_edit(chat,msg_id,fmt_signal(sig,chat))
        except Exception: tg_send(chat,fmt_signal(sig,chat))
        inc_signals(user)
        tg_send(chat,"🔄 <b>Pick another market:</b>",reply_markup=build_market_keyboard())
    except Exception as e:
        print(f"[!] Callback: {e}",flush=True)
        traceback.print_exc()

# ============================================================
#   HANDLE (v26 original)
# ============================================================
def handle(msg):
    try:
        chat,user = msg["chat"]["id"],msg["from"]["id"]
        name = msg["from"].get("first_name","")
        username = msg["from"].get("username","") or ""
        text = msg.get("text","").strip()
        print(f"[→] {user} (@{username or '—'}) ({name}): {text[:50]}",flush=True)
        register(user,name,username,chat)
        role = get_role(user)

        if role == "banned": tg_send(chat,"🚫 Access denied."); return
        if role == "normal" and not _is_owner(user):
            u = api_get_user(str(user)) or {}
            if u.get("role")!="pending":
                set_pending(user,name,username); notify_owner_new_user(user,name,username,chat)
            tg_send(chat,"⏳ <b>PENDING APPROVAL</b>\n━━━━━━━━━━━━━━━━━━━\n\n"
                f"Request sent to admin.\n\n🆔 <code>{user}</code>\n💬 <code>{chat}</code>\n📛 @{username or '—'}")
            return
        if role == "pending" and not _is_owner(user):
            tg_send(chat,"⏳ <b>PENDING APPROVAL</b>\nPlease wait.\n\n"
                f"🆔 <code>{user}</code>\n💬 <code>{chat}</code>")
            return

        if text.startswith("/start"):
            tg_send(chat,build_start_text(name,role),reply_markup=build_market_keyboard()); return
        if text.startswith("/markets"):
            tg_send(chat,"👇 <b>Select a market:</b>",reply_markup=build_market_keyboard()); return
        if text.startswith("/help"):
            b = "/start /markets /signal /test /myid /help"
            if role in ("vip","owner"): b += "\n/autostart /stop"
            if role == "owner": b += "\n/stats /users /approved /banned /pending /add /remove /ban /unban /broadcast"
            tg_send(chat,b); return
        if text.startswith("/myid"):
            tg_send(chat,f"🆔 User ID: <code>{user}</code>\n💬 Chat ID: <code>{chat}</code>\n📛 @{username or '—'}"); return

        if text.startswith("/signal"):
            tg_send(chat,"🔍 Multi-TF auto-scan...")
            s = scan_auto()
            if s:
                send_signal_with_countdown(chat,s)
                tg_send(chat,"🔄 Pick another:",reply_markup=build_market_keyboard())
            else: tg_send(chat,"⚠️ No high-quality setup.\nWait for better conditions.")
            return

        if text.startswith("/test"):
            total = len(SYMBOLS)
            dep = "✅ curl_cffi" if HAS_CFFI else "❌ curl_cffi MISSING"
            prog = tg_send(chat,
                f"🧪 <b>Testing all {total} markets...</b>\n{dep}\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                f"<code>{'░'*total}</code>\n0% (0/{total})")
            msg_id = None
            if prog and prog.get("ok"): msg_id = prog["result"]["message_id"]

            results = []; ok_count = 0
            for idx,s in enumerate(SYMBOLS,1):
                c = fetch_candles(s,"1m",20,debug=True)
                if c and len(c)>=20:
                    results.append((s,len(c),True,"OK")); ok_count += 1
                else:
                    reason = "no curl_cffi" if not HAS_CFFI else "Yahoo blocked"
                    results.append((s,0,False,reason))
                if msg_id and (idx%3==0 or idx==total):
                    bar = "█"*idx+"░"*(total-idx); pct = int(idx/total*100)
                    try: tg_edit(chat,msg_id,
                        f"🧪 <b>Testing all {total}...</b>\n{dep}\n"
                        "━━━━━━━━━━━━━━━━━━━\n"
                        f"<code>{bar}</code>\n{pct}% ({idx}/{total})")
                    except Exception: pass

            out = ["🧪 <b>DATA SOURCE TEST</b>", ""]
            for i,(s,cnt,ok,reason) in enumerate(results,1):
                status = "✅" if ok else "❌"
                out.append(f"{status} <b>{s}</b> — {cnt} ({reason})")
            out.append("")
            out.append(f"Total: <b>{total}</b> | ✓ {ok_count} | ✗ {total-ok_count}")
            final = "\n".join(out)
            if msg_id:
                try: tg_edit(chat,msg_id,final)
                except Exception: tg_send(chat,final)
            else: tg_send(chat,final)
            return

        if text.startswith("/autostart"):
            if role not in ("vip","owner"): tg_send(chat,"🔒 VIP only"); return
            open(AUTO_FLAG,"w").write(str(int(time.time())))
            tg_send(chat,f"▶️ Auto ({SCAN_INTERVAL//60} min)"); return
        if text.startswith("/stop"):
            if role not in ("vip","owner"): return
            try: os.remove(AUTO_FLAG)
            except Exception: pass
            tg_send(chat,"⏹ Stopped"); return

        if text.startswith("/stats"):
            if role != "owner": tg_send(chat,"🔒 <b>Owner only</b>"); return
            tg_send(chat,build_stats_report()); return

        if text.startswith("/users"):
            if role != "owner": tg_send(chat,"🔒 <b>Owner only</b>"); return
            tg_send(chat,build_stats_report()); time.sleep(0.3)
            tg_send(chat,_fmt_full_member_list()); return

        if role == "owner":
            if text.startswith("/approved"):
                items = approved_users()
                if not items: tg_send(chat,"No approved users"); return
                tg_send(chat,_fmt_full_member_list()); return
            if text.startswith("/banned"):
                users = all_users(); filtered = {k:v for k,v in users.items() if not v.get("active",True)}
                if not filtered: tg_send(chat,"No banned users"); return
                tg_send(chat,_fmt_filtered("BANNED",filtered)); return
            if text.startswith("/pending"):
                p = pending_users()
                if not p: tg_send(chat,"No pending users"); return
                tg_send(chat,_fmt_filtered("PENDING APPROVAL",p))
                for uid in list(p.keys())[-5:]:
                    tg_send(chat,f"👤 {p[uid].get('name','?')} — <code>{uid}</code>",
                            reply_markup=build_owner_pending_keyboard(int(uid)))
                return
            if text.startswith("/add"):
                parts = text.split()
                if len(parts)<2: return
                try: t = int(parts[1])
                except ValueError: return
                info = api_get_user(str(t)) or {}
                approve(t, info.get("name",""), info.get("username",""), info.get("chat_id",t))
                tg_send(chat,f"✅ Approved {t}")
                try: tg_send(t,"🎉 Approved! /start")
                except Exception: pass
                return
            if text.startswith("/remove"):
                parts = text.split()
                if len(parts)<2: return
                try: revoke(int(parts[1])); tg_send(chat,"✅ Removed")
                except Exception: pass
                return
            if text.startswith("/ban"):
                parts = text.split()
                if len(parts)<2: return
                try: ban(int(parts[1]),True); tg_send(chat,"🚫 Banned")
                except Exception: pass
                return
            if text.startswith("/unban"):
                parts = text.split()
                if len(parts)<2: return
                try: ban(int(parts[1]),False); tg_send(chat,"✅ Unbanned")
                except Exception: pass
                return
            if text.startswith("/broadcast"):
                parts = text.split(maxsplit=1)
                if len(parts)<2: return
                sent = 0
                for u in all_users().values():
                    if not u.get("active", True): continue
                    try: tg_send(int(u["id"]),f"📢 {parts[1]}"); sent += 1
                    except Exception: pass
                    time.sleep(0.05)
                tg_send(chat,f"✅ Sent {sent}"); return

        if text.startswith("/"): tg_send(chat,"❓ Use /start"); return
        tg_send(chat,"Use /start to pick a market")
    except Exception as e:
        print(f"[!] Handler: {e}",flush=True)
        traceback.print_exc()

# ============================================================
#   AUTO LOOP
# ============================================================
def auto_loop():
    last = 0
    while True:
        try:
            if os.path.exists(AUTO_FLAG):
                if time.time()-last >= SCAN_INTERVAL:
                    print(f"[*] Auto scan {datetime.now().strftime('%H:%M:%S')}")
                    s = scan_auto()
                    if s:
                        recips = {_OWNER}
                        for u in approved_users(): recips.add(u["chat_id"] or u["id"])
                        for rid in recips:
                            try: send_signal_with_countdown(rid,s)
                            except Exception: pass
                        print(f"[+] {s['symbol']} {s['direction']} {s['confidence']}%")
                    else: print("[~] No high-quality signal")
                    last = time.time()
                    open(AUTO_FLAG,"w").write(str(int(last)))
            time.sleep(5)
        except Exception as e:
            print(f"[!] Auto: {e}"); time.sleep(10)

# ============================================================
#   MAIN
# ============================================================
def main():
    print("="*60)
    print("  Quotex Signal Bot v32 — v26 Logic + API")
    print(f"  Markets: {len(SYMBOLS)} | Min Conf: {MIN_CONFIDENCE}%")
    print(f"  API: {API_URL}")
    print("="*60)

    if not HAS_CFFI:
        print("[!] curl_cffi NOT installed — Yahoo will block")
        print("[!] Run: pip install curl_cffi")
    else:
        print("[+] curl_cffi OK — Yahoo bypass active")

    print("[*] API test...", flush=True)
    t = api_get("stats")
    if t and t.get("ok"):
        st = t.get("stats",{})
        print(f"[+] API connected — users: {st.get('total',0)}")
    else:
        print(f"[!] API not reachable")

    print("[*] Yahoo test...")
    ok = 0
    for s in SYMBOLS[:5]:
        c = fetch_candles(s,"1m",20)
        if c: print(f"  [+] {s}: OK ({len(c)})"); ok += 1
        else: print(f"  [!] {s}: FAIL")
    print(f"[+] {ok}/5 OK\n")

    threading.Thread(target=auto_loop,daemon=True).start()
    offset = None
    while True:
        try:
            for up in tg_poll(offset):
                offset = up["update_id"]+1
                if "message" in up and "text" in up["message"]:
                    handle(up["message"])
                elif "callback_query" in up:
                    handle_callback(up["callback_query"])
        except KeyboardInterrupt:
            print("\nStopped"); break
        except Exception as e:
            print(f"[!] Poll: {e}"); time.sleep(3)

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: print("\nStopped")
