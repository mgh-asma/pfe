#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Calibration BLE — Calcul automatique de n, RMSE, R²
Lit les fichiers raw_A1.csv, raw_A2.csv, raw_A3.csv
Génère calibration_anchors.json
"""
import csv
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime

# ─────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────
ANCHORS    = ["A1", "A2", "A3"]
CSV_FILES  = {a: f"raw_{a}.csv" for a in ANCHORS}
OUTPUT     = "calibration_anchors.json"

# Distances à exclure du calcul du modèle par ancre
# (les données restent dans le CSV, on les ignore juste pour le modèle)
EXCLUDE_FROM_MODEL = {
    "A1": {6.0},              # garder tous les points
    "A2": set(),              # exclure seulement 7m
    "A3": set(),              # garder tous les points
}

# ─────────────────────────────────────────
# LECTURE DES FICHIERS CSV
# ─────────────────────────────────────────
def load_csv(filepath):
    """
    Lit un fichier raw_Ax.csv et retourne
    un dict {distance: [rssi, ...]}
    """
    groups = defaultdict(list)
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    d    = float(row["distance_m"])
                    rssi = float(row["rssi"])
                    groups[d].append(rssi)
                except (ValueError, KeyError):
                    continue
    except FileNotFoundError:
        print(f"  ⚠️  Fichier introuvable : {filepath}")
        return None
    return groups

# ─────────────────────────────────────────
# SÉLECTION DE LA MEILLEURE SESSION
# ─────────────────────────────────────────
def best_session(values, session_gap=60):
    """
    Découpe les valeurs en sessions de 30 échantillons
    et retourne celle avec la plus faible std.
    (Ici on travaille sur les valeurs brutes sans timestamp
     donc on prend des blocs de 30.)
    """
    n = 30
    sessions = [values[i:i+n] for i in range(0, len(values), n) if len(values[i:i+n]) >= n]
    if not sessions:
        # Prendre tout si moins de 30
        return values
    # Retourner la session avec la std minimale
    return min(sessions, key=lambda s: statistics.stdev(s) if len(s) > 1 else 0)

# ─────────────────────────────────────────
# CALCUL DU MODÈLE LOG-DISTANCE
# ─────────────────────────────────────────
def compute_model(medians):
    """
    medians : dict {distance: median_rssi}
    Retourne A, n, rmse, r2, comparaison
    """
    A = medians[1.0]
    dists = sorted(medians.keys())

    # Estimation de n par moindres carrés
    pts = [(d, m) for d, m in medians.items() if d >= 1.0]
    num = sum((-10 * math.log10(d)) * (rssi - A) for d, rssi in pts)
    den = sum((-10 * math.log10(d)) ** 2      for d, rssi in pts)
    n   = num / den if den != 0 else 2.0

    # Prédictions
    actual    = [medians[d] for d in dists]
    predicted = [A - 10 * n * math.log10(d) for d in dists]

    # RMSE et R²
    mean_y = sum(actual) / len(actual)
    ss_tot = sum((y - mean_y) ** 2          for y in actual)
    ss_res = sum((a - p) ** 2               for a, p in zip(actual, predicted))
    rmse   = math.sqrt(ss_res / len(actual))
    r2     = 1 - ss_res / ss_tot if ss_tot != 0 else 0

    comparison = {
        str(d): {
            "rssi_reel"  : medians[d],
            "rssi_predit": round(A - 10 * n * math.log10(d), 2),
            "erreur_dB"  : round(medians[d] - (A - 10 * n * math.log10(d)), 2)
        }
        for d in dists
    }

    return A, n, rmse, r2, comparison

# ─────────────────────────────────────────
# PIPELINE PRINCIPAL
# ─────────────────────────────────────────
def calibrate():
    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model"        : {
            "formula" : "RSSI = A - 10 * n * log10(d)",
            "inverse" : "d = 10 ^ ((A - RSSI) / (10 * n))"
        },
        "anchors": {}
    }

    for anchor in ANCHORS:
        print(f"\n{'='*45}")
        print(f"  Traitement {anchor}")
        print(f"{'='*45}")

        groups = load_csv(CSV_FILES[anchor])
        if groups is None:
            continue

        # Vérifier que 1m est présent (nécessaire pour A)
        if 1.0 not in groups:
            print(f"  ⚠️  Distance 1m manquante pour {anchor} — ignoré")
            continue

        # Distances exclues pour cet ancre
        excluded = EXCLUDE_FROM_MODEL.get(anchor, set())

        # Médiane par distance (meilleure session de 30)
        medians = {}
        print(f"\n  {'Distance':>10}  {'N valeurs':>10}  {'Médiane':>10}  {'Std':>8}  {'Statut':>8}")
        print(f"  {'-'*58}")
        for d in sorted(groups.keys()):
            vals    = best_session(groups[d])
            med     = statistics.median(vals)
            std     = statistics.stdev(vals) if len(vals) > 1 else 0
            if d in excluded:
                print(f"  {d:>10.1f}m  {len(vals):>10}  {med:>10.1f}  {std:>8.2f}  {'EXCLU':>8}")
                continue
            medians[d] = med
            print(f"  {d:>10.1f}m  {len(vals):>10}  {med:>10.1f}  {std:>8.2f}  {'✓':>8}")

        # Vérifier monotonie
        dists = sorted(medians.keys())
        mono_ok = all(medians[dists[i]] <= medians[dists[i-1]]
                      for i in range(1, len(dists)))
        if not mono_ok:
            print(f"\n  ⚠️  Monotonie non respectée pour {anchor}")
            for i in range(1, len(dists)):
                ok = medians[dists[i]] <= medians[dists[i-1]]
                print(f"     {dists[i-1]}m({medians[dists[i-1]]}) → "
                      f"{dists[i]}m({medians[dists[i]]}) {'✓' if ok else '✗'}")

        # Calcul du modèle
        A, n, rmse, r2, comparison = compute_model(medians)

        print(f"\n  Résultats modèle :")
        print(f"    A (RSSI@1m) = {A:.2f} dBm")
        print(f"    n           = {n:.4f}")
        print(f"    RMSE        = {rmse:.4f} dB")
        print(f"    R²          = {r2:.4f}")

        result["anchors"][anchor] = {
            "A_dBm"             : round(A, 2),
            "n"                 : round(n, 4),
            "rmse_dB"           : round(rmse, 4),
            "r2"                : round(r2, 4),
            "monotonie_ok"      : mono_ok,
            "calibration_points": {
                str(d): medians[d] for d in dists
            },
            "comparison"        : comparison
        }

    # Sauvegarde JSON
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*45}")
    print(f"  ✓ Fichier généré : {OUTPUT}")
    print(f"{'='*45}\n")

    # Résumé final
    print("  RÉSUMÉ FINAL")
    print(f"  {'Ancre':>6}  {'A (dBm)':>9}  {'n':>7}  {'RMSE':>8}  {'R²':>8}  {'Mono':>6}")
    print(f"  {'-'*52}")
    for a, v in result["anchors"].items():
        mono = "✓" if v["monotonie_ok"] else "✗"
        print(f"  {a:>6}  {v['A_dBm']:>9.2f}  {v['n']:>7.4f}  "
              f"{v['rmse_dB']:>8.4f}  {v['r2']:>8.4f}  {mono:>6}")

if __name__ == "__main__":
    calibrate()
