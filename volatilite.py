#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance Spot Anlık Pump & 24s En Yükseğe Gidenler Dedektörü (Motor #1 — LONG)
- Sıfır harici bağımlılık (yalnızca Python 3 standart kütüphanesi)
- Render / Bulut uyumlu mirror sırası (data-api.binance.vision öncelikli)
- Zaman-normalize edilmiş 15dk Paced RVOL + 1dk Peak RVOL
- 24s En Yükseğe Gidenler (24s +%6 ve 24s zirvesinin üst %28 bandında tutunan yeşil coinler)
- Aktif TRADING + taze orderbook filtresi (BREAK/delist hayalet coin temizliği)
- Açık Telegram hata teşhisi, çoklu chat /start kaydı, /test komutu & Health HEAD/GET
- 21:00 Günün Zaferi, 23:00 Top 24s Özeti, 60sn Binance Duyuruları
"""

import datetime
import html
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
try:
    PUMP_REPEAT_HOURS = max(0.0, float(os.environ.get("PUMP_REPEAT_HOURS", "2")))
except ValueError:
    PUMP_REPEAT_HOURS = 2.0

RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
STATE_FILE = os.environ.get("STATE_FILE", "/tmp/spot_pump_bot_state.json")

BINANCE_MIRRORS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]

TR_TZ = datetime.timezone(datetime.timedelta(hours=3))

# ==============================================================================
# GLOBAL DURUM
# ==============================================================================
STATE_LOCK = threading.RLock()
BOT_RUNNING = True
STARTED_AT = time.time()
PUMP_LOOPS = 0
PREFERRED_MIRROR_IDX = 0
BOT_USERNAME = ""
TELEGRAM_OK = False
LAST_TG_ERROR = ""
LAST_TICK_TS = 0.0

ACTIVE_TRADING_SYMBOLS = set()
TICKER_MAP = {}
PRICE_WINDOW_HISTORY = {}
VERIFY_COOLDOWN = {}
SIGNALS = []
LOCKS = {"pump": {}}
SUBSCRIBED_CHATS = []
NEWS_SEEN_IDS = set()
NEWS_SEEDED = False
LAST_VICTORY_DATE = ""
LAST_SUMMARY_DATE = ""

if CHAT_ID:
    SUBSCRIBED_CHATS.append(CHAT_ID)


def log(msg: str):
    ts = datetime.datetime.now(TR_TZ).strftime("%H:%M:%S")
    print(f"[{ts} TR] {msg}", flush=True)


def load_state():
    global SIGNALS, LOCKS, SUBSCRIBED_CHATS, NEWS_SEEN_IDS, LAST_VICTORY_DATE, LAST_SUMMARY_DATE
    try:
        if not os.path.exists(STATE_FILE):
            return
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        with STATE_LOCK:
            if isinstance(raw.get("signals"), list):
                SIGNALS = [s for s in raw["signals"] if s.get("engine") == "PUMP"][:200]
            if isinstance(raw.get("locks"), dict) and isinstance(raw["locks"].get("pump"), dict):
                LOCKS["pump"] = raw["locks"]["pump"]
            chats = raw.get("subscribed_chats", [])
            if isinstance(chats, list):
                for c in chats:
                    cs = str(c).strip()
                    if cs and cs not in SUBSCRIBED_CHATS:
                        SUBSCRIBED_CHATS.append(cs)
            seen = raw.get("news_seen_ids", [])
            if isinstance(seen, list):
                NEWS_SEEN_IDS = set(str(x) for x in seen[-300:])
            LAST_VICTORY_DATE = str(raw.get("last_victory_date", ""))
            LAST_SUMMARY_DATE = str(raw.get("last_summary_date", ""))
    except Exception as exc:
        log(f"[UYARI] State dosyası okunamadı: {exc}")


def save_state():
    try:
        with STATE_LOCK:
            payload = {
                "signals": SIGNALS[:200],
                "locks": LOCKS,
                "subscribed_chats": SUBSCRIBED_CHATS,
                "news_seen_ids": list(NEWS_SEEN_IDS)[-300:],
                "last_victory_date": LAST_VICTORY_DATE,
                "last_summary_date": LAST_SUMMARY_DATE,
            }
        tmp_path = f"{STATE_FILE}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, STATE_FILE)
    except Exception:
        pass


def get_target_chats():
    with STATE_LOCK:
        out = []
        if CHAT_ID:
            out.append(CHAT_ID)
        for c in SUBSCRIBED_CHATS:
            if c and c not in out:
                out.append(c)
        return out


def register_chat_id(cid: str):
    cs = str(cid or "").strip()
    if not cs:
        return
    with STATE_LOCK:
        if cs not in SUBSCRIBED_CHATS:
            SUBSCRIBED_CHATS.append(cs)
            save_state()


# ==============================================================================
# YARDIMCI HESAPLAMALAR
# ==============================================================================
def extract_base_quote(sym: str):
    for q in ("USDT", "BTC", "BNB"):
        if sym.endswith(q) and len(sym) > len(q):
            return sym[:-len(q)], q
    return None


def is_excluded_pair(sym: str) -> bool:
    bad_suffixes = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
    bad_prefixes = ("USDC", "FDUSD", "TUSD", "USDP", "EUR", "AEUR", "USD1", "RLUSD")
    if any(k in sym for k in bad_suffixes):
        return True
    if any(sym.startswith(p) for p in bad_prefixes):
        return True
    return False


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


def fmt_vol(v: float, quote: str = "USDT") -> str:
    if quote != "USDT":
        return f"{v:.2f} {quote}"
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"


# ==============================================================================
# HTTP / BINANCE MIRROR & TELEGRAM ISTEMCISI
# ==============================================================================
def http_get_json(url: str, timeout: float = 9.0):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; SpotPumpBot/2.1)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_binance_json(path_and_query: str, timeout: float = 9.0):
    global PREFERRED_MIRROR_IDX
    n = len(BINANCE_MIRRORS)
    last_err = None
    for offset in range(n):
        idx = (PREFERRED_MIRROR_IDX + offset) % n
        base_url = BINANCE_MIRRORS[idx]
        try:
            data = http_get_json(f"{base_url}{path_and_query}", timeout=timeout)
            PREFERRED_MIRROR_IDX = idx
            return data
        except Exception as exc:
            last_err = exc
    raise RuntimeError(f"Binance mirrors unreachable: {last_err}")


def fetch_klines(symbol: str, interval: str, limit: int = 40):
    raw = fetch_binance_json(
        f"/api/v3/klines?symbol={urllib.parse.quote(symbol)}&interval={urllib.parse.quote(interval)}&limit={limit}",
        timeout=10.0,
    )
    if not isinstance(raw, list):
        return []
    bars = []
    for k in raw:
        bars.append({
            "open_time": int(float(k[0]) // 1000),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "quote_vol": float(k[7]),
        })
    return bars


def tg_request(method: str, payload: dict, timeout: float = 15.0) -> dict:
    global TELEGRAM_OK, LAST_TG_ERROR
    if not BOT_TOKEN:
        LAST_TG_ERROR = "BOT_TOKEN boş"
        return {"ok": False, "description": LAST_TG_ERROR}
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if res.get("ok"):
                TELEGRAM_OK = True
            return res
    except urllib.error.HTTPError as e:
        err_text = e.read().decode("utf-8", errors="ignore")
        desc = err_text
        try:
            err_json = json.loads(err_text)
            desc = err_json.get("description", err_text)
        except Exception:
            pass
        LAST_TG_ERROR = f"{method} HTTP {e.code}: {desc}"
        if e.code != 409:
            log(f"[Telegram API HATA] {LAST_TG_ERROR}")
        return {"ok": False, "error_code": e.code, "description": desc}
    except Exception as exc:
        LAST_TG_ERROR = f"{method} ağ hatası: {exc}"
        log(f"[Telegram Bağlantı HATA] {LAST_TG_ERROR}")
        return {"ok": False, "description": str(exc)}


def tg_send_photo(chat_id: str, png_bytes: bytes, caption: str, reply_markup: dict = None) -> dict:
    global TELEGRAM_OK, LAST_TG_ERROR
    if not BOT_TOKEN:
        return {"ok": False, "description": "BOT_TOKEN boş"}
    boundary = f"----SpotBotBoundary{int(time.time() * 1000)}"
    parts = []

    def add_field(name: str, val: str):
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        parts.append(val.encode("utf-8"))
        parts.append(b"\r\n")

    add_field("chat_id", str(chat_id))
    add_field("caption", caption[:1020])
    add_field("parse_mode", "HTML")
    if reply_markup:
        add_field("reply_markup", json.dumps(reply_markup))

    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(b'Content-Disposition: form-data; name="photo"; filename="chart.png"\r\n')
    parts.append(b"Content-Type: image/png\r\n\r\n")
    parts.append(png_bytes)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))

    body = b"".join(parts)
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if res.get("ok"):
                TELEGRAM_OK = True
            return res
    except urllib.error.HTTPError as e:
        err_text = e.read().decode("utf-8", errors="ignore")
        desc = err_text
        try:
            desc = json.loads(err_text).get("description", err_text)
        except Exception:
            pass
        LAST_TG_ERROR = f"sendPhoto HTTP {e.code}: {desc}"
        log(f"[Telegram sendPhoto HATA] {LAST_TG_ERROR}")
        return {"ok": False, "error_code": e.code, "description": desc}
    except Exception as exc:
        LAST_TG_ERROR = f"sendPhoto ağ hatası: {exc}"
        log(f"[Telegram sendPhoto Bağlantı HATA] {LAST_TG_ERROR}")
        return {"ok": False, "description": str(exc)}


def send_to_targets(text: str, png_bytes: bytes = None, caption: str = None, reply_markup: dict = None) -> bool:
    targets = get_target_chats()
    if not targets:
        log("[UYARI] Hedef CHAT_ID tanımlı değil!")
        return False
    any_ok = False
    for cid in targets:
        if png_bytes:
            res = tg_send_photo(cid, png_bytes, caption or text, reply_markup=reply_markup)
            if res.get("ok"):
                any_ok = True
                continue
            log(f"[Bilgi] chat_id={cid} için sendPhoto başarısız, düz metin (sendMessage) deneniyor...")
        msg_payload = {
            "chat_id": cid,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            msg_payload["reply_markup"] = reply_markup
        res = tg_request("sendMessage", msg_payload)
        if res.get("ok"):
            any_ok = True
        else:
            desc = str(res.get("description", "")).lower()
            if "chat not found" in desc or "bot was blocked" in desc:
                log(f"[ÇÖZÜM İPUCU] chat_id={cid} için Telegram'dan bota en az 1 kez /start göndermelisiniz.")
    return any_ok


# ==============================================================================
# SAF PYTHON PNG GÖRSEL MOTORU (HARİCİ KÜTÜPHANE YOK)
# ==============================================================================
FONT_5X7 = {
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
    "S": [0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E],
    "T": [0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
    "U": [0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
    "V": [0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04],
    "W": [0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11],
    "X": [0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11],
    "Y": [0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04],
    "Z": [0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F],
    "0": [0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E],
    "1": [0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E],
    "2": [0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F],
    "3": [0x1E, 0x01, 0x01, 0x0E, 0x01, 0x01, 0x1E],
    "4": [0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02],
    "5": [0x1F, 0x10, 0x10, 0x1E, 0x01, 0x01, 0x1E],
    "6": [0x0E, 0x10, 0x10, 0x1E, 0x11, 0x11, 0x0E],
    "7": [0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08],
    "8": [0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E],
    "9": [0x0E, 0x11, 0x11, 0x0F, 0x01, 0x01, 0x0E],
    "+": [0x00, 0x04, 0x04, 0x1F, 0x04, 0x04, 0x00],
    "-": [0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00],
    ".": [0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C],
    ",": [0x00, 0x00, 0x00, 0x00, 0x04, 0x04, 0x08],
    ":": [0x00, 0x0C, 0x0C, 0x00, 0x0C, 0x0C, 0x00],
    "%": [0x18, 0x19, 0x02, 0x04, 0x08, 0x13, 0x03],
    "$": [0x04, 0x0F, 0x14, 0x0E, 0x05, 0x1E, 0x04],
    "/": [0x01, 0x02, 0x02, 0x04, 0x08, 0x08, 0x10],
    "|": [0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
    "(": [0x02, 0x04, 0x08, 0x08, 0x08, 0x04, 0x02],
    ")": [0x08, 0x04, 0x02, 0x02, 0x02, 0x04, 0x08],
    " ": [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
}


class Canvas:
    def __init__(self, w: int, h: int, bg=(10, 14, 23)):
        self.w = w
        self.h = h
        self.buf = bytearray(bg * (w * h))

    def fill_rect(self, x0: int, y0: int, w: int, h: int, c):
        x1 = max(0, min(self.w, x0 + w))
        y1 = max(0, min(self.h, y0 + h))
        x0 = max(0, x0)
        y0 = max(0, y0)
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(c) * (x1 - x0)
        for y in range(y0, y1):
            start = (y * self.w + x0) * 3
            self.buf[start:start + len(row)] = row

    def hline(self, x0: int, x1: int, y: int, c):
        self.fill_rect(x0, y, x1 - x0 + 1, 1, c)

    def vline(self, x: int, y0: int, y1: int, c):
        self.fill_rect(x, y0, 1, y1 - y0 + 1, c)

    def draw_text(self, x: int, y: int, text: str, c=(230, 237, 243), scale: int = 2):
        tr_map = str.maketrans("ÇĞİÖŞÜçğıöşü", "CGIOSUCGIOSU")
        clean = text.translate(tr_map).upper()
        cx = x
        for ch in clean:
            glyph = FONT_5X7.get(ch, FONT_5X7[" "])
            for row_i, row_bits in enumerate(glyph):
                for col_i in range(5):
                    if row_bits & (1 << (4 - col_i)):
                        self.fill_rect(cx + col_i * scale, y + row_i * scale, scale, scale, c)
            cx += 6 * scale

    def to_png(self) -> bytes:
        raw_rows = bytearray()
        stride = self.w * 3
        for y in range(self.h):
            raw_rows.append(0)
            s = y * stride
            raw_rows.extend(self.buf[s:s + stride])
        compressed = zlib.compress(bytes(raw_rows), level=6)

        def chunk(tag: bytes, data: bytes) -> bytes:
            return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)

        ihdr = struct.pack("!IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")


def draw_pump_chart_png(symbol: str, candles_15m: list, pct_str: str, rvol_str: str, cur_price: float, subtitle: str = "15M PUMP") -> bytes:
    w, h = 920, 520
    cv = Canvas(w, h, (11, 15, 25))
    cv.fill_rect(0, 0, w, 62, (16, 22, 36))
    cv.hline(0, w - 1, 62, (34, 48, 72))
    cv.draw_text(22, 14, f"{symbol} | {subtitle}", (245, 248, 252), scale=2)
    cv.draw_text(22, 41, f"FIYAT: ${fmt_price(cur_price)}   {pct_str}   RVOL: {rvol_str}", (34, 211, 153), scale=2)

    bars = candles_15m[-36:] if len(candles_15m) >= 2 else []
    if not bars:
        return cv.to_png()

    hi = max(b["high"] for b in bars)
    lo = min(b["low"] for b in bars)
    span = max(hi - lo, 1e-12)
    max_q = max((b["quote_vol"] for b in bars), default=1.0) or 1.0

    px0, px1 = 26, w - 130
    py0, py1 = 84, 370
    vy0, vy1 = 392, 488

    for i in range(5):
        gy = py0 + int((py1 - py0) * i / 4)
        cv.hline(px0, px1, gy, (24, 34, 52))
        val = hi - span * (i / 4)
        cv.draw_text(px1 + 10, gy - 6, fmt_price(val), (140, 155, 175), scale=2)

    step = (px1 - px0) / max(len(bars), 1)
    cw = max(4, int(step * 0.62))

    for i, b in enumerate(bars):
        cx = int(px0 + i * step + step * 0.5)
        is_up = b["close"] >= b["open"]
        col = (24, 198, 131) if is_up else (239, 68, 86)

        wy0 = py0 + int((hi - b["high"]) / span * (py1 - py0))
        wy1 = py0 + int((hi - b["low"]) / span * (py1 - py0))
        cv.vline(cx, wy0, wy1, col)

        bo = py0 + int((hi - b["open"]) / span * (py1 - py0))
        bc = py0 + int((hi - b["close"]) / span * (py1 - py0))
        top_y = min(bo, bc)
        bh = max(2, abs(bc - bo))
        cv.fill_rect(cx - cw // 2, top_y, cw, bh, col)

        vh = max(2, int((b["quote_vol"] / max_q) * (vy1 - vy0)))
        cv.fill_rect(cx - cw // 2, vy1 - vh, cw, vh, col)

    last_cx = int(px0 + (len(bars) - 1) * step + step * 0.5)
    cv.hline(last_cx - cw, last_cx + cw, py0 + 2, (250, 204, 21))
    cv.hline(last_cx - cw, last_cx + cw, py1 - 2, (250, 204, 21))
    cv.draw_text(26, 498, "SPOT ANLIK PUMP & 24S EN YUKSEGE GIDENLER - LONG RADAR", (110, 128, 152), scale=1)
    return cv.to_png()


def draw_market_radar_png(rows: list, title: str = "ANLIK SPOT PIYASA - EN YUKSEGE GIDENLER (24S)") -> bytes:
    top_rows = rows[:14]
    h = 92 + max(len(top_rows), 1) * 34
    w = 900
    cv = Canvas(w, h, (11, 15, 25))
    cv.fill_rect(0, 0, w, 62, (16, 22, 36))
    cv.hline(0, w - 1, 62, (34, 48, 72))
    cv.draw_text(22, 18, title, (245, 248, 252), scale=2)
    now_tr = datetime.datetime.now(TR_TZ).strftime("%H:%M:%S TR")
    cv.draw_text(w - 190, 20, now_tr, (148, 163, 184), scale=2)

    y = 78
    for idx, t in enumerate(top_rows, 1):
        if idx % 2 == 0:
            cv.fill_rect(14, y - 4, w - 28, 30, (17, 24, 39))
        sym = t["symbol"]
        p_str = f"${fmt_price(t['price'])}"
        c24 = t.get("change24h", 0.0)
        c7 = t.get("win7m", 0.0)
        vol_s = fmt_vol(t.get("vol24h", 0.0), t.get("quote", "USDT"))
        cv.draw_text(24, y, f"{idx:02d}. {sym}", (240, 244, 248), scale=2)
        cv.draw_text(270, y, p_str, (203, 213, 225), scale=2)
        cv.draw_text(470, y, f"24S {c24:+.1f}%", (34, 211, 153), scale=2)
        c7_col = (56, 189, 248) if c7 >= 0 else (248, 113, 113)
        cv.draw_text(640, y, f"7DK {c7:+.1f}%", c7_col, scale=2)
        cv.draw_text(780, y, vol_s, (148, 163, 184), scale=2)
        y += 34
    return cv.to_png()


# ==============================================================================
# MOTOR 1: SPOT ANLIK PUMP & 24S EN YÜKSEĞE GİDENLER (SADECE LONG/YÜKSELEN)
# ==============================================================================
def is_pump_locked(base: str, now_ts: float) -> bool:
    rec = LOCKS["pump"].get(base)
    if not rec:
        return False
    if PUMP_REPEAT_HOURS <= 0:
        return True
    return (now_ts - float(rec.get("locked_at", 0))) < (PUMP_REPEAT_HOURS * 3600.0)


def refresh_active_trading_symbols():
    global ACTIVE_TRADING_SYMBOLS
    try:
        info = fetch_binance_json("/api/v3/exchangeInfo", timeout=12.0)
        next_set = set()
        for s in info.get("symbols", []):
            sym = s.get("symbol", "")
            if sym and s.get("status") == "TRADING" and s.get("isSpotTradingAllowed") is not False:
                next_set.add(sym)
        if len(next_set) > 50:
            with STATE_LOCK:
                ACTIVE_TRADING_SYMBOLS = next_set
                for sym in list(TICKER_MAP.keys()):
                    if sym not in next_set:
                        TICKER_MAP.pop(sym, None)
                        PRICE_WINDOW_HISTORY.pop(sym, None)
            log(f"[Binance] Aktif TRADING spot parite sayısı: {len(next_set)}")
    except Exception as exc:
        log(f"[UYARI] exchangeInfo güncellenemedi: {exc}")


def verify_and_emit_pump(sym: str, base: str, quote: str, price: float, vol24h: float, chg24h: float, high24h: float, low24h: float, min7m_p: float, min7m_ts: float, min15m_p: float, min15m_ts: float):
    fast_th_pct = 4.0 if quote == "USDT" else 3.8
    breakout_th_pct = 3.2
    now_ts = time.time()

    try:
        tk = fetch_binance_json(f"/api/v3/ticker/24hr?symbol={urllib.parse.quote(sym)}", timeout=5.0)
        c1m = fetch_klines(sym, "1m", 20)
        c15m = fetch_klines(sym, "15m", 30)

        live_cur = float(tk.get("lastPrice") or price)
        live_vol = float(tk.get("quoteVolume") or vol24h)
        live_chg24 = float(tk.get("priceChangePercent") or chg24h)
        live_high24 = float(tk.get("highPrice") or high24h or live_cur)
        live_low24 = float(tk.get("lowPrice") or low24h or live_cur)

        recent_1m_7 = c1m[-7:]
        recent_1m_15 = c1m[-15:]
        bar_min_7 = min((b["low"] for b in recent_1m_7), default=min7m_p)
        verified_min_7 = min(min7m_p, bar_min_7 if bar_min_7 > 0 else min7m_p)
        verified_pct_7 = ((live_cur - verified_min_7) / verified_min_7) * 100.0 if verified_min_7 > 0 else 0.0

        cur_15m = c15m[-1] if c15m else None
        prev_15m = c15m[-2] if len(c15m) > 1 else None
        bar_min_15 = min(
            [b["low"] for b in recent_1m_15] + ([cur_15m["low"]] if cur_15m else []),
            default=min15m_p,
        )
        verified_min_15 = min(min15m_p, bar_min_15 if bar_min_15 > 0 else min15m_p)
        verified_pct_15 = ((live_cur - verified_min_15) / verified_min_15) * 100.0 if verified_min_15 > 0 else 0.0

        def qvol(b):
            return b["quote_vol"] if b["quote_vol"] > 0 else b["volume"] * b["close"]

        base_15m = c15m[-11:-1]
        avg_15m_q = (sum(qvol(b) for b in base_15m) / len(base_15m)) if len(base_15m) >= 3 else 0.0
        now_sec = int(time.time())
        elapsed_15m = max(90, min(900, now_sec - cur_15m["open_time"])) if cur_15m else 900
        cur_15m_paced_q = (qvol(cur_15m) * (900.0 / elapsed_15m)) if cur_15m else 0.0
        prev_15m_green_q = (
            qvol(prev_15m)
            if prev_15m and prev_15m["open"] > 0 and ((prev_15m["close"] - prev_15m["open"]) / prev_15m["open"]) * 100.0 >= 2.0
            else 0.0
        )
        active_15m_q = max(cur_15m_paced_q, prev_15m_green_q) if cur_15m else 0.0
        rvol_15m = (active_15m_q / avg_15m_q) if avg_15m_q > 0 else 1.0

        last_3m = c1m[-3:]
        base_1m = c1m[-13:-3]
        avg_1m_recent = (sum(qvol(b) for b in last_3m) / len(last_3m)) if last_3m else 0.0
        max_1m_recent = max((qvol(b) for b in last_3m), default=0.0)
        avg_1m_base = (sum(qvol(b) for b in base_1m) / len(base_1m)) if len(base_1m) >= 3 else 0.0
        rvol_1m_avg = (avg_1m_recent / avg_1m_base) if avg_1m_base > 0 else 1.0
        rvol_1m_peak = ((max_1m_recent / avg_1m_base) * 0.75) if avg_1m_base > 0 else 1.0

        rvol = round(max(rvol_15m, rvol_1m_avg, rvol_1m_peak), 1)

        candle_high = max(live_cur, cur_15m["high"] if cur_15m else live_cur)
        candle_low = min(verified_min_15, cur_15m["low"] if cur_15m else verified_min_15)
        hold_ratio = (live_cur - candle_low) / max(candle_high - candle_low, 1e-12)
        is_green_15m = True
        if cur_15m:
            is_green_15m = (live_cur > cur_15m["open"]) or (
                bool(prev_15m) and live_cur > prev_15m["open"] and prev_15m["close"] > prev_15m["open"]
            )

        fast_ok = verified_pct_7 >= fast_th_pct and rvol >= 1.4 and hold_ratio >= 0.45
        breakout_15m_ok = verified_pct_15 >= breakout_th_pct and rvol >= 1.6 and is_green_15m and hold_ratio >= 0.50

        live_pos24 = ((live_cur - live_low24) / (live_high24 - live_low24)) if live_high24 > live_low24 else 0.0
        live_dist_high = ((live_high24 - live_cur) / live_high24) * 100.0 if live_high24 > 0 else 99.0
        climber_24h_ok = (
            quote == "USDT"
            and live_vol >= 250_000.0
            and live_chg24 >= 6.0
            and live_pos24 >= 0.72
            and live_dist_high <= 3.5
            and verified_pct_7 >= 0.0
            and (is_green_15m or hold_ratio >= 0.45)
        )

        if not fast_ok and not breakout_15m_ok and not climber_24h_ok:
            with STATE_LOCK:
                LOCKS["pump"].pop(base, None)
                VERIFY_COOLDOWN[sym] = time.time() + 20.0
            return

        use_fast = fast_ok and (not breakout_15m_ok or verified_pct_7 >= fast_th_pct)
        use_24h = (not fast_ok) and (not breakout_15m_ok) and climber_24h_ok
        verified_min = live_low24 if use_24h else (verified_min_7 if use_fast else verified_min_15)
        verified_pct = live_chg24 if use_24h else (verified_pct_7 if use_fast else verified_pct_15)
        ref_ts = min7m_ts if use_fast else min15m_ts
        mins = max(1, min(7 if use_fast else 15, round((time.time() - ref_ts) / 60.0)))

        with STATE_LOCK:
            PRICE_WINDOW_HISTORY.pop(sym, None)
            VERIFY_COOLDOWN.pop(sym, None)
            sig = {
                "id": f"PUMP-{sym}-{int(now_ts * 1000)}",
                "engine": "PUMP",
                "symbol": sym,
                "base": base,
                "quote": quote,
                "entry_price": live_cur,
                "current_price": live_cur,
                "peak_price": live_cur,
                "peak_pct": 0.0,
                "change_pct": round(verified_pct, 2),
                "rvol": rvol,
                "created_at": time.time(),
            }
            SIGNALS.insert(0, sig)
            del SIGNALS[200:]
            save_state()

        lock_desc = f"🔒 {int(PUMP_REPEAT_HOURS)} saatlik dalga kilidi aktif" if PUMP_REPEAT_HOURS > 0 else "🔒 Tekil kilit aktif"
        vol_str = f"{round(live_vol):,} {quote}" if quote == "USDT" else f"{live_vol:.2f} {quote}"

        if use_24h:
            msg = (
                f"📈 <b>24 SAAT EN YÜKSEĞE GİDEN — #{html.escape(sym)}</b>\n"
                f"🚀 <b>24s Yükseliş:</b> +{live_chg24:.2f}% · <b>LONG</b>\n"
                f"🎯 <b>24s Zirve Gücü:</b> %{round(live_pos24 * 100)} (Zirveye %{live_dist_high:.2f} yakın)\n"
                f"💵 <b>24s Dip:</b> ${fmt_price(live_low24)} ➜ <b>Anlık:</b> ${fmt_price(live_cur)} (Zirve: ${fmt_price(live_high24)})\n"
                f"🔥 <b>7dk İvme:</b> +{max(0.0, verified_pct_7):.2f}% · <b>RVOL:</b> {rvol:.1f}x · <b>Hacim:</b> {vol_str}\n"
                f"{lock_desc}"
            )
            png_bytes = draw_pump_chart_png(sym, c15m, f"24S +{live_chg24:.2f}%", f"{rvol:.1f}x", live_cur, subtitle="24S ZIRVEYE GIDEN")
        else:
            mode_tag = "0-7 dk Ani Spike" if use_fast else "15dk Hacimli Mum Patlaması"
            msg = (
                f"🚀 <b>SPOT ANLIK PUMP DEDEKTÖRÜ — #{html.escape(sym)}</b>\n"
                f"📌 <b>Mod:</b> {mode_tag} · <b>LONG</b>\n"
                f"📈 <b>Değişim:</b> +{verified_pct:.2f}% ({mins} dk) · <b>24s:</b> {live_chg24:+.2f}%\n"
                f"💵 <b>Başlangıç:</b> ${fmt_price(verified_min)} ➜ <b>Anlık:</b> ${fmt_price(live_cur)}\n"
                f"🔥 <b>Hacim İvmesi (RVOL):</b> {rvol:.1f}x · <b>24s Hacim:</b> {vol_str}\n"
                f"{lock_desc}"
            )
            png_bytes = draw_pump_chart_png(sym, c15m, f"+{verified_pct:.2f}%", f"{rvol:.1f}x", live_cur)

        ok = send_to_targets(msg, png_bytes=png_bytes, caption=msg)
        log(f"[SİNYAL] {sym} ({'24S' if use_24h else 'PUMP'}) +{verified_pct:.2f}% RVOL {rvol:.1f}x -> Telegram: {'OK' if ok else 'HATA'}")
    except Exception as exc:
        with STATE_LOCK:
            LOCKS["pump"].pop(base, None)
        log(f"[HATA] {sym} doğrulama hatası: {exc}")


def evaluate_pump_tick(sym: str, base: str, quote: str, price: float, vol24h: float, chg24h: float, high24h: float, low24h: float, now_ts: float):
    min_vol = 150_000.0 if quote == "USDT" else (3.0 if quote == "BTC" else 250.0)
    fast_th_pct = 4.0 if quote == "USDT" else 3.8
    breakout_th_pct = 3.2
    if price <= 0 or vol24h < min_vol:
        return

    with STATE_LOCK:
        hist = PRICE_WINDOW_HISTORY.setdefault(sym, [])
        hist.append((now_ts, price))
        cutoff_15m = now_ts - 900.0
        cutoff_7m = now_ts - 420.0
        while hist and hist[0][0] < cutoff_15m:
            hist.pop(0)
        if len(hist) < 2:
            return

        min15m = hist[0]
        min7m = None
        for pt in hist:
            if pt[1] < min15m[1]:
                min15m = pt
            if pt[0] >= cutoff_7m and (min7m is None or pt[1] < min7m[1]):
                min7m = pt
        if min7m is None:
            min7m = min15m

        pct7m = ((price - min7m[1]) / min7m[1]) * 100.0 if min7m[1] > 0 else 0.0
        pct15m = ((price - min15m[1]) / min15m[1]) * 100.0 if min15m[1] > 0 else 0.0

        if sym in TICKER_MAP:
            TICKER_MAP[sym]["win7m"] = round(pct7m, 2)

        pos24h = ((price - low24h) / (high24h - low24h)) if high24h > low24h else 0.0
        dist_high = ((high24h - price) / high24h) * 100.0 if high24h > 0 else 99.0
        climber_24h_pre = (
            quote == "USDT"
            and vol24h >= 250_000.0
            and chg24h >= 6.0
            and pos24h >= 0.72
            and dist_high <= 3.5
            and pct7m >= 0.0
        )

        if not BOT_RUNNING:
            return
        if pct7m < fast_th_pct and pct15m < breakout_th_pct and not climber_24h_pre:
            return
        if is_pump_locked(base, now_ts):
            return
        if now_ts < VERIFY_COOLDOWN.get(sym, 0.0):
            return

        LOCKS["pump"][base] = {"base": base, "symbol": sym, "locked_at": now_ts}

    threading.Thread(
        target=verify_and_emit_pump,
        args=(sym, base, quote, price, vol24h, chg24h, high24h, low24h, min7m[1], min7m[0], min15m[1], min15m[0]),
        daemon=True,
    ).start()


def poll_tickers_once():
    global PUMP_LOOPS, LAST_TICK_TS
    data = fetch_binance_json("/api/v3/ticker/24hr", timeout=10.0)
    if not isinstance(data, list):
        return
    now_ts = time.time()
    now_ms = int(now_ts * 1000)

    for item in data:
        sym = str(item.get("symbol", ""))
        if not sym or is_excluded_pair(sym):
            continue
        if ACTIVE_TRADING_SYMBOLS and sym not in ACTIVE_TRADING_SYMBOLS:
            continue
        bq = extract_base_quote(sym)
        if not bq:
            continue
        base, quote = bq

        bid = float(item.get("bidPrice") or 0.0)
        ask = float(item.get("askPrice") or 0.0)
        cnt = int(float(item.get("count") or 0))
        close_ms = int(float(item.get("closeTime") or 0))
        is_fresh = bid > 0 and ask > 0 and cnt > 0 and (close_ms <= 0 or (now_ms - close_ms) <= 1_800_000)
        if not is_fresh:
            with STATE_LOCK:
                TICKER_MAP.pop(sym, None)
                PRICE_WINDOW_HISTORY.pop(sym, None)
            continue

        price = float(item.get("lastPrice") or 0.0)
        vol24h = float(item.get("quoteVolume") or 0.0)
        chg24 = float(item.get("priceChangePercent") or 0.0)
        high24 = float(item.get("highPrice") or price)
        low24 = float(item.get("lowPrice") or price)
        if price <= 0:
            continue

        with STATE_LOCK:
            prev_w7 = TICKER_MAP.get(sym, {}).get("win7m", 0.0)
            TICKER_MAP[sym] = {
                "symbol": sym,
                "base": base,
                "quote": quote,
                "price": price,
                "change24h": round(chg24, 2),
                "vol24h": vol24h,
                "high24h": high24,
                "low24h": low24,
                "win7m": prev_w7,
            }
        evaluate_pump_tick(sym, base, quote, price, vol24h, chg24, high24, low24, now_ts)

    with STATE_LOCK:
        LAST_TICK_TS = now_ts
        PUMP_LOOPS += 1
        changed = False
        for s in SIGNALS:
            tk = TICKER_MAP.get(s["symbol"])
            if tk and tk["price"] > 0:
                s["current_price"] = tk["price"]
                pct = ((tk["price"] - s["entry_price"]) / s["entry_price"]) * 100.0
                if pct > s.get("peak_pct", 0.0):
                    s["peak_pct"] = round(pct, 2)
                    s["peak_price"] = tk["price"]
                    changed = True
        if changed:
            save_state()


def run_boot_breakout_catchup():
    if not BOT_RUNNING:
        return
    with STATE_LOCK:
        cands = sorted(
            [t for t in TICKER_MAP.values() if t["quote"] == "USDT" and t["vol24h"] >= 250_000 and t["change24h"] >= 6.0],
            key=lambda x: x["change24h"],
            reverse=True,
        )[:12]
    now_ts = time.time()
    triggered = 0
    for t in cands:
        if triggered >= 5:
            break
        if is_pump_locked(t["base"], now_ts):
            continue
        try:
            pos24 = ((t["price"] - t["low24h"]) / (t["high24h"] - t["low24h"])) if t["high24h"] > t["low24h"] else 0.0
            dist_high = ((t["high24h"] - t["price"]) / t["high24h"]) * 100.0 if t["high24h"] > 0 else 99.0
            bars = fetch_klines(t["symbol"], "15m", 12)
            cur = bars[-1] if bars else None
            pct15 = ((t["price"] - cur["low"]) / cur["low"]) * 100.0 if cur and cur["low"] > 0 else 0.0
            if pct15 >= 3.2 or (pos24 >= 0.72 and dist_high <= 3.5):
                with STATE_LOCK:
                    PRICE_WINDOW_HISTORY[t["symbol"]] = [
                        (now_ts - 60.0, cur["low"] if cur else t["price"]),
                        (now_ts - 1.0, t["price"]),
                    ]
                evaluate_pump_tick(t["symbol"], t["base"], t["quote"], t["price"], t["vol24h"], t["change24h"], t["high24h"], t["low24h"], now_ts)
                triggered += 1
        except Exception:
            pass


# ==============================================================================
# EK ZAMANLI GÖREVLER: BINANCE DUYURU + 21:00 ZAFER + 23:00 TOP 24S ÖZETİ
# ==============================================================================
def translate_news_title_tr(title: str) -> str:
    pairs = [
        ("Binance Will List", "Binance Listeliyor:"),
        ("Binance Will Add", "Binance Ekliyor:"),
        ("Binance Will Support", "Binance Destekliyor:"),
        ("Binance Futures Will Launch", "Binance Vadeli Başlatıyor:"),
        ("Notice of Removal of Spot Trading Pairs", "Spot Çiftleri Kaldırma (Delist):"),
        ("Binance Will Delist", "Binance Delist Ediyor:"),
        ("Network Upgrade", "Ağ Güncellemesi"),
    ]
    tr = title.strip()
    for eng, tur in pairs:
        tr = re.sub(re.escape(eng), tur, tr, flags=re.IGNORECASE)
    return tr


def poll_binance_news():
    global NEWS_SEEDED
    catalogs = ((48, "Yeni Listeleme"), (49, "Binance Duyuru"), (161, "Delist / Kaldırma"))
    new_items = []
    for cat_id, cat_label in catalogs:
        try:
            url = f"https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query?catalogId={cat_id}&pageNo=1&pageSize=8"
            data = http_get_json(url, timeout=8.0)
            arts = data.get("data", {}).get("articles", []) or []
            for art in arts:
                aid = str(art.get("id") or art.get("code") or art.get("title") or "")
                if not aid or aid in NEWS_SEEN_IDS:
                    continue
                NEWS_SEEN_IDS.add(aid)
                code = str(art.get("code") or "")
                link = f"https://www.binance.com/en/support/announcement/detail/{code}" if code else "https://www.binance.com/en/support/announcement"
                new_items.append((cat_label, translate_news_title_tr(str(art.get("title") or "")), link))
        except Exception:
            pass

    if NEWS_SEEDED and new_items:
        for cat_label, title_tr, link in new_items[:5]:
            send_to_targets(f"📰 <b>Binance | {html.escape(cat_label)}</b>\n{html.escape(title_tr)}\n{link}")
    if new_items:
        save_state()
    NEWS_SEEDED = True


def check_daily_summaries():
    global LAST_VICTORY_DATE, LAST_SUMMARY_DATE
    now_tr = datetime.datetime.now(TR_TZ)
    today_str = now_tr.strftime("%Y-%m-%d")
    cutoff = time.time() - 86400.0

    with STATE_LOCK:
        day_sigs = [s for s in SIGNALS if s.get("created_at", 0) >= cutoff]

    if now_tr.hour == 21 and LAST_VICTORY_DATE != today_str:
        LAST_VICTORY_DATE = today_str
        save_state()
        winners = sorted([s for s in day_sigs if s.get("peak_pct", 0.0) >= 3.0], key=lambda x: x.get("peak_pct", 0.0), reverse=True)
        if winners:
            lines = ["🏆 <b>GÜNÜN ZAFERİ (Son 24s +%3+ Pik Sinyaller)</b>\n"]
            for i, s in enumerate(winners[:15], 1):
                lines.append(f"{i}. <b>#{s['symbol']}</b> · Pik: <b>+{s['peak_pct']:.2f}%</b> · Giriş: ${fmt_price(s['entry_price'])}")
            send_to_targets("\n".join(lines))

    if now_tr.hour == 23 and LAST_SUMMARY_DATE != today_str:
        LAST_SUMMARY_DATE = today_str
        save_state()
        if day_sigs:
            top_sigs = sorted(day_sigs, key=lambda x: x.get("peak_pct", 0.0), reverse=True)
            lines = [f"📊 <b>TOP 24 SAAT ÖZETİ ({len(top_sigs)} Sinyal)</b>\n"]
            for i, s in enumerate(top_sigs[:20], 1):
                lines.append(f"{i}. <b>#{s['symbol']}</b> · Pik: <b>+{s.get('peak_pct', 0.0):.2f}%</b> · Anlık: ${fmt_price(s.get('current_price', s['entry_price']))}")
            send_to_targets("\n".join(lines))


# ==============================================================================
# TELEGRAM KOMUTLARI & ETKILEŞIM
# ==============================================================================
def build_main_keyboard():
    toggle_label = "⏸ Taramayı Duraklat" if BOT_RUNNING else "▶️ Taramayı Başlat"
    return {
        "inline_keyboard": [
            [
                {"text": "📈 24s En Yükseğe Gidenler", "callback_data": "market_24h"},
                {"text": "🖼 Piyasa Görseli", "callback_data": "market_img"},
            ],
            [
                {"text": "🧪 Test Bildirimi Gönder", "callback_data": "send_test"},
                {"text": "📊 Durum", "callback_data": "status"},
            ],
            [
                {"text": "🔓 Kilitleri Sıfırla", "callback_data": "reset_locks"},
                {"text": toggle_label, "callback_data": "toggle_bot"},
            ],
        ]
    }


def get_sorted_market(mode: str = "24h"):
    with STATE_LOCK:
        rows = [t for t in TICKER_MAP.values() if t["quote"] == "USDT" and t["change24h"] > 0]
    if mode == "7m":
        rows.sort(key=lambda x: x.get("win7m", 0.0), reverse=True)
    elif mode == "vol":
        rows.sort(key=lambda x: x.get("vol24h", 0.0), reverse=True)
    else:
        rows.sort(key=lambda x: x.get("change24h", 0.0), reverse=True)
    return rows


def format_market_text(mode: str = "24h") -> str:
    rows = get_sorted_market(mode)[:15]
    titles = {"24h": "24s En Yükseğe Gidenler", "7m": "7dk İvme %", "vol": "24s Hacim (Yükselenler)"}
    lines = [f"📈 <b>ANLIK SPOT PİYASA ({titles.get(mode, '24s %')})</b>\n"]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{i:02d}. <b>{r['symbol']}</b> · ${fmt_price(r['price'])} | "
            f"7dk: {r.get('win7m', 0.0):+.2f}% | 24s: <b>{r['change24h']:+.2f}%</b> | {fmt_vol(r['vol24h'])}"
        )
    if not rows:
        lines.append("Veri henüz yükleniyor...")
    return "\n".join(lines)


def send_test_notification(target_cid: str = None):
    now_tr = datetime.datetime.now(TR_TZ).strftime("%H:%M:%S TR")
    try:
        bars = fetch_klines("BTCUSDT", "15m", 36)
        cur_p = bars[-1]["close"] if bars else 65000.0
    except Exception:
        bars = []
        cur_p = 65000.0
    msg = (
        f"🧪 <b>TEST BİLDİRİMİ — Telegram Bağlantısı Çalışıyor!</b>\n"
        f"🕒 Saat: {now_tr}\n"
        f"🤖 Mod: Sadece Yükselenler (0-7dk / 15dk Pump + 24s En Yükseğe Gidenler)\n"
        f"🔎 Taranan Aktif Çift: {len(TICKER_MAP)}"
    )
    png = draw_pump_chart_png("TEST-BTCUSDT", bars, "+4.20%", "2.1x", cur_p) if bars else None
    if target_cid:
        if png:
            res = tg_send_photo(target_cid, png, msg, reply_markup=build_main_keyboard())
            if res.get("ok"):
                return
        tg_request("sendMessage", {"chat_id": target_cid, "text": msg, "parse_mode": "HTML", "reply_markup": build_main_keyboard()})
    else:
        send_to_targets(msg, png_bytes=png, caption=msg, reply_markup=build_main_keyboard())


def handle_telegram_command(cid: str, text: str):
    global BOT_RUNNING
    register_chat_id(cid)
    cmd = text.strip().split()[0].lower()

    if cmd in ("/start", "/help", "/menu"):
        msg = (
            f"⚡ <b>Spot Pump & 24s En Yükseğe Gidenler Aktif</b>\n"
            f"🆔 Sohbet ID: <code>{cid}</code> (otomatik kaydedildi)\n"
            f"📈 Yalnızca Yükselenler: 0-7dk / 15dk Pump + 24s Zirve Kırılımı (LONG)\n"
            f"🔒 Dalga Kilidi: {int(PUMP_REPEAT_HOURS)} saat\n\n"
            f"Komutlar: /24s · /piyasa · /piyasagorsel · /test · /durum · /sifirla"
        )
        tg_request("sendMessage", {"chat_id": cid, "text": msg, "parse_mode": "HTML", "reply_markup": build_main_keyboard()})
    elif cmd == "/test":
        send_test_notification(cid)
    elif cmd == "/durum":
        uptime_m = int((time.time() - STARTED_AT) // 60)
        msg = (
            f"📊 <b>Bot Durumu</b>\n"
            f"• Durum: {'🟢 Çalışıyor' if BOT_RUNNING else '🟡 Duraklatıldı'}\n"
            f"• Çalışma Süresi: {uptime_m} dk\n"
            f"• Aktif Spot Çift: {len(TICKER_MAP)}\n"
            f"• Tarama Turu: {PUMP_LOOPS}\n"
            f"• Aktif Kilit: {len(LOCKS['pump'])}\n"
            f"• Kayıtlı Sohbetler: {', '.join(get_target_chats())}\n"
            f"• Mirror: {BINANCE_MIRRORS[PREFERRED_MIRROR_IDX]}"
        )
        tg_request("sendMessage", {"chat_id": cid, "text": msg, "parse_mode": "HTML", "reply_markup": build_main_keyboard()})
    elif cmd in ("/piyasa", "/24s"):
        kb = {
            "inline_keyboard": [[
                {"text": "24s %", "callback_data": "market_24h"},
                {"text": "7dk %", "callback_data": "market_7m"},
                {"text": "Hacim", "callback_data": "market_vol"},
                {"text": "🖼 Görsel", "callback_data": "market_img"},
            ]]
        }
        tg_request("sendMessage", {"chat_id": cid, "text": format_market_text("24h"), "parse_mode": "HTML", "reply_markup": kb})
    elif cmd == "/piyasagorsel":
        png = draw_market_radar_png(get_sorted_market("24h"))
        tg_send_photo(cid, png, "🖼 <b>Anlık Spot Piyasa — 24s En Yükseğe Gidenler</b>")
    elif cmd == "/sifirla":
        with STATE_LOCK:
            LOCKS["pump"] = {}
            save_state()
        tg_request("sendMessage", {"chat_id": cid, "text": "🔓 Tüm pump kilitleri sıfırlandı."})


def telegram_commands_loop():
    if not BOT_TOKEN:
        return
    offset = 0
    while True:
        try:
            res = tg_request("getUpdates", {"offset": offset, "timeout": 25}, timeout=32.0)
            if not res.get("ok"):
                if res.get("error_code") == 409:
                    log("[KRİTİK UYARI] Telegram 409 Conflict! Aynı BOT_TOKEN şu anda başka bir sunucuda daha çalışıyor. Sadece 1 sunucu açık bırakın!")
                    time.sleep(15.0)
                else:
                    time.sleep(5.0)
                continue
            for upd in res.get("result", []):
                offset = max(offset, upd["update_id"] + 1)
                if "message" in upd and "text" in upd["message"]:
                    cid = str(upd["message"]["chat"]["id"])
                    handle_telegram_command(cid, upd["message"]["text"])
                elif "callback_query" in upd:
                    cq = upd["callback_query"]
                    cid = str(cq["message"]["chat"]["id"])
                    data = str(cq.get("data", ""))
                    register_chat_id(cid)
                    tg_request("answerCallbackQuery", {"callback_query_id": cq["id"]})
                    if data in ("market_24h", "market_7m", "market_vol"):
                        mode = data.split("_")[1]
                        tg_request("sendMessage", {"chat_id": cid, "text": format_market_text(mode), "parse_mode": "HTML"})
                    elif data == "market_img":
                        handle_telegram_command(cid, "/piyasagorsel")
                    elif data == "send_test":
                        send_test_notification(cid)
                    elif data == "status":
                        handle_telegram_command(cid, "/durum")
                    elif data == "reset_locks":
                        handle_telegram_command(cid, "/sifirla")
                    elif data == "toggle_bot":
                        global BOT_RUNNING
                        BOT_RUNNING = not BOT_RUNNING
                        tg_request("sendMessage", {"chat_id": cid, "text": "🟢 Tarama başlatıldı." if BOT_RUNNING else "🟡 Tarama duraklatıldı.", "reply_markup": build_main_keyboard()})
        except Exception as exc:
            log(f"[Telegram Polling HATA] {exc}")
            time.sleep(5.0)


# ==============================================================================
# HEALTH CHECK HTTP SUNUCUSU (HEAD + GET DESTEKLI — UPTIMEROBOT / RENDER)
# ==============================================================================
class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

    def do_GET(self):
        payload = json.dumps({
            "ok": True,
            "running": BOT_RUNNING,
            "bot_username": BOT_USERNAME,
            "telegram_ok": TELEGRAM_OK,
            "last_tg_error": LAST_TG_ERROR,
            "subscribed_chats": get_target_chats(),
            "scanned_pairs": len(TICKER_MAP),
            "pump_loops": PUMP_LOOPS,
            "signals_count": len(SIGNALS),
            "uptime_sec": round(time.time() - STARTED_AT),
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


def start_health_server():
    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("0.0.0.0", PORT), HealthHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"[HTTP] Health sunucusu 0.0.0.0:{PORT} üzerinde aktif (GET & HEAD 200 OK)")


# ==============================================================================
# ANA DÖNGÜLER & BAŞLATMA
# ==============================================================================
def scanner_loop():
    last_ex_refresh = 0.0
    catchup_done = False
    while True:
        try:
            now = time.time()
            if now - last_ex_refresh > 900.0 or not ACTIVE_TRADING_SYMBOLS:
                refresh_active_trading_symbols()
                last_ex_refresh = now
            poll_tickers_once()
            if not catchup_done and TICKER_MAP:
                catchup_done = True
                top_rows = get_sorted_market("24h")
                if top_rows:
                    png = draw_market_radar_png(top_rows)
                    send_to_targets(format_market_text("24h"), png_bytes=png, caption="📈 <b>ANLIK SPOT PİYASA — 24s En Yükseğe Gidenler</b>")
                threading.Thread(target=run_boot_breakout_catchup, daemon=True).start()
        except Exception as exc:
            log(f"[Tarama HATA] {exc}")
        time.sleep(3.0)


def aux_loop():
    last_ping = 0.0
    while True:
        try:
            poll_binance_news()
            check_daily_summaries()
            if RENDER_EXTERNAL_URL and (time.time() - last_ping) > 480.0:
                last_ping = time.time()
                try:
                    urllib.request.urlopen(f"{RENDER_EXTERNAL_URL.rstrip('/')}/health", timeout=5.0).read()
                except Exception:
                    pass
        except Exception as exc:
            log(f"[Yardımcı Döngü HATA] {exc}")
        time.sleep(60.0)


def startup_telegram_check():
    global BOT_USERNAME
    if not BOT_TOKEN:
        log("[KRİTİK] BOT_TOKEN ortam değişkeni BOŞ! Render/Sunucu Environment Variables kısmına BOT_TOKEN ekleyin.")
        return
    me = tg_request("getMe", {})
    if not me.get("ok"):
        log(f"[KRİTİK] Telegram getMe başarısız! BOT_TOKEN geçersiz olabilir: {me.get('description')}")
        return
    BOT_USERNAME = str(me.get("result", {}).get("username", ""))
    log(f"[Telegram] Bağlandı -> @{BOT_USERNAME} | Hedefler: {get_target_chats()}")
    tg_request("deleteWebhook", {"drop_pending_updates": False})
    send_to_targets(
        f"🤖 <b>Spot Pump & 24s En Yükseğe Gidenler Botu Başlatıldı</b>\n"
        f"• Bot: @{BOT_USERNAME}\n"
        f"• Yalnızca Yükselenler: 0-7dk / 15dk Hacimli Pump + 24s Zirveye Gidenler\n"
        f"• Test için /test, 24s liste için /24s yazabilirsiniz.",
        reply_markup=build_main_keyboard(),
    )


def main():
    load_state()
    start_health_server()
    startup_telegram_check()
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=aux_loop, daemon=True).start()
    if BOT_TOKEN:
        telegram_commands_loop()
    else:
        while True:
            time.sleep(60.0)


if __name__ == "__main__":
    main()
