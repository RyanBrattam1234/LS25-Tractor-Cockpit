<#
.SYNOPSIS
    Baut FS25_TractorCockpit.zip aus mod\FS25_TractorCockpit und installiert es.

.DESCRIPTION
    Erstellt das Mod-ZIP neu (modDesc.xml liegt direkt im ZIP-Hauptordner, wie
    es LS25 erwartet) und kopiert es in den mods-Ordner des Spiels. Der
    Dokumente-Ordner wird automatisch gefunden, auch bei deutschem
    "Dokumente"-Namen oder OneDrive-Umleitung.

.PARAMETER NoInstall
    Nur bauen, nicht in den mods-Ordner kopieren.

.PARAMETER Force
    Vorhandenes ZIP im mods-Ordner ohne Rueckfrage ersetzen.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\install_mod.ps1
#>
[CmdletBinding()]
param(
    [switch]$NoInstall,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Get-DocumentsFolder {
    try {
        $value = (Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders' -Name 'Personal' -ErrorAction Stop).Personal
        if ($value) { return [Environment]::ExpandEnvironmentVariables($value) }
    } catch { }
    foreach ($name in @('Documents', 'Dokumente')) {
        $candidate = Join-Path $env:USERPROFILE $name
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return (Join-Path $env:USERPROFILE 'Documents')
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $projectRoot 'mod\FS25_TractorCockpit'
$zipPath = Join-Path $projectRoot 'FS25_TractorCockpit.zip'

if (-not (Test-Path -LiteralPath (Join-Path $source 'modDesc.xml'))) {
    throw "modDesc.xml nicht gefunden in $source"
}

# ZIP mit ausdruecklichen Forward-Slash-Pfaden bauen. CreateFromDirectory
# schreibt unter .NET Framework teils Backslashes, die LS25 nicht liest.
if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }

$archive = [System.IO.Compression.ZipFile]::Open($zipPath, 'Create')
try {
    $baseLength = $source.TrimEnd('\').Length
    $files = Get-ChildItem -LiteralPath $source -Recurse -File |
        Where-Object { $_.Name -notmatch '^(\.DS_Store|Thumbs\.db)$' -and $_.Extension -ne '.log' } |
        Sort-Object FullName

    foreach ($file in $files) {
        $relative = $file.FullName.Substring($baseLength + 1).Replace('\', '/')
        $entry = $archive.CreateEntry($relative, [System.IO.Compression.CompressionLevel]::Optimal)
        $stream = $entry.Open()
        try {
            $bytes = [System.IO.File]::ReadAllBytes($file.FullName)
            $stream.Write($bytes, 0, $bytes.Length)
        } finally {
            $stream.Dispose()
        }
        Write-Host "  + $relative"
    }
} finally {
    $archive.Dispose()
}

$built = Get-Item -LiteralPath $zipPath
Write-Host "Gebaut: $zipPath ($($built.Length) Bytes)"

if ($NoInstall) {
    Write-Host 'Uebersprungen: Installation in den mods-Ordner (-NoInstall).'
    return
}

$modsFolder = Join-Path (Get-DocumentsFolder) 'My Games\FarmingSimulator2025\mods'
if (-not (Test-Path -LiteralPath $modsFolder)) {
    New-Item -ItemType Directory -Path $modsFolder -Force | Out-Null
    Write-Host "mods-Ordner angelegt: $modsFolder"
}

$unpacked = Join-Path $modsFolder 'FS25_TractorCockpit'
if (Test-Path -LiteralPath $unpacked) {
    Write-Host "Hinweis: entpackter Ordner gefunden ($unpacked)."
    Write-Host '         Er wird nach Rueckfrage entfernt, damit der Mod nicht doppelt laedt.'
    if ($Force -or (Read-Host 'Entfernen? (j/n)') -eq 'j') {
        Remove-Item -LiteralPath $unpacked -Recurse -Force
        Write-Host 'Entfernt.'
    } else {
        Write-Host 'Beibehalten - Achtung, moegliche Doppelladung.'
    }
}

$target = Join-Path $modsFolder 'FS25_TractorCockpit.zip'
if ((Test-Path -LiteralPath $target) -and -not $Force) {
    $answer = Read-Host "Vorhandenes ZIP ersetzen? ($target) (j/n)"
    if ($answer -ne 'j') {
        Write-Host 'Abgebrochen - nichts kopiert.'
        return
    }
}

Copy-Item -LiteralPath $zipPath -Destination $target -Force
$copied = Get-Item -LiteralPath $target
$hash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
Write-Host "Installiert: $target ($($copied.Length) Bytes)"
Write-Host "SHA256:      $hash"
Write-Host ''
Write-Host 'Naechster Schritt: LS25 starten, Spielstand laden, in ein Fahrzeug einsteigen.'
Write-Host 'Danach: python server\server.py'
