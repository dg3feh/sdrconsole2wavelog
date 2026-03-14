import sys
import os
import serial
import requests
import time
import re
import json
from datetime import datetime, timezone

# ================= DEBUG FLAG =================
DEBUG = '-debug' in sys.argv or '--debug' in sys.argv
def log(*args, **kwargs):
    if DEBUG:
        print(*args, **kwargs)

# ================= CONFIG LADEN =================
# Pfad zur Config: neben der EXE oder Skript
if getattr(sys, 'frozen', False):
    # Wir laufen als PyInstaller-EXE
    base_path = os.path.dirname(sys.executable)
else:
    # Wir laufen normal in Python
    base_path = os.path.dirname(__file__)

config_path = os.path.join(base_path, "config.json")

if not os.path.exists(config_path):
    log(f"[ERROR] config.json nicht gefunden! Erwartet unter: {config_path}")
    sys.exit(1)

with open(config_path, "r") as f:
    cfg = json.load(f)

# ================= CONFIG =================
COM_PORT = cfg.get("COM_PORT", "COM10")
BAUDRATE = cfg.get("BAUDRATE", 57600)
WAVELOG_API_URL = cfg.get("WAVELOG_API_URL")
API_KEY = cfg.get("API_KEY")
RADIO_NAME = cfg.get("RADIO_NAME", "My Station")
sat_name = cfg.get("sat_name")
sat_mode = cfg.get("sat_mode")
prop_mode = cfg.get("prop_mode")
SAT_RXTX_OFFSET_HZ = cfg.get("SAT_RXTX_OFFSET_HZ", 8089500000)
POLL_INTERVAL = cfg.get("POLL_INTERVAL", 1.0)
POWER = cfg.get("power")  # optional

# ================= MODE MAP =================
MODE_MAP = {
    '1': 'LSB','2': 'USB','3': 'CW','4': 'RTTY',
    '5': 'AM','6': 'FM','7': 'DIGU','8': 'DIGL'
}

# ================= CAT FUNKTIONEN =================
def read_until_semicolon(ser):
    return ser.read_until(b';').decode(errors='ignore')

def get_rx_frequency(ser):
    ser.write(b'FA;')
    response = read_until_semicolon(ser)
    match = re.search(r'FA([0-9]+)', response)
    return match.group(1) if match else None

def get_mode(ser):
    ser.write(b'MD;')
    response = read_until_semicolon(ser)
    match = re.search(r'MD([0-9]+)', response)
    if match:
        return MODE_MAP.get(match.group(1), 'USB')
    return None

# ================= TX BERECHNUNG =================
def calculate_tx(rx_freq):
    try:
        rx = int(rx_freq)
        if sat_name:  # Satellit aktiv
            return str(rx - SAT_RXTX_OFFSET_HZ)
        return str(rx)
    except:
        return None

# ================= API SENDEN =================
def send_to_wavelog(rx_freq, mode, tx_freq):
    payload = {
        "key": API_KEY,
        "radio": RADIO_NAME,
        "frequency": int(tx_freq),
        "mode": mode,
        "frequency_rx": int(rx_freq),
        "mode_rx": mode,
        "timestamp": datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M:%S")
    }

    # optionale Felder
    if sat_name:
        payload["sat_name"] = sat_name
    if sat_mode:
        payload["sat_mode"] = sat_mode
    if prop_mode:
        payload["prop_mode"] = prop_mode
    if POWER is not None:
        payload["power"] = POWER

    log("[DEBUG-PAYLOAD]", payload)

    try:
        r = requests.post(WAVELOG_API_URL, json=payload, timeout=5)
        log(f"[API] Status: {r.status_code}")
        if r.status_code == 404:
            log("[API WARNING] 404 → URL prüfen!")
    except Exception as e:
        log("[API ERROR]", e)

# ================= API TEST =================
def test_api():
    log("Teste Wavelog API...")
    test_payload = {
        "key": API_KEY,
        "radio": RADIO_NAME,
        "frequency": 14074000,
        "mode": "SSB",
        "frequency_rx": 14074000,
        "mode_rx": "SSB",
        "timestamp": datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M:%S")
    }
    if POWER is not None:
        test_payload["power"] = POWER
    try:
        r = requests.post(WAVELOG_API_URL, json=test_payload, timeout=5)
        log(f"[TEST-POST] Status: {r.status_code}, Response: {r.text}")
    except Exception as e:
        log("[TEST-POST ERROR]", e)

# ================= HAUPTSCHLEIFE =================
def main():
    log("Starte CAT → Wavelog Bridge")
    log(f"COM-Port: {COM_PORT}, sat_name: {sat_name}, sat_mode: {sat_mode}, prop_mode: {prop_mode}, power: {POWER}")
    log("--------------------------------------------------")

    test_api()
    try:
        ser = serial.Serial(COM_PORT, BAUDRATE, timeout=1)
    except Exception as e:
        log("[ERROR] COM-Port öffnen fehlgeschlagen:", e)
        sys.exit(1)

    last_rx = None
    last_mode = None

    while True:
        try:
            rx_freq = get_rx_frequency(ser)
            mode = get_mode(ser)
            if not rx_freq or not mode:
                time.sleep(POLL_INTERVAL)
                continue

            tx_freq = calculate_tx(rx_freq)
            if not tx_freq:
                time.sleep(POLL_INTERVAL)
                continue

            if rx_freq != last_rx or mode != last_mode:
                log(f"[UPDATE] RX: {rx_freq}, TX: {tx_freq}, MODE: {mode}, ----------------")
                send_to_wavelog(rx_freq, mode, tx_freq)
                last_rx = rx_freq
                last_mode = mode

            time.sleep(POLL_INTERVAL)

        except Exception as e:
            log("[ERROR]", e)
            time.sleep(2)

if __name__ == "__main__":
    main()