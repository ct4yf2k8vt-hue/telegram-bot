import os
import time
import urllib.request
import urllib.parse

T = os.environ.get("BOT_TOKEN") or os.environ.get("BOT-TOKEN")
C = os.environ.get("CHAT_ID") or os.environ.get("CHAT-ID")

print("BASLATILIYOR...")
print("TOKEN VAR MI:", T is not None)
print("CHAT_ID VAR MI:", C is not None)

# Test mesajı gönder
try:
    d = urllib.parse.urlencode({"chat_id": C, "text": "TEST MESAJI - BOT CALISIYOR"}).encode()
    urllib.request.urlopen("https://api.telegram.org/bot" + T + "/sendMessage", d, timeout=10).read()
    print("TEST MESAJI GONDERILDI")
except Exception as e:
    print("TG HATASI:", e)

# Sonsuz döngü
while True:
    print("BOT CALISIYOR... " + time.strftime("%H:%M:%S"))
    time.sleep(60)
