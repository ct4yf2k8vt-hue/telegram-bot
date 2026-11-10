# =====================================================
# MOTOR 10: SMA200 (SADECE 3 GÜNLÜK VE HAFTALIK)
# =====================================================
def sma200_loop():
    log("SMA200", "SMA200 (3 Günlük ve Haftalık) Motoru Başladı.")
    time.sleep(180)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_SMA200]:
                # SADECE 3 GÜNLÜK (3d) VE HAFTALIK (1w) OLARAK AYARLANDI
                for tf in ['3d', '1w']:
                    cooldown_key = f"SMA200_{tf}_{symbol}"
                    if cooldown_key in son_sma200_sinyalleri and time.time() - son_sma200_sinyalleri[cooldown_key] < 43200: continue 
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=250")
                    if not isinstance(klines, list) or len(klines) < 205: continue
                    closes = [float(k[4]) for k in klines[:-1]]; curr, prev = closes[-1], closes[-2]
                    sma200 = calculate_sma(closes, 200)
                    if not sma200 or len(sma200) < 3: continue
                    c_sma = sma200[-1]; p_sma = sma200[-2]
                    
                    if prev <= p_sma and curr > c_sma and curr > c_sma * 1.005:
                        sqz = squeeze_radar_check(symbol, tf)
                        msg = (f"🚀 <b>SMA200 YUKARI KIRILIM (LONG): {symbol}</b>\n<i>{tf.upper()} Grafik</i>\n\n"
                               f"💡 <b>Açıklama:</b> Fiyat, 200 periyotluk basit ortalamanın (SMA200) üzerine çıktı. SMA200, kurumsal yatırımcıların ortalama maliyet çizgisidir. Bu çizginin üzerine çıkılması, piyasanın genel olarak 'Al' bölgesine geçtiğini gösterir.")
                        if sqz: msg += f"\n\n{sqz}"
                        if tg(C, msg): son_sma200_sinyalleri[cooldown_key] = time.time()
                    elif prev >= p_sma and curr < c_sma and curr < c_sma * 0.995:
                        sqz = squeeze_radar_check(symbol, tf)
                        msg = (f"💀 <b>SMA200 AŞAĞI KIRILIM (SHORT): {symbol}</b>\n<i>{tf.upper()} Grafik</i>\n\n"
                               f"💡 <b>Açıklama:</b> Fiyat, 200 periyotluk ortalamanın (SMA200) altına sarktı. Büyük yatırımcıların bölgeden çıktığını ve piyasanın genel olarak 'Sat' (Ayı) bölgesine geçtiğini gösterir. Düşüş trendi hız kazanabilir.")
                        if sqz: msg += f"\n\n{sqz}"
                        if tg(C, msg): son_sma200_sinyalleri[cooldown_key] = time.time()
                time.sleep(0.1)
            time.sleep(SMA200_ARALIGI)
        except Exception as e: time.sleep(60)
