#!/usr/bin/env python3
"""Misst die Leistung von Server, Erfassung und Datenbank.

Laeuft ohne Farming Simulator: es wird eine Telemetriedatei im echten Format
erzeugt und waehrend der Messung wie vom Mod viermal pro Sekunde neu
geschrieben. Zusaetzlich werden die echten Messwerte des Servers ausgegeben,
falls LS25 gerade laeuft.

Aufruf:
    python tools/benchmark.py
    python tools/benchmark.py --samples 300
"""
from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
WORKSPACE = SERVER_DIR.parent
sys.path.insert(0, str(SERVER_DIR))

import server  # noqa: E402
import storage  # noqa: E402

VEHICLE_FIELDS = (
    "state=vehicle|name=1050 Vario|speed=14.27|rpm=1799.96|rpmMin=650.00|rpmMax=1700.00"
    "|load=42.00|fuel=702.98|fuelCapacity=740.00|fuelType=diesel|engine=1|hours=12.35"
    "|x=58.35|y=29.79|z=-715.14"
    "|canToggleLight=1|lightCan=default,highBeam,workFront|lightOn=default,workFront"
    "|beaconCan=1|beacon=0|turnCan=1|turnLight=off"
    "|implCount=2"
    "|impl1Name=Krampe Big Body 750|impl1Type=trailer|impl1Lowered=1|impl1Unfolded=0"
    "|impl1Working=0|impl1Fill=12345.00|impl1FillCapacity=20000.00|impl1FillType=Weizen"
    "|impl1CanLower=1|impl1CanFold=0|impl1CanOn=0"
    "|impl2Name=Krone Comprima|impl2Lowered=0|impl2Unfolded=1|impl2On=1|impl2Working=1"
    "|impl2CanLower=0|impl2CanFold=1|impl2CanOn=1"
    "|cmdMode=xml|cmdPath=benchmark"
)


def log_line(fields: str, seconds_ago: float = 0.0) -> str:
    moment = datetime.now().timestamp() - seconds_ago
    text = datetime.fromtimestamp(moment).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return f"{text} TRACTORCOCKPIT|{fields}"


def measure(count: int, action) -> tuple[float, float, float]:
    """Rueckgabe: (Summe, Maximum, Durchschnitt) in Millisekunden."""
    total = 0.0
    worst = 0.0
    for _ in range(count):
        started = time.perf_counter()
        action()
        elapsed = (time.perf_counter() - started) * 1000.0
        total += elapsed
        worst = max(worst, elapsed)
    return total, worst, total / max(1, count)


def main() -> int:
    parser = argparse.ArgumentParser(description="Leistungsmessung des Tractor-Cockpit-Servers")
    parser.add_argument("--samples", type=int, default=200, help="Messungen je Pruefung")
    parser.add_argument("--http-samples", type=int, default=60, help="HTTP-Abrufe")
    args = parser.parse_args()

    samples = max(10, args.samples)
    http_samples = max(5, args.http_samples)

    work = WORKSPACE / "benchmark_tmp"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    (work / "modSettings").mkdir(parents=True, exist_ok=True)

    log_path = work / "log.txt"
    telemetry_path = work / "modSettings" / "FS25_TractorCockpit.txt"
    db_path = work / "history.sqlite3"
    log_path.write_text(log_line(VEHICLE_FIELDS) + "\n", encoding="utf-8")
    telemetry_path.write_text(log_line(VEHICLE_FIELDS) + "\n", encoding="utf-8")

    store = storage.HistoryStore(db_path, min_interval_ms=0, max_interval_ms=5000)
    controller = server.CommandController(work / "cmd.xml", enabled=False)
    reader = server.TelemetryReader(log_path, 15.0, "benchmark", telemetry_path)
    perf = server.PerformanceMonitor()
    sampler = server.TelemetrySampler(reader, controller, store, 0.5, perf)
    httpd = server.build_server("127.0.0.1", 0, reader, poll_ms=500,
                               controller=controller, store=store, sampler=sampler)
    httpd.RequestHandlerClass.log_message = lambda *args, **kwargs: None
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    stop = threading.Event()

    def writer():
        """Schreibt die Telemetriedatei wie der Mod: viermal pro Sekunde."""
        counter = 0
        while not stop.is_set():
            counter += 1
            speed = 10 + (counter % 50) / 10.0
            text = log_line(VEHICLE_FIELDS.replace("speed=14.27", f"speed={speed:.2f}"))
            try:
                telemetry_path.write_text(text + "\n", encoding="utf-8")
            except OSError:
                pass
            stop.wait(0.25)

    writer_thread = threading.Thread(target=writer, daemon=True)
    writer_thread.start()

    print("LS25 Tractor Cockpit - Leistungsmessung")
    print(f"Messungen je Pruefung: {samples}   HTTP-Abrufe: {http_samples}")
    print(f"Arbeitsordner: {work}")
    print()

    # 1. Erfassung (Datei lesen + parsen)
    time.sleep(0.6)
    total, worst, average = measure(samples, lambda: reader.poll())
    print("Erfassung")
    print(f"  Telemetrie lesen+parsen   Ø {average:7.3f} ms   max {worst:7.3f} ms   ({samples} Messungen)")
    print(f"  entspricht                {average * 2:7.3f} ms Rechenzeit pro Sekunde bei 2 Abfragen/s")

    # 2. Datenbank
    reading = reader.poll()
    reading["speed"] = 1.0
    total, worst, average = measure(samples, lambda: store.record(reading))
    print()
    print("Datenbank (SQLite)")
    print(f"  Messung speichern         Ø {average:7.3f} ms   max {worst:7.3f} ms")
    unchanged = dict(reading)
    total, worst, average = measure(samples, lambda: store.record(unchanged))
    print(f"  unveraendert (verworfen)  Ø {average:7.3f} ms   max {worst:7.3f} ms")
    rows = store.stats()["rows"]
    size = store.stats()["sizeBytes"] or 0
    print(f"  Zeilen: {rows}   Datei: {size / 1024:.1f} KB")

    # 3. HTTP
    def fetch(path, headers=None):
        request = urllib.request.Request(base + path, headers=headers or {})
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.read(), dict(response.headers)

    body, headers = fetch("/api/telemetry")
    print()
    print("HTTP (gleicher Rechner)")
    total, worst, average = measure(http_samples, lambda: fetch("/api/telemetry"))
    print(f"  /api/telemetry            Ø {average:7.3f} ms   max {worst:7.3f} ms")
    print(f"  Antwortgroesse            {len(body) / 1024:.2f} KB")
    print(f"  Datenrate je Client       {len(body) * 2 / 1024:.2f} KB/s bei 2 Abrufen/s")

    compressed, gz_headers = fetch("/api/telemetry", {"Accept-Encoding": "gzip"})
    print(f"  gepackt                   {len(compressed) / 1024:.2f} KB "
          f"({gz_headers.get('Content-Encoding', 'nicht gepackt')})")

    body, _ = fetch("/api/history?minutes=30&buckets=120")
    total, worst, average = measure(10, lambda: fetch("/api/history?minutes=30&buckets=120"))
    print(f"  /api/history              Ø {average:7.3f} ms   ({len(body) / 1024:.2f} KB)")
    cached = json.loads(fetch("/api/history?minutes=30&buckets=120")[0].decode("utf-8"))
    print(f"  Zwischenspeicher          {'aktiv' if cached.get('cached') else 'kalt'}")

    # 4. Messwerte des Servers selbst
    sampler.tick()
    time.sleep(0.2)
    stats = perf.stats()
    print()
    print("Innenansicht des Servers")
    for key in ("samplerTotal", "samplerRead", "samplerStore"):
        entry = stats.get(key)
        if entry:
            print(f"  {key:24s} Ø {entry['avgMs']:7.3f} ms   max {entry['maxMs']:7.3f} ms")
    print(f"  Antwortgroesse (gemessen) {stats.get('payloadBytes', 0) / 1024:.2f} KB")

    stop.set()
    writer_thread.join(timeout=1.0)
    httpd.shutdown()
    httpd.server_close()
    store.close()
    shutil.rmtree(work, ignore_errors=True)

    print()
    print("Alle Werte auf diesem Rechner gemessen. Die Erfassung laeuft im")
    print("Hintergrund; ein Dashboard-Abruf liefert den Zwischenspeicher.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
