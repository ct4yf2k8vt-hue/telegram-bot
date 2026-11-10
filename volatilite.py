# =====================================================
# MOTOR 8: YILLIK ZİRVE / DİP (365 GÜN) - GÜNCELLENDİ
# =====================================================
def ath_atl_loop():
    log("YILLIK_ZIRVE_DIP", "Yıllık Zirve/Dip (365 Gün) Motoru Başladı.")
    time.sleep(135)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_ATH_ATL]
            
            for symbol in adaylar:
                if symbol in son_ath_atl_sinyalleri and time.time() - son_ath_atl_sinyalleri[symbol] < 86400: continue
                
                # 500 Gün yerine 365 Gün (1 Yıl) veri çekiyoruz
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1d&limit=365")
                if not isinstance(klines, list) or len(klines) < 200: continue
                
                highs = [float(k[2]) for k in klines[:-1]]
                lows = [float(k[3]) for k in klines[:-1]]
                closes = [float(k[4]) for k in klines[:-1]]
                vols = [float(k[7]) for k in klines[:-1]]
                
                current_price = closes[-1]; prev_close = closes[-2]; current_vol = vols[-1]
                
                # Son 1 yılın en yüksek ve en düşük seviyeleri
                yillik_zirve = max(highs)
                yillik_dip = min(lows)
                
                if current_vol < (sum(vols[-21:-1])/20 * 1.2): continue
                
                # YILLIK DİP KIRILIMI (SHORT)
                if prev_close >= yillik_dip and current_price < yillik_dip:
                    stop = yillik_dip * 1.05; hedef = current_price * 0.80 
                    msg = (f"💀 <b>YILLIK DİP KIRILIMI (SHORT): {symbol}</b>\n<i>1 Günlük Grafik (Son 365 Gün)</i>\n\n"
                           f"💰 Giriş: {akilli_fiyat(current_price)}\n🎯 Hedef: {akilli_fiyat(hedef)}\n🛑 Stop: {akilli_fiyat(stop)}\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, son 1 yılın (365 gün) en düşük seviyesini hacimle aşağı kırdı. Psikolojik bir çöküş (kapitülasyon) yaşanıyor olabilir, düşüş derinleşebilir.")
                    if tg(C, msg): son_ath_atl_sinyalleri[symbol] = time.time()
                    
                # YILLIK ZİRVE KIRILIMI (LONG)
                elif prev_close <= yillik_zirve and current_price > yillik_zirve:
                    stop = yillik_zirve * 0.95; hedef = current_price * 1.20 
                    msg = (f"🚀 <b>YILLIK ZİRVE KIRILIMI (LONG): {symbol}</b>\n<i>1 Günlük Grafik (Son 365 Gün)</i>\n\n"
                           f"💰 Giriş: {akilli_fiyat(current_price)}\n🎯 Hedef: {akilli_fiyat(hedef)}\n🛑 Stop: {akilli_fiyat(stop)}\n\n"
                           f"💡 <b>Açıklama:</b> Fiyat, son 1 yılın (365 gün) en yüksek seviyesini hacimle yukarı kırdı. Önünde hiçbir yıllık direnç kalmadı (Fiyat Keşfi), büyük bir boğa trendi başlayabilir.")
                    if tg(C, msg): son_ath_atl_sinyalleri[symbol] = time.time()
                time.sleep(0.1)
            time.sleep(ATH_ATL_ARALIGI)
        except Exception as e: time.sleep(60)
