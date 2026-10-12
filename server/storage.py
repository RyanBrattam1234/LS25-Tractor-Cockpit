#!/usr/bin/env python3
"""SQLite-Speicher fuer Telemetrie-Verlauf und Befehlsprotokoll.

Nur Standardbibliothek (``sqlite3``). Der Speicher wird im Hintergrund
beschrieben, nie im HTTP-Pfad, damit das Dashboard schnell bleibt.

Aufbau:

* ``readings``  - eine Zeile pro Messung (nur bei Aenderung oder als Heartbeat)
* ``commands``  - Protokoll aller Steuerbefehle mit Ergebnis und Bestaetigung
* ``meta``      - Kleinigkeiten wie das Schema-Laufzeitkennzeichen

Die Datenbank waechst kontrolliert: alte Messungen werden nach
``retention_days`` geloescht und die Zeilenzahl bleibt unter ``max_rows``.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL    NOT NULL,
    channel       TEXT,
    connected     INTEGER,
    state         TEXT,
    vehicle       TEXT,
    speed         REAL,
    rpm           REAL,
    load          REAL,
    fuel          REAL,
    fuel_capacity REAL,
    fuel_type     TEXT,
    engine        INTEGER,
    hours         REAL,
    x             REAL,
    y             REAL,
    z             REAL,
    impl_count    INTEGER,
    impl_json     TEXT
);
CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts);

CREATE TABLE IF NOT EXISTS commands (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id INTEGER,
    ts         REAL,
    action     TEXT,
    target     INTEGER,
    name       TEXT,
    status     TEXT,
    message    TEXT,
    confirmed  INTEGER,
    client     TEXT
);
CREATE INDEX IF NOT EXISTS idx_commands_ts ON commands(ts);
"""

INSERT_READING = (
    "INSERT INTO readings (ts, channel, connected, state, vehicle, speed, rpm, load,"
    " fuel, fuel_capacity, fuel_type, engine, hours, x, y, z, impl_count, impl_json)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)

INSERT_COMMAND = (
    "INSERT INTO commands (command_id, ts, action, target, name, status, message,"
    " confirmed, client) VALUES (?,?,?,?,?,?,?,?,?)"
)


def _to_int(value):
    if value is None:
        return None
    return 1 if value else 0


def _round(value, digits=1):
    if not isinstance(value, (int, float)):
        return None
    return round(float(value), digits)


class HistoryStore:
    """Schreibt Messungen und Befehle in eine SQLite-Datei."""

    def __init__(
        self,
        path,
        enabled: bool = True,
        retention_days: float = 7.0,
        max_rows: int = 500_000,
        min_interval_ms: int = 500,
        max_interval_ms: int = 5000,
    ) -> None:
        self.path = Path(path) if path else None
        self.enabled = bool(enabled) and self.path is not None
        self.retention_seconds = max(0.0, float(retention_days) * 86400.0)
        self.max_rows = max(1000, int(max_rows))
        self.min_interval = max(0.0, float(min_interval_ms) / 1000.0)
        self.max_interval = max(self.min_interval, float(max_interval_ms) / 1000.0)

        self.available = False
        self.error: str | None = None
        self.skipped = 0
        self.rows = 0

        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None
        self._last_key = None
        self._last_store = 0.0
        self._last_prune = 0.0

        self._open()

    # -- Aufbau ------------------------------------------------------------
    def _open(self) -> None:
        if not self.enabled:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                str(self.path), timeout=5.0, check_same_thread=False
            )
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA temp_store=MEMORY")
            connection.executescript(SCHEMA)
            connection.commit()
            self.rows = connection.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
            self._connection = connection
            self.available = True
        except (sqlite3.Error, OSError) as exc:
            self.error = str(exc)
            self.enabled = False
            print(f"[Datenbank] {self.path} nicht nutzbar: {exc}")

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                try:
                    self._connection.commit()
                    self._connection.close()
                except sqlite3.Error:
                    pass
                self._connection = None
        self.available = False

    # -- Messungen ---------------------------------------------------------
    @staticmethod
    def _key(reading: dict):
        """Vergleichsschluessel: nur diese Werte entscheiden ueber ein Insert."""
        implements = reading.get("implements") or []
        signature = tuple(
            (
                item.get("name"),
                item.get("lowered"),
                item.get("unfolded"),
                item.get("on"),
                item.get("working"),
                _round(item.get("fill"), 1),
            )
            for item in implements
        )
        return (
            reading.get("state"),
            reading.get("vehicle"),
            _round(reading.get("speed"), 1),
            _round(reading.get("rpm"), 0),
            _round(reading.get("load"), 1),
            _round(reading.get("fuel"), 1),
            _round(reading.get("hours"), 2),
            reading.get("engine"),
            signature,
        )

    def record(self, reading) -> bool:
        """Speichert eine Messung, wenn sie sich geaendert hat (oder Heartbeat)."""
        if not self.available or not reading or self._connection is None:
            return False

        now = time.time()
        key = self._key(reading)
        timestamp = reading.get("updatedAt") or now

        with self._lock:
            changed = key != self._last_key
            if not changed and now - self._last_store < self.max_interval:
                self.skipped += 1
                return False
            if self._last_store and now - self._last_store < self.min_interval:
                self.skipped += 1
                return False

            implements = reading.get("implements") or []
            try:
                self._connection.execute(
                    INSERT_READING,
                    (
                        timestamp,
                        reading.get("channel"),
                        _to_int(reading.get("connected")),
                        reading.get("state"),
                        reading.get("vehicle"),
                        reading.get("speed"),
                        reading.get("rpm"),
                        reading.get("load"),
                        reading.get("fuel"),
                        reading.get("fuelCapacity"),
                        reading.get("fuelType"),
                        _to_int(reading.get("engine")),
                        reading.get("hours"),
                        reading.get("x"),
                        reading.get("y"),
                        reading.get("z"),
                        len(implements),
                        json.dumps(implements, separators=(",", ":"), default=str)
                        if implements
                        else None,
                    ),
                )
                self._connection.commit()
            except sqlite3.Error as exc:
                self.error = str(exc)
                return False

            self._last_key = key
            self._last_store = now
            self.rows += 1

            if now - self._last_prune > 60.0:
                self._last_prune = now
                self._prune_locked(now)
            return True

    def _prune_locked(self, now: float) -> None:
        if self._connection is None:
            return
        try:
            if self.retention_seconds > 0:
                self._connection.execute(
                    "DELETE FROM readings WHERE ts < ?", (now - self.retention_seconds,)
                )
            self._connection.execute(
                "DELETE FROM readings WHERE id <= (SELECT MAX(id) FROM readings) - ?",
                (self.max_rows,),
            )
            self._connection.commit()
            # Nach dem Aufraeumen neu zaehlen (die Zahl wird im Dashboard gezeigt).
            self.rows = self._connection.execute("SELECT COUNT(*) FROM readings").fetchone()[0]
        except sqlite3.Error as exc:
            self.error = str(exc)

    # -- Befehlsprotokoll --------------------------------------------------
    def record_command(self, command: dict) -> None:
        if not self.available or not command or self._connection is None:
            return
        with self._lock:
            try:
                self._connection.execute(
                    INSERT_COMMAND,
                    (
                        command.get("id"),
                        command.get("at") or time.time(),
                        command.get("action"),
                        command.get("target"),
                        command.get("name"),
                        command.get("status"),
                        command.get("message"),
                        _to_int(command.get("confirmed")),
                        command.get("client"),
                    ),
                )
                self._connection.commit()
            except sqlite3.Error as exc:
                self.error = str(exc)

    def set_command_confirmation(self, command_id, confirmed) -> None:
        if not self.available or self._connection is None or confirmed is None:
            return
        with self._lock:
            try:
                self._connection.execute(
                    "UPDATE commands SET confirmed = ? WHERE id ="
                    " (SELECT MAX(id) FROM commands WHERE command_id = ?)",
                    (_to_int(confirmed), command_id),
                )
                self._connection.commit()
            except sqlite3.Error as exc:
                self.error = str(exc)

    def recent_commands(self, limit: int = 20) -> list[dict]:
        if not self.available or self._connection is None:
            return []
        with self._lock:
            try:
                rows = self._connection.execute(
                    "SELECT command_id, ts, action, target, name, status, message,"
                    " confirmed, client FROM commands ORDER BY id DESC LIMIT ?",
                    (max(1, int(limit)),),
                ).fetchall()
            except sqlite3.Error as exc:
                self.error = str(exc)
                return []
        commands = [
            {
                "id": row[0],
                "ts": row[1],
                "action": row[2],
                "target": row[3],
                "name": row[4],
                "status": row[5],
                "message": row[6],
                "confirmed": None if row[7] is None else bool(row[7]),
                "client": row[8],
            }
            for row in rows
        ]
        commands.reverse()
        return commands

    # -- Verlauf -----------------------------------------------------------
    def series(self, minutes: float = 30.0, buckets: int = 120) -> dict:
        """Verdichteter Verlauf fuer das Diagramm (AVG/MAX pro Zeitfenster)."""
        if not self.available or self._connection is None:
            return {"available": False, "points": [], "error": self.error}

        now = time.time()
        minutes = max(0.5, min(float(minutes), 60.0 * 24 * 30))
        buckets = max(10, min(int(buckets), 600))
        start = now - minutes * 60.0
        width = max(1.0, (now - start) / buckets)

        with self._lock:
            try:
                rows = self._connection.execute(
                    "SELECT CAST((ts - ?) / ? AS INTEGER) AS bucket,"
                    " MIN(ts), AVG(speed), MAX(speed), AVG(rpm), MAX(rpm),"
                    " AVG(fuel), MIN(fuel), MAX(hours), MIN(hours), AVG(load), COUNT(*)"
                    " FROM readings WHERE ts >= ? AND connected = 1"
                    " GROUP BY bucket ORDER BY bucket",
                    (start, width, start),
                ).fetchall()
                totals = self._connection.execute(
                    "SELECT COUNT(*), MIN(ts), MAX(ts) FROM readings WHERE ts >= ?",
                    (start,),
                ).fetchone()
            except sqlite3.Error as exc:
                self.error = str(exc)
                return {"available": False, "points": [], "error": self.error}

        points = [
            {
                "t": row[1],
                "speed": row[2],
                "speedMax": row[3],
                "rpm": row[4],
                "rpmMax": row[5],
                "fuel": row[6],
                "fuelMin": row[7],
                "hours": row[8],
                "hoursMin": row[9],
                "load": row[10],
                "samples": row[11],
            }
            for row in rows
        ]

        summary = {"samples": totals[0] or 0, "firstTs": totals[1], "lastTs": totals[2]}
        speeds = [point["speedMax"] for point in points if point["speedMax"] is not None]
        rpms = [point["rpmMax"] for point in points if point["rpmMax"] is not None]
        hours = [point["hours"] for point in points if point["hours"] is not None]
        summary["speedMax"] = max(speeds) if speeds else None
        summary["rpmMax"] = max(rpms) if rpms else None
        summary["hoursDriven"] = (max(hours) - min(hours)) if len(hours) >= 2 else None

        return {
            "available": True,
            "minutes": minutes,
            "bucketSeconds": width,
            "start": start,
            "end": now,
            "points": points,
            "summary": summary,
        }

    # -- Info --------------------------------------------------------------
    def stats(self) -> dict:
        size = None
        try:
            if self.path is not None and self.path.exists():
                size = self.path.stat().st_size
        except OSError:
            size = None
        return {
            "enabled": self.enabled,
            "available": self.available,
            "path": str(self.path) if self.path else None,
            "sizeBytes": size,
            "rows": self.rows,
            "skippedSamples": self.skipped,
            "retentionDays": round(self.retention_seconds / 86400.0, 2),
            "maxRows": self.max_rows,
            "error": self.error,
        }
