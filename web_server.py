from flask import Flask
from threading import Thread
import time
import requests
import logging

app = Flask('')
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

@app.route('/')
def home():
    return "Bot is Running! 🚀"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

def ping_self():
    while True:
        try:
            time.sleep(600)
            requests.get("http://localhost:8080/")
        except: pass

def start_pinger():
    t = Thread(target=ping_self)
    t.start()


