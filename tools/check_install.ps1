<#
.SYNOPSIS
    Prueft die Installation des LS25 Tractor Cockpit (nur lesend).

.DESCRIPTION
    Kontrolliert Python, den LS25-Spielordner, den Mod im mods-Ordner, die
    Logdatei und die Telemetriezeilen. Findet auch bei deutschem
    "Dokumente"-Ordner oder OneDrive-Umleitung den richtigen Pfad.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\check_install.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'

function Get-DocumentsFolder {
    # Der Ordner heisst je nach Windows-Sprache "Documents" oder "Dokumente"
    # und kann nach OneDrive umgeleitet sein.
    try {
        $value = (Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders' -Name 'Personal' -ErrorAction Stop).Personal
        if ($value) {
            return [Environment]::ExpandEnvironmentVariables($value)
        }
    } catch { }
    foreach ($name in @('Documents', 'Dokumente')) {
        $candidate = Join-Path $env:USERPROFILE $name
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return (Join-Path $env:USERPROFILE 'Documents')
}

function Write-Status {
    param([string]$State, [string]$Text)
    $marker = switch ($State) {
        'ok'   { '[ OK ]' }
        'warn' { '[WARN]' }
        default { '[FEHL]' }
    }
    Write-Host "$marker $Text"
}

$documents = Get-DocumentsFolder
$gameFolder = Join-Path $documents 'My Games\FarmingSimulator2025'
$logPath = Join-Path $gameFolder 'log.txt'
$modsFolder = Join-Path $gameFolder 'mods'
$projectRoot = Split-Path -Parent $PSScriptRoot
$repoMod = Join-Path $projectRoot 'mod\FS25_TractorCockpit'
$repoZip = Join-Path $projectRoot 'FS25_TractorCockpit.zip'
$installedZip = Join-Path $modsFolder 'FS25_TractorCockpit.zip'
$installedFolder = Join-Path $modsFolder 'FS25_TractorCockpit'

$problems = 0

Write-Host '=== Python ==='
try {
    $pythonVersion = (& python --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -eq 0) { Write-Status ok "python: $pythonVersion" }
    else { Write-Status fehl 'python nicht gefunden. Installation von python.org noetig.'; $problems++ }
} catch {
    Write-Status fehl "python nicht gefunden: $($_.Exception.Message)"
    $problems++
}

Write-Host ''
Write-Host '=== LS25-Spielordner ==='
Write-Host "Dokumente-Ordner: $documents"
if (Test-Path -LiteralPath $gameFolder) {
    Write-Status ok "Spielordner vorhanden: $gameFolder"
    $versionFile = Join-Path $gameFolder 'VERSION'
    if (Test-Path -LiteralPath $versionFile) {
        Write-Host "         Spielversion: $((Get-Content -LiteralPath $versionFile -Raw).Trim())"
    }
} else {
    Write-Status fehl "Spielordner fehlt: $gameFolder (LS25 einmal starten)"
    $problems++
}

Write-Host ''
Write-Host '=== Mod im mods-Ordner ==='
if (Test-Path -LiteralPath $installedFolder) {
    Write-Status warn "Zusaetzlich liegt ein entpackter Ordner in mods: $installedFolder"
    Write-Host '         Bitte entfernen - sonst laedt LS25 den Mod doppelt.'
    $problems++
}
if (Test-Path -LiteralPath $installedZip) {
    $zipInfo = Get-Item -LiteralPath $installedZip
    Write-Status ok "Installiert: FS25_TractorCockpit.zip ($($zipInfo.Length) Bytes, $($zipInfo.LastWriteTime))"
    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [System.IO.Compression.ZipFile]::OpenRead($installedZip)
        $hasModDesc = ($archive.Entries | Where-Object { $_.FullName -eq 'modDesc.xml' }).Count -gt 0
        $hasScript = ($archive.Entries | Where-Object { $_.FullName -eq 'scripts/TractorCockpit.lua' }).Count -gt 0
        $archive.Dispose()
        if ($hasModDesc -and $hasScript) {
            Write-Status ok 'ZIP-Struktur korrekt (modDesc.xml im Hauptordner)'
        } else {
            Write-Status fehl 'ZIP-Struktur falsch: modDesc.xml muss direkt im ZIP liegen'
            $problems++
        }
    } catch {
        Write-Status fehl "ZIP nicht lesbar: $($_.Exception.Message)"
        $problems++
    }

    if (Test-Path -LiteralPath $repoZip) {
        $installedHash = (Get-FileHash -LiteralPath $installedZip -Algorithm SHA256).Hash
        $repoHash = (Get-FileHash -LiteralPath $repoZip -Algorithm SHA256).Hash
        if ($installedHash -eq $repoHash) {
            Write-Status ok 'Installierter Mod ist identisch mit dem Projekt-ZIP'
        } else {
            Write-Status warn 'Installierter Mod weicht vom Projekt-ZIP ab'
            Write-Host '         Aktualisieren: powershell -ExecutionPolicy Bypass -File tools\install_mod.ps1'
        }
    }
} else {
    Write-Status fehl 'FS25_TractorCockpit.zip liegt nicht im mods-Ordner'
    Write-Host '         Installieren: powershell -ExecutionPolicy Bypass -File tools\install_mod.ps1'
    $problems++
}

Write-Host ''
Write-Host '=== Logdatei und Telemetrie ==='
if (-not (Test-Path -LiteralPath $logPath)) {
    Write-Status fehl "Keine Logdatei: $logPath (LS25 einmal starten und beenden)"
    $problems++
} else {
    Write-Status ok "Logdatei: $logPath"

    $modDescLine = Select-String -LiteralPath $logPath -Pattern 'ModDesc Version' | Select-Object -Last 1
    if ($modDescLine) { Write-Host "         $($modDescLine.Line.Trim())" }

    $loaded = Select-String -LiteralPath $logPath -Pattern 'Load mod: FS25_TractorCockpit' | Select-Object -Last 1
    if ($loaded) { Write-Status ok 'Mod wurde vom Spiel geladen' }
    else { Write-Status warn 'Kein "Load mod: FS25_TractorCockpit" im Log gefunden' }

    $loadErrors = Select-String -LiteralPath $logPath -Pattern 'TractorCockpit.*(Error|Warning)' |
        Where-Object { $_.Line -notmatch 'ForceFeedback' } | Select-Object -Last 5
    if ($loadErrors) {
        Write-Status warn 'Meldungen zum Mod im Log:'
        $loadErrors | ForEach-Object { Write-Host "         $($_.Line.Trim())" }
    }

    $telemetry = Select-String -LiteralPath $logPath -Pattern 'TRACTORCOCKPIT\|'
    if ($telemetry) {
        Write-Status ok "Telemetriezeilen gefunden: $($telemetry.Count)"
        Write-Host '         Letzte Zeilen:'
        $telemetry | Select-Object -Last 3 | ForEach-Object { Write-Host "         $($_.Line.Trim())" }

        $lastStamp = $null
        if ($telemetry[-1].Line -match '^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})') {
            try {
                $lastStamp = [datetime]::ParseExact($Matches[1], 'yyyy-MM-dd HH:mm:ss.fff', $null)
                $age = (Get-Date) - $lastStamp
                if ($age.TotalSeconds -lt 15) {
                    Write-Status ok ("Letzte Zeile ist {0:N1} s alt - Spiel laeuft gerade" -f $age.TotalSeconds)
                } else {
                    Write-Host ("         Letzte Zeile ist {0:N1} Minuten alt - Spiel laeuft gerade nicht." -f $age.TotalMinutes)
                }
            } catch { }
        }
    } else {
        Write-Status warn 'Keine TRACTORCOCKPIT|-Zeile im Log'
        Write-Host '         Mod im Spielstand aktivieren und in ein Fahrzeug einsteigen.'
        $problems++
    }
}

Write-Host ''
Write-Host '=== Server ==='
$listening = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Status ok 'Port 8765 ist belegt - der Server laeuft vermutlich'
    Write-Host '         Laptop: http://127.0.0.1:8765'
} else {
    Write-Host '         Port 8765 ist frei (Server laeuft nicht).'
    Write-Host '         Starten: python server\server.py'
}

Write-Host ''
Write-Host '=== Mod-Dateien im Projekt ==='
try {
    & python (Join-Path $projectRoot 'tools\lua_check.py')
    if ($LASTEXITCODE -eq 0) { Write-Status ok 'Lua-Strukturpruefung bestanden' }
    else { Write-Status fehl 'Lua-Strukturpruefung fehlgeschlagen'; $problems++ }
} catch {
    Write-Status fehl "Lua-Pruefung nicht ausfuehrbar: $($_.Exception.Message)"
    $problems++
}

Write-Host ''
Write-Host '=== Selbsttest des Servers ==='
try {
    & python (Join-Path $projectRoot 'tools\selftest.py') 2>&1 | ForEach-Object { Write-Host "         $_" }
    if ($LASTEXITCODE -eq 0) { Write-Status ok 'Server-Selbsttest bestanden' }
    else { Write-Status fehl 'Server-Selbsttest fehlgeschlagen'; $problems++ }
} catch {
    Write-Status fehl "Selbsttest nicht ausfuehrbar: $($_.Exception.Message)"
    $problems++
}

Write-Host ''
if ($problems -eq 0) {
    Write-Host 'Ergebnis: alles bereit. Server starten mit: python server\server.py' -ForegroundColor Green
} else {
    Write-Host "Ergebnis: $problems Punkt(e) pruefen (siehe oben)." -ForegroundColor Yellow
}
