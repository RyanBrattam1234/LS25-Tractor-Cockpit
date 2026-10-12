#!/usr/bin/env python3
"""Offline-Selbsttest fuer server/server.py.

Laeuft ohne Farming Simulator: erzeugt synthetische Logdateien im echten
LS25-Format und prueft Parser, Veraltet-Erkennung, inkrementelles Lesen und
die HTTP-Endpunkte.

Aufruf:
    python tools/selftest.py
"""
from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import re
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
WORKSPACE = SERVER_DIR.parent
sys.path.insert(0, str(SERVER_DIR))

import server  # noqa: E402  (Pfad muss vorher gesetzt sein)
import storage  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str) -> None:
    print(f"[{'OK  ' if condition else 'FEHL'}] {label}")
    if not condition:
        FAILURES.append(label)


def log_line(fields: str, seconds_ago: float = 0.0) -> str:
    """Zeile im Format der Spiel-Logdatei: <Zeitstempel> TRACTORCOCKPIT|..."""
    moment = datetime.now() - timedelta(seconds=seconds_ago)
    stamp = moment.strftime("%Y-%m-%d %H:%M:%S.") + f"{moment.microsecond // 1000:03d}"
    return f"{stamp} TRACTORCOCKPIT|{fields}"


VEHICLE_FIELDS = (
    "state=vehicle|name=SKH 60|speed=14.27|rpm=1799.96|rpmMin=850.00|rpmMax=2200.00"
    "|load=42.00|fuel=68.00|fuelCapacity=75.00|fuelType=diesel|engine=1|hours=12.35"
    "|x=58.35|y=29.79|z=-715.14"
    "|implCount=2"
    "|impl1Name=Krampe Big Body 750|impl1Type=trailer|impl1Lowered=1|impl1Unfolded=0|impl1On=n/a"
    "|impl1Working=0|impl1Fill=12345.00|impl1FillCapacity=20000.00|impl1FillType=Weizen"
    "|impl1CanLower=1|impl1CanFold=0|impl1CanOn=0"
    "|impl2Name=Krone Comprima|impl2Lowered=0|impl2Unfolded=1|impl2On=1|impl2Working=1"
    "|impl2CanLower=0|impl2CanFold=1|impl2CanOn=1"
    "|canToggleLight=1|lightCan=default,highBeam,workFront|lightOn=default,workFront"
    "|beaconCan=1|beacon=0|turnCan=1|turnLight=off"
)
FOOT_FIELDS = (
    "state=foot|name=No vehicle|speed=n/a|rpm=n/a|rpmMin=n/a|rpmMax=n/a|load=n/a"
    "|fuel=n/a|fuelCapacity=n/a|fuelType=n/a|engine=n/a|hours=n/a|x=n/a|y=n/a|z=n/a"
)


def make_reader(path: Path, stale_seconds: float = 15.0, telemetry_path: Path | None = None) -> server.TelemetryReader:
    return server.TelemetryReader(path, stale_seconds, "test", telemetry_path)


# ---------------------------------------------------------------------------
# 1. Parser
# ---------------------------------------------------------------------------
print("== 1. Parser ==")
fields = server.parse_fields("2026-10-11 13:28:47.504 TRACTORCOCKPIT|" + VEHICLE_FIELDS)
check(fields is not None, "TRACTORCOCKPIT-Zeile wird erkannt")
reading = server.build_reading(fields or {}, 1_700_000_000.0)
check(reading["vehicle"] == "SKH 60", "Fahrzeugname wird gelesen")
check(abs((reading["speed"] or 0) - 14.27) < 0.001, "Geschwindigkeit wird als Zahl gelesen")
check(reading["engine"] is True, "engine=1 wird zu True")
check(reading["state"] == "vehicle", "state=vehicle wird erkannt")
check(abs((reading["hours"] or 0) - 12.35) < 0.001, "Betriebsstunden werden gelesen")
check(reading["fuelType"] == "diesel", "Kraftstoffart wird gelesen")
check(server.parse_fields("irgendeine andere Zeile") is None, "Fremde Logzeilen werden ignoriert")
check(
    server.parse_timestamp("2026-10-11 13:28:47.504 TRACTORCOCKPIT|x=1") is not None,
    "Log-Zeitstempel wird als Zeit erkannt",
)
check(server.to_number("n/a") is None, "'n/a' wird zu None")
check(server.to_number("14.27") == 14.27, "Zahlen werden geparst")

print("\n== 2. Fuss-Heartbeat ==")
foot_reading = server.build_reading(
    server.parse_fields("TRACTORCOCKPIT|" + FOOT_FIELDS) or {}, 1_700_000_000.0
)
check(foot_reading["state"] == "foot", "state=foot wird erkannt")
check(foot_reading["vehicle"] is None, "Ohne Fahrzeug bleibt der Name leer")
check(server.parse_fields("TRACTORCOCKPIT|" + FOOT_FIELDS) is not None,
      "Fuss-Heartbeat wird als Zeile erkannt")

print("\n== 2b. Anbaugeraete (Phase 3) ==")
implements = reading["implements"]
check(len(implements) == 2, "Beide Anbaugeraete werden gelesen")
if len(implements) == 2:
    first, second = implements
    check(first["name"] == "Krampe Big Body 750", "Name des ersten Geraets")
    check(first["type"] == "trailer", "Geraetetyp wird gelesen")
    check(first["lowered"] is True, "gesenkt wird als True gelesen")
    check(first["unfolded"] is False, "aufgeklappt wird als False gelesen")
    check(first["on"] is None, "'n/a' bei eingeschaltet bleibt None")
    check(first["working"] is False, "arbeitet wird als False gelesen")
    check(abs((first["fill"] or 0) - 12345.0) < 0.01, "Fuellstand des Geraets")
    check(abs((first["fillCapacity"] or 0) - 20000.0) < 0.01, "Kapazitaet des Geraets")
    check(first["fillType"] == "Weizen", "Fuellart des Geraets")
    check(second["working"] is True, "arbeitendes Geraet erkannt")
    check(second["fill"] is None, "Fehlender Fuellstand bleibt None")
    check(first["canLower"] is True and first["canFold"] is False,
          "Faehigkeiten des ersten Geraets werden gelesen")
    check(second["canOn"] is True and second["canLower"] is False,
          "Faehigkeiten des zweiten Geraets werden gelesen")
check(server.parse_implements({"implCount": "0"}) == [], "Ohne Geraete leere Liste")
check(server.parse_implements({"implCount": "5"}) == [], "Kaputte Zaehlung ergibt leere Liste")
check(server.to_bool("n/a") is None, "to_bool: n/a -> None")
check(server.to_bool("1") is True and server.to_bool("0") is False, "to_bool: 1/0")
check(server.to_bool("quatsch") is None, "to_bool: unbekannt -> None")

print("\n== 2c. Licht, Blinker und Rundumleuchte ==")
lights = reading["lights"]
check(lights is not None, "Lichtdaten werden erkannt")
if lights:
    check(lights["canToggle"] is True, "Licht schaltbar (getCanToggleLight)")
    check("default" in lights["capabilities"] and "workFront" in lights["capabilities"],
          "Lichtarten werden gelesen")
    check("workBack" not in lights["capabilities"], "Nicht vorhandene Lichtart fehlt in den Faehigkeiten")
    check("beacon" in lights["capabilities"] and "turn" in lights["capabilities"],
          "Rundumleuchte und Blinker werden erkannt")
    check(lights["on"] == ["default", "workFront"], "Eingeschaltete Lichter werden gelesen")
    check(lights["beacon"] is False, "Rundumleuchte aus gelesen")
    check(lights["turnLight"] == "off", "Blinkerzustand gelesen")
check(server.parse_lights({}) is None, "Ohne Lichtdaten gibt es kein Lichtpaket")
check(server.parse_lights({"canToggleLight": "0"})["capabilities"] == [],
      "Nicht schaltbares Licht hat keine Faehigkeiten")

# ---------------------------------------------------------------------------
# 3. Datei-Szenarien
# ---------------------------------------------------------------------------
# Testdateien liegen im Projektordner, damit der Test auch in einer
# eingeschraenkten Umgebung (kein Schreibrecht im System-Temp) laeuft.
class WorkDir:
    """Wie tempfile.TemporaryDirectory, aber ohne restriktive Ordnerrechte.

    tempfile legt Ordner mit Modus 0700 an. Unter einer eingeschraenkten
    Windows-Sandbox kann das dazu fuehren, dass der Ordner danach nicht mehr
    beschreibbar ist. Deshalb hier ein normaler Ordner mit Aufraeumen.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> str:
        return str(self.path)

    def __exit__(self, *exc_info) -> bool:
        shutil.rmtree(self.path, ignore_errors=True)
        return False


with WorkDir(WORKSPACE / "selftest_tmp") as tmp:
    tmp_path = Path(tmp)

    print("\n== 3. Fehlende Logdatei ==")
    missing = make_reader(tmp_path / "gibtsnicht.txt")
    payload = missing.poll()
    check(payload["connected"] is False, "Fehlende Datei -> nicht verbunden")
    check(payload["logExists"] is False, "logExists ist False")
    check("Logdatei" in payload["message"] and "gefunden" in payload["message"],
          "Meldung erklaert die fehlende Logdatei")

    print("\n== 4. Frische Telemetrie ==")
    log_file = tmp_path / "log.txt"
    log_file.write_text(log_line(VEHICLE_FIELDS) + "\n", encoding="utf-8")
    reader = make_reader(log_file)
    payload = reader.poll()
    check(payload["connected"] is True, "Frische Zeile -> verbunden")
    check(payload["stale"] is False, "Frische Zeile -> nicht veraltet")
    check(payload["vehicle"] == "SKH 60", "Fahrzeugname kommt im JSON an")
    check(payload["ageSeconds"] is not None and payload["ageSeconds"] < 5, "Alter der Daten ist klein")
    check(payload["modSeen"] is True, "modSeen ist True")

    print("\n== 5. Veraltete Telemetrie (Spiel beendet) ==")
    stale_file = tmp_path / "stale.txt"
    stale_file.write_text(log_line(VEHICLE_FIELDS, seconds_ago=600) + "\n", encoding="utf-8")
    payload = make_reader(stale_file, stale_seconds=15).poll()
    check(payload["connected"] is False, "Alte Zeile -> nicht verbunden")
    check(payload["stale"] is True, "Alte Zeile -> stale")
    check(payload["vehicle"] == "SKH 60", "Letzte bekannte Werte bleiben sichtbar")
    check("Keine frischen Daten" in payload["message"], "Meldung weist auf veraltete Daten hin")

    print("\n== 6. Inkrementelles Lesen ==")
    growing = tmp_path / "growing.txt"
    growing.write_text(log_line(VEHICLE_FIELDS) + "\n", encoding="utf-8")
    reader = make_reader(growing)
    check(reader.poll()["vehicle"] == "SKH 60", "Erste Zeile wird gelesen")

    # Zweite Zeile ohne abschliessenden Zeilenumbruch: darf nicht verloren gehen.
    partial_fields = VEHICLE_FIELDS.replace("name=SKH 60", "name=Neuer Traktor")
    with growing.open("a", encoding="utf-8") as handle:
        handle.write(log_line(partial_fields))
    time.sleep(0.05)
    check(reader.poll()["vehicle"] == "SKH 60", "Unvollstaendige Zeile wird noch nicht ausgewertet")

    with growing.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    time.sleep(0.05)
    check(reader.poll()["vehicle"] == "Neuer Traktor", "Nach dem Zeilenumbruch greift die neue Zeile")

    print("\n== 7. Neue Sitzung / rotierte Datei ==")
    growing.write_text(log_line(FOOT_FIELDS) + "\n", encoding="utf-8")
    payload = reader.poll()
    check(payload["state"] == "foot", "Kleinere Datei wird als neue Sitzung erkannt")
    check(payload["vehicle"] is None, "Ohne Fahrzeug kein Fahrzeugname")

    print("\n== 7b. Datei-Kanal (schneller Transport, Phase 2) ==")
    profile = tmp_path / "profile"
    (profile / "modSettings").mkdir(parents=True, exist_ok=True)
    telemetry_file = profile / "modSettings" / "FS25_TractorCockpit.txt"
    quiet_log = tmp_path / "quiet.txt"
    quiet_log.write_text("", encoding="utf-8")

    telemetry_file.write_text(log_line(VEHICLE_FIELDS) + "\n", encoding="utf-8")
    payload = make_reader(quiet_log, telemetry_path=telemetry_file).poll()
    check(payload["channel"] == "file", "Nur Datei-Kanal vorhanden -> Kanal 'file'")
    check(payload["connected"] is True, "Datei-Kanal liefert frische Daten")
    check(payload["vehicle"] == "SKH 60", "Werte kommen aus der Telemetriedatei")
    check(len(payload["implements"]) == 2, "Anbaugeraete kommen aus der Telemetriedatei")
    check(payload["telemetryFileExists"] is True, "Telemetriedatei wird als vorhanden gemeldet")

    # Datei frischer als die Logzeile -> Datei gewinnt
    quiet_log.write_text(log_line(FOOT_FIELDS, seconds_ago=30) + "\n", encoding="utf-8")
    payload = make_reader(quiet_log, telemetry_path=telemetry_file).poll()
    check(payload["channel"] == "file", "Frischere Datei schlaegt aeltere Logzeile")

    # Logzeile frischer als die Datei -> Log gewinnt
    old_stamp = time.time() - 120
    os.utime(telemetry_file, (old_stamp, old_stamp))
    quiet_log.write_text(log_line(VEHICLE_FIELDS) + "\n", encoding="utf-8")
    payload = make_reader(quiet_log, telemetry_path=telemetry_file).poll()
    check(payload["channel"] == "log", "Frischere Logzeile schlaegt aeltere Datei")

    # Datei veraltet und kein Log -> veraltet, aber Werte bleiben sichtbar
    empty_log = tmp_path / "empty.txt"
    empty_log.write_text("", encoding="utf-8")
    payload = make_reader(empty_log, telemetry_path=telemetry_file).poll()
    check(payload["connected"] is False and payload["stale"] is True,
          "Alte Telemetriedatei gilt als veraltet")
    check(payload["telemetryFileExists"] is True, "Vorhandene, aber alte Datei wird erkannt")
    check("Keine frischen Daten" in payload["message"], "Meldung weist auf alte Datei hin")

    print("\n== 7c. Rotation: neue Datei am selben Pfad ==")
    rotating = tmp_path / "rotating.txt"
    rotating.write_text(log_line(VEHICLE_FIELDS.replace("SKH 60", "Alt")) + "\n", encoding="utf-8")
    reader = make_reader(rotating, telemetry_path=tmp_path / "keine-datei.txt")
    check(reader.poll()["vehicle"] == "Alt", "Erste Datei wird gelesen")

    # Datei ersetzen und dabei groesser machen: ohne Identitaetspruefung wuerde
    # der Leser am alten Offset weiterlesen und nichts finden.
    rotating.unlink()
    rotating.write_text(
        log_line(VEHICLE_FIELDS.replace("SKH 60", "Neu")) + "|implCount=1|impl1Name=Testgeraet\n",
        encoding="utf-8",
    )
    payload = reader.poll()
    check(payload["vehicle"] == "Neu", "Neue Datei wird trotz groesserer Groesse erkannt")
    check(len(payload["implements"]) == 1, "Neue Datei liefert auch die Geraetedaten")

    print("\n== 7d. Leistungsmesswerte ==")
    metrics_log = tmp_path / "metrics.txt"
    metrics_log.write_text(log_line(VEHICLE_FIELDS) + "\n" * 20, encoding="utf-8")
    payload = make_reader(metrics_log, telemetry_path=tmp_path / "keine-datei.txt").poll()
    check(payload["logSizeBytes"] and payload["logSizeBytes"] > 0, "Loggroesse wird gemeldet")
    check("logGrowthPerHour" in payload, "Wachstumsmessung ist im Messpaket enthalten")
    check(isinstance(payload["staleSeconds"], float), "Veraltetschwelle wird mitgeliefert")

    print("\n== 8. HTTP-Endpunkte ==")
    http_file = tmp_path / "http.txt"
    http_file.write_text(log_line(VEHICLE_FIELDS) + "\n", encoding="utf-8")
    httpd = server.build_server("127.0.0.1", 0, make_reader(http_file))
    # Request-Logging des Testservers abschalten, sonst erscheinen die Zeilen
    # in PowerShell als rote Fehlermeldungen.
    httpd.RequestHandlerClass.log_message = lambda *args, **kwargs: None
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        with urllib.request.urlopen(base + "/api/telemetry", timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
            check(response.status == 200, "API antwortet mit 200")
            check(body["vehicle"] == "SKH 60", "API liefert die Telemetrie")
            check(body["channel"] == "log", "API nennt den benutzten Kanal")
            check(body["pollMs"] == 500, "API gibt den Abfragetakt vor")
            check(len(body["implements"]) == 2, "API liefert die Anbaugeraete")
            check("no-store" in response.headers.get("Cache-Control", ""), "API ist nicht cachebar")

        with urllib.request.urlopen(base + "/", timeout=5) as response:
            html = response.read().decode("utf-8")
            check(response.status == 200, "Dashboard wird ausgeliefert")
            check("Tractor Cockpit" in html, "HTML enthaelt das Dashboard")
            check("no-store" in response.headers.get("Cache-Control", ""), "HTML ist nicht cachebar")

        with urllib.request.urlopen(base + "/app.js", timeout=5) as response:
            check("application/javascript" in response.headers.get("Content-Type", ""),
                  "app.js hat den richtigen Content-Type")

        for path in ("/..%2fserver%2fserver.py", "/%2e%2e/server/server.py", "/../config/settings.json"):
            try:
                with urllib.request.urlopen(base + path, timeout=5) as response:
                    check(False, f"Pfadausbruch blockiert ({path}) - Status {response.status}")
            except urllib.error.HTTPError as error:
                check(error.code in (403, 404), f"Pfadausbruch blockiert ({path}) - Status {error.code}")

        try:
            urllib.request.urlopen(base + "/gibtsnicht.html", timeout=5)
            check(False, "Unbekannte Datei liefert 404")
        except urllib.error.HTTPError as error:
            check(error.code == 404, "Unbekannte Datei liefert 404")

        # Abgebrochene Verbindungen (Tablet schlaeft ein, Browser schliesst)
        # duerfen keinen Traceback im Serverfenster erzeugen.
        noise = io.StringIO()
        with contextlib.redirect_stderr(noise):
            for payload in (b"", b"GET /api/telemetry HTTP/1.1\r\nHost: lokal\r\n"):
                raw = socket.create_connection(("127.0.0.1", port), timeout=5)
                if payload:
                    raw.sendall(payload)
                raw.close()
            time.sleep(0.5)
            with urllib.request.urlopen(base + "/api/telemetry", timeout=5) as response:
                check(response.status == 200, "Server laeuft nach abgebrochenen Verbindungen weiter")
            time.sleep(0.3)

        messages = noise.getvalue()
        check(
            "Exception occurred during processing of request" not in messages,
            "Abgebrochene Verbindungen erzeugen keinen Traceback",
        )
    finally:
        httpd.shutdown()
        httpd.server_close()

    print("\n== 8b. HTTP mit Datei-Kanal ==")
    file_only_log = tmp_path / "fileonly.txt"
    file_only_log.write_text("", encoding="utf-8")
    telemetry_file.write_text(log_line(VEHICLE_FIELDS) + "\n", encoding="utf-8")
    httpd2 = server.build_server(
        "127.0.0.1", 0, make_reader(file_only_log, telemetry_path=telemetry_file), poll_ms=250
    )
    httpd2.RequestHandlerClass.log_message = lambda *args, **kwargs: None
    port2 = httpd2.server_address[1]
    threading.Thread(target=httpd2.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port2}/api/telemetry", timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
            check(body["channel"] == "file", "Laufender Server nutzt den Datei-Kanal")
            check(body["connected"] is True, "Datei-Kanal gilt als live")
            check(body["pollMs"] == 250, "Server gibt seinen eigenen Abfragetakt weiter")
            check(len(body["implements"]) == 2, "Geraete kommen ueber HTTP aus der Datei")
            check(body["telemetryFileExists"] is True, "HTTP meldet die vorhandene Telemetriedatei")
    finally:
        httpd2.shutdown()
        httpd2.server_close()

    # -----------------------------------------------------------------------
    print("\n== 8c. Steuerkanal: Sicherheitsgrenzen (Phase 4) ==")
    command_dir = tmp_path / "profil2" / "modSettings"
    command_dir.mkdir(parents=True, exist_ok=True)
    command_file = command_dir / "FS25_TractorCockpit_cmd.xml"

    def fresh_reading(fields_text=VEHICLE_FIELDS, command_path=None):
        fields = server.parse_fields("TRACTORCOCKPIT|" + fields_text)
        data = server.build_reading(fields, time.time())
        data["connected"] = True
        # Die Mod meldet, dass sie die Befehlsdatei ueber die XML-API lesen kann.
        data["cmdMode"] = "xml"
        data["cmdPath"] = str(command_path or command_file)
        return data

    def lights_reading(on="default,workFront", beacon="0", turn="off", command_path=None):
        text = VEHICLE_FIELDS
        text = re.sub(r"\|lightOn=[^|]*", "|lightOn=" + on, text)
        text = re.sub(r"\|beacon=[^|]*", "|beacon=" + beacon, text)
        text = re.sub(r"\|turnLight=[^|]*", "|turnLight=" + turn, text)
        return fresh_reading(text, command_path)

    reading = fresh_reading()

    disabled = server.CommandController(command_file, enabled=False)
    status, body = disabled.submit("lower", 1, reading, "test")
    check(status == 403 and body["ok"] is False, "Ohne Freigabe kein Befehl (403)")
    check(not command_file.exists(), "Ohne Freigabe wird keine Befehlsdatei geschrieben")

    control = server.CommandController(command_file, enabled=True)
    check(control.submit("selbstzerstoerung", 1, reading, "test")[0] == 400,
          "Aktion ausserhalb der Whitelist wird abgelehnt (400)")
    check(control.submit("lower", 99, reading, "test")[0] == 400,
          "Ungueltiger Geraeteplatz wird abgelehnt (400)")
    check(control.submit("lower", 3, reading, "test")[0] == 404,
          "Nicht angehaengtes Geraet wird abgelehnt (404)")
    check(control.submit("lower", 2, reading, "test")[0] == 409,
          "Nicht unterstuetzte Aktion wird abgelehnt (409)")
    check(control.submit("locker", 1, reading, "test")[0] == 400,
          "Aehnlicher Aktionsname wird nicht durchgelassen")

    no_channel = dict(reading)
    no_channel["cmdMode"] = "none"
    status, body = control.submit("lower", 1, no_channel, "test")
    check(status == 409 and "Steuerkanal" in body["error"],
          "Ohne lesbaren Steuerkanal wird nichts gesendet (FS25 erlaubt Mods nur Schreiben)")

    stale_reading = dict(reading)
    stale_reading["connected"] = False
    check(control.submit("lower", 1, stale_reading, "test")[0] == 409,
          "Bei veralteten Spieldaten wird abgelehnt (409)")
    check(not command_file.exists(), "Bis hierhin wurde nichts geschrieben")

    status, body = control.submit("lower", 1, reading, "test")
    check(status == 200 and body["ok"] is True, "Gueltiger Befehl wird angenommen")
    written = command_file.read_text(encoding="utf-8")
    check('id="1"' in written and 'action="lower"' in written and 'target="1"' in written,
          "Befehlsdatei enthaelt den Befehl als XML")
    check("Krampe Big Body 750" in written, "Geraetename steht in der XML-Datei")
    check(not (command_dir / "FS25_TractorCockpit_cmd.xml.tmp").exists(),
          "Es bleibt keine Nebendatei liegen")
    check(control.submit("turnOn", 2, reading, "test")[0] == 409,
          "Zweiter Befehl waehrend eines laufenden wird abgelehnt")

    print("\n== 8d. Steuerkanal: Rueckmeldung und Bestaetigung ==")
    answered = fresh_reading(VEHICLE_FIELDS + "|cmdId=1|cmdResult=ok|cmdMsg=Senken ausgeloest")
    control.update(answered)
    snapshot = control.snapshot(answered)
    check(snapshot["pending"] is None, "Rueckmeldung beendet den offenen Befehl")
    check(snapshot["last"]["status"] == "ok", "Status kommt aus der Telemetrie")
    check(snapshot["last"]["confirmed"] is True,
          "Zustand bestaetigt: lowered=true im echten Spielzustand")
    check(snapshot["last"]["confirmField"] == "lowered", "Bestaetigungsfeld wird mitgeliefert")
    check(len(snapshot["log"]) >= 2, "Audit-Liste enthaelt die Befehle")

    # Erwartung trifft nicht ein -> ausdruecklich nicht bestaetigt
    raising = server.CommandController(command_file, enabled=True)
    raise_reading = fresh_reading(VEHICLE_FIELDS + "|cmdId=1|cmdResult=ok|cmdMsg=Heben ausgeloest")
    raising.submit("raise", 1, raise_reading, "test")
    raising.update(raise_reading)
    snapshot = raising.snapshot(raise_reading)
    check(snapshot["last"]["confirmed"] is False,
          "Ausbleibende Zustandsaenderung wird als unbestätigt gemeldet")

    # Keine Rueckmeldung -> Timeout, Kanal wird wieder frei
    timing_out = server.CommandController(command_file, enabled=True, timeout_seconds=1.0)
    timing_out.submit("turnOn", 2, reading, "test")
    time.sleep(1.1)
    snapshot = timing_out.snapshot(reading)
    check(snapshot["last"]["status"] == "timeout", "Ohne Rueckmeldung gibt es einen Timeout")
    check(snapshot["pending"] is None, "Nach dem Timeout ist der Kanal wieder frei")

    # Mindestabstand (Timeout ist intern auf mindestens 1 s begrenzt)
    throttled = server.CommandController(
        command_file, enabled=True, min_interval_ms=5000, timeout_seconds=0.2
    )
    throttle_reading = fresh_reading(VEHICLE_FIELDS + "|cmdId=1|cmdResult=ok")
    throttled.submit("lower", 1, throttle_reading, "test")
    time.sleep(1.1)
    check(throttled.submit("raise", 1, throttle_reading, "test")[0] == 429,
          "Mindestabstand zwischen Befehlen wird eingehalten (429)")

    # Trennzeichen und Sonderzeichen muessen XML-sicher werden
    tricky = server.CommandController(command_file, enabled=True)
    tricky_reading = fresh_reading(VEHICLE_FIELDS.replace("Krampe Big Body 750", 'Marke & "Modell"'))
    tricky.submit("lower", 1, tricky_reading, "test")
    written = command_file.read_text(encoding="utf-8")
    check("Marke &amp; &quot;Modell&quot;" in written,
          "Sonderzeichen im Geraetenamen werden fuer XML entschaerft")

    print("\n== 8e. Steuerkanal ueber HTTP ==")
    post_log = tmp_path / "post.txt"
    post_log.write_text(
        log_line(VEHICLE_FIELDS + "|cmdMode=xml|cmdPath=" + str(command_file)) + "\n",
        encoding="utf-8",
    )
    http_control = server.CommandController(command_file, enabled=True, min_interval_ms=0)
    httpd3 = server.build_server(
        "127.0.0.1", 0, make_reader(post_log, telemetry_path=tmp_path / "leer9.txt"),
        poll_ms=500, controller=http_control,
    )
    httpd3.RequestHandlerClass.log_message = lambda *args, **kwargs: None
    port3 = httpd3.server_address[1]
    threading.Thread(target=httpd3.serve_forever, daemon=True).start()
    base3 = f"http://127.0.0.1:{port3}"

    def post_control(payload, headers=None):
        request = urllib.request.Request(
            base3 + "/api/control",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers or {"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    try:
        status, body = post_control({"action": "lower", "target": 1},
                                    {"Content-Type": "application/json",
                                     "Origin": "http://boese.example"})
        check(status == 403, "Befehl aus fremder Webseite wird abgelehnt (Origin)")

        status, _ = post_control({"action": "lower", "target": 1},
                                 {"Content-Type": "text/plain"})
        check(status == 415, "Falscher Content-Type wird abgelehnt (415)")

        status, body = post_control({"action": "lower", "target": 1},
                                    {"Content-Type": "application/json",
                                     "Origin": f"http://127.0.0.1:{port3}"})
        check(status == 200 and body["ok"] is True, "Befehl per HTTP wird angenommen")
        check(body["control"]["pending"] is not None, "HTTP-Antwort enthaelt den laufenden Befehl")

        status, body = post_control({"action": "lower", "target": 1})
        check(status == 409, "Zweiter Befehl per HTTP wird abgelehnt (eine Aktion zur Zeit)")

        with urllib.request.urlopen(base3 + "/api/telemetry", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            check(payload["control"]["enabled"] is True, "Telemetrie meldet die Freigabe")
            check(payload["control"]["configured"] is True, "Telemetrie meldet die Befehlsdatei")
            check(payload["control"]["actionCount"] == len(server.CONTROL_ACTIONS),
                  "Telemetrie nennt alle erlaubten Aktionen")
    finally:
        httpd3.shutdown()
        httpd3.server_close()

    # Regression: abgelehnte POSTs duerfen die naechste Anfrage auf derselben
    # HTTP/1.1-Verbindung nicht zerstoeren (Body muss gelesen werden).
    print("\n== 8f. Steuerkanal: mehrere Anfragen auf einer Verbindung ==")
    conn_log = tmp_path / "conn.txt"
    conn_log.write_text(
        log_line(VEHICLE_FIELDS + "|cmdMode=xml|cmdPath=" + str(command_file)) + "\n",
        encoding="utf-8",
    )
    conn_control = server.CommandController(command_file, enabled=True, min_interval_ms=0)
    httpd4 = server.build_server(
        "127.0.0.1", 0, make_reader(conn_log, telemetry_path=tmp_path / "leer10.txt"),
        poll_ms=500, controller=conn_control,
    )
    httpd4.RequestHandlerClass.log_message = lambda *args, **kwargs: None
    port4 = httpd4.server_address[1]
    threading.Thread(target=httpd4.serve_forever, daemon=True).start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port4, timeout=5)
        payload = json.dumps({"action": "lower", "target": 1})

        def send(body, headers):
            connection.request("POST", "/api/control", body=body, headers=headers)
            answer = connection.getresponse()
            answer.read()
            return answer.status

        statuses = [
            send(payload, {"Content-Type": "application/json", "Origin": "http://boese.example"}),
            send(payload, {"Content-Type": "application/json"}),
            send(json.dumps({"action": "unfug", "target": 1}), {"Content-Type": "application/json"}),
            send(payload, {"Content-Type": "application/json"}),
        ]
        check(statuses == [403, 200, 400, 409],
              f"Vier Anfragen auf einer Verbindung werden korrekt beantwortet ({statuses})")
        connection.close()
    finally:
        httpd4.shutdown()
        httpd4.server_close()

    # -----------------------------------------------------------------------
    print("\n== 8g. Steuerkanal: nicht schreibbarer Pfad ==")
    blocked_dir = tmp_path / "blockiert" / "modSettings"
    blocked_dir.mkdir(parents=True, exist_ok=True)
    # Ein Verzeichnis mit dem Dateinamen laesst sich nicht als Datei oeffnen -
    # das erzwingt denselben Fehlerpfad wie eine gesperrte Datei.
    blocked_path = blocked_dir / "FS25_TractorCockpit_cmd.xml"
    blocked_path.mkdir()
    blocked_control = server.CommandController(command_file, enabled=True, min_interval_ms=0)
    blocked_reading = fresh_reading(command_path=blocked_path)
    status, body = blocked_control.submit("lower", 1, blocked_reading, "test")
    check(status == 500, "Nicht schreibbarer Pfad fuehrt zu einem klaren Fehler (500)")
    check("schreibbar" in body["error"], "Fehlermeldung nennt das Schreibproblem")
    check(blocked_control.write_errors == 1, "Schreibfehler wird gezaehlt")

    print("\n== 8h. Steuerkanal: Licht, Blinker, Rundumleuchte ==")
    light_control = server.CommandController(command_file, enabled=True, min_interval_ms=0)

    check(light_control.submit("lightHighBeamOn", 1, lights_reading(), "test")[0] == 400,
          "Lichtbefehl mit Geraeteplatz wird abgelehnt (400)")
    check(light_control.submit("lower", 0, lights_reading(), "test")[0] == 400,
          "Geraetebefehl ohne Geraet wird abgelehnt (400)")

    status, body = light_control.submit("lightHighBeamOn", 0, lights_reading(), "test")
    check(status == 200, "Fernlicht einschalten wird angenommen")
    check(body["command"]["scope"] == "vehicle", "Befehl gilt fuer das Fahrzeug")

    not_allowed = lights_reading()
    not_allowed["lights"] = dict(not_allowed["lights"])
    not_allowed["lights"]["capabilities"] = ["default", "toggle"]
    check(light_control.submit("lightHighBeamOn", 0, not_allowed, "test")[0] == 409,
          "Nicht vorhandene Lichtart wird abgelehnt (409)")

    dark = lights_reading()
    dark["lights"] = dict(dark["lights"])
    dark["lights"]["canToggle"] = False
    check(light_control.submit("beaconOn", 0, dark, "test")[0] == 409,
          "Ohne Schaltfreigabe des Spiels wird abgelehnt (409)")

    # Rueckmeldung und Bestaetigung aus dem echten Zustand
    answered = lights_reading(on="default,highBeam,workFront")
    answered["cmdId"] = 1.0
    answered["cmdResult"] = "ok"
    answered["cmdMsg"] = "eingeschaltet"
    light_control.update(answered)
    snapshot = light_control.snapshot(answered)
    check(snapshot["last"]["status"] == "ok", "Lichtbefehl wird quittiert")
    check(snapshot["last"]["confirmed"] is True, "Fernlicht an wird aus dem Spielzustand bestaetigt")

    still_dark = lights_reading()
    still_dark["cmdId"] = 1.0
    still_dark["cmdResult"] = "ok"
    snapshot = light_control.snapshot(still_dark)
    check(snapshot["last"]["confirmed"] is False,
          "Ausbleibende Lichtaenderung wird als unbestätigt gemeldet")

    blink = server.CommandController(command_file, enabled=True, min_interval_ms=0)
    blink.submit("turnLeft", 0, lights_reading(), "test")
    blinking = lights_reading(turn="left")
    blinking["cmdId"] = 1.0
    blinking["cmdResult"] = "ok"
    blink.update(blinking)
    check(blink.snapshot(blinking)["last"]["confirmed"] is True, "Blinker links wird bestaetigt")

    beacon = server.CommandController(command_file, enabled=True, min_interval_ms=0)
    beacon.submit("beaconOn", 0, lights_reading(), "test")
    lit = lights_reading(beacon="1")
    lit["cmdId"] = 1.0
    lit["cmdResult"] = "ok"
    beacon.update(lit)
    check(beacon.snapshot(lit)["last"]["confirmed"] is True, "Rundumleuchte an wird bestaetigt")

    cycle = server.CommandController(command_file, enabled=True, min_interval_ms=0)
    cycle.submit("lightNext", 0, lights_reading(), "test")
    cycled = lights_reading(on="default,highBeam")
    cycled["cmdId"] = 1.0
    cycled["cmdResult"] = "ok"
    cycle.update(cycled)
    check(cycle.snapshot(cycled)["last"]["confirmed"] is None,
          "Lichtstufe hat keine feste Erwartung (nur ausgeloest)")

    print("\n== 8i. Leistung: Messwerte, Zwischenspeicher, gzip ==")
    perf_control = server.CommandController(command_file, enabled=True, min_interval_ms=0)
    perf_store = storage.HistoryStore(
        tmp_path / "db_perf.sqlite3", min_interval_ms=0, max_interval_ms=0
    )
    perf_reader = make_reader(post_log, telemetry_path=tmp_path / "leer13.txt")
    perf_sampler = server.TelemetrySampler(perf_reader, perf_control, perf_store, 0.1)
    httpd6 = server.build_server(
        "127.0.0.1", 0, perf_reader, poll_ms=500, controller=perf_control,
        store=perf_store, sampler=perf_sampler,
    )
    httpd6.RequestHandlerClass.log_message = lambda *args, **kwargs: None
    port6 = httpd6.server_address[1]
    threading.Thread(target=httpd6.serve_forever, daemon=True).start()
    base6 = f"http://127.0.0.1:{port6}"
    try:
        perf_sampler.tick()
        with urllib.request.urlopen(base6 + "/api/telemetry", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            check("performance" in payload, "Messwerte sind im Messpaket")
            check(payload["performance"]["samplerTotal"]["avgMs"] >= 0,
                  "Erfassungszeit wird gemessen")
            check(payload["performance"]["counters"]["apiRequests"] >= 1,
                  "API-Anfragen werden gezaehlt")
            check(payload["lights"] is not None, "Lichtdaten stehen im Messpaket")
            check(payload["control"]["actionCount"] == len(server.CONTROL_ACTIONS),
                  "Telemetrie nennt alle erlaubten Aktionen")

        # Zweiter Abruf: die gemessene Antwortgroesse des ersten steht jetzt bereit.
        with urllib.request.urlopen(base6 + "/api/telemetry", timeout=5) as response:
            second = json.loads(response.read().decode("utf-8"))
        check(second["performance"].get("payloadBytes", 0) > 0,
              "Antwortgroesse wird gemessen")

        with urllib.request.urlopen(base6 + "/api/history?minutes=30&buckets=60", timeout=5) as response:
            first = json.loads(response.read().decode("utf-8"))
        with urllib.request.urlopen(base6 + "/api/history?minutes=30&buckets=60", timeout=5) as response:
            second = json.loads(response.read().decode("utf-8"))
        check(first["available"] is True and second["cached"] is True,
              "Verlauf kommt beim zweiten Abruf aus dem Zwischenspeicher")

        request = urllib.request.Request(base6 + "/api/telemetry", headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read()
            encoding = response.headers.get("Content-Encoding", "")
            body = json.loads(raw.decode("utf-8")) if encoding != "gzip" else json.loads(
                __import__("gzip").decompress(raw).decode("utf-8")
            )
            check(body["vehicle"] == "SKH 60", "Gepackte Antwort ist lesbar")
            check(encoding in ("gzip", ""), "Antwort nennt die Kodierung")
    finally:
        httpd6.shutdown()
        httpd6.server_close()
        perf_store.close()

    print("\n== 9. SQLite: Verlauf und Befehlsprotokoll ==")
    db_path = tmp_path / "db" / "history.sqlite3"
    store = storage.HistoryStore(db_path, retention_days=7, min_interval_ms=0, max_interval_ms=5000)
    check(store.available is True, "SQLite-Datenbank wird angelegt")
    check(db_path.is_file(), "Datenbankdatei existiert")

    base = fresh_reading()
    check(store.record(base) is True, "Erste Messung wird gespeichert")
    check(store.record(dict(base)) is False, "Gleiche Messung wird nicht doppelt gespeichert")
    changed = dict(base)
    changed["speed"] = 25.5
    check(store.record(changed) is True, "Geaenderte Messung wird gespeichert")

    series = store.series(30, 60)
    check(series["available"] is True and len(series["points"]) >= 1, "Verlauf liefert Punkte")
    check(series["summary"]["speedMax"] == 25.5, "Verlauf kennt die Hoechstgeschwindigkeit")
    check(series["summary"]["samples"] >= 2, "Verlauf zaehlt die Messungen")

    store.record_command(
        {
            "id": 1, "at": time.time(), "action": "lower", "target": 1, "name": "Testgeraet",
            "status": "ok", "message": "Senken ausgeloest", "confirmed": None, "client": "test",
        }
    )
    store.set_command_confirmation(1, True)
    stored = store.recent_commands(5)
    check(len(stored) == 1 and stored[0]["confirmed"] is True,
          "Befehlsprotokoll speichert Ergebnis und Bestaetigung")
    check(stored[0]["action"] == "lower" and stored[0]["name"] == "Testgeraet",
          "Befehlsprotokoll behaelt Aktion und Geraet")

    for index in range(5):
        item = dict(base)
        item["speed"] = 100 + index
        item["updatedAt"] = time.time() + index
        store.record(item)
    check(store.stats()["rows"] > 0, "Messungen sind gezaehlt")
    store._prune_locked(time.time() + 10 ** 6)
    check(store.stats()["rows"] == 0, "Alte Messungen werden nach der Aufbewahrung geloescht")
    store.close()

    print("\n== 9b. Hintergrund-Erfassung ==")
    sampler_log = tmp_path / "sampler.txt"
    sampler_log.write_text(log_line(VEHICLE_FIELDS) + "\n", encoding="utf-8")
    store2 = storage.HistoryStore(
        tmp_path / "db2.sqlite3", min_interval_ms=0, max_interval_ms=0
    )
    sampler = server.TelemetrySampler(
        make_reader(sampler_log, telemetry_path=tmp_path / "leer11.txt"),
        server.CommandController(None),
        store2,
        interval_seconds=0.05,
    )
    first = sampler.tick()
    check(first["vehicle"] == "SKH 60", "Erfassung liest die Telemetrie")
    check(store2.stats()["rows"] == 1, "Erfassung schreibt in die Datenbank")
    sampler.start()
    time.sleep(0.25)
    sampler.stop()
    check(sampler.cycles >= 2, "Erfassung laeuft im Hintergrund weiter")
    check(store2.stats()["rows"] >= 1, "Datenbank bleibt gefuellt")
    store2.close()

    print("\n== 9c. Verlauf und Protokoll ueber HTTP ==")
    http_log = tmp_path / "hist.txt"
    http_log.write_text(log_line(VEHICLE_FIELDS) + "\n", encoding="utf-8")
    http_store = storage.HistoryStore(
        tmp_path / "db3.sqlite3", min_interval_ms=0, max_interval_ms=0
    )
    http_reader = make_reader(http_log, telemetry_path=tmp_path / "leer12.txt")
    http_controller = server.CommandController(None, enabled=False)
    http_sampler = server.TelemetrySampler(http_reader, http_controller, http_store, 0.1)
    httpd5 = server.build_server(
        "127.0.0.1", 0, http_reader, poll_ms=500, controller=http_controller,
        store=http_store, sampler=http_sampler,
    )
    httpd5.RequestHandlerClass.log_message = lambda *args, **kwargs: None
    port5 = httpd5.server_address[1]
    threading.Thread(target=httpd5.serve_forever, daemon=True).start()
    base5 = f"http://127.0.0.1:{port5}"
    try:
        http_sampler.tick()
        with urllib.request.urlopen(base5 + "/api/history?minutes=30&buckets=60", timeout=5) as response:
            history = json.loads(response.read().decode("utf-8"))
            check(history["available"] is True, "HTTP: Verlauf ist abrufbar")
            check(len(history["points"]) >= 1, "HTTP: Verlauf enthaelt Punkte")

        http_store.record_command(
            {"id": 2, "at": time.time(), "action": "turnOn", "target": 2, "name": "Presse",
             "status": "ok", "message": "eingeschaltet", "confirmed": False, "client": "test"}
        )
        with urllib.request.urlopen(base5 + "/api/commands?limit=5", timeout=5) as response:
            commands = json.loads(response.read().decode("utf-8"))
            check(commands["available"] is True and len(commands["commands"]) == 1,
                  "HTTP: Befehlsprotokoll ist abrufbar")

        with urllib.request.urlopen(base5 + "/api/telemetry", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            check(payload["storage"]["available"] is True, "HTTP: Telemetrie nennt die Datenbank")
            check(payload["storage"]["rows"] >= 1, "HTTP: Datenbank meldet Zeilen")
    finally:
        httpd5.shutdown()
        httpd5.server_close()
        http_store.close()

# ---------------------------------------------------------------------------
# 9. Pfadsuche auf diesem PC
# ---------------------------------------------------------------------------
print("\n== 9. Pfadsuche auf diesem PC ==")
documents = server.documents_candidates()
print("     Kandidaten: " + ", ".join(str(path) for path in documents[:5]))
home = Path.home()
wrong = home / "Documents" / "My Games" / "FarmingSimulator2025" / "log.txt"
resolved, candidates, source = server.resolve_log_path("")
print(f"     Erkannt:    {resolved}  [{source}]")
check(resolved.name == "log.txt", "Es wird eine log.txt gewaehlt")
check(
    resolved.is_file() or not wrong.is_file(),
    "Bei lokalisiertem Dokumente-Ordner wird nicht der falsche Pfad gewaehlt",
)
if resolved.is_file():
    check(
        resolved.parent.name == "FarmingSimulator2025",
        "Der gefundene Pfad liegt im LS25-Spielordner",
    )

print("\n== 10. Kandidatenliste fuer Fehlermeldungen ==")
check(len(candidates) > 0, "Es gibt Kandidatenpfade fuer die Diagnose")
check(all(isinstance(str(path), str) for path in candidates), "Kandidaten sind Strings")

print("\n== 11. Dashboard: HTML und JavaScript passen zusammen ==")
html_path = server.WEB_ROOT / "index.html"
js_path = server.WEB_ROOT / "app.js"
css_path = server.WEB_ROOT / "style.css"

if html_path.is_file() and js_path.is_file():
    html = html_path.read_text(encoding="utf-8")
    javascript = js_path.read_text(encoding="utf-8")

    html_ids = set(re.findall(r'id="([^"]+)"', html))
    js_ids = set(re.findall(r'\$\("([^"]+)"\)', javascript))
    missing = sorted(js_ids - html_ids)
    check(not missing, "Jede im JavaScript benutzte ID existiert im HTML"
          + (f" - fehlt: {', '.join(missing)}" if missing else ""))

    for needed in ("/api/telemetry", "/style.css", "/app.js"):
        check(needed in html or needed in javascript, f"Dashboard benutzt {needed}")

    check("hours" in html_ids, "Betriebsstunden sind im Dashboard vorhanden")
    check("km/h" in html, "Geschwindigkeit ist als km/h ausgewiesen")
    check("implements" in html_ids, "Geräteliste ist im Dashboard vorhanden")
    check("diagChannel" in html_ids, "Kanal-Anzeige ist im Dashboard vorhanden")
    check("diagRate" in html_ids, "Wachstumsanzeige ist im Dashboard vorhanden")
    check("controls" in html_ids and "controlPill" in html_ids,
          "Steuerpanel ist im Dashboard vorhanden")
    check("/api/control" in javascript, "Dashboard sendet Befehle an /api/control")
    check("bestätigt" in javascript, "Dashboard zeigt die Bestätigung aus dem Spielzustand")
    check('"POST"' in javascript, "Steuerbefehle werden als POST gesendet")
    check("historyChart" in html_ids, "Verlaufsdiagramm ist im Dashboard vorhanden")
    check("commandLog" in html_ids, "Befehlsprotokoll ist im Dashboard vorhanden")
    check("diagDb" in html_ids, "Datenbank-Anzeige ist im Dashboard vorhanden")
    check("/api/history" in javascript, "Dashboard laedt den Verlauf vom Server")
    check("/api/commands" in javascript, "Dashboard laedt das Befehlsprotokoll")
    check(".innerHTML" not in javascript,
          "Dashboard baut Gerätenamen ohne innerHTML auf (kein HTML-Einschleusen)")
    check("pollMs" in javascript, "Dashboard übernimmt den Abfragetakt vom Server")
    check(css_path.is_file() and ".badge" in css_path.read_text(encoding="utf-8"),
          "CSS enthaelt die Geräte-Markierungen")
    check(css_path.is_file() and "body.stale" in css_path.read_text(encoding="utf-8"),
          "CSS enthaelt den Veraltet-Zustand")
else:
    check(False, "Dashboard-Dateien gefunden")

print()
if FAILURES:
    print(f"{len(FAILURES)} Test(s) fehlgeschlagen:")
    for failure in FAILURES:
        print(f"  - {failure}")
    sys.exit(1)

print("Alle Tests bestanden.")
