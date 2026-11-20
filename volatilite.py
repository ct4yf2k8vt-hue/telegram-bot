#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spot Akis & Sinyal Telegram Botu (Zero-Dependency Render Edition)
- Ek paket gerektirmez (yalnizca Python 3 standart kutuphanesi)
- Render Web Service uyumlu (PORT env, varsayilan 10000)
- 8 Motor:
  1) 0-7 dk Spot Anlik Pump Dedektoru (Sade TR format, coin bazli tekil kilit)
  2) W1 ATL (Tum Zamanlarin En Dusugu - siki dip fiyati altina inmeden tekrar etmez)
  3) 4h Akis / Emir Defteri Duvari & 24s POC (1D + 3D Trend Onayli + LONG<->SHORT Kilitli)
  4 & 5) Gunun Zaferi (21:00 TR) & TOP 24 Saat (23:00 TR)
  6) 1G MA200 Kirilim & Dusus (Sadece klasik SMA200 + ABOVE<->BELOW Yon Kilidi)
  7) PABS Spot Alim Bolgesi (1h destek donusu + RSI14 + Dongu Kilidi)
  8) Binance Resmi Duyuru & Haber Takibi (Katalog 48 / 49 / 161)
"""

import json
import os
import re
import struct
import threading
import time
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==============================================================================
# YAPILANDIRMA
# ==============================================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()
PORT = int(os.environ.get("PORT", "10000"))
PUMP_REPEAT_HOURS = float(os.environ.get("PUMP_REPEAT_HOURS", "0"))  # 0 = coin basina 1 kez
STATE_FILE = os.environ.get("STATE_FILE", "/tmp/render_spot_bot_state.json")

TR_TZ = timezone(timedelta(hours=3))

BINANCE_MIRRORS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]

EXCLUDED_SUBSTRINGS = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
EXCLUDED_PREFIXES = ("USDC", "FDUSD", "TUSD", "USDP", "EUR")

# ==============================================================================
# KALICI DURUM (STATE & KILITLER)
# ==============================================================================
STATE_LOCK = threading.Lock()
STATE = {
    "pump_locks": {},       # base -> {"symbol": sym, "locked_at": ts}
    "atl_locks": {},        # sym  -> {"atl": float, "locked_at": ts}
    "flow_locks": {},       # sym  -> {"direction": "LONG"|"SHORT", "locked_at": ts}
    "ma200_locks": {},      # sym  -> {"direction": "ABOVE"|"BELOW", "ma200": float, "updated_at": ts}
    "pabs_locks": {},       # sym  -> {"unlock_above": float, "locked_at": ts}
    "pabs_seq": 48670,
    "active_signals": [],   # Gunun Zaferi / TOP 24 takibi
    "seen_news_ids": [],
    "last_victory_day": "",
    "last_top24_day": "",
}

PRICE_WINDOW_HIST = {}  # sym -> list[(ts_sec, price)]
ATL_WEEKLY_CACHE = {}   # sym -> (prev_atl, ts_sec)
LATEST_TICKERS = {}     # sym -> {"price": float, "chg24h": float, "vol": float, "high": float, "low": float}
NEWS_SEEDED = False


def load_state():
    global STATE
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                for k in STATE:
                    if k in raw:
                        STATE[k] = raw[k]
    except Exception:
        pass


def save_state():
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(STATE, f)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


# ==============================================================================
# HTTP / BINANCE / COKLU BORSA & TELEGRAM YARDIMCILARI
# ==============================================================================
def http_get_json(url, timeout=9):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; SpotFlowRenderBot/1.0)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def binance_get(path_and_query, timeout=9):
    last_err = None
    for mirror in BINANCE_MIRRORS:
        try:
            return http_get_json(mirror + path_and_query, timeout=timeout)
        except Exception as e:
            last_err = e
    raise last_err or RuntimeError("Binance mirrorlara ulasilamadi")


def fetch_klines(symbol, interval, limit=240):
    raw = binance_get(
        f"/api/v3/klines?symbol={urllib.parse.quote(symbol)}&interval={urllib.parse.quote(interval)}&limit={int(limit)}",
        timeout=10,
    )
    bars = []
    if not isinstance(raw, list):
        return bars
    for k in raw:
        bars.append({
            "t": int(k[0]) // 1000,
            "o": float(k[1]),
            "h": float(k[2]),
            "l": float(k[3]),
            "c": float(k[4]),
            "v": float(k[5]),
            "qv": float(k[7]),
        })
    return bars


def fetch_multi_exchange_sync(symbol, binance_price):
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    exchanges = ["Binance"]
    checks = [
        ("Bybit", f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}",
         lambda d: float(d.get("result", {}).get("list", [{}])[0].get("lastPrice", 0) or 0)),
        ("OKX", f"https://www.okx.com/api/v5/market/ticker?instId={base}-USDT",
         lambda d: float(d.get("data", [{}])[0].get("last", 0) or 0)),
        ("Gate", f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={base}_USDT",
         lambda d: float(d[0].get("last", 0) or 0) if isinstance(d, list) and d else 0.0),
    ]
    for name, url, extractor in checks:
        try:
            data = http_get_json(url, timeout=3.5)
            p = extractor(data)
            if p > 0 and abs(p - binance_price) / binance_price < 0.02:
                exchanges.append(name)
        except Exception:
            pass
    return exchanges


def tg_send_message(text):
    if not BOT_TOKEN:
        print("[TG DRY-RUN MESSAGE]\n" + text + "\n")
        return
    try:
        payload = json.dumps({
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:
        print("Telegram sendMessage hatasi:", e)


def tg_send_photo(png_bytes, caption):
    if not BOT_TOKEN:
        print("[TG DRY-RUN PHOTO]\n" + caption + "\n")
        return
    try:
        boundary = "----SpotFlowBoundary7MA4YWxkTrZu0gW"
        parts = []
        for field, val in [("chat_id", CHAT_ID), ("caption", caption), ("parse_mode", "HTML")]:
            parts.append((
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field}"\r\n\r\n'
                f"{val}\r\n"
            ).encode("utf-8"))
        parts.append((
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="chart.png"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode("utf-8"))
        parts.append(png_bytes)
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print("Telegram sendPhoto hatasi:", e)
        tg_send_message(caption)


# ==============================================================================
# SIFIR BAGIMLILIKLI PURE-PYTHON PNG MUM GRAFIK OLUSTURUCU
# ==============================================================================
def render_candlestick_png(candles, hlines=None, sma_series=None, band=None, width=900, height=440):
    bars = candles[-70:]
    if not bars:
        return b""

    hlines = hlines or []
    sma_slice = (sma_series or [])[-len(bars):]

    bg = (9, 13, 22)
    grid_col = (26, 36, 56)
    green = (16, 185, 129)
    red = (244, 63, 94)
    amber = (245, 158, 11)
    zone_col = (16, 70, 52)

    buf = bytearray([bg[0], bg[1], bg[2]] * (width * height))

    def set_px(x, y, col):
        if 0 <= x < width and 0 <= y < height:
            i = (y * width + x) * 3
            buf[i], buf[i + 1], buf[i + 2] = col

    def fill_rect(x0, y0, x1, y1, col):
        x0, x1 = max(0, int(x0)), min(width - 1, int(x1))
        y0, y1 = max(0, int(y0)), min(height - 1, int(y1))
        for y in range(y0, y1 + 1):
            row = y * width * 3
            for x in range(x0, x1 + 1):
                i = row + x * 3
                buf[i], buf[i + 1], buf[i + 2] = col

    def draw_line(x0, y0, x1, y1, col, dashed=False):
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        steps = max(dx, dy, 1)
        for s in range(steps + 1):
            if dashed and (s // 6) % 2 == 1:
                continue
            x = int(round(x0 + (x1 - x0) * s / steps))
            y = int(round(y0 + (y1 - y0) * s / steps))
            set_px(x, y, col)

    prices = [b["l"] for b in bars] + [b["h"] for b in bars]
    for lvl, _ in hlines:
        if lvl and lvl > 0:
            prices.append(lvl)
    if band:
        prices.extend([band[0], band[1]])

    min_p = min(prices) * 0.994
    max_p = max(prices) * 1.006
    rng = max(max_p - min_p, min_p * 0.01)

    pad_l, pad_r, pad_t, pad_b = 28, 28, 22, 72
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    def y_of(p):
        return int(round(pad_t + ((max_p - p) / rng) * plot_h))

    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = int(pad_t + plot_h * frac)
        draw_line(pad_l, gy, pad_l + plot_w, gy, grid_col, dashed=True)

    if band and band[1] > band[0]:
        fill_rect(pad_l, y_of(band[1]), pad_l + plot_w, y_of(band[0]), zone_col)

    for lvl, col in hlines:
        if lvl and lvl > 0:
            ly = y_of(lvl)
            draw_line(pad_l, ly, pad_l + plot_w, ly, col, dashed=True)

    n = len(bars)
    step = plot_w / n
    bw = max(2, int(step * 0.6))
    max_vol = max((b["v"] for b in bars), default=1.0) or 1.0
    vol_top = pad_t + plot_h + 10
    vol_h = 46

    for idx, b in enumerate(bars):
        cx = int(pad_l + (idx + 0.5) * step)
        col = green if b["c"] >= b["o"] else red
        draw_line(cx, y_of(b["h"]), cx, y_of(b["l"]), col)
        yo, yc = y_of(b["o"]), y_of(b["c"])
        fill_rect(cx - bw // 2, min(yo, yc), cx + bw // 2, max(yo, yc) + 1, col)
        vh = int((b["v"] / max_vol) * vol_h)
        if vh > 0:
            fill_rect(cx - bw // 2, vol_top + vol_h - vh, cx + bw // 2, vol_top + vol_h, col)

    prev_pt = None
    for idx, val in enumerate(sma_slice):
        if val is None:
            prev_pt = None
            continue
        cx = int(pad_l + (idx + 0.5) * step)
        cy = y_of(val)
        if prev_pt is not None:
            draw_line(prev_pt[0], prev_pt[1], cx, cy, amber)
        prev_pt = (cx, cy)

    raw_rows = bytearray()
    stride = width * 3
    for y in range(height):
        raw_rows.append(0)
        raw_rows.extend(buf[y * stride:(y + 1) * stride])

    def png_chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows), level=6)
    return b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr) + png_chunk(b"IDAT", idat) + png_chunk(b"IEND", b"")


# ==============================================================================
# FORMATLAMA & INDIKATORLER
# ==============================================================================
def extract_base_quote(sym):
    for q in ("USDT", "BTC", "BNB"):
        if sym.endswith(q):
            return sym[:-len(q)], q
    return None, None


def is_excluded_pair(sym):
    return any(s in sym for s in EXCLUDED_SUBSTRINGS) or any(sym.startswith(p) for p in EXCLUDED_PREFIXES)


def fmt_price(p):
    if not p or p <= 0:
        return "0"
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 10:
        return f"{p:.3f}".rstrip("0").rstrip(".")
    if p >= 1:
        return f"{p:.4f}".rstrip("0").rstrip(".")
    if p >= 0.01:
        return f"{p:.5f}".rstrip("0").rstrip(".")
    if p >= 0.0001:
        return f"{p:.6f}".rstrip("0").rstrip(".")
    return f"{p:.8f}".rstrip("0").rstrip(".")


def fmt_compact_vol(v):
    if v >= 1_000_000_000:
        return f"{v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}K"
    return f"{v:.0f}"


def compute_sma_series(closes, period=200):
    out = [None] * len(closes)
    if len(closes) < period:
        return out
    s = sum(closes[:period])
    out[period - 1] = s / period
    for i in range(period, len(closes)):
        s += closes[i] - closes[i - period]
        out[i] = s / period
    return out


def compute_rsi_series(closes, period=14):
    out = [50.0] * len(closes)
    if len(closes) < period + 2:
        return out
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d > 0:
            gains += d
        else:
            losses += -d
    ag, al = gains / period, losses / period
    for i in range(period, len(closes)):
        if i > period:
            d = closes[i] - closes[i - 1]
            ag = (ag * (period - 1) + max(d, 0.0)) / period
            al = (al * (period - 1) + max(-d, 0.0)) / period
        out[i] = (100.0 if ag > 0 else 50.0) if al == 0 else (100.0 - 100.0 / (1.0 + ag / al))
    return out


def record_active_signal(sym, engine, direction, entry_price):
    with STATE_LOCK:
        STATE["active_signals"].insert(0, {
            "symbol": sym,
            "engine": engine,
            "direction": direction,
            "entry": entry_price,
            "peak_pct": 0.0,
            "peak_price": entry_price,
            "ts": time.time(),
        })
        STATE["active_signals"] = STATE["active_signals"][:150]
        save_state()


def update_signal_peaks():
    now = time.time()
    changed = False
    with STATE_LOCK:
        kept = []
        for s in STATE["active_signals"]:
            if now - s["ts"] > 86400 * 2:
                continue
            tk = LATEST_TICKERS.get(s["symbol"])
            if tk and tk["price"] > 0:
                cur = tk["price"]
                is_short = s["direction"] in ("SHORT", "BELOW", "ATL")
                pct = ((s["entry"] - cur) / s["entry"]) * 100 if is_short else ((cur - s["entry"]) / s["entry"]) * 100
                if pct > s["peak_pct"]:
                    s["peak_pct"] = round(pct, 2)
                    s["peak_price"] = cur
                    changed = True
            kept.append(s)
        STATE["active_signals"] = kept
        if changed:
            save_state()


# ==============================================================================
# MOTOR 1: 0-7 DK SPOT ANLIK PUMP (SADE FORMAT + COIN BAZLI TEKIL KILIT)
# ==============================================================================
def check_pump(sym, base, quote, price, vol24h, now):
    min_vol = 150_000 if quote == "USDT" else (3.0 if quote == "BTC" else 250.0)
    th_pct = 4.5 if quote == "USDT" else 4.0
    if price <= 0 or vol24h < min_vol:
        return

    hist = PRICE_WINDOW_HIST.setdefault(sym, [])
    hist.append((now, price))
    cutoff = now - 420.0
    while hist and hist[0][0] < cutoff:
        hist.pop(0)
    if len(hist) < 2:
        return

    min_ts, min_p = min(hist, key=lambda x: x[1])
    if min_p <= 0:
        return
    pct = ((price - min_p) / min_p) * 100.0
    if pct < th_pct:
        return

    with STATE_LOCK:
        rec = STATE["pump_locks"].get(base)
        if rec:
            if PUMP_REPEAT_HOURS <= 0 or (now - rec["locked_at"] < PUMP_REPEAT_HOURS * 3600):
                return
        STATE["pump_locks"][base] = {"symbol": sym, "locked_at": now}
        save_state()

    PRICE_WINDOW_HIST.pop(sym, None)
    mins = max(1, round((now - min_ts) / 60.0))
    vol_str = f"{round(vol24h):,}" if quote == "USDT" else f"{vol24h:.2f}"
    msg = (
        f"⚡ <b>SPOT ANLIK PUMP DEDEKTÖRÜ — {sym}</b>\n\n"
        f"📈 Değişim: +{pct:.2f}% ({mins} dk içinde!)\n"
        f"💰 Başlangıç: {fmt_price(min_p)}  ➡️  Anlık: {fmt_price(price)}\n"
        f"💧 24s Parite Hacmi: {vol_str} ({quote})"
    )
    tg_send_message(msg)
    record_active_signal(sym, "PUMP", "LONG", price)


# ==============================================================================
# MOTOR 2, 3, 6, 7: YAPISAL TARAMALAR
# ==============================================================================
def scan_structural_pair(sym, tk):
    cur = tk["price"]
    vol = tk["vol"]
    chg24 = tk["chg24h"]
    low24 = tk["low"]
    now = time.time()
    if vol < 1_500_000 or cur <= 0:
        return

    # 1) MOTOR 6: 1G MA200 (Sadece klasik SMA200 & Kalici Yon Kilidi)
    try:
        c1d = fetch_klines(sym, "1d", 235)
        if len(c1d) >= 203:
            closes = [b["c"] for b in c1d]
            sma200 = compute_sma_series(closes, 200)
            prev_c, last_c = closes[-3], closes[-2]
            ma_prev, ma_last = sma200[-3], sma200[-2]
            if ma_prev and ma_last and ma_last > 0:
                new_pos = "ABOVE" if last_c >= ma_last * 1.0025 else ("BELOW" if last_c <= ma_last * 0.9975 else None)
                if new_pos:
                    crossed = False
                    with STATE_LOCK:
                        prev_lock = STATE["ma200_locks"].get(sym)
                        if not prev_lock:
                            raw_prev = "ABOVE" if prev_c >= ma_prev else "BELOW"
                            STATE["ma200_locks"][sym] = {"direction": new_pos, "ma200": ma_last, "updated_at": now}
                            if raw_prev != new_pos:
                                crossed = True
                            save_state()
                        elif prev_lock["direction"] != new_pos:
                            STATE["ma200_locks"][sym] = {"direction": new_pos, "ma200": ma_last, "updated_at": now}
                            crossed = True
                            save_state()
                    if crossed:
                        diff_pct = ((last_c - ma_last) / ma_last) * 100.0
                        title = f"🟢 <b>1G MA200 YUKARI KIRILIMI — #{sym}</b>" if new_pos == "ABOVE" else f"🔴 <b>1G MA200 AŞAĞI DÜŞÜŞ — #{sym}</b>"
                        desc = (
                            "Günlük kapanış 200 Günlük MA ortalamasının üzerine çıktı."
                            if new_pos == "ABOVE"
                            else "Günlük kapanış 200 Günlük MA ortalamasının altına kırıldı."
                        )
                        cap = (
                            f"{title}\n\n{desc}\n"
                            f"📊 1G Kapanış: ${fmt_price(last_c)} · MA200: ${fmt_price(ma_last)} ({diff_pct:+.2f}%)\n"
                            f"🔒 Ters kırılım gelene kadar tekrar sinyal gönderilmez."
                        )
                        png = render_candlestick_png(c1d[-90:], sma_series=sma200)
                        tg_send_photo(png, cap)
                        record_active_signal(sym, "MA200", new_pos, last_c)
    except Exception:
        pass

    # 2) MOTOR 7: PABS Spot Alim Bolgesi (Dongu Kilitli)
    try:
        with STATE_LOCK:
            pabs_rec = STATE["pabs_locks"].get(sym)
            if pabs_rec and cur > pabs_rec["unlock_above"]:
                del STATE["pabs_locks"][sym]
                save_state()
                pabs_rec = None
        if not pabs_rec and vol >= 2_000_000:
            c1h = fetch_klines(sym, "1h", 120)
            if len(c1h) >= 80:
                bars = c1h[-72:]
                rsis = compute_rsi_series([b["c"] for b in c1h], 14)
                rsi_now, rsi_prev = rsis[-2], rsis[-3]
                recent_high = max(b["h"] for b in bars[:-3])
                recent_low = min(b["l"] for b in bars[-24:])
                dd_pct = ((recent_high - cur) / recent_high) * 100.0
                dist_low = ((cur - recent_low) / recent_low) * 100.0
                last_b = c1h[-2]
                bull_react = last_b["c"] > last_b["o"] or cur > last_b["c"]
                if 3.5 <= dd_pct <= 22.0 and 0.0 <= dist_low <= 4.5 and 22.0 <= rsi_now <= 44.0 and rsi_now >= rsi_prev - 0.5 and bull_react:
                    buy_low = recent_low * 0.992
                    buy_high = max(cur * 1.008, recent_low * 1.032)
                    mid_buy = (buy_low + buy_high) / 2.0
                    stop_loss = buy_low * 0.962
                    risk_pct = max(3, round(((mid_buy - stop_loss) / mid_buy) * 100.0))
                    tp1 = max(cur * 1.048, buy_high + (recent_high - buy_high) * 0.45)
                    tp2 = max(tp1 * 1.038, recent_high * 0.995)
                    tp3 = max(tp2 * 1.04, recent_high * 1.035)
                    tp4 = max(tp3 * 1.045, recent_high * 1.078)
                    avg_profit = (((tp2 + tp3) / 2.0 - mid_buy) / mid_buy) * 100.0
                    with STATE_LOCK:
                        STATE["pabs_seq"] += 1
                        seq_no = STATE["pabs_seq"]
                        STATE["pabs_locks"][sym] = {"unlock_above": recent_high * 1.005, "locked_at": now}
                        save_state()
                    base, _ = extract_base_quote(sym)
                    cap = (
                        f"Yeni #{sym} spot sinyali <b>#{seq_no}</b> (PABS)\n"
                        f"24s Hacim: {fmt_compact_vol(vol)} USDT\n\n"
                        f"Ort: tepeden -{dd_pct:.1f}% geri çekilme, hedef kâr +{avg_profit:.2f}%. Risk: %{risk_pct}\n"
                        f"Alım Bölgesi : {fmt_price(buy_low)} – {fmt_price(buy_high)}\n"
                        f"Altcoin #{base}\n"
                        f"Zarar Kes (Stop): {fmt_price(stop_loss)}\n"
                        f"HEDEFLER\n"
                        f"TP1 : {fmt_price(tp1)}\n"
                        f"TP2 : {fmt_price(tp2)}\n"
                        f"TP3 : {fmt_price(tp3)}\n"
                        f"TP4 : {fmt_price(tp4)}"
                    )
                    png = render_candlestick_png(
                        c1h[-72:],
                        hlines=[(tp1, (16, 185, 129)), (stop_loss, (244, 63, 94))],
                        band=(buy_low, buy_high),
                    )
                    tg_send_photo(png, cap)
                    record_active_signal(sym, "PABS", "LONG", cur)
    except Exception:
        pass

    # 3) MOTOR 3: 4h Akis / Emir Defteri Duvari & POC (1D + 3D Trend Onayli & LONG<->SHORT Kilitli)
    try:
        if vol >= 2_500_000:
            depth = binance_get(f"/api/v3/depth?symbol={sym}&limit=100", timeout=7)
            c15m = fetch_klines(sym, "15m", 96)
            c4h = fetch_klines(sym, "4h", 56)
            c1d = fetch_klines(sym, "1d", 12)
            c3d = fetch_klines(sym, "3d", 12)
            if len(c15m) >= 40 and len(c4h) >= 28 and len(c1d) >= 7 and len(c3d) >= 5:
                ma1d = sum(b["c"] for b in c1d[-7:]) / 7.0
                ma3d = sum(b["c"] for b in c3d[-5:]) / 5.0
                trend_1d = "LONG" if cur >= ma1d else "SHORT"
                trend_3d = "LONG" if cur >= ma3d else "SHORT"

                d_lo = min(b["l"] for b in c15m)
                d_hi = max(b["h"] for b in c15m)
                if d_hi > d_lo:
                    buckets = 40
                    step_p = (d_hi - d_lo) / buckets
                    hist = [0.0] * buckets
                    for b in c15m:
                        mid = (b["h"] + b["l"] + b["c"]) / 3.0
                        idx = min(buckets - 1, max(0, int((mid - d_lo) / step_p)))
                        hist[idx] += b["qv"]
                    poc_idx = max(range(buckets), key=lambda i: hist[i])
                    poc_price = d_lo + (poc_idx + 0.5) * step_p

                    best_bid_p, best_bid_usd = 0.0, 0.0
                    best_ask_p, best_ask_usd = 0.0, 0.0
                    for ps, qs in depth.get("bids", []):
                        p = float(ps)
                        usd = p * float(qs)
                        if abs(cur - p) / cur <= 0.025 and usd > best_bid_usd:
                            best_bid_p, best_bid_usd = p, usd
                    for ps, qs in depth.get("asks", []):
                        p = float(ps)
                        usd = p * float(qs)
                        if abs(p - cur) / cur <= 0.025 and usd > best_ask_usd:
                            best_ask_p, best_ask_usd = p, usd

                    bias = None
                    wall_p, wall_usd, wall_lbl = 0.0, 0.0, ""
                    if best_ask_usd >= 120_000 and cur <= best_ask_p * 1.004 and best_ask_usd >= best_bid_usd * 1.15:
                        bias = "SHORT"
                        wall_p, wall_usd, wall_lbl = best_ask_p, best_ask_usd, "Ask (Satış) Duvarı"
                    elif best_bid_usd >= 120_000 and cur >= best_bid_p * 0.996 and best_bid_usd >= best_ask_usd * 1.15:
                        bias = "LONG"
                        wall_p, wall_usd, wall_lbl = best_bid_p, best_bid_usd, "Bid (Alış) Duvarı"

                    # Hem 1D hem de 3D grafik ayni yonu dogrulamalidir
                    if bias and bias == trend_1d and bias == trend_3d:
                        with STATE_LOCK:
                            locked_dir = STATE["flow_locks"].get(sym, {}).get("direction")
                            allow = locked_dir != bias
                            if allow:
                                STATE["flow_locks"][sym] = {"direction": bias, "locked_at": now}
                                save_state()
                        if allow:
                            sw_hi = max(b["h"] for b in c4h[-28:])
                            sw_lo = min(b["l"] for b in c4h[-28:])
                            rng = max(sw_hi - sw_lo, cur * 0.03)
                            tp1 = min(cur * 0.97, cur - rng * 0.25) if bias == "SHORT" else max(cur * 1.03, cur + rng * 0.25)
                            tp2 = min(cur * 0.948, cur - rng * 0.45) if bias == "SHORT" else max(cur * 1.052, cur + rng * 0.45)
                            tp3 = tp2 * 0.975 if bias == "SHORT" else tp2 * 1.025
                            stop_tight = max(wall_p * 1.005, cur * 1.008) if bias == "SHORT" else min(wall_p * 0.995, cur * 0.992)
                            trigger = cur * 0.992 if bias == "SHORT" else cur * 1.008
                            exchanges = fetch_multi_exchange_sync(sym, cur)
                            base, _ = extract_base_quote(sym)
                            pct = lambda t: f"{((t - cur) / cur) * 100:+.1f}%"
                            wall_note = (
                                "Şu an fiyatı yukarı sıçramasını tutuyor."
                                if bias == "SHORT"
                                else "Şu an fiyatın aşağı sarkmasını tutuyor."
                            )
                            pos_note = (
                                "Fiyat zaten bu seviyenin altında, spot dengesizlik zayıf (Spot akışı yönü destekliyor)."
                                if bias == "SHORT"
                                else "Fiyat zaten bu seviyenin üstünde, spot alım dengesizliği güçlü (Spot akışı yönü destekliyor)."
                            )
                            cap = (
                                f"<b>#{sym} ({bias})</b>\n\n"
                                f"Fiyat: {fmt_price(cur)}\n"
                                f"Hedefler: {fmt_price(tp1)} ({pct(tp1)}) → {fmt_price(tp2)} ({pct(tp2)}) → {fmt_price(tp3)} ({pct(tp3)})\n"
                                f"İptal: {fmt_price(stop_tight)} {'üstünde' if bias == 'SHORT' else 'altında'} ({pct(stop_tight)})\n\n"
                                f"{base} iki seviye arasında sıkışmış durumda ancak duvarlardan biri gerçekten tutuyor. "
                                f"{fmt_price(wall_p)} seviyesindeki {wall_lbl} — tek bir kademede yoğunlaşmış ${round(wall_usd / 1000)}k emir bulunuyor.\n\n"
                                f"{wall_note} Günlük hacim merkezi (POC) {fmt_price(poc_price)} bölgesinde konumlanıyor.\n\n"
                                f"{pos_note} Çoklu borsa akışı senkronize ({', '.join(exchanges)}), ayrışma yok. "
                                f"Günlük (1D) ve 3 Günlük (3D) grafik aynı yönü gösteriyor — bu bir karşı-trend değil, trend yönünde akış.\n\n"
                                f"Anahtar tetik: {fmt_price(trigger)} {'altında' if bias == 'SHORT' else 'üstünde'} kapanış/kalıcılık.\n"
                                f"🔒 Yön kilidi aktif: Ters yöne dönmeden aynı coinden tekrar sinyal gelmez."
                            )
                            png = render_candlestick_png(
                                c4h[-56:],
                                hlines=[(tp1, (16, 185, 129)), (tp2, (16, 185, 129)), (stop_tight, (244, 63, 94)), (poc_price, (245, 158, 11))],
                            )
                            tg_send_photo(png, cap)
                            record_active_signal(sym, "FLOW", bias, cur)
    except Exception:
        pass

    # 4) MOTOR 2: W1 ATL (O anki tum zamanlarin en dusugunun altina inmedikce tekrar gelmez)
    try:
        cached = ATL_WEEKLY_CACHE.get(sym)
        if not cached or now - cached[1] > 3600:
            c1w = fetch_klines(sym, "1w", 1000)
            if len(c1w) >= 12:
                prev_atl = min(b["l"] for b in c1w[:-1])
                cached = (prev_atl, now)
                ATL_WEEKLY_CACHE[sym] = cached
        if cached and cached[0] > 0:
            prev_atl = cached[0]
            with STATE_LOCK:
                atl_rec = STATE["atl_locks"].get(sym)
                floor_atl = min(prev_atl, atl_rec["atl"]) if atl_rec else prev_atl
                broke_strict = (cur < floor_atl * 0.995) if atl_rec else (cur < floor_atl)
                if broke_strict and low24 < prev_atl:
                    STATE["atl_locks"][sym] = {"atl": cur, "locked_at": now}
                    save_state()
                    fire_atl = True
                else:
                    fire_atl = False
            if fire_atl:
                msg = (
                    f"#{sym} ${fmt_price(cur)} tüm zamanların en düşüğü 👎📛😭\n"
                    f"24s {chg24:+.2f}% Hacim {fmt_compact_vol(vol)}"
                )
                tg_send_message(msg)
                record_active_signal(sym, "ATL", "ATL", cur)
    except Exception:
        pass


# ==============================================================================
# MOTOR 8: BINANCE RESMI DUYURU TAKIBI
# ==============================================================================
def translate_tr(title):
    pairs = [
        ("Binance Will List", "Binance Listeliyor:"),
        ("Binance Will Add", "Binance Ekliyor:"),
        ("Binance Will Support", "Binance Destekliyor:"),
        ("Binance Has Completed the", "Binance Tamamladı:"),
        ("Token Merge", "Token Birleşmesi (Merge)"),
        ("Contract Swap", "Kontrat Değişimi (Swap)"),
        ("Withdrawals Reopened", "Çekim İşlemleri Yeniden Açıldı"),
        ("Binance Will Delist", "Binance Delist Ediyor:"),
    ]
    out = title.strip()
    for eng, tr in pairs:
        out = re.sub(re.escape(eng), tr, out, flags=re.IGNORECASE)
    return out


def poll_binance_news():
    global NEWS_SEEDED
    for cat_id, label in [(48, "Yeni Listeleme"), (49, "Binance Duyuru"), (161, "Delist / Kaldırma")]:
        try:
            url = f"https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query?catalogId={cat_id}&pageNo=1&pageSize=6"
            data = http_get_json(url, timeout=8)
            for art in data.get("data", {}).get("articles", []):
                aid = str(art.get("id") or art.get("code") or art.get("title") or "")
                if not aid:
                    continue
                with STATE_LOCK:
                    if aid in STATE["seen_news_ids"]:
                        continue
                    STATE["seen_news_ids"].insert(0, aid)
                    STATE["seen_news_ids"] = STATE["seen_news_ids"][:300]
                    save_state()
                if NEWS_SEEDED:
                    title_tr = translate_tr(art.get("title", ""))
                    code = art.get("code", "")
                    link = f"https://www.binance.com/en/support/announcement/detail/{code}" if code else "https://www.binance.com/en/support/announcement"
                    tg_send_message(f"📰 <b>Binance | {label}</b>\n{title_tr}\n{link}")
        except Exception:
            pass
    NEWS_SEEDED = True


# ==============================================================================
# MOTOR 4 & 5: GUNUN ZAFERI (21:00 TR) & TOP 24 SAAT (23:00 TR)
# ==============================================================================
def check_daily_reports():
    now_tr = datetime.now(TR_TZ)
    day_key = now_tr.strftime("%Y-%m-%d")
    with STATE_LOCK:
        top = sorted(STATE["active_signals"], key=lambda s: s["peak_pct"], reverse=True)[:10]
        if now_tr.hour == 21 and STATE["last_victory_day"] != day_key and top:
            STATE["last_victory_day"] = day_key
            save_state()
            best = top[0]
            tg_send_message(
                f"🏆 <b>GÜNÜN ZAFERİ — #{best['symbol']}</b>\n"
                f"Motor: {best['engine']} ({best['direction']})\n"
                f"Giriş: ${fmt_price(best['entry'])} ➡️ Zirve: ${fmt_price(best['peak_price'])} (<b>+{best['peak_pct']:.2f}%</b>)"
            )
        if now_tr.hour == 23 and STATE["last_top24_day"] != day_key and top:
            STATE["last_top24_day"] = day_key
            save_state()
            lines = [f"{i + 1}. #{x['symbol']} ({x['engine']}) +{x['peak_pct']:.2f}%" for i, x in enumerate(top)]
            tg_send_message("🔥 <b>SON 24 SAAT TOP HAREKETLER</b>\n\n" + "\n".join(lines))


# ==============================================================================
# RENDER HEALTH SERVER & ANA DONGU
# ==============================================================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def main():
    load_state()
    srv = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"Spot Akis Botu baslatildi (Port {PORT}, Chat {CHAT_ID})")

    poll_binance_news()
    last_news = time.time()
    cursor = 0

    while True:
        try:
            tickers = binance_get("/api/v3/ticker/24hr", timeout=10)
            now = time.time()
            usdt_liquid = []
            for item in tickers:
                sym = item.get("symbol", "")
                if not sym or is_excluded_pair(sym):
                    continue
                base, quote = extract_base_quote(sym)
                if not base:
                    continue
                price = float(item.get("lastPrice", 0) or 0)
                vol = float(item.get("quoteVolume", 0) or 0)
                chg24 = float(item.get("priceChangePercent", 0) or 0)
                low24 = float(item.get("lowPrice", 0) or 0)
                high24 = float(item.get("highPrice", 0) or 0)
                if price <= 0:
                    continue
                LATEST_TICKERS[sym] = {"price": price, "chg24h": chg24, "vol": vol, "low": low24, "high": high24}
                check_pump(sym, base, quote, price, vol, now)
                if quote == "USDT" and vol >= 1_500_000:
                    usdt_liquid.append((sym, LATEST_TICKERS[sym]))

            update_signal_peaks()
            usdt_liquid.sort(key=lambda x: x[1]["vol"], reverse=True)
            top_liquid = usdt_liquid[:50]
            if top_liquid:
                for _ in range(4):
                    sym, tk = top_liquid[cursor % len(top_liquid)]
                    cursor += 1
                    scan_structural_pair(sym, tk)

            if now - last_news > 60:
                poll_binance_news()
                check_daily_reports()
                last_news = now
        except Exception as e:
            print("Dongu hatasi:", e)
        time.sleep(3)


if __name__ == "__main__":
    main()
