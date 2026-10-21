#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quotex Signal Bot v26 — Yahoo + curl_cffi Only (No API Keys)
- Yahoo bypass via curl_cffi Chrome TLS fingerprint
- No Stooq, no API key required
- Multi-TF (1m+5m+15m) analysis, 15+ strategies
- /test shows ALL markets with real failure reasons
- /users = STATS + FULL MEMBER LIST
- bot.py + users.txt only
"""

import subprocess
import sys
import importlib.util

# 检查 curl_cffi 是否已安装
if importlib.util.find_spec("curl_cffi") is None:
    print("[*] curl_cffi 未安装，正在自动安装...")
    try:
        # 调用当前 Python 环境的 pip 进行安装
        subprocess.check_call([sys.executable, "-m", "pip", "install", "curl_cffi"])
        print("[+] curl_cffi 安装成功")
    except subprocess.CalledProcessError as e:
        print(f"[!] 自动安装失败，请手动运行: pip install curl_cffi")
        print(f"    错误信息: {e}")
        sys.exit(1)  # 安装失败则退出，避免后续代码报错

import sys, os, json, time, math, random, threading
import urllib.parse, ssl
from pathlib import Path
from datetime import datetime

def log(msg, level="info"):
    icon = {"info": "[*]", "ok": "[+]", "warn": "[!]", "err": "[x]"}.get(level, "[*]")
    print(f"{icon} {msg}", flush=True)

if sys.version_info < (3, 8):
    log("Python 3.8+ required", "err"); sys.exit(1)

# ============================================================
#   CONFIG
# ============================================================
BOT_TOKEN = "8733882861:AAEpvcBAxJWtnfHWBTju8mOZCaNBTTtw4i8"
_OWNER = 6919881622

SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
    "USDCHF", "GBPCAD", "GBPCHF", "CHFJPY",
    "EURAUD", "AUDCAD", "CADJPY", "GBPJPY", "EURGBP",
    "NZDUSD", "EURJPY", "AUDJPY", "EURNZD",
]
YF_MAP = {s: s + "=X" for s in SYMBOLS}

SCAN_INTERVAL     = 300
MIN_CONFIDENCE    = 65
COUNTDOWN_SECONDS = 5
DEDUP_SECONDS     = 300
CACHE_TTL         = 30
MAX_CACHE         = 30

USERS_FILE = Path("users.txt")
AUTO_FLAG  = Path("auto.flag")

TG = f"https://api.telegram.org/bot{BOT_TOKEN}"

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode    = ssl.CERT_NONE

_CANDLE_CACHE    = {}
_LAST_SIGNAL_MEM = {}
_SIGNAL_STATS    = {"CALL": 0, "PUT": 0, "NO_TRADE": 0}

# ============================================================
#   HTTP — curl_cffi দিয়ে Yahoo বাইপাস (কোনো API Key নেই)
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
        # Fallback urllib (likely blocked by Yahoo)
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
    try:
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None

# ============================================================
#   YAHOO DATA FETCH (only source)
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
#   MATH
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
#   PATTERNS
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
#   FILTERS
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
#   SINGLE-TF ANALYSIS
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
#   MULTI-TF ANALYSIS
# ============================================================
def analyze(symbol):
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
    loser = total_put if direction=="CALL" else total_call

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
#   USERS
# ============================================================
def _ensure_users_file():
    if not USERS_FILE.exists():
        USERS_FILE.write_text(
            "# chat_id|username|name|role|active|signals|joined\n"
            f"{_OWNER}|@owner|Owner|owner|1|0|{int(time.time())}\n",
            encoding="utf-8")

def load_users():
    _ensure_users_file()
    users = {}
    try:
        for line in USERS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"): continue
            parts = line.split("|")
            if len(parts) < 4: continue
            try:
                cid = int(parts[0])
                uname = parts[1].strip()
                name = parts[2].strip()
                role = parts[3].strip() or "normal"
                act = int(parts[4]) if len(parts)>4 and parts[4].strip() else 1
                sig_n = int(parts[5]) if len(parts)>5 and parts[5].strip() else 0
                join = int(parts[6]) if len(parts)>6 and parts[6].strip() else int(time.time())
            except Exception: continue
            users[str(cid)] = {"id":cid,"chat_id":cid,
                "username":uname.lstrip("@") if uname not in ("","—") else "",
                "name":name,"role":role,"active":bool(act),
                "signals":sig_n,"joined":join}
    except Exception: pass
    return users

def save_users(users):
    lines = ["# chat_id|username|name|role|active|signals|joined"]
    for u in users.values():
        uname = ("@"+u.get("username","")) if u.get("username") else "—"
        lines.append(f"{u['id']}|{uname}|{u.get('name','')}|{u.get('role','normal')}|"
                     f"{1 if u.get('active',True) else 0}|{u.get('signals',0)}|{u.get('joined',0)}")
    try: USERS_FILE.write_text("\n".join(lines)+"\n", encoding="utf-8")
    except Exception: pass

def _is_owner(uid):
    try: return int(uid)==int(_OWNER)
    except Exception: return False

def get_role(uid):
    if _is_owner(uid): return "owner"
    u = load_users().get(str(uid),{})
    if u.get("role")=="vip" and u.get("active",True): return "vip"
    if not u.get("active",True) and u: return "banned"
    if u.get("role")=="pending": return "pending"
    return "normal"

def register(uid,name="",username="",chat_id=None):
    users = load_users(); k = str(uid); changed = False
    if k not in users:
        users[k] = {"id":int(uid),"chat_id":int(chat_id or uid),
            "username":username or "","name":name or "",
            "role":"owner" if _is_owner(uid) else "normal",
            "active":True,"signals":0,"joined":int(time.time())}
        changed = True
    else:
        u = users[k]
        if name and u.get("name")!=name: u["name"]=name; changed=True
        if username and u.get("username")!=username: u["username"]=username; changed=True
        if chat_id and u.get("chat_id")!=int(chat_id): u["chat_id"]=int(chat_id); changed=True
        if _is_owner(uid) and u.get("role")!="owner": u["role"]="owner"; changed=True
    if changed: save_users(users)

def set_pending(uid,name="",username=""):
    users = load_users(); k = str(uid)
    if k not in users:
        users[k] = {"id":int(uid),"chat_id":int(uid),
            "username":username or "","name":name or "",
            "role":"pending","active":True,"signals":0,"joined":int(time.time())}
    else:
        if users[k].get("role")=="normal": users[k]["role"]="pending"
        if name: users[k]["name"]=name
        if username: users[k]["username"]=username
    save_users(users)

def approve(uid):
    users = load_users(); k = str(uid)
    if k not in users:
        users[k] = {"id":int(uid),"chat_id":int(uid),"username":"","name":"",
            "role":"vip","active":True,"signals":0,"joined":int(time.time())}
    else:
        users[k]["role"]="vip"; users[k]["active"]=True
    save_users(users)

def revoke(uid):
    users = load_users(); k = str(uid)
    if k in users: users[k]["role"]="normal"; save_users(users)

def ban(uid,state=True):
    users = load_users(); k = str(uid)
    if k in users: users[k]["active"]=not state; save_users(users)

def inc_signals(uid):
    users = load_users(); k = str(uid)
    if k in users: users[k]["signals"]=users[k].get("signals",0)+1; save_users(users)

def all_users(): return load_users()
def approved_users():
    return [u for u in all_users().values() if u.get("role")=="vip" and u.get("active",True)]
def pending_users():
    return {k:v for k,v in all_users().items() if v.get("role")=="pending" and v.get("active",True)}

# ============================================================
#   REPORTS
# ============================================================
def build_stats_report():
    users = all_users(); total = len(users)
    vip = sum(1 for u in users.values() if u.get("role")=="vip" and u.get("active",True))
    owner_ = sum(1 for u in users.values() if u.get("role")=="owner")
    banned = sum(1 for u in users.values() if not u.get("active",True))
    pend = sum(1 for u in users.values() if u.get("role")=="pending" and u.get("active",True))
    normal = sum(1 for u in users.values() if u.get("role")=="normal" and u.get("active",True))
    return ("📊 <b>BOT STATS</b>\n━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Total users : {total}\n⭐ VIP users   : {vip}\n"
            f"🚫 Banned      : {banned}\n⏳ Pending     : {pend}\n"
            f"👤 Normal      : {normal}\n👑 Owner       : {owner_}\n"
            f"📞 CALL signals: {_SIGNAL_STATS.get('CALL',0)}\n"
            f"📉 PUT  signals: {_SIGNAL_STATS.get('PUT',0)}\n"
            f"🚫 No-trade    : {_SIGNAL_STATS.get('NO_TRADE',0)}\n"
            "━━━━━━━━━━━━━━━━━━━")

def _fmt_full_member_list():
    users = all_users()
    if not users: return "<b>📋 MEMBER LIST</b>\n(empty)"
    order = {"owner":0,"vip":1,"pending":2,"normal":3}
    items = sorted(users.values(), key=lambda u: 9 if not u.get("active",True) else order.get(u.get("role","normal"),5))
    W_NUM,W_USER,W_CHAT,W_NAME,W_ROLE = 3,22,13,16,10
    total_w = W_NUM+W_USER+W_CHAT+W_NAME+W_ROLE+12
    line = "─"*total_w; dline = "═"*total_w
    def cell(s,w,al="<"):
        s=str(s)[:w]
        return s.ljust(w) if al=="<" else s.rjust(w)
    out = ["📋 <b>FULL MEMBER LIST</b>", dline]
    out.append(" # │ "+cell("@USERNAME",W_USER)+" │ "+cell("CHAT ID",W_CHAT,">")+" │ "+cell("NAME",W_NAME)+" │ "+cell("ROLE",W_ROLE)+" ")
    out.append(line)
    for i,u in enumerate(items,1):
        cid = u.get("chat_id") or u.get("id","?")
        uname = u.get("username") or ""
        uf = f"@{uname}" if uname else "—"
        role = u.get("role","normal"); act = u.get("active",True)
        tag = "BANNED" if not act else ("👑 OWNER" if role=="owner" else ("⭐ VIP" if role=="vip" else ("⏳ PEND" if role=="pending" else "NORMAL")))
        out.append(cell(str(i),W_NUM,">")+" │ "+cell(uf,W_USER)+" │ "+cell(str(cid),W_CHAT,">")+" │ "+cell(u.get("name","?") or "—",W_NAME)+" │ "+cell(tag,W_ROLE)+" ")
    out.append(line); out.append(f"  Total: {len(items)}")
    return "<code>"+"\n".join(out)+"</code>"

def _fmt_filtered(title, users_dict):
    if not users_dict: return f"<b>{title}</b>\n(empty)"
    W_NUM,W_USER,W_CHAT,W_NAME,W_ROLE = 3,22,13,16,10
    total_w = W_NUM+W_USER+W_CHAT+W_NAME+W_ROLE+12
    line = "─"*total_w; dline = "═"*total_w
    def cell(s,w,al="<"):
        s=str(s)[:w]
        return s.ljust(w) if al=="<" else s.rjust(w)
    out = [f"📋 <b>{title}</b>", dline]
    out.append(" # │ "+cell("@USERNAME",W_USER)+" │ "+cell("CHAT ID",W_CHAT,">")+" │ "+cell("NAME",W_NAME)+" │ "+cell("ROLE",W_ROLE)+" ")
    out.append(line)
    for i,(k,u) in enumerate(users_dict.items(),1):
        cid = u.get("chat_id") or k
        uname = u.get("username") or ""
        uf = f"@{uname}" if uname else "—"
        out.append(cell(str(i),W_NUM,">")+" │ "+cell(uf,W_USER)+" │ "+cell(str(cid),W_CHAT,">")+" │ "+cell(u.get("name","?") or "—",W_NAME)+" │ "+cell((u.get("role","?") or "?")[:W_ROLE],W_ROLE)+" ")
    out.append(line); out.append(f"  Total: {len(users_dict)}")
    return "<code>"+"\n".join(out)+"</code>"

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
    txt = ("<b>🤖 Quotex Signal Bot v26</b>\n━━━━━━━━━━━━━━━━━━━\n"
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
        W_USER,W_CHAT,W_STAT = 22,13,11
        total_w = 3+W_USER+W_CHAT+W_STAT+9
        line = "─"*total_w; dline = "═"*total_w
        def cl(s,w,al="<"):
            s=str(s)[:w]; return s.ljust(w) if al=="<" else s.rjust(w)
        uf = f"@{username}" if username else "—"
        table = ["📊 NEW USER REQUEST",dline]
        table.append(" # │ "+cl("@USERNAME",W_USER)+" │ "+cl("CHAT ID",W_CHAT,">")+" │ "+cl("STATUS",W_STAT)+" ")
        table.append(line)
        table.append(cl("1",3,">")+" │ "+cl(uf,W_USER)+" │ "+cl(str(chat_id),W_CHAT,">")+" │ "+cl("⏳ PENDING",W_STAT)+" ")
        table.append(line); table.append("  Total: 1  |  ✓ 0  |  ✗ 1"); table.append(dline)
        table.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        tg_send(_OWNER,"<code>"+"\n".join(table)+"</code>",
                reply_markup=build_owner_pending_keyboard(uid))
    except Exception: pass

# ============================================================
#   CALLBACK
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
            tinfo = load_users().get(str(target),{})
            tuname = tinfo.get("username",""); tchat = tinfo.get("chat_id",target)
            uf = f"@{tuname}" if tuname else "—"
            W_USER,W_CHAT,W_STAT = 22,13,11
            total_w = 3+W_USER+W_CHAT+W_STAT+9
            line = "─"*total_w; dline = "═"*total_w
            def cl(s,w,al="<"):
                s=str(s)[:w]; return s.ljust(w) if al=="<" else s.rjust(w)
            if data.startswith("apr:"):
                approve(target); tag="✓ APPROVED"
                try: tg_edit(chat,msg_id,"<code>"+"\n".join([
                    "📊 APPROVAL RESULT",dline,
                    " # │ "+cl("@USERNAME",W_USER)+" │ "+cl("CHAT ID",W_CHAT,">")+" │ "+cl("STATUS",W_STAT)+" ",
                    line," 1 │ "+cl(uf,W_USER)+" │ "+cl(str(tchat),W_CHAT,">")+" │ "+cl(tag,W_STAT)+" ",
                    line,"  Total: 1  |  ✓ 1  |  ✗ 0"])+"</code>")
                except Exception: pass
                tg_answer(cb_id,"Approved")
                try: tg_send(target,"🎉 <b>You're approved!</b>\nTry /start now.")
                except Exception: pass
            else:
                ban(target,True); tag="✗ REJECTED"
                try: tg_edit(chat,msg_id,"<code>"+"\n".join([
                    "📊 APPROVAL RESULT",dline,
                    " # │ "+cl("@USERNAME",W_USER)+" │ "+cl("CHAT ID",W_CHAT,">")+" │ "+cl("STATUS",W_STAT)+" ",
                    line," 1 │ "+cl(uf,W_USER)+" │ "+cl(str(tchat),W_CHAT,">")+" │ "+cl(tag,W_STAT)+" ",
                    line,"  Total: 1  |  ✓ 0  |  ✗ 1"])+"</code>")
                except Exception: pass
                tg_answer(cb_id,"Rejected")
                try: tg_send(target,"❌ Access denied by admin.")
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

# ============================================================
#   HANDLE
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
            u = load_users().get(str(user),{})
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

        # ============ /test — ALL MARKETS ============
        if text.startswith("/test"):
            total = len(SYMBOLS)
            dep = "✅ curl_cffi" if HAS_CFFI else "❌ curl_cffi MISSING (pip install curl_cffi)"
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

            W_NUM,W_SYM,W_CNT,W_STAT,W_REASON = 3,10,7,10,18
            total_w = W_NUM+W_SYM+W_CNT+W_STAT+W_REASON+12
            line = "─"*total_w; dline = "═"*total_w
            def cell(s,w,al="<"):
                s=str(s)[:w]; return s.ljust(w) if al=="<" else s.rjust(w)
            out = ["🧪 <b>DATA SOURCE TEST</b>",dline]
            out.append(" # │ "+cell("MARKET",W_SYM)+" │ "+cell("CANDLES",W_CNT,">")+" │ "+cell("STATUS",W_STAT)+" │ "+cell("REASON",W_REASON)+" ")
            out.append(line)
            for i,(s,cnt,ok,reason) in enumerate(results,1):
                status = "✅ OK" if ok else "❌ FAIL"
                out.append(cell(str(i),W_NUM,">")+" │ "+cell(s,W_SYM)+" │ "+cell(str(cnt),W_CNT,">")+" │ "+cell(status,W_STAT)+" │ "+cell(reason,W_REASON)+" ")
            out.append(line); fail_count = total-ok_count
            out.append(f"  Total: {total}  |  ✓ {ok_count}  |  ✗ {fail_count}")
            if ok_count==0:
                out.append(""); out.append("💡 <b>Fix:</b>")
                if not HAS_CFFI: out.append("• <code>pip install curl_cffi</code>")
                else: out.append("• Yahoo may rate-limit your IP, wait 5 min")
                out.append("• Or run on different network")
            final = "<code>"+"\n".join(out)+"</code>"
            if msg_id:
                try: tg_edit(chat,msg_id,final)
                except Exception: tg_send(chat,final)
            else: tg_send(chat,final)
            return

        if text.startswith("/autostart"):
            if role not in ("vip","owner"): tg_send(chat,"🔒 VIP only"); return
            AUTO_FLAG.write_text(str(int(time.time())))
            tg_send(chat,f"▶️ Auto ({SCAN_INTERVAL//60} min)"); return
        if text.startswith("/stop"):
            if role not in ("vip","owner"): return
            try: AUTO_FLAG.unlink()
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
                approve(t); tg_send(chat,f"✅ Approved {t}"); tg_send(t,"🎉 Approved! /start"); return
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
                for uid in all_users().keys():
                    try: tg_send(int(uid),f"📢 {parts[1]}"); sent += 1
                    except Exception: pass
                    time.sleep(0.05)
                tg_send(chat,f"✅ Sent {sent}"); return

        if text.startswith("/"): tg_send(chat,"❓ Use /start"); return
        tg_send(chat,"Use /start to pick a market")
    except Exception as e:
        print(f"[!] Handler: {e}",flush=True)

# ============================================================
#   AUTO LOOP
# ============================================================
def auto_loop():
    last = 0
    while True:
        try:
            if AUTO_FLAG.exists():
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
                    last = time.time(); AUTO_FLAG.write_text(str(int(last)))
            time.sleep(5)
        except Exception as e:
            print(f"[!] Auto: {e}"); time.sleep(10)

# ============================================================
#   MAIN
# ============================================================
def main():
    print("="*60)
    print("  Quotex Signal Bot v26 — Yahoo + curl_cffi Only")
    print(f"  Markets: {len(SYMBOLS)} | Min Conf: {MIN_CONFIDENCE}%")
    print("="*60)
    _ensure_users_file()
    print(f"[+] Users: {USERS_FILE.resolve()}")
    if not HAS_CFFI:
        print("[!] curl_cffi NOT installed!")
        print("[!] Run: pip install curl_cffi")
        print("[!] Without it, Yahoo will block all requests (429)")
    else:
        print("[+] curl_cffi OK — Yahoo bypass active")
    print("[*] Testing...")
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