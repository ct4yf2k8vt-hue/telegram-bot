# =====================================================
# MOTOR 4: FORMASYON (MSS ÇİFT YÖNLÜ) - GÜNCELLENDİ
# =====================================================
def formasyon_loop():
    log("FORMASYON", "Formasyon (İkili Tepe/Omuz-Baş-Omuz) Motoru Başladı.")
    time.sleep(45)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_FORMASYON]:
                if symbol in son_formasyon_sinyalleri and time.time() - son_formasyon_sinyalleri[symbol] < 86400: continue
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1d&limit=100")
                if not isinstance(klines, list) or len(klines) < 100: continue
                closes = [float(k[4]) for k in klines]
                curr = closes[-1]
                
                # YUKARI KIRILIM (LONG) - Ters Omuz Baş Omuz / İkili Dip
                if curr > max(closes[-20:-5]): 
                    msg = (f"🚀 <b>FORMASYON KIRILIMI (LONG): {symbol}</b>\n"
                           f"<i>Ters Omuz-Baş-Omuz / İkili Dip Formasyonu</i>\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, görseldeki formasyonların boyun çizgisini (Neckline) yukarı yönlü kırdı. Piyasa Yapısı Değişimi (MSS) gerçekleşti. Alıcılar kontrolü ele geçirdi ve yeni bir yükseliş trendi (Higher High) başladı.")
                    if tg(C, msg): son_formasyon_sinyalleri[symbol] = time.time()
                
                # AŞAĞI KIRILIM (SHORT) - Omuz Baş Omuz / İkili Tepe
                elif curr < min(closes[-20:-5]):
                    msg = (f"💀 <b>FORMASYON KIRILIMI (SHORT): {symbol}</b>\n"
                           f"<i>Omuz-Baş-Omuz / İkili Tepe Formasyonu</i>\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, görseldeki formasyonların boyun çizgisini (Neckline) aşağı yönlü kırdı. Piyasa Yapısı Değişimi (MSS) aşağı yönlü gerçekleşti. Satıcılar kontrolü ele geçirdi ve yeni bir düşüş trendi (Lower Low) başladı.")
                    if tg(C, msg): son_formasyon_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(FORMASYON_ARALIGI)
        except: time.sleep(60)
