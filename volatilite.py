
def chartist_loop():
    log("CHARTIST", "Saatlik EMA200 LONG/SHORT Motoru Başladı.")
    time.sleep(30)
    while True:
        try:
            tickers = gj(f"{BASE}/api/v3/ticker/24hr")
            if not isinstance(tickers, list):
                time.sleep(60)
                continue

            for symbol in [
                t['symbol'] for t in tickers
                if t['symbol'].endswith('USDT')
                and t['symbol'] not in YASAKLI_HISSELER
                and not any(x in t['symbol'] for x in ['3L', '3S', '5L', '5S'])
                and float(t.get('quoteVolume', 0)) >= MIN_HACIM_CHARTIST
            ]:
                klines = gj(
                    f"{BASE}/api/v3/klines?symbol={symbol}&interval=1h&limit=500"
                )
                if not isinstance(klines, list) or len(klines) < 205:
                    continue

                # Yalnızca kapanmış mumları kullan
                closes = [float(k[4]) for k in klines[:-1]]
                vols = [float(k[7]) for k in klines[:-1]]

                curr, prev = closes[-1], closes[-2]
                ema200 = calculate_ema(closes, 200)

                if len(ema200) < 2:
                    continue

                avg_vol = sum(vols[-21:-1]) / 20
                if avg_vol <= 0 or vols[-1] <= avg_vol:
                    continue

                yon = None
                emoji = ""
                sl = 0
                hedef = 0

                # EMA200 yukarı kırılımı: LONG
                if (
                    prev <= ema200[-2]
                    and curr > ema200[-1]
                    and curr > ema200[-1] * 1.01
                ):
                    yon = "LONG"
                    emoji = "🚀"
                    sl = ema200[-1] * 0.97
                    hedef = curr * 1.15

                # EMA200 aşağı kırılımı: SHORT
                elif (
                    prev >= ema200[-2]
                    and curr < ema200[-1]
                    and curr < ema200[-1] * 0.99
                ):
                    yon = "SHORT"
                    emoji = "🔻"
                    sl = ema200[-1] * 1.03
                    hedef = curr * 0.85

                if not yon:
                    time.sleep(0.05)
                    continue

                cooldown_key = f"CH_1h_{yon}_{symbol}"
                if (
                    cooldown_key in son_sinyaller
                    and time.time() - son_sinyaller[cooldown_key] < 86400
                ):
                    time.sleep(0.05)
                    continue

                sl_pct = ((sl - curr) / curr) * 100
                hedef_pct = ((hedef - curr) / curr) * 100

                # LONG/SHORT stop ve hedef yönü kontrolü
                if yon == "LONG" and (sl >= curr or hedef <= curr):
                    continue
                if yon == "SHORT" and (sl <= curr or hedef >= curr):
                    continue

                if abs(sl_pct) > 12.0:
                    continue
                if abs(sl_pct) > abs(hedef_pct):
                    continue

                msg = (
                    f"{emoji} <b>SAATLİK EMA200 KIRILIMI ({yon}): {symbol}</b>\n"
                    f"<i>1 Saatlik Grafik - EMA200 Kırılımı</i>\n\n"
                    f"💰 <b>Giriş:</b> {akilli_fiyat(curr)}\n"
                    f"🛑 <b>Stop-Loss:</b> {akilli_fiyat(sl)} "
                    f"(<b>{sl_pct:+.1f}%</b>)\n"
                    f"🎯 <b>Hedef:</b> {akilli_fiyat(hedef)} "
                    f"(<b>{hedef_pct:+.1f}%</b>)\n\n"
                    f"📊 <b>EMA200:</b> {akilli_fiyat(ema200[-1])}\n"
                    f"📈 <b>Hacim:</b> Ortalama üzerinde\n\n"
                    f"💡 <b>Açıklama:</b> Fiyatın EMA200 seviyesini "
                    f"{'yukarı' if yon == 'LONG' else 'aşağı'} yönlü "
                    f"kırması ve hacim teyidi vermesiyle sinyal oluştu."
                )

                tg(C, msg)
                son_sinyaller[cooldown_key] = time.time()
                log("CHARTIST", f"SİNYAL: {symbol} ({yon})")

                time.sleep(0.05)

            time.sleep(CHARTIST_ARALIGI)

        except Exception as e:
            log("CHARTIST", f"Hata: {e}")
            time.sleep(60)
