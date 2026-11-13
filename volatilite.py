def pump_loop():
    log("PUMP", "Anlık Pump Avcısı Başladı.")
    time.sleep(30)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): time.sleep(60); continue
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_PUMP]:
                # SPAM ENGELLEME: Aynı coin için 1 saatte (3600 sn) bir sinyal
                if symbol in son_pump_sinyalleri and time.time() - son_pump_sinyalleri[symbol] < 3600: continue
                
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=5m&limit=3")
                if not isinstance(klines, list) or len(klines) < 3: continue
                
                # MUM KAPANIŞI: Son kapanan mumu (klines[-2]) alıyoruz
                curr_k, prev_k = klines[-2], klines[-3]
                cc, pc = float(curr_k[4]), float(prev_k[4])
                cv, pv = float(curr_k[7]), float(prev_k[7])
                if pc == 0 or pv == 0: continue
                
                if ((cc-pc)/pc)*100 >= 3.0 and ((cv-pv)/pv)*100 >= 100.0 and (cv-pv) >= 50_000:
                    teyit = trap_radar_check(symbol)
                    sqz = squeeze_radar_check(symbol, '4h')
                    msg = (f"🚀 <b>Pump - {symbol}</b>\n💰 Fiyat: {akilli_fiyat(cc)}\n📊 Hacim Artışı: +%{((cv-pv)/pv)*100:.0f}\n\n{teyit}\n\n"
                           f"💡 <b>Açıklama:</b> 5 dakikalık grafikte aniden sert bir hacim ve fiyat patlaması yaşandı. Trap Radar (OI ve CVD) sayesinde bu hareketin arkasında gerçek balinaların mı yoksa küçük yatırımcıyı çekmek isteyen sahte botların mı olduğunu analiz ettik.")
                    if sqz: msg += f"\n\n{sqz}"
                    tg(C, msg); son_pump_sinyalleri[symbol] = time.time()
                time.sleep(0.05)
            time.sleep(PUMP_ARALIGI)
        except: time.sleep(60)
