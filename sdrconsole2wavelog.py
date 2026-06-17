import sys
import os
import serial
import serial.tools.list_ports  # Import für die COM-Port-Erkennung
import requests
import time
import re
import json
import threading
import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk  # ttk hinzugefügt für die Combobox
from datetime import datetime, timezone

# ================= DETECT CONFIG PATH =================
if getattr(sys, 'frozen', False):
    base_path = os.path.dirname(sys.executable)
else:
    base_path = os.path.dirname(__file__)

config_path = os.path.join(base_path, "config.json")

# ================= GLOBAL VARIABLES & CONFIG STORAGE =================
cfg = {}

def load_config():
    global cfg
    if not os.path.exists(config_path):
        cfg = {
            "COM_PORT": "COM10", "BAUDRATE": 57600, 
            "WAVELOG_API_URL": "https://example.com/api/radio", "API_KEY": "",
            "RADIO_NAME": "SDR-Console", "prop_mode": "SAT",
            "sat_name": "QO-100", "sat_mode": "S/X",
            "SAT_RXTX_OFFSET_HZ": 8089500000, "POLL_INTERVAL": 1.0, "power": 10
        }
        save_config()
    else:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

def save_config():
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

load_config()

MODE_MAP = {'1': 'LSB','2': 'USB','3': 'CW','4': 'RTTY','5': 'AM','6': 'FM','7': 'DIGU','8': 'DIGL'}

status_data = {
    "com_status": "Disconnected",
    "wavelog_status": "Ready",
    "rx_freq": "---",
    "tx_freq": "---",
    "mode": "---"
}

# Event for controlling the thread (set by default -> starts running)
running_event = threading.Event()
running_event.set()

log_lock = threading.Lock()
gui_logs = []

def log_to_widget(text):
    timestamp = datetime.now().strftime("%H:%M:%S")
    with log_lock:
        gui_logs.append(f"[{timestamp}] {text}")
        if len(gui_logs) > 50:
            gui_logs.pop(0)

# ================= CAT FUNCTIONS =================
def read_until_semicolon(ser):
    try: return ser.read_until(b';').decode(errors='ignore')
    except: return ""

def get_rx_frequency(ser):
    try:
        ser.write(b'FA;')
        response = read_until_semicolon(ser)
        match = re.search(r'FA([0-9]+)', response)
        return match.group(1) if match else None
    except: return None

def get_mode(ser):
    try:
        ser.write(b'MD;')
        response = read_until_semicolon(ser)
        match = re.search(r'MD([0-9]+)', response)
        return MODE_MAP.get(match.group(1), 'USB') if match else None
    except: return None

def calculate_tx(rx_freq):
    try:
        rx = int(rx_freq)
        sat_n = cfg.get("sat_name")
        offset = cfg.get("SAT_RXTX_OFFSET_HZ", 8089500000)
        return str(rx - offset) if sat_n else str(rx)
    except: return None

def format_freq(freq_str):
    try:
        freq = float(freq_str) / 1000000.0
        return f"{freq:,.4f} MHz".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return freq_str

# ================= BACKGROUND WORKER =================
def cat_loop():
    global status_data
    last_rx, last_mode, ser = None, None, None
    
    log_to_widget("Background thread started.")
    log_to_widget(f"Testing Wavelog API at {cfg.get('WAVELOG_API_URL')}...")
    
    try:
        test_payload = {
            "key": cfg.get("API_KEY"), "radio": cfg.get("RADIO_NAME"), "frequency": 14074000, "mode": "SSB",
            "frequency_rx": 14074000, "mode_rx": "SSB", "timestamp": datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M:%S")
        }
        if cfg.get("power") is not None: test_payload["power"] = cfg.get("power")
        r = requests.post(cfg.get("WAVELOG_API_URL"), json=test_payload, timeout=5)
        log_to_widget(f"API Test Status: {r.status_code}")
    except Exception as e:
        log_to_widget(f"API Test delayed/failed: {e}")

    while True:
        if not running_event.is_set():
            if ser is not None and ser.is_open:
                try: ser.close()
                except: pass
                ser = None
            status_data["com_status"] = "Disconnected"
            status_data["rx_freq"] = "---"
            status_data["tx_freq"] = "---"
            status_data["mode"] = "---"
            last_rx, last_mode = None, None
            time.sleep(0.5)
            continue

        com = cfg.get("COM_PORT", "COM10")
        baud = cfg.get("BAUDRATE", 57600)
        poll = cfg.get("POLL_INTERVAL", 1.0)

        if ser is None or not ser.is_open:
            status_data["com_status"] = "Searching..."
            status_data["rx_freq"] = "---"
            status_data["tx_freq"] = "---"
            status_data["mode"] = "---"
            try:
                ser = serial.Serial(port=com, baudrate=baud, timeout=1, rtscts=False, dsrdtr=False)
                ser.dtr, ser.rts = True, True
                ser.reset_input_buffer()
                ser.reset_output_buffer()
                status_data["com_status"] = "Connected"
                log_to_widget(f"{com} successfully opened.")
            except Exception:
                status_data["com_status"] = "Waiting for port..."
                time.sleep(3)
                continue

        if ser.port != com or ser.baudrate != baud:
            log_to_widget(f"Port or Baudrate mismatch! Closing old port...")
            try: ser.close()
            except: pass
            ser = None
            continue

        try:
            rx_freq = get_rx_frequency(ser)
            mode = get_mode(ser)
            
            if rx_freq is None or mode is None:
                log_to_widget("Connection to port lost (remote side closed?).")
                try: ser.close()
                except: pass
                ser = None
                continue

            tx_freq = calculate_tx(rx_freq)
            
            status_data["rx_freq"] = format_freq(rx_freq)
            status_data["tx_freq"] = format_freq(tx_freq)
            status_data["mode"] = mode

            if rx_freq != last_rx or mode != last_mode:
                payload = {
                    "key": cfg.get("API_KEY"), "radio": cfg.get("RADIO_NAME"), "frequency": int(tx_freq), "mode": mode,
                    "frequency_rx": int(rx_freq), "mode_rx": mode,
                    "timestamp": datetime.now(timezone.utc).strftime("%Y/%m/%d %H:%M:%S")
                }
                if cfg.get("sat_name"): payload["sat_name"] = cfg.get("sat_name")
                if cfg.get("sat_mode"): payload["sat_mode"] = cfg.get("sat_mode")
                if cfg.get("prop_mode"): payload["prop_mode"] = cfg.get("prop_mode")
                if cfg.get("power") is not None: payload["power"] = cfg.get("power")

                try:
                    r = requests.post(cfg.get("WAVELOG_API_URL"), json=payload, timeout=4)
                    if r.status_code == 200:
                        status_data["wavelog_status"] = "OK (Sent)"
                        log_to_widget(f"Wavelog updated: {format_freq(tx_freq)} ({mode})")
                    else:
                        status_data["wavelog_status"] = f"Error ({r.status_code})"
                        log_to_widget(f"API Error: {r.status_code}")
                except Exception as e:
                    status_data["wavelog_status"] = "Timeout"
                    log_to_widget(f"API Send Error: {e}")

                last_rx, last_mode = rx_freq, mode

            time.sleep(poll)

        except Exception as e:
            log_to_widget(f"Loop error: {e}")
            try: ser.close()
            except: pass
            ser = None
            time.sleep(2)

# ================= GUI WIDGET =================
class WidgetApp:
    def __init__(self, root):
        self.root = root
        self.root.title("sdrconsole2wavelog")
        self.root.geometry("500x365")
        self.root.resizable(False, False)
        self.root.configure(bg="#1e1e2e")
        self.root.attributes("-topmost", False)

        # Style für die ttk-Komponenten (Combobox) anpassen, damit es zum Dark-Theme passt
        self.style = ttk.Style()
        self.style.theme_use('clam')
        self.style.configure("TCombobox", fieldbackground="#313244", background="#11111b", foreground="#cdd6f4", arrowcolor="#cdd6f4")

        self.menubar = tk.Menu(self.root)
        self.filemenu = tk.Menu(self.menubar, tearoff=0)
        self.filemenu.add_command(label="Settings", command=self.open_settings)
        self.filemenu.add_command(label="About", command=self.show_about)
        self.filemenu.add_separator()
        self.filemenu.add_command(label="Exit", command=self.root.quit)
        self.menubar.add_cascade(label="File", menu=self.filemenu)
        self.root.config(menu=self.menubar)

        lbl_font = ("Arial", 9, "bold")
        val_font = ("Consolas", 11, "bold")

        # Row 1: SDR-Console Status & Start/Stop Button
        tk.Label(root, text="SDR-Console:", bg="#1e1e2e", fg="#a6adc8", font=lbl_font).grid(row=0, column=0, sticky="w", padx=15, pady=4)
        self.lbl_com = tk.Label(root, text="---", bg="#1e1e2e", fg="#fab387", font=val_font)
        self.lbl_com.grid(row=0, column=1, sticky="w", pady=4)

        self.btn_toggle = tk.Button(root, text="Stop", bg="#f38ba8", fg="#11111b", font=("Arial", 8, "bold"), bd=0, padx=10, command=self.toggle_polling)
        self.btn_toggle.grid(row=0, column=4, columnspan=2, sticky="e", padx=15, pady=4)

        # Row 2: Satellite, Sat Mode & Power
        tk.Label(root, text="Sat:", bg="#1e1e2e", fg="#a6adc8", font=lbl_font).grid(row=1, column=0, sticky="w", padx=15, pady=4)
        self.lbl_sat_name = tk.Label(root, text="---", bg="#1e1e2e", fg="#cba6f7", font=val_font)
        self.lbl_sat_name.grid(row=1, column=1, sticky="w", pady=4)
        
        tk.Label(root, text="Sat Mode:", bg="#1e1e2e", fg="#a6adc8", font=lbl_font).grid(row=1, column=2, sticky="w", padx=15, pady=4)
        self.lbl_sat_mode = tk.Label(root, text="---", bg="#1e1e2e", fg="#89dceb", font=val_font)
        self.lbl_sat_mode.grid(row=1, column=3, sticky="w", pady=4)
        
        tk.Label(root, text="Power:", bg="#1e1e2e", fg="#a6adc8", font=lbl_font).grid(row=1, column=4, sticky="w", padx=15, pady=4)
        self.lbl_power = tk.Label(root, text="---", bg="#1e1e2e", fg="#f9e2af", font=val_font)
        self.lbl_power.grid(row=1, column=5, sticky="w", pady=4)

        # Row 3: RX Frequency
        tk.Label(root, text="RX:", bg="#1e1e2e", fg="#a6adc8", font=lbl_font).grid(row=2, column=0, sticky="w", padx=15, pady=4)
        self.lbl_rx = tk.Label(root, text="---", bg="#1e1e2e", fg="#89b4fa", font=val_font)
        self.lbl_rx.grid(row=2, column=1, columnspan=5, sticky="w", pady=4)

        # Row 4: TX Frequency
        tk.Label(root, text="TX:", bg="#1e1e2e", fg="#a6adc8", font=lbl_font).grid(row=3, column=0, sticky="w", padx=15, pady=4)
        self.lbl_tx = tk.Label(root, text="---", bg="#1e1e2e", fg="#f38ba8", font=val_font)
        self.lbl_tx.grid(row=3, column=1, columnspan=5, sticky="w", pady=4)

        # Row 5: Mode
        tk.Label(root, text="Mode:", bg="#1e1e2e", fg="#a6adc8", font=lbl_font).grid(row=4, column=0, sticky="w", padx=15, pady=4)
        self.lbl_mode = tk.Label(root, text="---", bg="#1e1e2e", fg="#a6e3a1", font=val_font)
        self.lbl_mode.grid(row=4, column=1, columnspan=5, sticky="w", pady=4)

        # Row 6: Wavelog API Status
        tk.Label(root, text="Wavelog API:", bg="#1e1e2e", fg="#a6adc8", font=lbl_font).grid(row=5, column=0, sticky="w", padx=15, pady=4)
        self.lbl_wave = tk.Label(root, text="Ready", bg="#1e1e2e", fg="#cdd6f4", font=("Arial", 9))
        self.lbl_wave.grid(row=5, column=1, columnspan=5, sticky="w", pady=4)

        # Row 7: Status Log Header
        tk.Label(root, text="Status Log:", bg="#1e1e2e", fg="#7f849c", font=("Arial", 8, "bold")).grid(row=6, column=0, sticky="w", padx=15, pady=2)
        
        self.log_area = scrolledtext.ScrolledText(root, width=66, height=5, bg="#11111b", fg="#a6e3a1", font=("Consolas", 8), bd=0, highlightthickness=0)
        self.log_area.grid(row=7, column=0, columnspan=6, padx=15, pady=5)
        
        self.last_log_len = 0
        self.update_gui()

    def toggle_polling(self):
        if running_event.is_set():
            running_event.clear()
            self.btn_toggle.config(text="Start", bg="#a6e3a1")
            log_to_widget("Polling manually stopped.")
        else:
            running_event.set()
            self.btn_toggle.config(text="Stop", bg="#f38ba8")
            log_to_widget("Polling manually started.")

    def show_about(self):
        about_text = "DG3FEH\nDr. Holger Lange\ndg3feh@dg3feh.de"
        messagebox.showinfo("About", about_text, parent=self.root)

    def update_gui(self):
        com_st = status_data["com_status"]
        
        if "Connected" in com_st:
            self.lbl_com.config(text="Connected", fg="#a6e3a1")
        elif "Waiting" in com_st:
            self.lbl_com.config(text="Waiting for port...", fg="#f9e2af")
        elif "Searching" in com_st:
            self.lbl_com.config(text="Searching...", fg="#f38ba8")
        else:
            self.lbl_com.config(text="Disconnected", fg="#f38ba8")

        self.lbl_sat_name.config(text=cfg.get("sat_name") if cfg.get("sat_name") else "None")
        self.lbl_sat_mode.config(text=cfg.get("sat_mode") if cfg.get("sat_mode") else "---")
        self.lbl_power.config(text=f"{cfg.get('power')} W" if cfg.get("power") is not None else "---")

        self.lbl_rx.config(text=status_data["rx_freq"])
        self.lbl_tx.config(text=status_data["tx_freq"])
        self.lbl_mode.config(text=status_data["mode"])
        self.lbl_wave.config(text=status_data["wavelog_status"])

        with log_lock:
            current_log_len = len(gui_logs)
            if current_log_len != self.last_log_len:
                self.log_area.config(state=tk.NORMAL)
                self.log_area.delete("1.0", tk.END)
                self.log_area.insert(tk.END, "\n".join(gui_logs))
                self.log_area.see(tk.END)
                self.log_area.config(state=tk.DISABLED)
                self.last_log_len = current_log_len

        self.root.after(250, self.update_gui)

    # ================= SETTINGS SUB-WINDOW =================
    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.geometry("460x420")
        win.resizable(False, False)
        win.configure(bg="#1e1e2e")
        win.attributes("-topmost", False)

        lbl_font = ("Arial", 9, "bold")
        entries = {}

        # 1. Verfügbare COM Ports vom System abfragen
        ports = serial.tools.list_ports.comports()
        available_ports = [p.device for p in ports]
        
        # Falls der aktuell konfigurierte Port nicht aktiv eingesteckt ist, packen wir ihn trotzdem in die Liste
        current_com = cfg.get("COM_PORT", "COM10")
        if current_com not in available_ports:
            available_ports.insert(0, current_com)
        
        # Sortieren, damit es ordentlich aussieht
        available_ports.sort(key=lambda x: int(re.sub(r'\D', '', x)) if re.sub(r'\D', '', x) else 0)

        fields = [
            ("COM_PORT", "COM Port:"),  # Wird separat als Combobox behandelt
            ("BAUDRATE", "Baudrate:"),
            ("WAVELOG_API_URL", "Wavelog API URL:"),
            ("API_KEY", "Wavelog API Key:"),
            ("RADIO_NAME", "Radio Name:"),
            ("prop_mode", "Propagation Mode:"),
            ("sat_name", "Satellite Name:"),
            ("sat_mode", "Sat Mode (e.g. S/X):"),
            ("SAT_RXTX_OFFSET_HZ", "Offset (RX-TX Hz):"),
            ("POLL_INTERVAL", "Poll Interval (sec):"),
            ("power", "TX Power (Watts):")
        ]

        for idx, (key, label_text) in enumerate(fields):
            tk.Label(win, text=label_text, bg="#1e1e2e", fg="#a6adc8", font=lbl_font).grid(row=idx, column=0, sticky="w", padx=15, pady=3)
            
            if key == "COM_PORT":
                # Pulldown-Menü (Combobox) für den COM Port erstellen
                combo = ttk.Combobox(win, values=available_ports, width=33, state="readonly")
                combo.grid(row=idx, column=1, sticky="w", padx=10, pady=3)
                
                # Aktuellen Wert vorauswählen
                if current_com in available_ports:
                    combo.set(current_com)
                elif available_ports:
                    combo.current(0)
                
                entries[key] = combo
            else:
                # Normale Entry-Felder für alle anderen Werte
                ent = tk.Entry(win, width=35, bg="#313244", fg="#cdd6f4", bd=1, relief="flat", insertbackground="white")
                ent.grid(row=idx, column=1, sticky="w", padx=10, pady=3)
                
                current_val = cfg.get(key, "")
                ent.insert(0, str(current_val))
                entries[key] = ent

        lbl_hint = tk.Label(
            win, 
            text="Note: Changes will take effect after you Stop and Start the polling again.", 
            bg="#1e1e2e", 
            fg="#fab387",
            font=("Arial", 8, "italic"),
            wraplength=430,
            justify="center"
        )
        lbl_hint.grid(row=len(fields), column=0, columnspan=2, pady=(15, 5), padx=15)

        def save_action():
            try:
                cfg["COM_PORT"] = entries["COM_PORT"].get().strip()
                cfg["BAUDRATE"] = int(entries["BAUDRATE"].get().strip())
                cfg["WAVELOG_API_URL"] = entries["WAVELOG_API_URL"].get().strip()
                cfg["API_KEY"] = entries["API_KEY"].get().strip()
                cfg["RADIO_NAME"] = entries["RADIO_NAME"].get().strip()
                cfg["prop_mode"] = entries["prop_mode"].get().strip()
                
                s_name = entries["sat_name"].get().strip()
                cfg["sat_name"] = s_name if s_name else None
                
                s_mode = entries["sat_mode"].get().strip()
                cfg["sat_mode"] = s_mode if s_mode else None
                
                cfg["SAT_RXTX_OFFSET_HZ"] = int(entries["SAT_RXTX_OFFSET_HZ"].get().strip())
                cfg["POLL_INTERVAL"] = float(entries["POLL_INTERVAL"].get().strip())
                
                pwr = entries["power"].get().strip()
                cfg["power"] = int(pwr) if pwr else None

                save_config()
                log_to_widget("Configuration saved. Please restart polling to apply changes.")
                win.destroy()
                messagebox.showinfo("Success", "Settings have been saved! Please toggle Stop/Start to apply.", parent=self.root)
            except Exception as ex:
                messagebox.showerror("Error", f"Invalid input:\n{ex}", parent=win)

        btn_save = tk.Button(win, text="Save", bg="#a6e3a1", fg="#11111b", font=lbl_font, bd=0, padx=15, pady=4, command=save_action)
        btn_save.grid(row=len(fields)+1, column=0, columnspan=2, pady=(5, 15))

def main():
    root = tk.Tk()
    app = WidgetApp(root)
    threading.Thread(target=cat_loop, daemon=True).start()
    root.mainloop()

if __name__ == "__main__":
    main()