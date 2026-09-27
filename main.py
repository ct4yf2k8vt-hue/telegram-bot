import os
import time
import urllib.request
import urllib.parse
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

T = os.environ.get("BOT_TOKEN") or os.environ.get("BOT-TOKEN")
C = os.environ.get("CHAT_ID") or os.environ.get("CHAT-ID")

def tg(m):
    try:
        d = urllib.parse.urlencode({"chat_id": C, "text": m, "parse_mode": "HTML", "disable_web_page_preview": True}).encode()
        urllib.request.urlopen("https://api.telegram.org/bot" + T + "/sendMessage", d, timeout=10).read()
        print("MESAJ GONDERILDI")
    except Exception as e:
        print("TG HATASI:", e)

def bot_loop():
    print("BASLATILIYOR...")
    print("TOKEN VAR MI:", T is not None)
    print("CHAT_ID VAR MI:", C is not None)
    tg("TEST MESAJI - BOT CALISIYOR")
    while True:
        print("BOT CALISIYOR... " + time.strftime("%H:%M:%S"))
        time.sleep(300)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print("WEB SUNUCUSU BASLADI - PORT:", port)
    server.serve_forever()

if __name__ == "__main__":
    bot_thread = threading.Thread(target=bot_loop)
    bot_thread.daemon = True
    bot_thread.start()
    run_web_server()
