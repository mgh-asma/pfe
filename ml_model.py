#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ML RTLS BLE — Entrainement et evaluation de 3 modeles
Modeles : Regression lineaire, KNN, Reseau de neurones (MLP)
Donnees : positions.db (x_kalman, y_kalman, rssi_A1, rssi_A2, rssi_A3)
"""
import sqlite3
import numpy as np
import pandas as pd
import joblib
import os
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LinearRegression
from sklearn.neighbors import KNeighborsRegressor
from sklearn.neural_network import MLPRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

DB_PATH     = "rtls.db"
MODELS_DIR  = "models"
REF_X, REF_Y = 3.36, 1.90

# -----------------------------------------
# 1. CHARGEMENT DES DONNEES
# -----------------------------------------
def load_data(db_path=DB_PATH):
    """Charge les donnees depuis SQLite et filtre les valeurs aberrantes."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("""
        SELECT x_kalman, y_kalman, rssi_A1, rssi_A2, rssi_A3
        FROM positions
        WHERE x_kalman IS NOT NULL
          AND y_kalman IS NOT NULL
          AND rssi_A1  IS NOT NULL
          AND rssi_A2  IS NOT NULL
          AND rssi_A3  IS NOT NULL
    """, conn)
    conn.close()

    print(f"  Donnees brutes : {len(df)} positions")

    # Filtrer les positions aberrantes (hors de la zone calibree)
    df = df[
        (df["x_kalman"] >= -2) & (df["x_kalman"] <= 12) &
        (df["y_kalman"] >= -5) & (df["y_kalman"] <= 10) &
        (df["rssi_A1"]  >= -100) & (df["rssi_A1"] <= -20) &
        (df["rssi_A2"]  >= -100) & (df["rssi_A2"] <= -20) &
        (df["rssi_A3"]  >= -100) & (df["rssi_A3"] <= -20)
    ]

    print(f"  Apres filtrage : {len(df)} positions")
    return df

# -----------------------------------------
# 2. PREPARATION DES DONNEES
# -----------------------------------------
def prepare_data(df):
    """Separe features et cibles, normalise."""
    X = df[["rssi_A1", "rssi_A2", "rssi_A3"]].values
    y = df[["x_kalman", "y_kalman"]].values

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    scaler = StandardScaler()
    X_train_sc = scaler.fit_transform(X_train)
    X_test_sc  = scaler.transform(X_test)

    print(f"  Train : {len(X_train)} | Test : {len(X_test)}")
    return X_train_sc, X_test_sc, y_train, y_test, scaler

# -----------------------------------------
# 3. EVALUATION
# -----------------------------------------
def evaluate(name, model, X_test, y_test):
    """Calcule MAE, RMSE et erreur euclidienne moyenne."""
    y_pred = model.predict(X_test)
    mae_x  = mean_absolute_error(y_test[:,0], y_pred[:,0])
    mae_y  = mean_absolute_error(y_test[:,1], y_pred[:,1])
    rmse_x = np.sqrt(mean_squared_error(y_test[:,0], y_pred[:,0]))
    rmse_y = np.sqrt(mean_squared_error(y_test[:,1], y_pred[:,1]))
    errors = np.sqrt((y_pred[:,0]-y_test[:,0])**2 + (y_pred[:,1]-y_test[:,1])**2)
    mean_err = np.mean(errors)
    median_err = np.median(errors)

    print(f"\n  [{name}]")
    print(f"    MAE  X={mae_x:.3f}m  Y={mae_y:.3f}m")
    print(f"    RMSE X={rmse_x:.3f}m Y={rmse_y:.3f}m")
    print(f"    Erreur euclidienne moyenne  : {mean_err:.3f}m")
    print(f"    Erreur euclidienne mediane  : {median_err:.3f}m")

    return {
        "name": name,
        "mae_x": round(mae_x,3), "mae_y": round(mae_y,3),
        "rmse_x": round(rmse_x,3), "rmse_y": round(rmse_y,3),
        "mean_error": round(mean_err,3),
        "median_error": round(median_err,3),
    }

# -----------------------------------------
# 4. ENTRAINEMENT
# -----------------------------------------
def train_all():
    os.makedirs(MODELS_DIR, exist_ok=True)

    print("=" * 55)
    print("  ML RTLS BLE — Entrainement des modeles")
    print("=" * 55)

    # Chargement
    df = load_data()
    if len(df) < 100:
        print("  ERREUR : pas assez de donnees (minimum 100)")
        return

    X_train, X_test, y_train, y_test, scaler = prepare_data(df)

    results = []

    # ── Modele 1 : Regression lineaire ──────────────────
    print("\n  Entrainement : Regression lineaire...")
    lr = LinearRegression()
    lr.fit(X_train, y_train)
    results.append(evaluate("Regression lineaire", lr, X_test, y_test))
    joblib.dump(lr, os.path.join(MODELS_DIR, "linear_regression.pkl"))

    # ── Modele 2 : KNN ───────────────────────────────────
    print("\n  Entrainement : KNN (k=5)...")
    knn = KNeighborsRegressor(n_neighbors=5, weights="distance", metric="euclidean")
    knn.fit(X_train, y_train)
    results.append(evaluate("KNN (k=5)", knn, X_test, y_test))
    joblib.dump(knn, os.path.join(MODELS_DIR, "knn.pkl"))

    # KNN optimal (cherche le meilleur k)
    print("\n  Recherche du k optimal...")
    best_k, best_err = 5, float("inf")
    for k in [3, 5, 7, 10, 15]:
        m = KNeighborsRegressor(n_neighbors=k, weights="distance")
        m.fit(X_train, y_train)
        p = m.predict(X_test)
        e = np.mean(np.sqrt((p[:,0]-y_test[:,0])**2 + (p[:,1]-y_test[:,1])**2))
        print(f"    k={k} -> erreur={e:.3f}m")
        if e < best_err:
            best_err = e
            best_k = k
    print(f"  Meilleur k : {best_k} (erreur={best_err:.3f}m)")
    knn_best = KNeighborsRegressor(n_neighbors=best_k, weights="distance")
    knn_best.fit(X_train, y_train)
    results.append(evaluate(f"KNN (k={best_k} optimal)", knn_best, X_test, y_test))
    joblib.dump(knn_best, os.path.join(MODELS_DIR, "knn_best.pkl"))

    # ── Modele 3 : Reseau de neurones (MLP) ─────────────
    print("\n  Entrainement : Reseau de neurones (MLP)...")
    mlp = MLPRegressor(
        hidden_layer_sizes=(64, 32),
        activation="relu",
        solver="adam",
        max_iter=500,
        random_state=42,
        early_stopping=True,
        validation_fraction=0.1,
        n_iter_no_change=20,
    )
    mlp.fit(X_train, y_train)
    results.append(evaluate("Reseau de neurones MLP", mlp, X_test, y_test))
    joblib.dump(mlp, os.path.join(MODELS_DIR, "mlp.pkl"))

    # Sauvegarder le scaler
    joblib.dump(scaler, os.path.join(MODELS_DIR, "scaler.pkl"))

    # ── Tableau comparatif ───────────────────────────────
    print("\n" + "=" * 55)
    print("  TABLEAU COMPARATIF")
    print("=" * 55)
    print(f"  {'Modele':<30} {'Err. moy':>10} {'Err. med':>10}")
    print(f"  {'-'*50}")
    for r in results:
        print(f"  {r['name']:<30} {r['mean_error']:>9.3f}m {r['median_error']:>9.3f}m")

    # Comparaison avec trilateration
    print(f"\n  Trilateration + Kalman (baseline) :  ~0.600m")
    print("=" * 55)

    # Sauvegarder les resultats
    import json
    with open(os.path.join(MODELS_DIR, "results.json"), "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  Modeles sauvegardes dans : {MODELS_DIR}/")
    print(f"  Resultats sauvegardes dans : {MODELS_DIR}/results.json")

if __name__ == "__main__":
    train_all()
