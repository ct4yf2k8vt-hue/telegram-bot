# =====================================================
# 3. MOTOR: ANLIK PUMP ALARMI (1 DAKİKALIK - HIZLI TARAMA)
# =====================================================

def pump_loop():
    log("PUMP", "Anlık Pump Alarmı Motoru Başladı (1m).")
    time.sleep(25)
    while True:
        try:
            log("PUMP", "Pump fırsatları taranıyor (1m)...")
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(30)
                continue

            # Çakma sinyalleri engellemek için 24s hacim barajı (5M$)
            adaylar = [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= MIN_HACIM_24H_PUMP]

            for symbol in adaylar:
                if symbol in son_pump_sinyalleri and time.time() - son_pump_sinyalleri[symbol] < 900: 
                    continue

                # 1 DAKİKALIK grafikte son 2 kapanmış mumu çek
                klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval=1m&limit=3")
                if not isinstance(klines, list) or len(klines) < 3: continue

                curr_k = klines[-2]
                prev_k = klines[-3]

                curr_close = float(curr_k[4])
                prev_close = float(prev_k[4])
                curr_vol = float(curr_k[7])
                prev_vol = float(prev_k[7])

                if prev_close == 0 or prev_vol == 0: continue

                # Fiyat ve hacim değişimi
                price_change = ((curr_close - prev_close) / prev_close) * 100
                vol_change = ((curr_vol - prev_vol) / prev_vol) * 100
                vol_increase_usdt = curr_vol - prev_vol

                # SİNYAL ŞARTLARI: 1 dakikada %3+ fiyat artışı VE %100+ hacim artışı VE en az 10 Bin Dolar hacim
                if price_change >= 3.0 and vol_change >= 100.0 and vol_increase_usdt >= 10000:
                    
                    p_old = f"{prev_close:.8f}".rstrip('0').rstrip('.')
                    p_new = f"{curr_close:.8f}".rstrip('0').rstrip('.')
                    now_time = simdi().strftime('%I:%M:%S %p')
                    
                    msg = (f"🚀 <b>Pump - {symbol.replace('USDT','')}/USDT [Binance]</b>\n"
                           f"💰 <b>Price:</b> ${p_old} ➔ ${p_new} (<b>{price_change:+.2f}%</b>)\n"
                           f"📊 <b>Volume:</b> {curr_vol:.0f} (<b>{vol_change:+.0f}%</b>)\n"
                           f"Volume increased by {vol_increase_usdt:.0f} ⬆️\n"
                           f"Time: {now_time} 🕐\n\n"
                           f"⚠️ <i>Hızlı hareket! Dikkatli olun.</i>")
                    
                    if tg(C, msg):
                        son_pump_sinyalleri[symbol] = time.time()
                        log("PUMP", f"🔥 PUMP SİNYALİ: {symbol} (+%{price_change:.1f})")

                time.sleep(0.05)

            log("PUMP", "Pump taraması bitti, 1 dk bekleniyor.")
            time.sleep(PUMP_ARALIGI)
        except Exception as e:
            log("PUMP", f"Hata: {e}")
            time.sleep(30)
