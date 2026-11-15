# =====================================================
# MOTOR 11: FORMASYON AVCISI (TAM KORUMALI SÜRÜM)
# =====================================================
import traceback # Hatayı net görmek için

def find_pivots(highs, lows, left=3, right=3):
    pivot_highs, pivot_lows = [], []
    for i in range(left, len(highs) - right):
        if highs[i] == max(highs[i-left:i+right+1]): pivot_highs.append((i, highs[i]))
        if lows[i] == min(lows[i-left:i+right+1]): pivot_lows.append((i, lows[i]))
    return pivot_highs, pivot_lows

# Sinyal hafızasını burada garantiye alıyoruz
son_formasyon_sinyalleri = {} 

def pattern_scanner_loop():
    print("✅ [LOG] Formasyon Avcısı Motoru Başlatıldı!")
    time.sleep(10) # Sadece 10 saniye beklesin, test için
    
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list): 
                time.sleep(60)
                continue
                
            for symbol in [t['symbol'] for t in tickers if t['symbol'].endswith('USDT') and float(t.get('quoteVolume', 0)) >= 1000000]: 
                for tf in ['4h', '1d']:
                    klines = gj(f"{BASE}/api/v3/klines?symbol={symbol}&interval={tf}&limit=100")
                    if not isinstance(klines, list) or len(klines) < 50: continue
                    
                    closes = [float(k[4]) for k in klines]
                    highs = [float(k[2]) for k in klines]
                    lows = [float(k[3]) for k in klines]
                    volumes = [float(k[5]) for k in klines]
                    
                    curr_close = closes[-2]
                    curr_vol = volumes[-2]
                    avg_vol = sum(volumes[-22:-2]) / 20 
                    
                    if curr_vol < avg_vol * 1.2: continue

                    pivot_highs, pivot_lows = find_pivots(highs, lows)
                    signal_type = None
                    formasyon_adi = ""
                    neckline = 0.0
                    yapi_yuksekligi = 0.0

                    if len(pivot_highs) >= 2:
                        p1_idx, p1_price = pivot_highs[-2]
                        p2_idx, p2_price = pivot_highs[-1]
                        if 5 <= (p2_idx - p1_idx) <= 30 and abs(p1_price - p2_price) / p1_price <= 0.02:
                            neckline = min(lows[p1_idx:p2_idx+1])
                            if curr_close < neckline * 0.99:
                                signal_type, formasyon_adi = "SHORT", "İkili Tepe"
                                yapi_yuksekligi = max(p1_price, p2_price) - neckline

                    if not signal_type and len(pivot_lows) >= 2:
                        p1_idx, p1_price = pivot_lows[-2]
                        p2_idx, p2_price = pivot_lows[-1]
                        if 5 <= (p2_idx - p1_idx) <= 30 and abs(p1_price - p2_price) / p1_price <= 0.02:
                            neckline = max(highs[p1_idx:p2_idx+1])
                            if curr_close > neckline * 1.01:
                                signal_type, formasyon_adi = "LONG", "İkili Dip"
                                yapi_yuksekligi = neckline - min(p1_price, p2_price)

                    if not signal_type and len(pivot_highs) >= 3:
                        p1_idx, p1_price = pivot_highs[-3]
                        p2_idx, p2_price = pivot_highs[-2] 
                        p3_idx, p3_price = pivot_highs[-1] 
                        if p2_price > p1_price and p2_price > p3_price and abs(p1_price - p3_price) / p1_price <= 0.03:
                            neckline = min(lows[p1_idx:p3_idx+1])
                            if curr_close < neckline * 0.99:
                                signal_type, formasyon_adi = "SHORT", "OBO"
                                yapi_yuksekligi = p2_price - neckline

                    if not signal_type and len(pivot_lows) >= 3:
                        p1_idx, p1_price = pivot_lows[-3]
                        p2_idx, p2_price = pivot_lows[-2] 
                        p3_idx, p3_price = pivot_lows[-1] 
                        if p2_price < p1_price and p2_price < p3_price and abs(p1_price - p3_price) / p1_price <= 0.03:
                            neckline = max(highs[p1_idx:p3_idx+1])
                            if curr_close > neckline * 1.01:
                                signal_type, formasyon_adi = "LONG", "Ters OBO"
                                yapi_yuksekligi = neckline - p2_price

                    if signal_type:
                        cooldown_key = f"FORMASYON_{tf}_{formasyon_adi}_{symbol}"
                        if cooldown_key in son_formasyon_sinyalleri and time.time() - son_formasyon_sinyalleri[cooldown_key] < 14400:
                            continue
                            
                        emoji = "🚀" if signal_type == "LONG" else "💀"
                        yon = "YUKARI KIRILIM (LONG)" if signal_type == "LONG" else "AŞAĞI KIRILIM (SHORT)"
                        hedef = neckline + yapi_yuksekligi if signal_type == "LONG" else neckline - yapi_yuksekligi

                        msg = (f"{emoji} <b>{formasyon_adi.upper()} {yon}: {symbol}</b>\n<i>{tf.upper()} Grafik</i>\n\n"
                               f"🎯 <b>Boyun Çizgisi:</b> {neckline:.4f}\n"
                               f"📊 <b>Hacim:</b> Ortalamanın {curr_vol/avg_vol:.1f} katı\n"
                               f"🏁 <b>Hedef:</b> {hedef:.4f}")
                        
                        if tg(C, msg): 
                            son_formasyon_sinyalleri[cooldown_key] = time.time()
                            
                time.sleep(0.2)
            time.sleep(1800) 
        except Exception as e:
            # EĞER HATA VARSA BURADA KIRMIZI KIRMIZI YAZACAK
            print(f"⚠️ [MOTOR 11 HATASI]: {e}") 
            traceback.print_exc() 
            time.sleep(60)

# MOTORU BAŞLATAN TETİKLEYİCİ
try:
    threading.Thread(target=pattern_scanner_loop, daemon=True).start()
    print("✅ [LOG] Formasyon Motoru Thread'i Başarıyla Başlatıldı.")
except Exception as e:
    print(f"🚨 [THREAD BAŞLATMA HATASI]: {e}")
    while True:
    time.sleep(10)
