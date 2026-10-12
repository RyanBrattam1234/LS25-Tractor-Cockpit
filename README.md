# LS25 Tractor Cockpit – V1

Ein lokales, Telemetrie- und Steuerungs-Dashboard für Farming Simulator 25
(PC/Steam), gedacht für PC, Laptop und Android-Tablet im selben WLAN.

```
LS25  ->  Lua-Mod  ->  Telemetriedatei + log.txt  ->  Python-Server  ->  Web-Dashboard
                            ^                                                    |
                            +--------------- XML-Befehlsdatei <------------------+
```

## Aufbau des Dashboards (Tabs)

| Tab | Inhalt |
|---|---|
| **Cockpit** | Geschwindigkeit, Drehzahl, Kraftstoff, Motor, Betriebsstunden, Position, Lichtübersicht |
| **Geräte** | angehängte Geräte mit Zuständen, Füllstand und Füllart |
| **Steuerung** | Licht, Fernlicht, Arbeitslicht, Blinker, Rundumleuchte, Gerätebefehle, Befehlsprotokoll |
| **Verlauf** | Diagramm und Kennzahlen aus der SQLite-Datenbank |
| **Diagnose** | Kanal, Loggröße, Datenbank, Steuerkanal und Leistungsmesswerte |

Der aktive Tab bleibt gespeichert; der Verlauf wird nur geladen, wenn der Tab
offen ist. Bei ausgeblendeter Seite wird nicht abgefragt.

## Leistung (gemessen auf diesem PC)

`python tools\benchmark.py` schreibt eine Telemetriedatei im echten Format
viermal pro Sekunde und misst alles:

| Messung | Ergebnis |
|---|---|
| Telemetrie lesen + parsen | **Ø 1,1 ms** je Abruf (max 3,3 ms) |
| SQLite: Messung speichern | **Ø 0,015 ms** |
| SQLite: unveränderte Messung | Ø 0,008 ms (wird verworfen) |
| `/api/telemetry` | Ø 2,8 ms, **2,26 KB** (gepackt 1,01 KB) |
| Datenrate je Client | 4,5 KB/s roh, ~2 KB/s gepackt bei 2 Abrufen/s |
| `/api/history` | Ø 5 ms kalt, danach aus dem Zwischenspeicher |

Daraus folgen die Entwurfsentscheidungen:

* Die Erfassung läuft in einem **eigenen Hintergrund-Faden** (500 ms). Ein
  HTTP-Abruf liefert nur den Zwischenspeicher, es wird also nicht pro Browser
  eine Datei gelesen.
* Geschrieben wird nur, wenn sich etwas geändert hat (sonst höchstens alle 5 s
  ein Heartbeat) – die Datenbank bleibt klein.
* Der Verlauf hat einen **2-Sekunden-Zwischenspeicher**: mehrere Geräte im WLAN
  kosten zusammen nur eine Datenbankabfrage.
* Antworten werden ab 1 KB **gzip-gepackt**, wenn der Browser das anbietet.
* Das Dashboard fragt nur, was der offene Tab braucht (Verlauf und Protokoll
  werden nur auf ihren Tabs geladen).

## Status: geprüft gegen deine Installation

FS25 **1.24.0.0** (GIANTS Engine Runtime 10.0.0), Spielordner
`C:\Users\anika\Dokumente\My Games\FarmingSimulator2025`. Alle im Mod benutzten
Funktionen sind im Spielquelltext (`sdk/debugger/gameSource.zip`) nachgeschlagen,
und **203 automatische Prüfungen** (`python tools\selftest.py`) decken Parser,
beide Transportkanäle, Lichter, Steuer-Whitelist, Bestätigungen, SQLite und
HTTP ab.

**Wichtig:** Ein Mod wird nur beim Start von LS25 geladen. Nach dem
Aktualisieren des Mods das Spiel einmal komplett beenden und neu starten.

## Transport und Performance (Phase 2)

Der Mod schreibt in **zwei Kanäle**; der Server nimmt immer den frischesten:

| Kanal | Pfad / Takt | Zweck |
|---|---|---|
| Datei-Kanal | `<Spielprofil>\modSettings\FS25_TractorCockpit.txt`, alle 250 ms neu geschrieben | schneller Transport, ~1 KB Datei |
| Log-Kanal | `TRACTORCOCKPIT\|…` in `log.txt`, alle 10 s (bzw. jede Sekunde als Rückfall) | Protokoll und garantierter Rückfall |

Gemessen auf deinem System:

* `log.txt` wuchs bisher mit **~800 KB pro Stunde** (eine Zeile pro Sekunde,
  Ø 211 Zeichen). Mit aktivem Datei-Kanal sinkt das auf **~80 KB pro Stunde**.
* Der Datei-Kanal ist kein Neuland: Der installierte Mod `FS25_LiveMap`
  schreibt in dieser Installation alle **25 ms** eine JSON-Datei nach
  `modSettings/` – der Mod hier schreibt alle **250 ms**.
* Das Dashboard fragt alle **500 ms** ab (vom Server vorgegeben über
  `poll_ms` in `config/settings.json`), die Antwort ist ~850 Bytes groß.
* Fällt der Datei-Kanal aus (z. B. keine Schreibrechte), erkennt der Mod das
  selbst, protokolliert eine Zeile und geht automatisch auf eine Logzeile pro
  Sekunde zurück. Der Server braucht dafür keine Einstellung.

**Kein Socket möglich (geprüft):** Die FS25-Lua-Laufzeit hat keine
Netzwerk-Bindings; im `scriptBinding.xml` der Engine existiert keine einzige
Socket-/HTTP-Funktion, und die Spielskripte nutzen weder `io` noch `require`.
Ein direkter TCP-Kanal aus dem Spiel heraus ist damit nicht umsetzbar – der
Datei-Kanal ist der schnellste offiziell unterstützte Weg.

## Datenbank: SQLite

Der Server speichert den Verlauf in einer lokalen SQLite-Datei
(Standard: `server/data/history.sqlite3`, nur Standardbibliothek `sqlite3`):

| Tabelle | Inhalt |
|---|---|
| `readings` | eine Zeile pro Messung – nur bei Änderung oder als Heartbeat (max. alle 5 s), deshalb wächst die Datei langsam |
| `commands` | dauerhaftes Protokoll **jeder** Steuerung: Aktion, Gerät, Status, Bestätigung, Client |
| `meta` | reserviert für spätere Schema-Änderungen |

* Aufbewahrung: `history_retention_days` (Standard 7 Tage) und
  `history_max_rows` (Standard 500 000). Beides wird im Betrieb automatisch
  aufgeräumt.
* Geschrieben wird in einem **eigenen Hintergrund-Faden**, nie im HTTP-Pfad.
  Dadurch liefert `/api/telemetry` die Antwort aus dem Zwischenspeicher
  (schneller als vorher) und der Verlauf wird auch dann lückenlos
  aufgezeichnet, wenn gerade kein Browser offen ist.
* Modi: `journal_mode=WAL`, `synchronous=NORMAL` – robust und schnell.
* Abschalten: `--no-history` oder `"history_enabled": false`.
* Neue Endpunkte: `GET /api/history?minutes=30&buckets=120` (verdichteter
  Verlauf) und `GET /api/commands?limit=20` (Protokoll).

Das Dashboard zeigt daraus ein Verlaufsdiagramm (Ø Geschwindigkeit, Ø Drehzahl,
max. Werte, Betriebsstunden im Zeitraum), das dauerhafte Befehlsprotokoll und
in der Diagnose-Ecke die Zahl der gespeicherten Messungen samt Dateigröße.

## Was im Dashboard steht

| Anzeige | Einheit | Quelle im Spiel (verifiziert) |
|---|---|---|
| Fahrzeugname | – | `Vehicle:getName()` |
| Geschwindigkeit | km/h | `Vehicle:getLastSpeed()` – liefert bereits km/h (`lastSpeed * 3600`) |
| Motordrehzahl | U/min | `Motorized:getMotorRpmReal()` |
| Drehzahl-Skala | U/min | `VehicleMotor:getMinRpm()` / `getMaxRpm()` |
| Motorlast | % | `Motorized:getMotorLoadPercentage()` |
| Kraftstofffüllstand | Liter | `Motorized:getConsumerFillUnitIndex(FillType.DIESEL)` + `FillUnit:getFillUnitFillLevel()` |
| Kraftstoffkapazität | Liter | `FillUnit:getFillUnitCapacity()` |
| Kraftstoffart | – | Diesel, Elektro oder Methan (wie im Spiel-HUD) |
| Motorstatus | Läuft/Aus | `Motorized:getIsMotorStarted()` |
| Betriebsstunden | h | `Vehicle:getOperatingTime()` – Millisekunden, ÷ 3 600 000 |
| Weltposition | X/Y/Z | `getWorldTranslation(vehicle.rootNode)` |

### Anbaugeräte (Phase 3)

Bis zu 4 Geräte, zwei Anhängeebenen tief (Traktor → Gewicht → Anhänger):

| Anzeige | Quelle (verifiziert) |
|---|---|
| Name | `Vehicle:getName()` |
| Typ | `vehicle.typeName` |
| gesenkt / angehoben | `Vehicle:getIsLowered()` |
| aufgeklappt / geklappt | `Foldable:getIsUnfolded()` |
| eingeschaltet / aus | `TurnOnVehicle:getIsTurnedOn()` |
| Arbeitszone aktiv | `WorkArea:getIsWorkAreaActive(workArea)` (max. 8 Zonen) |
| Füllstand / Kapazität / Füllart | größte FillUnit: `getFillUnits()`, `getFillUnitFillLevel/Capacity/FillType()`, `g_fillTypeManager:getFillTypeTitleByIndex()` |

**Es wird nur gesendet, was das Gerät wirklich liefert.** Ein Güllefass ohne
`getIsLowered()` bekommt einfach keine „gesenkt“-Markierung – es wird nichts
geraten und nichts geschätzt. Nicht verfügbare Werte erscheinen als `--`.

### Licht, Blinker und Rundumleuchte (V1)

Alle Aufrufe sind die, die **das Spiel selbst** an seine Lichttasten bindet
(`Lights.lua`). Es wird kein Lichtindex hart kodiert – die Konstanten kommen
aus dem Spiel, und der Mod sendet nur Lampen, die das Fahrzeug wirklich hat.

| Anzeige / Taste | Verifizierter Aufruf |
|---|---|
| Abblendlicht | `setLightsTypesMask(mask ± 2^Lights.LIGHT_TYPE_DEFAULT)` |
| Fernlicht | `… 2^Lights.LIGHT_TYPE_HIGHBEAM` |
| Arbeitslicht vorn / hinten | `… 2^Lights.LIGHT_TYPE_WORK_FRONT` / `_WORK_BACK` |
| Lichtstufe weiter / zurück | `Lights:setNextLightsState(±1)` (wie die Lichttaste) |
| Rundumleuchte | `Lights:setBeaconLightsVisibility(an/aus)` + `getBeaconLightsVisibility()` |
| Blinker links / rechts / Warnblinker / aus | `Lights:setTurnLightState(Lights.TURNLIGHT_LEFT/RIGHT/HAZARD/OFF)` |
| Zustand aller Lichter | `spec_lights.lightsTypesMask` (Bitmaske) |
| Kann das Fahrzeug schalten? | `Lights:getCanToggleLight()` |

Das Dashboard zeigt den **echten** Zustand aus der Bitmaske: eine Taste ist nur
grün, wenn das Spiel die Lampe als an meldet. Nach jedem Befehl vergleicht der
Server den gemeldeten Zustand mit der Erwartung und schreibt „bestätigt“ oder
„noch nicht bestätigt“.

Zusätzlich zeigt das Dashboard eine Diagnose-Ecke: benutzter Kanal, Logdatei,
Alter der Daten, Loggröße, Log-Wachstum pro Stunde, Datenbankgröße und die
Leistungsmesswerte des Servers.

## Dateien

| Pfad | Zweck |
|---|---|
| `mod/FS25_TractorCockpit/modDesc.xml` | Mod-Beschreibung (`descVersion 114` = FS25 1.24) |
| `mod/FS25_TractorCockpit/scripts/TractorCockpit.lua` | Telemetrie, Datei- und Log-Kanal |
| `server/server.py` | lokaler HTTP-Server, beide Kanäle (nur Standardbibliothek) |
| `server/www/` | Dashboard (HTML/CSS/JS, keine Abhängigkeiten) |
| `tools/install_mod.ps1` | baut das Mod-ZIP und kopiert es in den `mods`-Ordner |
| `tools/check_install.ps1` | prüft Python, Spielordner, Mod, Log, Lua und Server |
| `tools/backup_savegame.ps1` | sichert einen Spielstand vor Steuer-Tests |
| `tools/selftest.py` | Offline-Test von Server und Steuerkanal (ohne LS25) |
| `tools/lua_check.py` | Strukturprüfung der Lua-Datei (dieser PC hat keinen Lua-Interpreter) |
| `config/settings.json` | Host, Port, Logpfade, Schwelle, Abfragetakt, Steuerfreigabe |

## Steuerung (Phase 4) – standardmäßig aus

Die Steuerung ist **absichtlich gesperrt**, bis du sie ausdrücklich einschaltest.
Es gibt genau eine Freigabe (Server-Flag), und der Mod führt nur benannte
Aktionen aus einer Whitelist aus:

| Aktion | Was im Spiel passiert | Verifizierter Aufruf |
|---|---|---|
| `lower` / `raise` | Gerät senken / heben | `AttacherJoints:handleLowerImplementEvent(implement, true/false)` |
| `toggleFold` | klappen / aufklappen | `Foldable:setFoldDirection(Foldable:getToggledFoldDirection())` |
| `turnOn` / `turnOff` | Arbeitsgerät ein / aus | `TurnOnVehicle:setIsTurnedOn(true/false)` |
| `lightDefaultOn/Off` | Abblendlicht | `setLightsTypesMask` mit `LIGHT_TYPE_DEFAULT` |
| `lightHighBeamOn/Off` | Fernlicht | `setLightsTypesMask` mit `LIGHT_TYPE_HIGHBEAM` |
| `lightWorkFrontOn/Off` | Arbeitslicht vorn | `setLightsTypesMask` mit `LIGHT_TYPE_WORK_FRONT` |
| `lightWorkBackOn/Off` | Arbeitslicht hinten | `setLightsTypesMask` mit `LIGHT_TYPE_WORK_BACK` |
| `lightNext` / `lightPrev` | Lichtstufe weiter / zurück | `Lights:setNextLightsState(±1)` |
| `beaconOn` / `beaconOff` | Rundumleuchte | `Lights:setBeaconLightsVisibility(bool)` |
| `turnLeft` / `turnRight` / `turnHazard` / `turnOff` | Blinker und Warnblinker | `Lights:setTurnLightState(Lights.TURNLIGHT_*)` |

Gerätebefehle gehen an `target` 1–8 (Anbaugerät), Lichtbefehle an `target` 0
(das gesteuerte Fahrzeug selbst).

**PTO lässt sich nicht schalten.** Im FS25-Quelltext gibt es dafür keinen Setter
(nur `getPtoRpm`, `getDoConsumePtoPower` usw.). PTO ergibt sich im Spiel aus
„Gerät angehängt + abgesenkt/eingeschaltet“ – ich habe deshalb lieber nichts
erfunden.

### Sicherheitsgrenzen (alle geprüft)

1. **Aus per Standard.** Ohne `--enable-control` (oder `"control_enabled": true`)
   wird nichts geschrieben; die Knöpfe im Dashboard sind gesperrt.
2. **Whitelist.** Nur die fünf Aktionsnamen oben werden übertragen – niemals
   Lua-Code, niemals ein Funktionsname aus dem Netz.
3. **Ein Befehl zur Zeit**, mit Mindestabstand (Standard 500 ms) und Zeitlimit
   (Standard 5 s). Solange keine Rückmeldung da ist, wird nichts Neues gesendet.
4. **Nur unterstützte Geräte.** Der Mod meldet pro Gerät, was es kann
   (`CanLower`/`CanFold`/`CanOn`, geprüft über `getAllowsLowering()`,
   `setFoldDirection`-Existenz, `setIsTurnedOn`-Existenz). Server und Dashboard
   sperren alles andere aus.
5. **Kein fremder Browser.** POST auf `/api/control` wird abgelehnt, wenn der
   `Origin`-Header nicht zum Host passt (CSRF-Schutz), und verlangt
   `Content-Type: application/json`.
6. **Zustandsprüfung statt Optimismus.** Das Dashboard ändert nie von sich aus
   einen Zustand. Nach dem Befehl vergleicht der Server den gemeldeten
   Spielzustand mit der Erwartung und zeigt „bestätigt“ oder „noch nicht
   bestätigt“.
7. **Alte Befehle werden nicht wiederholt.** Der Mod liest die Befehlsdatei beim
   Laden einmal und ignoriert vorhandenen Inhalt aus früheren Sitzungen.

### Steuerung einschalten

```powershell
# 1. Spielstand sichern (Pflicht bei eigenen Tests)
powershell -ExecutionPolicy Bypass -File tools\backup_savegame.ps1

# 2. Server MIT Steuerung starten
python server\server.py --enable-control
```

Dann im Dashboard im Panel **Steuerung** die Knöpfe benutzen. Jeder Befehl steht
zusätzlich im Konsolenfenster des Servers, dauerhaft in der SQLite-Datenbank und
– als Ergebnis – als `TractorCockpit: Befehl n action -> status` in `log.txt`.

Zum Abschalten: Server ohne `--enable-control` starten. Für eine harte Sperre
im Spiel selbst zusätzlich `TractorCockpit.CONTROL_ENABLED = false` in
`TractorCockpit.lua` setzen (dann führt der Mod nie einen Befehl aus).

### Wie der Rückkanal funktioniert (und warum XML)

FS25 erlaubt Mods das **Schreiben** von Dateien, aber **nicht das Lesen** über
`io.open` – jeder Leseversuch wird mit
`Warning: io.open, only write mode ('w') is allowed` quittiert. Der einzige
erlaubte Leseweg ist die **XML-API des Spiels** (`XMLFile.load`). Deshalb ist
die Befehlsdatei eine XML-Datei:

```xml
<?xml version="1.0" encoding="utf-8" standalone="no"?>
<tractorcockpit id="7" action="lower" target="1" name="Krampe Big Body 750"/>
```

Der Mod testet beim Laden zwei Pfade und meldet den ersten lesbaren in der
Telemetrie:

1. `<Spielprofil>\modSettings\FS25_TractorCockpit_cmd.xml`
2. `<Spielstand>\FS25_TractorCockpit_cmd.xml` (z. B. `savegame6\…`)

Der Server schreibt den Befehl genau in den gemeldeten Pfad. Ist kein Pfad
lesbar, bleibt die Steuerung aus und das Dashboard sagt „KEIN KANAL“ – es wird
nichts ins Leere geschickt. Im `log.txt` steht nach dem Laden, welcher Pfad
funktioniert (`TractorCockpit: Steuerkanal bereit -> …`).

## 1. Mod installieren oder aktualisieren

```powershell
powershell -ExecutionPolicy Bypass -File tools\install_mod.ps1
```

Das Skript baut `FS25_TractorCockpit.zip` (mit `modDesc.xml` direkt im
ZIP-Hauptordner, wie LS25 es erwartet) und kopiert es nach
`…\My Games\FarmingSimulator2025\mods`. Der Dokumente-Ordner wird automatisch
gefunden – auch wenn er „Dokumente“ heißt oder in OneDrive liegt.

Danach: LS25 starten, Mod beim Spielstand aktivieren, in ein Fahrzeug
einsteigen und ein paar Sekunden fahren.

## 2. Installation prüfen

```powershell
powershell -ExecutionPolicy Bypass -File tools\check_install.ps1
```

Prüft unter anderem: Python-Version, gefundenen Logpfad, `ModDesc Version` aus
dem Log, ob der Mod geladen wurde, wie viele Telemetriezeilen es gibt und wie
alt die letzte ist, die Lua-Struktur und den Server-Selbsttest.

Alternativ von Hand in `log.txt` nach `TRACTORCOCKPIT|` suchen. Eine Zeile
sieht so aus:

```
2026-10-11 13:28:47.504 TRACTORCOCKPIT|state=vehicle|name=SKH 60|speed=14.27|rpm=1799.96|rpmMin=850.00|rpmMax=2200.00|load=42.00|fuel=68.00|fuelCapacity=75.00|fuelType=diesel|engine=1|hours=12.35|x=58.35|y=29.79|z=-715.14|implCount=1|impl1Name=Krampe Big Body 750|impl1Type=trailer|impl1Lowered=1|impl1Unfolded=0|impl1Fill=12345.00|impl1FillCapacity=20000.00|impl1FillType=Weizen
```

## 3. Python-Server starten

```powershell
python server\server.py
```

Der Server zeigt die Logdatei, die Telemetriedatei, die Laptop- und die
LAN-Adresse an:

* Laptop: `http://127.0.0.1:8765`
* Tablet: `http://<IP-des-PC>:8765`

Optionen bei Bedarf:

```powershell
python server\server.py --port 8766 --log "D:\log.txt" --poll-ms 250 --stale-seconds 10
```

`config/settings.json`:

```json
{
  "host": "0.0.0.0",
  "port": 8765,
  "log_path": "",
  "telemetry_file": "",
  "stale_seconds": 15,
  "poll_ms": 500
}
```

`log_path` und `telemetry_file` leer lassen ist der bequemste Weg: der Server
sucht beides selbst. Feste Pfade werden bevorzugt, solange die Datei existiert.

## 4. Windows-Firewall

1. Laptop und Tablet im selben **privaten** WLAN.
2. Python nur für **private Netzwerke** erlauben (beim ersten Start fragt Windows).
3. Keine Router-Portfreigabe. Kein öffentliches WLAN.

## 5. „Veraltet“ – was der Server meldet

Der Server vergleicht den Zeitstempel der jüngsten Zeile (aus Datei- oder
Log-Kanal) mit der Uhr. Ist sie älter als `stale_seconds`, zeigt das Dashboard
die Werte gedimmt und die Verbindung als „Daten veraltet“:

* Spiel beendet → keine neuen Zeilen → veraltet.
* Spiel pausiert (ESC-Menü) → das Spiel ruft `update()` nicht auf → veraltet.
* Spieler zu Fuß → der Mod sendet ein Lebenszeichen → „Kein Fahrzeug gesteuert“.

## 6. Sicherheit

* Der Server liest nur und hat **keine Route zum Steuern** von Fahrzeugen.
* Er hat keine Authentifizierung: nur im vertrauenswürdigen Heimnetz betreiben
  und nach dem Spielen mit `Ctrl+C` beenden.
* Pfadausbrüche aus dem Web-Ordner sind blockiert, Antworten sind `no-store`.
* Gerätenamen aus der Spieldatei werden im Dashboard als Text aufgebaut
  (kein `innerHTML`), also nie als HTML ausgeführt.

## 7. Nächste Schritte

Siehe `docs/ROADMAP.md`. Kurz: erst Telemetrie im Spiel bestätigen, dann erst
Steuerbefehle – und die brauchen einen Rückkanal, den FS25 nur über Dateien
erlaubt (siehe Transport-Abschnitt).

## Offizielle Referenz

GIANTS Developer Network – FS25 Scripting API:
<https://gdn.giants-software.com/documentation_scripting_fs25.php>
