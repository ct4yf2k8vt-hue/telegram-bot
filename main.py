import os
import time
import threading
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

T = os.environ.get("BOT_TOKEN") or os.environ.get("BOT-TOKEN")
C = os.environ.get("CHAT_ID") or os.environ.get("CHAT-ID")

print("TEST KODU BASLADI")
print("TOKEN:", T[:20] if T else "YOK")
print("CHAT_ID:", C if C else "YOK")

# Telegram'a test mesajı gönder
try:
    d = urllib.parse.urlencode({"chat_id": C, "text": "TEST MESAJI"}).encode()
    urllib.request.urlopen("https://api.telegram.org/bot" + T + "/sendMessage", d, timeout=10).read()
    print("TEST MESAJI GONDERILDI")
except Exception as e:
    print("TG HATASI:", e)

# Sonsuz döngü
while True:
    print("BOT CALISIYOR...", time.strftime("%H:%M:%S"))
    time.sleep(60)
