#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trilateration BLE + Filtre Kalman 2D + Ponderation par rejets + API Flask
- Calibration depuis calibration_anchors.json
- Fenetre glissante de 15 RSSI par ancre
- Rejet des valeurs aberrantes (> 1.5 * std)
- Ponderation : ancre instable = poids faible
- Offsets de correction par ancre
- Envoi a l'API Flask
- Sauvegarde dans positions.csv
"""
import json
import math
import numpy as np
import statistics
from collections import deque
from datetime import datetime
import csv
import paho.mqtt.client as mqtt
import requests
from database import init_db, insert_position

# -----------------------------------------
# CONFIGURATION
# -----------------------------------------
BROKER         = "localhost"
TOPIC          = "rtls/raw"
OUTPUT         = "positions.csv"
CALIB_FILE     = "calibration_anchors.json"
WINDOW_SIZE    = 15
REJECT_FACTOR  = 1.5
MIN_RSSI_VALID = 3
API_URL        = "http://localhost:5000"

# Offsets de correction par ancre (dBm)
RSSI_OFFSETS = {
    "A1": -1.0,
    "A2": -9.0,
    "A3": +5.1,
}

# Coordonnees des ancres (metres)
ANCHOR_POSITIONS = {
    "A1": (0.0,    0.0   ),
    "A2": (7.0,    0.0   ),
    "A3": (3.5,3.5),
}

# -----------------------------------------
# ENVOI A L'API FLASK
# -----------------------------------------
def send_to_api(data):
    try:
        requests.post(f"{API_URL}/update", json=data, timeout=0.5)
    except Exception:
        pass

# -----------------------------------------
# CHARGEMENT CALIBRATION
# -----------------------------------------
def load_calibration(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        anchors = {}
        for name, cfg in data["anchors"].items():
            anchors[name] = {
                "pos": ANCHOR_POSITIONS.get(name, (0, 0)),
                "A"  : cfg["A_dBm"],
                "n"  : cfg["n"],
            }
        print(f"  Calibration chargee depuis {filepath}")
        for a, v in anchors.items():
            print(f"    {a}: A={v['A']} dBm  n={v['n']:.4f}  pos={v['pos']}")
        return anchors
    except FileNotFoundError:
        print(f"  Fichier {filepath} introuvable - valeurs par defaut")
        return {
            "A1": {"pos": ANCHOR_POSITIONS["A1"], "A": -50.5, "n": 2.8636},
            "A2": {"pos": ANCHOR_POSITIONS["A2"], "A": -48.0, "n": 3.4482},
            "A3": {"pos": ANCHOR_POSITIONS["A3"], "A": -48.0, "n": 2.9533},
        }

# -----------------------------------------
# REJET DES ABERRANTES
# -----------------------------------------
def filter_rssi(window):
    vals = list(window)
    if len(vals) < 3:
        return statistics.median(vals), 0
    med = statistics.median(vals)
    std = statistics.stdev(vals)
    if std < 0.5:
        return med, 0
    filtered = [v for v in vals if abs(v - med) <= REJECT_FACTOR * std]
    n_rejected = len(vals) - len(filtered)
    if len(filtered) == 0:
        return med, 0
    return statistics.median(filtered), n_rejected

# -----------------------------------------
# RSSI -> DISTANCE
# -----------------------------------------
def rssi_to_distance(rssi, A, n):
    return 10 ** ((A - rssi) / (10 * n))

# -----------------------------------------
# TRILATERATION PONDEREE (WLS)
# -----------------------------------------
def trilaterate(distances, anchors, weights=None):
    keys = list(distances.keys())
    if len(keys) < 3:
        return None
    if weights is None:
        weights = {k: 1.0 for k in keys}
    ref = keys[0]
    x1, y1 = anchors[ref]["pos"]
    d1 = distances[ref]
    w1 = weights[ref]
    rows_A, rows_b, rows_w = [], [], []
    for k in keys[1:]:
        x2, y2 = anchors[k]["pos"]
        d2 = distances[k]
        w2 = weights[k]
        rows_A.append([2*(x2-x1), 2*(y2-y1)])
        rows_b.append(d1**2 - d2**2 - x1**2 + x2**2 - y1**2 + y2**2)
        rows_w.append(w1 * w2)
    try:
        A_mat = np.array(rows_A, dtype=float)
        b_vec = np.array(rows_b, dtype=float)
        W_mat = np.diag(rows_w)
        AtW = A_mat.T @ W_mat
        pos = np.linalg.solve(AtW @ A_mat, AtW @ b_vec)
        return float(pos[0]), float(pos[1])
    except Exception:
        return None

# -----------------------------------------
# FILTRE KALMAN 2D
# -----------------------------------------
class KalmanFilter2D:
    def __init__(self, process_noise=0.05, measurement_noise=1.5):
        self.x = np.zeros((4, 1))
        self.P = np.eye(4) * 10
        self.initialized = False
        self.Q = np.eye(4) * process_noise
        self.R = np.eye(2) * measurement_noise
        self.H = np.array([[1,0,0,0],[0,1,0,0]], dtype=float)

    def _F(self, dt):
        return np.array([[1,0,dt,0],[0,1,0,dt],[0,0,1,0],[0,0,0,1]], dtype=float)

    def update(self, x_meas, y_meas, dt=1.0):
        z = np.array([[x_meas], [y_meas]])
        if not self.initialized:
            self.x[0,0] = x_meas
            self.x[1,0] = y_meas
            self.initialized = True
            return x_meas, y_meas
        F = self._F(max(0.1, min(dt, 5.0)))
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + self.Q
        S = self.H @ self.P @ self.H.T + self.R
        K = self.P @ self.H.T @ np.linalg.inv(S)
        self.x = self.x + K @ (z - self.H @ self.x)
        self.P = (np.eye(4) - K @ self.H) @ self.P
        return float(self.x[0,0]), float(self.x[1,0])

# -----------------------------------------
# PIPELINE PRINCIPAL
# -----------------------------------------
ANCHORS = load_calibration(CALIB_FILE)
rssi_windows = {a: deque(maxlen=WINDOW_SIZE) for a in ANCHORS}
kalman = KalmanFilter2D(process_noise=0.05, measurement_noise=1.5)
last_time = [datetime.now()]

csv_file = open(OUTPUT, "w", newline="", encoding="utf-8")
writer = csv.writer(csv_file)
writer.writerow([
    "timestamp",
    "x_raw", "y_raw",
    "x_kalman", "y_kalman",
    "d_A1", "d_A2", "d_A3",
    "rssi_A1", "rssi_A2", "rssi_A3",
    "rejected_A1", "rejected_A2", "rejected_A3",
    "weight_A1", "weight_A2", "weight_A3",
])

def try_localize():
    if not all(len(rssi_windows[a]) >= MIN_RSSI_VALID for a in ANCHORS):
        return

    distances = {}
    rssi_used = {}
    rejected = {}

    for anchor, cfg in ANCHORS.items():
        med_rssi, n_rej = filter_rssi(rssi_windows[anchor])
        rejected[anchor] = n_rej
        rssi_used[anchor] = round(med_rssi, 1)
        distances[anchor] = rssi_to_distance(med_rssi, cfg["A"], cfg["n"])

    weights = {a: 1.0 / (1.0 + rejected[a]) for a in ANCHORS}

    pos = trilaterate(distances, ANCHORS, weights)
    if pos is None:
        return

    x_raw, y_raw = pos

    now = datetime.now()
    dt = max(0.1, min((now - last_time[0]).total_seconds(), 5.0))
    last_time[0] = now

    x_k, y_k = kalman.update(x_raw, y_raw, dt)

    ts = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    writer.writerow([
        ts,
        round(x_raw, 3), round(y_raw, 3),
        round(x_k, 3), round(y_k, 3),
        round(distances["A1"], 3),
        round(distances["A2"], 3),
        round(distances["A3"], 3),
        rssi_used["A1"], rssi_used["A2"], rssi_used["A3"],
        rejected["A1"], rejected["A2"], rejected["A3"],
        round(weights["A1"], 3), round(weights["A2"], 3), round(weights["A3"], 3),
    ])
    csv_file.flush()

    # Sauvegarder dans SQLite
    insert_position({
        "timestamp":   ts,
        "x_raw":       round(x_raw, 3), "y_raw":    round(y_raw, 3),
        "x_kalman":    round(x_k, 3),   "y_kalman": round(y_k, 3),
        "d_A1":        round(distances["A1"], 3),
        "d_A2":        round(distances["A2"], 3),
        "d_A3":        round(distances["A3"], 3),
        "rssi_A1":     rssi_used["A1"],
        "rssi_A2":     rssi_used["A2"],
        "rssi_A3":     rssi_used["A3"],
        "rejected_A1": rejected["A1"],
        "rejected_A2": rejected["A2"],
        "rejected_A3": rejected["A3"],
        "weight_A1":   round(weights["A1"], 3),
        "weight_A2":   round(weights["A2"], 3),
        "weight_A3":   round(weights["A3"], 3),
    })

    send_to_api({
        "x"      : round(x_k, 3),
        "y"      : round(y_k, 3),
        "timestamp": ts,
        "d_A1"   : round(distances["A1"], 3),
        "d_A2"   : round(distances["A2"], 3),
        "d_A3"   : round(distances["A3"], 3),
        "rssi_A1": rssi_used["A1"],
        "rssi_A2": rssi_used["A2"],
        "rssi_A3": rssi_used["A3"],
    })

    rej_info = (f"[rej A1={rejected['A1']} A2={rejected['A2']} A3={rejected['A3']}]  "
                f"[w A1={weights['A1']:.2f} A2={weights['A2']:.2f} A3={weights['A3']:.2f}]")
    print(f"[{ts}]  "
          f"RAW=({x_raw:6.2f}, {y_raw:6.2f})  "
          f"KALMAN=({x_k:6.2f}, {y_k:6.2f})  "
          f"d=[{distances['A1']:.2f}, {distances['A2']:.2f}, {distances['A3']:.2f}]m  "
          f"{rej_info}")

def on_connect(client_, userdata, flags, rc, properties=None):
    print("\nConnecte au broker MQTT")
    print(f"Fenetre={WINDOW_SIZE} RSSI | Rejet={REJECT_FACTOR}xstd | Min={MIN_RSSI_VALID} RSSI\n")
    client_.subscribe(TOPIC)

def on_message(client_, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        anchor = data.get("anchor")
        rssi = data.get("rssi")
        if anchor not in ANCHORS or rssi is None:
            return
        rssi_corrige = float(rssi) + RSSI_OFFSETS.get(anchor, 0.0)
        rssi_windows[anchor].append(rssi_corrige)
        try_localize()
    except Exception as e:
        print(f"Erreur message : {e}")

# -----------------------------------------
# LANCEMENT
# -----------------------------------------
print("=" * 60)
print("  Trilateration BLE + Kalman 2D + Ponderee + Offsets + API")
print("=" * 60)
init_db()

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER, 1883, 60)

try:
    client.loop_forever()
except KeyboardInterrupt:
    print("\nArret.")
finally:
    csv_file.close()
    client.disconnect()
