import ccxt
import pandas as pd
import numpy as np
import time

# Binance Spot ve Futures borsalarını başlat
spot_exchange = ccxt.binance({'enableRateLimit': True})
futures_exchange = ccxt.binanceusdm({'enableRateLimit': True})

def get_all_symbols(exchange, market_type):
    """Borsadaki tüm USDT paritelerini çeker ve likidite filtresinden geçirir."""
    markets = exchange.load_markets()
    symbols = []
    for sym in markets:
        if markets[sym]['quote'] == 'USDT' and markets[sym]['active']:
            # 24 saatlik hacim kontrolü (Likidite Filtresi)
            ticker = exchange.fetch_ticker(sym)
            if ticker['quoteVolume'] and ticker['quoteVolume'] > 5_000_000: # 5 Milyon USDT altını tarama
                symbols.append(sym)
    return symbols

def detect_pump(exchange, symbol):
    try:
        # Son 20 mumu çek (Hacim ortalaması için)
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='5m', limit=20)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # Son mumun hacmi ve fiyat değişimi
        current_vol = df['volume'].iloc[-1]
        prev_vol_avg = df['volume'].iloc[:-1].mean() # Son 19 mumun ortalaması
        
        current_price = df['close'].iloc[-1]
        prev_price = df['close'].iloc[-2]
        price_change_pct = ((current_price - prev_price) / prev_price) * 100
        
        # --- SİNYAL MANTIĞI (Gelişmiş) ---
        # 1. Hacim patlaması: Anlık hacim, ortalamanın en az 3 katı mı? (Dinamik eşik)
        volume_spike = current_vol > (prev_vol_avg * 3)
        
        # 2. Fiyat ivmesi: Fiyat en az %1.5 artmış mı?
        price_momentum = price_change_pct > 1.5
        
        # 3. Z-Skoru (İstatistiksel anormallik): Hacim gerçekten sıra dışı mı?
        vol_std = df['volume'].iloc[:-1].std()
        z_score = (current_vol - prev_vol_avg) / vol_std if vol_std > 0 else 0
        is_anomaly = z_score > 2.5 # Ortalamadan 2.5 standart sapma uzaksa gerçek bir anormalidir
        
        if volume_spike and price_momentum and is_anomaly:
            return {
                "symbol": symbol,
                "price": current_price,
                "change": price_change_pct,
                "volume": current_vol,
                "vol_mult": current_vol / prev_vol_avg,
                "z_score": z_score
            }
    except Exception as e:
        pass # API hatası durumunda sessizce geç
    return None

# --- ANA DÖNGÜ ---
if __name__ == "__main__":
    print("Bot başlatıldı. Spot ve Futures piyasaları taranıyor...")
    
    # Başlangıçta sembol listelerini al
    spot_symbols = get_all_symbols(spot_exchange, 'spot')
    futures_symbols = get_all_symbols(futures_exchange, 'futures')
    
    while True:
        # SPOT Taraması
        for sym in spot_symbols:
            signal = detect_pump(spot_exchange, sym)
            if signal:
                print(f"🚀 SPOT PUMP: {signal['symbol']} | Fiyat: {signal['price']:.5f} | Değişim: %{signal['change']:.2f} | Hacim Katı: {signal['vol_mult']:.1f}x | Z-Skor: {signal['z_score']:.2f}")
        
        # FUTURES Taraması
        for sym in futures_symbols:
            signal = detect_pump(futures_exchange, sym)
            if signal:
                print(f"🔥 FUTURES PUMP: {signal['symbol']} | Fiyat: {signal['price']:.5f} | Değişim: %{signal['change']:.2f} | Hacim Katı: {signal['vol_mult']:.1f}x | Z-Skor: {signal['z_score']:.2f}")
        
        print("Tarama tamamlandı, 10 saniye bekleniyor...\n")
        time.sleep(10) # API limitlerini aşmamak için bekle
