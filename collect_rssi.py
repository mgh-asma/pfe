#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Collecte RSSI pour calibration BLE
Usage :
  python3 collect_rssi.py --anchor A1 --tag_mac a0:f2:62:a4:7d:a2 --distance 1.0
  python3 collect_rssi.py --anchor A2 --tag_mac a0:f2:62:a4:7d:a2 --distance 2.0 --n 30
"""
import argparse
import csv
import json
import os
import time
from datetime import datetime
import paho.mqtt.client as mqtt


def parse_args():
    p = argparse.ArgumentParser(description="Collecte RSSI depuis MQTT et sauvegarde en CSV.")
    p.add_argument("--broker",      default="localhost")
    p.add_argument("--port",        type=int,   default=1883)
    p.add_argument("--topic",       default="rtls/raw")
    p.add_argument("--anchor",      required=True,              help="ex: A1")
    p.add_argument("--tag_mac",     required=True,              help="ex: a0:f2:62:a4:7d:a2")
    p.add_argument("--distance",    type=float, required=True,  help="Distance en metres")
    p.add_argument("--n",           type=int,   default=30,     help="Nombre d'echantillons a collecter")
    p.add_argument("--min_samples", type=int,   default=20,     help="Min echantillons par paquet MQTT")
    p.add_argument("--scenario",    default="LOS",              help="LOS / NLOS / etc.")
    p.add_argument("--out_dir",     default="data",             help="Dossier de sortie")
    p.add_argument("--timeout",     type=int,   default=180,    help="Timeout en secondes")
    return p.parse_args()


def main():
    args = parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = os.path.join(
        args.out_dir,
        f"calib_{args.scenario}_{args.anchor}_{args.tag_mac.replace(':','')}_{args.distance}m_{ts}.csv"
    )

    rows                = []
    dropped_bad_samples = 0
    dropped_other       = 0

    print("=" * 55)
    print(f"  Collecte RSSI — Ancre {args.anchor} @ {args.distance}m")
    print(f"  Tag MAC    : {args.tag_mac}")
    print(f"  Scenario   : {args.scenario}")
    print(f"  Objectif   : {args.n} echantillons")
    print(f"  Sortie     : {out_csv}")
    print("=" * 55)
    print(f"  En attente des donnees MQTT...")
    print(f"  {'-'*45}")

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            print(f"  MQTT connecte")
            client.subscribe(args.topic)
        else:
            print(f"  MQTT erreur connexion rc={rc}")

    def on_message(client, userdata, msg):
        nonlocal dropped_bad_samples, dropped_other

        try:
            payload = msg.payload.decode("utf-8", errors="ignore")
            p       = json.loads(payload)
        except Exception:
            dropped_other += 1
            return

        # Filtrer par ancre et MAC
        if p.get("anchor") != args.anchor:
            dropped_other += 1
            return
        if p.get("mac") != args.tag_mac:
            dropped_other += 1
            return

        rssi    = p.get("rssi")
        samples = p.get("samples", 0)

        if not isinstance(rssi, (int, float)):
            dropped_other += 1
            return

        if int(samples) < args.min_samples:
            dropped_bad_samples += 1
            return

        rows.append({
            "t_wall"    : time.time(),
            "scenario"  : args.scenario,
            "anchor"    : args.anchor,
            "tag_mac"   : args.tag_mac,
            "distance_m": args.distance,
            "rssi"      : float(rssi),
            "samples"   : int(samples),
            "ts_dev"    : p.get("ts", None),
        })

        print(f"  [{len(rows):>3}/{args.n}]  RSSI = {rssi:>5} dBm  |  samples = {samples}")

        if len(rows) >= args.n:
            client.disconnect()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.broker, args.port, keepalive=30)

    t0 = time.time()
    while True:
        client.loop(timeout=1.0)
        if len(rows) >= args.n:
            break
        if time.time() - t0 > args.timeout:
            print(f"\n  Timeout atteint ({args.timeout}s)")
            break

    client.disconnect()

    print(f"  {'-'*45}")

    if not rows:
        print(f"  Aucun echantillon capture !")
        print(f"  Verifications :")
        print(f"    - Ancre {args.anchor} bien allumee ?")
        print(f"    - MAC correcte : {args.tag_mac} ?")
        print(f"    - Topic MQTT : {args.topic} ?")
        print(f"  Rejetes (samples < {args.min_samples}) : {dropped_bad_samples}")
        print(f"  Rejetes (autres)                      : {dropped_other}")
        return

    # Sauvegarder CSV
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    import statistics
    rssi_vals = [r["rssi"] for r in rows]
    print(f"\n  Resultat :")
    print(f"    Echantillons  : {len(rows)}")
    print(f"    Mediane RSSI  : {statistics.median(rssi_vals):.1f} dBm")
    print(f"    Std           : {statistics.stdev(rssi_vals):.2f} dBm")
    print(f"    Min / Max     : {min(rssi_vals):.0f} / {max(rssi_vals):.0f} dBm")
    print(f"    Rejetes (samples < {args.min_samples}) : {dropped_bad_samples}")
    print(f"\n  Sauvegarde → {out_csv}")
    print("=" * 55)


if __name__ == "__main__":
    main()
