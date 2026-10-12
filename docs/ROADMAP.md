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


# Erweiterte Roadmap – kommende Funktionen

## Phase 5 – Stabilität und Zuverlässigkeit
- [x] Verlauf und dauerhaftes Befehlsprotokoll über SQLite
- [ ] Fahrzeugwechsel und Spielstandwechsel im Dashboard glätten
- [ ] Dauerlauf über mehrere Stunden auf dem Tablet beobachten
- [ ] Blinker-Automatik nach Fahrtrichtungswechsel prüfen
- [ ] Automatische Erkennung einer neuen Spielsitzung
- [ ] Wiederverbindung nach Server- oder Spielneustart
- [ ] Verständliche Fehlermeldungen bei fehlenden Telemetriedaten
- [ ] Schutz vor doppelten oder veralteten Messwerten
- [ ] Stabilitätstest über 6–12 Stunden
- [ ] Wiederherstellung des letzten Dashboard-Zustands nach Neuladen

## Phase 6 – Fahrzeugprofile und Cockpit-Personalisierung
- [ ] Automatische Erkennung des aktiven Fahrzeugs
- [ ] Individuelles Dashboard-Profil für jeden Traktor
- [ ] Fahrzeugspezifische Anzeigeelemente
- [ ] Eigene Anordnung und Größe der Cockpit-Kacheln
- [ ] Umschaltbare Ansichten für Traktor, Mähdrescher und andere Maschinen
- [ ] Anzeige von Marke, Modell und Fahrzeugbild, sofern verfügbar
- [ ] Benutzerdefinierte Warnschwellen für Drehzahl und Kraftstoff
- [ ] Speicherung der Dashboard-Einstellungen lokal
- [ ] Vollbildmodus für Laptop und Tablet
- [ ] Umschaltbare helle und dunkle Benutzeroberfläche

## Phase 7 – Erweiterte Telemetrie
- [ ] Motortemperatur anzeigen, sofern das Spiel den Wert bereitstellt
- [ ] Öl- und weitere Betriebsdaten prüfen, sofern verfügbar
- [ ] Aktuellen Gang und Fahrtrichtung anzeigen, sofern auslesbar
- [ ] Motorlast und Leistungsdaten ergänzen, sofern verfügbar
- [ ] Kraftstoffverbrauch über Zeit berechnen
- [ ] Durchschnitts- und Höchstgeschwindigkeit erfassen
- [ ] Zurückgelegte Strecke pro Fahrt berechnen
- [ ] Fahrzeit und Leerlaufzeit getrennt erfassen
- [ ] Betriebsdaten zwischen Fahrten vergleichen
- [ ] Warnungen bei ungewöhnlichen oder fehlenden Messwerten
- [ ] Fahrzeug- und Gerätedaten übersichtlich zusammenführen

## Phase 8 – GPS, Karte und Fahrtenaufzeichnung
- [ ] Live-Position auf einer lokalen Karte darstellen
- [ ] Zurückgelegte Fahrstrecke als GPS-Spur aufzeichnen
- [ ] Fahrtaufzeichnung starten und stoppen
- [ ] Fahrten mit Datum, Fahrzeug und Dauer speichern
- [ ] Geschwindigkeit entlang der Fahrstrecke visualisieren
- [ ] Arbeitsbereiche und wiederkehrende Fahrwege dokumentieren
- [ ] Koordinaten und Fahrtdaten exportieren
- [ ] Letzte Fahrzeugposition nach einer Fahrt anzeigen
- [ ] Kartenansicht für Laptop und Tablet optimieren

Hinweis: Eine richtige Karte benötigt geeignetes Kartenmaterial. Die reine Weltposition aus dem Spiel reicht noch nicht für eine fertige Kartenansicht.

## Phase 9 – Geräteverwaltung 2.0
- [ ] Mehr als zwei Anbauebenen zuverlässig erkennen
- [ ] Anzahl der erkannten Geräte dynamisch erweitern
- [ ] Geräte in einer übersichtlichen Baumstruktur darstellen
- [ ] Anbaugeräte mit Namen und Symbolen kennzeichnen
- [ ] Arbeitsstatus aller erkannten Geräte zusammenfassen
- [ ] Gerätewechsel ohne Neuladen des Dashboards erkennen
- [ ] Füllstände von Anhängern und Arbeitsgeräten getrennt darstellen
- [ ] Arbeitsbreite und weitere Geräteeigenschaften auslesen, sofern verfügbar
- [ ] Arbeitszeit pro Gerät erfassen
- [ ] Geräteprofile für häufig verwendete Kombinationen speichern

## Phase 10 – Steuerung und Bedienkomfort
- [ ] Bestätigung vor kritischen Aktionen
- [ ] Statusanzeige für laufende, bestätigte und fehlgeschlagene Befehle
- [ ] Doppelklickschutz für sämtliche Steuerungsschaltflächen
- [ ] Sicherheitsabschaltung der Fernsteuerung
- [ ] Steuerung automatisch sperren, wenn die Verbindung veraltet ist
- [ ] Gerätewechsel während eines laufenden Befehls sicher behandeln
- [ ] Steuerung nur für ausdrücklich unterstützte Funktionen freigeben
- [ ] Lichtsteuerung bei Fahrzeugwechsel neu synchronisieren
- [ ] Individuelle Schaltflächen für häufig verwendete Aktionen
- [ ] Optionaler Bestätigungsmodus für jede Steueraktion
- [ ] Steuerbefehle mit Zeitstempel und Ergebnis dokumentieren
- [ ] Alle Aktionen weiterhin über eine feste Whitelist begrenzen

Wichtig: Neue Steuerfunktionen werden erst freigegeben, nachdem ihre Spiel-API, ihre Auswirkungen und die sichere Rückmeldung geprüft wurden.

## Phase 11 – Verlauf, Statistiken und Berichte
- [ ] Fahrten automatisch in einzelne Sitzungen aufteilen
- [ ] Tages-, Wochen- und Monatsstatistiken erstellen
- [ ] Kraftstoffverbrauch pro Fahrt vergleichen
- [ ] Betriebsstunden je Fahrzeug auswerten
- [ ] Arbeitszeit und Fahrzeit vergleichen
- [ ] Höchstgeschwindigkeit und Drehzahlverlauf grafisch darstellen
- [ ] Anbaugeräte nach Nutzungshäufigkeit auswerten
- [ ] Filter nach Fahrzeug, Zeitraum und Spielstand
- [ ] Fahrtdaten als CSV und JSON exportieren
- [ ] Diagnosebericht für Fehlerfälle erstellen
- [ ] Verlauf bei Bedarf archivieren und alte Daten bereinigen
- [ ] Datenbankintegrität regelmäßig prüfen

## Phase 12 – Modernes Tablet-Cockpit
- [ ] Eigenständiges Tablet-Layout für Android
- [ ] Große, gut erreichbare Schaltflächen
- [ ] Kompakte Fahransicht mit Geschwindigkeit, Drehzahl und Kraftstoff
- [ ] Separate Arbeitsansicht für Anbaugeräte
- [ ] Separate Licht- und Steuerungsansicht
- [ ] Automatische Anpassung an Hoch- und Querformat
- [ ] Touch-Bedienung ohne versehentliche Doppelauslösung
- [ ] Reduzierte Animationen für schwächere Geräte
- [ ] Lesbare Warnmeldungen auch bei kleiner Bildschirmgröße
- [ ] Verbindung und Aktualität der Daten jederzeit sichtbar

## Phase 13 – Diagnose und Systemüberwachung
- [ ] Live-Anzeige der Telemetrie-Aktualisierungsrate
- [ ] Erkennung ungewöhnlich langer Aktualisierungspausen
- [ ] Messung der Verzögerung zwischen Spiel und Dashboard
- [ ] Anzeige des letzten erfolgreichen Datenempfangs
- [ ] Prüfung von Datei-, Server- und Datenbankzugriff
- [ ] Automatischer Selbsttest beim Serverstart
- [ ] Erkennung beschädigter oder unvollständiger Telemetriedaten
- [ ] Diagnoseexport mit relevanten Fehlerprotokollen
- [ ] Ressourcenverbrauch und Speicherentwicklung überwachen
- [ ] Optionale Aufbewahrungsgrenzen für Protokolle

## Phase 14 – Sicherheit und Netzwerk
- [ ] Fernsteuerung standardmäßig deaktiviert lassen
- [ ] Zugriff auf bekannte Geräte im lokalen Netzwerk beschränken
- [ ] Optionaler Zugriffsschutz mit Anmeldung
- [ ] Sitzungsschutz für Steuerbefehle
- [ ] Strikte Prüfung aller eingehenden Werte
- [ ] Schutz gegen wiederholte und veraltete Befehle
- [ ] Netzwerkzugriff und Fernsteuerung getrennt aktivieren
- [ ] Sicherheitsprüfung vor jeder neuen Steuerfunktion
- [ ] Keine unnötige Freigabe des Servers ins Internet
- [ ] Dokumentierte Notabschaltung der Steuerung

## Phase 15 – Export und Erweiterbarkeit
- [ ] Lokale JSON-API für weitere Dashboard-Komponenten
- [ ] Dokumentation aller verfügbaren Telemetriedaten
- [ ] Versionierung des Datenformats
- [ ] Kompatibilitätsprüfung nach FS25-Updates
- [ ] Import und Export von Dashboard-Konfigurationen
- [ ] Schnittstelle für eigene lokale Erweiterungen
- [ ] Export von Fahrten für externe Auswertungen
- [ ] Automatisierte Tests nach Änderungen am Mod
- [ ] Installations- und Updateprüfung verbessern
- [ ] Versionsübersicht für Mod, Server und Dashboard

## Phase 16 – Wartung und Betrieb
- [ ] Wartungsübersicht je Fahrzeug
- [ ] Erinnerungen anhand der erfassten Betriebsstunden
- [ ] Manuelle Wartungseinträge speichern
- [ ] Reparatur- und Wartungshistorie dokumentieren
- [ ] Wartungsdaten als Bericht exportieren
- [ ] Individuelle Wartungsintervalle konfigurierbar machen
- [ ] Optionaler Kostenüberblick für Wartungsarbeiten

Hinweis: Das Cockpit kann Wartungsintervalle berechnen und erinnern. Tatsächlicher Verschleiß, Ölzustand oder andere nicht verfügbare Spielwerte dürfen dabei nicht einfach erfunden werden.

## Phase 17 – Release 2.0
- [ ] Vollständiger Integrationstest mit mehreren Fahrzeugen
- [ ] Test mit Pflug, Anhänger, Ballenpresse und weiteren Geräten
- [ ] Mehrstündiger Dauerlauf mit PC und Android-Tablet
- [ ] Prüfung nach einem Neustart von Spiel und Server
- [ ] Wiederherstellung nach Verbindungsabbrüchen testen
- [ ] Steuerungsfunktionen auf einem Sicherungsspielstand testen
- [ ] Dokumentation für Installation und Fehlerbehebung erstellen
- [ ] Bekannte Einschränkungen dokumentieren
- [ ] Versionsnummern und Änderungsprotokoll pflegen
- [ ] Release-Paket mit Installationsprüfung erstellen
- [ ] Freigabe von Version 2.0 nach bestandenen Tests
