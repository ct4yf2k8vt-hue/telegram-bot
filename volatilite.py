# =====================================================
# 5. MOTOR: FORMASYON VE KIRILIM AVCISI (DÜZELTİLMİŞ)
# =====================================================

def formasyon_loop():
    log("FORMASYON", "Formasyon ve Kırılım Motoru Başladı.")
    time.sleep(45) 
    while True:
        try:
            log("FORMASYON", "Düşen kanal ve destek kırılımları taranıyor...")
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_FORMASYON]
            
            for symbol in adaylar:
                if symbol in son_formasyon_sinyalleri and time.time() - son_formasyon_sinyalleri[symbol] < 21600: continue
                
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1h&limit=50")
                if not isinstance(klines, list) or len(klines) < 50: continue
                
                highs = [float(k[2]) for k in klines]
                lows = [float(k[3]) for k in klines]
                closes = [float(k[4]) for k in klines]
                vols = [float(k[7]) for k in klines]
                current_price = closes[-1]
                
                high_first_half = max(highs[-40:-20])
                high_second_half = max(highs[-20:-5])
                support_zone = min(lows[-20:])
                
                avg_vol = sum(vols[-20:-1]) / 19 if len(vols) >= 20 else 1
                current_vol = vols[-1]
                
                # KIRILIM ŞARTLARI
                if high_first_half > high_second_half and current_price > high_second_half and current_price < (support_zone * 1.20):
                    if current_vol > (avg_vol * 1.5):
                        
                        # --- HATA DÜZELTME MANTIĞI BURADA ---
                        # Eğer düşen kanalı yukarı kırıyorsa, hedef geçmişteki en yüksek tepe olmalı (Long)
                        target = high_first_half
                        
                        if target > current_price: # Hedef girişten büyükse LONG'dur
                            stop = support_zone * 0.98
                            yon_text = "LONG (Spot Alım)"
                            emoji = "🚀"
                            tavsiye = "Buy and hold (Al ve Tut)!"
                        else:
                            # Eğer fiyat hedefi çoktan geçmişse bu sinyal geçersizdir, atla.
                            continue 

                        target_pct = ((target - current_price) / current_price) * 100
                        stop_pct = ((stop - current_price) / current_price) * 100

                        msg = (f"{emoji} <b>FORMASYON SİNYALİ: {symbol.replace('USDT','')}/USDT</b>\n"
                               f"<i>Düşen Kanal / Kama Kırılımı ve Destek Dönüşü!</i>\n\n"
                               f"📈 <b>Yön:</b> {yon_text}\n"
                               f"💰 <b>Giriş (Güncel Fiyat):</b> {current_price:.4f}\n"
                               f"🎯 <b>Hedef (Big Move):</b> {target:.4f} (<b>{target_pct:+.1f}%</b>)\n"
                               f"🛑 <b>Stop-Loss:</b> {stop:.4f} ({stop_pct:+.1f}%)\n\n"
                               f"📊 <b>Hacim:</b> {current_vol:.0f} (Ortalamanın {current_vol/avg_vol:.1f} katı)\n"
                               f"🛡️ <b>Destek Bölgesi:</b> {support_zone:.4f}\n\n"
                               f"💡 <b>Analiz:</b> Fiyat uzun süredir düşen bir kanal içindeydi. {support_zone:.4f} seviyesindeki güçlü destek bölgesinden tepki aldı ve şu an direnci kırıp yukarı ivme kazanıyor. <b>{tavsiye}</b> 📈\n\n"
                               f"<i>Bu tarz sinyaller haftada 1-2 kez gelir, büyük hareket potansiyeli taşır.</i>")
                        
                        if tg(C, msg):
                            son_formasyon_sinyalleri[symbol] = time.time()
                            log("FORMASYON", f"🔥 KIRILIM SİNYALİ: {symbol}")
                
                time.sleep(0.1)
            time.sleep(FORMASYON_ARALIGI)
        except Exception as e:
            log("FORMASYON", f"Hata: {e}")
            time.sleep(60)
