#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Spot Akis Terminali & Binance Futures 3x Oto Al-Sat Botu (Render 7/24, Zero-Dependency)
Sinyal Motorlari:
  1A. 0-7 dk Spot Anlik Pump (PUMP) - Tekil Coin Kilidi + Canli 1dk/15sn PNG Grafik
  1B. 15 dk Hacimli Kirilim (PUMP15M) - Ayri Sinyal & RVOL >= 1.4x + PNG Grafik
  2.  Kesin ATL & ATH Kirilimi (ATL / ATH) - Seviye Kirilmadikca Asla Tekrar Etmez (Sadece Bildirim)
Otomatik Islem Motoru:
  - SADECE PUMP (0-7dk) ve PUMP15M (15dk) sinyallerinde calisir (ATL/ATH islem acmaz!)
  - Binance USD-M Futures 3x LONG (BUY) acar ve fiyat +%5 hedefe (+%15 ROE) geldiginde satar
  - PAPER (sanal test) / TESTNET / LIVE modlari (API anahtarlari yalnizca ENV uzerinden)
Ozetler & Komutlar:
  - 21:00 TR Gunun Zaferi · 23:00 TR Top 24 Saat · 60 sn Binance Duyuru
  - Komutlar: /start, /24s, /islemler, /otomatik ac|kapat, /sat SYMBOL, /durum, /sifirla, /test
"""

import base64
import datetime
import hashlib
import hmac
import http.server
import json
import math
import os
import socket
import socketserver
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

# ==============================================================================
# KONFIGURASYON (ENV DEGISKENLERI)
# ==============================================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("CHAT_ID", "1623907197").strip()
PORT = int(os.environ.get("PORT", "10000"))
PUMP_REPEAT_HOURS = float(os.environ.get("PUMP_REPEAT_HOURS", "0"))
STATE_FILE = os.environ.get("STATE_FILE", "/tmp/spot_bot_state.json")

BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY", "").strip()
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET", "").strip()
BINANCE_KEYS_CONFIGURED = bool(BINANCE_API_KEY and BINANCE_API_SECRET)


def _env_bool(name, default=False):
    val = os.environ.get(name, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on", "ac")


def _env_float(name, default, lo, hi):
    try:
        v = float(os.environ.get(name, str(default)).strip())
        return max(lo, min(hi, v)) if math.isfinite(v) else default
    except Exception:
        return default


_raw_mode = os.environ.get("AUTO_TRADE_MODE", "PAPER").strip().upper()
if _raw_mode in ("TESTNET", "LIVE") and BINANCE_KEYS_CONFIGURED:
    INITIAL_MODE = _raw_mode
else:
    INITIAL_MODE = "PAPER"

PAPER_START_USDT = _env_float("PAPER_START_USDT", 1000.0, 10.0, 1_000_000.0)

BINANCE_MIRRORS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://data-api.binance.vision",
]

EXCLUDED_PREFIXES = ("USDC", "FDUSD", "TUSD", "USDP", "EUR", "USDE", "USD1")
EXCLUDED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
TR_TZ = datetime.timezone(datetime.timedelta(hours=3))

# ==============================================================================
# DURUM & KILIT YONETIMI
# ==============================================================================
state_lock = threading.Lock()
tickers = {}            # sym -> dict
price_history = {}      # sym -> list[(ts_sec, price)]
signals_log = []        # list[dict]
active_spot_symbols = set()
futures_filters = {}    # sym -> {"stepSize": str, "minQty": float, "tickSize": str, "minNotional": float}
news_seen_ids = set()
news_seeded = False
ws_connected = False
ban_until_ts = 0.0
preferred_mirror_idx = 0
subscribed_chats = set([CHAT_ID]) if CHAT_ID else set()
pump_verify_cooldown = {}    # sym -> ts
ath_atl_cooldown = {}        # sym -> ts
verify_in_flight = set()     # key
trade_in_flight = set()      # sym or trade_id

auto_trade = {
    "enabled": _env_bool("AUTO_TRADE_ENABLED", False),
    "mode": INITIAL_MODE,
    "leverage": int(_env_float("FUTURES_LEVERAGE", 3, 1, 20)),
    "trade_usdt": _env_float("TRADE_USDT", 25.0, 5.0, 50000.0),
    "tp_pct": _env_float("TP_PCT", 5.0, 0.2, 200.0),
    "max_open_trades": int(_env_float("MAX_OPEN_TRADES", 5, 1, 25)),
}
paper_balance_usdt = PAPER_START_USDT
trades = []  # list[dict]

locks = {
    "pump": {},       # base -> {"symbol": str, "locked_at": float}
    "pump15m": {},    # base -> {"symbol": str, "locked_at": float}
    "ath_atl": {},    # sym -> {"atl": float, "ath": float, "bar_count": int, "synced_at": float, "last_atl": float|None, "last_ath": float|None}
    "reports": {"victory_day": "", "top24_day": ""},
}


def load_state():
    global signals_log, paper_balance_usdt, trades
    try:
        if not os.path.exists(STATE_FILE):
            return
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        raw_locks = raw.get("locks", {})
        if isinstance(raw_locks.get("pump"), dict):
            locks["pump"] = raw_locks["pump"]
        if isinstance(raw_locks.get("pump15m"), dict):
            locks["pump15m"] = raw_locks["pump15m"]
        if isinstance(raw_locks.get("ath_atl"), dict):
            locks["ath_atl"] = raw_locks["ath_atl"]
        if isinstance(raw_locks.get("reports"), dict):
            locks["reports"].update(raw_locks["reports"])

        raw_sigs = raw.get("signals", [])
        if isinstance(raw_sigs, list):
            signals_log = [
                s for s in raw_sigs
                if isinstance(s, dict) and s.get("engine") in ("PUMP", "PUMP15M", "ATL", "ATH")
            ][-300:]

        raw_at = raw.get("auto_trade")
        if isinstance(raw_at, dict):
            if isinstance(raw_at.get("enabled"), bool):
                auto_trade["enabled"] = raw_at["enabled"]
            m = str(raw_at.get("mode", "")).upper()
            if m == "PAPER":
                auto_trade["mode"] = "PAPER"
            elif m in ("TESTNET", "LIVE") and BINANCE_KEYS_CONFIGURED:
                auto_trade["mode"] = m
            if isinstance(raw_at.get("leverage"), (int, float)):
                auto_trade["leverage"] = max(1, min(20, int(raw_at["leverage"])))
            if isinstance(raw_at.get("trade_usdt"), (int, float)):
                auto_trade["trade_usdt"] = max(5.0, min(50000.0, float(raw_at["trade_usdt"])))
            if isinstance(raw_at.get("tp_pct"), (int, float)):
                auto_trade["tp_pct"] = max(0.2, min(200.0, float(raw_at["tp_pct"])))
            if isinstance(raw_at.get("max_open_trades"), (int, float)):
                auto_trade["max_open_trades"] = max(1, min(25, int(raw_at["max_open_trades"])))

        if isinstance(raw.get("paper_balance_usdt"), (int, float)):
            paper_balance_usdt = max(0.0, float(raw["paper_balance_usdt"]))
        if isinstance(raw.get("trades"), list):
            trades = [t for t in raw["trades"] if isinstance(t, dict)][-200:]
        for cid in raw.get("chats", []):
            s = str(cid or "").strip()
            if s:
                subscribed_chats.add(s)
    except Exception:
        pass


def save_state():
    try:
        with state_lock:
            payload = {
                "locks": locks,
                "signals": signals_log[-300:],
                "auto_trade": auto_trade,
                "paper_balance_usdt": round(paper_balance_usdt, 4),
                "trades": trades[-200:],
                "chats": list(subscribed_chats),
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


def floor_to_step(val, step_str):
    try:
        step = float(step_str)
    except Exception:
        return val
    if not math.isfinite(step) or step <= 0:
        return val
    if "." in step_str:
        decimals = len(step_str.split(".", 1)[1].rstrip("0"))
    else:
        decimals = 0
    floored = math.floor(val / step + 1e-9) * step
    return round(floored, decimals)


def format_by_step(val, step_str):
    try:
        step = float(step_str)
    except Exception:
        return f"{val:.8f}".rstrip("0").rstrip(".")
    if not math.isfinite(step) or step <= 0:
        return f"{val:.8f}".rstrip("0").rstrip(".")
    if "." in step_str:
        decimals = len(step_str.split(".", 1)[1].rstrip("0"))
    else:
        decimals = 0
    floored = math.floor(val / step + 1e-9) * step
    return f"{floored:.{decimals}f}"


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


def quote_vol_of(bar):
    qv = bar.get("quoteVol", 0.0)
    return qv if qv > 0 else bar.get("volume", 0.0) * bar.get("close", 0.0)


# ==============================================================================
# HTTP / BINANCE REST + WS-API + BYBIT FALLBACK + FUTURES IMZALI API
# ==============================================================================
def http_get_json(url, timeout=9):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; SpotFuturesBot/2.0)",
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


def sign_query_hmac(params_dict, secret):
    ordered = [(k, str(v)) for k, v in params_dict.items()]
    qs = urllib.parse.urlencode(ordered)
    sig = hmac.new(secret.encode("utf-8"), qs.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{qs}&signature={sig}"


class BinanceFuturesError(RuntimeError):
    def __init__(self, msg, code=0):
        super().__init__(msg)
        self.code = code


def binance_futures_signed(method, path_url, params, mode):
    if not BINANCE_KEYS_CONFIGURED:
        raise BinanceFuturesError("BINANCE_API_KEY veya BINANCE_API_SECRET tanimli degil.", -1)
    base_url = "https://testnet.binancefuture.com" if mode == "TESTNET" else "https://fapi.binance.com"
    qparams = dict(params)
    qparams["recvWindow"] = 5000
    qparams["timestamp"] = int(time.time() * 1000)
    signed_qs = sign_query_hmac(qparams, BINANCE_API_SECRET)
    url = f"{base_url}{path_url}?{signed_qs}"
    req = urllib.request.Request(
        url,
        headers={
            "X-MBX-APIKEY": BINANCE_API_KEY,
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; SpotFuturesBot/2.0)",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw_body = ""
        try:
            raw_body = e.read().decode("utf-8")
            err_json = json.loads(raw_body)
        except Exception:
            err_json = {}
        code = int(err_json.get("code") or 0)
        msg = err_json.get("msg") or raw_body or f"HTTP {e.code}"
        raise BinanceFuturesError(f"Binance Futures {e.code} ({code}): {msg}", code)


def refresh_futures_contracts():
    try:
        info = http_get_json("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=12)
        updated = {}
        for s in info.get("symbols", []):
            if (
                s.get("symbol")
                and s.get("status") == "TRADING"
                and s.get("quoteAsset") == "USDT"
                and s.get("contractType") == "PERPETUAL"
            ):
                step_size = "0.001"
                min_qty = 0.0
                tick_size = "0.0001"
                min_notional = 5.0
                for flt in s.get("filters", []):
                    ft = flt.get("filterType")
                    if ft == "LOT_SIZE":
                        if flt.get("stepSize"):
                            step_size = flt["stepSize"]
                        if flt.get("minQty"):
                            min_qty = float(flt["minQty"] or 0.0)
                    elif ft == "PRICE_FILTER" and flt.get("tickSize"):
                        tick_size = flt["tickSize"]
                    elif ft == "MIN_NOTIONAL" and flt.get("notional"):
                        min_notional = float(flt["notional"] or 5.0)
                updated[s["symbol"]] = {
                    "stepSize": step_size,
                    "minQty": min_qty,
                    "tickSize": tick_size,
                    "minNotional": min_notional,
                }
        if len(updated) > 50:
            with state_lock:
                futures_filters.clear()
                futures_filters.update(updated)
    except Exception:
        pass


def refresh_active_spot_symbols():
    try:
        info = binance_rest_get("/api/v3/exchangeInfo", timeout=12)
        nxt = set()
        for s in info.get("symbols", []):
            if s.get("symbol") and s.get("status") == "TRADING" and s.get("isSpotTradingAllowed") is not False:
                nxt.add(s["symbol"])
        if len(nxt) > 50:
            with state_lock:
                active_spot_symbols.clear()
                active_spot_symbols.update(nxt)
    except Exception:
        pass


def fetch_futures_mark_price(symbol, mode):
    try:
        base_url = "https://testnet.binancefuture.com" if mode == "TESTNET" else "https://fapi.binance.com"
        d = http_get_json(f"{base_url}/fapi/v1/ticker/price?symbol={urllib.parse.quote(symbol)}", timeout=3.5)
        p = float(d.get("price") or 0.0)
        if p > 0:
            return p
    except Exception:
        pass
    with state_lock:
        tk = tickers.get(symbol)
        return float(tk["price"]) if tk else 0.0


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


def fetch_spot_klines(sym, interval="1m", limit=45):
    try:
        raw = binance_rest_get(
            f"/api/v3/klines?symbol={urllib.parse.quote(sym)}&interval={urllib.parse.quote(interval)}&limit={limit}",
            timeout=8,
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
    if interval == "1m":
        try:
            url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={urllib.parse.quote(sym)}&interval=1&limit={limit}"
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
                        "quoteVol": float(r[5]) * float(r[4]),
                    }
                    for r in reversed(rows)
                ]
        except Exception:
            pass
    return []


# ==============================================================================
# SIFIR BAGIMLILIKLI PNG MUM GRAFIGI OLUSTURUCU
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
    " ": [0x00, 0x00, 0x00, 0x00, 0x00], "|": [0x00, 0x00, 0x7F, 0x00, 0x00],
}


def render_pump_png(symbol, tf_label, candles, live_price, start_price, title_tag="SPOT PUMP"):
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
                            fill_rect(
                                cx + gx * scale,
                                y + gy * scale,
                                cx + gx * scale + scale - 1,
                                y + gy * scale + scale - 1,
                                col,
                            )
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

    draw_text(pad_l, 14, f"{symbol} | {tf_label} | {title_tag} | ANLIK ${fmt_price(live_price)}", TEXT, scale=2)

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
        fill_rect(
            cx - bw // 2,
            vol_top + vol_h - vh,
            cx + bw // 2,
            vol_top + vol_h,
            (col[0] // 2, col[1] // 2, col[2] // 2),
        )

    if live_price and live_price > 0:
        ly = y_of(live_price)
        draw_line(pad_l, ly, pad_l + plot_w, ly, LIVE_COL, dashed=True)
        fill_rect(pad_l + plot_w + 4, ly - 7, W - 4, ly + 7, (2, 132, 199))
        draw_text(pad_l + plot_w + 8, ly - 3, fmt_price(live_price), (248, 250, 252), scale=1)

    raw_rows = bytearray()
    stride = W * 3
    for y in range(H):
        raw_rows.append(0)
        raw_rows.extend(buf[y * stride:(y + 1) * stride])
    compressed = zlib.compress(bytes(raw_rows), 6)

    def png_chunk(tag, payload):
        return (
            struct.pack("!I", len(payload))
            + tag
            + payload
            + struct.pack("!I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack("!IIBBBBB", W, H, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", compressed)
        + png_chunk(b"IEND", b"")
    )


# ==============================================================================
# TELEGRAM GONDERICILER
# ==============================================================================
def _tg_call(method, payload, timeout=15):
    if not BOT_TOKEN:
        return {"ok": False}
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def telegram_send_message(text, chat_id=None):
    targets = [chat_id] if chat_id else list(subscribed_chats or [CHAT_ID])
    if not BOT_TOKEN:
        print(f"[MESAJ]\n{text}\n")
        return
    for cid in targets:
        if not cid:
            continue
        try:
            _tg_call(
                "sendMessage",
                {
                    "chat_id": cid,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
        except Exception as e:
            print(f"[TG sendMessage hata] {e}")


def telegram_send_photo(png_bytes, caption, chat_id=None):
    if len(caption) > 1000:
        caption = caption[:997] + "..."
    targets = [chat_id] if chat_id else list(subscribed_chats or [CHAT_ID])
    if not BOT_TOKEN:
        print(f"[GORSEL {len(png_bytes)}B]\n{caption}\n")
        return
    if not png_bytes:
        telegram_send_message(caption, chat_id=chat_id)
        return
    for cid in targets:
        if not cid:
            continue
        try:
            boundary = "----SpotFuturesBoundary7e8a9b"
            parts = []
            for k, v in (("chat_id", cid), ("caption", caption), ("parse_mode", "HTML")):
                parts.append(
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8")
                )
            parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"chart.png\"\r\nContent-Type: image/png\r\n\r\n".encode("utf-8")
                + png_bytes
                + b"\r\n"
            )
            parts.append(f"--{boundary}--\r\n".encode("utf-8"))
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data=b"".join(parts),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as e:
            print(f"[TG sendPhoto hata] {e}")
            telegram_send_message(caption, chat_id=cid)


# ==============================================================================
# OTOMATIK BINANCE USD-M FUTURES 3X LONG + +%5 TP MOTORU
# (SADECE PUMP 0-7DK VE PUMP15M 15DK KIRILIM SINYALLERINDE ACILIR!)
# ==============================================================================
def should_auto_buy_signal(engine, quote):
    return bool(auto_trade["enabled"]) and quote == "USDT" and engine in ("PUMP", "PUMP15M")


def get_trade_summary():
    with state_lock:
        open_tr = [t for t in trades if t.get("status") == "OPEN"]
        closed_tr = [t for t in trades if t.get("status") == "CLOSED"]
        wins = sum(1 for t in closed_tr if t.get("pnl_usdt", 0.0) > 0)
        realized = round(sum(t.get("pnl_usdt", 0.0) for t in closed_tr), 2)
        unrealized = round(sum(t.get("pnl_usdt", 0.0) for t in open_tr), 2)
        return {
            "paper_balance": round(paper_balance_usdt, 2),
            "open_count": len(open_tr),
            "closed_count": len(closed_tr),
            "wins": wins,
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "futures_count": len(futures_filters),
        }


def format_trades_telegram_message():
    sum_d = get_trade_summary()
    with state_lock:
        open_tr = [dict(t) for t in trades if t.get("status") == "OPEN"]
    lev = auto_trade["leverage"]
    tp = auto_trade["tp_pct"]
    lines = [
        f"🤖 <b>FUTURES {lev}x OTO LONG ({auto_trade['mode']})</b>",
        f"• Durum: {'🟢 Aktif' if auto_trade['enabled'] else '🔴 Pasif'} | Hedef Satis: <b>+{tp:.1f}%</b> (ROE: +{tp * lev:.0f}%)",
        "• Yalnizca 0-7dk Pump & 15dk Kirilim sinyallerinde BUY acar.",
        f"• Paper Bakiye: ${sum_d['paper_balance']:.2f} | Net K/Z: {'+' if sum_d['realized_pnl'] >= 0 else ''}${sum_d['realized_pnl']:.2f} ({sum_d['wins']}/{sum_d['closed_count']})",
    ]
    if not open_tr:
        lines.append("\nAcik Futures pozisyonu bulunmuyor.")
    else:
        lines.append(f"\n<b>Acik {lev}x Pozisyonlar ({len(open_tr)}):</b>")
        for tr in open_tr[:10]:
            roe = tr.get("roe_pct", 0.0)
            lines.append(
                f"• <b>#{tr['symbol']}</b> ({tr['engine']}) · Giris: ${fmt_price(tr['entry_price'])} → +%5: ${fmt_price(tr['target_price'])} | ROE: {'+' if roe >= 0 else ''}{roe:.2f}%"
            )
    return "\n".join(lines)


def maybe_execute_auto_buy(symbol, base, quote, engine, engine_label, ref_entry_price):
    global paper_balance_usdt
    if not should_auto_buy_signal(engine, quote):
        return None

    with state_lock:
        if symbol in trade_in_flight:
            return None
        open_tr = [t for t in trades if t.get("status") == "OPEN"]
        if len(open_tr) >= auto_trade["max_open_trades"]:
            return None
        if any(t.get("symbol") == symbol or t.get("base") == base for t in open_tr):
            return None
        flt = futures_filters.get(symbol)
        if not flt:
            return None
        mode = auto_trade["mode"]
        lev = auto_trade["leverage"]
        margin_usdt = auto_trade["trade_usdt"]
        tp_pct = auto_trade["tp_pct"]
        if mode == "PAPER" and paper_balance_usdt < margin_usdt:
            return None
        if mode in ("TESTNET", "LIVE") and not BINANCE_KEYS_CONFIGURED:
            return None
        trade_in_flight.add(symbol)

    try:
        notional_usdt = margin_usdt * lev
        ref_price = fetch_futures_mark_price(symbol, mode) or ref_entry_price
        if ref_price <= 0:
            return None
        now = time.time()

        if mode == "PAPER":
            qty = floor_to_step(notional_usdt / ref_price, flt["stepSize"])
            if qty <= 0 or qty < flt["minQty"]:
                return None
            actual_notional = qty * ref_price
            actual_margin = round(actual_notional / lev, 2)
            tp_str = format_by_step(ref_price * (1.0 + tp_pct / 100.0), flt["tickSize"])
            target_price = float(tp_str) if tp_str else ref_price * (1.0 + tp_pct / 100.0)
            fee_open = actual_notional * 0.0005

            tr = {
                "id": f"TR-PAPER-{symbol}-{int(now * 1000)}",
                "symbol": symbol,
                "base": base,
                "engine": engine,
                "mode": "PAPER",
                "leverage": lev,
                "status": "OPEN",
                "close_reason": None,
                "entry_price": ref_price,
                "target_price": target_price,
                "current_price": ref_price,
                "exit_price": None,
                "quantity": qty,
                "margin_usdt": actual_margin,
                "notional_usdt": round(actual_notional, 2),
                "pnl_usdt": round(-fee_open * 2, 2),
                "pnl_pct": 0.0,
                "roe_pct": round(((-fee_open * 2) / max(actual_margin, 1e-6)) * 100.0, 2),
                "buy_order_id": None,
                "tp_order_id": None,
                "opened_at": now,
                "closed_at": None,
            }
            with state_lock:
                paper_balance_usdt = max(0.0, round(paper_balance_usdt - actual_margin, 4))
                trades.insert(0, tr)
                if len(trades) > 200:
                    del trades[200:]
            save_state()
            telegram_send_message(
                f"🟢 <b>FUTURES {lev}x LONG ACILDI (PAPER · {engine_label})</b>\n"
                f"• Parite: <b>#{symbol}</b> · Teminat: ${tr['margin_usdt']:.2f} ({lev}x Hacim: ${tr['notional_usdt']:.2f})\n"
                f"• Giris: ${fmt_price(tr['entry_price'])}\n"
                f"🎯 +{tp_pct:.1f}% Satis Hedefi: <b>${fmt_price(tr['target_price'])}</b>"
            )
            return tr

        # TESTNET / LIVE USD-M FUTURES
        binance_futures_signed("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": lev}, mode)
        qty = floor_to_step(notional_usdt / ref_price, flt["stepSize"])
        if qty <= 0 or qty < flt["minQty"] or qty * ref_price < flt["minNotional"]:
            return None
        qty_str = format_by_step(qty, flt["stepSize"])

        hedge_mode = False
        try:
            buy_res = binance_futures_signed(
                "POST",
                "/fapi/v1/order",
                {"symbol": symbol, "side": "BUY", "type": "MARKET", "quantity": qty_str},
                mode,
            )
        except BinanceFuturesError as err:
            if err.code == -4061:
                hedge_mode = True
                buy_res = binance_futures_signed(
                    "POST",
                    "/fapi/v1/order",
                    {"symbol": symbol, "side": "BUY", "positionSide": "LONG", "type": "MARKET", "quantity": qty_str},
                    mode,
                )
            else:
                raise

        exec_qty = float(buy_res.get("executedQty") or qty)
        avg_price = float(buy_res.get("avgPrice") or 0.0)
        buy_oid = buy_res.get("orderId")
        if avg_price <= 0 and buy_oid:
            time.sleep(0.35)
            try:
                chk = binance_futures_signed("GET", "/fapi/v1/order", {"symbol": symbol, "orderId": buy_oid}, mode)
                exec_qty = float(chk.get("executedQty") or exec_qty)
                avg_price = float(chk.get("avgPrice") or 0.0)
            except Exception:
                pass

        entry_price = avg_price if avg_price > 0 else ref_price
        final_qty = floor_to_step(exec_qty, flt["stepSize"]) or qty
        final_qty_str = format_by_step(final_qty, flt["stepSize"])
        tp_str = format_by_step(entry_price * (1.0 + tp_pct / 100.0), flt["tickSize"])
        target_price = float(tp_str) if tp_str else entry_price * (1.0 + tp_pct / 100.0)

        tp_params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": final_qty_str,
            "price": tp_str,
        }
        if hedge_mode:
            tp_params["positionSide"] = "LONG"
        else:
            tp_params["reduceOnly"] = "true"

        tp_res = binance_futures_signed("POST", "/fapi/v1/order", tp_params, mode)
        tp_oid = str(tp_res["orderId"]) if tp_res.get("orderId") is not None else None
        actual_notional = final_qty * entry_price
        actual_margin = round(actual_notional / lev, 2)

        tr = {
            "id": f"TR-{mode}-{symbol}-{int(now * 1000)}",
            "symbol": symbol,
            "base": base,
            "engine": engine,
            "mode": mode,
            "leverage": lev,
            "status": "OPEN",
            "close_reason": None,
            "entry_price": entry_price,
            "target_price": target_price,
            "current_price": entry_price,
            "exit_price": None,
            "quantity": final_qty,
            "margin_usdt": actual_margin,
            "notional_usdt": round(actual_notional, 2),
            "pnl_usdt": 0.0,
            "pnl_pct": 0.0,
            "roe_pct": 0.0,
            "buy_order_id": str(buy_oid) if buy_oid is not None else None,
            "tp_order_id": tp_oid,
            "opened_at": now,
            "closed_at": None,
        }
        with state_lock:
            trades.insert(0, tr)
            if len(trades) > 200:
                del trades[200:]
        save_state()
        telegram_send_message(
            f"🟢 <b>FUTURES {lev}x LONG ACILDI ({mode} · {engine_label})</b>\n"
            f"• Parite: <b>#{symbol}</b> · Miktar: {final_qty_str} ({lev}x = ${tr['notional_usdt']:.2f})\n"
            f"• Giris: ${fmt_price(tr['entry_price'])}\n"
            f"🎯 +{tp_pct:.1f}% Limit Satis Kondu: <b>${fmt_price(tr['target_price'])}</b>"
        )
        return tr
    except Exception as e:
        print(f"[Futures Oto Alim Hata ({symbol})] {e}")
        telegram_send_message(f"⚠️ <b>Futures Oto Alim Basarisiz (#{symbol})</b>\n{e}")
        return None
    finally:
        with state_lock:
            trade_in_flight.discard(symbol)


def close_futures_trade(tr, reason="MANUAL", explicit_exit_price=None):
    global paper_balance_usdt
    tid = tr["id"]
    with state_lock:
        if tr.get("status") != "OPEN" or tid in trade_in_flight:
            return False
        trade_in_flight.add(tid)
        tk = tickers.get(tr["symbol"])
        live_price = explicit_exit_price or (tk["price"] if tk else None) or tr.get("current_price") or tr["entry_price"]
        flt = futures_filters.get(tr["symbol"], {"stepSize": "0.0001", "minQty": 0, "tickSize": "0.0001", "minNotional": 5})

    try:
        mode = tr["mode"]
        if mode == "PAPER":
            gross = tr["quantity"] * (live_price - tr["entry_price"])
            fees = tr["quantity"] * (tr["entry_price"] + live_price) * 0.0005
            net_pnl = gross - fees
            with state_lock:
                paper_balance_usdt = round(max(0.0, paper_balance_usdt + tr["margin_usdt"] + net_pnl), 4)
                tr["status"] = "CLOSED"
                tr["close_reason"] = reason
                tr["exit_price"] = live_price
                tr["current_price"] = live_price
                tr["pnl_usdt"] = round(net_pnl, 2)
                tr["pnl_pct"] = round(((live_price - tr["entry_price"]) / tr["entry_price"]) * 100.0, 2) if tr["entry_price"] > 0 else 0.0
                tr["roe_pct"] = round((net_pnl / max(tr["margin_usdt"], 1e-6)) * 100.0, 2)
                tr["closed_at"] = time.time()
        else:
            if reason != "TP" and tr.get("tp_order_id"):
                try:
                    binance_futures_signed("DELETE", "/fapi/v1/order", {"symbol": tr["symbol"], "orderId": tr["tp_order_id"]}, mode)
                except Exception:
                    pass
            exit_price = live_price
            if reason != "TP":
                qty_str = format_by_step(tr["quantity"], flt["stepSize"])
                try:
                    sell_res = binance_futures_signed(
                        "POST",
                        "/fapi/v1/order",
                        {"symbol": tr["symbol"], "side": "SELL", "type": "MARKET", "quantity": qty_str, "reduceOnly": "true"},
                        mode,
                    )
                except BinanceFuturesError as err:
                    if err.code in (-1106, -4061):
                        sell_res = binance_futures_signed(
                            "POST",
                            "/fapi/v1/order",
                            {"symbol": tr["symbol"], "side": "SELL", "positionSide": "LONG", "type": "MARKET", "quantity": qty_str},
                            mode,
                        )
                    else:
                        raise
                avg_p = float(sell_res.get("avgPrice") or 0.0)
                if avg_p > 0:
                    exit_price = avg_p

            gross = tr["quantity"] * (exit_price - tr["entry_price"])
            fees = tr["quantity"] * (tr["entry_price"] + exit_price) * 0.0005
            net_pnl = gross - fees
            with state_lock:
                tr["status"] = "CLOSED"
                tr["close_reason"] = reason
                tr["exit_price"] = exit_price
                tr["current_price"] = exit_price
                tr["pnl_usdt"] = round(net_pnl, 2)
                tr["pnl_pct"] = round(((exit_price - tr["entry_price"]) / tr["entry_price"]) * 100.0, 2) if tr["entry_price"] > 0 else 0.0
                tr["roe_pct"] = round((net_pnl / max(tr["margin_usdt"], 1e-6)) * 100.0, 2)
                tr["closed_at"] = time.time()

        save_state()
        icon = "✅" if tr["pnl_usdt"] >= 0 else "🛑"
        lbl = f"+{auto_trade['tp_pct']:.1f}% KAR AL" if reason == "TP" else "MANUEL KAPATMA"
        telegram_send_message(
            f"{icon} <b>FUTURES {tr['leverage']}x KAPANDI — #{tr['symbol']} ({lbl})</b>\n"
            f"• Giris: ${fmt_price(tr['entry_price'])} ➡️ Cikis: ${fmt_price(tr['exit_price'])}\n"
            f"• Net K/Z: <b>{'+' if tr['pnl_usdt'] >= 0 else ''}${tr['pnl_usdt']:.2f}</b> (Fiyat: {'+' if tr['pnl_pct'] >= 0 else ''}{tr['pnl_pct']:.2f}% · ROE: {'+' if tr['roe_pct'] >= 0 else ''}{tr['roe_pct']:.2f}%)"
        )
        return True
    except Exception as e:
        print(f"[Futures Satis Hata ({tr['symbol']})] {e}")
        return False
    finally:
        with state_lock:
            trade_in_flight.discard(tid)


def evaluate_open_trades_tick():
    to_tp = []
    with state_lock:
        for tr in trades:
            if tr.get("status") != "OPEN":
                continue
            tk = tickers.get(tr["symbol"])
            if not tk or tk["price"] <= 0:
                continue
            p = tk["price"]
            tr["current_price"] = p
            gross = tr["quantity"] * (p - tr["entry_price"])
            fees = tr["quantity"] * (tr["entry_price"] + p) * 0.0005
            net = gross - fees
            tr["pnl_usdt"] = round(net, 2)
            tr["pnl_pct"] = round(((p - tr["entry_price"]) / tr["entry_price"]) * 100.0, 2) if tr["entry_price"] > 0 else 0.0
            tr["roe_pct"] = round((net / max(tr["margin_usdt"], 1e-6)) * 100.0, 2)
            if tr["mode"] == "PAPER" and p >= tr["target_price"]:
                to_tp.append((tr, p))

    for tr, fill_p in to_tp:
        close_futures_trade(tr, reason="TP", explicit_exit_price=fill_p)


def poll_open_binance_trades_loop():
    while True:
        if BINANCE_KEYS_CONFIGURED:
            with state_lock:
                real_open = [t for t in trades if t.get("status") == "OPEN" and t.get("mode") != "PAPER" and t.get("tp_order_id")]
            for tr in real_open:
                try:
                    ord_info = binance_futures_signed(
                        "GET",
                        "/fapi/v1/order",
                        {"symbol": tr["symbol"], "orderId": tr["tp_order_id"]},
                        tr["mode"],
                    )
                    if ord_info.get("status") == "FILLED":
                        fill_p = float(ord_info.get("avgPrice") or tr["target_price"])
                        close_futures_trade(tr, reason="TP", explicit_exit_price=fill_p)
                except Exception:
                    pass
        time.sleep(4)


# ==============================================================================
# SINYAL KAYIT VE MOTOR 1A (0-7 DK PUMP) + MOTOR 1B (15 DK KIRILIM)
# ==============================================================================
def record_signal_and_notify(engine, symbol, base, quote, direction, entry_price, caption, png_bytes=b""):
    now = time.time()
    with state_lock:
        signals_log.append({
            "id": f"{engine}-{symbol}-{int(now * 1000)}",
            "engine": engine,
            "symbol": symbol,
            "base": base,
            "direction": direction,
            "entry": entry_price,
            "current": entry_price,
            "peak_price": entry_price,
            "peak_pct": 0.0,
            "created_at": now,
        })
        if len(signals_log) > 300:
            del signals_log[:-300]
    save_state()
    if png_bytes:
        telegram_send_photo(png_bytes, caption)
    else:
        telegram_send_message(caption)

    engine_label = "0-7 dk Ani Pump" if engine == "PUMP" else ("15 dk Hacimli Kirilim" if engine == "PUMP15M" else engine)
    if should_auto_buy_signal(engine, quote):
        threading.Thread(
            target=maybe_execute_auto_buy,
            args=(symbol, base, quote, engine, engine_label, entry_price),
            daemon=True,
        ).start()


def is_pump_locked(bucket_name, base, now):
    rec = locks[bucket_name].get(base)
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
    fast_th = 4.5 if quote == "USDT" else 4.0
    breakout_th = 3.2
    if price <= 0 or vol24h < min_vol:
        return

    with state_lock:
        hist = price_history.setdefault(sym, [])
        hist.append((now, price))
        cutoff_15m = now - 900
        cutoff_7m = now - 420
        while hist and hist[0][0] < cutoff_15m:
            hist.pop(0)
        if len(hist) < 2:
            return

        min_15m = min(hist, key=lambda x: x[1])
        pts_7m = [pt for pt in hist if pt[0] >= cutoff_7m]
        min_7m = min(pts_7m, key=lambda x: x[1]) if pts_7m else min_15m
        if min_15m[1] <= 0 or min_7m[1] <= 0:
            return

        pct_7m = ((price - min_7m[1]) / min_7m[1]) * 100.0
        pct_15m = ((price - min_15m[1]) / min_15m[1]) * 100.0

        want_7m = pct_7m >= fast_th and not is_pump_locked("pump", base, now)
        want_15m = pct_15m >= breakout_th and not is_pump_locked("pump15m", base, now)
        if not want_7m and not want_15m:
            return

        vkey = f"PUMP-{sym}"
        if now < pump_verify_cooldown.get(sym, 0.0) or vkey in verify_in_flight:
            return
        verify_in_flight.add(vkey)
        hist_copy = list(hist)

    threading.Thread(
        target=_verify_and_emit_pump,
        args=(sym, base, quote, price, vol24h, min_7m, min_15m, hist_copy),
        daemon=True,
    ).start()


def _verify_and_emit_pump(sym, base, quote, approx_cur, approx_vol, min_7m, min_15m, hist_copy):
    vkey = f"PUMP-{sym}"
    try:
        live_cur, live_vol = approx_cur, approx_vol
        try:
            tk = binance_rest_get(f"/api/v3/ticker/24hr?symbol={urllib.parse.quote(sym)}", timeout=4)
            if isinstance(tk, dict):
                live_cur = float(tk.get("lastPrice") or approx_cur)
                live_vol = float(tk.get("quoteVolume") or approx_vol)
        except Exception:
            pass

        c1m = fetch_spot_klines(sym, "1m", limit=20)
        c15m = fetch_spot_klines(sym, "15m", limit=30)

        recent_1m = c1m[-7:] if c1m else []
        recent_15m_1m = c1m[-15:] if c1m else []
        bar_min_7m = min((b["low"] for b in recent_1m if b["low"] > 0), default=min_7m[1])
        verified_min_7m = min(min_7m[1], bar_min_7m)
        verified_pct_7m = ((live_cur - verified_min_7m) / verified_min_7m) * 100.0

        cur_15m = c15m[-1] if c15m else None
        prev_15m = c15m[-2] if len(c15m) > 1 else None
        cand_15m_lows = [b["low"] for b in recent_15m_1m if b["low"] > 0]
        if cur_15m and cur_15m["low"] > 0:
            cand_15m_lows.append(cur_15m["low"])
        bar_min_15m = min(cand_15m_lows) if cand_15m_lows else min_15m[1]
        verified_min_15m = min(min_15m[1], bar_min_15m)
        verified_pct_15m = ((live_cur - verified_min_15m) / verified_min_15m) * 100.0

        base_15m_bars = c15m[-11:-1] if len(c15m) >= 4 else []
        avg_15m_q = (sum(quote_vol_of(b) for b in base_15m_bars) / len(base_15m_bars)) if len(base_15m_bars) >= 3 else 0.0
        now_sec = time.time()
        elapsed_15m = max(90.0, min(900.0, now_sec - cur_15m["openTime"])) if cur_15m else 900.0
        cur_15m_paced_q = (quote_vol_of(cur_15m) * (900.0 / elapsed_15m)) if cur_15m else 0.0
        prev_15m_green_q = (
            quote_vol_of(prev_15m)
            if prev_15m and prev_15m["open"] > 0 and ((prev_15m["close"] - prev_15m["open"]) / prev_15m["open"]) * 100.0 >= 2.0
            else 0.0
        )
        active_15m_q = max(cur_15m_paced_q, prev_15m_green_q)
        rvol_15m = (active_15m_q / avg_15m_q) if avg_15m_q > 0 else 1.0

        last_3m = c1m[-3:] if c1m else []
        base_1m = c1m[-13:-3] if len(c1m) >= 6 else []
        avg_1m_recent = (sum(quote_vol_of(b) for b in last_3m) / len(last_3m)) if last_3m else 0.0
        max_1m_recent = max((quote_vol_of(b) for b in last_3m), default=0.0)
        avg_1m_base = (sum(quote_vol_of(b) for b in base_1m) / len(base_1m)) if len(base_1m) >= 3 else 0.0
        rvol_1m_avg = (avg_1m_recent / avg_1m_base) if avg_1m_base > 0 else 1.0
        rvol_1m_peak = ((max_1m_recent / avg_1m_base) * 0.75) if avg_1m_base > 0 else 1.0
        rvol = round(max(rvol_15m, rvol_1m_avg, rvol_1m_peak), 1)

        candle_high = max(live_cur, cur_15m["high"] if cur_15m else live_cur)
        candle_low = min(verified_min_15m, cur_15m["low"] if cur_15m else verified_min_15m)
        hold_ratio = (live_cur - candle_low) / max(candle_high - candle_low, 1e-12)
        is_green_15m = (
            (live_cur > cur_15m["open"] or (prev_15m is not None and live_cur > prev_15m["open"] and prev_15m["close"] > prev_15m["open"]))
            if cur_15m
            else True
        )

        fast_th = 4.5 if quote == "USDT" else 4.0
        breakout_th = 3.2
        check_now = time.time()

        with state_lock:
            fast_ok = verified_pct_7m >= fast_th and hold_ratio >= 0.35 and not is_pump_locked("pump", base, check_now)
            breakout_ok = (
                verified_pct_15m >= breakout_th
                and rvol >= 1.4
                and is_green_15m
                and hold_ratio >= 0.45
                and not is_pump_locked("pump15m", base, check_now)
            )
            if not fast_ok and not breakout_ok:
                pump_verify_cooldown[sym] = check_now + 20.0
                return
            if fast_ok:
                locks["pump"][base] = {"symbol": sym, "locked_at": check_now}
            if breakout_ok:
                locks["pump15m"][base] = {"symbol": sym, "locked_at": check_now}

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

        vol_str = f"{round(live_vol):,}" if quote == "USDT" else f"{live_vol:.2f}"
        lock_7m_txt = f"{PUMP_REPEAT_HOURS:g} saatlik dalga kilidi aktif." if PUMP_REPEAT_HOURS > 0 else "Tekil coin kilidi aktif."
        lock_15m_txt = f"15dk {PUMP_REPEAT_HOURS:g} saatlik dalga kilidi aktif." if PUMP_REPEAT_HOURS > 0 else "15dk tekil coin kilidi aktif."

        if fast_ok:
            mins_7m = max(1, min(7, round((check_now - min_7m[0]) / 60.0)))
            png = render_pump_png(sym, tf_label, chart_bars, live_cur, verified_min_7m, title_tag="SPOT PUMP")
            cap = (
                f"⚡ <b>SPOT ANLIK PUMP DEDEKTORU — #{sym}</b>\n\n"
                f"📈 Degisim: <b>+{verified_pct_7m:.2f}%</b> ({mins_7m} dk icinde!)\n"
                f"💰 Baslangic: {fmt_price(verified_min_7m)} ➡️ Anlik: <b>{fmt_price(live_cur)}</b>\n"
                f"💧 24s Parite Hacmi: {vol_str} ({quote})\n"
                f"🔒 {lock_7m_txt}"
            )
            record_signal_and_notify("PUMP", sym, base, quote, "LONG", live_cur, cap, png)

        if breakout_ok:
            mins_15m = max(1, min(15, round((check_now - min_15m[0]) / 60.0)))
            png15 = render_pump_png(sym, "15DK KIRILIM", c15m or chart_bars, live_cur, verified_min_15m, title_tag="15DK KIRILIM")
            cap15 = (
                f"🚀 <b>SPOT 15DK HACIMLI KIRILIM — #{sym}</b>\n\n"
                f"📈 Degisim: <b>+{verified_pct_15m:.2f}%</b> ({mins_15m} dk icinde! · RVOL: {rvol:.1f}x)\n"
                f"💰 Baslangic: {fmt_price(verified_min_15m)} ➡️ Anlik: <b>{fmt_price(live_cur)}</b>\n"
                f"💧 24s Parite Hacmi: {vol_str} ({quote})\n"
                f"🔒 {lock_15m_txt}"
            )
            record_signal_and_notify("PUMP15M", sym, base, quote, "LONG", live_cur, cap15, png15)
    except Exception:
        pass
    finally:
        with state_lock:
            verify_in_flight.discard(vkey)


# ==============================================================================
# MOTOR 2: TUM ZAMANLARIN EN DUSUGU (ATL) & EN YUKSEGI (ATH) — KESIN KIRILIM
# (SADECE TELEGRAM BILDIRIMI — OTO ISLEM ACMAZ!)
# ==============================================================================
def sync_ath_atl_for_symbol(sym):
    try:
        m_bars = fetch_spot_klines(sym, "1M", limit=120)
        bar_count = len(m_bars) * 30
        lows = [b["low"] for b in m_bars if b["low"] > 0]
        highs = [b["high"] for b in m_bars if b["high"] > 0]
        if len(m_bars) < 2:
            d_bars = fetch_spot_klines(sym, "1d", limit=60)
            bar_count = len(d_bars)
            lows.extend(b["low"] for b in d_bars if b["low"] > 0)
            highs.extend(b["high"] for b in d_bars if b["high"] > 0)
        if not lows or not highs:
            return
        min_low = min(lows)
        max_high = max(highs)
        with state_lock:
            prev = locks["ath_atl"].get(sym, {})
            eff_atl = min(min_low, prev["last_atl"]) if prev.get("last_atl") else min_low
            eff_ath = max(max_high, prev["last_ath"]) if prev.get("last_ath") else max_high
            locks["ath_atl"][sym] = {
                "atl": eff_atl,
                "ath": eff_ath,
                "bar_count": bar_count,
                "synced_at": time.time(),
                "last_atl": prev.get("last_atl"),
                "last_ath": prev.get("last_ath"),
            }
    except Exception:
        pass


def ath_atl_sync_loop():
    cursor = 0
    while True:
        try:
            with state_lock:
                cands = sorted(
                    [t for t in tickers.values() if t["quote"] == "USDT" and t["volume24h"] >= 150_000],
                    key=lambda x: x["volume24h"],
                    reverse=True,
                )
                syms = [c["symbol"] for c in cands]
            if syms:
                now = time.time()
                synced = 0
                for off in range(len(syms)):
                    idx = (cursor + off) % len(syms)
                    sym = syms[idx]
                    with state_lock:
                        rec = locks["ath_atl"].get(sym)
                    if rec and (now - rec.get("synced_at", 0.0)) < 6 * 3600:
                        continue
                    sync_ath_atl_for_symbol(sym)
                    synced += 1
                    cursor = (idx + 1) % len(syms)
                    if synced >= 20:
                        break
                    time.sleep(0.15)
                if synced > 0:
                    save_state()
        except Exception:
            pass
        time.sleep(8)


def evaluate_ath_atl(sym, price, vol24h, chg24h, now):
    base, quote = extract_base_quote(sym)
    if quote != "USDT" or price <= 0 or vol24h < 150_000:
        return
    with state_lock:
        rec = locks["ath_atl"].get(sym)
        if not rec or rec.get("bar_count", 0) < 14:
            return
        if now < ath_atl_cooldown.get(sym, 0.0):
            return
        min_atl_lock = min(rec["atl"], rec["last_atl"]) if rec.get("last_atl") else rec["atl"]
        max_ath_lock = max(rec["ath"], rec["last_ath"]) if rec.get("last_ath") else rec["ath"]
        is_atl = min_atl_lock > 0 and price <= min_atl_lock * 0.998
        is_ath = max_ath_lock > 0 and price >= max_ath_lock * 1.002
        if not is_atl and not is_ath:
            return
        vkey = f"ATHATL-{sym}"
        if vkey in verify_in_flight:
            return
        verify_in_flight.add(vkey)

    threading.Thread(
        target=_verify_and_emit_ath_atl,
        args=(sym, base, price, chg24h, is_atl, is_ath),
        daemon=True,
    ).start()


def _verify_and_emit_ath_atl(sym, base, approx_cur, approx_chg24, is_atl, is_ath):
    vkey = f"ATHATL-{sym}"
    try:
        live_cur, live_chg = approx_cur, approx_chg24
        try:
            tk = binance_rest_get(f"/api/v3/ticker/24hr?symbol={urllib.parse.quote(sym)}", timeout=4)
            if isinstance(tk, dict):
                live_cur = float(tk.get("lastPrice") or approx_cur)
                live_chg = float(tk.get("priceChangePercent") or approx_chg24)
        except Exception:
            pass

        now_check = time.time()
        with state_lock:
            rec = locks["ath_atl"].get(sym)
            if not rec:
                return
            prev_atl = min(rec["atl"], rec["last_atl"]) if rec.get("last_atl") else rec["atl"]
            prev_ath = max(rec["ath"], rec["last_ath"]) if rec.get("last_ath") else rec["ath"]

            if is_atl and live_cur > 0 and live_cur <= prev_atl * 0.998:
                rec["atl"] = live_cur
                rec["last_atl"] = live_cur
                ath_atl_cooldown[sym] = now_check + 15.0
                emit_kind = "ATL"
            elif is_ath and live_cur > 0 and live_cur >= prev_ath * 1.002:
                rec["ath"] = live_cur
                rec["last_ath"] = live_cur
                ath_atl_cooldown[sym] = now_check + 15.0
                emit_kind = "ATH"
            else:
                ath_atl_cooldown[sym] = now_check + 15.0
                return

        if emit_kind == "ATL":
            p_str = f"${fmt_price(live_cur)}"
            prev_str = f"${fmt_price(prev_atl)}"
            cap = (
                f"🕳 <b>#{sym} {p_str} tum zamanlarin en dusugu (ATL)</b>\n"
                f"📉 Onceki ATL ({prev_str}) kesin olarak asagi kirildi · 24s: {'+' if live_chg >= 0 else ''}{live_chg:.2f}%\n"
                f"🔒 Kilit aktif: {p_str} altina dusmedikce tekrar gelmez."
            )
            record_signal_and_notify("ATL", sym, base, "USDT", "SHORT", live_cur, cap)
        else:
            p_str = f"${fmt_price(live_cur)}"
            prev_str = f"${fmt_price(prev_ath)}"
            cap = (
                f"🏔 <b>#{sym} {p_str} tum zamanlarin en yuksegi (ATH)</b>\n"
                f"📈 Onceki ATH ({prev_str}) kesin olarak yukari kirildi · 24s: {'+' if live_chg >= 0 else ''}{live_chg:.2f}%\n"
                f"🔒 Kilit aktif: {p_str} ustune cikmadikce tekrar gelmez."
            )
            record_signal_and_notify("ATH", sym, base, "USDT", "LONG", live_cur, cap)
    except Exception:
        with state_lock:
            ath_atl_cooldown[sym] = time.time() + 15.0
    finally:
        with state_lock:
            verify_in_flight.discard(vkey)


# ==============================================================================
# PIK TAKIBI, RAPORLAR, BINANCE DUYURU & INTERAKTIF TELEGRAM KOMUTLARI
# ==============================================================================
def update_signal_peaks():
    changed = False
    with state_lock:
        for s in signals_log:
            tk = tickers.get(s["symbol"])
            if not tk or tk["price"] <= 0:
                continue
            p = tk["price"]
            s["current"] = p
            pct = ((s["entry"] - p) / s["entry"]) * 100.0 if s.get("direction") == "SHORT" else ((p - s["entry"]) / s["entry"]) * 100.0
            if pct > s.get("peak_pct", 0.0):
                s["peak_pct"] = round(pct, 2)
                s["peak_price"] = p
                changed = True
    if changed:
        save_state()


def build_victory_report(now_ts=None):
    now_ts = now_ts or time.time()
    cutoff = now_ts - 86400
    with state_lock:
        recent = [s for s in signals_log if s["created_at"] >= cutoff and s.get("peak_pct", 0.0) >= 3.0]
    best = {}
    for s in recent:
        prev = best.get(s["symbol"])
        if not prev or s["peak_pct"] > prev["peak_pct"]:
            best[s["symbol"]] = s
    ranked = sorted(best.values(), key=lambda x: x["peak_pct"], reverse=True)
    if not ranked:
        return "🏆 <b>GUNUN ZAFERI (21:00 TR)</b>\n\nSon 24 saatte +%3 ve uzeri pik yapan sinyal henuz olusmadi."
    lines = ["🏆 <b>GUNUN ZAFERI — SPOT PUMP SIRALAMASI (21:00 TR)</b>\n"]
    for i, s in enumerate(ranked[:15], 1):
        lines.append(
            f"{i}. <b>#{s['symbol']}</b> ({s['engine']}) · <b>+{s['peak_pct']:.2f}%</b> | ${fmt_price(s['entry'])} ➡️ ${fmt_price(s['peak_price'])}"
        )
    return "\n".join(lines)


def build_top24_report(now_ts=None):
    now_ts = now_ts or time.time()
    cutoff = now_ts - 86400
    with state_lock:
        recent = [s for s in signals_log if s["created_at"] >= cutoff]
    best = {}
    for s in recent:
        prev = best.get(s["symbol"])
        if not prev or s["peak_pct"] > prev["peak_pct"]:
            best[s["symbol"]] = s
    ranked = sorted(best.values(), key=lambda x: x["peak_pct"], reverse=True)
    if not ranked:
        return "📊 <b>TOP 24 SAAT OZETI (23:00 TR)</b>\n\nSon 24 saatte kaydedilen sinyal bulunmuyor."
    lines = ["📊 <b>TOP 24 SAAT OZETI (23:00 TR)</b>\n"]
    for i, s in enumerate(ranked[:20], 1):
        lines.append(
            f"{i}. <b>#{s['symbol']}</b> · Pik: <b>+{s['peak_pct']:.2f}%</b> | Anlik: ${fmt_price(s['current'])}"
        )
    return "\n".join(lines)


def format_top24h_market_message():
    with state_lock:
        top = sorted(
            [t for t in tickers.values() if t["quote"] == "USDT" and t.get("change24h", 0.0) > 0],
            key=lambda x: x.get("change24h", 0.0),
            reverse=True,
        )[:15]
    if not top:
        return "Veri henuz yukleniyor..."
    lines = ["📈 <b>ANLIK SPOT PIYASA — 24s En Yuksege Gidenler</b>\n"]
    for i, t in enumerate(top, 1):
        lines.append(
            f"{i:02d}. <b>{t['symbol']}</b> · ${fmt_price(t['price'])} | 24s: <b>+{t.get('change24h', 0.0):.2f}%</b>"
        )
    return "\n".join(lines)


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
            for art in data.get("data", {}).get("articles", []):
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


def telegram_command_loop():
    if not BOT_TOKEN:
        return
    try:
        me = _tg_call("getMe", {})
        uname = me.get("result", {}).get("username", "")
        if uname:
            _tg_call("deleteWebhook", {"drop_pending_updates": False})
            telegram_send_message(
                f"⚡ <b>Spot Pump + Futures {auto_trade['leverage']}x Oto Bot 7/24 Aktif</b>\n"
                f"• Bot: @{uname}\n• Komutlar: /24s · /islemler · /otomatik ac|kapat · /durum"
            )
    except Exception:
        pass

    offset = 0
    while True:
        try:
            res = _tg_call("getUpdates", {"offset": offset, "timeout": 25}, timeout=32)
            if not res.get("ok"):
                time.sleep(5)
                continue
            for upd in res.get("result", []):
                offset = max(offset, int(upd["update_id"]) + 1)
                msg = upd.get("message") or {}
                txt = str(msg.get("text") or "").strip()
                cid = str((msg.get("chat") or {}).get("id") or "").strip()
                if not txt or not cid:
                    continue
                with state_lock:
                    if cid not in subscribed_chats:
                        subscribed_chats.add(cid)
                        save_state()

                parts = txt.split()
                cmd = parts[0].split("@")[0].lower()
                arg1 = parts[1].upper() if len(parts) > 1 else ""

                if cmd in ("/start", "/help", "/menu"):
                    telegram_send_message(
                        f"⚡ <b>Spot Pump + Futures {auto_trade['leverage']}x Oto LONG Aktif</b>\n"
                        f"🆔 Sohbet ID: <code>{cid}</code>\n"
                        f"Komutlar:\n"
                        f"/24s · 24s en cok yukselenler\n"
                        f"/islemler · Acik & kapanan 3x islemler\n"
                        f"/otomatik ac | kapat · Oto 3x LONG kontrolu\n"
                        f"/sat SYMBOL · Acik islemi manuel kapat\n"
                        f"/durum · Tarama & Futures durumu\n"
                        f"/sifirla · 7dk/15dk kilitleri sifirla",
                        chat_id=cid,
                    )
                elif cmd == "/test":
                    telegram_send_message(
                        f"🧪 <b>TEST BILDIRIMI — 7/24 Aktif</b>\n"
                        f"🔎 Aktif Cift: {len(tickers)} | Futures Kontrat: {len(futures_filters)}",
                        chat_id=cid,
                    )
                elif cmd in ("/24s", "/piyasa"):
                    telegram_send_message(format_top24h_market_message(), chat_id=cid)
                elif cmd in ("/islemler", "/bakiye"):
                    telegram_send_message(format_trades_telegram_message(), chat_id=cid)
                elif cmd == "/otomatik":
                    if arg1 in ("AC", "ON"):
                        auto_trade["enabled"] = True
                    elif arg1 in ("KAPAT", "OFF"):
                        auto_trade["enabled"] = False
                    save_state()
                    telegram_send_message(
                        f"🤖 <b>Futures {auto_trade['leverage']}x Oto LONG:</b> {'🟢 AKTIF' if auto_trade['enabled'] else '🔴 PASIF'} ({auto_trade['mode']} · +{auto_trade['tp_pct']:.1f}% TP · {auto_trade['trade_usdt']:.0f} USDT)\n"
                        f"• Sadece 0-7dk Pump & 15dk Kirilim sinyallerinde islem acar.",
                        chat_id=cid,
                    )
                elif cmd == "/sat" and arg1:
                    with state_lock:
                        open_tr = next(
                            (t for t in trades if t.get("status") == "OPEN" and (t.get("symbol") == arg1 or t.get("base") == arg1)),
                            None,
                        )
                    if not open_tr:
                        telegram_send_message(f"ℹ️ {arg1} icin acik islem bulunamadi.", chat_id=cid)
                    else:
                        ok = close_futures_trade(open_tr, reason="MANUAL")
                        telegram_send_message(
                            f"✅ {open_tr['symbol']} piyasadan kapatildi." if ok else f"❌ {open_tr['symbol']} kapatilamadi.",
                            chat_id=cid,
                        )
                elif cmd == "/durum":
                    sum_d = get_trade_summary()
                    telegram_send_message(
                        f"📊 <b>Bot Durumu</b>\n"
                        f"• Spot Cift: {len(tickers)} | Futures Kontrat: {sum_d['futures_count']}\n"
                        f"• Kilit (7dk / 15dk): {len(locks['pump'])} / {len(locks['pump15m'])}\n"
                        f"• Oto Futures {auto_trade['leverage']}x: {'🟢 Acik' if auto_trade['enabled'] else '🔴 Kapali'} ({auto_trade['mode']} · Acik: {sum_d['open_count']} · K/Z: {'+' if sum_d['realized_pnl'] >= 0 else ''}${sum_d['realized_pnl']:.2f})",
                        chat_id=cid,
                    )
                elif cmd == "/sifirla":
                    with state_lock:
                        locks["pump"] = {}
                        locks["pump15m"] = {}
                    save_state()
                    telegram_send_message("🔓 Tum 7dk ve 15dk pump kilitleri sifirlandi.", chat_id=cid)
        except Exception:
            time.sleep(3)


# ==============================================================================
# CANLI PIYASA DONGULERI (WEBSOCKET + REST + WATCHDOG + HEALTH)
# ==============================================================================
def process_ticker(sym, price, chg24h, vol24h, now):
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
            "change24h": chg24h,
            "volume24h": vol24h,
            "updated_at": now,
        }
    evaluate_pump(sym, price, vol24h, now)
    evaluate_ath_atl(sym, price, vol24h, chg24h, now)


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
                            process_ticker(item.get("s", ""), c, chg, float(item.get("q") or 0), now)
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
    last_exch_sync = 0.0
    while True:
        try:
            now = time.time()
            if now - last_exch_sync >= 900:
                refresh_active_spot_symbols()
                refresh_futures_contracts()
                last_exch_sync = now

            raw = binance_rest_get("/api/v3/ticker/24hr", timeout=10)
            if isinstance(raw, list):
                now_ms = int(now * 1000)
                for item in raw:
                    bid = float(item.get("bidPrice") or 0)
                    ask = float(item.get("askPrice") or 0)
                    cnt = int(item.get("count") or 0)
                    close_ms = int(item.get("closeTime") or 0)
                    if bid <= 0 or ask <= 0 or cnt <= 0 or (close_ms > 0 and now_ms - close_ms > 1_800_000):
                        continue
                    process_ticker(
                        item.get("symbol", ""),
                        float(item.get("lastPrice") or 0),
                        float(item.get("priceChangePercent") or 0),
                        float(item.get("quoteVolume") or 0),
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
        if dt.hour == 21 and locks["reports"].get("victory_day") != today:
            locks["reports"]["victory_day"] = today
            save_state()
            telegram_send_message(build_victory_report(now))
        if dt.hour == 23 and locks["reports"].get("top24_day") != today:
            locks["reports"]["top24_day"] = today
            save_state()
            telegram_send_message(build_top24_report(now))
        time.sleep(15)


class ReusableTCPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class HealthHandler(http.server.BaseHTTPRequestHandler):
    def _send_health(self, head_only=False):
        sum_d = get_trade_summary()
        body = json.dumps({
            "ok": True,
            "ws": ws_connected,
            "pairs": len(tickers),
            "futures_pairs": sum_d["futures_count"],
            "auto_trade": {
                "enabled": auto_trade["enabled"],
                "mode": auto_trade["mode"],
                "leverage": auto_trade["leverage"],
                "tp_pct": auto_trade["tp_pct"],
                "open_trades": sum_d["open_count"],
            },
            "engines": ["PUMP", "PUMP15M", "ATL", "ATH"],
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def do_HEAD(self):
        self._send_health(head_only=True)

    def do_GET(self):
        self._send_health(head_only=False)

    def log_message(self, format, *args):
        return


def main():
    load_state()
    refresh_active_spot_symbols()
    refresh_futures_contracts()
    print(
        f"[Spot + Futures {auto_trade['leverage']}x Bot] Port={PORT}, Mode={auto_trade['mode']}, "
        f"AutoTrade={auto_trade['enabled']}, Futures={len(futures_filters)}"
    )

    workers = {
        "ws_stream": ws_stream_loop,
        "rest_seed": rest_seed_loop,
        "ath_atl_sync": ath_atl_sync_loop,
        "futures_tp_poll": poll_open_binance_trades_loop,
        "scheduler_news": scheduler_and_news_loop,
        "telegram_cmd": telegram_command_loop,
    }
    threads = {}
    for name, fn in workers.items():
        t = threading.Thread(target=fn, name=name, daemon=True)
        t.start()
        threads[name] = t

    def watchdog_loop():
        while True:
            for name, fn in workers.items():
                if not threads[name].is_alive():
                    t = threading.Thread(target=fn, name=name, daemon=True)
                    t.start()
                    threads[name] = t
            time.sleep(10)

    threading.Thread(target=watchdog_loop, name="watchdog", daemon=True).start()

    while True:
        try:
            srv = ReusableTCPServer(("0.0.0.0", PORT), HealthHandler)
            srv.serve_forever()
        except Exception as e:
            print(f"[HTTP Server Recovery] {e}")
            time.sleep(3)


if __name__ == "__main__":
    main()
