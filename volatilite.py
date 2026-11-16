# =====================================================
# MOTOR 10: GRAFİK FORMASYONLARI (CHART PATTERNS) - GÜNCEL FİLTRELİ
# =====================================================
def chart_patterns_loop():
    log("PATTERN", "Grafik Formasyon Avcısı Başladı (İkili Tepe/Dip, OBO).")
    time.sleep(150)
    while True:
        try:
            tickers = gj(f"{FAPI}/fapi/v1/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and t['symbol'] not in YASAKLI_HISSELER and float(t.get('quoteVolume', 0)) >= MIN_HACIM_PATTERN]
            
            for symbol in adaylar:
                if symbol in son_chart_pattern_sinyalleri and time.time() - son_chart_pattern_sinyalleri[symbol] < 14400: continue
                
                for tf in ['4h', '1d']:
                    klines = gj(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval={tf}&limit=100")
                    if not isinstance(klines, list) or len(klines) < 50: continue
                    
                    highs = [float(k[2]) for k in klines]
                    lows = [float(k[3]) for k in klines]
                    closes = [float(k[4]) for k in klines]
                    vols = [float(k[7]) for k in klines]
                    
                    curr_close = closes[-2] 
                    curr_vol = vols[-2]
                    avg_vol = sum(vols[-22:-2]) / 20 
                    
                    if curr_vol < avg_vol * 1.2: continue

                    p_highs, p_lows = find_pivots(highs, lows) 
                    
                    sinyal = None; formasyon_adi = ""; yon = ""; emoji = ""; hedef = 0; stop = 0; neckline = 0
                    
                    if len(p_highs) >= 2:
                        p1_idx, p1_price = p_highs[-2]
                        p2_idx, p2_price = p_highs[-1]
                        if 5 <= (p2_idx - p1_idx) <= 30 and abs(p1_price - p2_price) / p1_price <= 0.02:
                            neckline = min(lows[p1_idx:p2_idx+1])
                            if curr_close < neckline * 0.99:
                                sinyal = True; formasyon_adi = "İkili Tepe (M)"; yon = "SHORT"; emoji = "🔻"
                                stop = max(p1_price, p2_price) * 1.02
                                hedef = neckline - (max(p1_price, p2_price) - neckline)

                    if not sinyal and len(p_lows) >= 2:
                        p1_idx, p1_price = p_lows[-2]
                        p2_idx, p2_price = p_lows[-1]
                        if 5 <= (p2_idx - p1_idx) <= 30 and abs(p1_price - p2_price) / p1_price <= 0.02:
                            neckline = max(highs[p1_idx:p2_idx+1])
                            if curr_close > neckline * 1.01:
                                sinyal = True; formasyon_adi = "İkili Dip (W)"; yon = "LONG"; emoji = "🚀"
                                stop = min(p1_price, p2_price) * 0.98
                                hedef = neckline + (neckline - min(p1_price, p2_price))

                    if not sinyal and len(p_highs) >= 3:
                        p1_idx, p1_price = p_highs[-3]
                        p2_idx, p2_price = p_highs[-2] 
                        p3_idx, p3_price = p_highs[-1] 
                        if p2_price > p1_price and p2_price > p3_price and abs(p1_price - p3_price) / p1_price <= 0.03:
                            neckline = min(lows[p1_idx:p3_idx+1])
                            if curr_close < neckline * 0.99:
                                sinyal = True; formasyon_adi = "Omuz-Baş-Omuz"; yon = "SHORT"; emoji = "💀"
                                stop = p3_price * 1.02
                                hedef = neckline - (p2_price - neckline)

                    if not sinyal and len(p_lows) >= 3:
                        p1_idx, p1_price = p_lows[-3]
                        p2_idx, p2_price = p_lows[-2] 
                        p3_idx, p3_price = p_lows[-1] 
                        if p2_price < p1_price and p2_price < p3_price and abs(p1_price - p3_price) / p1_price <= 0.03:
                            neckline = max(highs[p1_idx:p3_idx+1])
                            if curr_close > neckline * 1.01:
                                sinyal = True; formasyon_adi = "Ters Omuz-Baş-Omuz"; yon = "LONG"; emoji = "🚀"
                                stop = p3_price * 0.98
                                hedef = neckline + (neckline - p2_price)

                    # =====================================================
                    # MANTIK FİLTRELERİ (GÜNCEL)
                    # =====================================================
                    if sinyal:
                        hedef_pct = ((hedef - curr_close) / curr_close) * 100
                        stop_pct = ((stop - curr_close) / curr_close) * 100
                        
                        if yon == "SHORT" and hedef >= curr_close: continue
                        if yon == "LONG" and hedef <= curr_close: continue
                        if abs(stop_pct) > 15.0: continue 
                        if abs(hedef_pct) < 1.5 or abs(hedef_pct) > 40.0: continue
                        if yon == "SHORT" and curr_close < hedef * 1.01: continue
                        if yon == "LONG" and curr_close > hedef * 0.99: continue
                    # =====================================================

                        aciklama = "Fiyat, klasik bir dönüş formasyonunu tamamlayarak boyun çizgisini (Neckline) hacimle kırdı."
                        if "OBO" in formasyon_adi: aciklama = "Trendin tepe noktasında oluşan klasik dönüş formasyonu. Boyun çizgisi kırıldı, satıcılar kontrolü ele aldı."
                        if "Dip" in formasyon_adi: aciklama = "Dipte oluşan çift dip formasyonu. Satıcılar tükendi, alıcılar hacimle boyun çizgisini yukarı kırdı."

                        msg = (f"{emoji} <b>GRAFİK FORMASYONU ({yon}): {symbol}</b>\n"
                               f"<i>{tf.upper()} Grafik - {formasyon_adi}</i>\n\n"
                               f"💰 <b>Giriş:</b> {akilli_fiyat(curr_close)}\n"
                               f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} (<b>{hedef_pct:+.1f}%</b>)\n"
                               f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(stop)} ({stop_pct:+.1f}%)\n\n"
                               f"📊 <b>Boyun Çizgisi:</b> {akilli_fiyat(neckline)}\n"
                               f"📈 <b>Hacim Teyidi:</b> Alındı ({curr_vol/avg_vol:.1f}x)\n\n"
                               f"💡 <b>Açıklama:</b> {aciklama}")
                        
                        if tg(C, msg):
                            son_chart_pattern_sinyalleri[symbol] = time.time()
                            log("PATTERN", f"🔥 SİNYAL: {symbol} ({yon} - {formasyon_adi})")
                            
                    time.sleep(0.1)
            time.sleep(CHART_PATTERN_ARALIGI)
        except Exception as e:
            log("PATTERN", f"Hata: {e}")
            time.sleep(60)
