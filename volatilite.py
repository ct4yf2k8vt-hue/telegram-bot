# ==============================================================================
# SPOT AKIS TERMINALI - SADECE KAPANAN ISLEMLER TELEGRAM BOTU (BAGIMSIZ)
# Uretim: 17:32:15 · Rev #2
# Durum: Ustteki tum stratejiler, sinyaller, raporlar ve acilis bildirimleri OFF.
#        Yalnizca kapanan islemler (TP / SL / Manuel) Telegram'a gonderilir.
#        "Tum Ayarlari Kaydet & bot.py Uret" butonundan tamamen bagimsizdir.
# ==============================================================================
import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Set, Tuple

# Ustteki tum strateji motorlari ve diger Telegram bildirimleri KAPALI (OFF)
STRATEGIES_ENABLED = False
SIGNAL_TELEGRAM_ENABLED = False
HOURLY_REPORT_ENABLED = False
DAILY_REPORT_ENABLED = False
TOP24_REPORT_ENABLED = False
TRADE_OPEN_TELEGRAM_ENABLED = False
NOTIFY_ONLY_CLOSED_TRADES = True


def _read_env_any(*keys: str, default: str = "") -> str:
    for k in keys:
        val = os.getenv(k, "").strip().strip('"').strip("'").strip()
        if val:
            return val
    return default


def _normalize_bot_token(raw: str) -> str:
    cleaned = raw.strip().strip('"').strip("'").strip()
    return re.sub(r"^bot(?=\d+:)", "", cleaned, flags=re.IGNORECASE)


def _derive_snapshot_url(raw_url: str) -> str:
    u = raw_url.strip().rstrip("/")
    if not u:
        return ""
    if u.endswith("/api/snapshot"):
        return u
    if u.endswith("/api/bot-config"):
        return u[: -len("/api/bot-config")] + "/api/snapshot"
    if "/api/" in u:
        return u.split("/api/")[0] + "/api/snapshot"
    return u + "/api/snapshot"


BOT_TOKEN = _normalize_bot_token(
    _read_env_any("BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN", "TG_BOT_TOKEN")
)
CHAT_ID_RAW = _read_env_any("CHAT_ID", "TELEGRAM_CHAT_ID", "TG_CHAT_ID", "TELEGRAM_CHAT_IDS", default="")
SUBSCRIBED_CHATS: Set[str] = set([c.strip() for c in re.split(r"[\s,;]+", CHAT_ID_RAW) if c.strip()])
PORT = int(os.getenv("PORT", "10000"))

BINANCE_API_KEY = _read_env_any("BINANCE_API_KEY")
BINANCE_API_SECRET = _read_env_any("BINANCE_API_SECRET")
BINANCE_TESTNET_API_KEY = _read_env_any("BINANCE_TESTNET_API_KEY") or BINANCE_API_KEY
BINANCE_TESTNET_API_SECRET = _read_env_any("BINANCE_TESTNET_API_SECRET") or BINANCE_API_SECRET

DEFAULT_WEB_SNAPSHOT_URL = "https://ais-dev-l2d5gisz6i6pqkmywxsfn7-618297779656.europe-west3.run.app/api/snapshot"
RAW_SYNC_URL = _read_env_any("WEB_SYNC_URL") or DEFAULT_WEB_SNAPSHOT_URL
WEB_SNAPSHOT_URL = _derive_snapshot_url(RAW_SYNC_URL)

POLL_INTERVAL_SEC = max(2.0, float(os.getenv("CLOSED_POLL_SEC", "4")))
SEEN_STATE_FILE = os.getenv("CLOSED_SEEN_FILE", "/tmp/spot_closed_only_tg_seen.json")
TR_TZ = timezone(timedelta(hours=3))

SEEN_LOCK = threading.Lock()
SEEN_IDS: Set[str] = set()
RECENT_CLOSED_CACHE: List[Dict[str, Any]] = []
BOT_STARTED_AT = time.time()
TG_SENT_COUNT = 0
LAST_ERROR = ""
BINANCE_TIME_OFFSET_MS: Dict[str, int] = {"LIVE": 0, "TESTNET": 0}


def _load_seen_state() -> None:
    try:
        if os.path.exists(SEEN_STATE_FILE):
            with open(SEEN_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                ids = data.get("seenIds", [])
                chats = data.get("chats", [])
                if isinstance(ids, list):
                    for item in ids[-5000:]:
                        if isinstance(item, str) and item:
                            SEEN_IDS.add(item)
                if isinstance(chats, list):
                    for cid in chats:
                        s = str(cid or "").strip()
                        if s:
                            SUBSCRIBED_CHATS.add(s)
    except Exception:
        pass


def _save_seen_state() -> None:
    try:
        with SEEN_LOCK:
            payload = {
                "seenIds": list(SEEN_IDS)[-5000:],
                "chats": list(SUBSCRIBED_CHATS),
                "updatedAt": int(time.time() * 1000),
            }
        with open(SEEN_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        pass


def _escape_html(text: Any) -> str:
    s = str(text if text is not None else "")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_price(val: float) -> str:
    if not val:
        return "0"
    if val >= 1000:
        return f"{val:,.2f}"
    if val >= 1:
        return f"{val:.4f}"
    if val >= 0.01:
        return f"{val:.6f}"
    return f"{val:.8f}"


def _fmt_duration(opened_ms: Optional[float], closed_ms: Optional[float]) -> str:
    if not opened_ms or not closed_ms or closed_ms <= opened_ms:
        return "-"
    total_sec = int((closed_ms - opened_ms) // 1000)
    if total_sec < 60:
        return f"{total_sec}sn"
    mins = total_sec // 60
    secs = total_sec % 60
    if mins < 60:
        return f"{mins}dk {secs}sn" if secs > 0 else f"{mins}dk"
    hrs = mins // 60
    rem_m = mins % 60
    return f"{hrs}sa {rem_m}dk" if rem_m > 0 else f"{hrs}sa"


def _http_json(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body_bytes: Optional[bytes] = None,
    timeout: float = 12.0,
) -> Any:
    req = urllib.request.Request(url, data=body_bytes, method=method)
    req.add_header("User-Agent", "SpotClosedTradesBot/1.0")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw)


def tg_send_html(html_text: str, explicit_chat_id: Optional[str] = None) -> bool:
    global TG_SENT_COUNT, LAST_ERROR
    if not BOT_TOKEN:
        LAST_ERROR = "BOT_TOKEN tanimli degil"
        return False
    targets = [explicit_chat_id] if explicit_chat_id else list(SUBSCRIBED_CHATS)
    if not targets:
        LAST_ERROR = "CHAT_ID / abone sohbet bulunamadi"
        return False
    any_ok = False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for cid in targets:
        try:
            payload = json.dumps(
                {
                    "chat_id": cid,
                    "text": html_text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }
            ).encode("utf-8")
            res = _http_json(
                url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body_bytes=payload,
                timeout=12.0,
            )
            if isinstance(res, dict) and res.get("ok"):
                TG_SENT_COUNT += 1
                LAST_ERROR = ""
                any_ok = True
            else:
                LAST_ERROR = str(res.get("description", "Telegram hatasi"))
        except Exception as err:
            LAST_ERROR = str(err)
    return any_ok


def format_closed_trade_html(tr: Dict[str, Any]) -> str:
    symbol = str(tr.get("symbol") or "")
    mode = str(tr.get("mode") or "PAPER")
    direction = "SHORT" if str(tr.get("direction") or "").upper() == "SHORT" else "LONG"
    leverage = int(tr.get("leverage") or 1)
    engine = str(tr.get("engine") or "FUTURES")
    reason = str(tr.get("closeReason") or "MANUAL").upper()
    entry_px = float(tr.get("entryPrice") or 0)
    exit_px = float(tr.get("exitPrice") or tr.get("currentPrice") or entry_px)
    pnl_usdt = float(tr.get("pnlUsdt") or 0)
    pnl_pct = float(tr.get("pnlPct") or 0)
    roe_pct = float(tr.get("roePct") or 0)
    tp_pct = tr.get("tpPct")
    sl_pct = tr.get("slPct")
    opened_at = tr.get("openedAt")
    closed_at = tr.get("closedAt") or int(time.time() * 1000)

    icon = "✅" if pnl_usdt >= 0 else "🛑"
    if reason == "TP":
        reason_label = f"+%{tp_pct} KÂR AL" if tp_pct is not None else "KÂR AL (TP)"
    elif reason == "SL":
        reason_label = f"-%{sl_pct} STOP LOSS" if sl_pct is not None else "STOP LOSS (SL)"
    else:
        reason_label = "KAPATMA"

    dur_str = _fmt_duration(opened_at, closed_at)
    pnl_sign = "+" if pnl_usdt >= 0 else ""
    pct_sign = "+" if pnl_pct >= 0 else ""
    roe_sign = "+" if roe_pct >= 0 else ""

    ts_tr = datetime.fromtimestamp(float(closed_at) / 1000.0, tz=TR_TZ).strftime("%H:%M:%S")
    return (
        f"{icon} <b>İŞLEM KAPANDI ({_escape_html(mode)}) — #{_escape_html(symbol)} {leverage}x {direction} ({_escape_html(reason_label)})</b>\n"
        f"• Strateji: {_escape_html(engine)} · Süre: {_escape_html(dur_str)} · Saat: {ts_tr}\n"
        f"• Giriş: ${_fmt_price(entry_px)} ➡️ Çıkış: ${_fmt_price(exit_px)}\n"
        f"• Net K/Z: <b>{pnl_sign}${pnl_usdt:.2f}</b> (Fiyat: {pct_sign}{pnl_pct:.2f}% · {leverage}x ROE: {roe_sign}{roe_pct:.2f}%)"
    )


def _sync_binance_time(mode: str) -> None:
    base_url = "https://testnet.binancefuture.com" if mode == "TESTNET" else "https://fapi.binance.com"
    try:
        res = _http_json(f"{base_url}/fapi/v1/time", timeout=8.0)
        srv_t = int(res.get("serverTime", 0))
        if srv_t > 0:
            BINANCE_TIME_OFFSET_MS[mode] = srv_t - int(time.time() * 1000)
    except Exception:
        pass


def _binance_signed_get(path: str, params: Dict[str, Any], mode: str = "LIVE") -> Any:
    key = BINANCE_TESTNET_API_KEY if mode == "TESTNET" else BINANCE_API_KEY
    secret = BINANCE_TESTNET_API_SECRET if mode == "TESTNET" else BINANCE_API_SECRET
    if not key or not secret:
        return None
    base_url = "https://testnet.binancefuture.com" if mode == "TESTNET" else "https://fapi.binance.com"
    for attempt in range(2):
        ts = int(time.time() * 1000) + BINANCE_TIME_OFFSET_MS.get(mode, 0)
        q = dict(params)
        q["timestamp"] = ts
        q["recvWindow"] = 10000
        qs = urllib.parse.urlencode(q)
        sig = hmac.new(secret.encode("utf-8"), qs.encode("utf-8"), hashlib.sha256).hexdigest()
        full_url = f"{base_url}{path}?{qs}&signature={sig}"
        try:
            return _http_json(full_url, headers={"X-MBX-APIKEY": key}, timeout=10.0)
        except urllib.error.HTTPError as http_err:
            body = http_err.read().decode("utf-8", errors="replace")
            if "-1021" in body and attempt == 0:
                _sync_binance_time(mode)
                continue
            raise RuntimeError(body)
    return None


def poll_web_snapshot_closed(bootstrap_only: bool = False) -> None:
    global LAST_ERROR
    if not WEB_SNAPSHOT_URL:
        return
    try:
        data = _http_json(WEB_SNAPSHOT_URL, timeout=12.0)
        if not isinstance(data, dict):
            return
        trades = data.get("trades")
        if not isinstance(trades, list):
            return
        closed_list = [t for t in trades if isinstance(t, dict) and t.get("status") == "CLOSED"]
        closed_list.sort(key=lambda x: float(x.get("closedAt") or x.get("openedAt") or 0))
        with SEEN_LOCK:
            RECENT_CLOSED_CACHE[:] = closed_list[-20:]
        added = False
        for tr in closed_list:
            tid = str(tr.get("id") or "")
            if not tid:
                continue
            with SEEN_LOCK:
                if tid in SEEN_IDS:
                    continue
                SEEN_IDS.add(tid)
                added = True
            if not bootstrap_only:
                tg_send_html(format_closed_trade_html(tr))
        if added:
            _save_seen_state()
        LAST_ERROR = ""
    except Exception as err:
        LAST_ERROR = f"Web snapshot: {err}"


def poll_binance_closed_income(mode: str, bootstrap_only: bool = False) -> None:
    global LAST_ERROR
    key = BINANCE_TESTNET_API_KEY if mode == "TESTNET" else BINANCE_API_KEY
    secret = BINANCE_TESTNET_API_SECRET if mode == "TESTNET" else BINANCE_API_SECRET
    if not key or not secret:
        return
    try:
        rows = _binance_signed_get(
            "/fapi/v1/income",
            {"incomeType": "REALIZED_PNL", "limit": 50},
            mode=mode,
        )
        if not isinstance(rows, list):
            return
        rows.sort(key=lambda r: float(r.get("time") or 0))
        added = False
        for row in rows:
            tran_id = str(row.get("tranId") or "")
            sym = str(row.get("symbol") or "")
            t_ms = int(row.get("time") or 0)
            income = float(row.get("income") or 0)
            uid = f"BN-{mode}-{tran_id or f'{sym}-{t_ms}-{income}'}"
            if abs(income) < 1e-8:
                continue
            with SEEN_LOCK:
                if uid in SEEN_IDS:
                    continue
                SEEN_IDS.add(uid)
                added = True
            if not bootstrap_only and not WEB_SNAPSHOT_URL:
                icon = "✅" if income >= 0 else "🛑"
                sign = "+" if income >= 0 else ""
                ts_tr = datetime.fromtimestamp(t_ms / 1000.0, tz=TR_TZ).strftime("%H:%M:%S")
                tg_send_html(
                    f"{icon} <b>İŞLEM KAPANDI (BINANCE {mode}) — #{_escape_html(sym)}</b>\n"
                    f"• Gerçekleşen K/Z: <b>{sign}${income:.4f} USDT</b> · Saat: {ts_tr}"
                )
        if added:
            _save_seen_state()
    except Exception as err:
        LAST_ERROR = f"Binance {mode}: {err}"


def closed_watcher_loop() -> None:
    _load_seen_state()
    if BINANCE_API_KEY and BINANCE_API_SECRET:
        _sync_binance_time("LIVE")
    if BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_API_SECRET:
        _sync_binance_time("TESTNET")

    # Ilk acilista mevcut eski kapali islemleri tekrar spam atmamak icin kaydet
    poll_web_snapshot_closed(bootstrap_only=True)
    poll_binance_closed_income("LIVE", bootstrap_only=True)
    if BINANCE_TESTNET_API_KEY != BINANCE_API_KEY:
        poll_binance_closed_income("TESTNET", bootstrap_only=True)

    while True:
        poll_web_snapshot_closed(bootstrap_only=False)
        poll_binance_closed_income("LIVE", bootstrap_only=False)
        if BINANCE_TESTNET_API_KEY != BINANCE_API_KEY:
            poll_binance_closed_income("TESTNET", bootstrap_only=False)
        time.sleep(POLL_INTERVAL_SEC)


def telegram_command_loop() -> None:
    if not BOT_TOKEN:
        return
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?timeout=25&offset={offset}"
            res = _http_json(url, timeout=32.0)
            if isinstance(res, dict) and res.get("ok"):
                for upd in res.get("result", []):
                    offset = max(offset, int(upd.get("update_id", 0)) + 1)
                    msg = upd.get("message") or {}
                    chat = msg.get("chat") or {}
                    cid = str(chat.get("id") or "").strip()
                    text = str(msg.get("text") or "").strip()
                    if cid and cid not in SUBSCRIBED_CHATS:
                        SUBSCRIBED_CHATS.add(cid)
                        _save_seen_state()
                    cmd = text.split()[0].lower() if text else ""
                    if cmd in ("/start", "/test", "/durum"):
                        tg_send_html(
                            f"⚡ <b>Sadece Kapanan İşlemler Telegram Botu Aktif</b>\n"
                            f"• Üst Strateji &amp; Sinyal Bildirimleri: <b>OFF</b>\n"
                            f"• Yalnız Kapanan İşlemler → Telegram: <b>AÇIK</b>\n"
                            f"• Gönderilen Toplam Kapanış: {TG_SENT_COUNT}",
                            explicit_chat_id=cid or None,
                        )
                    elif cmd == "/kapanan":
                        with SEEN_LOCK:
                            last5 = list(RECENT_CLOSED_CACHE)[-5:]
                        if not last5:
                            tg_send_html("ℹ️ Henüz kapanmış işlem kaydı yok.", explicit_chat_id=cid or None)
                        else:
                            for item in reversed(last5):
                                tg_send_html(format_closed_trade_html(item), explicit_chat_id=cid or None)
        except Exception:
            time.sleep(5.0)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        payload = {
            "ok": True,
            "mode": "CLOSED_TRADES_ONLY",
            "strategiesEnabled": False,
            "tgSentCount": TG_SENT_COUNT,
            "uptimeSec": int(time.time() - BOT_STARTED_AT),
            "lastError": LAST_ERROR,
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    threading.Thread(target=closed_watcher_loop, daemon=True).start()
    threading.Thread(target=telegram_command_loop, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"[ClosedTradesBot] Port {PORT} uzerinde yalnizca kapanan islemler icin aktif.")
    server.serve_forever()


if __name__ == "__main__":
    main()
