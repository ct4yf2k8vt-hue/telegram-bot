#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spot Akis Terminali - 7/24 Render Python 3 Telegram & Futures 3x Botu (Zero-Dependency)

Aktif Sinyal Motorlari (4 Bagimsiz Motor):
  1. PUMP      : 0-7 dk Spot Anlik Pump (+4.5%+ USDT) -> Tekil Coin Kilidi + Canli PNG Grafik + FUTURES 3x LONG
  2. PUMP15M   : 15 dk Hacimli Kirilim (+3.2%+ & RVOL>=1.4x) -> Ayri Kilit + Canli PNG Grafik + FUTURES 3x LONG
  3. ATL       : Tum Zamanlarin En Dusugu Kesin Kirilimi -> Yalniz Bildirim (Tekrar etmez, Islem ACMAZ)
  4. ATH       : Tum Zamanlarin En Yuksegi Kesin Kirilimi -> Yalniz Bildirim (Tekrar etmez, Islem ACMAZ)

Futures 3x Otomatik Al-Sat Kurallari (En Yuksek Guvenlik):
  - YALNIZCA PUMP (0-7 dk) ve PUMP15M (15 dk) USDT sinyallerinde Izole (ISOLATED) 3x LONG (BUY) acar.
  - Acildigi anda otomatik +%5 Kar Al (LIMIT SELL) VE -%5 Stop-Loss (STOP_MARKET SELL, MARK_PRICE) koyar.
  - Biri tetiklendiginde karsi emir otomatik iptal edilir (OCO mantigi).
  - En Az Yetki (Least-Privilege): Kod ASLA bakiye, hesap, kisi bilgisi, spot/margin veya para cekme/transfer endpointlerini cagirmaz.

Otomatik Ozet & Duyurular:
  - Gunun Zaferi (21:00 TR) -> GUNUN KAZANANI formati (metin + gorsel)
  - Top 24 Saat PNG Liderlik Karti & Madalyali Ozet (23:00 TR + /top24)
  - Binance Resmi Duyurulari (60 sn)
"""

import base64
import datetime
import hashlib
import hmac
import html
import http.server
import json
import math
import os
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

# ==============================================================================
# KONFIGURASYON (ENV VARIABLES)
# ==============================================================================
def _env_float(name, default, lo, hi):
    try:
        v = float(os.environ.get(name, str(default)).strip())
        if math.isfinite(v):
            return max(lo, min(hi, v))
    except Exception:
        pass
    return float(default)


def _env_int(name, default, lo, hi):
    try:
        v = int(float(os.environ.get(name, str(default)).strip()))
        return max(lo, min(hi, v))
    except Exception:
        pass
    return int(default)


BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()
PORT = _env_int("PORT", 10000, 1, 65535)
PUMP_REPEAT_HOURS = _env_float("PUMP_REPEAT_HOURS", 0.0, 0.0, 720.0)
STATE_FILE = os.environ.get("STATE_FILE", "/tmp/spot_bot_state.json").strip()

BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "").strip()
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "").strip()
BINANCE_KEYS_CONFIGURED = bool(BINANCE_API_KEY and BINANCE_API_SECRET)

_RAW_MODE = os.environ.get("TRADE_MODE", "PAPER").strip().upper()
DEFAULT_TRADE_MODE = _RAW_MODE if _RAW_MODE in ("PAPER", "TESTNET", "LIVE") else "PAPER"
if DEFAULT_TRADE_MODE in ("TESTNET", "LIVE") and not BINANCE_KEYS_CONFIGURED:
    DEFAULT_TRADE_MODE = "PAPER"

_RAW_AT_ENABLED = os.environ.get("AUTO_TRADE_ENABLED", "").strip().lower()
DEFAULT_AT_ENABLED = (DEFAULT_TRADE_MODE == "PAPER") if not _RAW_AT_ENABLED else (_RAW_AT_ENABLED in ("1", "true", "yes"))

BINANCE_MIRRORS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://data-api.binance.vision",
]

EXCLUDED_PREFIXES = ("USDC", "FDUSD", "TUSD", "USDP", "EUR", "USDE", "USD1", "BUSD")
EXCLUDED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

TR_TZ = datetime.timezone(datetime.timedelta(hours=3))

# ==============================================================================
# DURUM, KILIT VE FUTURES POZISYON YONETIMI
# ==============================================================================
state_lock = threading.Lock()
tickers = {}               # sym -> dict
active_spot_symbols = set()
futures_filters = {}       # sym -> {stepSize, minQty, tickSize, minNotional}
price_history = {}         # sym -> list[(ts_sec, price)]
signals_log = []           # list[dict]
trades_log = []            # list[dict]
trade_in_flight = set()
news_seen_ids = set()
news_seeded = False
ws_connected = False
ban_until_ts = 0.0
preferred_mirror_idx = 0
last_tg_update_id = 0

auto_trade = {
    "enabled": DEFAULT_AT_ENABLED,
    "mode": DEFAULT_TRADE_MODE,
    "leverage": _env_int("FUTURES_LEVERAGE", 3, 1, 20),
    "trade_usdt": _env_float("TRADE_USDT", 25.0, 5.0, 10000.0),
    "tp_pct": _env_float("TP_PCT", 5.0, 0.5, 200.0),
    "sl_pct": _env_float("SL_PCT", 5.0, 0.2, 50.0),
    "max_open_trades": _env_int("MAX_OPEN_TRADES", 5, 1, 25),
    "paper_balance_usdt": 1000.0,
}

locks = {
    "pump": {},       # base -> {"symbol": str, "locked_at": float}
    "pump15m": {},    # base -> {"symbol": str, "locked_at": float}
    "ath_atl": {},    # "SYM:ATL" | "SYM:ATH" -> {"level": float, "locked_at": float}
    "reports": {"victory_day": "", "top24_day": ""},
}


def load_state():
    global signals_log, trades_log
    try:
        if not os.path.exists(STATE_FILE):
            return
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        raw_locks = raw.get("locks", {})
        for k in ("pump", "pump15m", "ath_atl"):
            if isinstance(raw_locks.get(k), dict):
                locks[k] = raw_locks[k]
        if isinstance(raw_locks.get("reports"), dict):
            locks["reports"].update(raw_locks["reports"])
        raw_sigs = raw.get("signals", [])
        if isinstance(raw_sigs, list):
            signals_log = [s for s in raw_sigs if isinstance(s, dict)][-300:]
        raw_trades = raw.get("trades", [])
        if isinstance(raw_trades, list):
            trades_log = [t for t in raw_trades if isinstance(t, dict)][:200]
        raw_at = raw.get("auto_trade", {})
        if isinstance(raw_at, dict):
            if isinstance(raw_at.get("enabled"), bool):
                auto_trade["enabled"] = raw_at["enabled"]
            if raw_at.get("mode") in ("PAPER", "TESTNET", "LIVE"):
                if raw_at["mode"] == "PAPER" or BINANCE_KEYS_CONFIGURED:
                    auto_trade["mode"] = raw_at["mode"]
            for num_k, lo, hi, fn in (
                ("leverage", 1, 20, int),
                ("trade_usdt", 5.0, 10000.0, float),
                ("tp_pct", 0.5, 200.0, float),
                ("sl_pct", 0.2, 50.0, float),
                ("max_open_trades", 1, 25, int),
                ("paper_balance_usdt", 0.0, 1e9, float),
            ):
                if isinstance(raw_at.get(num_k), (int, float)) and math.isfinite(raw_at[num_k]):
                    auto_trade[num_k] = max(lo, min(hi, fn(raw_at[num_k])))
    except Exception:
        pass


def save_state():
    try:
        with state_lock:
            payload = {
                "locks": locks,
                "signals": signals_log[-300:],
                "trades": trades_log[:200],
                "auto_trade": auto_trade,
            }
        tmp_path = STATE_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, STATE_FILE)
    except Exception:
        pass


# ==============================================================================
# YARDIMCI FONKSIYONLAR
# ==============================================================================
def extract_base_quote(sym):
    for q in ("USDT", "BTC", "BNB"):
        if sym.endswith(q) and len(sym) > len(q):
            return sym[:-len(q)], q
    return None, None


def is_excluded(sym):
    if any(sym.startswith(p) for p in EXCLUDED_PREFIXES):
        return True
    if any(sym.endswith(s) for s in EXCLUDED_SUFFIXES):
        return True
    return False


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


def fmt_peak_dur(created_at, peak_at, ascii_only=False):
    diff_min = max(1, round((max(peak_at or created_at, created_at) - created_at) / 60.0))
    if diff_min < 60:
        return f"{diff_min}DK" if ascii_only else f"{diff_min}dk"
    hrs = diff_min / 60.0
    return f"{hrs:.1f}S" if ascii_only else f"{hrs:.1f}s"


def floor_to_step(val, step_str):
    try:
        step = float(step_str)
        if not math.isfinite(step) or step <= 0:
            return val
        decimals = len(step_str.split(".")[1].rstrip("0")) if "." in step_str else 0
        floored = math.floor(val / step + 1e-9) * step
        return round(floored, decimals)
    except Exception:
        return val


def format_by_step(val, step_str):
    try:
        step = float(step_str)
        if not math.isfinite(step) or step <= 0:
            return f"{val:.8f}".rstrip("0").rstrip(".")
        decimals = len(step_str.split(".")[1].rstrip("0")) if "." in step_str else 0
        rounded = math.floor(val / step + 1e-9) * step
        return f"{rounded:.{decimals}f}"
    except Exception:
        return f"{val:.8f}".rstrip("0").rstrip(".")


def tr_now():
    return datetime.datetime.now(TR_TZ)


def update_live_candle(candles, live_price):
    if not candles or not live_price or live_price <= 0:
        return candles
    out = [dict(b) for b in candles]
    last = out[-1]
    last["close"] = live_price
    if live_price > last["high"]:
        last["high"] = live_price
    if live_price < last["low"]:
        last["low"] = live_price
    return out


# ==============================================================================
# HTTP / BINANCE SPOT REST + WS-API + BYBIT + EN AZ YETKILI FUTURES SIGNED REST
# ==============================================================================
def http_get_json(url, timeout=9):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; SpotPumpFuturesBot/2.0)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def binance_rest_get(path_and_query, timeout=9):
    global ban_until_ts, preferred_mirror_idx
    now = time.time()
    if now < ban_until_ts:
        raise RuntimeError("REST rate limit active")
    n = len(BINANCE_MIRRORS)
    last_err = None
    for off in range(n):
        idx = (preferred_mirror_idx + off) % n
        url = BINANCE_MIRRORS[idx] + path_and_query
        try:
            data = http_get_json(url, timeout=timeout)
            preferred_mirror_idx = idx
            return data
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (418, 429):
                ban_until_ts = time.time() + 900
                raise
        except Exception as e:
            last_err = e
    raise last_err or RuntimeError("Binance mirror unreachable")


class BinanceFuturesError(RuntimeError):
    def __init__(self, status, code, msg):
        super().__init__(f"Binance Futures {status} ({code}): {msg}")
        self.status = status
        self.code = code
        self.msg = msg


# GUVENLIK KISITI (ALLOWLIST): Bot yalnizca emir/kaldirac/izole marjin endpointlerini cagirir;
# hesap bakiyesi (/fapi/v2/account), kisi bilgisi, spot veya para cekme endpointleri KESINLIKLE cagirlmaz.
_ALLOWED_FUTURES_SIGNED_PATHS = frozenset({
    "/fapi/v1/marginType",
    "/fapi/v1/leverage",
    "/fapi/v1/order",
})


def binance_futures_signed(method, path_url, params, mode):
    if path_url not in _ALLOWED_FUTURES_SIGNED_PATHS:
        raise PermissionError(f"Guvenlik engeli: Izin verilmeyen endpoint ({path_url})")
    if not BINANCE_KEYS_CONFIGURED:
        raise RuntimeError("BINANCE_API_KEY veya BINANCE_API_SECRET tanimli degil.")
    base_url = "https://testnet.binancefuture.com" if mode == "TESTNET" else "https://fapi.binance.com"
    ordered = [(str(k), str(v)) for k, v in params.items()]
    ordered.append(("recvWindow", "5000"))
    ordered.append(("timestamp", str(int(time.time() * 1000))))
    qs = urllib.parse.urlencode(ordered)
    sig = hmac.new(BINANCE_API_SECRET.encode("utf-8"), qs.encode("utf-8"), hashlib.sha256).hexdigest()
    url = f"{base_url}{path_url}?{qs}&signature={sig}"
    req = urllib.request.Request(
        url,
        headers={
            "X-MBX-APIKEY": BINANCE_API_KEY,
            "Accept": "application/json",
            "User-Agent": "SpotPumpFuturesBot/2.0",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw_err = e.read().decode("utf-8", errors="replace")
        try:
            err_obj = json.loads(raw_err)
        except Exception:
            err_obj = {}
        raise BinanceFuturesError(e.code, int(err_obj.get("code") or 0), str(err_obj.get("msg") or raw_err or "Rejected"))


def fetch_futures_price(symbol, mode):
    base_url = "https://testnet.binancefuture.com" if mode == "TESTNET" else "https://fapi.binance.com"
    try:
        d = http_get_json(f"{base_url}/fapi/v1/ticker/price?symbol={urllib.parse.quote(symbol)}", timeout=4)
        p = float(d.get("price") or 0)
        if p > 0:
            return p
    except Exception:
        pass
    with state_lock:
        tk = tickers.get(symbol)
        return float(tk["price"]) if tk else 0.0


def refresh_futures_contracts():
    try:
        info = http_get_json("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=12)
        parsed = {}
        for s in info.get("symbols", []):
            sym = s.get("symbol")
            if sym and s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT" and s.get("contractType") == "PERPETUAL":
                step_size = "0.001"
                min_qty = 0.0
                tick_size = "0.0001"
                min_notional = 5.0
                for flt in s.get("filters", []):
                    ft = flt.get("filterType")
                    if ft == "LOT_SIZE":
                        step_size = flt.get("stepSize") or step_size
                        min_qty = float(flt.get("minQty") or 0)
                    elif ft == "PRICE_FILTER" and flt.get("tickSize"):
                        tick_size = flt["tickSize"]
                    elif ft == "MIN_NOTIONAL" and flt.get("notional"):
                        min_notional = float(flt.get("notional") or 5.0)
                parsed[sym] = {
                    "stepSize": step_size,
                    "minQty": min_qty,
                    "tickSize": tick_size,
                    "minNotional": min_notional,
                }
        if len(parsed) > 50:
            with state_lock:
                futures_filters.clear()
                futures_filters.update(parsed)
    except Exception:
        pass


def refresh_active_spot_symbols():
    try:
        info = binance_rest_get("/api/v3/exchangeInfo", timeout=12)
        nxt = {
            s["symbol"]
            for s in info.get("symbols", [])
            if s.get("symbol") and s.get("status") == "TRADING" and s.get("isSpotTradingAllowed") is not False
        }
        if len(nxt) > 50:
            with state_lock:
                active_spot_symbols.clear()
                active_spot_symbols.update(nxt)
    except Exception:
        pass


def _ws_send_frame(sock, data, opcode=0x1):
    mask = os.urandom(4)
    header = bytearray([0x80 | opcode])
    n = len(data)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", n))
    header.extend(mask)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(bytes(header) + masked)


def _ws_recv_exact(sock, buf, n):
    while len(buf) < n:
        chunk = sock.recv(max(4096, n - len(buf)))
        if not chunk:
            raise EOFError("WS EOF")
        buf.extend(chunk)
    out = bytes(buf[:n])
    del buf[:n]
    return out


def _ws_read_frame(sock, buf):
    b1, b2 = _ws_recv_exact(sock, buf, 2)
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack("!H", _ws_recv_exact(sock, buf, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _ws_recv_exact(sock, buf, 8))[0]
    mask = _ws_recv_exact(sock, buf, 4) if masked else None
    payload = _ws_recv_exact(sock, buf, length)
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def ws_api_request(method, params, timeout=9):
    host = "ws-api.binance.com"
    path = "/ws-api/v3"
    ctx = ssl.create_default_context()
    sock = socket.create_connection((host, 443), timeout=timeout)
    ssock = ctx.wrap_socket(sock, server_hostname=host)
    try:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        ssock.sendall(handshake.encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = ssock.recv(4096)
            if not chunk:
                raise RuntimeError("WS-API handshake closed")
            buf += chunk
        if b"101 " not in buf.split(b"\r\n", 1)[0]:
            raise RuntimeError("WS-API handshake rejected")
        req_id = f"r_{int(time.time() * 1000)}"
        payload = json.dumps({"id": req_id, "method": method, "params": params}).encode("utf-8")
        _ws_send_frame(ssock, payload)

        deadline = time.time() + timeout
        extra = buf.split(b"\r\n\r\n", 1)[1]
        frame_buf = bytearray(extra)
        while time.time() < deadline:
            opcode, msg_bytes = _ws_read_frame(ssock, frame_buf)
            if opcode == 0x8:
                break
            if opcode == 0x9:
                _ws_send_frame(ssock, msg_bytes, opcode=0xA)
                continue
            if opcode in (0x1, 0x2):
                obj = json.loads(msg_bytes.decode("utf-8"))
                if obj.get("id") == req_id:
                    if obj.get("status") != 200:
                        raise RuntimeError(f"WS-API error {obj.get('status')}")
                    return obj.get("result")
        raise TimeoutError("WS-API timeout")
    finally:
        try:
            ssock.close()
        except Exception:
            pass


def _parse_binance_klines(raw):
    return [
        {
            "openTime": int(k[0]) // 1000,
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "quoteVol": float(k[7]) if len(k) > 7 else float(k[5]) * float(k[4]),
        }
        for k in raw
    ]


def fetch_klines(sym, interval="1m", limit=45):
    try:
        raw = binance_rest_get(
            f"/api/v3/klines?symbol={urllib.parse.quote(sym)}&interval={interval}&limit={limit}",
            timeout=7,
        )
        if isinstance(raw, list) and raw:
            return _parse_binance_klines(raw)
    except Exception:
        pass
    try:
        raw = ws_api_request("klines", {"symbol": sym, "interval": interval, "limit": limit}, timeout=7)
        if isinstance(raw, list) and raw:
            return _parse_binance_klines(raw)
    except Exception:
        pass
    if interval in ("1m", "15m", "1d", "1M"):
        bybit_tf = {"1m": "1", "15m": "15", "1d": "D", "1M": "M"}[interval]
        try:
            url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={urllib.parse.quote(sym)}&interval={bybit_tf}&limit={limit}"
            d = http_get_json(url, timeout=6)
            rows = d.get("result", {}).get("list", [])
            if rows:
                return [
                    {
                        "openTime": int(r[0]) // 1000,
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": float(r[5]),
                        "quoteVol": float(r[6]) if len(r) > 6 else float(r[5]) * float(r[4]),
                    }
                    for r in reversed(rows)
                ]
        except Exception:
            pass
    return []


# ==============================================================================
# SIFIR BAGIMLILIKLI PNG OLUSTURUCU (MUM GRAFIGI + TOP 24 SAAT LIDERLIK KARTI)
# ==============================================================================
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
    "!": [0x00, 0x00, 0x5F, 0x00, 0x00], "*": [0x14, 0x08, 0x3E, 0x08, 0x14],
    "=": [0x14, 0x14, 0x14, 0x14, 0x14], " ": [0x00, 0x00, 0x00, 0x00, 0x00],
    "|": [0x00, 0x00, 0x7F, 0x00, 0x00],
}


def _encode_rgb_png(W, H, buf):
    raw_rows = bytearray()
    stride = W * 3
    for y in range(H):
        raw_rows.append(0)
        raw_rows.extend(buf[y * stride:(y + 1) * stride])
    compressed = zlib.compress(bytes(raw_rows), 6)

    def png_chunk(tag, payload):
        return struct.pack("!I", len(payload)) + tag + payload + struct.pack("!I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    ihdr = struct.pack("!IIBBBBB", W, H, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", ihdr) + png_chunk(b"IDAT", compressed) + png_chunk(b"IEND", b"")


def render_pump_png(symbol, tf_label, candles, live_price, start_price, badge="SPOT PUMP"):
    W, H = 860, 440
    BG = (9, 13, 22)
    GRID = (28, 38, 56)
    BULL = (16, 185, 129)
    BEAR = (244, 63, 94)
    TEXT = (226, 232, 240)
    MUTED = (148, 163, 184)
    START_COL = (245, 158, 11)
    LIVE_COL = (56, 189, 248)

    bars = update_live_candle(candles[-75:], live_price)
    if not bars:
        return b""

    buf = bytearray(bytes(BG) * (W * H))

    def set_px(x, y, col):
        if 0 <= x < W and 0 <= y < H:
            i = (y * W + x) * 3
            buf[i] = col[0]
            buf[i + 1] = col[1]
            buf[i + 2] = col[2]

    def fill_rect(x0, y0, x1, y1, col):
        x0, x1 = max(0, min(x0, x1)), min(W - 1, max(x0, x1))
        y0, y1 = max(0, min(y0, y1)), min(H - 1, max(y0, y1))
        row_bytes = bytes(col) * (x1 - x0 + 1)
        for y in range(y0, y1 + 1):
            start = (y * W + x0) * 3
            buf[start:start + len(row_bytes)] = row_bytes

    def draw_line(x0, y0, x1, y1, col, dashed=False):
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        step_n = 0
        while True:
            if not dashed or (step_n % 8) < 5:
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
            step_n += 1

    def draw_text(x, y, text, col, scale=1):
        cx = x
        for ch in text.upper():
            glyph = FONT_5X7.get(ch, FONT_5X7[" "])
            for gx in range(5):
                bits = glyph[gx]
                for gy in range(7):
                    if bits & (1 << gy):
                        if scale == 1:
                            set_px(cx + gx, y + gy, col)
                        else:
                            fill_rect(cx + gx * scale, y + gy * scale, cx + gx * scale + scale - 1, y + gy * scale + scale - 1, col)
            cx += 6 * scale

    pad_l, pad_r, pad_t, pad_b = 18, 118, 42, 24
    vol_h = 62
    gap = 12
    plot_w = W - pad_l - pad_r
    price_h = H - pad_t - pad_b - vol_h - gap

    all_p = [b["high"] for b in bars] + [b["low"] for b in bars]
    if live_price and live_price > 0:
        all_p.append(live_price)
    if start_price and start_price > 0:
        all_p.append(start_price)

    lo_p = min(all_p) * 0.994
    hi_p = max(all_p) * 1.006
    rng_p = max(hi_p - lo_p, lo_p * 0.005)
    max_vol = max([b["volume"] for b in bars] + [1.0])

    def y_of(p):
        return int(pad_t + ((hi_p - p) / rng_p) * price_h)

    draw_text(pad_l, 14, f"{symbol} | {tf_label} | {badge} | ANLIK ${fmt_price(live_price)}", TEXT, scale=2)

    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = int(pad_t + price_h * frac)
        draw_line(pad_l, gy, pad_l + plot_w, gy, GRID, dashed=True)
        pval = hi_p - rng_p * frac
        draw_text(pad_l + plot_w + 8, gy - 3, fmt_price(pval), MUTED, scale=1)

    if start_price and start_price > 0:
        sy = y_of(start_price)
        if pad_t <= sy <= pad_t + price_h:
            draw_line(pad_l, sy, pad_l + plot_w, sy, START_COL, dashed=True)
            draw_text(pad_l + plot_w + 8, sy - 3, f"BAS {fmt_price(start_price)}", START_COL, scale=1)

    n = len(bars)
    step = plot_w / max(1, n)
    bw = max(2, int(step * 0.62))
    vol_top = pad_t + price_h + gap
    for idx, b in enumerate(bars):
        cx = int(pad_l + (idx + 0.5) * step)
        col = BULL if b["close"] >= b["open"] else BEAR
        yo = y_of(b["open"])
        yc = y_of(b["close"])
        yh = y_of(b["high"])
        yl = y_of(b["low"])
        draw_line(cx, yh, cx, yl, col)
        fill_rect(cx - bw // 2, min(yo, yc), cx + bw // 2, max(min(yo, yc) + 1, max(yo, yc)), col)
        vh = max(1, int((b["volume"] / max_vol) * vol_h))
        fill_rect(cx - bw // 2, vol_top + vol_h - vh, cx + bw // 2, vol_top + vol_h, (col[0] // 2, col[1] // 2, col[2] // 2))

    if live_price and live_price > 0:
        ly = y_of(live_price)
        draw_line(pad_l, ly, pad_l + plot_w, ly, LIVE_COL, dashed=True)
        fill_rect(pad_l + plot_w + 4, ly - 7, W - 4, ly + 7, (2, 132, 199))
        draw_text(pad_l + plot_w + 8, ly - 3, fmt_price(live_price), (248, 250, 252), scale=1)

    return _encode_rgb_png(W, H, buf)


def render_top24_png(ranked, total_count, hit2_pct):
    """TOP 24 SAAT liderlik karti PNG (IMG_3145 tasarimiyla birebir)."""
    rows = ranked[:5]
    if not rows:
        return b""
    W, H = 860, 700
    BG = (11, 15, 35)
    CARD = (16, 21, 45)
    BORDER = (45, 54, 99)
    ROW1_BG = (23, 29, 61)
    ROW1_BD = (99, 102, 241)
    ROW_BG = (14, 19, 41)
    DIVIDER = (30, 38, 74)
    TEXT = (255, 255, 255)
    MUTED = (148, 163, 184)
    GREEN = (52, 211, 153)
    GOLD = (251, 191, 36)

    buf = bytearray(bytes(BG) * (W * H))

    def set_px(x, y, col):
        if 0 <= x < W and 0 <= y < H:
            i = (y * W + x) * 3
            buf[i] = col[0]
            buf[i + 1] = col[1]
            buf[i + 2] = col[2]

    def fill_rect(x0, y0, x1, y1, col):
        x0, x1 = max(0, min(x0, x1)), min(W - 1, max(x0, x1))
        y0, y1 = max(0, min(y0, y1)), min(H - 1, max(y0, y1))
        row_bytes = bytes(col) * (x1 - x0 + 1)
        for y in range(y0, y1 + 1):
            s = (y * W + x0) * 3
            buf[s:s + len(row_bytes)] = row_bytes

    def draw_box(x0, y0, x1, y1, fill, border):
        fill_rect(x0, y0, x1, y1, fill)
        fill_rect(x0, y0, x1, y0 + 1, border)
        fill_rect(x0, y1 - 1, x1, y1, border)
        fill_rect(x0, y0, x0 + 1, y1, border)
        fill_rect(x1 - 1, y0, x1, y1, border)

    def fill_circle(cx, cy, r, col):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx * dx + dy * dy <= r * r:
                    set_px(cx + dx, cy + dy, col)

    def draw_text(x, y, text, col, scale=1):
        cx = x
        for ch in text.upper():
            glyph = FONT_5X7.get(ch, FONT_5X7[" "])
            for gx in range(5):
                bits = glyph[gx]
                for gy in range(7):
                    if bits & (1 << gy):
                        if scale == 1:
                            set_px(cx + gx, y + gy, col)
                        else:
                            fill_rect(cx + gx * scale, y + gy * scale, cx + (gx + 1) * scale - 1, y + (gy + 1) * scale - 1, col)
            cx += 6 * scale

    draw_box(24, 20, W - 24, H - 20, CARD, BORDER)
    fill_rect(54, 46, 98, 90, (99, 102, 241))
    draw_text(66, 61, "SF", TEXT, scale=2)
    draw_text(114, 48, "SPOT AKIS TERMINALI", (248, 250, 252), scale=2)
    draw_text(114, 72, "CANLI TARAYICI | FUTURES 3X", MUTED, scale=1)
    draw_box(W - 220, 48, W - 52, 86, (26, 31, 68), (79, 70, 229))
    draw_text(W - 196, 61, "TOP * 24 SAAT", (199, 210, 254), scale=2)

    draw_text(54, 120, "24 SAATIN EN IYI HAREKETLERI", TEXT, scale=2)
    draw_text(54, 148, "SINYAL YONUNDEKI MAKSIMUM YUKSELIS", MUTED, scale=1)

    badge_cols = (
        (236, 72, 153),
        (249, 115, 22),
        (6, 182, 212),
        (139, 92, 246),
        (16, 185, 129),
    )

    for idx, s in enumerate(rows):
        ry0 = 180 + idx * 80
        ry1 = ry0 + 68
        if idx == 0:
            draw_box(48, ry0, W - 48, ry1, ROW1_BG, ROW1_BD)
        else:
            fill_rect(48, ry0, W - 48, ry1, ROW_BG)
            fill_rect(48, ry1, W - 48, ry1, DIVIDER)
        draw_text(66, ry0 + 25, str(idx + 1), GOLD if idx == 0 else MUTED, scale=2)
        fill_circle(122, ry0 + 34, 18, badge_cols[idx % len(badge_cols)])
        draw_text(111, ry0 + 27, s["base"][:2], TEXT, scale=2)

        draw_text(158, ry0 + 16, s["base"], TEXT, scale=2)
        dur = fmt_peak_dur(s["created_at"], s.get("peak_at", s["created_at"]), ascii_only=True)
        draw_text(158, ry0 + 42, f"PIK {dur} ICINDE", MUTED, scale=1)

        pk = s.get("peak_pct", 0.0)
        pct_txt = f"+{pk:.0f}%" if pk >= 10 else f"+{pk:.1f}%"
        sc = 3 if idx == 0 else 2
        tw = len(pct_txt) * 6 * sc
        draw_text(W - 68 - tw, ry0 + (22 if idx == 0 else 26), pct_txt, GREEN, scale=sc)

    draw_box(48, 594, W - 48, 646, (19, 25, 54), (39, 49, 90))
    draw_text(68, 608, "SPOT AKIS TERMINALI  |  CANLI SINYAL & FUTURES 3X", (226, 232, 240), scale=1)
    draw_text(68, 626, "PUMP & KIRILIM OTOMATIK +5% TP / -5% SL", (129, 140, 248), scale=1)
    draw_text(54, 658, f"{total_count} SINYAL TAKIPTE  |  %{hit2_pct} +2% ULASTI  |  HAREKET != KAR GARANTISI", (100, 116, 139), scale=1)

    return _encode_rgb_png(W, H, buf)


# ==============================================================================
# TELEGRAM GONDERICILER (MESAJ & FOTOGRAF)
# ==============================================================================
def telegram_send_message(text):
    if not BOT_TOKEN:
        print(f"[SINYAL/MESAJ]\n{text}\n")
        return
    try:
        body = json.dumps({
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=12).read()
    except Exception as e:
        print(f"[TG sendMessage hata] {e}")


def telegram_send_photo(png_bytes, caption):
    if len(caption) > 1000:
        caption = caption[:997] + "..."
    if not BOT_TOKEN:
        print(f"[SINYAL/GORSEL {len(png_bytes)}B]\n{caption}\n")
        return
    if not png_bytes:
        telegram_send_message(caption)
        return
    try:
        boundary = "----SpotPumpBoundary7e8a9b"
        parts = []
        for k, v in (("chat_id", CHAT_ID), ("caption", caption), ("parse_mode", "HTML")):
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8")
            )
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"chart.png\"\r\nContent-Type: image/png\r\n\r\n".encode("utf-8")
            + png_bytes
            + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print(f"[TG sendPhoto hata] {e}")
        telegram_send_message(caption)


def record_signal_and_notify(engine, symbol, base, direction, entry_price, caption, png_bytes=b""):
    now = time.time()
    with state_lock:
        signals_log.append({
            "id": f"{engine}-{symbol}-{int(now)}",
            "engine": engine,
            "symbol": symbol,
            "base": base,
            "direction": direction,
            "entry": entry_price,
            "current": entry_price,
            "peak_price": entry_price,
            "peak_pct": 0.0,
            "created_at": now,
            "peak_at": now,
        })
        if len(signals_log) > 300:
            del signals_log[:-300]
    save_state()
    if png_bytes:
        telegram_send_photo(png_bytes, caption)
    else:
        telegram_send_message(caption)


# ==============================================================================
# FUTURES 3X IZOLE OTOMATIK AL-SAT MOTORU (+%5 TP VE -%5 STOP LOSS)
# ==============================================================================
def should_auto_trade_signal(engine, quote):
    # SADECE PUMP (0-7 dk) ve PUMP15M (15 dk) USDT sinyallerinde Futures 3x LONG acar!
    # ATL ve ATH sinyalleri KESINLIKLE islem acmaz.
    if not auto_trade["enabled"] or quote != "USDT":
        return False
    return engine in ("PUMP", "PUMP15M")


def maybe_execute_futures_buy(engine, engine_label, symbol, base, quote, ref_price):
    if not should_auto_trade_signal(engine, quote):
        return
    with state_lock:
        if symbol in trade_in_flight:
            return
        open_trades = [t for t in trades_log if t.get("status") == "OPEN"]
        if len(open_trades) >= auto_trade["max_open_trades"]:
            return
        if any(t.get("symbol") == symbol or t.get("base") == base for t in open_trades):
            return
        flt = futures_filters.get(symbol)
        if not flt:
            return
        mode = auto_trade["mode"]
        leverage = auto_trade["leverage"]
        margin_usdt = auto_trade["trade_usdt"]
        tp_pct = auto_trade["tp_pct"]
        sl_pct = auto_trade["sl_pct"]
        if mode == "PAPER" and auto_trade["paper_balance_usdt"] < margin_usdt:
            return
        if mode in ("TESTNET", "LIVE") and not BINANCE_KEYS_CONFIGURED:
            return
        trade_in_flight.add(symbol)

    try:
        now = time.time()
        notional_usdt = margin_usdt * leverage
        live_ref = fetch_futures_price(symbol, mode) or ref_price
        if live_ref <= 0:
            return

        if mode == "PAPER":
            qty = floor_to_step(notional_usdt / live_ref, flt["stepSize"])
            if qty <= 0 or qty < flt["minQty"]:
                return
            actual_notional = qty * live_ref
            actual_margin = round(actual_notional / leverage, 2)
            tp_str = format_by_step(live_ref * (1.0 + tp_pct / 100.0), flt["tickSize"])
            sl_str = format_by_step(live_ref * (1.0 - sl_pct / 100.0), flt["tickSize"])
            target_price = float(tp_str) if tp_str else live_ref * (1.0 + tp_pct / 100.0)
            stop_price = float(sl_str) if sl_str else live_ref * (1.0 - sl_pct / 100.0)
            fee_open = actual_notional * 0.0005

            tr = {
                "id": f"TR-PAPER-{symbol}-{int(now * 1000)}",
                "symbol": symbol,
                "base": base,
                "engine": engine,
                "mode": "PAPER",
                "leverage": leverage,
                "status": "OPEN",
                "entry_price": live_ref,
                "target_price": target_price,
                "stop_price": stop_price,
                "current_price": live_ref,
                "quantity": qty,
                "margin_usdt": actual_margin,
                "notional_usdt": round(actual_notional, 2),
                "pnl_usdt": round(-fee_open * 2, 2),
                "pnl_pct": 0.0,
                "roe_pct": round(((-fee_open * 2) / max(actual_margin, 0.01)) * 100.0, 2),
                "opened_at": now,
            }
            with state_lock:
                auto_trade["paper_balance_usdt"] = round(max(0.0, auto_trade["paper_balance_usdt"] - actual_margin), 4)
                trades_log.insert(0, tr)
                del trades_log[200:]
            save_state()
            telegram_send_message(
                f"🟢 <b>FUTURES {leverage}x IZOLE LONG ACILDI (PAPER · {html.escape(engine_label)})</b>\n"
                f"• Parite: <b>#{html.escape(symbol)}</b> · Teminat: ${actual_margin:.2f} ({leverage}x = ${tr['notional_usdt']:.2f})\n"
                f"• Giris: ${fmt_price(live_ref)}\n"
                f"🎯 +%{tp_pct:g} Kar Al: <b>${fmt_price(target_price)}</b> | 🛑 -%{sl_pct:g} Stop: <b>${fmt_price(stop_price)}</b>"
            )
            return

        # LIVE / TESTNET: 1) ISOLATED margin, 2) 3x kaldirac, 3) MARKET BUY, 4) LIMIT TP + STOP_MARKET SL
        try:
            binance_futures_signed("POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": "ISOLATED"}, mode)
        except BinanceFuturesError as m_err:
            if m_err.code not in (-4046, -4048):
                print(f"[MarginType uyari {symbol}] {m_err}")

        binance_futures_signed("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage}, mode)

        qty = floor_to_step(notional_usdt / live_ref, flt["stepSize"])
        if qty <= 0 or qty < flt["minQty"] or qty * live_ref < flt["minNotional"]:
            return
        qty_str = format_by_step(qty, flt["stepSize"])

        hedge_mode = False
        try:
            buy_res = binance_futures_signed(
                "POST", "/fapi/v1/order",
                {"symbol": symbol, "side": "BUY", "type": "MARKET", "quantity": qty_str},
                mode,
            )
        except BinanceFuturesError as b_err:
            if b_err.code == -4061:
                hedge_mode = True
                buy_res = binance_futures_signed(
                    "POST", "/fapi/v1/order",
                    {"symbol": symbol, "side": "BUY", "positionSide": "LONG", "type": "MARKET", "quantity": qty_str},
                    mode,
                )
            else:
                raise

        exec_qty = float(buy_res.get("executedQty") or qty)
        avg_price = float(buy_res.get("avgPrice") or 0)
        if avg_price <= 0 and buy_res.get("orderId") is not None:
            time.sleep(0.35)
            try:
                chk = binance_futures_signed("GET", "/fapi/v1/order", {"symbol": symbol, "orderId": buy_res["orderId"]}, mode)
                exec_qty = float(chk.get("executedQty") or exec_qty)
                avg_price = float(chk.get("avgPrice") or 0)
            except Exception:
                pass

        entry_price = avg_price if avg_price > 0 else live_ref
        final_qty = floor_to_step(exec_qty, flt["stepSize"]) or qty
        final_qty_str = format_by_step(final_qty, flt["stepSize"])
        tp_price_str = format_by_step(entry_price * (1.0 + tp_pct / 100.0), flt["tickSize"])
        sl_price_str = format_by_step(entry_price * (1.0 - sl_pct / 100.0), flt["tickSize"])
        target_price = float(tp_price_str) if tp_price_str else entry_price * (1.0 + tp_pct / 100.0)
        stop_price = float(sl_price_str) if sl_price_str else entry_price * (1.0 - sl_pct / 100.0)

        tp_params = {
            "symbol": symbol, "side": "SELL", "type": "LIMIT",
            "timeInForce": "GTC", "quantity": final_qty_str, "price": tp_price_str,
        }
        if hedge_mode:
            tp_params["positionSide"] = "LONG"
        else:
            tp_params["reduceOnly"] = "true"
        tp_res = binance_futures_signed("POST", "/fapi/v1/order", tp_params, mode)
        tp_order_id = str(tp_res["orderId"]) if tp_res.get("orderId") is not None else None

        sl_order_id = None
        try:
            sl_params = {
                "symbol": symbol, "side": "SELL", "type": "STOP_MARKET",
                "stopPrice": sl_price_str, "quantity": final_qty_str,
                "workingType": "MARK_PRICE", "priceProtect": "TRUE",
            }
            if hedge_mode:
                sl_params["positionSide"] = "LONG"
            else:
                sl_params["reduceOnly"] = "true"
            sl_res = binance_futures_signed("POST", "/fapi/v1/order", sl_params, mode)
            if sl_res.get("orderId") is not None:
                sl_order_id = str(sl_res["orderId"])
        except BinanceFuturesError as sl_err:
            if sl_err.code == -2021:
                if tp_order_id:
                    try:
                        binance_futures_signed("DELETE", "/fapi/v1/order", {"symbol": symbol, "orderId": tp_order_id}, mode)
                    except Exception:
                        pass
                emerg = {"symbol": symbol, "side": "SELL", "type": "MARKET", "quantity": final_qty_str}
                if hedge_mode:
                    emerg["positionSide"] = "LONG"
                else:
                    emerg["reduceOnly"] = "true"
                emerg_res = {}
                try:
                    emerg_res = binance_futures_signed("POST", "/fapi/v1/order", emerg, mode)
                except Exception:
                    pass
                exit_p = float(emerg_res.get("avgPrice") or 0) or fetch_futures_price(symbol, mode) or stop_price
                actual_notional = final_qty * entry_price
                actual_margin = round(actual_notional / leverage, 2)
                net_pnl = final_qty * (exit_p - entry_price) - final_qty * (entry_price + exit_p) * 0.0005
                closed_tr = {
                    "id": f"TR-{mode}-{symbol}-{int(now * 1000)}",
                    "symbol": symbol, "base": base, "engine": engine, "mode": mode, "leverage": leverage,
                    "status": "CLOSED", "close_reason": "SL",
                    "entry_price": entry_price, "target_price": target_price, "stop_price": stop_price,
                    "current_price": exit_p, "exit_price": exit_p, "quantity": final_qty,
                    "margin_usdt": actual_margin, "notional_usdt": round(actual_notional, 2),
                    "pnl_usdt": round(net_pnl, 2),
                    "pnl_pct": round(((exit_p - entry_price) / entry_price) * 100.0, 2),
                    "roe_pct": round((net_pnl / max(actual_margin, 0.01)) * 100.0, 2),
                    "buy_order_id": str(buy_res["orderId"]) if buy_res.get("orderId") is not None else None,
                    "tp_order_id": tp_order_id, "opened_at": now, "closed_at": time.time(),
                }
                with state_lock:
                    trades_log.insert(0, closed_tr)
                    del trades_log[200:]
                save_state()
                telegram_send_message(
                    f"🛑 <b>FUTURES {leverage}x ANINDA STOP (-%{sl_pct:g} Altinda Acildi · #{html.escape(symbol)})</b>\n"
                    f"• Giris: ${fmt_price(entry_price)} ➡️ Cikis: ${fmt_price(exit_p)}\n"
                    f"• Net K/Z: <b>{'+' if closed_tr['pnl_usdt'] >= 0 else ''}${closed_tr['pnl_usdt']:.2f}</b>"
                )
                return
            print(f"[SL emir uyari {symbol}] {sl_err}")

        actual_notional = final_qty * entry_price
        actual_margin = round(actual_notional / leverage, 2)
        tr = {
            "id": f"TR-{mode}-{symbol}-{int(now * 1000)}",
            "symbol": symbol, "base": base, "engine": engine, "mode": mode, "leverage": leverage,
            "status": "OPEN", "entry_price": entry_price, "target_price": target_price, "stop_price": stop_price,
            "current_price": entry_price, "quantity": final_qty,
            "margin_usdt": actual_margin, "notional_usdt": round(actual_notional, 2),
            "pnl_usdt": 0.0, "pnl_pct": 0.0, "roe_pct": 0.0,
            "buy_order_id": str(buy_res["orderId"]) if buy_res.get("orderId") is not None else None,
            "tp_order_id": tp_order_id, "sl_order_id": sl_order_id, "opened_at": now,
        }
        with state_lock:
            trades_log.insert(0, tr)
            del trades_log[200:]
        save_state()
        telegram_send_message(
            f"🟢 <b>FUTURES {leverage}x IZOLE LONG ACILDI ({mode} · {html.escape(engine_label)})</b>\n"
            f"• Parite: <b>#{html.escape(symbol)}</b> · Miktar: {final_qty_str} ({leverage}x = ${tr['notional_usdt']:.2f})\n"
            f"• Giris: ${fmt_price(entry_price)}\n"
            f"🎯 +%{tp_pct:g} Kar Al: <b>${fmt_price(target_price)}</b> | 🛑 -%{sl_pct:g} Stop: <b>${fmt_price(stop_price)}</b>"
            + ("" if sl_order_id else " (Yazilim Takibi)")
        )
    except Exception as e:
        print(f"[Futures oto alim hata {symbol}] {e}")
        telegram_send_message(f"⚠️ <b>Futures Oto Alim Basarisiz (#{html.escape(symbol)})</b>\n{html.escape(str(e))}")
    finally:
        with state_lock:
            trade_in_flight.discard(symbol)


def close_futures_trade(tr, reason, explicit_exit_price=None):
    tid = tr["id"]
    with state_lock:
        if tr.get("status") != "OPEN" or tid in trade_in_flight:
            return False
        trade_in_flight.add(tid)
        sym = tr["symbol"]
        mode = tr["mode"]
        tk = tickers.get(sym)
        live_price = explicit_exit_price or (tk["price"] if tk else 0.0) or tr.get("current_price") or tr["entry_price"]
        flt = futures_filters.get(sym, {"stepSize": "0.0001"})
        tp_oid = tr.get("tp_order_id")
        sl_oid = tr.get("sl_order_id")
        qty = tr["quantity"]
        entry = tr["entry_price"]
        margin = tr["margin_usdt"]
        lev = tr["leverage"]

    try:
        if mode == "PAPER":
            gross = qty * (live_price - entry)
            fees = qty * (entry + live_price) * 0.0005
            net_pnl = gross - fees
            with state_lock:
                auto_trade["paper_balance_usdt"] = round(max(0.0, auto_trade["paper_balance_usdt"] + margin + net_pnl), 4)
                tr["status"] = "CLOSED"
                tr["close_reason"] = reason
                tr["exit_price"] = live_price
                tr["current_price"] = live_price
                tr["pnl_usdt"] = round(net_pnl, 2)
                tr["pnl_pct"] = round(((live_price - entry) / entry) * 100.0, 2) if entry > 0 else 0.0
                tr["roe_pct"] = round((net_pnl / margin) * 100.0, 2) if margin > 0 else 0.0
                tr["closed_at"] = time.time()
        else:
            if reason != "TP" and tp_oid:
                try:
                    binance_futures_signed("DELETE", "/fapi/v1/order", {"symbol": sym, "orderId": tp_oid}, mode)
                except Exception:
                    pass
            if reason != "SL" and sl_oid:
                try:
                    binance_futures_signed("DELETE", "/fapi/v1/order", {"symbol": sym, "orderId": sl_oid}, mode)
                except Exception:
                    pass

            exit_price = live_price
            if reason == "MANUAL" or (reason == "SL" and not sl_oid):
                qty_str = format_by_step(qty, flt["stepSize"])
                try:
                    sell_res = binance_futures_signed(
                        "POST", "/fapi/v1/order",
                        {"symbol": sym, "side": "SELL", "type": "MARKET", "quantity": qty_str, "reduceOnly": "true"},
                        mode,
                    )
                except BinanceFuturesError as s_err:
                    if s_err.code in (-1106, -4061):
                        sell_res = binance_futures_signed(
                            "POST", "/fapi/v1/order",
                            {"symbol": sym, "side": "SELL", "positionSide": "LONG", "type": "MARKET", "quantity": qty_str},
                            mode,
                        )
                    else:
                        raise
                avg_p = float(sell_res.get("avgPrice") or 0)
                if avg_p > 0:
                    exit_price = avg_p

            gross = qty * (exit_price - entry)
            fees = qty * (entry + exit_price) * 0.0005
            net_pnl = gross - fees
            with state_lock:
                tr["status"] = "CLOSED"
                tr["close_reason"] = reason
                tr["exit_price"] = exit_price
                tr["current_price"] = exit_price
                tr["pnl_usdt"] = round(net_pnl, 2)
                tr["pnl_pct"] = round(((exit_price - entry) / entry) * 100.0, 2) if entry > 0 else 0.0
                tr["roe_pct"] = round((net_pnl / margin) * 100.0, 2) if margin > 0 else 0.0
                tr["closed_at"] = time.time()

        save_state()
        icon = "✅" if tr["pnl_usdt"] >= 0 else "🛑"
        lbl = (
            f"+%{auto_trade['tp_pct']:g} KAR AL"
            if reason == "TP"
            else (f"-%{auto_trade['sl_pct']:g} STOP LOSS" if reason == "SL" else "MANUEL KAPATMA")
        )
        telegram_send_message(
            f"{icon} <b>FUTURES {lev}x KAPANDI — #{html.escape(sym)} ({lbl})</b>\n"
            f"• Giris: ${fmt_price(entry)} ➡️ Cikis: ${fmt_price(tr['exit_price'])}\n"
            f"• Net K/Z: <b>{'+' if tr['pnl_usdt'] >= 0 else ''}${tr['pnl_usdt']:.2f}</b> "
            f"(Fiyat: {'+' if tr['pnl_pct'] >= 0 else ''}{tr['pnl_pct']:.2f}% · {lev}x ROE: {'+' if tr['roe_pct'] >= 0 else ''}{tr['roe_pct']:.2f}%)"
        )
        return True
    except Exception as e:
        print(f"[Futures kapatma hata {sym}] {e}")
        return False
    finally:
        with state_lock:
            trade_in_flight.discard(tid)


def evaluate_open_trades_tick():
    to_close = []
    with state_lock:
        for tr in trades_log:
            if tr.get("status") != "OPEN":
                continue
            tk = tickers.get(tr["symbol"])
            if not tk or tk["price"] <= 0:
                continue
            p = tk["price"]
            tr["current_price"] = p
            gross = tr["quantity"] * (p - tr["entry_price"])
            fees = tr["quantity"] * (tr["entry_price"] + p) * 0.0005
            net_pnl = gross - fees
            tr["pnl_usdt"] = round(net_pnl, 2)
            tr["pnl_pct"] = round(((p - tr["entry_price"]) / tr["entry_price"]) * 100.0, 2) if tr["entry_price"] > 0 else 0.0
            tr["roe_pct"] = round((net_pnl / tr["margin_usdt"]) * 100.0, 2) if tr["margin_usdt"] > 0 else 0.0

            if tr["mode"] == "PAPER":
                if p >= tr["target_price"]:
                    to_close.append((tr, "TP", p))
                elif tr.get("stop_price", 0) > 0 and p <= tr["stop_price"]:
                    to_close.append((tr, "SL", p))

    for tr, reason, p in to_close:
        close_futures_trade(tr, reason, p)


def poll_open_binance_trades_loop():
    while True:
        try:
            if BINANCE_KEYS_CONFIGURED:
                with state_lock:
                    real_open = [t for t in trades_log if t.get("status") == "OPEN" and t.get("mode") != "PAPER"]
                for tr in real_open:
                    try:
                        tp_oid = tr.get("tp_order_id")
                        sl_oid = tr.get("sl_order_id")
                        if tp_oid:
                            ord_tp = binance_futures_signed("GET", "/fapi/v1/order", {"symbol": tr["symbol"], "orderId": tp_oid}, tr["mode"])
                            if ord_tp.get("status") == "FILLED":
                                fp = float(ord_tp.get("avgPrice") or tr["target_price"])
                                close_futures_trade(tr, "TP", fp)
                                continue
                        if sl_oid:
                            ord_sl = binance_futures_signed("GET", "/fapi/v1/order", {"symbol": tr["symbol"], "orderId": sl_oid}, tr["mode"])
                            if ord_sl.get("status") == "FILLED":
                                fp = float(ord_sl.get("avgPrice") or tr["stop_price"])
                                close_futures_trade(tr, "SL", fp)
                                continue
                        elif tr.get("stop_price", 0) > 0 and tr.get("current_price", 0) <= tr["stop_price"]:
                            close_futures_trade(tr, "SL", tr["current_price"])
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(8)


# ==============================================================================
# MOTOR 1: 0-7 DK SPOT ANLIK PUMP (TEKIL COIN KILIDI + FUTURES 3X)
# ==============================================================================
def is_pump_locked(lock_bucket, base, now):
    rec = locks[lock_bucket].get(base)
    if not rec:
        return False
    if PUMP_REPEAT_HOURS <= 0:
        return True
    return (now - rec["locked_at"]) < PUMP_REPEAT_HOURS * 3600


def evaluate_pump(sym, price, vol24h, now):
    base, quote = extract_base_quote(sym)
    if not base:
        return
    min_vol = 150_000 if quote == "USDT" else (3.0 if quote == "BTC" else 250.0)
    th_pct = 4.5 if quote == "USDT" else 4.0
    if price <= 0 or vol24h < min_vol:
        return

    with state_lock:
        hist = price_history.setdefault(sym, [])
        hist.append((now, price))
        cutoff = now - 420
        while hist and hist[0][0] < cutoff:
            hist.pop(0)
        if len(hist) < 2:
            return
        min_pt = min(hist, key=lambda x: x[1])
        pct = ((price - min_pt[1]) / min_pt[1]) * 100.0 if min_pt[1] > 0 else 0.0
        if pct < th_pct or is_pump_locked("pump", base, now):
            return
        locks["pump"][base] = {"symbol": sym, "locked_at": now}
        hist_copy = list(hist)

    threading.Thread(
        target=_verify_and_emit_pump,
        args=(sym, base, quote, price, vol24h, min_pt, hist_copy),
        daemon=True,
    ).start()


def _verify_and_emit_pump(sym, base, quote, approx_cur, approx_vol, min_pt, hist_copy):
    try:
        live_cur, live_vol = approx_cur, approx_vol
        try:
            tk = binance_rest_get(f"/api/v3/ticker/24hr?symbol={urllib.parse.quote(sym)}", timeout=4)
            if isinstance(tk, dict):
                live_cur = float(tk.get("lastPrice") or approx_cur)
                live_vol = float(tk.get("quoteVolume") or approx_vol)
        except Exception:
            pass

        c1m = fetch_klines(sym, "1m", limit=45)
        recent_bars = c1m[-7:] if c1m else []
        bar_min = min((b["low"] for b in recent_bars if b["low"] > 0), default=min_pt[1])
        verified_min = min(min_pt[1], bar_min)
        verified_pct = ((live_cur - verified_min) / verified_min) * 100.0
        th_pct = 4.5 if quote == "USDT" else 4.0
        if verified_pct < th_pct:
            with state_lock:
                locks["pump"].pop(base, None)
            return

        with state_lock:
            price_history.pop(sym, None)

        mins = max(1, round((time.time() - min_pt[0]) / 60))
        vol_str = f"{round(live_vol):,}" if quote == "USDT" else f"{live_vol:.2f}"
        tf_label = "1DK CANLI"
        chart_bars = c1m
        if not chart_bars or len(chart_bars) < 5:
            tf_label = "15SN CANLI"
            buckets = {}
            for ts_p, pr in hist_copy:
                bk = (int(ts_p) // 15) * 15
                if bk not in buckets:
                    buckets[bk] = {"openTime": bk, "open": pr, "high": pr, "low": pr, "close": pr, "volume": 1.0}
                else:
                    b = buckets[bk]
                    b["high"] = max(b["high"], pr)
                    b["low"] = min(b["low"], pr)
                    b["close"] = pr
                    b["volume"] += 1.0
            chart_bars = [buckets[k] for k in sorted(buckets.keys())]

        png = render_pump_png(sym, tf_label, chart_bars, live_cur, verified_min, badge="SPOT PUMP")
        caption = (
            f"⚡ <b>SPOT ANLIK PUMP DEDEKTORU — #{sym}</b>\n\n"
            f"📈 Degisim: <b>+{verified_pct:.2f}%</b> ({mins} dk icinde!)\n"
            f"💰 Baslangic: {fmt_price(verified_min)} ➡️ Anlik: <b>{fmt_price(live_cur)}</b>\n"
            f"💧 24s Parite Hacmi: {vol_str} ({quote})\n"
            f"🔒 Tekil coin kilidi aktif."
        )
        record_signal_and_notify("PUMP", sym, base, "LONG", live_cur, caption, png)
        maybe_execute_futures_buy("PUMP", "0-7 dk Ani Pump", sym, base, quote, live_cur)
    except Exception:
        with state_lock:
            locks["pump"].pop(base, None)


# ==============================================================================
# MOTOR 2, 3, 4: 15 DK KIRILIM (FUTURES 3X) + KESIN ATL / ATH (YALNIZ BILDIRIM)
# ==============================================================================
def structure_scan_loop():
    cursor = 0
    while True:
        try:
            with state_lock:
                usdt_syms = sorted(
                    [t for t in tickers.values() if t["quote"] == "USDT" and t["volume24h"] >= 250_000],
                    key=lambda x: x["volume24h"],
                    reverse=True,
                )
            if not usdt_syms:
                time.sleep(3)
                continue

            batch = []
            for i in range(min(3, len(usdt_syms))):
                batch.append(usdt_syms[(cursor + i) % len(usdt_syms)])
            cursor = (cursor + len(batch)) % len(usdt_syms)

            for tk in batch:
                if time.time() < ban_until_ts:
                    break
                sym = tk["symbol"]
                base = tk["base"]
                now = time.time()

                # --- 15 DK HACIMLI KIRILIM (PUMP15M) -> AYRI KILIT + FUTURES 3X ---
                if not is_pump_locked("pump15m", base, now):
                    c15 = fetch_klines(sym, "15m", limit=24)
                    if len(c15) >= 10:
                        last_bar = c15[-1]
                        prev_bars = c15[-9:-1]
                        avg_qvol = sum(b["quoteVol"] for b in prev_bars) / max(1, len(prev_bars))
                        rvol = (last_bar["quoteVol"] / avg_qvol) if avg_qvol > 0 else 0.0
                        start_15m = last_bar["open"]
                        live_p = max(tk["price"], last_bar["close"])
                        pct_15m = ((live_p - start_15m) / start_15m) * 100.0 if start_15m > 0 else 0.0
                        if pct_15m >= 3.2 and rvol >= 1.4:
                            with state_lock:
                                if not is_pump_locked("pump15m", base, now):
                                    locks["pump15m"][base] = {"symbol": sym, "locked_at": now}
                                    already = False
                                else:
                                    already = True
                            if not already:
                                c1m = fetch_klines(sym, "1m", limit=45)
                                png = render_pump_png(sym, "15DK KIRILIM", c1m or c15, live_p, start_15m, badge="15DK KIRILIM")
                                caption = (
                                    f"🚀 <b>15 DK HACİMLİ KIRILIM — #{sym}</b>\n\n"
                                    f"📈 Degisim: <b>+{pct_15m:.2f}%</b> (15 dk mumu!)\n"
                                    f"💰 Acilis: {fmt_price(start_15m)} ➡️ Anlik: <b>{fmt_price(live_p)}</b>\n"
                                    f"📊 Hacim Patlamasi: <b>{rvol:.1f}x RVOL</b> | 24s Hacim: {round(tk['volume24h']):,} (USDT)\n"
                                    f"🔒 15dk tekil coin kilidi aktif."
                                )
                                record_signal_and_notify("PUMP15M", sym, base, "LONG", live_p, caption, png)
                                maybe_execute_futures_buy("PUMP15M", "15 dk Hacimli Kirilim", sym, base, "USDT", live_p)

                # --- KESIN ATL & ATH TESPITI (DAHA ASAGI/YUKARI KIRILMADIKCA TEKRAR GELMEZ, ISLEM ACMAZ) ---
                monthly = fetch_klines(sym, "1M", limit=120)
                if len(monthly) >= 2:
                    hist_bars = monthly[:-1]
                    cur_month = monthly[-1]
                    prev_atl = min(b["low"] for b in hist_bars if b["low"] > 0)
                    prev_ath = max(b["high"] for b in hist_bars if b["high"] > 0)
                    live_p = tk["price"]
                    chg24 = tk.get("change24h", 0.0)

                    atl_key = f"{sym}:ATL"
                    with state_lock:
                        prev_atl_lock = locks["ath_atl"].get(atl_key)
                    if prev_atl > 0 and live_p < prev_atl * 0.9985 and cur_month["low"] >= live_p * 0.999:
                        if not prev_atl_lock or live_p < prev_atl_lock["level"] * 0.9985:
                            locked_ref = prev_atl_lock["level"] if prev_atl_lock else prev_atl
                            with state_lock:
                                locks["ath_atl"][atl_key] = {"level": live_p, "locked_at": now}
                            msg = (
                                f"🕳 <b>#{sym} ${fmt_price(live_p)} tum zamanlarin en dusugu (ATL)</b>\n"
                                f"📉 Onceki ATL (${fmt_price(locked_ref)}) kesin olarak asagi kirildi · 24s: {chg24:+.2f}%\n"
                                f"🔒 Kilit aktif: ${fmt_price(live_p)} altina dusmedikce tekrar gelmez."
                            )
                            record_signal_and_notify("ATL", sym, base, "SHORT", live_p, msg)

                    ath_key = f"{sym}:ATH"
                    with state_lock:
                        prev_ath_lock = locks["ath_atl"].get(ath_key)
                    if prev_ath > 0 and live_p > prev_ath * 1.0015 and cur_month["high"] <= live_p * 1.001:
                        if not prev_ath_lock or live_p > prev_ath_lock["level"] * 1.0015:
                            locked_ref = prev_ath_lock["level"] if prev_ath_lock else prev_ath
                            with state_lock:
                                locks["ath_atl"][ath_key] = {"level": live_p, "locked_at": now}
                            msg = (
                                f"🏔 <b>#{sym} ${fmt_price(live_p)} tum zamanlarin en yuksegi (ATH)</b>\n"
                                f"📈 Onceki ATH (${fmt_price(locked_ref)}) kesin olarak yukari kirildi · 24s: {chg24:+.2f}%\n"
                                f"🔒 Kilit aktif: ${fmt_price(live_p)} ustune cikmadikce tekrar gelmez."
                            )
                            record_signal_and_notify("ATH", sym, base, "LONG", live_p, msg)

                time.sleep(0.45)
        except Exception:
            pass
        time.sleep(2.5)


# ==============================================================================
# PIK TAKIBI, GUNUN KAZANANI (21:00 TR), TOP 24 SAAT (23:00 TR) & DUYURULAR (60 SN)
# ==============================================================================
def update_signal_peaks():
    changed = False
    now_ts = time.time()
    with state_lock:
        for s in signals_log:
            if s.get("engine") not in ("PUMP", "PUMP15M"):
                continue
            tk = tickers.get(s["symbol"])
            if not tk or tk["price"] <= 0:
                continue
            p = tk["price"]
            s["current"] = p
            pct = ((p - s["entry"]) / s["entry"]) * 100.0
            if pct > s.get("peak_pct", 0.0):
                s["peak_pct"] = round(pct, 2)
                s["peak_price"] = p
                s["peak_at"] = now_ts
                changed = True
    if changed:
        save_state()


def build_victory_report(now_ts=None):
    """Gunun Zaferi -> GUNUN KAZANANI formati. (metin, kazanan_dict) dondurur."""
    now_ts = now_ts or time.time()
    cutoff = now_ts - 86400
    with state_lock:
        recent = [
            s for s in signals_log
            if s["created_at"] >= cutoff and s.get("engine") in ("PUMP", "PUMP15M") and s.get("peak_pct", 0.0) >= 3.0
        ]
    best_by_sym = {}
    for s in recent:
        prev = best_by_sym.get(s["symbol"])
        if not prev or s["peak_pct"] > prev["peak_pct"]:
            best_by_sym[s["symbol"]] = s
    ranked = sorted(best_by_sym.values(), key=lambda x: x["peak_pct"], reverse=True)
    if not ranked:
        return "🏆 <b>GÜNÜN KAZANANI (21:00 TR)</b>\n\nSon 24 saatte +%3 ve üzeri pik yapan Pump/Kırılım sinyali henüz oluşmadı.", None

    top = ranked[0]
    base = top["base"]
    entry = top["entry"]
    peak = top["peak_price"]
    peak_pct = top["peak_pct"]

    lines = [
        f"🏆 <b>GÜNÜN KAZANANI: {html.escape(base)}</b>",
        "",
        "🔥 <b>24 saat içinde en çok yükselen coin!</b>",
        "",
        f"📉 Dip Fiyat: <b>{fmt_price(entry)}</b>",
        f"🚀 Zirve Fiyat: <b>{fmt_price(peak)}</b>",
        f"📈 Maksimum Yükseliş: <b>+{peak_pct:.1f}%</b>",
        "",
        f"💡 <b>Değerlendirme:</b> {html.escape(base)} gün içerisinde muazzam bir hacim ve fiyat patlaması yaşadı. "
        f"Pump Dedektör verilerimize göre günün en kârlı hareketi bu coinde gerçekleşti.",
    ]
    return "\n".join(lines), top


def build_top24_report_and_png(now_ts=None):
    now_ts = now_ts or time.time()
    cutoff = now_ts - 86400
    with state_lock:
        recent = [s for s in signals_log if s["created_at"] >= cutoff and s.get("engine") in ("PUMP", "PUMP15M")]
    best_by_sym = {}
    for s in recent:
        prev = best_by_sym.get(s["symbol"])
        if not prev or s["peak_pct"] > prev["peak_pct"]:
            best_by_sym[s["symbol"]] = s
    ranked = sorted(best_by_sym.values(), key=lambda x: x["peak_pct"], reverse=True)
    if not ranked:
        return "🔥 <b>24 saatin TOP hareketleri</b>\n\nSon 24 saatte kaydedilen Pump / Kirilim sinyali henuz olusmadi.", b""
    hit2_cnt = sum(1 for s in recent if s.get("peak_pct", 0.0) >= 2.0)
    hit2_pct = round((hit2_cnt / max(1, len(recent))) * 100)
    medals = ("🥇", "🥈", "🥉", "🏅", "⭐️")
    lines = ["🔥 <b>24 saatin TOP hareketleri</b>\n", "sinyal yonundeki maksimum yukselis:\n"]
    for i, s in enumerate(ranked[:5]):
        pk = s.get("peak_pct", 0.0)
        pct_str = f"{pk:.0f}" if pk >= 10 else f"{pk:.1f}"
        dur = fmt_peak_dur(s["created_at"], s.get("peak_at", s["created_at"]), ascii_only=False)
        lines.append(f"{medals[i]} <b>#{s['base']}</b> +{pct_str}% · pik {dur}")
    lines.append(f"\n📊 {len(recent)} sinyal takipte · %{hit2_pct} en az +2% ulasti")
    png = render_top24_png(ranked, len(recent), hit2_pct)
    return "\n".join(lines), png


def translate_binance_title_tr(title):
    tr = title.strip()
    pairs = (
        ("Binance Will List", "Binance Listeliyor:"),
        ("Binance Will Add", "Binance Ekliyor:"),
        ("Binance Will Support", "Binance Destekliyor:"),
        ("Binance Has Completed the", "Binance Tamamladi:"),
        ("Binance Futures Will Launch", "Binance Vadeli Baslatiyor:"),
        ("Token Merge", "Token Birlesmesi (Merge)"),
        ("Contract Swap", "Kontrat Degisimi (Swap)"),
        ("Withdrawals Reopened", "Cekim Islemleri Yeniden Acildi"),
        ("Notice of Removal of Spot Trading Pairs", "Spot Islem Ciftleri Kaldirma (Delist)"),
        ("Binance Will Delist", "Binance Delist Ediyor:"),
        ("Network Upgrade", "Ag Guncellemesi"),
    )
    for eng, tur in pairs:
        tr = tr.replace(eng, tur)
    return tr


def poll_binance_announcements():
    global news_seeded
    catalogs = ((48, "Yeni Listeleme"), (49, "Binance Duyuru"), (161, "Delist / Kaldirma"))
    newly = []
    for cat_id, cat_label in catalogs:
        try:
            url = f"https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query?catalogId={cat_id}&pageNo=1&pageSize=8"
            data = http_get_json(url, timeout=8)
            arts = data.get("data", {}).get("articles", [])
            for art in arts:
                aid = str(art.get("id") or art.get("code") or art.get("title") or "")
                if not aid or aid in news_seen_ids:
                    continue
                news_seen_ids.add(aid)
                title_orig = str(art.get("title") or "").strip()
                code = str(art.get("code") or "")
                link = f"https://www.binance.com/en/support/announcement/detail/{code}" if code else "https://www.binance.com/en/support/announcement"
                newly.append((cat_label, translate_binance_title_tr(title_orig), link))
        except Exception:
            pass
    if news_seeded and newly:
        for cat_label, title_tr, link in newly[:5]:
            telegram_send_message(f"📰 <b>Binance | {cat_label}</b>\n{title_tr}\n{link}")
    news_seeded = True


# ==============================================================================
# TELEGRAM KOMUT DINLEYICI (/durum, /top24, /futures, /al_baslat, /al_durdur, /kapat, /sifirla)
# ==============================================================================
def handle_telegram_command(text):
    parts = text.strip().split()
    if not parts:
        return
    cmd = parts[0].split("@")[0].lower()

    if cmd in ("/start", "/help", "/yardim"):
        telegram_send_message(
            "🤖 <b>Spot Pump & Futures 3x Bot Komutlari</b>\n"
            "• /top24 — TOP 24 Saat PNG Liderlik Karti & madalyali ozet\n"
            "• /durum — Aktif pariteler, kilitler ve Futures 3x ozeti\n"
            "• /futures — Oto Futures ayarlari (+%5 TP / -%5 SL)\n"
            "• /al_baslat — PUMP & PUMP15M oto 3x LONG'u baslat\n"
            "• /al_durdur — Oto al-sat'i durdur (yalniz sinyal)\n"
            "• /pozisyonlar — Acik Futures pozisyonlari ve canli K/Z\n"
            "• /kapat SYMBOL — Ornek: /kapat BTCUSDT\n"
            "• /kilitler — Kilitli coin sayilari\n"
            "• /sifirla — Pump & 15dk kilitlerini temizle"
        )
    elif cmd in ("/top24", "/ozet", "/24s"):
        cap, png = build_top24_report_and_png(time.time())
        if png:
            telegram_send_photo(png, cap)
        else:
            telegram_send_message(cap)
    elif cmd == "/durum":
        with state_lock:
            n_pairs = len(tickers)
            n_fut = len(futures_filters)
            open_cnt = sum(1 for t in trades_log if t.get("status") == "OPEN")
        telegram_send_message(
            f"📡 <b>Bot Durumu (7/24 Aktif)</b>\n"
            f"• WebSocket: {'🟢 Bagli' if ws_connected else '🟡 Yeniden Baglaniyor'} | Pariteler: {n_pairs}\n"
            f"• Kilitler: Pump {len(locks['pump'])} | 15dk {len(locks['pump15m'])} | ATL/ATH {len(locks['ath_atl'])}\n"
            f"• Futures 3x: {'🟢 AKTIF' if auto_trade['enabled'] else '⏸ DURDURULDU'} ({auto_trade['mode']}) | Acik: {open_cnt} | Kontrat: {n_fut}"
        )
    elif cmd == "/futures":
        telegram_send_message(
            f"⚙️ <b>Futures 3x Ayarlari</b>\n"
            f"• Durum: {'🟢 AKTIF' if auto_trade['enabled'] else '⏸ PASIF'} | Mod: <b>{auto_trade['mode']}</b>\n"
            f"• Teminat: ${auto_trade['trade_usdt']:.2f} x {auto_trade['leverage']}x Izole\n"
            f"• Kar Al (TP): <b>+%{auto_trade['tp_pct']:g}</b> | Stop-Loss (SL): <b>-%{auto_trade['sl_pct']:g}</b>\n"
            f"• Kural: Yalnizca PUMP (0-7dk) & PUMP15M (15dk) USDT"
        )
    elif cmd == "/al_baslat":
        auto_trade["enabled"] = True
        save_state()
        telegram_send_message(
            f"🟢 <b>Futures {auto_trade['leverage']}x Oto Al-Sat BASLATILDI</b> ({auto_trade['mode']} · TP +%{auto_trade['tp_pct']:g} / SL -%{auto_trade['sl_pct']:g})"
        )
    elif cmd == "/al_durdur":
        auto_trade["enabled"] = False
        save_state()
        telegram_send_message("⏸ <b>Futures Oto Al-Sat DURDURULDU.</b> Sinyaller Telegram'a gelmeye devam eder.")
    elif cmd == "/pozisyonlar":
        with state_lock:
            opens = [t for t in trades_log if t.get("status") == "OPEN"]
        if not opens:
            telegram_send_message("📭 Acik Futures 3x pozisyonu yok.")
        else:
            lines = [f"📊 <b>Acik Futures 3x Pozisyonlar ({len(opens)})</b>\n"]
            for t in opens:
                lines.append(
                    f"• <b>#{t['symbol']}</b> ({t['engine']}) | Giris: ${fmt_price(t['entry_price'])} | "
                    f"TP: ${fmt_price(t['target_price'])} | SL: ${fmt_price(t['stop_price'])} | "
                    f"K/Z: <b>{'+' if t['pnl_usdt'] >= 0 else ''}${t['pnl_usdt']:.2f}</b>"
                )
            telegram_send_message("\n".join(lines))
    elif cmd == "/kapat" and len(parts) >= 2:
        sym_q = parts[1].strip().upper()
        with state_lock:
            target = next((t for t in trades_log if t.get("status") == "OPEN" and t["symbol"] == sym_q), None)
        if not target:
            telegram_send_message(f"⚠️ {html.escape(sym_q)} icin acik pozisyon bulunamadi.")
        else:
            ok = close_futures_trade(target, "MANUAL")
            if not ok:
                telegram_send_message(f"⚠️ {html.escape(sym_q)} kapatilamadi.")
    elif cmd == "/kilitler":
        telegram_send_message(
            f"🔒 <b>Aktif Kilitler</b>\n"
            f"• 0-7dk Pump: {len(locks['pump'])} coin\n"
            f"• 15dk Kirilim: {len(locks['pump15m'])} coin\n"
            f"• ATL / ATH Seviyeleri: {len(locks['ath_atl'])} kayit"
        )
    elif cmd == "/sifirla":
        with state_lock:
            locks["pump"].clear()
            locks["pump15m"].clear()
        save_state()
        telegram_send_message("♻️ <b>0-7 dk Pump ve 15 dk Kirilim kilitleri sifirlandi.</b> (ATL/ATH kesin seviye kilitleri korundu.)")


def telegram_commands_loop():
    global last_tg_update_id
    while True:
        try:
            if not BOT_TOKEN:
                time.sleep(10)
                continue
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={last_tg_update_id + 1}&timeout=20"
            data = http_get_json(url, timeout=28)
            for upd in data.get("result", []):
                uid = int(upd.get("update_id") or 0)
                if uid > last_tg_update_id:
                    last_tg_update_id = uid
                msg = upd.get("message") or upd.get("edited_message") or {}
                chat_id_str = str(msg.get("chat", {}).get("id", ""))
                txt = msg.get("text") or ""
                if chat_id_str == CHAT_ID and txt.startswith("/"):
                    handle_telegram_command(txt)
        except Exception:
            time.sleep(4)


# ==============================================================================
# CANLI PIYASA DONGULERI & WATCHDOG
# ==============================================================================
def process_ticker(sym, price, vol24h, chg24h, now):
    if price <= 0 or is_excluded(sym):
        return
    with state_lock:
        if active_spot_symbols and sym not in active_spot_symbols:
            return
    base, quote = extract_base_quote(sym)
    if not base:
        return
    with state_lock:
        tickers[sym] = {
            "symbol": sym,
            "base": base,
            "quote": quote,
            "price": price,
            "volume24h": vol24h,
            "change24h": chg24h,
            "updated_at": now,
        }
    evaluate_pump(sym, price, vol24h, now)


def ws_stream_loop():
    global ws_connected
    host = "stream.binance.com"
    path = "/ws/!miniTicker@arr"
    while True:
        ssock = None
        try:
            ctx = ssl.create_default_context()
            sock = socket.create_connection((host, 9443), timeout=15)
            ssock = ctx.wrap_socket(sock, server_hostname=host)
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            req = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:9443\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n\r\n"
            )
            ssock.sendall(req.encode("ascii"))
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = ssock.recv(4096)
                if not chunk:
                    raise EOFError("WS handshake EOF")
                buf += chunk
            ws_connected = True
            frame_buf = bytearray(buf.split(b"\r\n\r\n", 1)[1])
            while True:
                opcode, payload = _ws_read_frame(ssock, frame_buf)
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    _ws_send_frame(ssock, payload, opcode=0xA)
                    continue
                if opcode in (0x1, 0x2):
                    arr = json.loads(payload.decode("utf-8"))
                    if isinstance(arr, list):
                        now = time.time()
                        for item in arr:
                            c = float(item.get("c") or 0)
                            o = float(item.get("o") or 0)
                            chg = ((c - o) / o) * 100.0 if o > 0 else 0.0
                            process_ticker(item.get("s", ""), c, float(item.get("q") or 0), chg, now)
                        update_signal_peaks()
                        evaluate_open_trades_tick()
        except Exception:
            ws_connected = False
            time.sleep(5)
        finally:
            try:
                if ssock:
                    ssock.close()
            except Exception:
                pass


def rest_seed_loop():
    last_exch = 0.0
    while True:
        try:
            now = time.time()
            if now - last_exch >= 1800:
                refresh_active_spot_symbols()
                refresh_futures_contracts()
                last_exch = now
            raw = binance_rest_get("/api/v3/ticker/24hr", timeout=10)
            if isinstance(raw, list):
                now = time.time()
                for item in raw:
                    process_ticker(
                        item.get("symbol", ""),
                        float(item.get("lastPrice") or 0),
                        float(item.get("quoteVolume") or 0),
                        float(item.get("priceChangePercent") or 0),
                        now,
                    )
                update_signal_peaks()
                evaluate_open_trades_tick()
        except Exception:
            pass
        time.sleep(25 if ws_connected else 5)


def scheduler_and_news_loop():
    last_news_ts = 0.0
    while True:
        now = time.time()
        if now - last_news_ts >= 60:
            poll_binance_announcements()
            last_news_ts = now

        dt = tr_now()
        today = dt.strftime("%Y-%m-%d")

        # 21:00 TR — GUNUN KAZANANI (ONCE METIN BILDIRIM, SONRA GORSEL)
        if dt.hour == 21 and locks["reports"].get("victory_day") != today:
            locks["reports"]["victory_day"] = today
            save_state()
            cap, winner = build_victory_report(now)

            # 1) ONCE BILDIRIM (metin)
            telegram_send_message(cap)

            # 2) SONRA GORSEL (grafik)
            if winner:
                try:
                    c1m = fetch_klines(winner["symbol"], "1m", limit=45)
                    if c1m:
                        with state_lock:
                            tk = tickers.get(winner["symbol"])
                        live = tk["price"] if tk and tk["price"] > 0 else winner["peak_price"]
                        png = render_pump_png(
                            winner["symbol"],
                            "GUNUN KAZANANI",
                            c1m,
                            live,
                            winner["entry"],
                            badge="GUNUN KAZANANI",
                        )
                        if png:
                            telegram_send_photo(
                                png,
                                f"🏆 <b>GÜNÜN KAZANANI — #{html.escape(winner['base'])}</b>\n"
                                f"📈 Maksimum: <b>+{winner['peak_pct']:.1f}%</b> · "
                                f"${fmt_price(winner['entry'])} ➡️ ${fmt_price(winner['peak_price'])}",
                            )
                except Exception as e:
                    print(f"[Gunun kazanani gorsel hata] {e}")

        # 23:00 TR — TOP 24 SAAT
        if dt.hour == 23 and locks["reports"].get("top24_day") != today:
            locks["reports"]["top24_day"] = today
            save_state()
            cap, png = build_top24_report_and_png(now)
            if png:
                telegram_send_photo(png, cap)
            else:
                telegram_send_message(cap)
        time.sleep(15)


class HealthHandler(http.server.BaseHTTPRequestHandler):
    def _send_ok(self, include_body=True):
        with state_lock:
            open_cnt = sum(1 for t in trades_log if t.get("status") == "OPEN")
            n_pairs = len(tickers)
            n_fut = len(futures_filters)
        body = json.dumps({
            "ok": True,
            "ws": ws_connected,
            "pairs": n_pairs,
            "futuresPairs": n_fut,
            "engines": ["PUMP", "PUMP15M", "ATL", "ATH"],
            "autoTrade": {
                "enabled": auto_trade["enabled"],
                "mode": auto_trade["mode"],
                "leverage": auto_trade["leverage"],
                "tpPct": auto_trade["tp_pct"],
                "slPct": auto_trade["sl_pct"],
                "openTrades": open_cnt,
            },
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def do_GET(self):
        self._send_ok(include_body=True)

    def do_HEAD(self):
        self._send_ok(include_body=False)

    def log_message(self, format, *args):
        return


def _watchdog_runner(target_fn):
    while True:
        try:
            target_fn()
        except Exception as e:
            print(f"[Watchdog yeniden baslatma {target_fn.__name__}] {e}")
        time.sleep(3)


def main():
    load_state()
    refresh_active_spot_symbols()
    refresh_futures_contracts()
    print(
        f"[Spot Pump & Futures 3x Bot] Port={PORT}, Mode={auto_trade['mode']}, "
        f"Enabled={auto_trade['enabled']}, TP=+{auto_trade['tp_pct']}%, SL=-{auto_trade['sl_pct']}%"
    )
    workers = (
        ws_stream_loop,
        rest_seed_loop,
        structure_scan_loop,
        poll_open_binance_trades_loop,
        scheduler_and_news_loop,
        telegram_commands_loop,
    )
    for fn in workers:
        threading.Thread(target=_watchdog_runner, args=(fn,), daemon=True).start()

    srv = http.server.HTTPServer(("0.0.0.0", PORT), HealthHandler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
