API_MIN_GAP = 0.25  # İstek aralığını biraz rahatlatın (saniyede max 4 istek)
_ticker_lock = threading.Lock()
_symbol_lock = threading.Lock()

KLINE_TTL_MAP = {
    "5m": 55,
    "15m": 180,
    "1h": 600,
    "4h": 1800,
    "1d": 3600,
    "3d": 3600,
}

def gj(url, cache_key=None, cache_ttl=0):
    global _last_api_call, _api_block_until

    if cache_key and cache_ttl:
        with _cache_lock:
            item = _kline_cache.get(cache_key)
            if item and time.time() - item[0] < cache_ttl:
                return item[1]

    with _api_lock:
        now = time.time()
        if now < _api_block_until:
            return None
        wait = API_MIN_GAP - (now - _last_api_call)
        if wait > 0:
            time.sleep(wait)
        _last_api_call = time.time()

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            used_weight = response.headers.get("X-MBX-USED-WEIGHT-1M")
            if used_weight and used_weight.isdigit() and int(used_weight) >= 1800:
                with _api_lock:
                    _api_block_until = max(_api_block_until, time.time() + 30)
                log("BINANCE", f"Yüksek API weight ({used_weight}/2400); 30s yavaşlatıldı.")
            result = json.loads(response.read().decode("utf-8"))

        if cache_key and cache_ttl:
            with _cache_lock:
                if len(_kline_cache) > 1500:
                    _kline_cache.clear()
                _kline_cache[cache_key] = (time.time(), result)
        return result

    except urllib.error.HTTPError as e:
        if e.code in (418, 429):
            retry_after = e.headers.get("Retry-After")
            try:
                # Binance'in verdiği gerçek ban süresine uy (1800 ile kesme!)
                pause = max(120, int(retry_after) + 5) if retry_after else 300
            except Exception:
                pause = 600
            with _api_lock:
                _api_block_until = max(_api_block_until, time.time() + pause)
            log("BINANCE", f"HTTP {e.code}; API istekleri {pause} saniye durduruldu.")
        else:
            log("BINANCE", f"HTTP {e.code}: {url.split('?')[0]}")
        return None
    except Exception as e:
        log("BINANCE", f"Bağlantı/veri hatası: {e}")
        return None
