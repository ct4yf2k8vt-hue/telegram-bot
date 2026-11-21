#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spot Akis Terminali - Bagimsiz Render Telegram Botu (Sifir Harici Kutuphane)
4 Katmanli Akilli Veri Yonlendirici:
  1) Kalici Binance WS-API (wss://ws-api.binance.com/ws-api/v3) + Proaktif Kota Freni
  2) Binance HTTP REST Aynalari (418/429 Ayrik Devre Kesici)
  3) Coklu Borsa Yedegi (Bybit v5 + OKX v5 - Render IP 418 Banindan Bagimsiz)
  4) Canli WebSocket Tik Tamponu (15SN CANLI OHLC Grafik Guvencesi)
"""

import os
import sys
import ssl
import json
import time
import math
import zlib
import base64
import socket
import struct
import threading
import urllib.error
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
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
]
WS_HOSTS = [
    ("data-stream.binance.vision", 443, "/ws/!miniTicker@arr"),
    ("stream.binance.com", 9443, "/ws/!miniTicker@arr"),
]

preferred_mirror_idx = 0
mirror_lock = threading.Lock()
rest_ban_until = 0.0
ws_api_ban_until = 0.0
ban_lock = threading.Lock()

TR_TZ = timezone(timedelta(hours=3))

ticker_lock = threading.Lock()
state_lock = threading.Lock()
ticker_map = {}
price_window_history = {}
signals_today = []
atl_weekly_cache = {}
daily_stats_cache = {}
flow_scan_cache = {}
news_seen_ids = set()
news_initial_seeded = False
report_sent_days = {"victory": "", "top24": ""}
last_peak_save_ts = 0.0

router_state = {
    "active_data_source": "ws-api",
    "ws_api_connected": False,
    "ip_weight_used": 0,
    "ip_weight_limit": 6000,
    "ip_weight_ts": 0.0,
    "consecutive_all_fail": 0,
    "outage_notified": False,
    "fallback_calls": 0,
}

diag_status = {
    "ws_connected": False,
    "ws_host": "",
    "ws_messages": 0,
    "signals_sent": 0,
    "last_signal": "",
    "last_tg_error": "",
    "last_binance_error": "",
    "telegram_bot_ok": False,
    "structural_scans": 0,
}

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
    body_w = min(22, max(2, int(step * 0.62)))
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
            for k in ("pump", "atl", "flow", "ma200", "month", "year", "exhaust"):
                if isinstance(raw["locks"].get(k), dict):
                    locks[k] = raw["locks"][k]
        if isinstance(raw.get("signals_today"), list):
            signals_today = raw["signals_today"][-250:]
    except Exception:
        pass


# ==============================================================================
# WEBSOCKET YARDIMCILARI VE KALICI BINANCE WS-API ISTEMCISI
# ==============================================================================
def _ws_send_frame(sock, opcode: int, payload: bytes):
    mask_key = os.urandom(4)
    length = len(payload)
    header = bytearray([0x80 | (opcode & 0x0F)])
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    header.extend(mask_key)
    masked = bytes(payload[i] ^ mask_key[i % 4] for i in range(length))
    sock.sendall(bytes(header) + masked)


def trigger_binance_ban(scope: str, code: int, retry_sec: float = 0.0):
    global rest_ban_until, ws_api_ban_until
    sec = retry_sec if retry_sec and retry_sec > 0 else (135.0 if code == 418 else 25.0)
    target = time.time() + min(sec + 5.0, 1800.0)
    with ban_lock:
        if scope == "ws-api":
            if target > ws_api_ban_until:
                ws_api_ban_until = target
        else:
            if target > rest_ban_until:
                rest_ban_until = target
    print(f"[Kota Koruma:{scope}] HTTP {code} -> Yedek borsalar devrede ({round(target - time.time())}s).", flush=True)


class PersistentWsApiClient:
    """Tek TLS baglantisi uzerinden calisan, rateLimits okumali kalici WS-API istemcisi."""

    def __init__(self):
        self._sock = None
        self._send_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending = {}
        self._seq = 0
        self._started = False

    def start(self):
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._run_forever, daemon=True).start()

    def _close_sock(self):
        s = self._sock
        self._sock = None
        router_state["ws_api_connected"] = False
        if s:
            try:
                s.close()
            except Exception:
                pass
        with self._pending_lock:
            for item in self._pending.values():
                item["event"].set()

    def _run_forever(self):
        ctx = ssl.create_default_context()
        while True:
            try:
                raw_sock = socket.create_connection(("ws-api.binance.com", 443), timeout=10)
                sock = ctx.wrap_socket(raw_sock, server_hostname="ws-api.binance.com")
                sock.settimeout(25)
                key = base64.b64encode(os.urandom(16)).decode("ascii")
                req = (
                    "GET /ws-api/v3?returnRateLimits=true HTTP/1.1\r\n"
                    "Host: ws-api.binance.com:443\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {key}\r\n"
                    "Sec-WebSocket-Version: 13\r\n\r\n"
                )
                sock.sendall(req.encode("ascii"))
                buf = b""
                while b"\r\n\r\n" not in buf:
                    chunk = sock.recv(4096)
                    if not chunk:
                        raise RuntimeError("ws-api handshake EOF")
                    buf += chunk
                if b"101" not in buf.split(b"\r\n", 1)[0]:
                    raise RuntimeError("ws-api 101 reddedildi")

                self._sock = sock
                router_state["ws_api_connected"] = True
                rest = buf.split(b"\r\n\r\n", 1)[1]
                frag = bytearray()

                def _ensure(n_bytes):
                    nonlocal rest
                    while len(rest) < n_bytes:
                        try:
                            ch = sock.recv(65536)
                        except socket.timeout:
                            with self._send_lock:
                                _ws_send_frame(sock, 0x9, b"hb")
                            continue
                        if not ch:
                            raise RuntimeError("ws-api EOF")
                        rest += ch

                while True:
                    _ensure(2)
                    b1, b2 = rest[0], rest[1]
                    fin = (b1 & 0x80) != 0
                    opcode = b1 & 0x0F
                    masked = (b2 & 0x80) != 0
                    ln = b2 & 0x7F
                    off = 2
                    if ln == 126:
                        _ensure(4)
                        ln = struct.unpack("!H", rest[2:4])[0]
                        off = 4
                    elif ln == 127:
                        _ensure(10)
                        ln = struct.unpack("!Q", rest[2:10])[0]
                        off = 10
                    mask_b = b""
                    if masked:
                        _ensure(off + 4)
                        mask_b = rest[off : off + 4]
                        off += 4
                    _ensure(off + ln)
                    pl = rest[off : off + ln]
                    rest = rest[off + ln :]
                    if masked and mask_b:
                        pl = bytes(pl[i] ^ mask_b[i % 4] for i in range(ln))

                    if opcode == 0x8:
                        break
                    if opcode == 0x9:
                        with self._send_lock:
                            _ws_send_frame(sock, 0xA, pl)
                        continue
                    if opcode == 0xA:
                        continue
                    if opcode in (0x1, 0x2, 0x0):
                        frag.extend(pl)
                        if fin:
                            raw_txt = bytes(frag).decode("utf-8", errors="ignore")
                            frag.clear()
                            try:
                                msg = json.loads(raw_txt)
                            except Exception:
                                continue
                            for rl in msg.get("rateLimits") or []:
                                if rl.get("rateLimitType") == "REQUEST_WEIGHT":
                                    router_state["ip_weight_used"] = int(rl.get("count") or 0)
                                    router_state["ip_weight_limit"] = int(rl.get("limit") or 6000)
                                    router_state["ip_weight_ts"] = time.time()
                            rid = msg.get("id")
                            if rid is not None:
                                with self._pending_lock:
                                    slot = self._pending.get(str(rid))
                                if slot:
                                    slot["resp"] = msg
                                    slot["event"].set()
            except Exception:
                pass
            finally:
                self._close_sock()
            time.sleep(3)

    def call(self, method: str, params: dict, timeout=6.0):
        self.start()
        if not self._sock:
            for _ in range(15):
                if self._sock:
                    break
                time.sleep(0.1)
        sock = self._sock
        if not sock:
            raise RuntimeError("ws-api bagli degil")

        with self._pending_lock:
            self._seq += 1
            rid = f"r{self._seq}"
            ev = threading.Event()
            self._pending[rid] = {"event": ev, "resp": None}

        payload = json.dumps({"id": rid, "method": method, "params": params}).encode("utf-8")
        try:
            with self._send_lock:
                _ws_send_frame(sock, 0x1, payload)
        except Exception as e:
            with self._pending_lock:
                self._pending.pop(rid, None)
            self._close_sock()
            raise e

        ev.wait(timeout=timeout)
        with self._pending_lock:
            slot = self._pending.pop(rid, None)
        resp = slot["resp"] if slot else None
        if not resp:
            raise TimeoutError("ws-api zaman asimi")
        status = int(resp.get("status") or 0)
        if status in (418, 429):
            err_obj = resp.get("error") or {}
            data_obj = err_obj.get("data") or {}
            retry_ms = float(data_obj.get("retryAfter") or 0)
            if retry_ms > 10_000:
                retry_sec = max(10.0, (retry_ms - time.time() * 1000.0) / 1000.0)
            else:
                retry_sec = retry_ms / 1000.0 if retry_ms > 0 else 0.0
            trigger_binance_ban("ws-api", status, retry_sec)
            raise RuntimeError(f"ws-api HTTP {status}")
        if status != 200:
            raise RuntimeError(f"ws-api status {status}: {(resp.get('error') or {}).get('msg', '')}")
        return resp.get("result")


ws_api_client = PersistentWsApiClient()


# ==============================================================================
# HTTP / COKLU BORSA VERI YONLENDIRICISI (BINANCE -> BYBIT -> OKX)
# ==============================================================================
def http_get_json(url: str, timeout=9.0):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; SpotFlowBot/3.0)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _binance_rest_json(path_and_query: str, timeout=7.5):
    global preferred_mirror_idx
    with ban_lock:
        if time.time() < rest_ban_until:
            raise RuntimeError("rest_banned")
    last_err = None
    n = len(BINANCE_MIRRORS)
    with mirror_lock:
        start_idx = preferred_mirror_idx
    for offset in range(n):
        idx = (start_idx + offset) % n
        m = BINANCE_MIRRORS[idx]
        try:
            data = http_get_json(m + path_and_query, timeout=timeout)
            with mirror_lock:
                if preferred_mirror_idx != idx:
                    preferred_mirror_idx = idx
            diag_status["last_binance_error"] = ""
            return data
        except urllib.error.HTTPError as he:
            last_err = he
            if he.code in (418, 429):
                try:
                    r_sec = float(he.headers.get("Retry-After", "0") or 0)
                except Exception:
                    r_sec = 0.0
                trigger_binance_ban("rest", he.code, r_sec)
                diag_status["last_binance_error"] = f"HTTP {he.code}"
                raise he
        except Exception as e:
            last_err = e
    diag_status["last_binance_error"] = str(last_err)
    raise last_err or RuntimeError("Binance REST erisilemedi")


def _parse_binance_kline_rows(raw):
    out = []
    if not isinstance(raw, list):
        return out
    for k in raw:
        out.append({
            "open_time": int(float(k[0]) / 1000),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "quote_vol": float(k[7]),
            "taker_buy_quote": float(k[10]),
        })
    return out


def _estimate_taker_buy(o: float, h: float, l: float, c: float, qvol: float) -> float:
    rng = h - l
    if rng <= 1e-12:
        return qvol * (0.60 if c >= o else 0.40)
    ratio = max(0.08, min(0.92, (c - l) / rng))
    return qvol * ratio


def _fetch_bybit_klines(symbol: str, interval: str, limit=240):
    imap = {"1m": "1", "15m": "15", "1h": "60", "4h": "240", "1d": "D", "1w": "W"}
    bi = imap.get(interval)
    if not bi:
        return []
    url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={urllib.parse.quote(symbol)}&interval={bi}&limit={min(limit, 1000)}"
    data = http_get_json(url, timeout=6.0)
    if data.get("retCode") != 0:
        return []
    rows = (data.get("result") or {}).get("list") or []
    out = []
    for k in reversed(rows):
        o, h, l, c, v, q = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), float(k[6])
        out.append({
            "open_time": int(float(k[0]) / 1000),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": v,
            "quote_vol": q,
            "taker_buy_quote": _estimate_taker_buy(o, h, l, c, q),
        })
    return out


def _fetch_okx_klines(symbol: str, interval: str, limit=240):
    bq = extract_base_quote(symbol)
    if not bq:
        return []
    imap = {"1m": "1m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W"}
    oi = imap.get(interval)
    if not oi:
        return []
    inst = f"{bq[0]}-{bq[1]}"
    url = f"https://www.okx.com/api/v5/market/candles?instId={urllib.parse.quote(inst)}&bar={oi}&limit={min(limit, 300)}"
    data = http_get_json(url, timeout=6.0)
    if str(data.get("code")) != "0":
        return []
    rows = data.get("data") or []
    out = []
    for k in reversed(rows):
        o, h, l, c, v, q = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), float(k[7])
        out.append({
            "open_time": int(float(k[0]) / 1000),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": v,
            "quote_vol": q,
            "taker_buy_quote": _estimate_taker_buy(o, h, l, c, q),
        })
    return out


def _price_matches_binance(symbol: str, last_price: float) -> bool:
    if last_price <= 0:
        return False
    with ticker_lock:
        tk = ticker_map.get(symbol)
    if not tk or tk["price"] <= 0:
        return True
    return abs(last_price - tk["price"]) / tk["price"] <= 0.015


def aggregate_daily_to_3d(c1d):
    if not c1d or len(c1d) < 15:
        return []
    bars = []
    n = len(c1d)
    start = n % 3
    for i in range(start, n, 3):
        chunk = c1d[i : i + 3]
        if not chunk:
            continue
        bars.append({
            "open_time": chunk[0]["open_time"],
            "open": chunk[0]["open"],
            "high": max(b["high"] for b in chunk),
            "low": min(b["low"] for b in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(b["volume"] for b in chunk),
            "quote_vol": sum(b["quote_vol"] for b in chunk),
            "taker_buy_quote": sum(b["taker_buy_quote"] for b in chunk),
        })
    return bars


def _note_router_result(source: str, ok: bool):
    if ok:
        was_outage = router_state["outage_notified"]
        router_state["active_data_source"] = source
        router_state["consecutive_all_fail"] = 0
        if source in ("bybit", "okx"):
            router_state["fallback_calls"] += 1
        if was_outage:
            router_state["outage_notified"] = False
            if BOT_TOKEN:
                tg_send_text(f"🟢 <b>Veri akışı yeniden sağlandı</b> ({source.upper()}) — Tüm taramalar aktif.")
    else:
        router_state["consecutive_all_fail"] += 1
        if router_state["consecutive_all_fail"] >= 12 and not router_state["outage_notified"]:
            router_state["outage_notified"] = True
            if BOT_TOKEN:
                tg_send_text(
                    "⚠️ <b>Tüm Borsa Kaynakları Geçici Meşgul</b>\n"
                    "Hem Binance hem yedek borsalar (Bybit/OKX) yanıt vermiyor. WebSocket (0–7 dk Spot Pump) çalışmaya devam ediyor."
                )


def _is_ip_weight_hot() -> bool:
    return (time.time() - router_state["ip_weight_ts"]) < 60.0 and router_state["ip_weight_used"] >= 4000


def fetch_spot_klines(symbol: str, interval: str, limit=240):
    now = time.time()
    with ban_lock:
        ws_banned = now < ws_api_ban_until
        rest_banned = now < rest_ban_until
    weight_hot = _is_ip_weight_hot()

    # 1. Katman: Kalici Binance WS-API
    if not ws_banned and not weight_hot:
        try:
            raw = ws_api_client.call("klines", {"symbol": symbol, "interval": interval, "limit": min(limit, 1000)}, timeout=6.0)
            bars = _parse_binance_kline_rows(raw)
            if bars:
                _note_router_result("ws-api", True)
                return bars
        except Exception:
            pass

    # 2. Katman: Binance HTTP REST Aynalari
    if not rest_banned and not weight_hot:
        try:
            raw = _binance_rest_json(
                f"/api/v3/klines?symbol={urllib.parse.quote(symbol)}&interval={urllib.parse.quote(interval)}&limit={min(limit, 1000)}",
                timeout=7.0,
            )
            bars = _parse_binance_kline_rows(raw)
            if bars:
                _note_router_result("rest", True)
                return bars
        except Exception:
            pass

    # 3. Katman: Bybit -> OKX Spot Yedegi (Binance 418/429 veya kota >= 4000 ise)
    if interval == "3d":
        c1d_rec = daily_stats_cache.get(symbol)
        if c1d_rec and len(c1d_rec.get("c1d", [])) >= 18:
            bars_3d = aggregate_daily_to_3d(c1d_rec["c1d"][-36:])
            if bars_3d:
                return bars_3d[-limit:]

    for src_name, fn in (("bybit", _fetch_bybit_klines), ("okx", _fetch_okx_klines)):
        try:
            bars = fn(symbol, interval, limit)
            if bars and _price_matches_binance(symbol, bars[-1]["close"]):
                _note_router_result(src_name, True)
                return bars
        except Exception:
            pass

    _note_router_result("none", False)
    return []


def fetch_spot_depth(symbol: str, limit=100):
    now = time.time()
    with ban_lock:
        ws_banned = now < ws_api_ban_until
        rest_banned = now < rest_ban_until
    weight_hot = _is_ip_weight_hot()

    if not ws_banned and not weight_hot:
        try:
            res = ws_api_client.call("depth", {"symbol": symbol, "limit": limit}, timeout=5.5)
            if isinstance(res, dict) and res.get("bids") and res.get("asks"):
                _note_router_result("ws-api", True)
                return res
        except Exception:
            pass

    if not rest_banned and not weight_hot:
        try:
            res = _binance_rest_json(f"/api/v3/depth?symbol={urllib.parse.quote(symbol)}&limit={limit}", timeout=6.0)
            if isinstance(res, dict) and res.get("bids") and res.get("asks"):
                _note_router_result("rest", True)
                return res
        except Exception:
            pass

    # Bybit orderbook yedegi
    try:
        data = http_get_json(f"https://api.bybit.com/v5/market/orderbook?category=spot&symbol={urllib.parse.quote(symbol)}&limit={min(limit, 200)}", timeout=5.0)
        res = data.get("result") or {}
        bids, asks = res.get("b") or [], res.get("a") or []
        if data.get("retCode") == 0 and bids and asks:
            mid = (float(bids[0][0]) + float(asks[0][0])) / 2.0
            if _price_matches_binance(symbol, mid):
                _note_router_result("bybit", True)
                return {"bids": bids, "asks": asks}
    except Exception:
        pass

    # OKX orderbook yedegi
    try:
        bq = extract_base_quote(symbol)
        if bq:
            inst = f"{bq[0]}-{bq[1]}"
            data = http_get_json(f"https://www.okx.com/api/v5/market/books?instId={urllib.parse.quote(inst)}&sz={min(limit, 400)}", timeout=5.0)
            rows = data.get("data") or []
            if str(data.get("code")) == "0" and rows:
                bids, asks = rows[0].get("bids") or [], rows[0].get("asks") or []
                if bids and asks:
                    mid = (float(bids[0][0]) + float(asks[0][0])) / 2.0
                    if _price_matches_binance(symbol, mid):
                        _note_router_result("okx", True)
                        return {"bids": bids, "asks": asks}
    except Exception:
        pass

    _note_router_result("none", False)
    return None


def build_ohlc_from_ticks(ticks, live_cur: float, bucket_sec=15):
    pts = list(ticks or [])
    now_ms = int(time.time() * 1000)
    if live_cur > 0:
        pts.append((now_ms, live_cur))
    if len(pts) < 2:
        return []
    buckets = {}
    order = []
    for ts_ms, price in pts:
        if price <= 0:
            continue
        bkey = int(ts_ms // (bucket_sec * 1000))
        if bkey not in buckets:
            buckets[bkey] = {
                "open_time": bkey * bucket_sec,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": 0.0,
                "quote_vol": 0.0,
                "taker_buy_quote": 0.0,
            }
            order.append(bkey)
        else:
            b = buckets[bkey]
            if price > b["high"]:
                b["high"] = price
            if price < b["low"]:
                b["low"] = price
            b["close"] = price
    bars = [buckets[k] for k in sorted(order)]
    if len(bars) == 1 and live_cur > 0:
        b0 = bars[0]
        bars = [
            {**b0, "close": b0["open"]},
            {**b0, "open": b0["open"], "close": live_cur},
        ]
    return bars[-60:]


def fetch_multi_exchange_sync(symbol: str, binance_price: float):
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    exchanges = ["Binance"]
    lock = threading.Lock()
    checks = [
        ("Bybit", f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}", lambda d: float(d["result"]["list"][0]["lastPrice"])),
        ("OKX", f"https://www.okx.com/api/v5/market/ticker?instId={base}-USDT", lambda d: float(d["data"][0]["last"])),
        ("Gate", f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={base}_USDT", lambda d: float(d[0]["last"])),
    ]

    def _chk(name, url, parser):
        try:
            p = parser(http_get_json(url, timeout=2.0))
            if p > 0 and abs(p - binance_price) / binance_price < 0.02:
                with lock:
                    exchanges.append(name)
        except Exception:
            pass

    threads = [threading.Thread(target=_chk, args=c, daemon=True) for c in checks]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.2)
    return exchanges


def fetch_futures_context_line(symbol: str):
    if not symbol.endswith("USDT"):
        return None
    res = {}
    lock = threading.Lock()

    def _q(key, url, fn):
        try:
            val = fn(http_get_json(url, timeout=1.8))
            if val:
                with lock:
                    res[key] = val
        except Exception:
            pass

    qs = urllib.parse.quote(symbol)
    jobs = [
        ("oi", f"https://fapi.binance.com/fapi/v1/openInterest?symbol={qs}", lambda d: f"OI: {round(float(d.get('openInterest') or 0)):,}" if float(d.get("openInterest") or 0) > 0 else ""),
        ("top", f"https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={qs}&period=1h&limit=1", lambda d: (lambda lp: f"Balina: %{lp}L/%{100-lp}S" if lp > 0 else "")(round(float(d[0].get("longAccount") or 0) * 100)) if isinstance(d, list) and d else ""),
        ("gls", f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={qs}&period=1h&limit=1", lambda d: (lambda lp: f"Kalabalık: %{lp}L/%{100-lp}S" if lp > 0 else "")(round(float(d[0].get("longAccount") or 0) * 100)) if isinstance(d, list) and d else ""),
    ]
    threads = [threading.Thread(target=_q, args=j, daemon=True) for j in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)
    parts = [res[k] for k in ("oi", "top", "gls") if res.get(k)]
    return " · ".join(parts) if parts else None


# ==============================================================================
# TELEGRAM TESLIM KATMANI
# ==============================================================================
def _extract_http_err(e):
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read().decode("utf-8", errors="ignore")
            return f"HTTP {e.code}: {body[:200]}"
        except Exception:
            return f"HTTP {e.code}"
    return str(e)


def verify_telegram_token():
    if not BOT_TOKEN:
        diag_status["last_tg_error"] = "BOT_TOKEN tanimli degil (Render Environment Variables'a ekleyin)"
        print("[UYARI] BOT_TOKEN tanimli degil! Telegram mesajlari gonderilemez.", flush=True)
        return False
    try:
        res = http_get_json(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=8.0)
        ok = bool(res.get("ok"))
        diag_status["telegram_bot_ok"] = ok
        if ok:
            diag_status["last_tg_error"] = ""
            uname = (res.get("result") or {}).get("username", "bot")
            print(f"[Telegram] Baglanti basarili: @{uname}", flush=True)
            tg_send_text(f"✅ <b>Spot Akış Botu aktif!</b> (@{uname}) — Çoklu kaynaklı (WS-API + Yedek Borsa) akış başladı.")
        return ok
    except Exception as e:
        err_msg = _extract_http_err(e)
        diag_status["last_tg_error"] = err_msg
        print(f"[Telegram getMe Hata] {err_msg}", flush=True)
        return False


def tg_send_text(text: str, chat_id: str = None, tag_label: str = "") -> bool:
    if not BOT_TOKEN:
        diag_status["last_tg_error"] = "BOT_TOKEN tanimli degil"
        return False
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
        with urllib.request.urlopen(req, timeout=12) as _:
            pass
        diag_status["last_tg_error"] = ""
        if tag_label:
            diag_status["signals_sent"] += 1
            diag_status["last_signal"] = tag_label
        return True
    except Exception as e:
        err_msg = _extract_http_err(e)
        diag_status["last_tg_error"] = err_msg
        print(f"[Telegram Hata] sendMessage: {err_msg}", flush=True)
        return False


def tg_send_photo(caption: str, png_bytes: bytes, chat_id: str = None, tag_label: str = "") -> bool:
    if not BOT_TOKEN:
        diag_status["last_tg_error"] = "BOT_TOKEN tanimli degil"
        return False
    if not png_bytes:
        return tg_send_text(caption, chat_id=chat_id, tag_label=tag_label)
    target = chat_id or CHAT_ID
    boundary = f"----SpotBotBoundary{int(time.time() * 1000)}"
    parts = []
    for name, val in [("chat_id", target), ("caption", caption[:1024]), ("parse_mode", "HTML")]:
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
        diag_status["last_tg_error"] = ""
        if tag_label:
            diag_status["signals_sent"] += 1
            diag_status["last_signal"] = tag_label
        return True
    except Exception as e:
        err_msg = _extract_http_err(e)
        diag_status["last_tg_error"] = err_msg
        print(f"[Telegram Hata] sendPhoto fallback: {err_msg}", flush=True)
        return tg_send_text(caption, chat_id=target, tag_label=tag_label)


# ==============================================================================
# MOTOR 1: 0-7 DK SPOT ANLIK PUMP (4 KATMANLI CANLI GRAFIK GARANTISI)
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
    c1m = fetch_spot_klines(sym, "1m", 35)

    with ticker_lock:
        latest_tk = ticker_map.get(sym, tk)
    live_cur = latest_tk["price"]
    live_vol = latest_tk["volume24h"]
    bar_min = min((b["low"] for b in c1m[-7:]), default=min_pt[1]) if c1m else min_pt[1]
    verified_min = min(min_pt[1], bar_min if bar_min > 0 else min_pt[1])
    verified_pct = ((live_cur - verified_min) / verified_min) * 100.0 if verified_min > 0 else 0.0

    if verified_pct < th_pct:
        with state_lock:
            locks["pump"].pop(base, None)
        return

    with state_lock:
        ticks_snap = list(price_window_history.get(sym, []))
        price_window_history.pop(sym, None)
        now_ms = int(time.time() * 1000)
        signals_today.append({"engine": "PUMP", "symbol": sym, "dir": "LONG", "entry": live_cur, "peak_pct": 0.0, "ts": now_ms})

    chart_bars = c1m
    chart_label = "1DK CANLI"
    if chart_bars:
        last_b = dict(chart_bars[-1])
        last_b["close"] = live_cur
        last_b["high"] = max(last_b["high"], live_cur)
        last_b["low"] = min(last_b["low"], live_cur)
        chart_bars = list(chart_bars[:-1]) + [last_b]
    else:
        chart_bars = build_ohlc_from_ticks(ticks_snap, live_cur, bucket_sec=15)
        chart_label = "15SN CANLI"

    mins = max(1, round((time.time() * 1000 - min_pt[0]) / 60000))
    vol_str = f"{round(live_vol):,}" if quote == "USDT" else f"{live_vol:.2f}"
    caption = (
        f"⚡ <b>SPOT ANLIK PUMP DEDEKTÖRÜ — {sym}</b>\n\n"
        f"📈 Değişim: <b>+{verified_pct:.2f}%</b> ({mins} dk içinde!)\n"
        f"💰 Başlangıç: <code>{fmt_price(verified_min)}</code>  ➡️  Anlık: <code>{fmt_price(live_cur)}</code>\n"
        f"💧 24s Parite Hacmi: {vol_str} ({quote})"
    )
    png = (
        render_candlestick_png(
            sym, chart_label, chart_bars,
            levels=[("BAS", verified_min, (250, 204, 21)), ("ANLIK", live_cur, (34, 197, 94))],
            header_note=f"+{verified_pct:.2f}%",
        )
        if chart_bars
        else b""
    )
    tg_send_photo(caption, png, tag_label=f"PUMP {sym}")
    save_state()


def evaluate_pump_for_ticker(tk, now_ms: int):
    quote = tk["quote"]
    min_vol = 150_000 if quote == "USDT" else (3 if quote == "BTC" else 250)
    th_pct = 4.5 if quote == "USDT" else 4.0
    if tk["price"] <= 0 or tk["volume24h"] < min_vol:
        return
    sym = tk["symbol"]
    with state_lock:
        hist = price_window_history.setdefault(sym, [])
        if hist and abs(hist[-1][1] - tk["price"]) < 1e-12 and (now_ms - hist[-1][0]) < 1500:
            return
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
    if pct < th_pct:
        return
    with state_lock:
        if is_pump_locked(tk["base"], now_ms):
            return
        locks["pump"][tk["base"]] = {"base": tk["base"], "symbol": sym, "lockedAt": now_ms}
    threading.Thread(target=_verify_and_emit_pump, args=(dict(tk), min_pt), daemon=True).start()


# ==============================================================================
# MOTOR 2, 3, 6, 7, EXHAUST: AKILLI ONBELLEKLI & YEDEK BORSALI TARAMA
# ==============================================================================
def get_daily_stats_cached(sym: str):
    day_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rec = daily_stats_cache.get(sym)
    if rec and rec["day"] == day_utc:
        return rec
    c1d = fetch_spot_klines(sym, "1d", 370)
    if not c1d:
        return None
    rec = {"day": day_utc, "c1d": c1d}
    if len(c1d) >= 203:
        prev199 = [b["close"] for b in c1d[-200:-1]]
        rec["sum199"] = sum(prev199)
        closes_full = [b["close"] for b in c1d]
        sma_hist = compute_sma_series(closes_full[:-1], 200)
        if len(sma_hist) >= 2 and sma_hist[-1]:
            rec["yest_close"] = closes_full[-2]
            rec["yest_ma200"] = sma_hist[-1]
    if len(c1d) >= 60:
        p30 = c1d[-31:-1]
        rec["month_max"] = max(b["high"] for b in p30)
        rec["month_min"] = min(b["low"] for b in p30)
    if len(c1d) >= 366:
        p365 = c1d[-366:-1]
        rec["year_max"] = max(b["high"] for b in p365)
        rec["year_min"] = min(b["low"] for b in p365)
    elif len(c1d) >= 30:
        rec["year_min"] = min(b["low"] for b in c1d[:-1])
    if len(c1d) >= 7:
        rec["ma1d"] = sum(b["close"] for b in c1d[-7:]) / 7.0
    daily_stats_cache[sym] = rec
    return rec


def scan_structural_pair(tk):
    if tk["quote"] != "USDT" or tk["volume24h"] < 1_500_000:
        return False
    sym = tk["symbol"]
    cur = tk["price"]
    now_ms = int(time.time() * 1000)
    did_net = False
    c15m_shared = []

    # 1) MOTOR 6: 1G MA200 & STORMGAIN PRO (YEAR / MONTH)
    try:
        was_cached = daily_stats_cache.get(sym, {}).get("day") == datetime.now(timezone.utc).strftime("%Y-%m-%d")
        dstat = get_daily_stats_cached(sym)
        if not was_cached:
            did_net = True
        if dstat:
            c1d = dstat["c1d"]
            if "sum199" in dstat:
                ma_live = (dstat["sum199"] + cur) / 200.0
                if ma_live > 0:
                    new_pos = "ABOVE" if cur >= ma_live * 1.004 else ("BELOW" if cur <= ma_live * 0.996 else None)
                    if new_pos:
                        crossed = False
                        with state_lock:
                            prev_lock = locks["ma200"].get(sym)
                            if not prev_lock:
                                y_c = dstat.get("yest_close", 0.0)
                                y_m = dstat.get("yest_ma200", 0.0)
                                if y_c > 0 and y_m > 0:
                                    yest_pos = "ABOVE" if y_c >= y_m else "BELOW"
                                    if yest_pos != new_pos:
                                        crossed = True
                                locks["ma200"][sym] = {"symbol": sym, "direction": new_pos, "ma200": ma_live, "updatedAt": now_ms}
                            elif prev_lock["direction"] != new_pos:
                                locks["ma200"][sym] = {"symbol": sym, "direction": new_pos, "ma200": ma_live, "updatedAt": now_ms}
                                crossed = True
                        if crossed:
                            save_state()
                            diff_pct = ((cur - ma_live) / ma_live) * 100.0
                            with state_lock:
                                signals_today.append({"engine": "MA200", "symbol": sym, "dir": new_pos, "entry": cur, "peak_pct": 0.0, "ts": now_ms})
                            closes_full = [b["close"] for b in c1d]
                            closes_full[-1] = cur
                            sma200 = compute_sma_series(closes_full, 200)
                            title = f"🟢 <b>1G MA200 YUKARI KIRILIMI — #{sym}</b>" if new_pos == "ABOVE" else f"🔴 <b>1G MA200 AŞAĞI DÜŞÜŞ — #{sym}</b>"
                            caption = (
                                f"{title}\n"
                                f"💰 Anlık Fiyat: <code>${fmt_price(cur)}</code> · Canlı MA200: <code>${fmt_price(ma_live)}</code> ({diff_pct:+.2f}%)\n"
                                f"🔒 Kilit aktif: Ters kırılım gelene kadar tekrar sinyal üretilmez."
                            )
                            png = render_candlestick_png(
                                sym, "1G MA200", c1d[-80:],
                                levels=[("MA200", ma_live, (56, 189, 248)), ("ANLIK", cur, (34, 197, 94) if new_pos == "ABOVE" else (239, 68, 68))],
                                ma_series=sma200[-80:], header_note=f"MA200 {diff_pct:+.2f}%",
                            )
                            tg_send_photo(caption, png, tag_label=f"MA200 {sym} {new_pos}")

            if tk["volume24h"] >= 15_000_000 and "month_max" in dstat:
                cooldown_ms = 48 * 3600 * 1000
                month_max = dstat["month_max"]
                month_min = dstat["month_min"]
                year_max = dstat.get("year_max", 0.0)
                year_min = dstat.get("year_min", 0.0)

                with state_lock:
                    seeded = False
                    if year_max > year_min and sym not in locks["year"]:
                        init_y = "MAX" if cur > year_max * 1.06 else ("MIN" if cur < year_min * 0.94 else "NEUTRAL")
                        locks["year"][sym] = {"symbol": sym, "direction": init_y, "level": cur, "yearMax": year_max, "yearMin": year_min, "updatedAt": now_ms}
                        seeded = True
                    if month_max > month_min and sym not in locks["month"]:
                        init_m = "MAX" if cur > month_max * 1.06 else ("MIN" if cur < month_min * 0.94 else "NEUTRAL")
                        locks["month"][sym] = {"symbol": sym, "direction": init_m, "level": cur, "monthMax": month_max, "monthMin": month_min, "updatedAt": now_ms}
                        seeded = True
                    prev_y = locks["year"].get(sym)
                    prev_m = locks["month"].get(sym)
                if seeded:
                    save_state()

                candidate = None
                if year_max > year_min:
                    if year_max * 1.001 <= cur <= year_max * 1.06:
                        if not prev_y or prev_y["direction"] != "MAX" or ((now_ms - prev_y["updatedAt"] >= cooldown_ms) and cur > prev_y["level"] * 1.005):
                            candidate = ("YEAR", "MAX", year_max, year_max, year_min)
                    elif year_min * 0.94 <= cur <= year_min * 0.999:
                        if not prev_y or prev_y["direction"] != "MIN" or ((now_ms - prev_y["updatedAt"] >= cooldown_ms) and cur < prev_y["level"] * 0.995):
                            candidate = ("YEAR", "MIN", year_min, year_max, year_min)

                if not candidate and month_max > month_min:
                    if month_max * 1.001 <= cur <= month_max * 1.06:
                        if not prev_m or prev_m["direction"] != "MAX" or ((now_ms - prev_m["updatedAt"] >= cooldown_ms) and cur > prev_m["level"] * 1.005):
                            candidate = ("MONTH", "MAX", month_max, month_max, month_min)
                    elif month_min * 0.94 <= cur <= month_min * 0.999:
                        if not prev_m or prev_m["direction"] != "MIN" or ((now_ms - prev_m["updatedAt"] >= cooldown_ms) and cur < prev_m["level"] * 0.995):
                            candidate = ("MONTH", "MIN", month_min, month_max, month_min)

                if candidate:
                    did_net = True
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
                        last_1h = c1h[-1]
                        elapsed_frac = max(0.15, min(1.0, (time.time() - last_1h["open_time"]) / 3600.0))
                        norm_cur_1h = last_1h["quote_vol"] / elapsed_frac
                        recent1h = max(norm_cur_1h, c1h[-2]["quote_vol"])
                        rvol = (recent1h / avg1h) if avg1h > 0 else 1.0

                        tot_q = sum(b["quote_vol"] for b in c1h[-2:])
                        tot_tb = sum(b["taker_buy_quote"] for b in c1h[-2:])
                        taker_buy_pct = (tot_tb / tot_q * 100.0) if tot_q > 0 else 50.0

                        is_max = c_dir == "MAX"
                        chg24 = tk["change24h"]
                        momentum_ok = (
                            1.0 <= chg24 <= 22.0
                            and 48.0 <= rsi4h <= 82.0
                            and (macd4h["macd"] > macd4h["signal"] or macd4h["hist"] > macd4h["prev_hist"])
                            and rvol >= 1.15
                            and taker_buy_pct >= 51.0
                        ) if is_max else (
                            -22.0 <= chg24 <= -1.0
                            and 18.0 <= rsi4h <= 52.0
                            and (macd4h["macd"] < macd4h["signal"] or macd4h["hist"] < macd4h["prev_hist"])
                            and rvol >= 1.15
                            and taker_buy_pct <= 49.0
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
                            next_trig = cur * 1.005 if is_max else cur * 0.995
                            opp_txt = ("365G dibe" if is_max else "365G zirveye") if is_year else ("30G dibe" if is_max else "30G zirveye")
                            dir_cond = "üstüne çıkmadıkça" if is_max else "altına düşmedikçe"
                            caption = (
                                f"{icon} <b>#{sym}, {'1 yıllık' if is_year else '1 aylık'} ({tag}) değere ulaştı: ${fmt_price(cur)}</b>\n\n"
                                f"📌 Son {365 if is_year else 30} günün {'en yüksek' if is_max else 'en düşük'} seviyesi (<code>${fmt_price(ref_lvl)}</code>) hacimli kırıldı.\n"
                                f"⚡ RVOL: <b>{rvol:.1f}x</b> · 4S RSI: {rsi4h:.1f} · 4S MACD {'Pozitif' if is_max else 'Negatif'} · {taker_txt}\n"
                                f"💧 24s Değişim: {chg24:+.2f}% · Hacim: {tk['volume24h'] / 1_000_000:.1f}M USDT\n"
                                + (f"🐋 Vadeli Bağlam: {fut_line}\n" if fut_line else "")
                                + f"🔒 Kilit aktif: ${fmt_price(next_trig)} {dir_cond} veya {opp_txt} dönmedikçe tekrar gelmez (48s kilit)"
                            )
                            lbl_hi = "365MAX" if is_year else "30MAX"
                            lbl_lo = "365MIN" if is_year else "30MIN"
                            png = render_candlestick_png(
                                sym, "1G", c1d[-80:],
                                levels=[(lbl_hi, hi_lvl, (34, 197, 94)), (lbl_lo, lo_lvl, (239, 68, 68)), ("ANLIK", cur, (56, 189, 248))],
                                header_note=tag.upper(),
                            )
                            tg_send_photo(caption, png, tag_label=f"{scope} {sym} {c_dir}")
    except Exception as e:
        print(f"[{sym} MA200/StormGain Hata] {e}", flush=True)

    # 2) MOTOR 3: 4h Akis / Emir Defteri Duvari & POC (coin basina min 120 sn aralikli)
    try:
        dstat = daily_stats_cache.get(sym)
        last_flow_ts = flow_scan_cache.get(sym, 0)
        if tk["volume24h"] >= 2_500_000 and dstat and "ma1d" in dstat and (now_ms - last_flow_ts) > 120_000:
            flow_scan_cache[sym] = now_ms
            did_net = True
            depth = fetch_spot_depth(sym, limit=100)
            if depth:
                best_bid, best_ask = (0.0, 0.0), (0.0, 0.0)
                for row in depth.get("bids", []):
                    p, usd = float(row[0]), float(row[0]) * float(row[1])
                    if abs(cur - p) / cur <= 0.025 and usd > best_bid[1]:
                        best_bid = (p, usd)
                for row in depth.get("asks", []):
                    p, usd = float(row[0]), float(row[0]) * float(row[1])
                    if abs(p - cur) / cur <= 0.025 and usd > best_ask[1]:
                        best_ask = (p, usd)

                bias, wall = None, None
                if best_ask[1] >= 120_000 and cur <= best_ask[0] * 1.004 and best_ask[1] >= best_bid[1] * 1.15:
                    bias, wall = "SHORT", (best_ask[0], best_ask[1], "Ask (Satış) Duvarı")
                elif best_bid[1] >= 120_000 and cur >= best_bid[0] * 0.996 and best_bid[1] >= best_ask[1] * 1.15:
                    bias, wall = "LONG", (best_bid[0], best_bid[1], "Bid (Alış) Duvarı")

                trend1d = "LONG" if cur >= dstat["ma1d"] else "SHORT"
                if bias and bias == trend1d and locks["flow"].get(sym, {}).get("direction") != bias:
                    c15m = fetch_spot_klines(sym, "15m", 96)
                    c15m_shared = c15m
                    c4h = fetch_spot_klines(sym, "4h", 56)
                    c3d_s = fetch_spot_klines(sym, "3d", 12)
                    if len(c15m) >= 40 and len(c4h) >= 28 and len(c3d_s) >= 5:
                        ma3d = sum(b["close"] for b in c3d_s[-5:]) / 5.0
                        trend3d = "LONG" if cur >= ma3d else "SHORT"
                        d_lo = min(b["low"] for b in c15m)
                        d_hi = max(b["high"] for b in c15m)
                        if bias == trend3d and d_hi > d_lo:
                            buckets = 40
                            step_p = (d_hi - d_lo) / buckets
                            hist_v = [0.0] * buckets
                            for b in c15m:
                                mid = (b["high"] + b["low"] + b["close"]) / 3.0
                                idx = min(buckets - 1, max(0, int((mid - d_lo) / step_p)))
                                hist_v[idx] += b["quote_vol"]
                            poc_idx = max(range(buckets), key=lambda i: hist_v[i])
                            poc_price = d_lo + (poc_idx + 0.5) * step_p

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

                            base_coin = tk["base"]
                            wall_usd_str = f"${wall[1] / 1_000_000:.1f}M" if wall[1] >= 1_000_000 else f"${round(wall[1] / 1000)}k"
                            opp_usd = best_bid[1] if bias == "SHORT" else best_ask[1]
                            wall_intro = (
                                f"{base_coin} iki seviye arasında sıkışmış durumda ancak duvarlardan biri gerçekten tutuyor. {fmt_price(wall[0])} seviyesindeki {wall[2]} — tek bir kademede yoğunlaşmış {wall_usd_str} emir bulunuyor."
                                if opp_usd >= 60_000
                                else f"{base_coin} kritik likidite kademesine dayanmış durumda. {fmt_price(wall[0])} seviyesindeki {wall[2]} — tek bir kademede yoğunlaşmış {wall_usd_str} emir bulunuyor."
                            )
                            hold_sentence = (
                                f"Şu an fiyatın yukarı sıçramasını tutuyor. Günlük hacim merkezi (POC) {fmt_price(poc_price)} bölgesinde konumlanıyor."
                                if bias == "SHORT"
                                else f"Şu an fiyatın aşağı sarkmasını tutuyor. Günlük hacim merkezi (POC) {fmt_price(poc_price)} bölgesinde konumlanıyor."
                            )
                            if bias == "SHORT":
                                poc_rel = (
                                    "Fiyat zaten bu seviyenin altında, spot dengesizlik zayıf (Spot akışı yönü destekliyor)."
                                    if cur < poc_price
                                    else "Fiyat POC direncinin hemen testinde, spot dengesizlik zayıf (Spot akışı yönü destekliyor)."
                                )
                            else:
                                poc_rel = (
                                    "Fiyat zaten bu seviyenin üzerinde, spot alıcı dengesizliği güçlü (Spot akışı yönü destekliyor)."
                                    if cur > poc_price
                                    else "Fiyat POC desteğinin hemen üzerinde, spot alıcı dengesizliği güçlü (Spot akışı yönü destekliyor)."
                                )

                            caption = (
                                f"<b>#{sym} ({bias})</b>\n\n"
                                f"Fiyat: <code>{fmt_price(cur)}</code>\n"
                                f"Hedefler: <code>{fmt_price(tp1)}</code> ({_pstr(tp1)}) → <code>{fmt_price(tp2)}</code> ({_pstr(tp2)}) → <code>{fmt_price(tp3)}</code> ({_pstr(tp3)})\n"
                                f"İptal: <code>{fmt_price(stop_tight)}</code> {'üstünde' if bias == 'SHORT' else 'altında'} ({_pstr(stop_tight)})\n\n"
                                f"{wall_intro}\n\n"
                                f"{hold_sentence}\n\n"
                                f"{poc_rel} Çoklu borsa akışı senkronize ({', '.join(exchanges)}), ayrışma yok. Günlük (1D) ve 3 Günlük (3D) grafik aynı yönü gösteriyor — bu bir karşı-trend değil, trend yönünde akış.\n\n"
                                f"Anahtar tetik: <code>{fmt_price(trigger)}</code> {'altında' if bias == 'SHORT' else 'üstünde'} kapanış/kalıcılık.\n"
                                f"🔒 Yön kilidi aktif: Ters yöne dönmeden aynı coinden tekrar sinyal gelmez."
                            )
                            png = render_candlestick_png(
                                sym, "4S", c4h,
                                levels=[
                                    ("TP1", tp1, (34, 197, 94)),
                                    ("TP2", tp2, (16, 185, 129)),
                                    ("STOP", stop_tight, (239, 68, 68)),
                                    ("POC", poc_price, (250, 204, 21)),
                                    ("ANLIK", cur, (56, 189, 248)),
                                ],
                                header_note=bias,
                            )
                            tg_send_photo(caption, png, tag_label=f"FLOW {sym} {bias}")
    except Exception as e:
        print(f"[{sym} FLOW Hata] {e}", flush=True)

    # 3) MOTOR 2: W1 ATL (Sadece 1 yillik dibe %3 yakinsa 1w ceker)
    try:
        dstat = daily_stats_cache.get(sym)
        near_1y_low = bool(dstat and dstat.get("year_min", 0) > 0 and cur <= dstat["year_min"] * 1.03)
        if near_1y_low:
            cached = atl_weekly_cache.get(sym)
            if not cached or (now_ms - cached["ts"]) > 14_400_000:
                did_net = True
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
                        sym, "1S ATL", c1h_atl,
                        levels=[("ATL", floor_atl, (250, 204, 21)), ("ANLIK", cur, (239, 68, 68))],
                        header_note="W1 ATL",
                    )
                    tg_send_photo(caption, png, tag_label=f"ATL {sym}")
    except Exception as e:
        print(f"[{sym} ATL Hata] {e}", flush=True)

    # 4) MOTOR EXHAUST: PARABOLIK TUKENME
    try:
        prev_exh = locks["exhaust"].get(sym)
        exh_cooldown_ms = 24 * 3600 * 1000
        if (
            tk["volume24h"] >= 2_500_000
            and tk["change24h"] >= 4.5
            and tk["high24h"] > cur
            and (tk["high24h"] * 0.945 <= cur <= tk["high24h"] * 0.997)
            and (not prev_exh or (now_ms - prev_exh["lockedAt"]) >= exh_cooldown_ms)
        ):
            did_net = True
            c15m = c15m_shared[-40:] if len(c15m_shared) >= 40 else fetch_spot_klines(sym, "15m", 40)
            if len(c15m) >= 24:
                recent16 = c15m[-17:]
                swing_low = min(b["low"] for b in recent16[:-1])
                swing_high = max(b["high"] for b in recent16)
                rally_pct = ((swing_high - swing_low) / swing_low * 100.0) if swing_low > 0 else 0.0

                if rally_pct >= 7.0 and (swing_high * 0.945 <= cur <= swing_high * 0.997):
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
                        _wick_ratio(prev_bar) >= 0.32
                        or _wick_ratio(last_bar) >= 0.32
                        or prev_bar["close"] < prev_bar["open"]
                    )
                    last2 = c15m[-2:]
                    tot_q = sum(b["quote_vol"] for b in last2)
                    tot_tb = sum(b["taker_buy_quote"] for b in last2)
                    taker_buy_pct = (tot_tb / tot_q * 100.0) if tot_q > 0 else 50.0

                    if rsi_peak4 >= 72.0 and rsi_cur <= (rsi_peak4 - 1.2) and has_rejection and taker_buy_pct <= 49.0:
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
                            sym, "15DK TEPE", c15m[-40:],
                            levels=[
                                ("TP1", tp1, (34, 197, 94)),
                                ("TP2", tp2, (16, 185, 129)),
                                ("STOP", stop_loss, (239, 68, 68)),
                                ("ANLIK", cur, (250, 204, 21)),
                            ],
                            header_note="TEPE TUKENME",
                        )
                        tg_send_photo(caption, png, tag_label=f"EXHAUST {sym}")
    except Exception as e:
        print(f"[{sym} EXHAUST Hata] {e}", flush=True)

    return did_net


# ==============================================================================
# MOTOR 4, 5 & 8: CANLI PIK RAPORLARI (21:00 / 23:00 TR) & BINANCE DUYURU
# ==============================================================================
def update_signal_peaks():
    global last_peak_save_ts
    changed = False
    with ticker_lock:
        snap = dict(ticker_map)
    with state_lock:
        for s in signals_today:
            tk = snap.get(s["symbol"])
            if not tk or tk["price"] <= 0:
                continue
            cur = tk["price"]
            is_short = s["dir"] in ("SHORT", "BELOW", "ATL")
            pct = ((s["entry"] - cur) / s["entry"] * 100.0) if is_short else ((cur - s["entry"]) / s["entry"] * 100.0)
            if pct > s["peak_pct"]:
                s["peak_pct"] = round(pct, 2)
                changed = True
    now_s = time.time()
    if changed and (now_s - last_peak_save_ts) >= 20.0:
        last_peak_save_ts = now_s
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
# SIFIR AGIRLIKLI WEBSOCKET MINI-TICKER AKISI + YEDEK SEED + TARAMA DONGUSU
# ==============================================================================
def _ingest_ws_tickers(arr):
    if not isinstance(arr, list):
        return
    now_ms = int(time.time() * 1000)
    for item in arr:
        sym = item.get("s", "")
        if not sym or is_excluded_pair(sym):
            continue
        bq = extract_base_quote(sym)
        if not bq:
            continue
        price = float(item.get("c") or 0)
        if price <= 0:
            continue
        open_p = float(item.get("o") or price)
        chg_pct = ((price - open_p) / open_p * 100.0) if open_p > 0 else 0.0
        tk = {
            "symbol": sym,
            "base": bq[0],
            "quote": bq[1],
            "price": price,
            "change24h": chg_pct,
            "volume24h": float(item.get("q") or 0),
            "high24h": float(item.get("h") or price),
            "low24h": float(item.get("l") or price),
        }
        with ticker_lock:
            ticker_map[sym] = tk
        evaluate_pump_for_ticker(tk, now_ms)
    update_signal_peaks()
    check_daily_reports()


def ws_ticker_loop():
    ctx = ssl.create_default_context()
    host_idx = 0
    while True:
        host, port, path = WS_HOSTS[host_idx % len(WS_HOSTS)]
        host_idx += 1
        sock = None
        try:
            raw_sock = socket.create_connection((host, port), timeout=12)
            sock = ctx.wrap_socket(raw_sock, server_hostname=host)
            sock.settimeout(20)
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            req = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n\r\n"
            )
            sock.sendall(req.encode("ascii"))
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    raise RuntimeError("WS handshake closed")
                buf += chunk
            head_line = buf.split(b"\r\n", 1)[0].decode("ascii", errors="ignore")
            if "101" not in head_line:
                raise RuntimeError(f"WS reddedildi: {head_line}")
            diag_status["ws_connected"] = True
            diag_status["ws_host"] = host
            print(f"[WebSocket] Canli akis baglandi: {host}", flush=True)

            rest = buf.split(b"\r\n\r\n", 1)[1]
            frag_buf = bytearray()

            def _ensure(n_bytes):
                nonlocal rest
                while len(rest) < n_bytes:
                    chunk = sock.recv(65536)
                    if not chunk:
                        raise RuntimeError("WS EOF")
                    rest += chunk

            while True:
                _ensure(2)
                b1, b2 = rest[0], rest[1]
                fin = (b1 & 0x80) != 0
                opcode = b1 & 0x0F
                masked = (b2 & 0x80) != 0
                ln = b2 & 0x7F
                off = 2
                if ln == 126:
                    _ensure(4)
                    ln = struct.unpack("!H", rest[2:4])[0]
                    off = 4
                elif ln == 127:
                    _ensure(10)
                    ln = struct.unpack("!Q", rest[2:10])[0]
                    off = 10
                mask_bytes = b""
                if masked:
                    _ensure(off + 4)
                    mask_bytes = rest[off : off + 4]
                    off += 4
                _ensure(off + ln)
                payload = rest[off : off + ln]
                rest = rest[off + ln :]
                if masked and mask_bytes:
                    payload = bytes(payload[i] ^ mask_bytes[i % 4] for i in range(ln))

                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    _ws_send_frame(sock, 0xA, payload)
                    continue
                if opcode == 0xA:
                    continue
                if opcode in (0x1, 0x2, 0x0):
                    frag_buf.extend(payload)
                    if fin:
                        msg_bytes = bytes(frag_buf)
                        frag_buf.clear()
                        try:
                            arr = json.loads(msg_bytes.decode("utf-8"))
                            diag_status["ws_messages"] += 1
                            _ingest_ws_tickers(arr)
                        except Exception:
                            pass
        except Exception as e:
            diag_status["ws_connected"] = False
            print(f"[WebSocket Yeniden Baglaniyor] {host}: {e}", flush=True)
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        time.sleep(5)


def rest_seed_loop():
    """Sadece baslangicta parite sayisi azsa WS-API veya Bybit/REST ile baslangic doldurur."""
    while True:
        try:
            with ticker_lock:
                t_count = len(ticker_map)
            if not diag_status["ws_connected"] or t_count < 200:
                raw = []
                now = time.time()
                with ban_lock:
                    ws_b = now < ws_api_ban_until
                    rest_b = now < rest_ban_until
                if not ws_b:
                    try:
                        raw = ws_api_client.call("ticker.24hr", {}, timeout=8.0) or []
                    except Exception:
                        raw = []
                if not raw and not rest_b:
                    try:
                        raw = _binance_rest_json("/api/v3/ticker/24hr", timeout=10.0)
                    except Exception:
                        raw = []
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
                        with ticker_lock:
                            ticker_map[sym] = tk
                        evaluate_pump_for_ticker(tk, now_ms)
                    update_signal_peaks()
                    check_daily_reports()
        except Exception as e:
            print(f"[Seed Hata] {e}", flush=True)
        time.sleep(60)


def structure_loop():
    cursor = 0
    while True:
        try:
            with ticker_lock:
                all_vals = list(ticker_map.values())
            liquid = sorted(
                [t for t in all_vals if t["quote"] == "USDT" and t["volume24h"] >= 1_500_000],
                key=lambda x: x["volume24h"],
                reverse=True,
            )[:400]
            if liquid:
                batch = []
                for _ in range(min(8, len(liquid))):
                    batch.append(liquid[cursor % len(liquid)])
                    cursor += 1
                for tk in batch:
                    did_net = scan_structural_pair(tk)
                    diag_status["structural_scans"] += 1
                    if did_net:
                        time.sleep(0.25 if router_state["active_data_source"] == "ws-api" else 0.15)
        except Exception as e:
            print(f"[Structure Loop Hata] {e}", flush=True)
        time.sleep(1.2)


def news_loop():
    while True:
        poll_binance_news()
        time.sleep(60)


class HealthHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

    def do_GET(self):
        if self.path == "/test-telegram":
            ok = tg_send_text("🔔 <b>Telegram Test Mesajı</b> — Render botu Telegram bağlantısı çalışıyor!")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "sent": ok,
                        "telegram_token_set": bool(BOT_TOKEN),
                        "chat_id": CHAT_ID,
                        "telegram_last_error": diag_status["last_tg_error"],
                    }
                ).encode("utf-8")
            )
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        now = time.time()
        with ban_lock:
            rest_rem = max(0, int(rest_ban_until - now))
            ws_rem = max(0, int(ws_api_ban_until - now))
        with ticker_lock:
            pair_count = len(ticker_map)
        body = json.dumps(
            {
                "ok": True,
                "active_data_source": router_state["active_data_source"],
                "ws_api_connected": router_state["ws_api_connected"],
                "ip_weight_used": router_state["ip_weight_used"],
                "ip_weight_limit": router_state["ip_weight_limit"],
                "fallback_calls": router_state["fallback_calls"],
                "binance_rest_ban_sec": rest_rem,
                "binance_ws_api_ban_sec": ws_rem,
                "telegram_token_set": bool(BOT_TOKEN),
                "telegram_bot_ok": diag_status["telegram_bot_ok"],
                "telegram_last_error": diag_status["last_tg_error"],
                "chat_id": CHAT_ID,
                "ws_connected": diag_status["ws_connected"],
                "ws_host": diag_status["ws_host"],
                "ws_messages": diag_status["ws_messages"],
                "structural_scans": diag_status["structural_scans"],
                "binance_last_error": diag_status["last_binance_error"],
                "mirror": BINANCE_MIRRORS[preferred_mirror_idx],
                "pairs": pair_count,
                "signals_sent": diag_status["signals_sent"],
                "last_signal": diag_status["last_signal"],
                "locks": {k: len(v) for k, v in locks.items()},
            }
        ).encode("utf-8")
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def main():
    load_state()
    verify_telegram_token()
    ws_api_client.start()
    threading.Thread(target=ws_ticker_loop, daemon=True).start()
    threading.Thread(target=rest_seed_loop, daemon=True).start()
    threading.Thread(target=structure_loop, daemon=True).start()
    threading.Thread(target=news_loop, daemon=True).start()
    srv = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"[SpotFlowBot] Port {PORT} aktif | TokenSet: {bool(BOT_TOKEN)} | CHAT_ID: {CHAT_ID}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
