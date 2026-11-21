#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spot Akis Terminali - Bagimsiz Render Telegram Botu (Sifir Harici Kutuphane)
Motorlar:
  1) 0-7 dk Spot Anlik Pump (Tekil Coin Kilidi + Canli 1m Kline Dogrulama)
  2) W1 ATL (Tum Zamanlarin En Dusugu - Siki Taban Kilidi)
  3) 4h Akis / Emir Defteri Duvari >=$120K & 24s POC (1D + 3D Trend Onayi)
  4) & 5) Gunun Zaferi (21:00 TR) & TOP 24 Saat (23:00 TR) Canli Pik Raporlari
  6) 1G MA200 Kirilimi (Klasik SMA200 - Sessiz Tohumlama + Canli Kirilim)
  7) StormGain PRO (365G YEAR & 30G MONTH Max/Min + RVOL + 4h MACD/RSI + Taker Baskisi + 48s Kilit)
  8) Parabolik Tukenme / Pump Tepesi Donusu (EXHAUST - 15dk RSI/Fitil/Taker + Siki Stop + 24s Kilit)
  9) Binance Resmi Duyurular & Haberler (48, 49, 161)
"""

import os
import sys
import json
import time
import math
import zlib
import struct
import threading
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()
PORT = int(os.environ.get("PORT", "10000"))
PUMP_REPEAT_HOURS = float(os.environ.get("PUMP_REPEAT_HOURS", "0"))
STATE_FILE = "/tmp/spot_bot_state.json"

BINANCE_MIRRORS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://data-api.binance.vision",
]

TR_TZ = timezone(timedelta(hours=3))

state_lock = threading.Lock()
ticker_map = {}
price_window_history = {}
signals_today = []
atl_weekly_cache = {}
news_seen_ids = set()
news_initial_seeded = False
report_sent_days = {"victory": "", "top24": ""}

locks = {
    "pump": {},
    "atl": {},
    "flow": {},
    "ma200": {},
    "month": {},
    "year": {},
    "exhaust": {},
}

# ==============================================================================
# SIFIR BAGIMLILIKLI PNG MUM GRAFIGI MOTORU (5x7 BITMAP FONT + ZLIB)
# ==============================================================================
FONT_5X7 = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00110", "01000", "10000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
    "D": ["11100", "10010", "10001", "10001", "10001", "10010", "11100"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01110", "10001", "10000", "10111", "10001", "10001", "01110"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["01110", "00100", "00100", "00100", "00100", "00100", "01110"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    ",": ["00000", "00000", "00000", "00000", "01100", "01100", "01000"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    "%": ["11001", "11010", "00010", "00100", "01000", "01011", "10011"],
    "/": ["00001", "00010", "00100", "00100", "01000", "10000", "00000"],
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}


def _png_chunk(ctype: bytes, data: bytes) -> bytes:
    return (
        struct.pack("!I", len(data))
        + ctype
        + data
        + struct.pack("!I", zlib.crc32(ctype + data) & 0xFFFFFFFF)
    )


def _draw_text(buf, w, h, x, y, text, color, scale=1):
    cx = x
    for ch in text.upper():
        glyph = FONT_5X7.get(ch, FONT_5X7[" "])
        for ry, row in enumerate(glyph):
            for rx, bit in enumerate(row):
                if bit == "1":
                    for sy in range(scale):
                        for sx in range(scale):
                            px, py = cx + rx * scale + sx, y + ry * scale + sy
                            if 0 <= px < w and 0 <= py < h:
                                idx = (py * w + px) * 3
                                buf[idx : idx + 3] = color
        cx += 6 * scale


def _fill_rect(buf, w, h, x0, y0, x1, y1, color):
    x0, x1 = max(0, min(x0, x1)), min(w - 1, max(x0, x1))
    y0, y1 = max(0, min(y0, y1)), min(h - 1, max(y0, y1))
    for py in range(y0, y1 + 1):
        row_off = py * w * 3
        for px in range(x0, x1 + 1):
            idx = row_off + px * 3
            buf[idx : idx + 3] = color


def _draw_line(buf, w, h, x0, y0, x1, y1, color, dashed=False):
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    step = 0
    while True:
        if not dashed or (step // 4) % 2 == 0:
            if 0 <= x0 < w and 0 <= y0 < h:
                idx = (y0 * w + x0) * 3
                buf[idx : idx + 3] = color
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
        step += 1


def render_candlestick_png(symbol, interval_label, candles, levels=None, ma_series=None, header_note=""):
    w, h = 920, 500
    bg = bytes((11, 15, 23))
    grid_col = bytes((24, 32, 46))
    text_col = bytes((226, 232, 240))
    muted_col = bytes((148, 163, 184))
    green_col = bytes((34, 197, 94))
    red_col = bytes((239, 68, 68))
    ma_col = bytes((56, 189, 248))

    buf = bytearray(bg * (w * h))
    pad_l, pad_r, pad_t, pad_b = 20, 140, 48, 30
    cw = w - pad_l - pad_r
    ch = h - pad_t - pad_b

    bars = candles[-80:] if len(candles) > 80 else candles
    if not bars:
        return b""
    ma_tail = ma_series[-len(bars) :] if ma_series and len(ma_series) >= len(bars) else None

    lows = [b["low"] for b in bars]
    highs = [b["high"] for b in bars]
    if levels:
        for _, val, _ in levels:
            if val and val > 0:
                lows.append(val)
                highs.append(val)
    if ma_tail:
        for v in ma_tail:
            if v is not None and v > 0:
                lows.append(v)
                highs.append(v)

    p_min = min(lows)
    p_max = max(highs)
    if p_max <= p_min:
        p_max = p_min * 1.01
    pad = (p_max - p_min) * 0.08
    p_min -= pad
    p_max += pad

    def py_of(price):
        ratio = (price - p_min) / max(p_max - p_min, 1e-12)
        return int(pad_t + ch - ratio * ch)

    for i in range(5):
        gy = int(pad_t + (ch * i) / 4)
        _draw_line(buf, w, h, pad_l, gy, w - pad_r, gy, grid_col, dashed=True)
        gp = p_max - ((p_max - p_min) * i) / 4
        _draw_text(buf, w, h, w - pad_r + 8, gy - 3, fmt_price(gp), muted_col, 1)

    title = f"{symbol}  {interval_label.upper()}  {header_note}".strip()
    _draw_text(buf, w, h, pad_l, 16, title[:62], text_col, 2)

    n = len(bars)
    step = cw / max(n, 1)
    body_w = max(2, int(step * 0.62))
    for i, b in enumerate(bars):
        cx = int(pad_l + (i + 0.5) * step)
        col = green_col if b["close"] >= b["open"] else red_col
        yh = py_of(b["high"])
        yl = py_of(b["low"])
        yo = py_of(b["open"])
        yc = py_of(b["close"])
        _draw_line(buf, w, h, cx, yh, cx, yl, col, dashed=False)
        top_b, bot_b = min(yo, yc), max(yo, yc)
        if bot_b == top_b:
            bot_b += 1
        _fill_rect(buf, w, h, cx - body_w // 2, top_b, cx + body_w // 2, bot_b, col)

    if ma_tail:
        prev_pt = None
        for i, mv in enumerate(ma_tail):
            if mv is None:
                continue
            cx = int(pad_l + (i + 0.5) * step)
            cy = py_of(mv)
            if prev_pt:
                _draw_line(buf, w, h, prev_pt[0], prev_pt[1], cx, cy, ma_col, dashed=False)
            prev_pt = (cx, cy)

    if levels:
        used_y = []
        for tag, val, rgb in levels:
            if not val or val <= 0:
                continue
            ly = max(pad_t + 6, min(pad_t + ch - 6, py_of(val)))
            col = bytes(rgb)
            _draw_line(buf, w, h, pad_l, ly, w - pad_r, ly, col, dashed=True)
            ty = ly
            for uy in used_y:
                if abs(ty - uy) < 16:
                    ty += 16
            used_y.append(ty)
            _fill_rect(buf, w, h, w - pad_r + 4, ty - 7, w - 6, ty + 7, col)
            lbl = f"{tag} {fmt_price(val)}"
            _draw_text(buf, w, h, w - pad_r + 8, ty - 3, lbl[:18], bytes((11, 15, 23)), 1)

    raw_rows = bytearray()
    stride = w * 3
    for row in range(h):
        raw_rows.append(0)
        raw_rows.extend(buf[row * stride : (row + 1) * stride])

    ihdr = struct.pack("!IIBBBBB", w, h, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows), 6)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


# ==============================================================================
# YARDIMCI HESAPLAMALAR VE KALICILIK
# ==============================================================================
def extract_base_quote(sym: str):
    for q in ("USDT", "BTC", "BNB"):
        if sym.endswith(q):
            return sym[: -len(q)], q
    return None


def is_excluded_pair(sym: str) -> bool:
    return (
        "UPUSDT" in sym
        or "DOWNUSDT" in sym
        or "BULLUSDT" in sym
        or "BEARUSDT" in sym
        or sym.startswith(("USDC", "FDUSD", "TUSD", "USDP", "EUR"))
    )


def fmt_price(p: float) -> str:
    if not math.isfinite(p) or p <= 0:
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
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses += -diff
    avg_g, avg_l = gains / period, losses / period
    for i in range(period, len(closes)):
        if i > period:
            diff = closes[i] - closes[i - 1]
            avg_g = (avg_g * (period - 1) + max(diff, 0.0)) / period
            avg_l = (avg_l * (period - 1) + max(-diff, 0.0)) / period
        if avg_l == 0:
            out[i] = 100.0 if avg_g > 0 else 50.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + avg_g / avg_l)
    return out


def compute_ema_series(values, period):
    if not values:
        return []
    k = 2.0 / (period + 1)
    out = [values[0]]
    for i in range(1, len(values)):
        out.append(values[i] * k + out[i - 1] * (1.0 - k))
    return out


def compute_macd_last(closes):
    if len(closes) < 35:
        return {"macd": 0.0, "signal": 0.0, "hist": 0.0, "prev_hist": 0.0}
    ema12 = compute_ema_series(closes, 12)
    ema26 = compute_ema_series(closes, 26)
    macd_line = [ema12[i] - ema26[i] for i in range(len(closes))]
    sig_line = compute_ema_series(macd_line, 9)
    hist = macd_line[-1] - sig_line[-1]
    prev_hist = macd_line[-2] - sig_line[-2]
    return {"macd": macd_line[-1], "signal": sig_line[-1], "hist": hist, "prev_hist": prev_hist}


def save_state():
    try:
        with state_lock:
            payload = {"locks": locks, "signals_today": signals_today[-250:]}
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


def load_state():
    global signals_today
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw.get("locks"), dict):
            for k in ("pump", "atl", "flow", "ma200", "year", "exhaust"):
                if isinstance(raw["locks"].get(k), dict):
                    locks[k] = raw["locks"][k]
        if isinstance(raw.get("signals_today"), list):
            signals_today = raw["signals_today"][-250:]
    except Exception:
        pass


# ==============================================================================
# HTTP / BINANCE / COKLU BORSA & TELEGRAM ILETISIM
# ==============================================================================
def http_get_json(url: str, timeout=8.0):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; SpotFlowBot/2.0)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_binance_json(path_and_query: str, timeout=8.0):
    last_err = None
    for m in BINANCE_MIRRORS:
        try:
            return http_get_json(m + path_and_query, timeout=timeout)
        except Exception as e:
            last_err = e
    raise last_err or RuntimeError("Binance mirrors unreachable")


def fetch_spot_klines(symbol: str, interval: str, limit=240):
    raw = fetch_binance_json(
        f"/api/v3/klines?symbol={urllib.parse.quote(symbol)}&interval={urllib.parse.quote(interval)}&limit={limit}",
        timeout=9.0,
    )
    out = []
    if not isinstance(raw, list):
        return out
    for k in raw:
        out.append(
            {
                "open_time": int(float(k[0]) / 1000),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "quote_vol": float(k[7]),
                "taker_buy_quote": float(k[10]),
            }
        )
    return out


def fetch_multi_exchange_sync(symbol: str, binance_price: float):
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    exchanges = ["Binance"]
    checks = [
        ("Bybit", f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}", lambda d: float(d["result"]["list"][0]["lastPrice"])),
        ("OKX", f"https://www.okx.com/api/v5/market/ticker?instId={base}-USDT", lambda d: float(d["data"][0]["last"])),
        ("Gate", f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={base}_USDT", lambda d: float(d[0]["last"])),
    ]
    for name, url, parser in checks:
        try:
            p = parser(http_get_json(url, timeout=3.5))
            if p > 0 and abs(p - binance_price) / binance_price < 0.02:
                exchanges.append(name)
        except Exception:
            pass
    return exchanges


def fetch_futures_context_line(symbol: str):
    if not symbol.endswith("USDT"):
        return None
    parts = []
    try:
        oi_data = http_get_json(f"https://fapi.binance.com/fapi/v1/openInterest?symbol={urllib.parse.quote(symbol)}", timeout=2.5)
        oi_val = float(oi_data.get("openInterest") or 0)
        if oi_val > 0:
            parts.append(f"OI: {round(oi_val):,}")
    except Exception:
        pass
    try:
        top_data = http_get_json(f"https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={urllib.parse.quote(symbol)}&period=1h&limit=1", timeout=2.5)
        if isinstance(top_data, list) and top_data:
            l_pct = round(float(top_data[0].get("longAccount") or 0) * 100)
            if l_pct > 0:
                parts.append(f"Balina: %{l_pct}L/%{100 - l_pct}S")
    except Exception:
        pass
    try:
        gls_data = http_get_json(f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={urllib.parse.quote(symbol)}&period=1h&limit=1", timeout=2.5)
        if isinstance(gls_data, list) and gls_data:
            l_pct = round(float(gls_data[0].get("longAccount") or 0) * 100)
            if l_pct > 0:
                parts.append(f"Kalabalık: %{l_pct}L/%{100 - l_pct}S")
    except Exception:
        pass
    return " · ".join(parts) if parts else None


def tg_send_text(text: str, chat_id: str = None):
    if not BOT_TOKEN:
        return
    target = chat_id or CHAT_ID
    try:
        body = json.dumps(
            {
                "chat_id": target,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as _:
            pass
    except Exception:
        pass


def tg_send_photo(caption: str, png_bytes: bytes, chat_id: str = None):
    if not BOT_TOKEN:
        return
    if not png_bytes:
        tg_send_text(caption, chat_id=chat_id)
        return
    target = chat_id or CHAT_ID
    boundary = f"----SpotBotBoundary{int(time.time() * 1000)}"
    parts = []
    for name, val in [("chat_id", target), ("caption", caption[:1000]), ("parse_mode", "HTML")]:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n".encode("utf-8")
        )
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"chart.png\"\r\nContent-Type: image/png\r\n\r\n".encode("utf-8")
        + png_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as _:
            pass
    except Exception:
        tg_send_text(caption, chat_id=target)


# ==============================================================================
# MOTOR 1: 0-7 DK SPOT ANLIK PUMP (TEKIL COIN KILIDI)
# ==============================================================================
def is_pump_locked(base: str, now_ms: int) -> bool:
    rec = locks["pump"].get(base)
    if not rec:
        return False
    if PUMP_REPEAT_HOURS <= 0:
        return True
    return (now_ms - rec["lockedAt"]) < PUMP_REPEAT_HOURS * 3600 * 1000


def _verify_and_emit_pump(tk, min_pt):
    sym = tk["symbol"]
    base = tk["base"]
    quote = tk["quote"]
    th_pct = 4.5 if quote == "USDT" else 4.0
    try:
        fresh_tk = fetch_binance_json(f"/api/v3/ticker/24hr?symbol={urllib.parse.quote(sym)}", timeout=4.0)
        c1m = fetch_spot_klines(sym, "1m", 10)
        live_cur = float(fresh_tk.get("lastPrice") or tk["price"])
        live_vol = float(fresh_tk.get("quoteVolume") or tk["volume24h"])
        bar_min = min((b["low"] for b in c1m[-7:]), default=min_pt[1])
        verified_min = min(min_pt[1], bar_min if bar_min > 0 else min_pt[1])
        verified_pct = ((live_cur - verified_min) / verified_min) * 100.0
        if verified_pct < th_pct:
            with state_lock:
                locks["pump"].pop(base, None)
            return
        with state_lock:
            price_window_history.pop(sym, None)
            now_ms = int(time.time() * 1000)
            signals_today.append(
                {
                    "engine": "PUMP",
                    "symbol": sym,
                    "dir": "LONG",
                    "entry": live_cur,
                    "peak_pct": 0.0,
                    "ts": now_ms,
                }
            )
        mins = max(1, round((time.time() * 1000 - min_pt[0]) / 60000))
        vol_str = f"{round(live_vol):,}" if quote == "USDT" else f"{live_vol:.2f}"
        caption = (
            f"⚡ <b>SPOT ANLIK PUMP DEDEKTÖRÜ — {sym}</b>\n\n"
            f"📈 Değişim: <b>+{verified_pct:.2f}%</b> ({mins} dk içinde!)\n"
            f"💰 Başlangıç: <code>{fmt_price(verified_min)}</code>  ➡️  Anlık: <code>{fmt_price(live_cur)}</code>\n"
            f"💧 24s Parite Hacmi: {vol_str} ({quote})"
        )
        png = render_candlestick_png(
            sym,
            "1DK",
            c1m,
            levels=[("BAS", verified_min, (250, 204, 21)), ("ANLIK", live_cur, (34, 197, 94))],
            header_note=f"+{verified_pct:.2f}%",
        )
        tg_send_photo(caption, png)
        save_state()
    except Exception:
        with state_lock:
            locks["pump"].pop(base, None)


def evaluate_pump_for_ticker(tk, now_ms: int):
    quote = tk["quote"]
    min_vol = 150_000 if quote == "USDT" else (3 if quote == "BTC" else 250)
    th_pct = 4.5 if quote == "USDT" else 4.0
    if tk["price"] <= 0 or tk["volume24h"] < min_vol:
        return
    sym = tk["symbol"]
    hist = price_window_history.setdefault(sym, [])
    hist.append((now_ms, tk["price"]))
    cutoff = now_ms - 420_000
    while hist and hist[0][0] < cutoff:
        hist.pop(0)
    if len(hist) < 2:
        return
    min_pt = min(hist, key=lambda x: x[1])
    if min_pt[1] <= 0:
        return
    pct = ((tk["price"] - min_pt[1]) / min_pt[1]) * 100.0
    if pct < th_pct or is_pump_locked(tk["base"], now_ms):
        return
    locks["pump"][tk["base"]] = {"base": tk["base"], "symbol": sym, "lockedAt": now_ms}
    threading.Thread(target=_verify_and_emit_pump, args=(dict(tk), min_pt), daemon=True).start()


# ==============================================================================
# MOTOR 2, 3, 6, 7, EXHAUST: YAPISAL & YON KILITLI TARAMA
# ==============================================================================
def scan_structural_pair(tk):
    if tk["quote"] != "USDT" or tk["volume24h"] < 1_500_000:
        return
    sym = tk["symbol"]
    cur = tk["price"]
    now_ms = int(time.time() * 1000)

    # 1) MOTOR 6: 1G MA200 (Klasik SMA200 - Sessiz tohumlama + Anlik kirilim)
    try:
        c1d = fetch_spot_klines(sym, "1d", 370)
        if len(c1d) >= 203:
            closes = [b["close"] for b in c1d]
            closes[-1] = cur
            sma200 = compute_sma_series(closes, 200)
            ma_live = sma200[-1]
            if ma_live and ma_live > 0:
                new_pos = "ABOVE" if cur >= ma_live * 1.004 else ("BELOW" if cur <= ma_live * 0.996 else None)
                if new_pos:
                    crossed = False
                    with state_lock:
                        prev_lock = locks["ma200"].get(sym)
                        if not prev_lock:
                            locks["ma200"][sym] = {"symbol": sym, "direction": new_pos, "ma200": ma_live, "updatedAt": now_ms}
                        elif prev_lock["direction"] != new_pos:
                            locks["ma200"][sym] = {"symbol": sym, "direction": new_pos, "ma200": ma_live, "updatedAt": now_ms}
                            crossed = True
                    if crossed:
                        save_state()
                        diff_pct = ((cur - ma_live) / ma_live) * 100.0
                        with state_lock:
                            signals_today.append({"engine": "MA200", "symbol": sym, "dir": new_pos, "entry": cur, "peak_pct": 0.0, "ts": now_ms})
                        title = f"🟢 <b>1G MA200 YUKARI KIRILIMI — #{sym}</b>" if new_pos == "ABOVE" else f"🔴 <b>1G MA200 AŞAĞI DÜŞÜŞ — #{sym}</b>"
                        caption = (
                            f"{title}\n"
                            f"💰 Anlık Fiyat: <code>${fmt_price(cur)}</code> · Canlı MA200: <code>${fmt_price(ma_live)}</code> ({diff_pct:+.2f}%)\n"
                            f"🔒 Kilit aktif: Ters kırılım gelene kadar tekrar sinyal üretilmez."
                        )
                        png = render_candlestick_png(
                            sym,
                            "1G MA200",
                            c1d[-80:],
                            levels=[("MA200", ma_live, (56, 189, 248)), ("ANLIK", cur, (34, 197, 94) if new_pos == "ABOVE" else (239, 68, 68))],
                            ma_series=sma200[-80:],
                            header_note=f"MA200 {diff_pct:+.2f}%",
                        )
                        tg_send_photo(caption, png)

        # 1B) STORMGAIN PRO MOTORU: 365G (YEAR) & 30G (MONTH) + 5-FAKTOR MOMENTUM/HACIM ONAYI
        if tk["volume24h"] >= 15_000_000 and len(c1d) >= 60:
            cooldown_ms = 48 * 3600 * 1000
            prior30 = c1d[-31:-1]
            month_max = max(b["high"] for b in prior30)
            month_min = min(b["low"] for b in prior30)

            year_max, year_min = 0.0, 0.0
            if len(c1d) >= 366:
                prior365 = c1d[-366:-1]
                year_max = max(b["high"] for b in prior365)
                year_min = min(b["low"] for b in prior365)

            with state_lock:
                seeded = False
                if year_max > year_min and sym not in locks["year"]:
                    init_y = "MAX" if cur >= year_max else ("MIN" if cur <= year_min else "NEUTRAL")
                    locks["year"][sym] = {"symbol": sym, "direction": init_y, "level": cur, "yearMax": year_max, "yearMin": year_min, "updatedAt": now_ms}
                    seeded = True
                if month_max > month_min and sym not in locks["month"]:
                    init_m = "MAX" if cur >= month_max else ("MIN" if cur <= month_min else "NEUTRAL")
                    locks["month"][sym] = {"symbol": sym, "direction": init_m, "level": cur, "monthMax": month_max, "monthMin": month_min, "updatedAt": now_ms}
                    seeded = True
                prev_y = locks["year"].get(sym)
                prev_m = locks["month"].get(sym)
            if seeded:
                save_state()

            candidate = None
            if year_max > year_min:
                if year_max * 1.001 <= cur <= year_max * 1.028:
                    if not prev_y or prev_y["direction"] != "MAX" or ((now_ms - prev_y["updatedAt"] >= cooldown_ms) and cur > prev_y["level"] * 1.02):
                        candidate = ("YEAR", "MAX", year_max, year_max, year_min)
                elif year_min * 0.972 <= cur <= year_min * 0.999:
                    if not prev_y or prev_y["direction"] != "MIN" or ((now_ms - prev_y["updatedAt"] >= cooldown_ms) and cur < prev_y["level"] * 0.98):
                        candidate = ("YEAR", "MIN", year_min, year_max, year_min)

            if not candidate and month_max > month_min:
                if month_max * 1.001 <= cur <= month_max * 1.028:
                    if not prev_m or prev_m["direction"] != "MAX" or ((now_ms - prev_m["updatedAt"] >= cooldown_ms) and cur > prev_m["level"] * 1.02):
                        candidate = ("MONTH", "MAX", month_max, month_max, month_min)
                elif month_min * 0.972 <= cur <= month_min * 0.999:
                    if not prev_m or prev_m["direction"] != "MIN" or ((now_ms - prev_m["updatedAt"] >= cooldown_ms) and cur < prev_m["level"] * 0.98):
                        candidate = ("MONTH", "MIN", month_min, month_max, month_min)

            if candidate:
                scope, c_dir, ref_lvl, hi_lvl, lo_lvl = candidate
                c4h = fetch_spot_klines(sym, "4h", 50)
                c1h = fetch_spot_klines(sym, "1h", 28)
                if len(c4h) >= 36 and len(c1h) >= 24:
                    closes4h = [b["close"] for b in c4h]
                    closes4h[-1] = cur
                    rsi4h = compute_rsi_series(closes4h, 14)[-1]
                    macd4h = compute_macd_last(closes4h)

                    base20 = c1h[-22:-2]
                    avg1h = sum(b["quote_vol"] for b in base20) / max(len(base20), 1)
                    recent1h = max(c1h[-1]["quote_vol"], c1h[-2]["quote_vol"])
                    rvol = (recent1h / avg1h) if avg1h > 0 else 1.0

                    tot_q = sum(b["quote_vol"] for b in c1h[-2:])
                    tot_tb = sum(b["taker_buy_quote"] for b in c1h[-2:])
                    taker_buy_pct = (tot_tb / tot_q * 100.0) if tot_q > 0 else 50.0

                    is_max = c_dir == "MAX"
                    chg24 = tk["change24h"]
                    momentum_ok = (
                        1.0 <= chg24 <= 18.0
                        and 50.0 <= rsi4h <= 74.0
                        and (macd4h["macd"] > macd4h["signal"] or macd4h["hist"] > macd4h["prev_hist"])
                        and rvol >= 1.45
                        and taker_buy_pct >= 52.0
                    ) if is_max else (
                        -18.0 <= chg24 <= -1.0
                        and 26.0 <= rsi4h <= 50.0
                        and (macd4h["macd"] < macd4h["signal"] or macd4h["hist"] < macd4h["prev_hist"])
                        and rvol >= 1.45
                        and taker_buy_pct <= 48.0
                    )

                    if momentum_ok:
                        with state_lock:
                            if scope == "YEAR":
                                locks["year"][sym] = {"symbol": sym, "direction": c_dir, "level": cur, "yearMax": year_max, "yearMin": year_min, "updatedAt": now_ms}
                                if month_max > month_min:
                                    locks["month"][sym] = {"symbol": sym, "direction": c_dir, "level": cur, "monthMax": month_max, "monthMin": month_min, "updatedAt": now_ms}
                            else:
                                locks["month"][sym] = {"symbol": sym, "direction": c_dir, "level": cur, "monthMax": month_max, "monthMin": month_min, "updatedAt": now_ms}
                            signals_today.append({"engine": scope, "symbol": sym, "dir": "LONG" if is_max else "SHORT", "entry": cur, "peak_pct": 0.0, "ts": now_ms})
                        save_state()

                        fut_line = fetch_futures_context_line(sym)
                        is_year = scope == "YEAR"
                        tag = ("year max" if is_max else "year min") if is_year else ("month max" if is_max else "month min")
                        icon = "🟢" if is_max else "🔴"
                        taker_txt = f"Taker Alıcı: %{round(taker_buy_pct)}" if is_max else f"Taker Satıcı: %{round(100 - taker_buy_pct)}"
                        caption = (
                            f"{icon} <b>#{sym}, {'1 yıllık' if is_year else '1 aylık'} ({tag}) değere ulaştı: ${fmt_price(cur)}</b>\n"
                            f"⚡ RVOL: <b>{rvol:.1f}x</b> · 4S RSI: {rsi4h:.1f} · 4S MACD {'Pozitif' if is_max else 'Negatif'} · {taker_txt}\n"
                            f"💰 24s: {chg24:+.2f}% · Hacim: ${tk['volume24h'] / 1_000_000:.1f}M\n"
                            + (f"📊 {fut_line}\n" if fut_line else "")
                            + "🔒 48 saatlik kilit aktif (aynı yönde artarda spam yapılmaz)."
                        )
                        lbl_hi = "365MAX" if is_year else "30MAX"
                        lbl_lo = "365MIN" if is_year else "30MIN"
                        png = render_candlestick_png(
                            sym,
                            "1G",
                            c1d[-80:],
                            levels=[(lbl_hi, hi_lvl, (34, 197, 94)), (lbl_lo, lo_lvl, (239, 68, 68)), ("ANLIK", cur, (56, 189, 248))],
                            header_note=tag.upper(),
                        )
                        tg_send_photo(caption, png)
    except Exception:
        pass

    # 2) MOTOR 3: 4h Akis / Emir Defteri Duvari & POC (1D + 3D Trend Onayli)
    try:
        if tk["volume24h"] >= 2_500_000:
            depth = fetch_binance_json(f"/api/v3/depth?symbol={urllib.parse.quote(sym)}&limit=100", timeout=7.0)
            c15m = fetch_spot_klines(sym, "15m", 96)
            c4h = fetch_spot_klines(sym, "4h", 56)
            c1d_s = fetch_spot_klines(sym, "1d", 12)
            c3d_s = fetch_spot_klines(sym, "3d", 12)
            if len(c15m) >= 40 and len(c4h) >= 28 and len(c1d_s) >= 7 and len(c3d_s) >= 5:
                ma1d = sum(b["close"] for b in c1d_s[-7:]) / 7.0
                trend1d = "LONG" if cur >= ma1d else "SHORT"
                ma3d = sum(b["close"] for b in c3d_s[-5:]) / 5.0
                trend3d = "LONG" if cur >= ma3d else "SHORT"

                d_lo = min(b["low"] for b in c15m)
                d_hi = max(b["high"] for b in c15m)
                if d_hi > d_lo:
                    buckets = 40
                    step_p = (d_hi - d_lo) / buckets
                    hist_v = [0.0] * buckets
                    for b in c15m:
                        mid = (b["high"] + b["low"] + b["close"]) / 3.0
                        idx = min(buckets - 1, max(0, int((mid - d_lo) / step_p)))
                        hist_v[idx] += b["quote_vol"]
                    poc_idx = max(range(buckets), key=lambda i: hist_v[i])
                    poc_price = d_lo + (poc_idx + 0.5) * step_p

                    best_bid, best_ask = (0.0, 0.0), (0.0, 0.0)
                    for ps, qs in depth.get("bids", []):
                        p, usd = float(ps), float(ps) * float(qs)
                        if abs(cur - p) / cur <= 0.025 and usd > best_bid[1]:
                            best_bid = (p, usd)
                    for ps, qs in depth.get("asks", []):
                        p, usd = float(ps), float(ps) * float(qs)
                        if abs(p - cur) / cur <= 0.025 and usd > best_ask[1]:
                            best_ask = (p, usd)

                    bias, wall = None, None
                    if best_ask[1] >= 120_000 and cur <= best_ask[0] * 1.004 and best_ask[1] >= best_bid[1] * 1.15:
                        bias, wall = "SHORT", (best_ask[0], best_ask[1], "Ask (Satış) Duvarı")
                    elif best_bid[1] >= 120_000 and cur >= best_bid[0] * 0.996 and best_bid[1] >= best_ask[1] * 1.15:
                        bias, wall = "LONG", (best_bid[0], best_bid[1], "Bid (Alış) Duvarı")

                    if bias and bias == trend1d and bias == trend3d and locks["flow"].get(sym, {}).get("direction") != bias:
                        sw_hi = max(b["high"] for b in c4h[-28:])
                        sw_lo = min(b["low"] for b in c4h[-28:])
                        rng = max(sw_hi - sw_lo, cur * 0.03)
                        tp1 = min(cur * 0.97, cur - rng * 0.25) if bias == "SHORT" else max(cur * 1.03, cur + rng * 0.25)
                        tp2 = min(cur * 0.948, cur - rng * 0.45) if bias == "SHORT" else max(cur * 1.052, cur + rng * 0.45)
                        tp3 = tp2 * 0.975 if bias == "SHORT" else tp2 * 1.025
                        stop_tight = max(wall[0] * 1.005, cur * 1.008) if bias == "SHORT" else min(wall[0] * 0.995, cur * 0.992)
                        trigger = cur * 0.992 if bias == "SHORT" else cur * 1.008
                        exchanges = fetch_multi_exchange_sync(sym, cur)

                        with state_lock:
                            locks["flow"][sym] = {"symbol": sym, "direction": bias, "trend1d": trend1d, "trend3d": trend3d, "lockedAt": now_ms}
                            signals_today.append({"engine": "FLOW", "symbol": sym, "dir": bias, "entry": cur, "peak_pct": 0.0, "ts": now_ms})
                        save_state()

                        def _pstr(t):
                            return f"{((t - cur) / cur) * 100:+.1f}%"

                        caption = (
                            f"🔥 <b>#{sym} ({bias}) — ${round(wall[1] / 1000)}k {wall[2]}</b>\n"
                            f"🎯 Hedefler: <code>{fmt_price(tp1)}</code> ({_pstr(tp1)}) → <code>{fmt_price(tp2)}</code> ({_pstr(tp2)}) → <code>{fmt_price(tp3)}</code> ({_pstr(tp3)})\n"
                            f"🛑 İptal: <code>{fmt_price(stop_tight)}</code> ({_pstr(stop_tight)})\n"
                            f"🧱 Duvar: {fmt_price(wall[0])} · 24s POC: {fmt_price(poc_price)}\n"
                            f"📐 1D + 3D Trend Onayı: <b>{bias}</b> · Senkron: {', '.join(exchanges)}\n"
                            f"🔑 Anahtar Tetik: {fmt_price(trigger)} {'altında' if bias == 'SHORT' else 'üstünde'} kalıcılık"
                        )
                        png = render_candlestick_png(
                            sym,
                            "4S",
                            c4h,
                            levels=[
                                ("TP1", tp1, (34, 197, 94)),
                                ("TP2", tp2, (16, 185, 129)),
                                ("STOP", stop_tight, (239, 68, 68)),
                                ("POC", poc_price, (250, 204, 21)),
                                ("ANLIK", cur, (56, 189, 248)),
                            ],
                            header_note=bias,
                        )
                        tg_send_photo(caption, png)
    except Exception:
        pass

    # 3) MOTOR 2: W1 ATL (Siki Taban Kilidi)
    try:
        cached = atl_weekly_cache.get(sym)
        if not cached or (now_ms - cached["ts"]) > 3_600_000:
            c1w = fetch_spot_klines(sym, "1w", 1000)
            if len(c1w) >= 12:
                prev_atl = min(b["low"] for b in c1w[:-1])
                cached = {"prev_atl": prev_atl, "ts": now_ms}
                atl_weekly_cache[sym] = cached
        if cached and cached["prev_atl"] > 0:
            atl_rec = locks["atl"].get(sym)
            floor_atl = min(cached["prev_atl"], atl_rec["atl"]) if atl_rec else cached["prev_atl"]
            broke = (cur < floor_atl * 0.995) if atl_rec else (cur < floor_atl)
            if broke and tk["low24h"] < cached["prev_atl"]:
                with state_lock:
                    locks["atl"][sym] = {"symbol": sym, "atl": cur, "lockedAt": now_ms}
                    signals_today.append({"engine": "ATL", "symbol": sym, "dir": "ATL", "entry": cur, "peak_pct": 0.0, "ts": now_ms})
                save_state()
                c1h_atl = fetch_spot_klines(sym, "1h", 80)
                caption = (
                    f"🕳 <b>#{sym} ${fmt_price(cur)} tüm zamanların en düşüğü (ATL)</b>\n"
                    f"📉 Önceki ATL (<code>${fmt_price(floor_atl)}</code>) kesin olarak aşağı kırıldı · 24s: {tk['change24h']:+.2f}%\n"
                    f"🔒 Kilit aktif: ${fmt_price(cur)} altına düşmedikçe tekrar gelmez."
                )
                png = render_candlestick_png(
                    sym,
                    "1S ATL",
                    c1h_atl,
                    levels=[("ATL", floor_atl, (250, 204, 21)), ("ANLIK", cur, (239, 68, 68))],
                    header_note="W1 ATL",
                )
                tg_send_photo(caption, png)
    except Exception:
        pass

    # 4) MOTOR EXHAUST: PARABOLIK TUKENME / PUMP TEPESI DONUSU (SHORT / Kar Al Uyarisi - 24s Kilitli)
    try:
        prev_exh = locks["exhaust"].get(sym)
        exh_cooldown_ms = 24 * 3600 * 1000
        if (
            tk["volume24h"] >= 5_000_000
            and tk["change24h"] >= 7.5
            and tk["high24h"] > cur
            and (tk["high24h"] * 0.955 <= cur <= tk["high24h"] * 0.996)
            and (not prev_exh or (now_ms - prev_exh["lockedAt"]) >= exh_cooldown_ms)
        ):
            c15m = fetch_spot_klines(sym, "15m", 40)
            if len(c15m) >= 24:
                recent16 = c15m[-17:]
                swing_low = min(b["low"] for b in recent16[:-1])
                swing_high = max(b["high"] for b in recent16)
                rally_pct = ((swing_high - swing_low) / swing_low * 100.0) if swing_low > 0 else 0.0

                if rally_pct >= 7.5 and (swing_high * 0.960 <= cur <= swing_high * 0.996):
                    closes15 = [b["close"] for b in c15m]
                    closes15[-1] = cur
                    rsi_ser = compute_rsi_series(closes15, 14)
                    rsi_cur = rsi_ser[-1]
                    rsi_peak4 = max(rsi_ser[-4:])

                    last_bar = c15m[-1]
                    prev_bar = c15m[-2]

                    def _wick_ratio(b):
                        return (b["high"] - max(b["open"], b["close"])) / max(b["high"] - b["low"], 1e-9)

                    has_rejection = (
                        _wick_ratio(prev_bar) >= 0.35
                        or _wick_ratio(last_bar) >= 0.35
                        or prev_bar["close"] < prev_bar["open"]
                    )
                    last2 = c15m[-2:]
                    tot_q = sum(b["quote_vol"] for b in last2)
                    tot_tb = sum(b["taker_buy_quote"] for b in last2)
                    taker_buy_pct = (tot_tb / tot_q * 100.0) if tot_q > 0 else 50.0

                    if rsi_peak4 >= 75.0 and rsi_cur <= (rsi_peak4 - 1.5) and has_rejection and taker_buy_pct <= 47.0:
                        rng = swing_high - swing_low
                        tp1 = min(cur * 0.985, swing_high - rng * 0.382)
                        tp2 = min(tp1 * 0.985, swing_high - rng * 0.50)
                        tp3 = min(tp2 * 0.985, swing_high - rng * 0.618)
                        stop_loss = swing_high * 1.008

                        with state_lock:
                            locks["exhaust"][sym] = {"symbol": sym, "peakPrice": swing_high, "entryPrice": cur, "lockedAt": now_ms}
                            signals_today.append({"engine": "EXHAUST", "symbol": sym, "dir": "SHORT", "entry": cur, "peak_pct": 0.0, "ts": now_ms})
                        save_state()

                        fut_line = fetch_futures_context_line(sym)

                        def _exh_p(t):
                            return f"{((t - cur) / cur) * 100:+.1f}%"

                        caption = (
                            f"🪫 <b>#{sym} (SHORT / Tepe Uyarısı) — Parabolik Tükenme (${fmt_price(cur)})</b>\n"
                            f"📈 Ralli: <b>+%{rally_pct:.1f}</b> · Tepe 15dk RSI: {rsi_peak4:.1f} → {rsi_cur:.1f} (Üst fitil reddi + Taker Satıcı %{round(100 - taker_buy_pct)})\n"
                            f"🎯 Hedefler (Fib): <code>{fmt_price(tp1)}</code> ({_exh_p(tp1)}) → <code>{fmt_price(tp2)}</code> ({_exh_p(tp2)}) → <code>{fmt_price(tp3)}</code> ({_exh_p(tp3)})\n"
                            f"🛑 İptal (Sıkı Stop): <code>{fmt_price(stop_loss)}</code> üstünde ({_exh_p(stop_loss)})\n"
                            + (f"📊 {fut_line}\n" if fut_line else "")
                            + "🔒 24 saatlik kilit aktif · Spotta kâr al / Vadelide tepe dönüşü uyarısı."
                        )
                        png = render_candlestick_png(
                            sym,
                            "15DK TEPE",
                            c15m[-40:],
                            levels=[
                                ("TP1", tp1, (34, 197, 94)),
                                ("TP2", tp2, (16, 185, 129)),
                                ("STOP", stop_loss, (239, 68, 68)),
                                ("ANLIK", cur, (250, 204, 21)),
                            ],
                            header_note="TEPE TUKENME",
                        )
                        tg_send_photo(caption, png)
    except Exception:
        pass


# ==============================================================================
# MOTOR 4, 5 & 8: CANLI PIK RAPORLARI (21:00 / 23:00 TR) & BINANCE DUYURU
# ==============================================================================
def update_signal_peaks():
    changed = False
    with state_lock:
        for s in signals_today:
            tk = ticker_map.get(s["symbol"])
            if not tk or tk["price"] <= 0:
                continue
            cur = tk["price"]
            is_short = s["dir"] in ("SHORT", "BELOW", "ATL")
            pct = ((s["entry"] - cur) / s["entry"] * 100.0) if is_short else ((cur - s["entry"]) / s["entry"] * 100.0)
            if pct > s["peak_pct"]:
                s["peak_pct"] = round(pct, 2)
                changed = True
    if changed:
        save_state()


def check_daily_reports():
    now_tr = datetime.now(TR_TZ)
    day_key = now_tr.strftime("%Y-%m-%d")
    cutoff_ms = int((time.time() - 86400) * 1000)

    if now_tr.hour == 21 and report_sent_days["victory"] != day_key:
        report_sent_days["victory"] = day_key
        with state_lock:
            winners = [s for s in signals_today if s["ts"] >= cutoff_ms and s["peak_pct"] >= 3.0]
        winners.sort(key=lambda x: x["peak_pct"], reverse=True)
        if winners:
            lines = [f"🏆 <b>GÜNÜN ZAFERİ ({day_key})</b>\n"]
            for i, w in enumerate(winners[:10], 1):
                lines.append(f"{i}. #{w['symbol']} [{w['engine']}/{w['dir']}] → <b>+{w['peak_pct']:.2f}%</b> pik")
            tg_send_text("\n".join(lines))

    if now_tr.hour == 23 and report_sent_days["top24"] != day_key:
        report_sent_days["top24"] = day_key
        with state_lock:
            top_list = [s for s in signals_today if s["ts"] >= cutoff_ms]
        top_list.sort(key=lambda x: x["peak_pct"], reverse=True)
        if top_list:
            lines = [f"📊 <b>TOP 24 SAAT ÖZETİ ({day_key})</b>\n"]
            for i, w in enumerate(top_list[:15], 1):
                lines.append(f"{i}. #{w['symbol']} [{w['engine']}/{w['dir']}] → {w['peak_pct']:+.2f}%")
            tg_send_text("\n".join(lines))


def translate_binance_title_tr(title: str) -> str:
    tr = title.strip()
    pairs = [
        ("Binance Will List", "Binance Listeliyor:"),
        ("Binance Will Add", "Binance Ekliyor:"),
        ("Binance Will Support", "Binance Destekliyor:"),
        ("Binance Has Completed the", "Binance Tamamladı:"),
        ("Binance Futures Will Launch", "Binance Vadeli Başlatıyor:"),
        ("Notice of Removal of Spot Trading Pairs", "Spot İşlem Çiftleri Delist:"),
        ("Binance Will Delist", "Binance Delist Ediyor:"),
        ("Network Upgrade", "Ağ Güncellemesi"),
    ]
    for eng, tur in pairs:
        tr = tr.replace(eng, tur)
    return tr


def poll_binance_news():
    global news_initial_seeded
    for cat_id, cat_lbl in [(48, "Yeni Listeleme"), (49, "Binance Duyuru"), (161, "Delist / Kaldırma")]:
        try:
            url = f"https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query?catalogId={cat_id}&pageNo=1&pageSize=6"
            data = http_get_json(url, timeout=8.0)
            arts = (data.get("data") or {}).get("articles") or []
            for art in arts:
                aid = str(art.get("id") or art.get("code") or art.get("title") or "")
                if not aid or aid in news_seen_ids:
                    continue
                news_seen_ids.add(aid)
                if news_initial_seeded:
                    title_tr = translate_binance_title_tr(str(art.get("title") or ""))
                    code = str(art.get("code") or "")
                    link = f"https://www.binance.com/en/support/announcement/detail/{code}" if code else "https://www.binance.com/en/support/announcement"
                    tg_send_text(f"📰 <b>Binance | {cat_lbl}</b>\n{title_tr}\n{link}")
        except Exception:
            pass
    news_initial_seeded = True


# ==============================================================================
# ANA DONGULER & RENDER KEEP-ALIVE SUNUCUSU
# ==============================================================================
def ticker_pump_loop():
    while True:
        try:
            raw = fetch_binance_json("/api/v3/ticker/24hr", timeout=10.0)
            now_ms = int(time.time() * 1000)
            if isinstance(raw, list):
                for item in raw:
                    sym = item.get("symbol", "")
                    if is_excluded_pair(sym):
                        continue
                    bq = extract_base_quote(sym)
                    if not bq:
                        continue
                    price = float(item.get("lastPrice") or 0)
                    if price <= 0:
                        continue
                    tk = {
                        "symbol": sym,
                        "base": bq[0],
                        "quote": bq[1],
                        "price": price,
                        "change24h": float(item.get("priceChangePercent") or 0),
                        "volume24h": float(item.get("quoteVolume") or 0),
                        "high24h": float(item.get("highPrice") or price),
                        "low24h": float(item.get("lowPrice") or price),
                    }
                    ticker_map[sym] = tk
                    evaluate_pump_for_ticker(tk, now_ms)
                update_signal_peaks()
                check_daily_reports()
        except Exception:
            pass
        time.sleep(3)


def structure_loop():
    cursor = 0
    while True:
        try:
            liquid = sorted(
                [t for t in ticker_map.values() if t["quote"] == "USDT" and t["volume24h"] >= 1_500_000],
                key=lambda x: x["volume24h"],
                reverse=True,
            )[:500]
            if liquid:
                batch = []
                for _ in range(min(8, len(liquid))):
                    batch.append(liquid[cursor % len(liquid)])
                    cursor += 1
                threads = [threading.Thread(target=scan_structural_pair, args=(t,), daemon=True) for t in batch]
                for th in threads:
                    th.start()
                for th in threads:
                    th.join(timeout=18)
        except Exception:
            pass
        time.sleep(4)


def news_loop():
    while True:
        poll_binance_news()
        time.sleep(60)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body = json.dumps(
            {
                "ok": True,
                "pairs": len(ticker_map),
                "locks": {k: len(v) for k, v in locks.items()},
            }
        ).encode("utf-8")
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def main():
    load_state()
    threading.Thread(target=ticker_pump_loop, daemon=True).start()
    threading.Thread(target=structure_loop, daemon=True).start()
    threading.Thread(target=news_loop, daemon=True).start()
    srv = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"[SpotFlowBot] Port {PORT} uzerinde aktif. Takip edilen kilit gruplari: {list(locks.keys())}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
