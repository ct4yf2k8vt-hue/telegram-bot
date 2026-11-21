def dislanmis(s):
    return s.startswith(STABIL_ONEKLER) or s.endswith(KALDIRACLI_SONEKLER)

class WSFeed:
    def __init__(self, ad, adresler):
        self.ad = ad; self.adresler = adresler; self.veri = {}
        self.son = 0.0; self.bagli = False; self.son_hata = ""
        self.idx = 0; self.dinleyici = None

    @staticmethod
    def _recv_exact(sock, buf, n):
        while len(buf) < n:
            chunk = sock.recv(65536)
            if not chunk: raise EOFError("WS kapandı")
            buf.extend(chunk)
        out = bytes(buf[:n]); del buf[:n]; return out

    @staticmethod
    def _send(sock, data, opcode):
        mask = os.urandom(4); n = len(data)
        head = bytearray([0x80 | opcode])
        if n < 126: head.append(0x80 | n)
        elif n < 65536: head.append(0x80 | 126); head.extend(struct.pack("!H", n))
        else: head.append(0x80 | 127); head.extend(struct.pack("!Q", n))
        head.extend(mask)
        sock.sendall(bytes(head) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _mesaj(self, sock, buf):
        parca = bytearray()
        while True:
            b1, b2 = self._recv_exact(sock, buf, 2)
            fin = b1 & 0x80; op = b1 & 0x0F; ln = b2 & 0x7F
            if ln == 126: ln = struct.unpack("!H", self._recv_exact(sock, buf, 2))[0]
            elif ln == 127: ln = struct.unpack("!Q", self._recv_exact(sock, buf, 8))[0]
            if b2 & 0x80: self._recv_exact(sock, buf, 4)
            payload = self._recv_exact(sock, buf, ln)
            if op == 0x8: raise EOFError("WS close")
            if op == 0x9: self._send(sock, payload, 0xA); continue
            if op == 0xA: continue
            if op in (0x1, 0x2): parca = bytearray(payload)
            elif op == 0x0: parca.extend(payload)
            if fin: return bytes(parca)

    def _baglan(self):
        host, port, path = self.adresler[self.idx % len(self.adresler)]
        ctx = ssl.create_default_context()
        sock = socket.create_connection((host, port), timeout=15)
        ssock = ctx.wrap_socket(sock, server_hostname=host); ssock.settimeout(45)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: Mozilla/5.0\r\n\r\n")
        ssock.sendall(req.encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = ssock.recv(4096)
            if not chunk: raise EOFError("handshake kapandı")
            buf += chunk
        ilk = buf.split(b"\r\n", 1)[0].decode("utf-8", "ignore")
        if " 101" not in ilk:
            ssock.close(); raise RuntimeError(f"handshake red: {ilk}")
        return ssock, bytearray(buf.split(b"\r\n\r\n", 1)[1])

    def calis(self):
        while True:
            ssock = None
            try:
                ssock, buf = self._baglan()
                self.bagli = True; self.son_hata = ""; self.son = time.time()
                log("WS", f"{self.ad} bağlandı.")
                while True:
                    ham = self._mesaj(ssock, buf)
                    arr = json.loads(ham.decode("utf-8"))
                    if isinstance(arr, dict) and "data" in arr: arr = arr["data"]
                    if not isinstance(arr, list): continue
                    now = time.time()
                    for it in arr:
                        try: self.veri[it["s"]] = (float(it["c"]), float(it["o"]), float(it["q"]), now)
                        except Exception: continue
                    self.son = now
                    if self.dinleyici:
                        try: self.dinleyici(self, arr, now)
                        except Exception as e: log("WS", f"dinleyici: {e}")
            except Exception as e:
                self.bagli = False
                self.son_hata = f"{type(e).__name__}: {e}"[:120]
                log("WS", f"{self.ad} koptu: {self.son_hata}")
                self.idx += 1
                time.sleep(5)
            finally:
                try:
                    if ssock: ssock.close()
                except Exception: pass

FUT = WSFeed("Futures", [("fstream.binance.com", 443, "/market/ws/!miniTicker@arr"),
                         ("fstream.binance.com", 443, "/ws/!miniTicker@arr")])
SPOT = WSFeed("Spot", [("stream.binance.com", 9443, "/ws/!miniTicker@arr"),
                       ("data-stream.binance.com", 9443, "/ws/!miniTicker@arr")])

def besleme_taze(f): return f.bagli and time.time() - f.son < 25
def aktif_besleme():
    if besleme_taze(FUT): return FUT
    if besleme_taze(SPOT): return SPOT
    return None
def pump_besleme():
    if besleme_taze(SPOT): return SPOT
    if besleme_taze(FUT): return FUT
    return None

def ticker_listesi(futures):
    sirali = (FUT, SPOT) if futures else (SPOT, FUT)
    now = time.time()
    for f in sirali:
        if besleme_taze(f) and len(f.veri) > 50:
            out = []
            for s, (c, o, q, t) in list(f.veri.items()):
                if now - t > 10800 or o <= 0: continue
                if s in YASAKLI_HISSELER or dislanmis(s): continue
                out.append({"symbol": s, "lastPrice": c, "quoteVolume": q, "priceChangePercent": (c - o) / o * 100})
            return out
    return None

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
    " ": [0x00, 0x00, 0x00, 0x00, 0x00], "|": [0x00, 0x00, 0x7F, 0x00, 0x00],
}

def render_png(baslik, candles, live_price, start_price, zaman=None):
    W, H = 860, 440
    BG = (9, 13, 22); GRID = (28, 38, 56)
    BULL = (16, 185, 129); BEAR = (244, 63, 94)
    TEXT = (226, 232, 240); MUTED = (148, 163, 184)
    START_COL = (245, 158, 11); LIVE_COL = (56, 189, 248)

    bars = []
    for b in candles[-75:]:
        try:
            if b["high"] >= b["low"] > 0 and b["close"] > 0 and b["open"] > 0:
                bars.append(dict(b))
        except (KeyError, TypeError): continue
    if not bars: return b""
    if live_price is None or live_price <= 0 or math.isnan(live_price) or math.isinf(live_price):
        live_price = bars[-1]["close"]
    last = bars[-1]
    last["close"] = live_price
    last["high"] = max(last["high"], live_price)
    last["low"] = min(last["low"], live_price)

    buf = bytearray(bytes(BG) * (W * H))

    def set_px(x, y, col):
        if 0 <= x < W and 0 <= y < H:
            i = (y * W + x) * 3
            buf[i] = col[0]; buf[i + 1] = col[1]; buf[i + 2] = col[2]

    def fill_rect(x0, y0, x1, y1, col):
        x0, x1 = max(0, min(x0, x1)), min(W - 1, max(x0, x1))
        y0, y1 = max(0, min(y0, y1)), min(H - 1, max(y0, y1))
        if x1 < x0 or y1 < y0: return
        row = bytes(col) * (x1 - x0 + 1)
        for y in range(y0, y1 + 1):
            st = (y * W + x0) * 3
            buf[st:st + len(row)] = row

    def draw_line(x0, y0, x1, y1, col, dashed=False):
        dx = abs(x1 - x0); dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1; sy = 1 if y0 < y1 else -1
        err = dx + dy; n = 0
        while True:
            if not dashed or (n % 8) < 5: set_px(x0, y0, col)
            if x0 == x1 and y0 == y1: break
            e2 = 2 * err
            if e2 >= dy: err += dy; x0 += sx
            if e2 <= dx: err += dx; y0 += sy
            n += 1

    def draw_text(x, y, text, col, scale=1):
        cx = x
        for ch in text.upper():
            g = FONT_5X7.get(ch, FONT_5X7[" "])
            for gx in range(5):
                for gy in range(7):
                    if g[gx] & (1 << gy):
                        fill_rect(cx + gx * scale, y + gy * scale,
                                  cx + gx * scale + scale - 1, y + gy * scale + scale - 1, col)
            cx += 6 * scale

    pad_l, pad_r, pad_t, pad_b = 18, 118, 48, 24
    vol_h, gap = 62, 12
    plot_w = W - pad_l - pad_r
    price_h = H - pad_t - pad_b - vol_h - gap

    allp = [b["high"] for b in bars] + [b["low"] for b in bars] + [live_price]
    if start_price and start_price > 0: allp.append(start_price)
    lo_p = min(allp) * 0.994
    hi_p = max(allp) * 1.006
    rng_p = max(hi_p - lo_p, lo_p * 0.005)
    max_vol = max([b["volume"] for b in bars] + [1.0])

    def y_of(p): return int(pad_t + ((hi_p - p) / rng_p) * price_h)

    draw_text(pad_l, 14, baslik, TEXT, scale=2)
    if zaman: draw_text(pad_l, 34, zaman, MUTED, scale=1)

    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = int(pad_t + price_h * frac)
        draw_line(pad_l, gy, pad_l + plot_w, gy, GRID, dashed=True)
        draw_text(pad_l + plot_w + 8, gy - 3, fmt_price(hi_p - rng_p * frac), MUTED)

    if start_price and start_price > 0:
        sy = y_of(start_price)
        if pad_t <= sy <= pad_t + price_h:
            draw_line(pad_l, sy, pad_l + plot_w, sy, START_COL, dashed=True)
            draw_text(pad_l + plot_w + 8, sy - 3, "BAS " + fmt_price(start_price), START_COL)

    n = len(bars); step = plot_w / max(1, n); bw = max(2, int(step * 0.62))
    vol_top = pad_t + price_h + gap
    for i, b in enumerate(bars):
        cx = int(pad_l + (i + 0.5) * step)
        col = BULL if b["close"] >= b["open"] else BEAR
        yo, yc, yh, yl = y_of(b["open"]), y_of(b["close"]), y_of(b["high"]), y_of(b["low"])
        draw_line(cx, yh, cx, yl, col)
        fill_rect(cx - bw // 2, min(yo, yc), cx + bw // 2,
                  max(min(yo, yc) + 1, max(yo, yc)), col)
        vh = max(1, int((b["volume"] / max_vol) * vol_h))
        fill_rect(cx - bw // 2, vol_top + vol_h - vh, cx + bw // 2, vol_top + vol_h,
                  (col[0] // 2, col[1] // 2, col[2] // 2))

    ly = y_of(live_price)
    draw_line(pad_l, ly, pad_l + plot_w, ly, LIVE_COL, dashed=True)
    fill_rect(pad_l + plot_w + 4, ly - 7, W - 4, ly + 7, (2, 132, 199))
    draw_text(pad_l + plot_w + 8, ly - 3, fmt_price(live_price), (248, 250, 252))

    raw = bytearray()
    stride = W * 3
    for y in range(H):
        raw.append(0)
        raw.extend(buf[y * stride:(y + 1) * stride])
    comp = zlib.compress(bytes(raw), 6)

    def chunk(tag, payload):
        return struct.pack("!I", len(payload)) + tag + payload + struct.pack("!I", zlib.crc32(tag + payload) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack("!IIBBBBB", W, H, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", comp)
            + chunk(b"IEND", b""))

# =====================================================
# PUMP / DUMP (ATR + EWMA + CANLI FİYAT)
# =====================================================
PUMP_MIN_HACIM = 2_000_000
PUMP_PENCERELER = ((420, 4.5),)
PUMP_MIN_CARPAN = 2.0
PUMP_MIN_SON5 = 25_000
PUMP_MIN_Z = 2.0

pump_gecmis = {}
pump_son_ornek = {}
pump_cd = {}
pump_aktif = set()
pump_kilit = threading.Lock()
pump_sem = threading.Semaphore(3)
pump_durum = {"feed": None}
toplu = {"t": [], "sessiz": 0.0}
sayac = {"PUMP": 0, "DUMP": 0}

def toplu_kontrol(now):
    if now < toplu["sessiz"]: return True
    toplu["t"] = [x for x in toplu["t"] if now - x < 120] + [now]
    if len(toplu["t"]) > 6:
        toplu["sessiz"] = now + 300
        log("PUMP", "Piyasa genelinde sert hareket: 5 dk durduruldu")
        return True
    return False

def pump_isle(feed, arr, now):
    if pump_besleme() is not feed: return
    if pump_durum["feed"] is not feed:
        pump_durum["feed"] = feed
        pump_gecmis.clear(); pump_son_ornek.clear()
        return
    for it in arr:
        try:
            s = it["s"]
            if not s.endswith("USDT") or s in YASAKLI_HISSELER or dislanmis(s): continue
            q = float(it["q"])
            if q < PUMP_MIN_HACIM: continue
            if now - pump_son_ornek.get(s, 0) < 5: continue
            p = float(it["c"]); o = float(it["o"])
            if p <= 0: continue
            pump_son_ornek[s] = now
            h = pump_gecmis.setdefault(s, [])
            h.append((now, p))
            kes = 0
            while kes < len(h) and h[kes][0] < now - 1200: kes += 1
            if kes: del h[:kes]
            if len(h) < 8 or h[0][0] > now - 90: continue
            pump_kontrol(feed, s, p, o, q, h, now)
        except Exception: continue

def pump_kontrol(feed, s, p, o, q, h, now):
    for pencere, esik in PUMP_PENCERELER:
        pts = [x[1] for x in h if x[0] >= now - pencere]
        if len(pts) < 4: continue
        lo, hi = min(pts), max(pts)
        if lo <= 0 or hi <= 0: continue
        up = (p - lo) / lo * 100
        if up >= esik and p >= hi * 0.985: yon = "PUMP"
        else: continue
        key = (s, yon)
        with pump_kilit:
            if key in pump_aktif or now < pump_cd.get(key, 0): return
            pump_aktif.add(key)
        deg24 = (p - o) / o * 100 if o > 0 else 0.0
        threading.Thread(target=pump_dogrula,
                         args=(feed.ad, s, yon, p, pencere, esik, q, deg24),
                         daemon=True).start()
        return

def pump_dogrula(feed_ad, s, yon, p, pencere, esik, q24, deg24):
    key = (s, yon)
    bitis = time.time() + 300
    alindi = pump_sem.acquire(timeout=30)
    try:
        if not alindi: return
        fut = feed_ad == "Futures"
        url = (f"{FAPI}/fapi/v1/klines?symbol={s}&interval=1m&limit=45" if fut
               else f"{BASE}/api/v3/klines?symbol={s}&interval=1m&limit=45")
        kl = gj(url)
        if not isinstance(kl, list) or len(kl) < 25:
            log("PUMP", f"{s} {yon}: mum verisi alınamadı"); return

        feed = FUT if fut else SPOT
        if feed.bagli and s in feed.veri:
            cp, _, _, ct = feed.veri[s]
            if time.time() - ct < 30 and cp > 0:
                p = cp

        zirveler = [float(k[2]) for k in kl]
        dipler = [float(k[3]) for k in kl]
        hacimler = [float(k[7]) for k in kl]
        up = yon == "PUMP"
        n_son = pencere // 60 + 1
        if up:
            ref = min(dipler[-n_son:]); kp = (p - ref) / ref * 100
        else:
            ref = max(zirveler[-n_son:]); kp = (ref - p) / ref * 100

        atr = atr_pct(kl, 14) or 1.5
        dinamik_esik = max(esik, atr * 1.2)
        if kp < dinamik_esik * 0.9:
            log("PUMP", f"{s} {yon}: mum teyidi yok ({kp:.2f}% / gerekli {dinamik_esik:.2f}%)")
            return

        son5 = sum(hacimler[-5:])
        onceki = hacimler[:-5]
        if len(onceki) < 10:
            log("PUMP", f"{s} {yon}: yeterli hacim geçmişi yok"); return
        ort, std = ewma_stats(onceki, alpha=0.15)
        son5_avg = son5 / 5
        if ort <= 0: return
        carpan = son5_avg / ort
        z = (son5_avg - ort) / std if std > 0 else 0.0
        if carpan < PUMP_MIN_CARPAN or son5 < PUMP_MIN_SON5:
            log("PUMP", f"{s} {yon}: hacim oranı yetersiz ({carpan:.1f}x)"); return
        if z < PUMP_MIN_Z:
            log("PUMP", f"{s} {yon}: z-skoru düşük ({z:.1f})"); return

        bitis = time.time() + (PUMP_TEKRAR_SAAT * 3600 if PUMP_TEKRAR_SAAT > 0 else 10 ** 9)
        sayac[yon] += 1
        if toplu_kontrol(time.time()): return

        guc_raw = (min(4, kp / max(dinamik_esik, 0.1)) +
                   min(3, carpan / 2) +
                   min(2, z / 2) +
                   min(1, atr / 3))
        guc = max(1, min(10, round(guc_raw + 1)))

        kaynak = feed_ad.upper()
        tur = "PUMP"
        msg = (f"⚡ <b>{kaynak} ANLIK {tur} — #{s}</b>\n\n"
               f"📈 Değişim: <b>+{kp:.2f}%</b> ({pencere // 60} dk)\n"
               f"💰 Başlangıç: {fmt_price(ref)} ➡️ Anlık: <b>{fmt_price(p)}</b>\n"
               f"💧 24s Hacim: {q24:,.0f} USDT\n"
               f"🔥 Hacim: <b>{carpan:.1f}x</b> (z={z:.1f})\n"
               f"📊 ATR(14): {atr:.2f}% → Dinamik eşik: {dinamik_esik:.2f}%\n"
               f"⭐ Sinyal gücü: <b>{guc}/10</b>")

        mumlar = [{"openTime": int(k[0]) // 1000, "open": float(k[1]), "high": float(k[2]),
                   "low": float(k[3]), "close": float(k[4]), "volume": float(k[7])} for k in kl]
        try:
            png = render_png(f"{s} | 1DK | {kaynak} {tur} | ${fmt_price(p)}",
                             mumlar, p, ref, zaman=simdi().strftime("%d.%m %H:%M:%S TR"))
        except Exception as e:
            log("PUMP", f"grafik: {e}"); png = b""
        if tg_foto(png, msg):
            log("PUMP", f"🔥 {s} (+{kp:.2f}%) güç={guc}/10")
    except Exception as e:
        log("PUMP", f"{s} doğrulama hatası: {e}")
    finally:
        if alindi: pump_sem.release()
        with pump_kilit:
            pump_aktif.discard(key)
            pump_cd[key] = bitis

# =====================================================
# WEB SUNUCU + BAŞLAT
# =====================================================
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b"Ultimate Futures Bot is running!")
    def do_HEAD(self):
        self.send_response(200); self.end_headers()
    def log_message(self, *a): return

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log("SİSTEM", f"WEB SUNUCU - PORT: {port}")
    server.serve_forever()

def gozetmen(fn):
    while True:
        try:
            fn(); return
        except Exception as e:
            log("GÖZETMEN", f"{getattr(fn, '__name__', 'motor')} çöktü: {e}")
            time.sleep(30)

def durum_loop():
    baslangic = time.time(); son_uyari_ws = 0.0
    while True:
        time.sleep(30)
        try:
            now = time.time()
            if now - baslangic > 300 and now - son_uyari_ws > 1800:
                kopuk = [f for f in (FUT, SPOT) if not (f.bagli and now - f.son < 120)]
                if len(kopuk) == 2:
                    son_uyari_ws = now
                    tg(C, "⚠️ <b>Canlı akış kesildi</b>\n" +
                       "\n".join(f"{f.ad}: {f.son_hata or 'veri yok'}" for f in kopuk))
        except Exception as e:
            log("DURUM", f"Hata: {e}")

def keepalive_loop():
    url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    if not url: return
    while True:
        time.sleep(600)
        try: urllib.request.urlopen(url, timeout=15).read()
        except Exception: pass

if __name__ == "__main__":
    threading.Thread(target=run_web_server, daemon=True).start()
    FUT.dinleyici = pump_isle
    SPOT.dinleyici = pump_isle
    for f in (FUT, SPOT):
        threading.Thread(target=gozetmen, args=(f.calis,), daemon=True).start()
    for m in (pump_winner_loop, durum_loop, keepalive_loop):
        threading.Thread(target=gozetmen, args=(m,), daemon=True).start()

    time.sleep(10)
    satirlar = []
    for f in (FUT, SPOT):
        if besleme_taze(f): satirlar.append(f"✅ {f.ad} canlı akış ({len(f.veri)} coin)")
        else: satirlar.append(f"❌ {f.ad}: {f.son_hata or 'bağlanıyor'}")
    if aktif_besleme():
        bilgi = "Canlı akıştan çalışıyor."
    else:
        ok, durum = baglanti_testi("fapi")
        if not ok:
            ok2, durum2 = baglanti_testi("spot"); durum += "\n" + durum2; ok = ok2
        satirlar.append(durum)
        bilgi = "REST ile devam." if ok else "Veri yok, sinyal gelmez."
    if tg(C, f"🤖 <b>Bot başladı</b> ({simdi().strftime('%d.%m %H:%M')})\n" + "\n".join(satirlar) + f"\n\n{bilgi}"):
        log("SİSTEM", "Telegram OK.")
    else:
        log("SİSTEM", "⚠️ Telegram gönderilemedi!")

    while True:
        time.sleep(3600)
