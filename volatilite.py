import urllib.request
import urllib.error
import urllib.parse
import json
import math
import os
import ssl
import struct
import threading
import time
import zlib
import http.server
from collections import deque
from datetime import datetime, timedelta, timezone

# =====================================================
# AYARLAR
# =====================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()
PORT = int(os.environ.get("PORT", "10000"))
STATE_FILE = os.environ.get("STATE_FILE", "/tmp/tobo_state.json")

TR_TZ = timezone(timedelta(hours=3))

TOBO_TIMEFRAME = "1h"
TOBO_MIN_HEIGHT_PCT = 1.2
TOBO_SHOULDER_TOLERANCE = 0.03
TOBO_MAX_BARS_SINCE_RS = 12
TOBO_BREAK_BUFFER = 0.005
TOBO_COOLDOWN_HOURS = 4.0
TOBO_MIN_VOL_USDT = 500_000
TOBO_KLINES_LIMIT = 80

SCAN_INTERVAL_S = 25
SCAN_BATCH = 10
MARKET_POLL_S = 10
SYMBOLS_REFRESH_S = 1800
TRACK_DURATION_S = 300
TRACK_INTERVAL_S = 30

BINANCE_MIRRORS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
]

# =====================================================
# GLOBAL DURUM
# =====================================================
TICKER_MAP = {}          # symbol -> ticker dict
ACTIVE_SPOT = set()
SIGNALS = []
TOBO_LOCKS = {}
SUBSCRIBED_CHATS = {CHAT_ID} if CHAT_ID else set()
MIRROR_IDX = 0
RATE_LIMITED_UNTIL = 0.0
STATE_LOCK = threading.Lock()


# =====================================================
# YARDIMCILAR
# =====================================================
def log(msg):
    print(f"[{datetime.now(TR_TZ).strftime('%H:%M:%S')}] {msg}", flush=True)


def fmt_price(p):
    if not p or p <= 0 or p != p or math.isinf(p):
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


def esc_html(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load_state():
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


def save_state():
    try:
        with STATE_LOCK:
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
# BINANCE İSTEKLERİ (mirror + rate limit koruması)
# =====================================================
def _http_get_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_binance_json(path, timeout=12):
    global MIRROR_IDX, RATE_LIMITED_UNTIL
    now = time.time()
    if now < RATE_LIMITED_UNTIL:
        time.sleep(RATE_LIMITED_UNTIL - now)

    n = len(BINANCE_MIRRORS)
    last_err = None
    for off in range(n):
        idx = (MIRROR_IDX + off) % n
        url = f"{BINANCE_MIRRORS[idx]}{path}"
        try:
            return _http_get_json(url, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                ra = 60
                try:
                    ra_hdr = e.headers.get("Retry-After")
                    if ra_hdr:
                        ra = int(ra_hdr)
                except Exception:
                    ra = 60
                RATE_LIMITED_UNTIL = max(RATE_LIMITED_UNTIL, time.time() + ra + 2)
                log(f"RATE LIMIT {e.code} | {ra}s bekleniyor")
                last_err = f"HTTP {e.code}"
                continue
            last_err = f"HTTP {e.code}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
    raise RuntimeError(f"Binance erisilemedi: {last_err}")


def fetch_spot_klines(symbol, interval, limit):
    raw = fetch_binance_json(f"/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}")
    if not isinstance(raw, list):
        return []
    out = []
    for k in raw:
        try:
            out.append({
                "openTime": int(k[0]) // 1000,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "quoteVol": float(k[7]),
            })
        except Exception:
            continue
    return out


# =====================================================
# TELEGRAM
# =====================================================
def tg_api(method, payload=None, files=None):
    if not BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        if files:
            boundary = f"----BotBoundary{os.urandom(8).hex()}"
            body = bytearray()
            for k, v in payload.items():
                body += f"--{boundary}\r\n".encode()
                body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
                body += str(v).encode("utf-8") + b"\r\n"
            for field_name, (filename, content, ctype) in files.items():
                body += f"--{boundary}\r\n".encode()
                body += f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode()
                body += f"Content-Type: {ctype}\r\n\r\n".encode()
                body += content + b"\r\n"
            body += f"--{boundary}--\r\n".encode()
            req = urllib.request.Request(
                url, data=bytes(body),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
            )
        else:
            data = urllib.parse.urlencode(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
            log(f"TG {method} HTTP {e.code}: {body[:200]}")
        except Exception:
            log(f"TG {method} HTTP {e.code}")
        return None
    except Exception as e:
        log(f"TG {method} hata: {e}")
        return None


def tg_send_text(text, cid=None):
    targets = [cid] if cid else list(SUBSCRIBED_CHATS)
    for c in targets:
        tg_api("sendMessage", {
            "chat_id": c, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        })


def tg_send_photo(png, caption, cid=None):
    targets = [cid] if cid else list(SUBSCRIBED_CHATS)
    safe_cap = caption[:1000]
    last_msg_id = None
    for c in targets:
        if not png:
            tg_send_text(caption, c)
            continue
        r = tg_api("sendPhoto",
                   {"chat_id": c, "caption": safe_cap, "parse_mode": "HTML"},
                   {"photo": ("chart.png", png, "image/png")})
        if r and r.get("ok"):
            last_msg_id = r["result"]["message_id"]
        else:
            tg_send_text(caption, c)
    return last_msg_id


def tg_edit_caption(cid, msg_id, caption):
    if not msg_id:
        return
    tg_api("editMessageCaption", {
        "chat_id": cid, "message_id": msg_id,
        "caption": caption[:1000], "parse_mode": "HTML",
    })


# =====================================================
# PNG ÇİZİM
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
    "(": [0x00, 0x1C, 0x22, 0x41, 0x00], ")": [0x00, 0x41, 0x22, 0x1C, 0x00],
    " ": [0x00, 0x00, 0x00, 0x00, 0x00],
}


def encode_png(w, h, buf):
    stride = w * 3
    raw = bytearray((stride + 1) * h)
    for y in range(h):
        raw[y * (stride + 1)] = 0
        raw[y * (stride + 1) + 1: y * (stride + 1) + 1 + stride] = buf[y * stride: (y + 1) * stride]
    comp = zlib.compress(bytes(raw), 6)

    def chunk(tag, payload):
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", comp) + chunk(b"IEND", b"")


def render_tobo_png(symbol, bars, live_price, head, neckline, target):
    if not bars:
        return b""
    sl = bars[-50:]
    w, h = 860, 440
    BG = (11, 16, 27)
    GRID = (30, 41, 59)
    TEXT = (248, 250, 252)
    MUTED = (148, 163, 184)
    BULL = (16, 185, 129)
    BEAR = (244, 63, 94)
    HEAD_COL = (244, 63, 94)
    NECK_COL = (245, 158, 11)
    TGT_COL = (52, 211, 153)
    LIVE_COL = (56, 189, 248)

    buf = bytearray(bytes(BG) * (w * h))

    def set_px(x, y, col):
        if 0 <= x < w and 0 <= y < h:
            i = (y * w + x) * 3
            buf[i], buf[i + 1], buf[i + 2] = col

    def fill_rect(x0, y0, x1, y1, col):
        a0, a1 = max(0, min(x0, x1)), min(w - 1, max(x0, x1))
        b0, b1 = max(0, min(y0, y1)), min(h - 1, max(y0, y1))
        if a1 < a0 or b1 < b0:
            return
        row = bytes(col) * (a1 - a0 + 1)
        for y in range(b0, b1 + 1):
            st = (y * w + a0) * 3
            buf[st:st + len(row)] = row

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

    pad_l, pad_r, pad_t, pad_b = 18, 118, 44, 22
    vol_h, gap = 58, 12
    plot_w = w - pad_l - pad_r
    price_h = h - pad_t - pad_b - vol_h - gap

    prices = [b["high"] for b in sl] + [b["low"] for b in sl]
    for v in (live_price, head, neckline, target):
        if v and v > 0:
            prices.append(v)
    lo_p = min(prices) * 0.993
    hi_p = max(prices) * 1.007
    rng_p = max(hi_p - lo_p, lo_p * 0.004)
    max_vol = max((b["volume"] for b in sl), default=1.0) or 1.0

    def y_of(p):
        return round(pad_t + ((hi_p - p) / rng_p) * price_h)

    draw_text(pad_l, 14, f"{symbol} | 1H TOBO | ${fmt_price(live_price)}", TEXT, 2)

    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = round(pad_t + price_h * frac)
        draw_line(pad_l, gy, pad_l + plot_w, gy, GRID, True)
        draw_text(pad_l + plot_w + 8, gy - 3, fmt_price(hi_p - rng_p * frac), MUTED, 1)

    if head > 0:
        hy = y_of(head)
        draw_line(pad_l, hy, pad_l + plot_w, hy, HEAD_COL, True)
        draw_text(pad_l + plot_w + 8, hy - 3, f"BAS {fmt_price(head)}", HEAD_COL, 1)

    if neckline > 0:
        ny = y_of(neckline)
        draw_line(pad_l, ny, pad_l + plot_w, ny, NECK_COL, True)
        draw_text(pad_l + plot_w + 8, ny - 3, f"BOYUN {fmt_price(neckline)}", NECK_COL, 1)

    if target > 0:
        ty = y_of(target)
        draw_line(pad_l, ty, pad_l + plot_w, ty, TGT_COL, True)
        draw_text(pad_l + plot_w + 8, ty - 3, f"HDF {fmt_price(target)}", TGT_COL, 1)

    step = plot_w / len(sl)
    bw = max(3, int(step * 0.62))
    vol_top = pad_t + price_h + gap
    for idx, b in enumerate(sl):
        cx = round(pad_l + (idx + 0.5) * step)
        col = BULL if b["close"] >= b["open"] else BEAR
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
        draw_line(pad_l, ly, pad_l + plot_w, ly, LIVE_COL, True)
        fill_rect(pad_l + plot_w + 4, ly - 7, w - 4, ly + 7, (2, 132, 199))
        draw_text(pad_l + plot_w + 8, ly - 3, fmt_price(live_price), TEXT, 1)

    return encode_png(w, h, buf)


# =====================================================
# TOBO TESPİT
# =====================================================
def detect_tobo(bars):
    if len(bars) < 40:
        return None
    rec = bars[-75:]
    n = len(rec)
    break_bar = rec[n - 2]
    cur = rec[n - 1]["close"]

    lows = []
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
def build_caption(symbol, entry, live, peak_pct, cur_pct, info, ts):
    head = info["head"]
    neckline = info["neckline"]
    target = info["target"]

    return (
        f"<b>Inverted H&amp;S (TOBO) ({TOBO_TIMEFRAME}) · LONG</b>\n\n"
        f"{ts}\n"
        f"Giriş: ${fmt_price(entry)}\n"
        f"Anlık: ${fmt_price(live)}\n"
        f"Zirve: {peak_pct:+.2f}%\n\n"
        f"Inverted H&amp;S (TOBO) ({TOBO_TIMEFRAME}) — #{esc_html(symbol)} [LONG]\n"
        f"📐 TOBO Baş (${fmt_price(head)}) &amp; boyun (${fmt_price(neckline)}) yukarı kırıldı.\n"
        f"🎯 Formasyon Hedefi: ${fmt_price(target)} ({cur_pct:+.2f}%) · Boyun/Destek: ${fmt_price(head)}"
    )


# =====================================================
# SİNYAL EMIT + TAKİP
# =====================================================
def emit_tobo_signal(symbol, entry, info):
    now_s = time.time()
    ts = datetime.now(TR_TZ).strftime("%H:%M:%S")

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

    log(f"TOBO SİNYAL: {symbol} entry=${fmt_price(entry)} neckline=${fmt_price(info['neckline'])} target=${fmt_price(info['target'])} depth={info['head_depth_pct']:.2f}%")

    try:
        bars = fetch_spot_klines(symbol, TOBO_TIMEFRAME, TOBO_KLINES_LIMIT)
    except Exception:
        bars = []
    png = render_tobo_png(symbol, bars, entry, info["head"], info["neckline"], info["target"])
    cap = build_caption(symbol, entry, entry, 0.0, 0.0, info, ts)

    for cid in list(SUBSCRIBED_CHATS):
        msg_id = tg_send_photo(png, cap, cid)
        if msg_id:
            threading.Thread(
                target=track_signal, args=(signal, cid, msg_id), daemon=True
            ).start()


def track_signal(signal, cid, msg_id):
    entry = signal["entryPrice"]
    peak = entry
    end_t = time.time() + TRACK_DURATION_S
    while time.time() < end_t:
        time.sleep(TRACK_INTERVAL_S)
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
        tg_edit_caption(cid, msg_id, cap)


# =====================================================
# DÖNGÜLER
# =====================================================
def market_loop():
    while True:
        try:
            raw = fetch_binance_json("/api/v3/ticker/24hr")
            for item in raw:
                sym = str(item.get("symbol", ""))
                if not sym.endswith("USDT"):
                    continue
                if ACTIVE_SPOT and sym not in ACTIVE_SPOT:
                    continue
                price = float(item.get("lastPrice") or 0)
                if price <= 0:
                    continue
                TICKER_MAP[sym] = {
                    "symbol": sym,
                    "base": sym[:-4],
                    "price": price,
                    "change24h": float(item.get("priceChangePercent") or 0),
                    "volume24h": float(item.get("quoteVolume") or 0),
                    "high24h": float(item.get("highPrice") or price),
                    "low24h": float(item.get("lowPrice") or price),
                }
        except Exception as e:
            log(f"market_loop: {e}")
        time.sleep(MARKET_POLL_S)


def tobo_loop():
    cursor = 0
    while True:
        try:
            if not TICKER_MAP:
                time.sleep(SCAN_INTERVAL_S)
                continue

            now_s = time.time()
            cands = sorted(
                [t for t in TICKER_MAP.values() if t["volume24h"] >= TOBO_MIN_VOL_USDT],
                key=lambda x: x["volume24h"], reverse=True,
            )
            if not cands:
                time.sleep(SCAN_INTERVAL_S)
                continue

            for _ in range(min(SCAN_BATCH, len(cands))):
                t = cands[cursor % len(cands)]
                cursor += 1
                sym = t["symbol"]

                if now_s - TOBO_LOCKS.get(sym, 0) < TOBO_COOLDOWN_HOURS * 3600:
                    continue

                try:
                    bars = fetch_spot_klines(sym, TOBO_TIMEFRAME, TOBO_KLINES_LIMIT)
                except Exception:
                    continue

                info = detect_tobo(bars)
                if info:
                    try:
                        emit_tobo_signal(sym, t["price"], info)
                    except Exception as e:
                        log(f"emit hata {sym}: {e}")
                time.sleep(0.2)

        except Exception as e:
            log(f"tobo_loop: {e}")
        time.sleep(SCAN_INTERVAL_S)


def refresh_symbols_loop():
    while True:
        try:
            sp = fetch_binance_json("/api/v3/exchangeInfo")
            tmp = set()
            for s in sp.get("symbols", []):
                if s.get("status") == "TRADING" and s.get("isSpotTradingAllowed", True):
                    tmp.add(s["symbol"])
            ACTIVE_SPOT.clear()
            ACTIVE_SPOT.update(tmp)
            log(f"Symbols: {len(ACTIVE_SPOT)}")
        except Exception as e:
            log(f"refresh_symbols: {e}")
        time.sleep(SYMBOLS_REFRESH_S)


def telegram_command_loop():
    if not BOT_TOKEN:
        return
    offset = 0
    while True:
        try:
            r = tg_api("getUpdates", {"offset": offset, "timeout": 25})
            if not r or not r.get("ok"):
                time.sleep(5)
                continue
            for upd in r.get("result", []):
                offset = max(offset, int(upd["update_id"]) + 1)
                msg = upd.get("message") or {}
                txt = str(msg.get("text") or "").strip()
                cid = str((msg.get("chat") or {}).get("id") or "")
                if not txt or not cid:
                    continue
                SUBSCRIBED_CHATS.add(cid)
                save_state()
                cmd = txt.split()[0].split("@")[0].lower()
                if cmd in ("/start", "/help"):
                    tg_send_text(
                        "<b>TOBO 1h LONG Botu</b>\n"
                        "/durum - motor durumu\n"
                        "/son - son 5 sinyal",
                        cid,
                    )
                elif cmd == "/durum":
                    tg_send_text(
                        f"<b>Durum</b>\n"
                        f"Motor: TOBO · {TOBO_TIMEFRAME} · LONG\n"
                        f"Taranan coin: {len(TICKER_MAP)}\n"
                        f"Kilitli coin: {len(TOBO_LOCKS)}\n"
                        f"Toplam sinyal: {len(SIGNALS)}",
                        cid,
                    )
                elif cmd == "/son":
                    if not SIGNALS:
                        tg_send_text("Henüz sinyal yok.", cid)
                    else:
                        lines = []
                        for s in SIGNALS[:5]:
                            ts = datetime.fromtimestamp(s["createdAt"] / 1000, TR_TZ).strftime("%H:%M")
                            lines.append(f"{ts} · <b>#{esc_html(s['symbol'])}</b> · ${fmt_price(s['entryPrice'])}")
                        tg_send_text("<b>Son Sinyaller</b>\n" + "\n".join(lines), cid)
        except Exception as e:
            log(f"tg_cmd: {e}")
            time.sleep(4)


# =====================================================
# WEB SUNUCU
# =====================================================
class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({
                "ok": True,
                "pairs": len(TICKER_MAP),
                "signals": len(SIGNALS),
                "locked": len(TOBO_LOCKS),
                "rate_limited": time.time() < RATE_LIMITED_UNTIL,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = b"TOBO Bot Running"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        return


def run_web_server():
    try:
        server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
        log(f"Web server aktif: port {PORT}")
        server.serve_forever()
    except Exception as e:
        log(f"Web server hata: {e}")


def gozetmen(fn, name):
    while True:
        try:
            fn()
            return
        except Exception as e:
            log(f"GÖZETMEN {name} çöktü: {e}, 30s sonra tekrar")
            time.sleep(30)


# =====================================================
# MAIN
# =====================================================
if __name__ == "__main__":
    load_state()
    log("=" * 50)
    log("TOBO 1h LONG Botu baslatiliyor (saf Python)")
    log(f"BOT_TOKEN: {'VAR' if BOT_TOKEN else 'YOK!'} | CHAT_ID: {CHAT_ID}")
    log(f"STATE_FILE: {STATE_FILE}")
    log(f"Port: {PORT}")
    log("=" * 50)

    threading.Thread(target=run_web_server, daemon=True).start()

    if BOT_TOKEN and CHAT_ID:
        tg_send_text(
            f"<b>TOBO 1h LONG Botu başladı</b>\n"
            f"Motor: Inverted H&S · {TOBO_TIMEFRAME} · LONG",
            CHAT_ID,
        )

    for fn, name in [
        (refresh_symbols_loop, "symbols"),
        (market_loop, "market"),
        (tobo_loop, "tobo"),
        (telegram_command_loop, "tg_cmd"),
    ]:
        threading.Thread(target=gozetmen, args=(fn, name), daemon=True).start()

    while True:
        time.sleep(3600)
