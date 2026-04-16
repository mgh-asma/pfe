#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Collecte de données fingerprinting pour RTLS BLE
- Pose le tag à chaque point de la grille
- Le script collecte les RSSI des 3 ancres simultanément
- Sauvegarde dans fingerprint_data.csv
- Compatible avec ml_model.py

Usage :
    python3 fingerprint_collect.py --x 1.0 --y 0.0
    python3 fingerprint_collect.py --x 3.5 --y 1.17 --samples 30
"""

import argparse
import json
import statistics
import csv
import os
from collections import defaultdict
from datetime import datetime
import paho.mqtt.client as mqtt

# -----------------------------------------
# CONFIGURATION
# -----------------------------------------
BROKER       = "localhost"
TOPIC        = "rtls/raw"
OUTPUT_FILE  = "fingerprint_data.csv"
ANCHORS      = ["A1", "A2", "A3"]
TARGET_MAC   = "a0:f2:62:a4:7d:a2"

# Offsets identiques à trilateration_kalman_final.py
RSSI_OFFSETS = {
    "A1": -4.0,
    "A2": -8.5,
    "A3": -1.9,
}

# -----------------------------------------
# ARGUMENTS
# -----------------------------------------
parser = argparse.ArgumentParser(description="Collecte fingerprinting BLE")
parser.add_argument("--x",       type=float, required=True,  help="Coordonnée X du point (mètres)")
parser.add_argument("--y",       type=float, required=True,  help="Coordonnée Y du point (mètres)")
parser.add_argument("--samples", type=int,   default=30,     help="Nombre d'échantillons par ancre (défaut: 30)")
parser.add_argument("--out",     type=str,   default=OUTPUT_FILE, help="Fichier de sortie CSV")
args = parser.parse_args()

X_POS    = args.x
Y_POS    = args.y
N_TARGET = args.samples
OUT_FILE = args.out

# -----------------------------------------
# ÉTAT
# -----------------------------------------
rssi_collected = defaultdict(list)
collection_done = False

# -----------------------------------------
# CALLBACKS MQTT
# -----------------------------------------
def on_connect(client, userdata, flags, rc, properties=None):
    print(f"  MQTT connecte (rc={rc})")
    client.subscribe(TOPIC)

def on_message(client, userdata, msg):
    global collection_done
    if collection_done:
        return
    try:
        data = json.loads(msg.payload.decode())
        anchor = data.get("anchor")
        rssi   = data.get("rssi")
        mac    = data.get("mac", "")

        if anchor not in ANCHORS:
            return
        if TARGET_MAC not in mac:
            return
        if rssi is None:
            return

        # Appliquer offset
        rssi_corrige = float(rssi) + RSSI_OFFSETS.get(anchor, 0.0)
        rssi_collected[anchor].append(rssi_corrige)

        # Afficher progression
        counts = {a: len(rssi_collected[a]) for a in ANCHORS}
        print(f"  [{anchor}] {len(rssi_collected[anchor]):>2}/{N_TARGET}  "
              f"RSSI={rssi_corrige:.1f} dBm  "
              f"| A1={counts['A1']} A2={counts['A2']} A3={counts['A3']}", end="\r")

        # Vérifier si tous les ancres ont assez d'échantillons
        if all(len(rssi_collected[a]) >= N_TARGET for a in ANCHORS):
            collection_done = True
            client.disconnect()

    except Exception as e:
        print(f"\n  Erreur message : {e}")

# -----------------------------------------
# SAUVEGARDE CSV
# -----------------------------------------
def save_to_csv():
    file_exists = os.path.isfile(OUT_FILE)

    # Calculer médianes et std
    results = {}
    for anchor in ANCHORS:
        vals = rssi_collected[anchor][:N_TARGET]
        results[anchor] = {
            "median": round(statistics.median(vals), 2),
            "std":    round(statistics.stdev(vals) if len(vals) > 1 else 0.0, 3),
            "n":      len(vals),
        }

    row = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "x":         X_POS,
        "y":         Y_POS,
        "rssi_A1":   results["A1"]["median"],
        "rssi_A2":   results["A2"]["median"],
        "rssi_A3":   results["A3"]["median"],
        "std_A1":    results["A1"]["std"],
        "std_A2":    results["A2"]["std"],
        "std_A3":    results["A3"]["std"],
        "n_A1":      results["A1"]["n"],
        "n_A2":      results["A2"]["n"],
        "n_A3":      results["A3"]["n"],
    }

    fieldnames = list(row.keys())

    with open(OUT_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    return results

# -----------------------------------------
# MAIN
# -----------------------------------------
def main():
    print("=" * 55)
    print(f"  Fingerprinting BLE — Point ({X_POS}, {Y_POS})")
    print(f"  Objectif : {N_TARGET} échantillons par ancre")
    print(f"  Fichier  : {OUT_FILE}")
    print(f"  Offsets  : A1={RSSI_OFFSETS['A1']} A2={RSSI_OFFSETS['A2']} A3={RSSI_OFFSETS['A3']}")
    print("=" * 55)
    print()
    print("  Orientation tag : entre A2 et A3 — NE PAS BOUGER")
    print()
    input("  Appuie sur ENTREE quand le tag est en position...")
    print()
    print("  Collecte en cours...")
    print()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER, 1883, 60)

    try:
        client.loop_forever()
    except Exception as e:
        print(f"\n  Erreur : {e}")

    print()
    print()

    # Vérifier qu'on a assez de données
    missing = [a for a in ANCHORS if len(rssi_collected[a]) < N_TARGET]
    if missing:
        print(f"  Données insuffisantes pour : {missing}")
        print(f"  Collecté : { {a: len(rssi_collected[a]) for a in ANCHORS} }")
        return

    # Sauvegarder
    results = save_to_csv()

    print("  Résultats :")
    print(f"  {'Ancre':>6}  {'Médiane':>10}  {'Std':>8}  {'N':>5}")
    print(f"  {'-'*38}")
    for anchor in ANCHORS:
        r = results[anchor]
        print(f"  {anchor:>6}  {r['median']:>10.2f}  {r['std']:>8.3f}  {r['n']:>5}")

    print()
    print(f"  Sauvegarde → {OUT_FILE}")
    print("=" * 55)
    print()

    # Compter les points déjà collectés
    if os.path.isfile(OUT_FILE):
        with open(OUT_FILE, "r") as f:
            n_points = sum(1 for _ in f) - 1  # -1 pour le header
        print(f"  Total points collectés jusqu'ici : {n_points}/41")
    print()

if __name__ == "__main__":
    main()
