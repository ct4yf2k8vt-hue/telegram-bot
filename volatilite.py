#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Render 7/24 Binance Spot Sinyal & Canlı Grafik Botu (Sıfır Harici Bağımlılık)
Motorlar:
  1) 0-7 dk Spot Anlık Pump (USDT/BTC/BNB, tekil coin kilidi, eşzamanlı 1m+ticker doğrulamalı, 1DK PNG)
  2) W1 ATL (Kesin dip altı kilidi, geri sekmede açılmaz, 1H PNG)
  3) 4h Akış / Emir Defteri Duvarı & 24s POC (1D + 3D trend onaylı, LONG<->SHORT kilitli, 4S PNG)
  4) 1G MA200 Canlı Kırılım (Klasik SMA200, sessiz başlangıç tohumlama, ±%0.4 eşik, 1G PNG)
  5) 30G Zirve / Dip — StormGain Tarzı MONTH MAX & MONTH MIN (Çift yönlü, ±%0.5 kırılım kilidi, Futures OI/Balina/Kalabalık destekli, 1G PNG)
  6) Günün Zaferi (21:00 TR) & TOP 24 Saat (23:00 TR)
  7) Binance Resmi Duyuru & Haber Takibi (48/49/161 Türkçe özet)
"""

import datetime
import http.server
import json
import math
import os
import re
import socketserver
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

# ==============================================================================
# YAPILANDIRMA
# ==============================================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()
PORT = int(os.environ.get("PORT", "10000"))
PUMP_REPEAT_HOURS = float(os.environ.get("PUMP_REPEAT_HOURS", "0"))
STATE_FILE = "/tmp/render_spot_bot_state.json"

BINANCE_MIRRORS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://data-api.binance.vision",
]

EXCLUDED_BASES = ("USDC", "FDUSD", "TUSD", "USDP", "EUR", "BUSD")
EXCLUDED_SUBS = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

# ==============================================================================
# DURUM (STATE) & KILITLER
# ==============================================================================
state_lock = threading.Lock()
price_history = {}       # sym -> list[(ts, price)]
signals_today = []       # gunluk raporlama & zirve takibi
atl_cache = {}           # sym -> (prev_atl, ts)
news_seen = set()
news_seeded = False

locks = {
    "pump": {},          # base -> ts
    "atl": {},           # sym -> {"atl": float, "ts": float}
    "flow": {},          # sym -> {"dir": "LONG"|"SHORT", "ts": float}
    "ma200": {},         # sym -> {"dir": "ABOVE"|"BELOW", "ts": float}
    "month": {},         # sym -> {"dir": "MAX"|"MIN"|"NEUTRAL", "level": float, "ts": float}
}

last_daily_21 = ""
last_daily_23 = ""


def load_state():
    global locks
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data.get("locks"), dict):
                for k in ("pump", "atl", "flow", "ma200", "month"):
                    if isinstance(data["locks"].get(k), dict):
                        locks[k] = data["locks"][k]
    except Exception:
        pass


def save_state():
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"locks": locks}, f)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


# ==============================================================================
# YARDIMCI FONKSIYONLAR
# ==============================================================================
def tr_now():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=3)


def fmt_price(p):
    if not p or p <= 0 or math.isnan(p) or math.isinf(p):
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


def extract_base_quote(sym):
    for q in ("USDT", "BTC", "BNB"):
        if sym.endswith(q):
            return sym[:-len(q)], q
    return None, None


def is_excluded(sym):
    if any(sub in sym for sub in EXCLUDED_SUBS):
        return True
    if any(sym.startswith(b) for b in EXCLUDED_BASES):
        return True
    return False


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


# ==============================================================================
# HTTP / BINANCE / FUTURES / TELEGRAM ISTEMCISI
# ==============================================================================
def http_get_json(url, timeout=8.0):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; SpotFlowBot/3.0)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_binance_json(path_and_query, timeout=8.0):
    last_err = None
    for mirror in BINANCE_MIRRORS:
        try:
            return http_get_json(mirror + path_and_query, timeout=timeout)
        except Exception as e:
            last_err = e
    raise last_err or RuntimeError("Binance mirror unreachable")


def fetch_klines(symbol, interval, limit=240):
    raw = fetch_binance_json(
        f"/api/v3/klines?symbol={urllib.parse.quote(symbol)}&interval={interval}&limit={limit}",
        timeout=9.0,
    )
    bars = []
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
    if not symbol.endswith("USDT"):
        return ["Binance"]
    base = symbol[:-4]
    exchanges = ["Binance"]

    def chk_bybit():
        d = http_get_json(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}", 3.5)
        p = float(d["result"]["list"][0]["lastPrice"])
        if p > 0 and abs(p - binance_price) / binance_price < 0.02:
            exchanges.append("Bybit")

    def chk_okx():
        d = http_get_json(f"https://www.okx.com/api/v5/market/ticker?instId={base}-USDT", 3.5)
        p = float(d["data"][0]["last"])
        if p > 0 and abs(p - binance_price) / binance_price < 0.02:
            exchanges.append("OKX")

    def chk_gate():
        d = http_get_json(f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={base}_USDT", 3.5)
        p = float(d[0]["last"])
        if p > 0 and abs(p - binance_price) / binance_price < 0.02:
            exchanges.append("Gate")

    threads = [threading.Thread(target=fn, daemon=True) for fn in (chk_bybit, chk_okx, chk_gate)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=3.6)
    return exchanges


def fetch_futures_context_line(symbol):
    if not symbol.endswith("USDT"):
        return None
    results = {}

    def get_oi():
        try:
            d = http_get_json(
                f"https://fapi.binance.com/fapi/v1/openInterest?symbol={urllib.parse.quote(symbol)}",
                2.5,
            )
            val = float(d.get("openInterest", 0))
            if val > 0:
                results["oi"] = f"OI: {round(val):,}"
        except Exception:
            pass

    def get_whale():
        try:
            d = http_get_json(
                f"https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={urllib.parse.quote(symbol)}&period=1h&limit=1",
                2.5,
            )
            if isinstance(d, list) and d:
                l_pct = round(float(d[0].get("longAccount", 0)) * 100)
                if l_pct > 0:
                    results["whale"] = f"Balina: %{l_pct}L/%{100 - l_pct}S"
        except Exception:
            pass

    def get_crowd():
        try:
            d = http_get_json(
                f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={urllib.parse.quote(symbol)}&period=1h&limit=1",
                2.5,
            )
            if isinstance(d, list) and d:
                l_pct = round(float(d[0].get("longAccount", 0)) * 100)
                if l_pct > 0:
                    results["crowd"] = f"Kalabalik: %{l_pct}L/%{100 - l_pct}S"
        except Exception:
            pass

    threads = [threading.Thread(target=fn, daemon=True) for fn in (get_oi, get_whale, get_crowd)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=2.7)

    parts = [results[k] for k in ("oi", "whale", "crowd") if k in results]
    return " · ".join(parts) if parts else None


def tg_send_text(text):
    if not BOT_TOKEN:
        print("[TG TEXT]", text.replace("\n", " | "))
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
        print("TG sendMessage hata:", e)


def tg_send_photo(png_bytes, caption):
    if not BOT_TOKEN:
        print(f"[TG PHOTO {len(png_bytes)}B]", caption.replace("\n", " | "))
        return
    try:
        boundary = "----SpotFlowBoundary7MA4YWxkTrZu0gW"
        parts = []
        for name, val in (("chat_id", CHAT_ID), ("caption", caption), ("parse_mode", "HTML")):
            parts.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{val}\r\n".encode("utf-8")
            )
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="chart.png"\r\n'
            f"Content-Type: image/png\r\n\r\n".encode("utf-8")
            + png_bytes
            + f"\r\n--{boundary}--\r\n".encode("utf-8")
        )
        body = b"".join(parts)
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print("TG sendPhoto hata:", e)
        tg_send_text(caption)


# ==============================================================================
# SIFIR BAGIMLILIKLI 5x7 PIXEL FONT + PNG MUM GRAFIK MOTORU
# ==============================================================================
FONT_5X7 = {
    "0": [0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E],
    "1": [0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E],
    "2": [0x0E, 0x11, 0x01, 0x06, 0x08, 0x10, 0x1F],
    "3": [0x1F, 0x02, 0x04, 0x06, 0x01, 0x11, 0x0E],
    "4": [0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02],
    "5": [0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E],
    "6": [0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E],
    "7": [0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08],
    "8": [0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E],
    "9": [0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C],
    "A": [0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
    "B": [0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E],
    "C": [0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E],
    "D": [0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E],
    "E": [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F],
    "F": [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10],
    "G": [0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F],
    "H": [0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
    "I": [0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
    "J": [0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C],
    "K": [0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11],
    "L": [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F],
    "M": [0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11],
    "N": [0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11],
    "O": [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
    "P": [0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10],
    "Q": [0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D],
    "R": [0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11],
    "S": [0x0E, 0x11, 0x10, 0x0E, 0x01, 0x11, 0x0E],
    "T": [0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
    "U": [0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
    "V": [0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04],
    "W": [0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11],
    "X": [0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11],
    "Y": [0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04],
    "Z": [0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F],
    ".": [0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C],
    ",": [0x00, 0x00, 0x00, 0x00, 0x04, 0x04, 0x08],
    ":": [0x00, 0x0C, 0x0C, 0x00, 0x0C, 0x0C, 0x00],
    "-": [0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00],
    "+": [0x00, 0x04, 0x04, 0x1F, 0x04, 0x04, 0x00],
    "/": [0x01, 0x02, 0x02, 0x04, 0x08, 0x08, 0x10],
    "%": [0x19, 0x19, 0x02, 0x04, 0x08, 0x13, 0x13],
    "$": [0x04, 0x0F, 0x14, 0x0E, 0x05, 0x1E, 0x04],
    "|": [0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
    " ": [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
}


def render_candlestick_png(
    bars,
    title,
    cur_price=None,
    levels=None,
    ma_series=None,
    interval="1h",
    width=920,
    height=500,
):
    levels = levels or []
    bars = [dict(b) for b in bars[-72:]]
    if not bars:
        bars = [{"t": int(time.time()), "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0}]

    if cur_price and cur_price > 0:
        last = bars[-1]
        last["c"] = cur_price
        last["h"] = max(last["h"], cur_price)
        last["l"] = min(last["l"], cur_price)

    bg = (11, 15, 25)
    grid_col = (26, 35, 53)
    axis_col = (71, 85, 105)
    up_col = (16, 185, 129)
    dn_col = (244, 63, 94)
    txt_col = (203, 213, 225)
    muted_col = (120, 134, 156)

    buf = bytearray(bg * (width * height))

    def set_px(x, y, col):
        if 0 <= x < width and 0 <= y < height:
            i = (y * width + x) * 3
            buf[i] = col[0]
            buf[i + 1] = col[1]
            buf[i + 2] = col[2]

    def fill_rect(x0, y0, x1, y1, col):
        x0 = max(0, int(x0))
        y0 = max(0, int(y0))
        x1 = min(width - 1, int(x1))
        y1 = min(height - 1, int(y1))
        for yy in range(y0, y1 + 1):
            row_start = yy * width * 3
            for xx in range(x0, x1 + 1):
                idx = row_start + xx * 3
                buf[idx] = col[0]
                buf[idx + 1] = col[1]
                buf[idx + 2] = col[2]

    def draw_line(x0, y0, x1, y1, col, dashed=False):
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        step = 0
        while True:
            if not dashed or (step % 8 < 5):
                set_px(x0, y0, col)
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

    def draw_text(x, y, text, col, scale=1):
        cx = int(x)
        cy = int(y)
        for ch in text.upper():
            glyph = FONT_5X7.get(ch, FONT_5X7[" "])
            for row_i, row_bits in enumerate(glyph):
                for col_i in range(5):
                    if row_bits & (1 << (4 - col_i)):
                        if scale == 1:
                            set_px(cx + col_i, cy + row_i, col)
                        else:
                            fill_rect(
                                cx + col_i * scale,
                                cy + row_i * scale,
                                cx + (col_i + 1) * scale - 1,
                                cy + (row_i + 1) * scale - 1,
                                col,
                            )
            cx += 6 * scale

    pad_l, pad_t, pad_r, pad_b = 18, 44, 150, 36
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    vals = [b["h"] for b in bars] + [b["l"] for b in bars]
    for _, lv, _ in levels:
        if lv and lv > 0:
            vals.append(lv)
    if ma_series:
        for mv in ma_series[-len(bars):]:
            if mv and mv > 0:
                vals.append(mv)

    hi = max(vals)
    lo = min(vals)
    if hi <= lo:
        hi = lo * 1.02 + 1e-8
    span = hi - lo
    hi += span * 0.08
    lo = max(0.0, lo - span * 0.08)
    span = hi - lo

    def py(val):
        ratio = (hi - val) / span
        return int(pad_t + max(0.0, min(1.0, ratio)) * plot_h)

    fill_rect(0, 0, width - 1, 34, (15, 23, 42))
    draw_line(0, 34, width - 1, 34, grid_col)
    draw_text(16, 11, title[:62], txt_col, scale=2)

    for i in range(6):
        gy = int(pad_t + (plot_h * i) / 5)
        draw_line(pad_l, gy, pad_l + plot_w, gy, grid_col, dashed=True)
        pval = hi - (span * i) / 5
        draw_text(pad_l + plot_w + 8, gy - 3, fmt_price(pval), muted_col, scale=1)

    draw_line(pad_l + plot_w, pad_t, pad_l + plot_w, pad_t + plot_h, axis_col)
    draw_line(pad_l, pad_t + plot_h, pad_l + plot_w, pad_t + plot_h, axis_col)

    n = len(bars)
    step_x = plot_w / max(n, 1)
    body_w = max(3, int(step_x * 0.62))
    for i, b in enumerate(bars):
        cx = int(pad_l + (i + 0.5) * step_x)
        yh = py(b["h"])
        yl = py(b["l"])
        yo = py(b["o"])
        yc = py(b["c"])
        col = up_col if b["c"] >= b["o"] else dn_col
        draw_line(cx, yh, cx, yl, col)
        top = min(yo, yc)
        bot = max(yo, yc)
        if bot - top < 2:
            bot = top + 2
        fill_rect(cx - body_w // 2, top, cx + body_w // 2, bot, col)

    tick_idxs = sorted(set([0, n // 4, n // 2, (3 * n) // 4, n - 1]))
    for idx in tick_idxs:
        if 0 <= idx < n:
            cx = int(pad_l + (idx + 0.5) * step_x)
            dt = datetime.datetime.utcfromtimestamp(bars[idx]["t"]) + datetime.timedelta(hours=3)
            lbl = dt.strftime("%d/%m") if interval in ("1d", "3d", "1w") else dt.strftime("%H:%M")
            draw_line(cx, pad_t + plot_h, cx, pad_t + plot_h + 4, axis_col)
            draw_text(max(pad_l, cx - 15), pad_t + plot_h + 10, lbl, muted_col, scale=1)

    if ma_series:
        tail_ma = ma_series[-n:]
        prev_pt = None
        for i, mv in enumerate(tail_ma):
            if mv and mv > 0:
                cx = int(pad_l + (i + 0.5) * step_x)
                cy = py(mv)
                if prev_pt:
                    draw_line(prev_pt[0], prev_pt[1], cx, cy, (245, 158, 11))
                    draw_line(prev_pt[0], prev_pt[1] + 1, cx, cy + 1, (245, 158, 11))
                prev_pt = (cx, cy)

    used_y = []
    for label, lv, col in levels:
        if not lv or lv <= 0:
            continue
        ly = py(lv)
        draw_line(pad_l, ly, pad_l + plot_w, ly, col, dashed=True)
        tag_y = ly
        for uy in used_y:
            if abs(tag_y - uy) < 15:
                tag_y = uy + 15
        tag_y = max(pad_t + 6, min(pad_t + plot_h - 8, tag_y))
        used_y.append(tag_y)
        tag_txt = f"{label} {fmt_price(lv)}"[:20]
        fill_rect(pad_l + plot_w + 4, tag_y - 6, width - 6, tag_y + 6, col)
        draw_text(pad_l + plot_w + 8, tag_y - 3, tag_txt, (11, 15, 25), scale=1)

    raw_rows = bytearray()
    stride = width * 3
    for y in range(height):
        raw_rows.append(0)
        raw_rows.extend(buf[y * stride:(y + 1) * stride])

    def png_chunk(ctype, data):
        chunk = ctype + data
        return struct.pack(">I", len(data)) + chunk + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows), 6)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", idat)
        + png_chunk(b"IEND", b"")
    )


# ==============================================================================
# MOTOR 1: 0-7 DK SPOT ANLIK PUMP (TEKIL COIN KILIDI + ESZAMANLI DOGRULAMA)
# ==============================================================================
def is_pump_locked(base, now_ts):
    ts = locks["pump"].get(base)
    if not ts:
        return False
    if PUMP_REPEAT_HOURS <= 0:
        return True
    return (now_ts - ts) < (PUMP_REPEAT_HOURS * 3600)


def check_pump_ticker(sym, price, vol24h, now_ts):
    base, quote = extract_base_quote(sym)
    if not base or is_excluded(sym):
        return
    min_vol = 150000 if quote == "USDT" else (3 if quote == "BTC" else 250)
    th_pct = 4.5 if quote == "USDT" else 4.0
    if price <= 0 or vol24h < min_vol:
        return

    with state_lock:
        hist = price_history.setdefault(sym, [])
        hist.append((now_ts, price))
        cutoff = now_ts - 420
        while hist and hist[0][0] < cutoff:
            hist.pop(0)
        if len(hist) < 2:
            return
        min_ts, min_p = min(hist, key=lambda x: x[1])
        if min_p <= 0:
            return
        pct = ((price - min_p) / min_p) * 100.0
        if pct < th_pct or is_pump_locked(base, now_ts):
            return
        locks["pump"][base] = now_ts
        save_state()

    def verify_and_send():
        try:
            fresh = fetch_binance_json(f"/api/v3/ticker/24hr?symbol={urllib.parse.quote(sym)}", 4.5)
            c1m = fetch_klines(sym, "1m", limit=18)
            live_cur = float(fresh.get("lastPrice") or price)
            live_vol = float(fresh.get("quoteVolume") or vol24h)
            recent_lows = [b["l"] for b in c1m[-7:]]
            bar_min = min(recent_lows) if recent_lows else min_p
            verified_min = min(min_p, bar_min if bar_min > 0 else min_p)
            verified_pct = ((live_cur - verified_min) / verified_min) * 100.0
            if verified_pct < th_pct:
                with state_lock:
                    locks["pump"].pop(base, None)
                    save_state()
                return

            with state_lock:
                price_history.pop(sym, None)
                signals_today.append({
                    "sym": sym,
                    "engine": "PUMP",
                    "dir": "LONG",
                    "entry": live_cur,
                    "peak": live_cur,
                    "ts": time.time(),
                })

            mins = max(1, round((time.time() - min_ts) / 60))
            vol_str = f"{round(live_vol):,}" if quote == "USDT" else f"{live_vol:.2f}"
            caption = (
                f"⚡ SPOT ANLIK PUMP DEDEKTÖRÜ — {sym}\n\n"
                f"📈 Değişim: +{verified_pct:.2f}% ({mins} dk içinde!)\n"
                f"💰 Başlangıç: {fmt_price(verified_min)}  ➡️  Anlık: {fmt_price(live_cur)}\n"
                f"💧 24s Parite Hacmi: {vol_str} ({quote})"
            )
            png = render_candlestick_png(
                c1m,
                title=f"{sym} | 1DK SPOT PUMP",
                cur_price=live_cur,
                levels=[
                    ("BAS", verified_min, (245, 158, 11)),
                    ("ANLIK", live_cur, (16, 185, 129)),
                ],
                interval="1m",
            )
            tg_send_photo(png, caption)
        except Exception:
            with state_lock:
                locks["pump"].pop(base, None)
                save_state()

    threading.Thread(target=verify_and_send, daemon=True).start()


# ==============================================================================
# MOTOR 2, 3, 4 (MA200) & MOTOR 5 (MONTH MAX / MONTH MIN)
# ==============================================================================
def scan_structural_pair(tk):
    sym = tk["symbol"]
    if not sym.endswith("USDT") or is_excluded(sym):
        return
    cur = float(tk["lastPrice"])
    vol24h = float(tk["quoteVolume"])
    chg24h = float(tk["priceChangePercent"])
    low24h = float(tk["lowPrice"])
    if cur <= 0 or vol24h < 1_500_000:
        return
    now_ts = time.time()

    # --- 1) 1G MA200 (Klasik SMA200) + 2) 30G Zirve/Dip (MONTH MAX & MONTH MIN) ---
    try:
        c1d = fetch_klines(sym, "1d", limit=235)
        if len(c1d) >= 203:
            closes = [b["c"] for b in c1d]
            closes[-1] = cur
            sma200 = compute_sma_series(closes, 200)
            ma_live = sma200[-1]
            if ma_live and ma_live > 0:
                new_pos = "ABOVE" if cur >= ma_live * 1.004 else ("BELOW" if cur <= ma_live * 0.996 else None)
                if new_pos:
                    crossed = False
                    with state_lock:
                        prev_ma = locks["ma200"].get(sym)
                        if not prev_ma:
                            locks["ma200"][sym] = {"dir": new_pos, "ts": now_ts}
                            save_state()
                        elif prev_ma["dir"] != new_pos:
                            locks["ma200"][sym] = {"dir": new_pos, "ts": now_ts}
                            crossed = True
                            save_state()
                    if crossed:
                        diff_pct = ((cur - ma_live) / ma_live) * 100.0
                        is_up = new_pos == "ABOVE"
                        icon = "🟢" if is_up else "🔴"
                        head = f"{icon} 1G MA200 {'YUKARI KIRILIMI' if is_up else 'AŞAĞI DÜŞÜŞ'} — #{sym}"
                        caption = (
                            f"{head}\n\n"
                            f"Anlık fiyat 200 Günlük MA ortalamasının {'üzerine' if is_up else 'altına'} canlı olarak kırıldı.\n"
                            f"📊 Anlık Fiyat: ${fmt_price(cur)} · Canlı MA200: ${fmt_price(ma_live)} ({diff_pct:+.2f}%)\n"
                            f"🔒 Ters kırılım gelene kadar tekrar sinyal gönderilmez."
                        )
                        png = render_candlestick_png(
                            c1d[-72:],
                            title=f"{sym} | 1G SMA200",
                            cur_price=cur,
                            levels=[
                                ("MA200", ma_live, (245, 158, 11)),
                                ("ANLIK", cur, (16, 185, 129) if is_up else (244, 63, 94)),
                            ],
                            ma_series=sma200[-72:],
                            interval="1d",
                        )
                        tg_send_photo(png, caption)

        # --- StormGain Tarzi 30G Zirve / Dip (MONTH MAX & MONTH MIN) ---
        if len(c1d) >= 32:
            prior30 = c1d[-31:-1]
            month_max = max(b["h"] for b in prior30)
            month_min = min(b["l"] for b in prior30)
            if month_max > month_min > 0:
                hit_dir = "MAX" if cur >= month_max else ("MIN" if cur <= month_min else "NEUTRAL")
                fire_month = None
                with state_lock:
                    prev_m = locks["month"].get(sym)
                    if not prev_m:
                        locks["month"][sym] = {"dir": hit_dir, "level": cur, "ts": now_ts}
                        save_state()
                    else:
                        if hit_dir == "MAX" and (prev_m["dir"] != "MAX" or cur > prev_m["level"] * 1.005):
                            fire_month = "MAX"
                        elif hit_dir == "MIN" and (prev_m["dir"] != "MIN" or cur < prev_m["level"] * 0.995):
                            fire_month = "MIN"
                        if fire_month:
                            locks["month"][sym] = {"dir": fire_month, "level": cur, "ts": now_ts}
                            save_state()

                if fire_month:
                    is_max = fire_month == "MAX"
                    with state_lock:
                        signals_today.append({
                            "sym": sym,
                            "engine": "MONTH",
                            "dir": "LONG" if is_max else "SHORT",
                            "entry": cur,
                            "peak": cur,
                            "ts": now_ts,
                        })
                    fut_line = fetch_futures_context_line(sym)
                    vol_c = f"{vol24h/1e6:.1f}M" if vol24h >= 1e6 else f"{round(vol24h/1e3)}K"
                    icon = "🟢" if is_max else "🔴"
                    m_lbl = "month max" if is_max else "month min"
                    lines = [
                        f"{icon} <b>#{sym}</b>, {m_lbl} değere ulaştı: <b>${fmt_price(cur)}</b>",
                        "",
                        f"📌 Son 30 günün {'en yüksek' if is_max else 'en düşük'} seviyesi (${fmt_price(month_max if is_max else month_min)}) {'yukarı' if is_max else 'aşağı'} kırıldı.",
                        f"💧 24s Değişim: {chg24h:+.2f}% · Hacim: {vol_c} USDT",
                    ]
                    if fut_line:
                        lines.append(f"🐋 Vadeli Bağlam: {fut_line}")
                    lines.append(
                        f"🔒 Kilit aktif: ${fmt_price(cur * (1.005 if is_max else 0.995))} {'üstüne çıkmadıkça' if is_max else 'altına düşmedikçe'} veya 30G {'dibe' if is_max else 'zirveye'} dönmedikçe tekrar gelmez."
                    )
                    png = render_candlestick_png(
                        c1d[-45:],
                        title=f"{sym} | 1G 30G ZIRVE/DIP",
                        cur_price=cur,
                        levels=[
                            ("30MAX", month_max, (16, 185, 129)),
                            ("30MIN", month_min, (244, 63, 94)),
                            ("ANLIK", cur, (56, 189, 248)),
                        ],
                        interval="1d",
                    )
                    tg_send_photo(png, "\n".join(lines))
    except Exception:
        pass

    # --- 3) 4h Akis / Emir Defteri Duvari & POC (1D + 3D Trend Onayli) ---
    try:
        if vol24h >= 2_500_000:
            depth = fetch_binance_json(f"/api/v3/depth?symbol={sym}&limit=100", 7.0)
            c15m = fetch_klines(sym, "15m", limit=96)
            c4h = fetch_klines(sym, "4h", limit=56)
            c1d_s = fetch_klines(sym, "1d", limit=12)
            c3d_s = fetch_klines(sym, "3d", limit=12)
            if len(c15m) >= 40 and len(c4h) >= 28 and len(c1d_s) >= 7 and len(c3d_s) >= 5:
                ma1d = sum(b["c"] for b in c1d_s[-7:]) / 7.0
                ma3d = sum(b["c"] for b in c3d_s[-5:]) / 5.0
                trend1d = "LONG" if cur >= ma1d else "SHORT"
                trend3d = "LONG" if cur >= ma3d else "SHORT"

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
                    poc = d_lo + (poc_idx + 0.5) * step_p

                    best_bid = (0.0, 0.0)
                    best_ask = (0.0, 0.0)
                    for ps, qs in depth.get("bids", []):
                        p, q = float(ps), float(qs)
                        usd = p * q
                        if abs(cur - p) / cur <= 0.025 and usd > best_bid[1]:
                            best_bid = (p, usd)
                    for ps, qs in depth.get("asks", []):
                        p, q = float(ps), float(qs)
                        usd = p * q
                        if abs(p - cur) / cur <= 0.025 and usd > best_ask[1]:
                            best_ask = (p, usd)

                    bias = None
                    wall_p, wall_usd, wall_lbl = 0.0, 0.0, ""
                    if best_ask[1] >= 120_000 and cur <= best_ask[0] * 1.004 and best_ask[1] >= best_bid[1] * 1.15:
                        bias, wall_p, wall_usd, wall_lbl = "SHORT", best_ask[0], best_ask[1], "Ask (Satış) Duvarı"
                    elif best_bid[1] >= 120_000 and cur >= best_bid[0] * 0.996 and best_bid[1] >= best_ask[1] * 1.15:
                        bias, wall_p, wall_usd, wall_lbl = "LONG", best_bid[0], best_bid[1], "Bid (Alış) Duvarı"

                    if bias and bias == trend1d == trend3d:
                        should_fire = False
                        with state_lock:
                            if locks["flow"].get(sym, {}).get("dir") != bias:
                                locks["flow"][sym] = {"dir": bias, "ts": now_ts}
                                save_state()
                                should_fire = True
                        if should_fire:
                            sw_hi = max(b["h"] for b in c4h[-28:])
                            sw_lo = min(b["l"] for b in c4h[-28:])
                            rng = max(sw_hi - sw_lo, cur * 0.03)
                            tp1 = min(cur * 0.97, cur - rng * 0.25) if bias == "SHORT" else max(cur * 1.03, cur + rng * 0.25)
                            tp2 = min(cur * 0.948, cur - rng * 0.45) if bias == "SHORT" else max(cur * 1.052, cur + rng * 0.45)
                            tp3 = tp2 * (0.975 if bias == "SHORT" else 1.025)
                            stop = max(wall_p * 1.005, cur * 1.008) if bias == "SHORT" else min(wall_p * 0.995, cur * 0.992)
                            trigger = cur * (0.992 if bias == "SHORT" else 1.008)
                            exchanges = fetch_multi_exchange_sync(sym, cur)

                            with state_lock:
                                signals_today.append({"sym": sym, "engine": "FLOW", "dir": bias, "entry": cur, "peak": cur, "ts": now_ts})

                            def ps(v):
                                return f"{((v - cur) / cur) * 100:+.1f}%"

                            caption = (
                                f"#{sym} ({bias})\n\n"
                                f"Fiyat: {fmt_price(cur)}\n"
                                f"Hedefler: {fmt_price(tp1)} ({ps(tp1)}) → {fmt_price(tp2)} ({ps(tp2)}) → {fmt_price(tp3)} ({ps(tp3)})\n"
                                f"İptal: {fmt_price(stop)} {'üstünde' if bias == 'SHORT' else 'altında'} ({ps(stop)})\n\n"
                                f"{fmt_price(wall_p)} {wall_lbl} (${round(wall_usd/1000)}k) · POC: {fmt_price(poc)}\n"
                                f"1D ve 3 Günlük (3D) grafik aynı yönü ({bias}) gösteriyor · Senkron: {', '.join(exchanges)}\n"
                                f"Anahtar tetik: {fmt_price(trigger)} {'altında' if bias == 'SHORT' else 'üstünde'} kapanış/kalıcılık.\n"
                                f"🔒 Yön kilidi aktif: Ters yöne dönmeden aynı coinden tekrar sinyal gelmez."
                            )
                            png = render_candlestick_png(
                                c4h,
                                title=f"{sym} | 4S {bias} AKIS",
                                cur_price=cur,
                                levels=[
                                    ("TP1", tp1, (16, 185, 129)),
                                    ("TP2", tp2, (52, 211, 153)),
                                    ("STOP", stop, (244, 63, 94)),
                                    ("POC", poc, (245, 158, 11)),
                                    ("ANLIK", cur, (56, 189, 248)),
                                ],
                                interval="4h",
                            )
                            tg_send_photo(png, caption)
    except Exception:
        pass

    # --- 4) W1 ATL (Tum zamanlarin en dusugu - kesin kilit) ---
    try:
        cached = atl_cache.get(sym)
        if not cached or (now_ts - cached[1]) > 3600:
            c1w = fetch_klines(sym, "1w", limit=1000)
            if len(c1w) >= 12:
                prev_atl = min(b["l"] for b in c1w[:-1])
                cached = (prev_atl, now_ts)
                atl_cache[sym] = cached
        if cached and cached[0] > 0:
            prev_atl = cached[0]
            with state_lock:
                atl_rec = locks["atl"].get(sym)
                floor_atl = min(prev_atl, atl_rec["atl"]) if atl_rec else prev_atl
                broke = (cur < floor_atl * 0.995) if atl_rec else (cur < floor_atl)
                should_fire = bool(broke and low24h < prev_atl)
                if should_fire:
                    locks["atl"][sym] = {"atl": cur, "ts": now_ts}
                    save_state()
            if should_fire:
                c1h = fetch_klines(sym, "1h", limit=72)
                caption = (
                    f"📉 #{sym} ${fmt_price(cur)} tüm zamanların en düşüğü\n\n"
                    f"Önceki en düşük (${fmt_price(floor_atl)}) kesin olarak aşağı kırıldı.\n"
                    f"24s Değişim: {chg24h:+.2f}%\n"
                    f"🔒 Kilit aktif: ${fmt_price(cur)} altına düşmedikçe tekrar ATL sinyali gelmez."
                )
                png = render_candlestick_png(
                    c1h,
                    title=f"{sym} | 1S W1 ATL",
                    cur_price=cur,
                    levels=[
                        ("ATL", floor_atl, (244, 63, 94)),
                        ("ANLIK", cur, (245, 158, 11)),
                    ],
                    interval="1h",
                )
                tg_send_photo(png, caption)
    except Exception:
        pass


# ==============================================================================
# MOTOR 8: BINANCE RESMI DUYURU & HABER TAKIBI
# ==============================================================================
def translate_binance_title(title):
    pairs = [
        ("Binance Will List", "Binance Listeliyor:"),
        ("Binance Will Add", "Binance Ekliyor:"),
        ("Binance Will Support", "Binance Destekliyor:"),
        ("Binance Has Completed the", "Binance Tamamladı:"),
        ("Binance Futures Will Launch", "Binance Vadeli Başlatıyor:"),
        ("Token Merge", "Token Birleşmesi (Merge)"),
        ("Contract Swap", "Kontrat Değişimi (Swap)"),
        ("Withdrawals Reopened", "Çekim İşlemleri Yeniden Açıldı"),
        ("Notice of Removal of Spot Trading Pairs", "Spot İşlem Çiftleri Kaldırma (Delist)"),
        ("Binance Will Delist", "Binance Delist Ediyor:"),
        ("Network Upgrade", "Ağ Güncellemesi"),
    ]
    out = title.strip()
    for eng, tr in pairs:
        out = re.sub(re.escape(eng), tr, out, flags=re.IGNORECASE)
    return out


def poll_news():
    global news_seeded
    catalogs = [(48, "Yeni Listeleme"), (49, "Binance Duyuru"), (161, "Delist / Kaldırma")]
    for cat_id, cat_lbl in catalogs:
        try:
            url = f"https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query?catalogId={cat_id}&pageNo=1&pageSize=6"
            d = http_get_json(url, 8.0)
            for art in d.get("data", {}).get("articles", []):
                aid = str(art.get("id") or art.get("code") or art.get("title") or "")
                if not aid or aid in news_seen:
                    continue
                news_seen.add(aid)
                if news_seeded:
                    code = str(art.get("code") or "")
                    link = f"https://www.binance.com/en/support/announcement/detail/{code}" if code else "https://www.binance.com/en/support/announcement"
                    tg_send_text(f"📰 <b>Binance | {cat_lbl}</b>\n{translate_binance_title(art.get('title', ''))}\n{link}")
        except Exception:
            pass
    news_seeded = True


# ==============================================================================
# ANA DONGULER & GUNLUK RAPORLAR
# ==============================================================================
latest_usdt_liquid = []


def ticker_loop():
    global latest_usdt_liquid
    while True:
        try:
            arr = fetch_binance_json("/api/v3/ticker/24hr", 9.0)
            now_ts = time.time()
            liq = []
            for item in arr:
                sym = item["symbol"]
                if is_excluded(sym):
                    continue
                p = float(item["lastPrice"])
                v = float(item["quoteVolume"])
                if p <= 0:
                    continue
                check_pump_ticker(sym, p, v, now_ts)
                if sym.endswith("USDT") and v >= 1_500_000:
                    liq.append(item)
                with state_lock:
                    for sig in signals_today:
                        if sig["sym"] == sym:
                            if sig["dir"] == "SHORT":
                                if ((sig["entry"] - p) / sig["entry"]) > ((sig["entry"] - sig["peak"]) / sig["entry"]):
                                    sig["peak"] = p
                            else:
                                if p > sig["peak"]:
                                    sig["peak"] = p
            liq.sort(key=lambda x: float(x["quoteVolume"]), reverse=True)
            latest_usdt_liquid = liq[:300]
        except Exception as e:
            print("ticker_loop hata:", e)
        time.sleep(3)


def structure_loop():
    cursor = 0
    while True:
        try:
            snap = list(latest_usdt_liquid)
            if snap:
                batch = []
                for _ in range(min(8, len(snap))):
                    batch.append(snap[cursor % len(snap)])
                    cursor += 1
                threads = [threading.Thread(target=scan_structural_pair, args=(tk,), daemon=True) for tk in batch]
                for th in threads:
                    th.start()
                for th in threads:
                    th.join(timeout=15)
        except Exception as e:
            print("structure_loop hata:", e)
        time.sleep(4)


def daily_report_and_news_loop():
    global last_daily_21, last_daily_23
    while True:
        try:
            poll_news()
            now_tr = tr_now()
            day_key = now_tr.strftime("%Y-%m-%d")
            with state_lock:
                cutoff = time.time() - 86400
                signals_today[:] = [s for s in signals_today if s["ts"] >= cutoff]
                ranked = []
                for s in signals_today:
                    pct = ((s["entry"] - s["peak"]) / s["entry"] * 100.0) if s["dir"] == "SHORT" else ((s["peak"] - s["entry"]) / s["entry"] * 100.0)
                    ranked.append((pct, s))
                ranked.sort(key=lambda x: x[0], reverse=True)

            if now_tr.hour == 21 and last_daily_21 != day_key and ranked:
                last_daily_21 = day_key
                top_lines = [f"{i+1}. #{s['sym']} ({s['engine']} {s['dir']}) → +{pct:.2f}%" for i, (pct, s) in enumerate(ranked[:5])]
                tg_send_text("🏆 <b>GÜNÜN ZAFERİ (21:00 TR)</b>\n\n" + "\n".join(top_lines))

            if now_tr.hour == 23 and last_daily_23 != day_key and ranked:
                last_daily_23 = day_key
                top_lines = [f"{i+1}. #{s['sym']} ({s['engine']}) → +{pct:.2f}%" for i, (pct, s) in enumerate(ranked[:10])]
                tg_send_text("📊 <b>TOP 24 SAAT ÖZETİ (23:00 TR)</b>\n\n" + "\n".join(top_lines))
        except Exception as e:
            print("daily_news_loop hata:", e)
        time.sleep(60)


class KeepAliveHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        with state_lock:
            payload = {
                "status": "ok",
                "locks": {k: len(v) for k, v in locks.items()},
                "liquid_pairs": len(latest_usdt_liquid),
            }
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def log_message(self, format, *args):
        return


def main():
    load_state()
    threading.Thread(target=ticker_loop, daemon=True).start()
    threading.Thread(target=structure_loop, daemon=True).start()
    threading.Thread(target=daily_report_and_news_loop, daemon=True).start()
    with socketserver.TCPServer(("0.0.0.0", PORT), KeepAliveHandler) as httpd:
        print(f"Render Spot Bot calisiyor (PORT={PORT})")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
