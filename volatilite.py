import ccxt
import pandas as pd
import pandas_ta as ta
import time

# Borsa Bağlantısı (Örnek: Binance. Kendi borsanı yazabilirsin)
exchange = ccxt.binance({
    'enableRateLimit': True,
    # 'apiKey': 'SENIN_API_KEY', # Sadece veri çekmek için gerek yok, işlem yapmak için gerekli
    # 'secret': 'SENIN_SECRET_KEY',
})

def analyze_coin(symbol):
    try:
        # 1. 15 Dakikalık (m15) Mum Verilerini Çek (Son 200 mum)
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=200)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # 2. İndikatörleri Hesapla
        # MA 20 ve MA 200
        df['MA20'] = ta.sma(df['close'], length=20)
        df['MA200'] = ta.sma(df['close'], length=200)
        
        # RSI (14)
        df['RSI'] = ta.rsi(df['close'], length=14)
        
        # MACD (12, 26, 9)
        macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
        df['MACD'] = macd['MACD_12_26_9']
        
        # Anlık Fiyat ve Değişim (Delta)
        current_price = df['close'].iloc[-1]
        prev_price = df['close'].iloc[-2]
        delta = ((current_price - prev_price) / prev_price) * 100
        
        # MA'lara olan uzaklık (%)
        dist_ma20 = ((current_price - df['MA20'].iloc[-1]) / df['MA20'].iloc[-1]) * 100
        dist_ma200 = ((current_price - df['MA200'].iloc[-1]) / df['MA200'].iloc[-1]) * 100
        
        current_rsi = df['RSI'].iloc[-1]
        current_macd = df['MACD'].iloc[-1]
        
        # 3. EXHAUSTION (Tükenmişlik) Mantığı
        # Botun mantığı: RSI aşırı alım (70 üstü) veya aşırı satım (30 altı) ise VE fiyat MA20'den çok uzaksa tükenmiştir.
        exhaustion = "NO"
        if current_rsi > 70 and dist_ma20 > 5:
            exhaustion = "YES 🔥"
        elif current_rsi < 30 and dist_ma20 < -5:
            exhaustion = "YES 🔥"
            
        # 4. Rapor Formatını Yazdır (Görseldeki gibi)
        print(f"# {symbol}:")
        print(f"m15 Delta: {delta:.2f} %")
        print(f"Exhaustion: {exhaustion}")
        print(f"+{dist_ma20:.2f}% to 20 MA")
        print(f"+{dist_ma200:.2f}% to 200 MA")
        print(f"RSI: {current_rsi:.1f}")
        print(f"$ {current_price} ({delta:.1f}%)")
        print(f"MACD: {current_macd:.5f}")
        print("-" * 30)
        
        # Likidite Bölgesi / Direnç (Basitleştirilmiş Destek-Direnç)
        # Son 50 mumun en yükseği ve en düşüğü
        resistance = df['high'].rolling(window=50).max().iloc[-1]
        liquidity_zone = df['low'].rolling(window=50).min().iloc[-1]
        print(f"1D RESISTANCE: ${resistance:.5f}")
        print(f"LIQUIDITY ZONE: ${liquidity_zone:.5f}")
        print("=" * 40)

    except Exception as e:
        print(f"Hata oluştu ({symbol}): {e}")

# Test etmek istediğin coinler
coins = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']

print("Bot Başlatıldı, Piyasa Taranıyor...\n")
while True:
    for coin in coins:
        analyze_coin(coin)
    
    print("15 saniye bekleniyor...")
    time.sleep(15) # Sürekli tarama yapmaması için bekleme süresi
