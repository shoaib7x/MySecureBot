from flask import Flask
from threading import Thread
import time
import requests
import logging

app = Flask('')

# Reduce logging noise
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

@app.route('/')
def home():
    return "Bot is Alive & Running! 🚀"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# Self-pinger to prevent Render from sleeping
def ping_self():
    while True:
        try:
            time.sleep(600)  # Ping every 10 minutes
            # Localhost ping is usually enough for Render internal keep-alive
            requests.get("http://localhost:8080/")
            print("Ping sent to keep bot alive!")
        except Exception as e:
            print(f"Pinger Error: {e}")

def start_pinger():
    t = Thread(target=ping_self)
    t.start()


