import urllib.request
import urllib.parse
import json
import time
import socket
import os
import threading
import http.server
import socketserver
import statistics
import random
from datetime import datetime, timedelta, timezone

# =====================================================
# AYARLAR
# =====================================================

socket.setdefaulttimeout(15)

T = os.environ.get("BOT_TOKEN", "").strip()
C = os.environ.get("CHAT_ID", "1623907197").strip()

TR_Saati = timezone(timedelta(hours=3))
BASE = "https://api.binance.com"

# MOTOR ÇALIŞMA SÜRELERİ
RAPOR_ARALIGI = 14400      
CHARTIST_ARALIGI = 3600    
PUMP_ARALIGI = 60          
FORMASYON_ARALIGI = 3600   
EMA_RIBBON_ARALIGI = 3600  
MACD_ARALIGI = 3600        
MIDAS_SETUP_ARALIGI = 7200 
ATH_ATL_ARALIGI = 3600     
DIGITAL_CHARTIST_ARALIGI = 3600 
SMA200_ARALIGI = 3600      
BOLLINGER_ARALIGI = 3600   
ICHIMOKU_ARALIGI = 3600    
FVG_KONFLUANS_ARALIGI = 3600 
SQUEEZE_RADAR_ARALIGI = 14400
HACIM_PATLAMASI_ARALIGI = 3600 
EMILIM_ARALIGI = 3600 
SMART_MONEY_ARALIGI = 3600 

# FİLTRELER (Hacim)
MIN_HACIM_CHARTIST = 10_000_000 
MIN_HACIM_PUMP = 5_000_000     
MIN_HACIM_FORMASYON = 10_000_000 
MIN_HACIM_EMA_RIBBON = 10_000_000 
MIN_HACIM_MACD = 10_000_000 
MIN_HACIM_MIDAS_SETUP = 15_000_000 
MIN_HACIM_ATH_ATL = 5_000_000 
MIN_HACIM_DIGITAL = 10_000_000 
MIN_HACIM_SMA200 = 15_000_000  
MIN_HACIM_BOLLINGER = 10_000_000 
MIN_HACIM_ICHIMOKU = 10_000_000  
MIN_HACIM_FVG = 10_000_000 
MIN_HACIM_SQUEEZE = 20_000_000 
MIN_HACIM_HACIM_PATLAMASI = 10_000_000 
MIN_HACIM_EMILIM = 5_000_000 
MIN_HACIM_SMART_MONEY = 100_000_000 

# SİNYAL HAFIZALARI
son_sinyaller = {}
son_pump_sinyalleri = {}
son_formasyon_sinyalleri = {}
son_ema_ribbon_sinyalleri = {}
son_macd_sinyalleri = {}
son_midas_setup_sinyalleri = {}
son_ath_atl_sinyalleri = {}
son_chartist_sinyalleri = {} 
son_sma200_sinyalleri = {}   
son_bollinger_sinyalleri = {} 
son_ichimoku_sinyalleri = {}  
son_fvg_sinyalleri = {}       
son_squeeze_sinyalleri = {}
son_hacim_sinyalleri = {} 
son_emilim_sinyalleri = {} 
son_smart_money_sinyalleri = {} 

# =====================================================
# YARDIMCI FONKSİYONLAR
# =====================================================

def simdi():
    return datetime.now(TR_Saati)

def log(prefix, m):
    print(f"[{simdi().strftime('%H:%M:%S')}] [{prefix}] {m}", flush=True)

def akilli_fiyat(p):
    if p is None: return "0"
    p = float(p)
    if p >= 1000: return f"{p:.2f}"
    if p >= 1: return f"{p:.4f}"
    if p >= 0.01: return f"{p:.6f}"
    return f"{p:.8f}"

def tg(chat_id, message):
    if not T or not chat_id: return False
    try:
        data = urllib.parse.urlencode({
            "chat_id": str(chat_id), "text": message, "parse_mode": "HTML", "disable_web_page_preview": "true"
        }).encode("utf-8")
        req = urllib.request.Request(f"https://api.telegram.org/bot{T}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8")).get("ok", False)
    except:
        return False

def gj(url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except:
        return None

def calculate_ema(prices, period):
    if len(prices) < period: return []
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]
    for price in prices[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema

def calculate_sma(prices, period):
    if len(prices) < period: return []
    return [sum(prices[i:i+period])/period for i in range(len(prices)-period+1)]

def calculate_targets(entry_price, recent_low):
    diff = entry_price - recent_low
    targets = [entry_price + (diff * 0.272), entry_price + (diff * 0.618), entry_price + (diff * 1.000), entry_price + (diff * 1.618), entry_price + (diff * 2.618)]
    return targets

def calculate_short_targets(entry_price, recent_high):
    diff = recent_high - entry_price
    targets = [entry_price - (diff * 0.272), entry_price - (diff * 0.618), entry_price - (diff * 1.000), entry_price - (diff * 1.618), entry_price - (diff * 2.618)]
    return targets

def calculate_macd(prices, fast=12, slow=26, signal=9):
    if len(prices) < slow + signal: return None, None, None, None
    ema_fast = calculate_ema(prices, fast)
    ema_slow = calculate_ema(prices, slow)
    if not ema_fast or not ema_slow: return None, None, None, None
    min_len = min(len(ema_fast), len(ema_slow))
    macd_line = [ema_fast[-min_len+i] - ema_slow[-min_len+i] for i in range(min_len)]
    signal_line = calculate_ema(macd_line, signal)
    if not signal_line: return None, None, None, None
    return macd_line[-1], signal_line[-1], macd_line[-2], signal_line[-2]

def calculate_bollinger(prices, period=20, std_dev=2):
    if len(prices) < period: return None, None, None
    sma = sum(prices[-period:]) / period
    std = statistics.pstdev(prices[-period:])
    return sma + (std_dev * std), sma, sma - (std_dev * std)

def calculate_ichimoku(highs, lows, closes):
    if len(closes) < 52: return None, None
    tenkan = (max(highs[-9:]) + min(lows[-9:])) / 2
    kijun = (max(highs[-26:]) + min(lows[-26:])) / 2
    senkou_a = (tenkan + kijun) / 2
    senkou_b = (max(highs[-52:]) + min(lows[-52:])) / 2
    return max(senkou_a, senkou_b), min(senkou_a, senkou_b)

def get_market_trend(tickers):
    try:
        sorted_tickers = sorted(tickers, key=lambda x: float(x.get('quoteVolume', 0)), reverse=True)[:100]
        changes = [float(t.get('priceChangePercent', 0)) for t in sorted_tickers]
        if not changes: return "Bilinmiyor", 0, 0, 0
        avg_change = sum(changes) / len(changes)
        ups, downs = sum(1 for c in changes if c > 0), sum(1 for c in changes if c < 0)
        if avg_change > 1.5 and ups > downs: trend = "🟢 YUKARI (Boğa)"
        elif avg_change < -1.5 and downs > ups: trend = "🔴 AŞAĞI (Ayı)"
        else: trend = "🟡 YATAY (Kararsız)"
        return trend, avg_change, ups, downs
    except: return "Bilinmiyor", 0, 0, 0

def trap_radar_check(symbol):
    try:
        oi_data = gj(f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=2")
        oi_artiyor = False
        if oi_data and len(oi_data) >= 2:
            if ((float(oi_data[1]['sumOpenInterest']) - float(oi_data[0]['sumOpenInterest'])) / float(oi_data[0]['sumOpenInterest'])) * 100 > 0.2: oi_artiyor = True
        taker_data = gj(f"https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1")
        alici_baskin = False
        if taker_data and len(taker_data) > 0:
            total = float(taker_data[0]['buyVol']) + float(taker_data[0]['sellVol'])
            if (float(taker_data[0]['buyVol']) / total) * 100 > 55: alici_baskin = True
        if oi_artiyor and alici_baskin: return "🐋 <b>BALİNA TEYİDİ (Gerçek Pump)</b>"
        elif not alici_baskin: return "⚠️ <b>DİKKAT: SAHTE PUMP (TUZAK OLABİLİR)</b>"
        else: return "⚖️ <b>Kısmi Teyit</b>"
    except: return "⚖️ <b>Kısmi Teyit</b>"

def squeeze_radar_check(symbol, timeframe='4h'):
    try:
        global_data = gj(f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period={timeframe}&limit=1")
        top_data = gj(f"https://fapi.binance.com/futures/data/topLongShortAccountRatio?symbol={symbol}&period={timeframe}&limit=1")
        if not global_data or not top_data: return ""
        g_long_pct = float(global_data[0]['longAccount']) * 100
        t_long_pct = float(top_data[0]['longAccount']) * 100
        if g_long_pct > 75 and t_long_pct < 50:
            return f"⚠️ <b>SİNYAL RİSKLİ:</b> Long Sıkışması Riski Var! (Kalabalık %{g_long_pct:.0f} Long, Balina %{t_long_pct:.0f} Long)"
        elif g_long_pct < 30 and t_long_pct > 60:
            return f"🚀 <b>SİNYAL DESTEKLİ:</b> Short Sıkışması Riski Var! (Kalabalık %{g_long_pct:.0f} Long, Balina %{t_long_pct:.0f} Long)"
        return ""
    except: return ""

# =====================================================
# MOTOR 17: SMART MONEY (LİKİDİTE AVI & OTE)
# =====================================================
def smart_money_loop():
    log("SMART_MONEY", "Akıllı Para (Likidite Avı & OTE) Motoru Başladı.")
    time.sleep(400)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_SMART_MONEY]:
                for tf in ['4h', '1d']:
                    cooldown_key = f"SM_{tf}_{symbol}"
                    if cooldown_key in son_smart_money_sinyalleri and time.time() - son_smart_money_sinyalleri[cooldown_key] < 43200: continue
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=50")
                    if not isinstance(klines, list) or len(klines) < 30: continue
                    highs = [float(k[2]) for k in klines]
                    lows = [float(k[3]) for k in klines]
                    closes = [float(k[4]) for k in klines]
                    curr_high = highs[-2]; curr_low = lows[-2]; curr_close = closes[-2]
                    son_20_dip = min(lows[-22:-2]); son_20_tepe = max(highs[-22:-2])
                    
                    if curr_l
