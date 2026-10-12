# Roadmap

## Phase 0 – lokales Grundgerüst ✅
- [x] Browser-Dashboard (PC, Laptop, Tablet)
- [x] Python-Server ohne externe Pakete
- [x] Logdatei-Parser mit inkrementellem Lesen
- [x] Sicherheitsgrenze: keine Steuerbefehle
- [x] Automatische Pfadsuche („Dokumente“/OneDrive/Registry)
- [x] Veraltet-Erkennung statt „ewig verbunden“
- [x] Offline-Selbsttest und Installationsprüfung

## Phase 1 – Telemetrie validieren ✅ (gegen FS25 1.24.0.0 geprüft)
- [x] Fahrzeugname – `Vehicle:getName()`
- [x] Geschwindigkeit – `getLastSpeed()` liefert km/h (im Quelltext bestätigt
      und über Positionsdifferenzen der echten Logzeilen gegengerechnet)
- [x] Motordrehzahl – `getMotorRpmReal()`, Skala über `getMinRpm()/getMaxRpm()`
- [x] Kraftstoff – `getConsumerFillUnitIndex()` + `getFillUnitFillLevel/Capacity`
- [x] Kraftstoffart – Diesel/Elektro/Methan wie im Spiel-HUD
- [x] Motorstatus – `getIsMotorStarted()`
- [x] Betriebsstunden – `getOperatingTime()` (ms ÷ 3 600 000)
- [x] Weltposition – `getWorldTranslation(rootNode)`
- [x] Tablet-Zugriff im WLAN (Firewall nur privat, kein Portforwarding)

Offen bleibt nur der Praxistest **nach** dem Neustart des Spiels mit dem neuen
Mod-ZIP: einmal fahren und im Dashboard prüfen, ob alle Werte plausibel sind.

## Phase 2 – robuste Datenbrücke ✅
- [x] **Socket geprüft: nicht möglich.** Im `scriptBinding.xml` von FS25 1.24
      gibt es keine Socket-/HTTP-Funktion; die Spielskripte nutzen weder `io`
      noch `require`. Ein TCP-Kanal aus dem Spiel ist damit ausgeschlossen.
- [x] **Direkter Transport umgesetzt (Datei-Kanal).** Der Mod schreibt alle
      250 ms `<Spielprofil>/modSettings/FS25_TractorCockpit.txt` über
      `io.open` – dasselbe Muster wie der in dieser Installation laufende Mod
      `FS25_LiveMap` (dort alle 25 ms). Der Server liest Datei **und**
      `log.txt` und nimmt immer den frischesten Wert.
- [x] **Rückfall eingebaut.** Schlägt der Datei-Kanal fehl (z. B. keine
      Schreibrechte), erkennt der Mod das selbst und schreibt wieder jede
      Sekunde ins Log; der Server braucht keine Umschaltung.
- [x] **Log-Rotation und neue Spielsitzungen** werden über Datei-Identität
      (`st_dev`/`st_ino`/Erstellungszeit) *und* Größenschrumpfung erkannt –
      auch wenn die neue Datei größer ist als die alte.
- [x] **Datenrate/Loggröße gemessen und im Dashboard sichtbar.**
      Vorher ~800 KB/h Logwachstum, jetzt ~80 KB/h; Loggröße und Wachstum pro
      Stunde stehen in der Diagnose-Ecke.

## Phase 3 – Standardgeräte erkennen ✅
- [x] Attacher/Implement-Objekte auslesen (`getAttachedImplements()`),
      bis zu 4 Geräte, zwei Anhängeebenen tief
- [x] Gerätetyp (`vehicle.typeName`)
- [x] Zustände einzeln geprüft: gesenkt/angehoben (`getIsLowered()`),
      aufgeklappt/geklappt (`getIsUnfolded()`), ein/aus (`getIsTurnedOn()`),
      Arbeitszone aktiv (`getIsWorkAreaActive()`, max. 8 Zonen)
- [x] Füllstand, Kapazität und Füllart der größten FillUnit des Geräts
- [x] Nur Daten anzeigen, die tatsächlich verfügbar sind: nicht vorhandene
      Werte werden gar nicht erst gesendet und erscheinen als `--`

Offen: eigene Prüfung im Spiel mit verschiedenen Geräten (Pflug, Ballenpresse,
Anhänger), ob die Zustände so angezeigt werden, wie du sie im Cockpit brauchst.

## Phase 4 – Steuerung ✅ umgesetzt, Freigabe bewusst standardmäßig AUS
- [x] Rückkanal vom Server zum Spiel: Datei
      `<Spielprofil>/modSettings/FS25_TractorCockpit_cmd.txt`, die der Mod alle
      250 ms liest. Rückmeldung kommt über `cmdId`/`cmdResult`/`cmdMsg` in der
      Telemetrie zurück.
- [x] Server-seitige Command-Whitelist: nur `lower`, `raise`, `toggleFold`,
      `turnOn`, `turnOff` (in `server.py` als `CONTROL_ACTIONS`).
- [x] Keine beliebigen Lua-Aufrufe aus HTTP: übertragen wird nur ein
      Aktionsname aus der Whitelist plus Geräteplatz, sonst nichts. Der Mod
      lehnt alles ab, was nicht in seiner eigenen `COMMAND_ACTIONS`-Tabelle steht.
- [x] Eine Aktion nach der anderen: laufender Befehl blockiert den nächsten,
      Mindestabstand 500 ms, Zeitlimit 5 s, dann „timeout“.
- [x] PTO: **geprüft und nicht umgesetzt**, weil FS25 dafür keinen Setter hat
      (nur `getPtoRpm`/`getDoConsumePtoPower`). PTO folgt im Spiel aus
      anhängen + absenken/einschalten.
- [x] Nur bei unterstütztem Gerät freischalten: `CanLower` (über
      `getAllowsLowering()`), `CanFold`, `CanOn` werden mitgesendet; Server und
      Dashboard sperren den Rest.
- [x] Rückmeldung aus tatsächlichem Spielzustand: nach dem Befehl vergleicht der
      Server den gemeldeten Zustand mit der Erwartung (`lowered`/`on`) bzw. bei
      `toggleFold` die tatsächliche Änderung. Die UI ändert nie etwas von selbst.
- [x] Tests mit Sicherungsspielstand: `tools/backup_savegame.ps1` sichert den
      zuletzt gespielten Spielstand mit Zeitstempel nach `savegameBackup`.
      140 Offline-Prüfungen decken Whitelist, Ein-Befehl-Regel, Timeout,
      Capability-Sperre, CSRF/Origin, Content-Type und Bestätigung ab.

**Offen (nur im Spiel prüfbar):** der erste echte Befehl. Ablauf:
Spielstand sichern → `python server\server.py --enable-control` → LS25 starten,
Gerät anhängen, im Dashboard **Steuerung** nutzen → im Log
`TractorCockpit: Befehl n … -> ok` und die Bestätigung im Dashboard prüfen.

## V1 – Dashboard, Licht und Leistung ✅
- [x] **UI überarbeitet und in Tabs aufgeteilt**: Cockpit, Geräte, Steuerung,
      Verlauf, Diagnose. Der aktive Tab wird gemerkt; Verlauf und Protokoll
      werden nur auf ihrem Tab geladen.
- [x] **Lichtsteuerung**: Abblendlicht, Fernlicht, Arbeitslicht vorn/hinten,
      Lichtstufen, Rundumleuchte, Blinker links/rechts, Warnblinker – alles
      über die Funktionen, die das Spiel selbst an seine Lichttasten bindet.
- [x] **Echte Zustandsanzeige**: Lampen und Blinker werden aus der
      `lightsTypesMask` gelesen; nach jedem Befehl prüft der Server den
      gemeldeten Zustand (bestätigt / nicht bestätigt).
- [x] **Leistungsmessung** als Werkzeug (`tools/benchmark.py`) und im
      Dashboard: Erfassungszeit, Antwortgröße, Datenbank, API-Anfragen.
      Gemessen: Ø 1,1 ms Erfassung, Ø 0,015 ms pro Datenbankzeile,
      2,26 KB Antwort (1,01 KB gepackt).
- [x] **Optimierungen**: Hintergrund-Erfassung, Änderungs-Erkennung,
      2-Sekunden-Zwischenspeicher für den Verlauf, gzip ab 1 KB,
      Antwortgröße um ~30 % verkleinert.

## Phase 5 – Stabilität
- [x] Verlauf und dauerhaftes Befehlsprotokoll aus der **SQLite**-Datenbank
- [ ] Fahrzeugwechsel und Spielstandwechsel im Dashboard glätten
- [ ] Dauerlauf über mehrere Stunden auf dem Tablet beobachten
- [ ] Blinker-Automatik (Blinken nach Fahrtrichtungswechsel) prüfen
