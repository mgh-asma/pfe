#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Base de donnees SQLite pour RTLS BLE
Tables :
  - positions : historique des positions
  - users     : comptes utilisateurs
  - tags      : tags BLE enregistres
  - alerts    : historique des alertes
"""
import sqlite3
import os
import hashlib
import secrets
from datetime import datetime

DB_PATH = "rtls.db"

# -----------------------------------------
# CREATION DES TABLES
# -----------------------------------------
def init_db(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Table positions
    c.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT    NOT NULL,
            tag_id       TEXT    DEFAULT 'TAG1',
            x_raw        REAL,
            y_raw        REAL,
            x_kalman     REAL,
            y_kalman     REAL,
            d_A1         REAL,
            d_A2         REAL,
            d_A3         REAL,
            rssi_A1      REAL,
            rssi_A2      REAL,
            rssi_A3      REAL,
            rejected_A1  INTEGER,
            rejected_A2  INTEGER,
            rejected_A3  INTEGER,
            weight_A1    REAL,
            weight_A2    REAL,
            weight_A3    REAL
        )
    """)

    # Table users
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            username   TEXT    NOT NULL UNIQUE,
            password   TEXT    NOT NULL,
            email      TEXT,
            role       TEXT    DEFAULT 'user',
            created_at TEXT    DEFAULT CURRENT_TIMESTAMP,
            last_login TEXT
        )
    """)

    # Table tags
    c.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_id       TEXT    NOT NULL UNIQUE,
            name         TEXT    NOT NULL,
            mac_address  TEXT,
            description  TEXT,
            active       INTEGER DEFAULT 1,
            last_seen    TEXT,
            created_at   TEXT    DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Table alerts
    c.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT    NOT NULL,
            tag_id       TEXT,
            alert_type   TEXT    NOT NULL,
            message      TEXT,
            acknowledged INTEGER DEFAULT 0
        )
    """)

    # Table sessions
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token      TEXT PRIMARY KEY,
            user_id    INTEGER NOT NULL,
            created_at TEXT    DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT    NOT NULL
        )
    """)

    conn.commit()
    conn.close()

    # Creer utilisateur admin par defaut si n'existe pas
    _create_default_admin(db_path)

    # Creer tag par defaut si n'existe pas
    _create_default_tag(db_path)

    print(f"  Base de donnees initialisee : {db_path}")

def _create_default_admin(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE username = 'admin'")
    if not c.fetchone():
        pwd = hash_password("admin123")
        c.execute("""
            INSERT INTO users (username, password, email, role)
            VALUES (?, ?, ?, ?)
        """, ("admin", pwd, "admin@rtls.local", "admin"))
        conn.commit()
        print("  Compte admin cree : admin / admin123")
    conn.close()

def _create_default_tag(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT id FROM tags WHERE tag_id = 'TAG1'")
    if not c.fetchone():
        c.execute("""
            INSERT INTO tags (tag_id, name, mac_address, description)
            VALUES (?, ?, ?, ?)
        """, ("TAG1", "Tag ESP32 #1", "AA:BB:CC:DD:EE:FF", "Tag de test principal"))
        conn.commit()
        print("  Tag par defaut cree : TAG1")
    conn.close()

# -----------------------------------------
# PASSWORDS
# -----------------------------------------
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, hashed):
    return hash_password(password) == hashed

# -----------------------------------------
# USERS
# -----------------------------------------
def create_user(username, password, email, role="user", db_path=DB_PATH):
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("""
            INSERT INTO users (username, password, email, role)
            VALUES (?, ?, ?, ?)
        """, (username, hash_password(password), email, role))
        conn.commit()
        conn.close()
        return True, "Utilisateur cree"
    except sqlite3.IntegrityError:
        return False, "Nom d'utilisateur deja pris"

def get_user(username, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def get_all_users(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT id, username, email, role, created_at, last_login FROM users")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def update_last_login(username, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("UPDATE users SET last_login = ? WHERE username = ?",
              (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), username))
    conn.commit()
    conn.close()

def delete_user(username, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE username = ? AND role != 'admin'", (username,))
    conn.commit()
    conn.close()

# -----------------------------------------
# SESSIONS
# -----------------------------------------
def create_session(user_id, db_path=DB_PATH):
    token = secrets.token_hex(32)
    expires = datetime.now().replace(hour=23, minute=59, second=59).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        INSERT INTO sessions (token, user_id, expires_at)
        VALUES (?, ?, ?)
    """, (token, user_id, expires))
    conn.commit()
    conn.close()
    return token

def verify_session(token, db_path=DB_PATH):
    if not token:
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT u.id, u.username, u.role, u.email
        FROM sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.token = ? AND s.expires_at > datetime('now')
    """, (token,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def delete_session(token, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()

# -----------------------------------------
# TAGS
# -----------------------------------------
def get_all_tags(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM tags ORDER BY tag_id")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_tag(tag_id, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM tags WHERE tag_id = ?", (tag_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def add_tag(tag_id, name, mac_address="", description="", db_path=DB_PATH):
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("""
            INSERT INTO tags (tag_id, name, mac_address, description)
            VALUES (?, ?, ?, ?)
        """, (tag_id, name, mac_address, description))
        conn.commit()
        conn.close()
        return True, "Tag ajoute"
    except sqlite3.IntegrityError:
        return False, "Tag ID deja existant"

def update_tag_last_seen(tag_id, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("UPDATE tags SET last_seen = ? WHERE tag_id = ?",
              (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tag_id))
    conn.commit()
    conn.close()

def delete_tag(tag_id, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("DELETE FROM tags WHERE tag_id = ?", (tag_id,))
    conn.commit()
    conn.close()

# -----------------------------------------
# POSITIONS
# -----------------------------------------
def insert_position(data, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        INSERT INTO positions (
            timestamp, tag_id,
            x_raw, y_raw, x_kalman, y_kalman,
            d_A1, d_A2, d_A3,
            rssi_A1, rssi_A2, rssi_A3,
            rejected_A1, rejected_A2, rejected_A3,
            weight_A1, weight_A2, weight_A3
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        data.get("timestamp"),
        data.get("tag_id", "TAG1"),
        data.get("x_raw"),    data.get("y_raw"),
        data.get("x_kalman"), data.get("y_kalman"),
        data.get("d_A1"),     data.get("d_A2"),     data.get("d_A3"),
        data.get("rssi_A1"),  data.get("rssi_A2"),  data.get("rssi_A3"),
        data.get("rejected_A1"), data.get("rejected_A2"), data.get("rejected_A3"),
        data.get("weight_A1"),   data.get("weight_A2"),   data.get("weight_A3"),
    ))
    conn.commit()
    conn.close()
    update_tag_last_seen(data.get("tag_id", "TAG1"), db_path)

def get_last_position(tag_id="TAG1", db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM positions WHERE tag_id=? ORDER BY id DESC LIMIT 1", (tag_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def get_history(tag_id="TAG1", limit=100, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM positions WHERE tag_id=? ORDER BY id DESC LIMIT ?", (tag_id, limit))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]

def get_stats(tag_id="TAG1", db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        SELECT
            COUNT(*)                    as total,
            ROUND(AVG(x_kalman), 3)    as x_moyen,
            ROUND(AVG(y_kalman), 3)    as y_moyen,
            ROUND(MIN(x_kalman), 3)    as x_min,
            ROUND(MAX(x_kalman), 3)    as x_max,
            ROUND(MIN(y_kalman), 3)    as y_min,
            ROUND(MAX(y_kalman), 3)    as y_max,
            MIN(timestamp)             as debut,
            MAX(timestamp)             as fin
        FROM positions WHERE tag_id = ?
    """, (tag_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "total": row[0],
            "x_moyen": row[1], "y_moyen": row[2],
            "x_min": row[3],   "x_max": row[4],
            "y_min": row[5],   "y_max": row[6],
            "debut": row[7],   "fin": row[8],
        }
    return {}

def get_history_by_time(tag_id, start, end, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT * FROM positions
        WHERE tag_id=? AND timestamp BETWEEN ? AND ?
        ORDER BY id ASC
    """, (tag_id, start, end))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# -----------------------------------------
# ALERTS
# -----------------------------------------
def insert_alert(tag_id, alert_type, message, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        INSERT INTO alerts (timestamp, tag_id, alert_type, message)
        VALUES (?, ?, ?, ?)
    """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), tag_id, alert_type, message))
    conn.commit()
    conn.close()

def get_alerts(limit=50, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def acknowledge_alert(alert_id, db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("UPDATE alerts SET acknowledged=1 WHERE id=?", (alert_id,))
    conn.commit()
    conn.close()

# -----------------------------------------
# IMPORT CSV
# -----------------------------------------
def import_from_csv(csv_path="positions.csv", tag_id="TAG1", db_path=DB_PATH):
    import csv
    if not os.path.exists(csv_path):
        print(f"  Fichier {csv_path} introuvable")
        return 0
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    count = 0
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    c.execute("""
                        INSERT INTO positions (
                            timestamp, tag_id,
                            x_raw, y_raw, x_kalman, y_kalman,
                            d_A1, d_A2, d_A3,
                            rssi_A1, rssi_A2, rssi_A3,
                            rejected_A1, rejected_A2, rejected_A3,
                            weight_A1, weight_A2, weight_A3
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        row.get("timestamp"), tag_id,
                        float(row.get("x_raw",    0)),
                        float(row.get("y_raw",    0)),
                        float(row.get("x_kalman", 0)),
                        float(row.get("y_kalman", 0)),
                        float(row.get("d_A1", 0)),
                        float(row.get("d_A2", 0)),
                        float(row.get("d_A3", 0)),
                        float(row.get("rssi_A1", 0)),
                        float(row.get("rssi_A2", 0)),
                        float(row.get("rssi_A3", 0)),
                        int(float(row.get("rejected_A1", 0))),
                        int(float(row.get("rejected_A2", 0))),
                        int(float(row.get("rejected_A3", 0))),
                        float(row.get("weight_A1", 1)),
                        float(row.get("weight_A2", 1)),
                        float(row.get("weight_A3", 1)),
                    ))
                    count += 1
                except Exception:
                    continue
        conn.commit()
    finally:
        conn.close()
    print(f"  {count} positions importees depuis {csv_path}")
    return count

# -----------------------------------------
# MAIN
# -----------------------------------------
if __name__ == "__main__":
    print("Initialisation base de donnees...")
    init_db()
    print("Import CSV existant...")
    import_from_csv()
    stats = get_stats()
    print(f"\nStatistiques TAG1 :")
    print(f"  Total    : {stats.get('total', 0)} positions")
    print(f"  X moyen  : {stats.get('x_moyen')} m")
    print(f"  Y moyen  : {stats.get('y_moyen')} m")
    print(f"  Debut    : {stats.get('debut')}")
    print(f"  Fin      : {stats.get('fin')}")
    print(f"\nUtilisateurs :")
    for u in get_all_users():
        print(f"  {u['username']} ({u['role']}) — {u['email']}")
    print(f"\nTags :")
    for t in get_all_tags():
        print(f"  {t['tag_id']} — {t['name']} — {t['mac_address']}")
