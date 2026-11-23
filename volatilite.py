import asyncio
import json
import os
import struct
import time
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import aiohttp
from aiohttp import web

# =====================================================
# AYARLAR
# =====================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "1623907197").strip()
PORT = int(os.getenv("PORT", "10000"))

TR_TZ = timezone(timedelta(hours=3))
STATE_FILE = os.getenv("STATE_FILE", "/tmp/tobo_state.json")

TOBO_TIMEFRAME = "1h"
TOBO_MIN_HEIGHT_PCT = 1.2
TOBO_SHOULDER_TOLERANCE = 0.03
TOBO_MAX_BARS_SINCE_RS = 12
TOBO_BREAK_BUFFER = 0.005
TOBO_COOLDOWN_HOURS = 4.0
TOBO_MIN_VOL_USDT = 500_000

SCAN_INTERVAL_S = 25
SCAN_BATCH = 10
TRACK_DURATION_S = 300
TRACK_INTERVAL_S = 30

MARKET_POLL_S = 10
SYMBOLS_REFRESH_S = 1800

BINANCE_MIRRORS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]

# =====================================================
# DURUM
# =====================================================
BOT_RUNNING = True
TICKER_MAP: Dict[str, Dict[str, Any]] = {}
ACTIVE_SPOT_SYMBOLS: Set[str] = set()
SIGNALS: List[Dict[str, Any]] = []
TOBO_LOCKS: Dict[str, float] = {}
SUBSCRIBED_CHATS: Set[str] = {CHAT_ID} if CHAT_ID else set()
PREFERRED_MIRROR_IDX = 0
RATE_LIMITED_UNTIL = 0.0


# =====================================================
# YARDIMCILAR
# =====================================================
def fmt_price(p: float) -> str:
    if p <= 0 or p != p:
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


def esc_html(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def log(msg: str) -> None:
    print(f"[{datetime.now(TR_TZ).strftime('%H:%M:%S')}] {msg}", flush=True)


def load_state() -> None:
    global SIGNALS, TOBO_LOCKS, SUBSCRIBED_CHATS
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw.get("signals"), list):
            SIGNALS = raw["signals"][:100]
        if isinstance(raw.get("locks"), dict):
            TOBO_LOCKS = {k: float(v) for k, v in raw["locks"].items()}
        for cid in raw.get("chats", []):
            if str(cid).strip():
                SUBSCRIBED_CHATS.add(str(cid).strip())
    except Exception:
        pass


def save_state() -> None:
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "signals": SIGNALS[:100],
                "locks": TOBO_LOCKS,
                "chats": list(SUBSCRIBED_CHATS),
            }, f)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


# =====================================================
# PNG OLUŞTURUCU
# =====================================================
FONT_5X7 = {
    "0": [0x3E, 0x51, 0x49, 0x45, 0x3E], "1": [0x00, 0x42, 0x7F, 0x40, 0x00],
    "2": [0x42, 0x61, 0x51, 0x49, 0x46], "3": [0x21, 0x41, 0x45, 0x4B, 0x31],
    "4": [0x18, 0x14, 0x12, 0x7F, 0x10], "5": [0x27, 0x45, 0x45, 0x45, 0x39],
    "6": [0x3C, 0x4A, 0x49, 0x49, 0x30], "7": [0x01, 0x71, 0x09, 0x05, 0x03],
    "8": [0x36, 0x49, 0x49, 0x49, 0x36], "9": [0x06, 0x49, 0x49, 0x29, 0x1E],
    "A": [0x7E, 0x11, 0x11, 0x11, 0x7E], "B": [0x7F, 0x49, 0x49, 0x49, 0x36],
    "C": [0x3E, 0x41, 0x41, 0x41, 0x22], "D": [0x7F, 0x41, 0x41, 0x22, 0x1C],
    "E": [0x7F, 0x49, 0x49, 0x49, 0x41], "F": [0x7F, 0x09, 0x09, 0x09, 0x01],
    "G": [0x3E, 0x41, 0x49, 0x49, 0x7A], "H": [0x7F, 0x08, 0x08, 0x08, 0x7F],
    "I": [0x00, 0x41, 0x7F, 0x41, 0x00], "J": [0x20, 0x40, 0x41, 0x3F, 0x01],
    "K": [0x7F, 0x08, 0x14, 0x22, 0x41], "L": [0x7F, 0x40, 0x40, 0x40, 0x40],
    "M": [0x7F, 0x02, 0x0C, 0x02, 0x7F], "N": [0x7F, 0x04, 0x08, 0x10, 0x7F],
    "O": [0x3E, 0x41, 0x41, 0x41, 0x3E], "P": [0x7F, 0x09, 0x09, 0x09, 0x06],
    "Q": [0x3E, 0x41, 0x51, 0x21, 0x5E], "R": [0x7F, 0x09, 0x19, 0x29, 0x46],
    "S": [0x46, 0x49, 0x49, 0x49, 0x31], "T": [0x01, 0x01, 0x7F, 0x01, 0x01],
    "U": [0x3F, 0x40, 0x40, 0x40, 0x3F], "V": [0x1F, 0x20, 0x40, 0x20, 0x1F],
    "W": [0x3F, 0x40, 0x38, 0x40, 0x3F], "X": [0x63, 0x14, 0x08, 0x14, 0x63],
    "Y": [0x07, 0x08, 0x70, 0x08, 0x07], "Z": [0x61, 0x51, 0x49, 0x45, 0x43],
    ".": [0x00, 0x60, 0x60, 0x00, 0x00], ",": [0x00, 0x50, 0x30, 0x00, 0x00],
    ":": [0x00, 0x36, 0x36, 0x00, 0x00], "-": [0x08, 0x08, 0x08, 0x08, 0x08],
    "+": [0x08, 0x08, 0x3E, 0x08, 0x08], "/": [0x20, 0x10, 0x08, 0x04, 0x02],
    "%": [0x23, 0x13, 0x08, 0x64, 0x62], "$": [0x24, 0x2A, 0x7F, 0x2A, 0x12],
    "|": [0x00, 0x00, 0x7F, 0x00, 0x00], "*": [0x14, 0x08, 0x3E, 0x08, 0x14],
    " ": [0x00, 0x00, 0x00, 0x00, 0x00],
}


def encode_png(w: int, h: int, buf: bytearray) -> bytes:
    stride = w * 3
    raw = bytearray((stride + 1) * h)
    for y in range(h):
        raw[y * (stride + 1)] = 0
        raw[y * (stride + 1) + 1: y * (stride + 1) + 1 + stride] = buf[y * stride: (y + 1) * stride]
    comp = zlib.compress(bytes(raw), 6)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", comp) + chunk(b"IEND", b"")


def _canvas(w: int, h: int, bg):
    buf = bytearray(bytes(bg) * (w * h))

    def set_px(x, y, col):
        if 0 <= x < w and 0 <= y < h:
            i = (y * w + x) * 3
            buf[i], buf[i + 1], buf[i + 2] = col

    def fill_rect(x0, y0, x1, y1, col):
        a0, a1 = max(0, min(x0, x1)), min(w - 1, max(x0, x1))
        b0, b1 = max(0, min(y0, y1)), min(h - 1, max(y0, y1))
        row = bytes(col) * (a1 - a0 + 1)
        for y in range(b0, b1 + 1):
            st = (y * w + a0) * 3
            buf[st:st + len(row)] = row

    def fill_circle(cx, cy, r, col):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy <= r * r:
                    set_px(cx + dx, cy + dy, col)

    def draw_line(x0, y0, x1, y1, col, dashed=False):
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        n = 0
        cx, cy = x0, y0
        while True:
            if not dashed or (n % 8) < 5:
                set_px(cx, cy, col)
            if cx == x1 and cy == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                cx += sx
            if e2 <= dx:
                err += dx
                cy += sy
            n += 1

    def draw_text(x, y, text, col, scale=1):
        cx = x
        for ch in text.upper():
            g = FONT_5X7.get(ch, FONT_5X7[" "])
            for gx in range(5):
                bits = g[gx]
                for gy in range(7):
                    if bits & (1 << gy):
                        if scale == 1:
                            set_px(cx + gx, y + gy, col)
                        else:
                            fill_rect(cx + gx * scale, y + gy * scale,
                                      cx + (gx + 1) * scale - 1, y + (gy + 1) * scale - 1, col)
            cx += 6 * scale

    return buf, set_px, fill_rect, fill_circle, draw_line, draw_text


def render_tobo_png(symbol, bars, live_price, head, neckline, target):
    if not bars:
        return b""
    sl = bars[-50:]
    w, h = 860, 440
    buf, set_px, fill_rect, fill_circle, draw_line, draw_text = _canvas(w, h, (11, 16, 27))
    pad_l, pad_r, pad_t, pad_b, vol_h, gap = 18, 118, 44, 22, 58, 12
    plot_w = w - pad_l - pad_r
    price_h = h - pad_t - pad_b - vol_h - gap

    prices = [b["high"] for b in sl] + [b["low"] for b in sl]
    for extra in (live_price, head, neckline, target):
        if extra and extra > 0:
            prices.append(extra)
    lo_p = min(prices) * 0.993
    hi_p = max(prices) * 1.007
    rng_p = max(hi_p - lo_p, lo_p * 0.004)
    max_vol = max((b["volume"] for b in sl), default=1.0) or 1.0

    def y_of(p):
        return round(pad_t + ((hi_p - p) / rng_p) * price_h)

    draw_text(pad_l, 14, f"{symbol} | 1H TOBO (INVERTED H&S) | ${fmt_price(live_price)}", (248, 250, 252), 2)

    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = round(pad_t + price_h * frac)
        draw_line(pad_l, gy, pad_l + plot_w, gy, (30, 41, 59), True)
        draw_text(pad_l + plot_w + 8, gy - 3, fmt_price(hi_p - rng_p * frac), (148, 163, 184), 1)

    if head > 0:
        hy = y_of(head)
        draw_line(pad_l, hy, pad_l + plot_w, hy, (244, 63, 94), True)
        draw_text(pad_l + plot_w + 8, hy - 3, f"BAS {fmt_price(head)}", (244, 63, 94), 1)

    if neckline > 0:
        ny = y_of(neckline)
        draw_line(pad_l, ny, pad_l + plot_w, ny, (245, 158, 11), True)
        draw_text(pad_l + plot_w + 8, ny - 3, f"BOYUN {fmt_price(neckline)}", (245, 158, 11), 1)

    if target > 0:
        ty = y_of(target)
        draw_line(pad_l, ty, pad_l + plot_w, ty, (52, 211, 153), True)
        draw_text(pad_l + plot_w + 8, ty - 3, f"HDF {fmt_price(target)}", (52, 211, 153), 1)

    step = plot_w / len(sl)
    bw = max(3, int(step * 0.62))
    vol_top = pad_t + price_h + gap
    for idx, b in enumerate(sl):
        cx = round(pad_l + (idx + 0.5) * step)
        col = (16, 185, 129) if b["close"] >= b["open"] else (244, 63, 94)
        yo, yc = y_of(b["open"]), y_of(b["close"])
        yh, yl = y_of(b["high"]), y_of(b["low"])
        draw_line(cx, yh, cx, yl, col)
        fill_rect(cx - bw // 2, min(yo, yc), cx + bw // 2,
                  max(min(yo, yc) + 1, max(yo, yc)), col)
        vh = max(1, round((b["volume"] / max_vol) * vol_h))
        fill_rect(cx - bw // 2, vol_top + vol_h - vh, cx + bw // 2, vol_top + vol_h,
                  (col[0] // 2, col[1] // 2, col[2] // 2))

    if live_price > 0:
        ly = y_of(live_price)
        draw_line(pad_l, ly, pad_l + plot_w, ly, (56, 189, 248), True)
        fill_rect(pad_l + plot_w + 4, ly - 7, w - 4, ly + 7, (2, 132, 199))
        draw_text(pad_l + plot_w + 8, ly - 3, fmt_price(live_price), (248, 250, 252), 1)

    return encode_png(w, h, buf)


# =====================================================
# BINANCE (rate limit korumalı)
# =====================================================
async def fetch_binance_json(session: aiohttp.ClientSession, path: str, timeout_s: float = 9.0) -> Any:
    global PREFERRED_MIRROR_IDX, RATE_LIMITED_UNTIL
    now = time.time()
    if now < RATE_LIMITED_UNTIL:
        await asyncio.sleep(RATE_LIMITED_UNTIL - now)

    n = len(BINANCE_MIRRORS)
    last_err = None
    for off in range(n):
        idx = (PREFERRED_MIRROR_IDX + off) % n
        url = f"{BINANCE_MIRRORS[idx]}{path}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout_s)) as r:
                if r.status == 200:
                    PREFERRED_MIRROR_IDX = idx
                    return await r.json()
                if r.status in (418, 429):
                    try:
                        ra = int(r.headers.get("Retry-After", "60"))
                    except Exception:
                        ra = 60
                    RATE_LIMITED_UNTIL = max(RATE_LIMITED_UNTIL, time.time() + ra + 2)
                    log(f"RATE LIMIT {r.status} | {ra}s bekleniyor")
                    last_err = f"HTTP {r.status}"
                    continue
                last_err = f"HTTP {r.status}"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Binance erisilemedi: {last_err}")


async def fetch_spot_klines(session: aiohttp.ClientSession, symbol: str, interval: str, limit: int = 120) -> List[Dict[str, float]]:
    raw = await fetch_binance_json(session, f"/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}")
    if not isinstance(raw, list):
        return []
    return [{
        "openTime": int(k[0]) // 1000,
        "open": float(k[1]),
        "high": float(k[2]),
        "low": float(k[3]),
        "close": float(k[4]),
        "volume": float(k[5]),
        "quoteVol": float(k[7]),
    } for k in raw]


# =====================================================
# TELEGRAM
# =====================================================
async def tg_send_text(session, text: str, explicit_cid: Optional[str] = None) -> None:
    if not BOT_TOKEN:
        return
    targets = [explicit_cid] if explicit_cid else list(SUBSCRIBED_CHATS)
    for cid in targets:
        try:
            async with session.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": cid, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=aiohttp.ClientTimeout(total=12),
            ) as r:
                await r.read()
        except Exception:
            pass


async def tg_send_photo(session, png: bytes, caption: str, explicit_cid: Optional[str] = None) -> Optional[int]:
    if not BOT_TOKEN:
        return None
    targets = [explicit_cid] if explicit_cid else list(SUBSCRIBED_CHATS)
    safe_cap = caption[:995] + "..." if len(caption) > 1000 else caption
    last_id = None
    for cid in targets:
        try:
            data = aiohttp.FormData()
            data.add_field("chat_id", cid)
            data.add_field("caption", safe_cap)
            data.add_field("parse_mode", "HTML")
            data.add_field("photo", png, filename="chart.png", content_type="image/png")
            async with session.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data=data, timeout=aiohttp.ClientTimeout(total=18),
            ) as r:
                js = await r.json()
                if js.get("ok"):
                    last_id = js["result"]["message_id"]
                else:
                    await tg_send_text(session, caption, cid)
        except Exception:
            await tg_send_text(session, caption, cid)
    return last_id


async def tg_edit_caption(session, cid: str, msg_id: int, caption: str) -> None:
    if not BOT_TOKEN or not msg_id:
        return
    try:
        async with session.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageCaption",
            json={"chat_id": cid, "message_id": msg_id, "caption": caption[:1000], "parse_mode": "HTML"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            await r.read()
    except Exception:
        pass


# =====================================================
# TOBO TESPİT
# =====================================================
def detect_tobo(bars: List[Dict[str, float]]) -> Optional[Dict[str, Any]]:
    if len(bars) < 40:
        return None
    rec = bars[-75:]
    n = len(rec)
    break_bar = rec[n - 2]
    cur = rec[n - 1]["close"]

    lows: List[tuple] = []
    for i in range(3, n - 3):
        if all(rec[j]["low"] > rec[i]["low"] for j in range(i - 3, i + 3) if j != i):
            lows.append((i, rec[i]["low"]))

    if len(lows) < 3:
        return None

    ls, hd, rs = lows[-3], lows[-2], lows[-1]

    if not (hd[1] < ls[1] * 0.994 and hd[1] < rs[1] * 0.994):
        return None
    if abs(ls[1] - rs[1]) / max(ls[1], 1e-9) > TOBO_SHOULDER_TOLERANCE:
        return None
    if (n - 2 - rs[0]) > TOBO_MAX_BARS_SINCE_RS:
        return None

    neckline = max(b["high"] for b in rec[hd[0]: rs[0] + 1])
    head_depth = (neckline - hd[1]) / hd[1] * 100.0
    if head_depth < TOBO_MIN_HEIGHT_PCT:
        return None

    if not (cur > neckline and break_bar["close"] > neckline * (1 - TOBO_BREAK_BUFFER)):
        return None

    return {
        "head": hd[1],
        "neckline": neckline,
        "target": neckline + (neckline - hd[1]),
        "head_depth_pct": head_depth,
        "ls": ls[1],
        "rs": rs[1],
    }


# =====================================================
# CAPTION
# =====================================================
def build_caption(symbol: str, entry: float, live: float,
                  peak_pct: float, cur_pct: float,
                  info: Dict[str, float], ts: str) -> str:
    head = info["head"]
    neckline = info["neckline"]
    target = info["target"]

    return (
        f"🔷 <b>Inverted H&S (TOBO) · {TOBO_TIMEFRAME} · LONG</b>\n\n"
        f"🕐 {ts}\n"
        f"💰 Giriş: <b>${fmt_price(entry)}</b>\n"
        f"📊 Anlık: ${fmt_price(live)} ({cur_pct:+.2f}%)\n"
        f"⬆️ Zirve: {peak_pct:+.2f}%\n\n"
        f"📐 <b>#{esc_html(symbol)}</b> [LONG]\n"
        f"Baş (${fmt_price(head)}) & Boyun (${fmt_price(neckline)}) yukarı kırıldı.\n"
        f"🎯 Hedef: <b>${fmt_price(target)}</b> · Destek: ${fmt_price(head)}"
    )


# =====================================================
# SİNYAL TAKİBİ
# =====================================================
async def track_signal(session, signal, cid, msg_id):
    entry = signal["entryPrice"]
    peak = entry
    end_t = time.time() + TRACK_DURATION_S
    while time.time() < end_t and BOT_RUNNING:
        await asyncio.sleep(TRACK_INTERVAL_S)
        tick = TICKER_MAP.get(signal["symbol"])
        if not tick:
            continue
        cur = tick["price"]
        if cur > peak:
            peak = cur
        peak_pct = (peak - entry) / entry * 100.0
        cur_pct = (cur - entry) / entry * 100.0
        cap = build_caption(signal["symbol"], entry, cur, peak_pct, cur_pct,
                            signal["info"], signal["ts"])
        await tg_edit_caption(session, cid, msg_id, cap)


# =====================================================
# SİNYAL EMIT
# =====================================================
async def emit_tobo_signal(session, ticker, info):
    symbol = ticker["symbol"]
    now_s = time.time()
    ts = datetime.now(TR_TZ).strftime("%H:%M:%S")
    entry = ticker["price"]

    signal = {
        "id": f"TOBO-{symbol}-{int(now_s * 1000)}",
        "symbol": symbol,
        "entryPrice": entry,
        "info": info,
        "ts": ts,
        "createdAt": int(now_s * 1000),
    }
    SIGNALS.insert(0, signal)
    del SIGNALS[100:]
    TOBO_LOCKS[symbol] = now_s
    save_state()

    log(f"TOBO SINYAL: {symbol} entry=${fmt_price(entry)} neckline=${fmt_price(info['neckline'])} target=${fmt_price(info['target'])} depth={info['head_depth_pct']:.2f}%")

    try:
        bars = await fetch_spot_klines(session, symbol, TOBO_TIMEFRAME, 80)
    except Exception:
        bars = []
    png = render_tobo_png(symbol, bars, entry, info["head"], info["neckline"], info["target"])
    cap = build_caption(symbol, entry, entry, 0.0, 0.0, info, ts)

    for cid in list(SUBSCRIBED_CHATS):
        msg_id = await tg_send_photo(session, png, cap, cid)
        if msg_id:
            asyncio.create_task(track_signal(session, signal, cid, msg_id))


# =====================================================
# DÖNGÜLER
# =====================================================
async def market_loop(session):
    while True:
        try:
            raw = await fetch_binance_json(session, "/api/v3/ticker/24hr")
            for item in raw:
                sym = str(item.get("symbol", ""))
                if not sym.endswith("USDT") or (ACTIVE_SPOT_SYMBOLS and sym not in ACTIVE_SPOT_SYMBOLS):
                    continue
                price = float(item.get("lastPrice") or 0)
                vol = float(item.get("quoteVolume") or 0)
                if price <= 0:
                    continue
                TICKER_MAP[sym] = {
                    "symbol": sym, "base": sym[:-4], "quote": "USDT",
                    "price": price,
                    "change24h": float(item.get("priceChangePercent") or 0),
                    "volume24h": vol,
                    "high24h": float(item.get("highPrice") or price),
                    "low24h": float(item.get("lowPrice") or price),
                }
        except Exception as e:
            log(f"market_loop: {e}")
        await asyncio.sleep(MARKET_POLL_S)


async def tobo_loop(session):
    cursor = 0
    while True:
        try:
            if not BOT_RUNNING or not TICKER_MAP:
                await asyncio.sleep(SCAN_INTERVAL_S)
                continue

            now_s = time.time()
            cands = sorted(
                [t for t in TICKER_MAP.values() if t["volume24h"] >= TOBO_MIN_VOL_USDT],
                key=lambda x: x["volume24h"], reverse=True,
            )
            if not cands:
                await asyncio.sleep(SCAN_INTERVAL_S)
                continue

            for _ in range(min(SCAN_BATCH, len(cands))):
                t = cands[cursor % len(cands)]
                cursor += 1
                sym = t["symbol"]

                if now_s - TOBO_LOCKS.get(sym, 0) < TOBO_COOLDOWN_HOURS * 3600:
                    continue

                try:
                    bars = await fetch_spot_klines(session, sym, TOBO_TIMEFRAME, 80)
                except Exception:
                    continue

                info = detect_tobo(bars)
                if info:
                    await emit_tobo_signal(session, t, info)
                await asyncio.sleep(0.15)

        except Exception as e:
            log(f"tobo_loop: {e}")
        await asyncio.sleep(SCAN_INTERVAL_S)


async def refresh_symbols_loop(session):
    while True:
        try:
            sp = await fetch_binance_json(session, "/api/v3/exchangeInfo")
            ACTIVE_SPOT_SYMBOLS.clear()
            for s in sp.get("symbols", []):
                if s.get("status") == "TRADING" and s.get("isSpotTradingAllowed", True):
                    ACTIVE_SPOT_SYMBOLS.add(s["symbol"])
            log(f"Symbols: {len(ACTIVE_SPOT_SYMBOLS)}")
        except Exception as e:
            log(f"refresh_symbols: {e}")
        await asyncio.sleep(SYMBOLS_REFRESH_S)


async def telegram_command_loop(session):
    if not BOT_TOKEN:
        return
    offset = 0
    while True:
        try:
            async with session.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                json={"offset": offset, "timeout": 25},
                timeout=aiohttp.ClientTimeout(total=32),
            ) as r:
                js = await r.json()
            if not js.get("ok"):
                await asyncio.sleep(5)
                continue
            for upd in js.get("result", []):
                offset = max(offset, int(upd["update_id"]) + 1)
                msg = upd.get("message") or {}
                txt = str(msg.get("text") or "").strip()
                cid = str((msg.get("chat") or {}).get("id") or "")
                if not txt or not cid:
                    continue
                SUBSCRIBED_CHATS.add(cid)
                cmd = txt.split()[0].split("@")[0].lower()
                if cmd in ("/start", "/help"):
                    await tg_send_text(session,
                        f"⚡ <b>TOBO 1h LONG Botu</b>\n"
                        f"Durum: {'🟢 Aktif' if BOT_RUNNING else '🔴 Durdu'}\n\n"
                        f"/durum - motor durumu\n"
                        f"/son - son 5 sinyal", cid)
                elif cmd == "/durum":
                    await tg_send_text(session,
                        f"📊 <b>Durum</b>\n"
                        f"Motor: Inverted H&S (TOBO) · {TOBO_TIMEFRAME} · LONG\n"
                        f"Taranan coin: {len(TICKER_MAP)}\n"
                        f"Kilitli coin: {len(TOBO_LOCKS)}\n"
                        f"Toplam sinyal: {len(SIGNALS)}", cid)
                elif cmd == "/son":
                    if not SIGNALS:
                        await tg_send_text(session, "Henüz sinyal yok.", cid)
                    else:
                        lines = []
                        for s in SIGNALS[:5]:
                            ts = datetime.fromtimestamp(s["createdAt"] / 1000, TR_TZ).strftime("%H:%M")
                            lines.append(f"{ts} · <b>#{esc_html(s['symbol'])}</b> · ${fmt_price(s['entryPrice'])}")
                        await tg_send_text(session, "📜 <b>Son Sinyaller</b>\n" + "\n".join(lines), cid)
        except Exception as e:
            log(f"tg_cmd: {e}")
            await asyncio.sleep(4)


# =====================================================
# MAIN
# =====================================================
async def main():
    load_state()
    log("=" * 50)
    log("TOBO 1h LONG Botu baslatiliyor...")
    log(f"BOT_TOKEN: {'VAR' if BOT_TOKEN else 'YOK!'} | CHAT_ID: {CHAT_ID}")
    log(f"STATE_FILE: {STATE_FILE}")
    log(f"Web Port: {PORT}")
    log("=" * 50)

    app = web.Application()
    app.router.add_get("/", lambda _: web.Response(text="TOBO Bot Running"))
    app.router.add_get("/health", lambda _: web.json_response({
        "ok": True, "pairs": len(TICKER_MAP), "signals": len(SIGNALS),
        "locked": len(TOBO_LOCKS), "rate_limited": time.time() < RATE_LIMITED_UNTIL,
    }))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log(f"Web server aktif: port {PORT}")

    if BOT_TOKEN and CHAT_ID:
        async with aiohttp.ClientSession() as s:
            await tg_send_text(s,
                f"🤖 <b>TOBO 1h LONG Botu başladı</b>\n"
                f"Motor: Inverted H&S (TOBO) · {TOBO_TIMEFRAME} · LONG\n"
                f"Her turda ~{SCAN_BATCH} coin taranacak.", CHAT_ID)

    async with aiohttp.ClientSession() as session:
        await asyncio.gather(
            refresh_symbols_loop(session),
            market_loop(session),
            tobo_loop(session),
            telegram_command_loop(session),
        )


if __name__ == "__main__":
    asyncio.run(main())
