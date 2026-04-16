#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API Flask RTLS BLE - Version complete
Routes :
  Auth    : POST /login, POST /logout
  Tags    : GET/POST /tags, DELETE /tags/<id>
  Pos     : GET /position/<tag_id>, POST /update
  History : GET /positions/history/<tag_id>
  Stats   : GET /positions/stats/<tag_id>
  Alerts  : GET /alerts, POST /alerts/acknowledge/<id>
  Users   : GET/POST /users, DELETE /users/<username>
  Web     : GET /dashboard, GET /login, GET /tags
"""
from flask import Flask, jsonify, request, send_from_directory, make_response
from datetime import datetime
import threading
import smtplib
from email.mime.text import MIMEText
from database import (
    init_db,
    insert_position, get_last_position, get_history, get_stats, get_history_by_time,
    get_user, get_all_users, create_user, delete_user, update_last_login,
    verify_password,
    create_session, verify_session, delete_session,
    get_all_tags, get_tag, add_tag, delete_tag, update_tag_last_seen,
    insert_alert, get_alerts, acknowledge_alert,
)

app = Flask(__name__)

@app.after_request
def add_headers(response):
    response.headers["Content-Security-Policy"] = ""
    return response

WEB_DIR    = "/home/asma/calibration_ble/web"
ALERT_SECS = 30   # secondes sans donnees = alerte tag inactif

# Email config (a modifier)
EMAIL_CONFIG = {
    "enabled":  False,         # mettre True pour activer
    "smtp":     "smtp.gmail.com",
    "port":     587,
    "user":     "ton.email@gmail.com",
    "password": "ton_mot_de_passe",
    "from":     "RTLS BLE <ton.email@gmail.com>",
}

lock = threading.Lock()

# Positions courantes par tag (memoire)
current_positions = {}
ml_positions = {}
ml_positions = {}

# Timestamps derniere reception par tag
last_received = {}

# -----------------------------------------
# UTILITAIRES
# -----------------------------------------
def get_token(req):
    """Recupere le token depuis cookie ou header."""
    return req.cookies.get("rtls_token") or req.headers.get("X-Token")

def require_auth(req):
    """Verifie le token — retourne user ou None."""
    token = get_token(req)
    return verify_session(token)

def send_email(to, subject, body):
    """Envoie un email d'alerte."""
    if not EMAIL_CONFIG["enabled"]:
        return
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"]    = EMAIL_CONFIG["from"]
        msg["To"]      = to
        with smtplib.SMTP(EMAIL_CONFIG["smtp"], EMAIL_CONFIG["port"]) as s:
            s.starttls()
            s.login(EMAIL_CONFIG["user"], EMAIL_CONFIG["password"])
            s.send_message(msg)
    except Exception as e:
        print(f"  Email erreur : {e}")

def check_tag_inactive():
    """Verifie si un tag est inactif et cree une alerte."""
    import time
    while True:
        time.sleep(10)
        now = datetime.now()
        for tag_id, last_ts in list(last_received.items()):
            diff = (now - last_ts).total_seconds()
            if diff > ALERT_SECS:
                msg = f"Tag {tag_id} inactif depuis {int(diff)}s"
                insert_alert(tag_id, "INACTIF", msg)
                # Envoyer email aux admins
                for u in get_all_users():
                    if u["role"] == "admin" and u["email"]:
                        send_email(u["email"], f"[RTLS] Alerte : {msg}", msg)
                # Supprimer pour ne pas re-alerter immediatement
                del last_received[tag_id]

# Lancer le thread de surveillance
import threading
t = threading.Thread(target=check_tag_inactive, daemon=True)
t.start()

# -----------------------------------------
# PAGES WEB
# -----------------------------------------
@app.route("/")
@app.route("/login")
def page_login():
    return send_from_directory(WEB_DIR, "login.html")

@app.route("/tags-page")
def page_tags():
    user = require_auth(request)
    if not user:
        return send_from_directory(WEB_DIR, "login.html")
    return send_from_directory(WEB_DIR, "tags.html")

@app.route("/dashboard")
def page_dashboard():
    user = require_auth(request)
    if not user:
        return send_from_directory(WEB_DIR, "login.html")
    return send_from_directory(WEB_DIR, "dashboard.html")

# -----------------------------------------
# AUTH
# -----------------------------------------
@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "Donnees manquantes"}), 400

    username = data.get("username", "").strip()
    password = data.get("password", "")

    user = get_user(username)
    if not user or not verify_password(password, user["password"]):
        return jsonify({"status": "error", "message": "Identifiants incorrects"}), 401

    token = create_session(user["id"])
    update_last_login(username)

    resp = make_response(jsonify({
        "status":   "ok",
        "token":    token,
        "username": user["username"],
        "role":     user["role"],
    }))
    resp.set_cookie("rtls_token", token, httponly=True, samesite="Lax")
    return resp

@app.route("/api/logout", methods=["POST"])
def api_logout():
    token = get_token(request)
    if token:
        delete_session(token)
    resp = make_response(jsonify({"status": "ok"}))
    resp.delete_cookie("rtls_token")
    return resp

@app.route("/api/me", methods=["GET"])
def api_me():
    user = require_auth(request)
    if not user:
        return jsonify({"status": "error", "message": "Non connecte"}), 401
    return jsonify({"status": "ok", "user": user})

# -----------------------------------------
# USERS (admin seulement)
# -----------------------------------------
@app.route("/api/users", methods=["GET"])
def api_get_users():
    user = require_auth(request)
    if not user or user["role"] != "admin":
        return jsonify({"status": "error", "message": "Acces refuse"}), 403
    return jsonify({"status": "ok", "users": get_all_users()})

@app.route("/api/users", methods=["POST"])
def api_create_user():
    user = require_auth(request)
    if not user or user["role"] != "admin":
        return jsonify({"status": "error", "message": "Acces refuse"}), 403
    data = request.get_json()
    ok, msg = create_user(
        data.get("username"),
        data.get("password"),
        data.get("email", ""),
        data.get("role", "user"),
    )
    return jsonify({"status": "ok" if ok else "error", "message": msg})

@app.route("/api/users/<username>", methods=["DELETE"])
def api_delete_user(username):
    user = require_auth(request)
    if not user or user["role"] != "admin":
        return jsonify({"status": "error", "message": "Acces refuse"}), 403
    if username == "admin":
        return jsonify({"status": "error", "message": "Impossible de supprimer admin"}), 400
    delete_user(username)
    return jsonify({"status": "ok"})

# -----------------------------------------
# TAGS
# -----------------------------------------
@app.route("/api/tags", methods=["GET"])
def api_get_tags():
    user = require_auth(request)
    if not user:
        return jsonify({"status": "error", "message": "Non connecte"}), 401
    tags = get_all_tags()
    # Ajouter derniere position a chaque tag
    for tag in tags:
        pos = get_last_position(tag["tag_id"])
        tag["last_position"] = {
            "x": pos["x_kalman"] if pos else None,
            "y": pos["y_kalman"] if pos else None,
        } if pos else None
        # Statut actif/inactif
        if tag["last_seen"]:
            try:
                last = datetime.strptime(tag["last_seen"], "%Y-%m-%d %H:%M:%S")
                diff = (datetime.now() - last).total_seconds()
                tag["online"] = diff < ALERT_SECS
            except Exception:
                tag["online"] = False
        else:
            tag["online"] = False
    return jsonify({"status": "ok", "tags": tags})

@app.route("/api/tags", methods=["POST"])
def api_add_tag():
    user = require_auth(request)
    if not user or user["role"] != "admin":
        return jsonify({"status": "error", "message": "Acces refuse"}), 403
    data = request.get_json()
    ok, msg = add_tag(
        data.get("tag_id"),
        data.get("name"),
        data.get("mac_address", ""),
        data.get("description", ""),
    )
    return jsonify({"status": "ok" if ok else "error", "message": msg})

@app.route("/api/tags/<tag_id>", methods=["DELETE"])
def api_delete_tag(tag_id):
    user = require_auth(request)
    if not user or user["role"] != "admin":
        return jsonify({"status": "error", "message": "Acces refuse"}), 403
    delete_tag(tag_id)
    return jsonify({"status": "ok"})

# -----------------------------------------
# POSITIONS
# -----------------------------------------
@app.route("/api/position/<tag_id>", methods=["GET"])
def api_get_position(tag_id):
    user = require_auth(request)
    if not user:
        return jsonify({"status": "error", "message": "Non connecte"}), 401
    with lock:
        pos = current_positions.get(tag_id) or get_last_position(tag_id)
    if not pos:
        return jsonify({"status": "no_data", "message": "Aucune position"}), 404
    # Supporte les deux formats : "x"/"y" (ancien) et "x_kalman"/"y_kalman" (nouveau)
    px = pos.get("x_kalman") or pos.get("x")
    py = pos.get("y_kalman") or pos.get("y")
    return jsonify({
        "status":    "ok",
        "position":  {"x": px, "y": py},
        "distances": {"A1": pos.get("d_A1"), "A2": pos.get("d_A2"), "A3": pos.get("d_A3")},
        "rssi":      {"A1": pos.get("rssi_A1"), "A2": pos.get("rssi_A2"), "A3": pos.get("rssi_A3")},
        "timestamp": pos.get("timestamp"),
    })

# Route compatibilite ancienne version
@app.route("/position", methods=["GET"])
def api_position_legacy():
    user = require_auth(request)
    if not user:
        return jsonify({"status": "error", "message": "Non connecte"}), 401
    with lock:
        pos = current_positions.get("TAG1") or get_last_position("TAG1")
    if not pos:
        return jsonify({"status": "no_data"}), 404
    return jsonify({
        "status":    "ok",
        "position":  {"x": pos.get("x_kalman"), "y": pos.get("y_kalman")},
        "distances": {"A1": pos.get("d_A1"), "A2": pos.get("d_A2"), "A3": pos.get("d_A3")},
        "rssi":      {"A1": pos.get("rssi_A1"), "A2": pos.get("rssi_A2"), "A3": pos.get("rssi_A3")},
        "timestamp": pos.get("timestamp"),
    })

@app.route("/api/positions/history/<tag_id>", methods=["GET"])
def api_get_history(tag_id):
    user = require_auth(request)
    if not user:
        return jsonify({"status": "error", "message": "Non connecte"}), 401
    limit = int(request.args.get("limit", 100))
    start = request.args.get("start")
    end   = request.args.get("end")
    if start and end:
        rows = get_history_by_time(tag_id, start, end)
    else:
        rows = get_history(tag_id, limit)
    return jsonify({"status": "ok", "count": len(rows), "history": rows})

@app.route("/api/positions/stats/<tag_id>", methods=["GET"])
def api_get_stats(tag_id):
    user = require_auth(request)
    if not user:
        return jsonify({"status": "error", "message": "Non connecte"}), 401
    return jsonify({"status": "ok", "stats": get_stats(tag_id)})

@app.route("/update", methods=["POST"])
def api_update():
    """Reçoit position depuis trilateration_kalman_final.py"""
    data = request.get_json()
    if not data:
        return jsonify({"status": "error"}), 400
    tag_id = data.get("tag_id", "TAG1")
    with lock:
        current_positions[tag_id] = data.copy()
        last_received[tag_id] = datetime.now()
    insert_position(data)
    return jsonify({"status": "ok"}), 200

@app.route("/update/ml", methods=["POST"])
def api_update_ml():
    """Reçoit position depuis ml_predict.py"""
    data = request.get_json()
    if not data:
        return jsonify({"status": "error"}), 400
    tag_id = data.get("tag_id", "TAG1")
    with lock:
        ml_positions[tag_id] = data.copy()
    return jsonify({"status": "ok"}), 200

@app.route("/api/position/ml/<tag_id>", methods=["GET"])
def api_get_ml_position(tag_id):
    """Retourne la position ML actuelle"""
    user = require_auth(request)
    if not user:
        return jsonify({"status": "error", "message": "Non connecte"}), 401
    with lock:
        pos = ml_positions.get(tag_id)
    if not pos:
        return jsonify({"status": "no_data"}), 404
    return jsonify({
        "status": "ok",
        "position":  {"x": pos.get("x"), "y": pos.get("y")},
        "rssi":      {"A1": pos.get("rssi_A1"), "A2": pos.get("rssi_A2"), "A3": pos.get("rssi_A3")},
        "timestamp": pos.get("timestamp"),
        "method":    pos.get("method", "ml_knn"),
    })

# -----------------------------------------
# ALERTS
# -----------------------------------------
@app.route("/api/alerts", methods=["GET"])
def api_get_alerts():
    user = require_auth(request)
    if not user:
        return jsonify({"status": "error", "message": "Non connecte"}), 401
    limit = int(request.args.get("limit", 50))
    return jsonify({"status": "ok", "alerts": get_alerts(limit)})

@app.route("/api/alerts/acknowledge/<int:alert_id>", methods=["POST"])
def api_ack_alert(alert_id):
    user = require_auth(request)
    if not user:
        return jsonify({"status": "error", "message": "Non connecte"}), 401
    acknowledge_alert(alert_id)
    return jsonify({"status": "ok"})

# -----------------------------------------
# STATUS
# -----------------------------------------
@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({
        "status":        "ok",
        "tags_actifs":   len(current_positions),
        "ancres": {
            "A1": {"pos": [0.0, 0.0]},
            "A2": {"pos": [6.0, 0.0]},
            "A3": {"pos": [4.0833, 5.6856]},
        }
    })

# -----------------------------------------
# LANCEMENT
# -----------------------------------------
if __name__ == "__main__":
    print("=" * 55)
    print("  API Flask RTLS BLE — Version complete")
    print("=" * 55)
    print("  Initialisation base de donnees...")
    init_db()
    print(f"  Surveillance tags inactifs : >{ALERT_SECS}s = alerte")
    print(f"  Serveur sur http://0.0.0.0:5000")
    print(f"  Dashboard : http://0.0.0.0:5000/dashboard")
    print("=" * 55)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
