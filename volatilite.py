#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TOBO 1H LONG FORMASYON BOTU
- Yalnizca TOBO (Ters Omuz Bas Omuz) tarar.
- Yalnizca 1 saatlik kapanmis mumlari kullanir.
- Minimum formasyon yuksekligi: %1.2
- Sadece boyun cizgisi yukari kirildiginda LONG sinyali gonderir.
- Otomatik islem, pump, ATH/ATL, MA200, volatilite ve diger formasyonlar yoktur.
- Render icin aiohttp health endpoint'i 10000 portunda calisir.

Render Environment:
  BOT_TOKEN       = Telegram bot tokeniniz
  CHAT_ID         = Telegram chat ID (varsayilan asagidaki deger)
  PORT            = 10000 (Render otomatik verir)
  MAX_SCAN_SYMBOLS= 40 (istege bagli)
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from aiohttp import web


# =============================================================================
# AYARLAR
# =============================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "1623907197").strip()
PORT = int(os.getenv("PORT", "10000"))

TIMEFRAME = "1h"
KLINE_LIMIT = 100
MIN_FORMATION_HEIGHT_PCT = 1.2
MAX_SHOULDER_DIFFERENCE_PCT = 3.0
MIN_PIVOT_DISTANCE = 5
MAX_PIVOT_DISTANCE = 35
MAX_RIGHT_SHOULDER_AGE = 20
MAX_SCAN_SYMBOLS = max(10, min(100, int(os.getenv("MAX_SCAN_SYMBOLS", "40"))))
SCAN_INTERVAL_SECONDS = 300
CONCURRENT_REQUESTS = 8
MIN_24H_QUOTE_VOLUME = 500_000
STATE_FILE = "/tmp/tobo_1h_bot_state.json"

BINANCE_BASES = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
]

TR_TZ = timezone(timedelta(hours=3))
SEEN_SIGNALS: set = set()
ACTIVE_SYMBOLS: set = set()
LAST_SCAN = 0.0
LAST_ERROR = ""
SCAN_LOCK = asyncio.Lock()


# =============================================================================
# DURUM KAYDI
# =============================================================================
def load_state() -> None:
    global SEEN_SIGNALS
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        SEEN_SIGNALS = set(str(x) for x in data.get("seenSignals", []))
    except Exception:
        SEEN_SIGNALS = set()


def save_state() -> None:
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"seenSignals": list(SEEN_SIGNALS)[-2000:]}, f)
        os.replace(tmp, STATE_FILE)
    except Exception as exc:
        print(f"[STATE] Kayit hatasi: {exc}", flush=True)


# =============================================================================
# BINANCE ERISIMI
# =============================================================================
async def binance_json(
    session: aiohttp.ClientSession,
    path: str,
    params: Optional[Dict[str, Any]] = None,
) -> Any:
    last_error = None
    for base in BINANCE_BASES:
        try:
            async with session.get(
                base + path,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status == 200:
                    return await response.json()
                body = await response.text()
                last_error = f"HTTP {response.status}: {body[:180]}"
                if response.status in (418, 429):
                    raise RuntimeError(last_error)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_error = str(exc)
        except RuntimeError:
            raise
    raise RuntimeError(last_error or "Binance API erisimi basarisiz")


async def get_spot_symbols(session: aiohttp.ClientSession) -> set:
    data = await binance_json(session, "/api/v3/exchangeInfo")
    symbols = set()
    for item in data.get("symbols", []):
        symbol = str(item.get("symbol", ""))
        if (
            item.get("status") == "TRADING"
            and item.get("quoteAsset") == "USDT"
            and item.get("isSpotTradingAllowed", True)
            and not symbol.endswith(("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"))
        ):
            symbols.add(symbol)
    return symbols


async def get_top_symbols(session: aiohttp.ClientSession) -> List[str]:
    global ACTIVE_SYMBOLS
    if not ACTIVE_SYMBOLS:
        ACTIVE_SYMBOLS = await get_spot_symbols(session)

    tickers = await binance_json(session, "/api/v3/ticker/24hr")
    ranked = []
    for item in tickers:
        symbol = str(item.get("symbol", ""))
        if symbol not in ACTIVE_SYMBOLS:
            continue
        try:
            quote_volume = float(item.get("quoteVolume") or 0)
            price = float(item.get("lastPrice") or 0)
        except (TypeError, ValueError):
            continue
        if price > 0 and quote_volume >= MIN_24H_QUOTE_VOLUME:
            ranked.append((quote_volume, symbol))
    ranked.sort(reverse=True)
    return [symbol for _, symbol in ranked[:MAX_SCAN_SYMBOLS]]


async def get_hourly_klines(
    session: aiohttp.ClientSession,
    symbol: str,
) -> List[Dict[str, float]]:
    raw = await binance_json(
        session,
        "/api/v3/klines",
        {"symbol": symbol, "interval": TIMEFRAME, "limit": KLINE_LIMIT},
    )
    if not isinstance(raw, list) or len(raw) < 50:
        return []

    # Binance son elemani halen acik olan saatlik mumdur.
    # Yalnizca kapanmis mumlar formasyon analizine girer.
    closed = raw[:-1]
    result = []
    for candle in closed:
        result.append({
            "open_time": int(candle[0]),
            "open": float(candle[1]),
            "high": float(candle[2]),
            "low": float(candle[3]),
            "close": float(candle[4]),
            "volume": float(candle[5]),
            "close_time": int(candle[6]),
        })
    return result


# =============================================================================
# TOBO TESPITI
# =============================================================================
def find_pivot_lows(
    bars: List[Dict[str, float]],
    left_right: int = 3,
) -> List[Tuple[int, float]]:
    pivots = []
    for i in range(left_right, len(bars) - left_right):
        value = bars[i]["low"]
        left_ok = all(bars[j]["low"] > value for j in range(i - left_right, i))
        right_ok = all(bars[j]["low"] > value for j in range(i + 1, i + left_right + 1))
        if left_ok and right_ok:
            pivots.append((i, value))
    return pivots


def detect_tobo(bars: List[Dict[str, float]]) -> Optional[Dict[str, Any]]:
    """
    TOBO:
      - Uc pivot dip: sol omuz, bas, sag omuz
      - Bas iki omuzdan belirgin sekilde asagida
      - Omuzlar arasi fark en fazla %3
      - Boyun cizgisi kirilimi kapanmis mumla teyit edilir
      - Formasyon yuksekligi en az %1.2
    """
    if len(bars) < 45:
        return None

    pivots = find_pivot_lows(bars[-85:], 3)
    if len(pivots) < 3:
        return None

    offset = max(0, len(bars) - 85)
    pivots = [(i + offset, price) for i, price in pivots]

    # En guncel olasi omuz dizilimlerinden baslayarak kontrol et.
    for k in range(len(pivots) - 3, -1, -1):
        left_shoulder = pivots[k]
        head = pivots[k + 1]
        right_shoulder = pivots[k + 2]

        ls_i, ls_p = left_shoulder
        hd_i, hd_p = head
        rs_i, rs_p = right_shoulder

        if not (MIN_PIVOT_DISTANCE <= hd_i - ls_i <= MAX_PIVOT_DISTANCE):
            continue
        if not (MIN_PIVOT_DISTANCE <= rs_i - hd_i <= MAX_PIVOT_DISTANCE):
            continue
        if len(bars) - 1 - rs_i > MAX_RIGHT_SHOULDER_AGE:
            continue

        shoulder_diff = abs(ls_p - rs_p) / max(ls_p, rs_p) * 100.0
        if shoulder_diff > MAX_SHOULDER_DIFFERENCE_PCT:
            continue

        # Bas, iki omuzdan da en az %0.6 daha asagida olmali.
        if not (hd_p < ls_p * 0.994 and hd_p < rs_p * 0.994):
            continue

        # Boyun cizgisinin iki omuz arasindaki tepe noktalarini bul.
        left_peak = max(b["high"] for b in bars[ls_i + 1:hd_i])
        right_peak = max(b["high"] for b in bars[hd_i + 1:rs_i])
        if left_peak <= 0 or right_peak <= 0:
            continue

        # Egimli boyun cizgisi: sag omuzdan sonraki mum konumuna dogru uzatilir.
        left_peak_i = max(
            range(ls_i + 1, hd_i),
            key=lambda idx: bars[idx]["high"],
        )
        right_peak_i = max(
            range(hd_i + 1, rs_i),
            key=lambda idx: bars[idx]["high"],
        )
        if right_peak_i == left_peak_i:
            neckline_now = max(left_peak, right_peak)
        else:
            slope = (right_peak - left_peak) / (right_peak_i - left_peak_i)
            neckline_now = right_peak + slope * (len(bars) - 1 - right_peak_i)

        # Formasyonun minimum yuksekligi boyun cizgisine gore hesaplanir.
        formation_height = (min(left_peak, right_peak) - hd_p) / hd_p * 100.0
        if formation_height < MIN_FORMATION_HEIGHT_PCT:
            continue

        if neckline_now <= hd_p:
            continue

        previous_close = bars[-2]["close"]
        last_close = bars[-1]["close"]

        # Sinyal yalnizca son kapanmis mum boyun cizgisini asagi taraftan
        # yukari kesti ise uretilir.
        if previous_close <= neckline_now and last_close > neckline_now:
            return {
                "left_shoulder": ls_p,
                "head": hd_p,
                "right_shoulder": rs_p,
                "neckline": neckline_now,
                "height_pct": formation_height,
                "shoulder_diff_pct": shoulder_diff,
                "breakout_price": last_close,
                "breakout_time": bars[-1]["close_time"],
                "left_time": bars[ls_i]["open_time"],
                "head_time": bars[hd_i]["open_time"],
                "right_time": bars[rs_i]["open_time"],
                "target": last_close + (neckline_now - hd_p),
            }

    return None


# =============================================================================
# TELEGRAM
# =============================================================================
def fmt_price(value: float) -> str:
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if value >= 0.01:
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return f"{value:.8f}".rstrip("0").rstrip(".")


async def telegram_send(session: aiohttp.ClientSession, message: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("[TELEGRAM] BOT_TOKEN veya CHAT_ID eksik.", flush=True)
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        async with session.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            data = await response.json()
            if not data.get("ok"):
                print(f"[TELEGRAM] Gonderim hatasi: {data}", flush=True)
                return False
            return True
    except Exception as exc:
        print(f"[TELEGRAM] Baglanti hatasi: {exc}", flush=True)
        return False


async def send_tobo_signal(
    session: aiohttp.ClientSession,
    symbol: str,
    hit: Dict[str, Any],
) -> None:
    close_dt = datetime.fromtimestamp(
        hit["breakout_time"] / 1000,
        tz=timezone.utc,
    ).astimezone(TR_TZ)

    message = (
        "🟢 <b>1 SAATLİK TOBO — LONG SİNYALİ</b>\n\n"
        f"🪙 <b>Coin:</b> #{symbol}\n"
        f"⏱ <b>Zaman dilimi:</b> 1H\n"
        f"💰 <b>Kırılım fiyatı:</b> ${fmt_price(hit['breakout_price'])}\n"
        f"📏 <b>Formasyon yüksekliği:</b> %{hit['height_pct']:.2f}\n"
        f"〰️ <b>Boyun çizgisi:</b> ${fmt_price(hit['neckline'])}\n\n"
        f"👈 <b>Sol omuz:</b> ${fmt_price(hit['left_shoulder'])}\n"
        f"🧠 <b>Baş:</b> ${fmt_price(hit['head'])}\n"
        f"👉 <b>Sağ omuz:</b> ${fmt_price(hit['right_shoulder'])}\n"
        f"📐 <b>Omuz farkı:</b> %{hit['shoulder_diff_pct']:.2f}\n\n"
        f"🎯 <b>Teorik hedef:</b> ${fmt_price(hit['target'])}\n"
        f"🕒 <b>Mum kapanışı:</b> {close_dt.strftime('%d.%m.%Y %H:%M')} TR\n\n"
        "✅ Boyun çizgisi yukarı kırıldı; 1 saatlik kapanışla teyit edildi.\n"
        "<i>Bu bir işlem emri değil, teknik analiz sinyalidir.</i>"
    )
    await telegram_send(session, message)


# =============================================================================
# TARAMA DONGUSU
# =============================================================================
async def scan_symbol(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    symbol: str,
) -> None:
    async with semaphore:
        try:
            bars = await get_hourly_klines(session, symbol)
            if not bars:
                return

            hit = detect_tobo(bars)
            if not hit:
                return

            # Ayni sembol ve ayni TOBO omuz dizilimi icin tek sinyal.
            signal_id = (
                f"{symbol}:{hit['left_time']}:"
                f"{hit['head_time']}:{hit['right_time']}"
            )
            if signal_id in SEEN_SIGNALS:
                return

            # Once kaydet: gecici Telegram hatasinda ayni formasyonun
            # arka arkaya mesaj yagdirilmasini engeller.
            SEEN_SIGNALS.add(signal_id)
            save_state()

            print(
                f"[TOBO] {symbol} | 1H LONG | "
                f"yukseklik=%{hit['height_pct']:.2f}",
                flush=True,
            )
            await send_tobo_signal(session, symbol, hit)

        except Exception as exc:
            print(f"[SCAN] {symbol}: {exc}", flush=True)


async def scanner_loop(session: aiohttp.ClientSession) -> None:
    global LAST_SCAN, LAST_ERROR
    semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)

    while True:
        started = time.time()
        try:
            async with SCAN_LOCK:
                symbols = await get_top_symbols(session)
                if not symbols:
                    raise RuntimeError("Taranacak uygun USDT spot paritesi bulunamadi")

                print(
                    f"[SCAN] {len(symbols)} parite taraniyor. "
                    f"Zaman dilimi={TIMEFRAME}, formasyon=TOBO, yon=LONG",
                    flush=True,
                )
                await asyncio.gather(
                    *(scan_symbol(session, semaphore, symbol) for symbol in symbols)
                )

                LAST_SCAN = time.time()
                LAST_ERROR = ""
                print(
                    f"[SCAN] Tamamlandi: {len(symbols)} parite, "
                    f"{time.time() - started:.1f} saniye",
                    flush=True,
                )

        except Exception as exc:
            LAST_ERROR = str(exc)
            print(f"[SCAN HATASI] {LAST_ERROR}", flush=True)
            if "418" in LAST_ERROR or "429" in LAST_ERROR:
                await asyncio.sleep(60)

        elapsed = time.time() - started
        await asyncio.sleep(max(10, SCAN_INTERVAL_SECONDS - elapsed))


# =============================================================================
# RENDER WEB SUNUCUSU
# =============================================================================
async def health(_request: web.Request) -> web.Response:
    return web.json_response({
        "ok": True,
        "bot": "TOBO 1H LONG",
        "timeframe": TIMEFRAME,
        "pattern": "TOBO",
        "direction": "LONG",
        "minimumFormationHeightPct": MIN_FORMATION_HEIGHT_PCT,
        "maxScanSymbols": MAX_SCAN_SYMBOLS,
        "lastScan": int(LAST_SCAN) if LAST_SCAN else None,
        "lastError": LAST_ERROR,
        "telegramConfigured": bool(BOT_TOKEN and CHAT_ID),
    })


async def index(_request: web.Request) -> web.Response:
    return web.Response(
        text="OK - TOBO 1H LONG Bot Running",
        content_type="text/plain",
    )


async def main() -> None:
    load_state()

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    print(
        f"[START] TOBO 1H LONG bot aktif. Port={PORT} | "
        f"Minimum yukseklik=%{MIN_FORMATION_HEIGHT_PCT:.1f}",
        flush=True,
    )

    connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        headers={"User-Agent": "TOBO-1H-LONG-Bot/1.0"},
    ) as session:
        await telegram_send(
            session,
            "🟢 <b>TOBO 1H LONG BOT AKTİF</b>\n"
            "Yalnızca TOBO formasyonu taranıyor.\n"
            "Minimum formasyon yüksekliği: <b>%1.2</b>\n"
            "Sinyal: kapanmış 1 saatlik mumda boyun çizgisi yukarı kırılımı.",
        )
        await scanner_loop(session)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot durduruldu.", flush=True)
