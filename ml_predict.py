#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ML RTLS BLE — Prediction temps reel
Utilise le meilleur modele (KNN k=15) pour predire la position
a partir des RSSI recus via MQTT
"""
import json
import time
import joblib
import numpy as np
import paho.mqtt.client as mqtt
import requests
from collections import deque
from datetime import datetime
import os

# -----------------------------------------
# CONFIG
# -----------------------------------------
BROKER        = "localhost"
TOPIC         = "rtls/raw"
API_URL       = "http://localhost:5000"
MODELS_DIR    = "models"
MODEL_NAME    = "mlp.pkl"   # meilleur modele
WINDOW_SIZE   = 15
MIN_RSSI_VALID = 3

ANCHORS = ["A1", "A2", "A3"]

# -----------------------------------------
# CHARGEMENT DU MODELE
# -----------------------------------------
def load_model():
    model_path  = os.path.join(MODELS_DIR, MODEL_NAME)
    scaler_path = os.path.join(MODELS_DIR, "scaler.pkl")

    if not os.path.exists(model_path):
        print(f"  ERREUR : modele introuvable : {model_path}")
        print(f"  Lancez d'abord : python3 ml_model.py")
        exit(1)

    model  = joblib.load(model_path)
    scaler = joblib.load(scaler_path)
    print(f"  Modele charge : {MODEL_NAME}")
    return model, scaler

# -----------------------------------------
# STOCKAGE RSSI PAR FENETRE GLISSANTE
# -----------------------------------------
rssi_windows = {a: deque(maxlen=WINDOW_SIZE) for a in ANCHORS}
last_update  = {a: 0 for a in ANCHORS}

def get_median_rssi():
    """Retourne la mediane RSSI de chaque ancre si assez de donnees."""
    medians = {}
    for a in ANCHORS:
        w = list(rssi_windows[a])
        if len(w) >= MIN_RSSI_VALID:
            medians[a] = float(np.median(w))
        else:
            return None  # pas assez de donnees
    return medians

def predict_position(model, scaler, rssi):
    """Predit la position a partir des RSSI."""
    X = np.array([[rssi["A1"], rssi["A2"], rssi["A3"]]])
    X_sc = scaler.transform(X)
    pred = model.predict(X_sc)[0]
    return float(pred[0]), float(pred[1])

def send_to_api(data):
    """Envoie la position predite a l'API Flask."""
    try:
        requests.post(
            API_URL + "/update/ml",
            json=data,
            timeout=1
        )
    except Exception:
        pass

# -----------------------------------------
# MQTT
# -----------------------------------------
model, scaler = load_model()

def on_connect(client, userdata, flags, rc, properties=None):
    print(f"  Connecte au broker MQTT (rc={rc})")
    client.subscribe(TOPIC)
    print(f"  Topic : {TOPIC}")

def on_message(client, userdata, msg):
    try:
        data   = json.loads(msg.payload.decode())
        anchor = data.get("anchor")
        rssi   = data.get("rssi")

        if anchor not in ANCHORS or rssi is None:
            return

        rssi_windows[anchor].append(float(rssi))
        last_update[anchor] = time.time()

        # Prediction si toutes les ancres ont assez de donnees
        medians = get_median_rssi()
        if medians is None:
            return

        x_pred, y_pred = predict_position(model, scaler, medians)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        print(f"[{ts}]  ML=({x_pred:6.2f}, {y_pred:6.2f})  "
              f"RSSI=[{medians['A1']:.1f}, {medians['A2']:.1f}, {medians['A3']:.1f}] dBm")

        send_to_api({
            "x"        : round(x_pred, 3),
            "y"        : round(y_pred, 3),
            "timestamp": ts,
            "d_A1"     : 0.0,
            "d_A2"     : 0.0,
            "d_A3"     : 0.0,
            "rssi_A1"  : medians["A1"],
            "rssi_A2"  : medians["A2"],
            "rssi_A3"  : medians["A3"],
            "tag_id"   : "TAG1",
            "method"   : "ml_knn",
        })

    except Exception as e:
        print(f"  Erreur : {e}")

# -----------------------------------------
# LANCEMENT
# -----------------------------------------
if __name__ == "__main__":
    print("=" * 55)
    print("  ML RTLS BLE — Prediction temps reel")
    print(f"  Modele : {MODEL_NAME}")
    print(f"  Fenetre RSSI : {WINDOW_SIZE} mesures")
    print("=" * 55)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(BROKER, 1883, 60)
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n  Arret.")
    except Exception as e:
        print(f"  Erreur connexion MQTT : {e}")
