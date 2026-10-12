# Fehlersuche

Am schnellsten zuerst das Prüfskript laufen lassen:

```powershell
powershell -ExecutionPolicy Bypass -File tools\check_install.ps1
python tools\selftest.py
python tools\lua_check.py
```

## Dashboard zeigt „Keine Telemetrie“

1. LS25 starten, Mod beim Spielstand aktivieren, in ein Fahrzeug einsteigen.
2. `log.txt` nach `TRACTORCOCKPIT|` durchsuchen (Prüfskript macht das automatisch).
3. Zeile fehlt → Mod-Ladefehler im Log suchen (siehe unten).
4. Zeile vorhanden → Dashboard-Diagnose-Ecke prüfen: zeigt sie denselben Pfad?

## Dashboard zeigt „Daten veraltet“

Die letzte Telemetriezeile ist älter als `stale_seconds` (Standard 15 s).

* Läuft LS25 noch? Nach dem Beenden bleibt die letzte Zeile im Log stehen – der
  Server erkennt das korrekt als veraltet.
* Ist das Spiel pausiert (ESC-Menü)? Dann ruft LS25 `update()` nicht auf, es
  kommen also keine neuen Zeilen. Kurz weiterspielen.
* Steht der Spieler zu Fuß herum, sendet der Mod „Kein Fahrzeug gesteuert“ –
  das gilt als frisch, aber ohne Fahrzeugwerte.

## Welcher Kanal liefert gerade? (Datei oder Log)

In der Diagnose-Ecke des Dashboards steht unter **Kanal** entweder
„Telemetriedatei (schnell)“ oder „Logdatei (Rückfall)“. Der Server nimmt immer
den Kanal mit der frischesten Zeile.

* Steht dort dauerhaft „Logdatei (Rückfall)“, obwohl LS25 läuft, konnte der Mod
  die Telemetriedatei nicht schreiben. Im `log.txt` nach diesen Zeilen suchen:
  * `TractorCockpit: Datei-Kanal aktiv -> …` → alles in Ordnung
  * `TractorCockpit: Schreiben in … fehlgeschlagen` → keine Schreibrechte
  * `TractorCockpit: io.open nicht verfuegbar` → Laufzeit ohne Dateizugriff
* Die Telemetriedatei liegt hier:
  `%USERPROFILE%\Documents\My Games\FarmingSimulator2025\modSettings\FS25_TractorCockpit.txt`
  (bei dir: `C:\Users\anika\Dokumente\…`). Sie wird mehrmals pro Sekunde neu
  geschrieben – das ist normal.
* Der Mod funktioniert auch ohne Datei-Kanal vollständig, dann aber mit einer
  Logzeile pro Sekunde. Dauerhaft abschalten:
  `TractorCockpit.FILE_ENABLED = false` in `TractorCockpit.lua`.

## Logdatei wächst zu schnell

Mit dem Datei-Kanal schreibt der Mod nur noch **alle 10 s** eine Zeile ins Log
(vorher jede Sekunde, ca. 800 KB pro Stunde). Bleibt das Log trotzdem groß,
prüfe im Dashboard unter **Log-Wachstum**, ob ein anderer Mod die Ursache ist –
die Diagnose-Ecke zeigt die gemessene Wachstumsrate pro Stunde.

## Steuerung: Knöpfe fehlen oder sind grau

1. **Panel sagt „GESPERRT“** → der Server läuft ohne Freigabe. Mit
   `python server\server.py --enable-control` starten (oder
   `"control_enabled": true` in `config/settings.json` setzen).
2. **Panel sagt „AKTIV“, aber ein Gerät hat keine Knöpfe** → das Gerät kann laut
   Spiel nichts davon: `CanLower` kommt aus `getAllowsLowering()`,
   `CanFold`/`CanOn` nur, wenn das Gerät die passenden Funktionen hat. Ein
   Anhänger ohne Hebewerk bekommt also keine Heben/Senken-Knöpfe. Das ist Absicht.
3. **Knöpfe sind während eines Befehls grau** → es läuft noch ein Befehl. Nach
   spätestens 5 s kommt „timeout“ und es geht weiter.

## Steuerung: „Spielzustand noch nicht bestätigt“

Der Befehl wurde ausgelöst, aber der gemeldete Zustand passt noch nicht.
Häufigste Ursachen:

* Klappen dauert ein paar Sekunden (Animation) – kurz warten, die Anzeige
  aktualisiert sich von selbst.
* Das Gerät wird gerade blockiert (z. B. Klappen während der Fahrt nicht
  erlaubt) – im Dashboard steht dann der Grund aus dem Spiel.
* Das Spiel ist pausiert (ESC-Menü): dann läuft `update()` nicht und es kommt
  keine Rückmeldung → „timeout“.

## Steuerung: „io.open, only write mode ('w') is allowed“

Diese Warnung im `log.txt` bedeutet: **FS25 erlaubt Mods das Lesen von Dateien
nicht** – nur das Schreiben. Gelesen wird deshalb über die XML-API des Spiels
(`XMLFile.load`), und die Befehlsdatei ist eine XML-Datei.

Nach dem Laden steht im `log.txt`, was möglich ist:

* `TractorCockpit: Steuerkanal bereit -> <Pfad>` → Steuerung funktioniert.
* `TractorCockpit: Steuerkanal-Pfad nicht lesbar -> <Pfad>` → dieser Pfad ging
  nicht, der nächste wurde getestet.
* `TractorCockpit: kein lesbarer Steuerkanal gefunden` → in dieser
  Spielversion ist kein Rückkanal möglich; die Anzeige läuft weiter, die
  Knöpfe bleiben aus.

Im Dashboard steht derselbe Zustand: **AKTIV** oder **KEIN KANAL**.

Falls „KEIN KANAL“ erscheint: im `log.txt` nachsehen, welche Pfade getestet
wurden. Der zweite Kandidat ist der Spielstandordner des geladenen Spielstands
(`savegameN`) – dort schreibt das Spiel selbst XML-Dateien, dieser Pfad sollte
lesbar sein.

## Steuerung: Befehl kommt nicht an

* Prüfen, ob `<Spielprofil>\modSettings\FS25_TractorCockpit_cmd.txt` existiert
  und `TRACTORCMD|id=…|action=…|target=…|name=…` enthält. Schreibt der Server
  die Datei nicht, steht im Serverfenster eine Zeile mit „Schreiben
  fehlgeschlagen“.
* Im `log.txt` nach `TractorCockpit: Befehl` und nach
  `alter Befehl in der Befehlsdatei wird ignoriert` suchen.
* Steht dort `io.open nicht verfuegbar`, kann die Mod nicht lesen – dann bleibt
  nur die Anzeige.
* `TractorCockpit.CONTROL_ENABLED = false` in der Lua sperrt die Steuerung
  zusätzlich hart im Spiel.

## Steuerung: „Sharing violation on file … cmd.txt“ (alte Version)

Diese Meldung stammt aus der früheren Version, die eine **Textdatei** lesen
wollte. FS25 verbietet Mods das Lesen per `io.open`, deshalb konnte das nie
funktionieren. Ab 0.6 läuft der Rückkanal über XML (siehe oben). Alte
`…cmd.txt`-Dateien und `.tmp`-Reste räumt der Server beim Start mit
`--enable-control` automatisch weg.

## Verlauf/Diagramm bleibt leer

* Das Diagramm braucht ein paar Minuten Daten – der Server schreibt nur bei
  Änderung oder als Heartbeat (max. alle 5 s).
* Prüfen, ob die Datenbank nutzbar ist: in der Diagnose-Ecke steht unter
  **Datenbank** entweder die Zahl der Messungen oder ein Fehler. Eine
  Meldung beim Start zeigt denselben Zustand.
* Verlauf absichtlich abschalten: `--no-history`. Anderer Ort:
  `--database "D:\pfad\history.sqlite3"`.

## Vor dem Testen von Befehlen

```powershell
powershell -ExecutionPolicy Bypass -File tools\backup_savegame.ps1
```

Sichert den zuletzt gespielten Spielstand nach `savegameBackup`. Zum
Zurückholen LS25 beenden, den aktuellen `savegameN`-Ordner umbenennen und die
Sicherung wieder als `savegameN` kopieren.

## Licht: Knöpfe fehlen oder sind grau

* **Kein Knopf für eine Lichtart** → dieses Fahrzeug hat sie nicht. Der Mod
  sammelt beim Laden, welche Lichtarten in den Lampen des Fahrzeugs wirklich
  vorkommen, und meldet nur diese. Ein Traktor ohne Dachscheinwerfer bekommt
  also keinen „Arbeitslicht“-Knopf.
* **Alle Knöpfe grau, Hinweis „Licht kann gerade nicht geschaltet werden“** →
  `Lights:getCanToggleLight()` ist im Spiel falsch. Das ist der Fall, wenn du
  nicht im Fahrzeug sitzt oder ein Helfer (KI) fährt. Einsteigen bzw. Helfer
  stoppen.
* **Knopf reagiert, Zustand bleibt aber aus** → im Spiel ist das Schalten
  blockiert (z. B. Licht während des Starts). Im Steuerungs-Tab steht dann der
  Grund aus dem Spiel.
* **Blinker geht nicht** → manche Fahrzeuge haben keine Blinker-Meshes; dann
  meldet der Mod „keine Blinker“ und es erscheinen keine Blinker-Knöpfe.
* Grundsatz: die Knöpfe sind nur grün, wenn **das Spiel** die Lampe als
  eingeschaltet meldet – nie aus Optimismus nach dem Klick.

## Leistung: ist der Server schnell genug?

```powershell
python tools\benchmark.py
```

Misst Erfassung, Datenbank und HTTP mit echten Datenmengen und zeigt die
gemessenen Zeiten. Im Dashboard stehen dieselben Werte unter **Diagnose →
Leistung** (Erfassungszeit, Antwortgröße, API-Anfragen).

Wenn die Erfassungszeit deutlich über ~10 ms liegt, prüfen:

* Läuft die Logdatei auf einem langsamen Laufwerk oder Netzwerkpfad?
* Ist die Datenbank sehr groß geworden? `history_retention_days` und
  `history_max_rows` in `config/settings.json` begrenzen das (Standard: 7 Tage,
  500 000 Zeilen). Beim Start wird automatisch aufgeräumt.
* Verlauf abschalten: `--no-history`.

## Mod wird nicht geladen

* Prüfen, ob `Load mod: FS25_TractorCockpit` im Log steht.
* `ModDesc Version` im Log mit `descVersion` in `modDesc.xml` vergleichen.
  Für FS25 1.24 gilt **114**.
* Das ZIP muss `modDesc.xml` **direkt im Hauptordner** enthalten, nicht in
  einem Unterordner. `tools\install_mod.ps1` baut es korrekt.
* **Nicht** gleichzeitig Ordner und ZIP im `mods`-Ordner haben – sonst lädt der
  Mod doppelt.
* Nach dem Aktualisieren des Mods LS25 **komplett neu starten**.

## „Logdatei nicht gefunden“ / falscher Pfad

Der Ordner heißt je nach Windows-Sprache `Documents` oder `Dokumente` und kann
nach OneDrive umgeleitet sein. Der Server sucht selbst und nutzt den
Registry-Eintrag
`HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders` →
`Personal`.

Wenn das nicht reicht, in `config/settings.json` den echten Pfad eintragen, z. B.:

```json
"log_path": "C:\\Users\\anika\\Dokumente\\My Games\\FarmingSimulator2025\\log.txt"
```

Hinweis: Der Schlüssel `Shell Folders` (ohne „User“) ist auf manchen Systemen
veraltet und zeigt auf einen falschen Ordner – deshalb nutzt der Server ihn nur
als letzte Rückfallebene.

## Einige Werte sind `--` oder `n/a`

`n/a` heißt: die abgefragte Funktion hat für dieses Fahrzeug keinen Wert
geliefert. Das ist kein Beweis, dass der Wert nicht existiert. Geprüft sind:

* `getLastSpeed()` → km/h
* `getMotorRpmReal()` → U/min
* `getIsMotorStarted()` → true/false
* `getConsumerFillUnitIndex(FillType.DIESEL/ELECTRICCHARGE/METHANE)` → Liter
* `getOperatingTime()` → Millisekunden

Reiht ein Mod-Fahrzeug hier etwas anderes ein, greifen die Rückfallebenen in
`TractorCockpit.lua`.

## Tablet kann den Server nicht erreichen

* Laptop und Tablet im selben WLAN; am Laptop `http://127.0.0.1:8765` testen.
* Die LAN-IP wird beim Serverstart angezeigt – benutze sie am Tablet.
* Windows-Netzwerkprofil auf „Privat“ stellen, Python nur für private Netze
  erlauben.
* Gäste-WLAN und „Client Isolation“ blockieren Verbindungen zwischen Geräten.
* Keine Router-Portfreigabe einrichten.

## Dashboard zeigt alte Werte nach einer Änderung

Sollte nicht passieren: HTML, CSS und JavaScript werden mit
`Cache-Control: no-store` ausgeliefert. Wenn doch, am Tablet die Seite neu laden.

## Server startet nicht (Port belegt)

```powershell
python server\server.py --port 8766
```

## Mod aktualisieren oder zurückrollen

```powershell
powershell -ExecutionPolicy Bypass -File tools\install_mod.ps1
```

Die vorherige Mod-Version liegt als `backup\FS25_TractorCockpit_vorher.zip`
im Projekt. Zum Zurückrollen diese Datei einfach wieder als
`FS25_TractorCockpit.zip` in den `mods`-Ordner kopieren und LS25 neu starten.

## Prüfskript meldet „ZIP nicht lesbar“

Das ZIP ist beschädigt oder wurde manuell falsch gepackt. Neu bauen:

```powershell
powershell -ExecutionPolicy Bypass -File tools\install_mod.ps1
```
