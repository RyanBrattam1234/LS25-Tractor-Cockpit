#!/usr/bin/env python3
"""Lokaler, nur lesender Telemetrie-Server fuer das LS25 Tractor Cockpit.

Liest die Telemetrie des Mods aus zwei Kanaelen und stellt sie dem
Browser-Dashboard unter ``http://<PC-IP>:8765`` bereit:

1. **Datei-Kanal** (schnell): ``<Spielprofil>/modSettings/FS25_TractorCockpit.txt``
   wird vom Mod mehrmals pro Sekunde neu geschrieben.
2. **Log-Kanal** (Rueckfall): ``TRACTORCOCKPIT|``-Zeilen in der LS25-``log.txt``.

Es wird immer der Kanal mit der frischesten Zeile verwendet.

Wichtig:
* Nur Standardbibliothek, keine Installation noetig.
* Es gibt keine Route zum Steuern von Fahrzeugen - reines Auslesen.
* Der Server ist fuer das private Heimnetz gedacht, nicht fuers Internet.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import socket
import sys
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from xml.sax.saxutils import escape

import storage

ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "www"
DEFAULT_CONFIG_PATH = ROOT.parent / "config" / "settings.json"

GAME_LOG_RELATIVE = Path("My Games") / "FarmingSimulator2025" / "log.txt"
# Vom Mod geschriebene Datei (siehe TractorCockpit.FILE_NAME in der Lua-Datei).
TELEMETRY_FILE_RELATIVE = Path("modSettings") / "FS25_TractorCockpit.txt"
# Befehlsdatei fuer den Rueckkanal (Phase 4). XML, weil FS25 Mods nur das
# Schreiben per io.open erlaubt - gelesen wird ueber die XML-API des Spiels.
COMMAND_FILE_RELATIVE = Path("modSettings") / "FS25_TractorCockpit_cmd.xml"

# Erlaubte Aktionen. Alles andere wird abgelehnt, bevor etwas geschrieben wird.
#   scope:      "implement" (target 1..8) oder "vehicle" (target 0)
#   capability: Feld aus der Telemetrie, das die Unterstuetzung anzeigt
#   expect:     erwarteter Zustand danach (fuer die Bestaetigung aus dem Spiel)
CONTROL_ACTIONS = {
    # --- Anbaugeraete ----------------------------------------------------
    "lower": {"scope": "implement", "capability": "canLower", "expect": ("lowered", True)},
    "raise": {"scope": "implement", "capability": "canLower", "expect": ("lowered", False)},
    "toggleFold": {"scope": "implement", "capability": "canFold", "expect": None},
    "turnOn": {"scope": "implement", "capability": "canOn", "expect": ("on", True)},
    "turnOff": {"scope": "implement", "capability": "canOn", "expect": ("on", False)},
    # --- Licht ------------------------------------------------------------
    "lightDefaultOn": {"scope": "vehicle", "capability": "light:default",
                       "expect": ("light:default", True)},
    "lightDefaultOff": {"scope": "vehicle", "capability": "light:default",
                        "expect": ("light:default", False)},
    "lightHighBeamOn": {"scope": "vehicle", "capability": "light:highBeam",
                        "expect": ("light:highBeam", True)},
    "lightHighBeamOff": {"scope": "vehicle", "capability": "light:highBeam",
                         "expect": ("light:highBeam", False)},
    "lightWorkFrontOn": {"scope": "vehicle", "capability": "light:workFront",
                         "expect": ("light:workFront", True)},
    "lightWorkFrontOff": {"scope": "vehicle", "capability": "light:workFront",
                          "expect": ("light:workFront", False)},
    "lightWorkBackOn": {"scope": "vehicle", "capability": "light:workBack",
                        "expect": ("light:workBack", True)},
    "lightWorkBackOff": {"scope": "vehicle", "capability": "light:workBack",
                         "expect": ("light:workBack", False)},
    "lightNext": {"scope": "vehicle", "capability": "toggle", "expect": None},
    "lightPrev": {"scope": "vehicle", "capability": "toggle", "expect": None},
    # --- Rundumleuchte und Blinker ---------------------------------------
    "beaconOn": {"scope": "vehicle", "capability": "beacon", "expect": ("beacon", True)},
    "beaconOff": {"scope": "vehicle", "capability": "beacon", "expect": ("beacon", False)},
    "turnOff": {"scope": "vehicle", "capability": "turn", "expect": ("turnLight", "off")},
    "turnLeft": {"scope": "vehicle", "capability": "turn", "expect": ("turnLight", "left")},
    "turnRight": {"scope": "vehicle", "capability": "turn", "expect": ("turnLight", "right")},
    "turnHazard": {"scope": "vehicle", "capability": "turn", "expect": ("turnLight", "hazard")},
}
MAX_REQUEST_BYTES = 4096
# Standardpfad der SQLite-Datenbank (relativ zum Projektordner).
DEFAULT_DATABASE = ROOT / "data" / "history.sqlite3"
# Verlauf kurz zwischenspeichern: mehrere Geraete im WLAN kosten dann nur eine
# Datenbankabfrage.
HISTORY_CACHE_SECONDS = 2.0
# Ab dieser Groesse werden Antworten gepackt (Tablet im WLAN).
GZIP_MIN_BYTES = 1024

MARKER = "TRACTORCOCKPIT|"
TIMESTAMP_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})\.(\d{3})"
)
PARTIAL_LINE_LIMIT = 64 * 1024
MAX_READ_BYTES = 1024 * 1024
MAX_TELEMETRY_FILE_BYTES = 64 * 1024

DOCUMENT_DIR_NAMES = (
    "Documents",
    "Dokumente",
    "Mes documents",
    "Documentos",
    "Documenti",
    "Mijn documenten",
)

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
}


# ---------------------------------------------------------------------------
# Konfiguration und Pfadsuche
# ---------------------------------------------------------------------------

def load_config(path: Path) -> dict:
    """Liest config/settings.json. Fehler werden gemeldet, nicht verschluckt."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        print(f"[Warnung] Konfiguration nicht lesbar ({path}): {exc}")
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[Warnung] Konfiguration ist kein gueltiges JSON ({path}): {exc}")
        return {}

    if not isinstance(data, dict):
        print(f"[Warnung] Konfiguration muss ein JSON-Objekt sein ({path}).")
        return {}
    return data


def expand_path(value: str) -> Path:
    """Loest %USERPROFILE%, $HOME und ~ auf und entfernt Anfuehrungszeichen."""
    text = str(value).strip().strip('"').strip("'")
    text = os.path.expandvars(text)
    try:
        return Path(text).expanduser()
    except RuntimeError:
        return Path(text)


def registry_documents_dirs() -> list[Path]:
    """Dokumente-Ordner aus der Windows-Registrierung (auch bei Umleitung)."""
    if os.name != "nt":
        return []

    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return []

    results: list[Path] = []
    keys = (
        (r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders", True),
        # Achtung: "Shell Folders" ist oft veraltet oder zeigt auf einen
        # Unterordner - deshalb nur als letzte Rueckfallebene.
        (r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders", False),
    )
    for key_path, needs_expand in keys:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "Personal")
        except OSError:
            continue
        if not value:
            continue
        candidate = expand_path(value) if needs_expand else Path(value)
        if candidate.is_dir():
            results.append(candidate)
    return results


def documents_candidates() -> list[Path]:
    """Moegliche Dokumente-Ordner, bester Kandidat zuerst."""
    candidates: list[Path] = []
    candidates.extend(registry_documents_dirs())

    home = Path.home()
    candidates.append(home / "Documents")
    candidates.append(home / "Dokumente")
    candidates.append(home)

    # OneDrive und andere Umleitungen ("Dokumente" liegt dann oft in OneDrive).
    for pattern in ("OneDrive*", "OneDrive*/Dokumente", "OneDrive*/Documents"):
        try:
            candidates.extend(sorted(home.glob(pattern)))
        except OSError:
            continue

    # Registrierung als Nachzuegler, falls oben nichts passt.
    candidates.extend(registry_documents_dirs())

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen or not candidate.is_dir():
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def log_candidates(documents: list[Path]) -> list[Path]:
    return [folder / GAME_LOG_RELATIVE for folder in documents]


def scan_for_log(documents: list[Path]) -> list[Path]:
    """Sucht eine Ebene tiefer nach <Ordner>/My Games/FarmingSimulator2025/log.txt."""
    found: list[Path] = []
    bases = list(documents)
    try:
        bases.append(Path.home())
    except RuntimeError:
        pass

    for base in bases:
        try:
            entries = list(base.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir():
                continue
            candidate = entry / GAME_LOG_RELATIVE
            try:
                if candidate.is_file():
                    found.append(candidate)
            except OSError:
                continue
    return found


def resolve_log_path(explicit: str | None) -> tuple[Path, list[Path], str]:
    """Bestimmt die Logdatei.

    Reihenfolge: expliziter Pfad (CLI/Konfiguration) -> automatische Suche.
    Rueckgabe: (Pfad, Kandidaten fuer Fehlermeldungen, Herkunft)
    """
    documents = documents_candidates()
    discovered = log_candidates(documents)
    for extra in scan_for_log(documents):
        if extra not in discovered:
            discovered.append(extra)

    wanted: Path | None = None
    if explicit:
        wanted = expand_path(explicit)
        try:
            if wanted.is_file():
                return wanted, [wanted] + discovered, "konfiguriert"
        except OSError:
            pass

    for candidate in discovered:
        try:
            if candidate.is_file():
                return candidate, discovered, "automatisch gefunden"
        except OSError:
            continue

    if wanted is not None:
        return wanted, [wanted] + discovered, "nicht gefunden"
    if discovered:
        return discovered[0], discovered, "nicht gefunden"
    return Path.home() / GAME_LOG_RELATIVE, discovered, "nicht gefunden"


def get_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.5)
            sock.connect(("192.0.2.1", 80))
            return sock.getsockname()[0]
    except OSError:
        return "IP nicht ermittelbar"


# ---------------------------------------------------------------------------
# Telemetrie zerlegen
# ---------------------------------------------------------------------------

def to_number(value):
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text.lower() in ("n/a", "na", "none", "nil"):
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def to_bool(value):
    """True/False/None - None bedeutet 'vom Fahrzeug nicht geliefert'."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("", "n/a", "na", "none", "nil"):
        return None
    if text in ("1", "true", "on", "yes", "ja"):
        return True
    if text in ("0", "false", "off", "no", "nein"):
        return False
    return None


def parse_timestamp(line: str):
    """Epoche aus dem Log-Zeitstempel am Zeilenanfang (lokale Zeit)."""
    match = TIMESTAMP_RE.match(line)
    if not match:
        return None
    year, month, day, hour, minute, second, milli = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day, hour, minute, second, milli * 1000).timestamp()
    except ValueError:
        return None


def parse_fields(line: str):
    """Zerlegt eine TRACTORCOCKPIT-Zeile in Felder (oder None)."""
    position = line.find(MARKER)
    if position < 0:
        return None

    fields = {}
    for part in line[position + len(MARKER):].strip().split("|"):
        if "=" in part:
            key, value = part.split("=", 1)
            fields[key.strip()] = value.strip()
    return fields or None


def parse_implements(fields: dict, limit: int = 8) -> list[dict]:
    """Liest implCount/implN*-Felder. Es werden nur vorhandene Werte uebernommen."""
    count = to_number(fields.get("implCount"))
    if count is None or count <= 0:
        return []

    implements: list[dict] = []
    for index in range(1, min(int(count), limit) + 1):
        prefix = f"impl{index}"
        name = (fields.get(prefix + "Name") or "").strip()
        if not name or name.lower() == "n/a":
            continue
        implements.append(
            {
                "name": name,
                "type": (fields.get(prefix + "Type") or "").strip() or None,
                "lowered": to_bool(fields.get(prefix + "Lowered")),
                "unfolded": to_bool(fields.get(prefix + "Unfolded")),
                "on": to_bool(fields.get(prefix + "On")),
                "working": to_bool(fields.get(prefix + "Working")),
                "fill": to_number(fields.get(prefix + "Fill")),
                "fillCapacity": to_number(fields.get(prefix + "FillCapacity")),
                "fillType": (fields.get(prefix + "FillType") or "").strip() or None,
                # Faehigkeiten: daran haengen die Steuerknoepfe.
                "canLower": to_bool(fields.get(prefix + "CanLower")),
                "canFold": to_bool(fields.get(prefix + "CanFold")),
                "canOn": to_bool(fields.get(prefix + "CanOn")),
            }
        )
    return implements


def parse_name_list(value) -> list[str]:
    """'a,b' -> ['a','b']; 'none'/'n/a'/'' -> []."""
    text = (value or "").strip()
    if not text or text.lower() in ("n/a", "none", "unknown"):
        return []
    return [item.strip() for item in text.split(",") if item.strip() and item.strip() != "none"]


def parse_lights(fields: dict) -> dict | None:
    """Lichtzustand des Fahrzeugs (nur wenn der Mod ihn liefert)."""
    can_toggle = to_bool(fields.get("canToggleLight"))
    capabilities = parse_name_list(fields.get("lightCan"))
    if not capabilities and can_toggle is None:
        return None  # Fahrzeug ohne Lichtmeldung

    beacon_can = to_bool(fields.get("beaconCan"))
    turn_can = to_bool(fields.get("turnCan"))
    if beacon_can is True and "beacon" not in capabilities:
        capabilities.append("beacon")
    if turn_can is True and "turn" not in capabilities:
        capabilities.append("turn")
    if can_toggle is True and "toggle" not in capabilities:
        capabilities.append("toggle")

    lights = {
        "canToggle": can_toggle,
        "capabilities": capabilities,
        "on": parse_name_list(fields.get("lightOn")),
        "beacon": to_bool(fields.get("beacon")),
        "turnLight": (fields.get("turnLight") or "").strip() or None,
    }
    if lights["turnLight"] in ("", "unknown"):
        lights["turnLight"] = None
    return lights


def build_reading(fields: dict, stamp) -> dict:
    name = (fields.get("name") or "").strip()
    state_field = (fields.get("state") or "").strip().lower()
    has_vehicle = state_field != "foot" and name.lower() not in ("", "n/a", "no vehicle")

    fuel_type = (fields.get("fuelType") or "").strip().lower()

    return {
        "state": "vehicle" if has_vehicle else "foot",
        "vehicle": name if has_vehicle else None,
        "speed": to_number(fields.get("speed")),
        "rpm": to_number(fields.get("rpm")),
        "rpmMin": to_number(fields.get("rpmMin")),
        "rpmMax": to_number(fields.get("rpmMax")),
        "load": to_number(fields.get("load")),
        "fuel": to_number(fields.get("fuel")),
        "fuelCapacity": to_number(fields.get("fuelCapacity")),
        "fuelType": fuel_type if fuel_type and fuel_type != "n/a" else None,
        "engine": to_bool(fields.get("engine")),
        "hours": to_number(fields.get("hours")),
        "x": to_number(fields.get("x")),
        "y": to_number(fields.get("y")),
        "z": to_number(fields.get("z")),
        "implements": parse_implements(fields),
        "lights": parse_lights(fields),
        # Ergebnis des zuletzt ausgefuehrten Befehls (Steuerkanal).
        "cmdId": to_number(fields.get("cmdId")),
        "cmdResult": (fields.get("cmdResult") or "").strip() or None,
        "cmdMsg": (fields.get("cmdMsg") or "").strip() or None,
        "cmdReadErrors": to_number(fields.get("cmdReadErrors")) or 0,
        "cmdMode": (fields.get("cmdMode") or "").strip() or None,
        "cmdPath": (fields.get("cmdPath") or "").strip() or None,
        "updatedAt": stamp,
    }


EMPTY_READING: dict = {
    "state": "unknown",
    "vehicle": None,
    "speed": None,
    "rpm": None,
    "rpmMin": None,
    "rpmMax": None,
    "load": None,
    "fuel": None,
    "fuelCapacity": None,
    "fuelType": None,
    "engine": None,
    "hours": None,
    "x": None,
    "y": None,
    "z": None,
    "implements": [],
    "lights": None,
    "cmdId": None,
    "cmdResult": None,
    "cmdMsg": None,
    "cmdReadErrors": 0,
    "cmdMode": None,
    "cmdPath": None,
    "updatedAt": None,
}


# ---------------------------------------------------------------------------
# Telemetrie lesen (zwei Kanaele)
# ---------------------------------------------------------------------------

class TelemetryReader:
    """Liest Datei-Kanal und Log-Kanal und nimmt den frischesten Wert.

    Beide Kanaele werden inkrementell bzw. nur bei Aenderung gelesen, damit
    ein Abruf im laufenden Betrieb praktisch keine Rechenzeit kostet.
    """

    def __init__(
        self,
        log_path: Path,
        stale_seconds: float = 15.0,
        source: str = "",
        telemetry_path: Path | None = None,
    ) -> None:
        self.log_path = Path(log_path)
        self.telemetry_path = Path(telemetry_path) if telemetry_path else (
            self.log_path.parent / TELEMETRY_FILE_RELATIVE
        )
        self.stale_seconds = float(stale_seconds)
        self.source = source
        self.candidates: list[Path] = []

        self._lock = threading.Lock()

        # Log-Kanal
        self._offset = 0
        self._partial = ""
        self._log_reading: dict | None = None
        self._log_signature = None
        self._log_error: str | None = None

        # Datei-Kanal
        self._file_reading: dict | None = None
        self._file_signature = None
        self._file_error: str | None = None

        self._saw_marker = False

        # Messwerte
        self._log_baseline: tuple[float, int] | None = None
        self._log_growth_per_hour: float | None = None
        self._log_size = 0
        self._read_calls = 0
        self._parsed_lines = 0
        self._started_at = time.time()

    # -- Log-Kanal ---------------------------------------------------------
    def _consume_log_line(self, line: str, fallback_stamp) -> None:
        if MARKER not in line:
            return
        fields = parse_fields(line)
        if not fields:
            return
        stamp = parse_timestamp(line)
        if stamp is None:
            stamp = fallback_stamp
        self._log_reading = build_reading(fields, stamp)
        self._saw_marker = True
        self._parsed_lines += 1

    def _reset_log_stream(self) -> None:
        self._offset = 0
        self._partial = ""

    def _read_log_channel(self) -> None:
        try:
            stat = self.log_path.stat()
        except OSError:
            self._log_error = "Logdatei nicht gefunden"
            self._log_size = 0
            return

        # Neue Spielsitzung oder rotierte Datei erkennen: andere Datei-Identitaet
        # (Windows/NTFS liefert st_ino/st_dev), andere Erstellungszeit oder
        # geschrumpfte Datei. st_birthtime gibt es nicht auf jedem Dateisystem,
        # deshalb ist es nur ein Zusatzsignal.
        signature = (stat.st_dev, stat.st_ino, getattr(stat, "st_birthtime", None))
        if self._log_signature is not None and signature != self._log_signature:
            self._reset_log_stream()
        self._log_signature = signature
        if stat.st_size < self._offset:
            self._reset_log_stream()

        self._log_size = stat.st_size
        self._read_calls += 1

        chunk = ""
        try:
            with self.log_path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self._offset)
                chunk = handle.read(MAX_READ_BYTES)
                self._offset = handle.tell()
        except OSError as exc:
            self._log_error = f"Logdatei nicht lesbar: {exc}"
            return

        self._log_error = None
        if not chunk:
            return

        self._partial += chunk
        if len(self._partial) > PARTIAL_LINE_LIMIT and "\n" not in self._partial[-PARTIAL_LINE_LIMIT:]:
            self._partial = self._partial[-4096:]

        lines = self._partial.split("\n")
        self._partial = lines.pop()
        for line in lines:
            self._consume_log_line(line.rstrip("\r"), stat.st_mtime)

    # -- Datei-Kanal -------------------------------------------------------
    def _read_file_channel(self) -> None:
        try:
            stat = self.telemetry_path.stat()
        except OSError:
            self._file_error = "Telemetriedatei nicht vorhanden"
            return

        # Nur bei Aenderung lesen: Schreiben passiert mehrmals pro Sekunde.
        signature = (stat.st_size, stat.st_mtime_ns)
        if signature == self._file_signature:
            self._file_error = None
            return
        self._file_signature = signature
        self._file_error = None

        if stat.st_size <= 0 or stat.st_size > MAX_TELEMETRY_FILE_BYTES:
            return

        try:
            text = self.telemetry_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self._file_error = f"Telemetriedatei nicht lesbar: {exc}"
            return

        for line in reversed(text.splitlines()):
            fields = parse_fields(line)
            if fields:
                self._file_reading = build_reading(fields, stat.st_mtime)
                self._saw_marker = True
                return

    # -- Auswahl und Aufbereitung -----------------------------------------
    def _pick(self) -> tuple[dict | None, str | None, float | None]:
        options = []
        if self._log_reading is not None:
            options.append((self._log_reading.get("updatedAt") or 0.0, self._log_reading, "log"))
        if self._file_reading is not None:
            options.append((self._file_reading.get("updatedAt") or 0.0, self._file_reading, "file"))
        if not options:
            return None, None, None
        options.sort(key=lambda item: item[0], reverse=True)
        stamp, reading, channel = options[0]
        return reading, channel, stamp

    def _update_metrics(self, now: float) -> None:
        if self._log_baseline is None:
            self._log_baseline = (now, self._log_size)
            return
        started, size = self._log_baseline
        elapsed = now - started
        if elapsed >= 60.0:
            self._log_growth_per_hour = (self._log_size - size) / elapsed * 3600.0
            if self._log_growth_per_hour < 0:
                self._log_growth_per_hour = 0.0
            self._log_baseline = (now, self._log_size)

    def poll(self) -> dict:
        with self._lock:
            self._read_log_channel()
            self._read_file_channel()

            now = time.time()
            self._update_metrics(now)

            reading, channel, stamp = self._pick()
            payload = dict(reading) if reading else dict(EMPTY_READING)
            payload["implements"] = list(payload.get("implements") or [])

            age = max(0.0, now - stamp) if isinstance(stamp, (int, float)) else None
            fresh = reading is not None and (age is None or age <= self.stale_seconds)
            stale = reading is not None and not fresh

            try:
                log_exists = self.log_path.is_file()
            except OSError:
                log_exists = False
            try:
                file_exists = self.telemetry_path.is_file()
                file_age = max(0.0, now - self.telemetry_path.stat().st_mtime)
            except OSError:
                file_exists = False
                file_age = None

            payload.update(
                {
                    "connected": fresh,
                    "stale": stale,
                    "channel": channel,
                    "ageSeconds": age,
                    "staleSeconds": self.stale_seconds,
                    "logPath": str(self.log_path),
                    "logPathSource": self.source,
                    "logExists": log_exists,
                    "logSizeBytes": self._log_size,
                    "logGrowthPerHour": self._log_growth_per_hour,
                    "telemetryPath": str(self.telemetry_path),
                    "telemetryFileExists": file_exists,
                    "telemetryFileAgeSeconds": file_age,
                    "modSeen": self._saw_marker,
                    "serverTime": now,
                    "message": self._message(reading, fresh, stale, age, log_exists, file_exists),
                }
            )
            # Kandidatenliste nur mitschicken, wenn sie gebraucht wird
            # (haelt die Antwort fuer das Tablet klein).
            if not log_exists:
                payload["candidatePaths"] = [str(path) for path in self.candidates]
            return payload

    def _message(self, reading, fresh: bool, stale: bool, age, log_exists: bool, file_exists: bool) -> str:
        if not log_exists and not file_exists:
            return (
                f"Weder Logdatei ({self.log_path}) noch Telemetriedatei gefunden. "
                "Starte LS25 einmal und pruefe den Pfad in config/settings.json."
            )
        if self._saw_marker is False:
            return (
                "Spielordner gefunden, aber keine TRACTORCOCKPIT-Daten. "
                "Mod im Spielstand aktivieren und in ein Fahrzeug einsteigen."
            )
        if stale:
            age_text = "unbekannt" if age is None else f"{age:.0f} s"
            return (
                f"Keine frischen Daten (letzte Zeile vor {age_text}). "
                "Laeuft LS25, oder ist das Spiel pausiert?"
            )
        if reading is not None and reading.get("state") == "foot":
            return "Spieler ist zu Fuss unterwegs - kein Fahrzeug gesteuert."
        if not fresh:
            return "Telemetrie empfangen, aber ohne verwertbaren Zeitstempel."
        return "Live aus dem Spiel (Mod schreibt Telemetrie)."


# ---------------------------------------------------------------------------
# Messhilfen
# ---------------------------------------------------------------------------

class PerformanceMonitor:
    """Mittelt kurze Messreihen (Rechenzeit, Nutzlast, Anfragen pro Minute)."""

    def __init__(self, window: int = 120) -> None:
        self._lock = threading.Lock()
        self._samples: dict[str, deque] = {}
        self._gauges: dict[str, float] = {}
        self._counters: dict[str, int] = {}
        self._window = window

    def add(self, name: str, seconds: float) -> None:
        with self._lock:
            series = self._samples.get(name)
            if series is None:
                series = deque(maxlen=self._window)
                self._samples[name] = series
            series.append(max(0.0, seconds) * 1000.0)

    def set(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def count(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def stats(self) -> dict:
        with self._lock:
            result: dict = {}
            for name, series in self._samples.items():
                if not series:
                    continue
                values = list(series)
                result[name] = {
                    "avgMs": round(sum(values) / len(values), 3),
                    "maxMs": round(max(values), 3),
                    "samples": len(values),
                }
            result.update({name: round(value, 3) for name, value in self._gauges.items()})
            result["counters"] = dict(self._counters)
            return result


class TimedCache:
    """Sehr einfacher Zwischenspeicher mit Ablaufzeit (z. B. fuer den Verlauf)."""

    def __init__(self, ttl_seconds: float, max_entries: int = 16) -> None:
        self.ttl = float(ttl_seconds)
        self.max_entries = int(max_entries)
        self._lock = threading.Lock()
        self._items: dict = {}

    def get(self, key, producer):
        """Rueckgabe: (Wert, aus_dem_Zwischenspeicher)."""
        now = time.time()
        with self._lock:
            item = self._items.get(key)
            if item is not None and now - item[0] < self.ttl:
                return item[1], True

        value = producer()

        with self._lock:
            self._items[key] = (time.time(), value)
            if len(self._items) > self.max_entries:
                oldest = min(self._items, key=lambda item: self._items[item][0])
                self._items.pop(oldest, None)
        return value, False


# ---------------------------------------------------------------------------
# Steuerkanal (Phase 4)
# ---------------------------------------------------------------------------

def sanitize_field(value, limit: int = 80) -> str:
    """Feld fuer die Befehlsdatei: keine Trennzeichen, keine Zeilenumbrueche."""
    text = str(value if value is not None else "")
    for bad in ("|", "\r", "\n", "="):
        text = text.replace(bad, "_")
    return text[:limit]


def build_command_xml(command_id: int, action: str, target: int, name) -> str:
    """XML-Befehl. Die Mod liest ihn ueber XMLFile.load (io.open darf nur schreiben)."""
    attribute_name = escape(str(name if name is not None else ""), {'"': "&quot;"})
    return (
        '<?xml version="1.0" encoding="utf-8" standalone="no"?>\n'
        f'<tractorcockpit id="{int(command_id)}" action="{escape(str(action))}"'
        f' target="{int(target)}" name="{attribute_name}"/>\n'
    )


class CommandController:
    """Schreibt genau eine Befehlszeile und prueft die Rueckmeldung des Spiels.

    Sicherheitsgrenzen:

    * Steuerung ist ohne ausdrueckliche Freigabe vollstaendig aus.
    * Nur Aktionen aus ``CONTROL_ACTIONS`` werden geschrieben (Whitelist).
    * Ein Befehl nach dem anderen, mit Mindestabstand und Zeitlimit.
    * Nur Geraete, die die Aktion laut Telemetrie unterstuetzen.
    * Es wird nie freier Lua-Code oder ein Funktionsname uebertragen, sondern
      immer nur ein Aktionsname aus der Whitelist plus Geraeteplatz.
    * Die Bestaetigung kommt aus dem echten Spielzustand, nicht aus dem Klick.
    """

    def __init__(
        self,
        path,
        enabled: bool = False,
        timeout_seconds: float = 5.0,
        min_interval_ms: int = 500,
        log_size: int = 15,
    ) -> None:
        self.path = Path(path) if path else None
        self.enabled = bool(enabled)
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.min_interval = max(0.0, float(min_interval_ms) / 1000.0)
        self.log_size = int(log_size)
        self.write_errors = 0

        self._lock = threading.Lock()
        self._counter = 0
        self._pending: dict | None = None
        self._last: dict | None = None
        self._log: list[dict] = []
        self._last_submit = 0.0
        self.active_path: Path | None = None
        self.reported_path: Path | None = None
        self.reported_mode: str | None = None

        # Optionale Aufzeichnung (SQLite): Callable[[dict], None]
        self.recorder = None
        self.confirm_recorder = None
        self._confirmation_saved: set[int] = set()

    # -- Dateien -----------------------------------------------------------
    def _adopt_channel(self, reading) -> None:
        """Pfad und Modus uebernehmen, die die Mod als lesbar gemeldet hat."""
        if not reading:
            return
        mode = reading.get("cmdMode")
        if mode:
            self.reported_mode = mode
        path = reading.get("cmdPath")
        if mode == "xml" and path:
            try:
                self.reported_path = Path(path)
            except (TypeError, ValueError):
                self.reported_path = None

    def command_path(self) -> Path | None:
        """Pfad, den die Mod als lesbar gemeldet hat - sonst der Standardpfad."""
        return self.reported_path or self.path

    def cleanup(self) -> None:
        """Reste der frueheren Textdatei-Version entfernen."""
        directory = self.path.parent if self.path else None
        if directory is None:
            return
        names = (
            "FS25_TractorCockpit_cmd.txt",
            "FS25_TractorCockpit_cmd2.txt",
            "FS25_TractorCockpit_cmd.txt.tmp",
            "FS25_TractorCockpit_cmd2.txt.tmp",
        )
        for name in names:
            junk = directory / name
            try:
                if junk.exists():
                    junk.unlink()
                    print(f"[Steuerung] Alte Datei entfernt: {name}")
            except OSError as exc:
                print(f"[Steuerung] Hinweis: {name} laesst sich nicht entfernen ({exc})")

    def _reset_command_file(self) -> None:
        """Datei auf id=0 setzen - eine leere Datei waere kein gueltiges XML."""
        target = self.active_path or self.command_path()
        if target is None:
            return
        try:
            with target.open("wb") as handle:
                handle.write(build_command_xml(0, "", 0, "").encode("utf-8"))
        except OSError:
            pass

    def _write_command(self, command: dict) -> str | None:
        """Schreibt den Befehl als XML in die von der Mod gemeldete Datei.

        Kein ``os.replace``: das braucht unter Windows DELETE-Rechte am Ziel
        und scheitert deshalb leichter, wenn das Spiel die Datei offen hat.
        Ein normales Schreiben ist vertraeglicher; zusaetzlich wird mehrfach
        versucht.
        """
        target = self.command_path()
        if target is None:
            return None

        data = build_command_xml(
            command["id"], command["action"], command["target"], command["name"]
        ).encode("utf-8")
        last_error: OSError | None = None

        for attempt in range(5):
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as handle:
                    handle.write(data)
                self.active_path = target
                return str(target)
            except OSError as exc:
                last_error = exc
                time.sleep(0.04 * (attempt + 1))

        self.active_path = None
        print(f"[Steuerung] Schreiben fehlgeschlagen ({target}): {last_error}")
        return None

    # -- Befehl annehmen ---------------------------------------------------
    def submit(self, action, target, reading, client: str = "?"):
        """Prueft einen Befehlsauftrag. Rueckgabe: (http_status, payload)."""
        now = time.time()
        with self._lock:
            self._adopt_channel(reading)
            if not self.enabled:
                return 403, {
                    "ok": False,
                    "error": "Steuerung ist deaktiviert. Server mit --enable-control starten.",
                }
            if self.path is None:
                return 503, {"ok": False, "error": "Befehlsdatei ist nicht konfiguriert."}
            if action not in CONTROL_ACTIONS:
                allowed = ", ".join(sorted(CONTROL_ACTIONS))
                return 400, {"ok": False, "error": f"Unbekannte Aktion. Erlaubt: {allowed}."}
            if isinstance(target, bool) or not isinstance(target, int) or target < 0 or target > 8:
                return 400, {
                    "ok": False,
                    "error": "target muss 0 (Fahrzeug) oder 1 bis 8 (Anbaugeraet) sein.",
                }
            if not reading or not reading.get("connected"):
                return 409, {
                    "ok": False,
                    "error": "Keine aktuellen Daten vom Spiel - Befehl abgelehnt.",
                }

            # Der Mod darf nur schreiben und liest die Befehle ueber die
            # XML-API. Meldet er keinen lesbaren Kanal, wird nichts gesendet.
            if reading.get("cmdMode") != "xml":
                return 409, {
                    "ok": False,
                    "error": (
                        "Die Mod meldet keinen lesbaren Steuerkanal. FS25 erlaubt Mods "
                        "nur das Schreiben von Dateien - Details stehen im log.txt "
                        "(Zeile 'Steuerkanal')."
                    ),
                }

            if self._pending is not None and now - self._pending["at"] < self.timeout_seconds:
                return 409, {
                    "ok": False,
                    "error": "Es laeuft noch ein Befehl - eine Aktion nach der anderen.",
                }
            if now - self._last_submit < self.min_interval:
                return 429, {"ok": False, "error": "Zu schnell - bitte kurz warten."}

            action_info = CONTROL_ACTIONS[action]
            scope = action_info.get("scope", "implement")
            name = None
            before: dict = {}

            if scope == "vehicle":
                lights = reading.get("lights") or {}
                if not lights:
                    return 409, {
                        "ok": False,
                        "error": "Dieses Fahrzeug meldet keine Lichtsteuerung.",
                    }
                if target != 0:
                    return 400, {
                        "ok": False,
                        "error": "Lichtbefehle gelten fuer das Fahrzeug (target=0).",
                    }
                if lights.get("canToggle") is not True:
                    return 409, {
                        "ok": False,
                        "error": (
                            "Licht kann gerade nicht geschaltet werden "
                            "(nicht eingestiegen oder ein Helfer faehrt)."
                        ),
                    }

                capability = action_info.get("capability") or ""
                capabilities = lights.get("capabilities") or []
                if capability.startswith("light:"):
                    light_name = capability.split(":", 1)[1]
                    if light_name not in capabilities:
                        return 409, {
                            "ok": False,
                            "error": f"Dieses Fahrzeug hat kein {light_name}-Licht.",
                        }
                elif capability and capability not in capabilities:
                    return 409, {
                        "ok": False,
                        "error": "Diese Funktion unterstuetzt das Fahrzeug nicht.",
                    }

                name = reading.get("vehicle") or "Fahrzeug"
                before = {
                    "lightsOn": list(lights.get("on") or []),
                    "beacon": lights.get("beacon"),
                    "turnLight": lights.get("turnLight"),
                }
            else:
                if target < 1:
                    return 400, {"ok": False, "error": "Geraetebefehle brauchen target 1 bis 8."}
                implements = reading.get("implements") or []
                if target > len(implements):
                    return 404, {"ok": False, "error": f"Geraet {target} haengt nicht an."}
                implement = implements[target - 1]
                if not implement.get(action_info["capability"]):
                    return 409, {
                        "ok": False,
                        "error": (
                            f"{implement.get('name') or 'Geraet'} unterstuetzt "
                            f"'{action}' laut Spiel nicht."
                        ),
                    }
                name = implement.get("name")
                before = {
                    "lowered": implement.get("lowered"),
                    "unfolded": implement.get("unfolded"),
                    "on": implement.get("on"),
                }

            self._counter += 1
            command = {
                "id": self._counter,
                "action": action,
                "scope": scope,
                "target": target,
                "name": name,
                "client": client,
                "at": now,
                "status": "pending",
                "message": "an das Spiel gesendet",
                "before": before,
            }

            written_to = self._write_command(command)
            if written_to is None:
                self.write_errors += 1
                return 500, {
                    "ok": False,
                    "error": (
                        f"Befehlsdatei nicht schreibbar ({self.command_path()}). "
                        "Pfad und Rechte pruefen."
                    ),
                }

            command["file"] = written_to
            self._pending = command
            self._last_submit = now
            self._append_log(command)
            print(
                f"[Steuerung] Befehl {command['id']}: {action} -> '{command['name']}'"
                f" (target {target}) von {client} -> {Path(written_to).name}"
            )
            return 200, {"ok": True, "command": dict(command)}

    def _append_log(self, entry: dict) -> None:
        self._log.append(dict(entry))
        if len(self._log) > self.log_size:
            del self._log[:-self.log_size]

    # -- Rueckmeldung aus dem Spiel ----------------------------------------
    def update(self, reading) -> None:
        """Uebernimmt cmdId/cmdResult/cmdMsg aus der Telemetrie des Mods."""
        if not reading:
            return
        cmd_id = reading.get("cmdId")
        if cmd_id is None:
            return
        with self._lock:
            pending = self._pending
            if pending is None or int(cmd_id) != pending["id"]:
                return
            # Pfad und Modus der Mod uebernehmen (sie entscheidet, was lesbar ist).
            self._adopt_channel(reading)

            pending["status"] = reading.get("cmdResult") or "error"
            pending["message"] = reading.get("cmdMsg") or ""
            pending["answeredAt"] = time.time()
            self._pending = None
            self._last = pending
            self._append_log(pending)
            self._reset_command_file()
            if self.recorder is not None:
                self.recorder(dict(pending))
            print(f"[Steuerung] Befehl {pending['id']} -> {pending['status']} ({pending['message']})")

    def _finish_timeout(self, now: float) -> None:
        pending = self._pending
        if pending is None or now - pending["at"] < self.timeout_seconds:
            return
        pending["status"] = "timeout"
        pending["message"] = "keine Rueckmeldung aus dem Spiel"
        pending["answeredAt"] = now
        self._pending = None
        self._last = pending
        self._append_log(pending)
        self._reset_command_file()
        if self.recorder is not None:
            self.recorder(dict(pending))
        print(f"[Steuerung] Befehl {pending['id']} -> timeout")

    def _annotate_confirmation(self, command: dict, reading) -> dict:
        """Prueft am echten Spielzustand, ob der Befehl gewirkt hat."""
        if command.get("status") != "ok":
            command["confirmed"] = None
            return command

        action_info = CONTROL_ACTIONS.get(command.get("action"), {})
        scope = command.get("scope") or action_info.get("scope", "implement")
        expected = action_info.get("expect")

        if scope == "vehicle":
            lights = (reading or {}).get("lights") or {}
            if expected is None:
                # lightNext/lightPrev: Ergebnis ist die Lichtstufe, nicht ein Bit.
                command["confirmed"] = None
                return command
            field, value = expected
            command["confirmField"] = field
            if field.startswith("light:"):
                light_name = field.split(":", 1)[1]
                command["confirmed"] = (light_name in (lights.get("on") or [])) == bool(value)
            else:
                command["confirmed"] = lights.get(field) == value
            self._record_confirmation(command)
            return command

        implements = (reading or {}).get("implements") or []
        index = int(command.get("target", 0)) - 1
        if index < 0 or index >= len(implements):
            command["confirmed"] = None
            command["confirmHint"] = "Geraet haengt nicht mehr an"
            return command

        implement = implements[index]
        if expected is not None:
            field, value = expected
            command["confirmField"] = field
            command["confirmed"] = implement.get(field) == value
        else:
            # toggleFold: Richtung ist nicht vorhersagbar, also zaehlt die
            # tatsaechliche Aenderung des Klappzustands.
            field = "unfolded"
            before = (command.get("before") or {}).get(field)
            after = implement.get(field)
            command["confirmField"] = field
            command["confirmed"] = (
                after is not None and before is not None and after != before
            )

        self._record_confirmation(command)
        return command

    def _record_confirmation(self, command: dict) -> None:
        """Die Bestaetigung einmalig ins Protokoll schreiben (nicht bei jedem Abruf)."""
        if command.get("confirmed") is None or self.confirm_recorder is None:
            return
        command_id = command.get("id")
        if command_id in self._confirmation_saved:
            return
        self._confirmation_saved.add(command_id)
        if len(self._confirmation_saved) > 2000:
            self._confirmation_saved.clear()
            self._confirmation_saved.add(command_id)
        self.confirm_recorder(command_id, command["confirmed"])

    @staticmethod
    def _log_entry(entry: dict) -> dict:
        """Nur das, was das Dashboard wirklich anzeigt (spart Antwortgroesse)."""
        return {
            "id": entry.get("id"),
            "action": entry.get("action"),
            "target": entry.get("target"),
            "name": entry.get("name"),
            "status": entry.get("status"),
            "message": entry.get("message"),
            "at": entry.get("at"),
        }

    def snapshot(self, reading) -> dict:
        now = time.time()
        with self._lock:
            self._finish_timeout(now)
            last = dict(self._last) if self._last else None
            if last is not None:
                last = self._annotate_confirmation(last, reading)
            return {
                "enabled": self.enabled,
                "configured": self.path is not None,
                "path": str(self.command_path()) if self.command_path() else None,
                "mode": (reading or {}).get("cmdMode"),
                "activePath": str(self.active_path) if self.active_path else None,
                "writeErrors": self.write_errors,
                "readErrors": int((reading or {}).get("cmdReadErrors") or 0),
                "pending": dict(self._pending) if self._pending else None,
                "count": self._counter,
                "actionCount": len(CONTROL_ACTIONS),
                "last": last,
                "log": [self._log_entry(entry) for entry in self._log[-6:]],
            }


# ---------------------------------------------------------------------------
# Hintergrund-Erfassung
# ---------------------------------------------------------------------------

class TelemetrySampler:
    """Holt die Telemetrie in eigenem Takt ab und schreibt sie in die Datenbank.

    Dadurch muss kein HTTP-Abruf mehr Dateien lesen: die Antwort kommt aus dem
    Zwischenspeicher, und der Verlauf wird auch dann lueckenlos aufgezeichnet,
    wenn gerade kein Browser offen ist.
    """

    def __init__(self, reader: TelemetryReader, controller: CommandController,
                 store: "storage.HistoryStore | None" = None,
                 interval_seconds: float = 0.5, perf: PerformanceMonitor | None = None) -> None:
        self.reader = reader
        self.controller = controller
        self.store = store
        self.interval = max(0.05, float(interval_seconds))
        self.perf = perf or PerformanceMonitor()
        self.cycles = 0
        self._reading: dict | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> dict:
        started = time.perf_counter()
        reading = self.reader.poll()
        after_read = time.perf_counter()
        self.controller.update(reading)
        if self.store is not None:
            self.store.record(reading)
        finished = time.perf_counter()

        self.perf.add("samplerRead", after_read - started)
        self.perf.add("samplerStore", finished - after_read)
        self.perf.add("samplerTotal", finished - started)

        with self._lock:
            self._reading = reading
            self.cycles += 1
        return reading

    def latest(self, force: bool = False) -> dict:
        if force or self._reading is None:
            return self.tick()
        with self._lock:
            return self._reading

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="telemetry-sampler", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # nie den Faden sterben lassen
                print(f"[Erfassung] Fehler: {exc}")
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _query_number(query: dict, key: str, default: float, low: float, high: float) -> float:
    """Zahl aus der URL, begrenzt auf einen sinnvollen Bereich."""
    values = query.get(key)
    if not values:
        return default
    try:
        number = float(values[0])
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return max(low, min(number, high))


def _origin_allowed(origin: str, host: str) -> bool:
    """Schuetzt vor Befehlen aus einer fremden Webseite (CSRF).

    Der Server hat keine Anmeldung. Ohne diese Pruefung koennte jede Seite, die
    der Nutzer im Browser offen hat, im Hintergrund Befehle an das Spiel senden.
    """
    if not origin:
        return True  # kein Origin-Header: gleiche Herkunft oder Nicht-Browser
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    if not parsed.netloc:
        return origin.strip().lower() in ("null", "")
    return parsed.netloc.lower() == (host or "").strip().lower()


def make_handler(
    reader: TelemetryReader,
    poll_ms: int,
    controller: CommandController,
    sampler: TelemetrySampler | None = None,
    store: "storage.HistoryStore | None" = None,
):
    if sampler is None:
        sampler = TelemetrySampler(reader, controller)
    if store is None:
        store = storage.HistoryStore(None, enabled=False)

    # Eigene Namen: im Klassenkoerper wuerde "sampler = sampler" nicht auf die
    # lokale Variable der umgebenden Funktion zeigen.
    active_sampler = sampler
    active_store = store
    active_cache = TimedCache(HISTORY_CACHE_SECONDS)

    class Handler(BaseHTTPRequestHandler):
        telemetry = reader
        control = controller
        sampler = active_sampler
        history = active_store
        history_cache = active_cache
        client_poll_ms = poll_ms
        server_version = "TractorCockpit/0.7"
        protocol_version = "HTTP/1.1"

        def handle(self):
            # Browser und Tablets schliessen Verbindungen jederzeit; das ist
            # normal und soll keinen Traceback im Konsolenfenster erzeugen.
            try:
                super().handle()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                pass

        def log_message(self, fmt, *args):  # noqa: A003 - Signatur der Basisklasse
            path = getattr(self, "path", "") or ""
            if "/api/" in path or path == "":
                return
            super().log_message(fmt, *args)

        # -- Helfer --------------------------------------------------------
        def _send(self, status: int, body: bytes, content_type: str,
                  allow_gzip: bool = True) -> None:
            encoding = ""
            if (
                allow_gzip
                and len(body) >= GZIP_MIN_BYTES
                and "gzip" in (self.headers.get("Accept-Encoding") or "").lower()
            ):
                try:
                    compressed = gzip.compress(body, 6)
                except (OSError, ValueError):
                    compressed = body
                if len(compressed) < len(body):
                    body = compressed
                    encoding = "gzip"

            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if encoding:
                self.send_header("Content-Encoding", encoding)
            self.send_header("Vary", "Accept-Encoding")
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            if self.command == "HEAD":
                return
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # Browser/Tablet hat die Verbindung geschlossen.

        def _send_json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.sampler.perf.set("payloadBytes", float(len(body)))
            self._send(status, body, "application/json; charset=utf-8")

        def _send_text(self, status: int, text: str) -> None:
            self._send(status, text.encode("utf-8"), "text/plain; charset=utf-8")

        # -- Routen --------------------------------------------------------
        def do_GET(self):  # noqa: N802 - Name von BaseHTTPRequestHandler
            parsed = urlparse(self.path)
            path = unquote(parsed.path)

            if path == "/api/telemetry":
                self.sampler.perf.count("apiRequests")
                reading = self.sampler.latest()
                self.control.update(reading)
                payload = dict(reading)
                payload["pollMs"] = self.client_poll_ms
                payload["control"] = self.control.snapshot(reading)
                payload["storage"] = self.history.stats()
                payload["performance"] = self.sampler.perf.stats()
                self._send_json(payload)
                return

            if path == "/api/history":
                self.sampler.perf.count("apiRequests")
                query = parse_qs(parsed.query)
                minutes = _query_number(query, "minutes", 30.0, 0.5, 43200.0)
                buckets = int(_query_number(query, "buckets", 120, 10, 600))
                key = (round(minutes, 2), buckets)
                series, cached = self.history_cache.get(
                    key, lambda: self.history.series(minutes, buckets)
                )
                series = dict(series)
                series["cached"] = cached
                self._send_json(series)
                return

            if path == "/api/commands":
                self.sampler.perf.count("apiRequests")
                query = parse_qs(parsed.query)
                limit = int(_query_number(query, "limit", 20, 1, 200))
                self._send_json(
                    {
                        "available": self.history.available,
                        "commands": self.history.recent_commands(limit),
                    }
                )
                return

            if path == "/favicon.ico":
                self._send(204, b"", "image/x-icon")
                return

            if path in ("", "/"):
                path = "/index.html"

            if "\x00" in path:
                self._send_text(400, "Ungueltiger Pfad.")
                return

            target = (WEB_ROOT / path.lstrip("/\\")).resolve()
            web_root = WEB_ROOT.resolve()
            if target != web_root and web_root not in target.parents:
                self._send_text(403, "Zugriff ausserhalb des Web-Ordners.")
                return
            if not target.is_file():
                self._send_text(404, "Nicht gefunden.")
                return

            try:
                body = target.read_bytes()
            except OSError as exc:
                self._send_text(500, f"Datei nicht lesbar: {exc}")
                return

            mime = MIME_TYPES.get(target.suffix.lower(), "application/octet-stream")
            self._send(200, body, mime)

        def do_HEAD(self):  # noqa: N802
            self.do_GET()

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            if unquote(parsed.path) != "/api/control":
                self.close_connection = True
                self._send_text(404, "Nicht gefunden.")
                return

            # Den Body IMMER zuerst lesen - auch wenn die Anfrage abgelehnt
            # wird. Sonst bleiben die Bytes im Socket und die naechste Anfrage
            # auf derselben HTTP/1.1-Verbindung wird als Unsinn gelesen.
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_REQUEST_BYTES:
                self.close_connection = True
                self._send_json({"ok": False, "error": "Ungueltige Anfragegroesse."}, 400)
                return
            raw = self.rfile.read(length) if length > 0 else b""

            # Kein Befehl aus einer fremden Webseite heraus.
            if not _origin_allowed(self.headers.get("Origin", ""), self.headers.get("Host", "")):
                self._send_json({"ok": False, "error": "Origin nicht erlaubt."}, 403)
                return

            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/json":
                self._send_json(
                    {"ok": False, "error": "Content-Type muss application/json sein."}, 415
                )
                return

            if not raw:
                self._send_json({"ok": False, "error": "Leere Anfrage."}, 400)
                return

            try:
                request = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "Kein gueltiges JSON."}, 400)
                return
            if not isinstance(request, dict):
                self._send_json({"ok": False, "error": "JSON-Objekt erwartet."}, 400)
                return

            target = request.get("target")
            if isinstance(target, str) and target.strip().lstrip("-").isdigit():
                target = int(target.strip())

            reading = self.sampler.latest(force=True)
            status, body = self.control.submit(
                request.get("action"), target, reading, self.client_address[0]
            )
            if status == 200:
                body["control"] = self.control.snapshot(reading)
            self._send_json(body, status)

    return Handler


def build_server(
    host: str,
    port: int,
    reader: TelemetryReader,
    poll_ms: int = 500,
    controller: CommandController | None = None,
    store: "storage.HistoryStore | None" = None,
    sampler: TelemetrySampler | None = None,
) -> ThreadingHTTPServer:
    if controller is None:
        controller = CommandController(None, enabled=False)
    handler = make_handler(reader, poll_ms, controller, sampler, store)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Lokaler Telemetrie-Server fuer das LS25 Tractor Cockpit (nur lesend).",
    )
    parser.add_argument("--host", help="Bind-Adresse (Standard: 0.0.0.0 = auch Tablet im WLAN)")
    parser.add_argument("--port", type=int, help="Port (Standard: 8765)")
    parser.add_argument("--log", help="Pfad zur LS25 log.txt (Standard: automatische Suche)")
    parser.add_argument(
        "--telemetry-file",
        dest="telemetry_file",
        help="Pfad zur Telemetriedatei des Mods (Standard: <Spielprofil>/modSettings/FS25_TractorCockpit.txt)",
    )
    parser.add_argument(
        "--stale-seconds",
        type=float,
        dest="stale_seconds",
        help="Ab wann Daten als veraltet gelten (Standard: 15)",
    )
    parser.add_argument(
        "--poll-ms",
        type=int,
        dest="poll_ms",
        help="Empfohlenes Abfrageintervall fuer das Dashboard in ms (Standard: 500)",
    )
    parser.add_argument(
        "--enable-control",
        action="store_true",
        dest="enable_control",
        help="Steuerung einschalten (Befehle an das Spiel). Standard: aus.",
    )
    parser.add_argument(
        "--command-file",
        dest="command_file",
        help="Pfad zur Befehlsdatei (Standard: <Spielprofil>/modSettings/FS25_TractorCockpit_cmd.txt)",
    )
    parser.add_argument(
        "--command-timeout",
        type=float,
        dest="command_timeout",
        help="Sekunden, die auf die Rueckmeldung des Spiels gewartet wird (Standard: 5)",
    )
    parser.add_argument(
        "--command-min-interval-ms",
        type=int,
        dest="command_min_interval_ms",
        help="Mindestabstand zwischen zwei Befehlen in ms (Standard: 500)",
    )
    parser.add_argument(
        "--database",
        help="Pfad zur SQLite-Datenbank (Standard: server/data/history.sqlite3)",
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        dest="no_history",
        help="Verlauf nicht in die Datenbank schreiben",
    )
    parser.add_argument("--config", help="Pfad zu config/settings.json")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    config_path = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    config = load_config(config_path)

    explicit_log = args.log or config.get("log_path") or ""
    log_path, candidates, source = resolve_log_path(explicit_log)

    explicit_file = args.telemetry_file or config.get("telemetry_file") or ""
    telemetry_path = expand_path(explicit_file) if explicit_file else None

    host = args.host or str(config.get("host", "0.0.0.0"))
    port = int(args.port if args.port is not None else config.get("port", 8765))
    stale_seconds = float(
        args.stale_seconds if args.stale_seconds is not None else config.get("stale_seconds", 15)
    )
    poll_ms = int(args.poll_ms if args.poll_ms is not None else config.get("poll_ms", 500))
    if poll_ms < 100:
        poll_ms = 100
    if poll_ms > 10000:
        poll_ms = 10000

    reader = TelemetryReader(log_path, stale_seconds, source, telemetry_path)
    reader.candidates = candidates

    # --- Steuerkanal ------------------------------------------------------
    control_enabled = bool(args.enable_control) or bool(config.get("control_enabled", False))
    explicit_command = args.command_file or config.get("command_file") or ""
    command_path = (
        expand_path(explicit_command) if explicit_command
        else log_path.parent / COMMAND_FILE_RELATIVE
    )
    command_timeout = float(
        args.command_timeout if args.command_timeout is not None
        else config.get("command_timeout_seconds", 5)
    )
    command_interval = int(
        args.command_min_interval_ms if args.command_min_interval_ms is not None
        else config.get("command_min_interval_ms", 500)
    )
    controller = CommandController(
        command_path, control_enabled, command_timeout, command_interval
    )
    if control_enabled:
        controller.cleanup()

    # --- Datenbank (SQLite) ----------------------------------------------
    database_value = args.database or config.get("database", "") or ""
    database_path = expand_path(database_value) if database_value else DEFAULT_DATABASE
    store = storage.HistoryStore(
        database_path,
        enabled=bool(config.get("history_enabled", True)) and not args.no_history,
        retention_days=float(config.get("history_retention_days", 7)),
        max_rows=int(config.get("history_max_rows", 500000)),
        min_interval_ms=int(config.get("history_min_interval_ms", 500)),
        max_interval_ms=int(config.get("history_max_interval_ms", 5000)),
    )
    controller.recorder = store.record_command
    controller.confirm_recorder = store.set_command_confirmation
    perf = PerformanceMonitor()
    sampler = TelemetrySampler(reader, controller, store, poll_ms / 1000.0, perf)

    print("LS25 Tractor Cockpit - lokaler Telemetrie-Server (nur lesend)")
    print(f"Konfiguration: {config_path}")
    print(f"Logdatei:      {log_path}  [{source}]")
    print(f"Telemetriedatei: {reader.telemetry_path}")
    if store.enabled:
        print(f"Datenbank:     {store.path}  ({store.rows} Messungen gespeichert)")
    else:
        print("Datenbank:     aus (Verlauf wird nicht gespeichert)")
    if not log_path.is_file():
        print("  -> Logdatei noch nicht vorhanden. Geprueft wurden:")
        for candidate in candidates[:6]:
            print(f"     - {candidate}")
    if not reader.telemetry_path.is_file():
        print("  -> Telemetriedatei noch nicht vorhanden (Mod schreibt sie im Spiel).")
    print("  Hinweis: der Server nutzt automatisch den Kanal mit den frischesten Daten.")

    if control_enabled:
        print("")
        print("!!! STEUERUNG AKTIV - es werden Fahrzeugbefehle an das Spiel gesendet !!!")
        print(f"    Erlaubt sind nur: {', '.join(sorted(CONTROL_ACTIONS))}")
        print(f"    Ein Befehl nach dem anderen, nur von Geraeten, die es unterstuetzen.")
        print(f"    Befehlsdatei: {command_path}")
        print("    Vorher Spielstand sichern:  powershell -ExecutionPolicy Bypass -File tools\\backup_savegame.ps1")
    else:
        print("Steuerung:     deaktiviert (nur Anzeige). Einschalten mit --enable-control")

    try:
        httpd = build_server(host, port, reader, poll_ms, controller, store, sampler)
    except OSError as exc:
        print(f"[Fehler] Port {port} auf {host} kann nicht belegt werden: {exc}")
        print("         Anderen Port waehlen: python server.py --port 8766")
        return 1

    real_port = httpd.server_address[1]
    print(f"Laptop:        http://127.0.0.1:{real_port}")
    print(f"Tablet:        http://{get_lan_ip()}:{real_port}")
    print(f"Dashboard fragt alle {poll_ms} ms ab, veraltet ab {stale_seconds:.0f} s ohne neue Zeile")
    print("Nur im vertrauenswuerdigen privaten WLAN verwenden. Beenden: Ctrl+C")

    try:
        sampler.start()
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer wird beendet.")
    finally:
        sampler.stop()
        httpd.server_close()
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
