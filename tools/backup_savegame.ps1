<#
.SYNOPSIS
    Sichert einen LS25-Spielstand, bevor Steuerbefehle getestet werden.

.DESCRIPTION
    Kopiert einen Spielstandordner (Standard: der zuletzt geaenderte) in den
    Ordner savegameBackup des Spielprofils, mit Zeitstempel im Namen. Damit ist
    ein Test der Steuerung jederzeit zurueckholbar.

    Der Spielstand wird NICHT ueberschrieben. Zum Zurueckholen einfach den
    gewuenschten Ordner wieder als savegameN kopieren (Spiel vorher beenden).

.PARAMETER Savegame
    Nummer des Spielstands, z.B. 6. Standard: der zuletzt geaenderte.

.PARAMETER Destination
    Zielordner. Standard: <Spielprofil>\savegameBackup

.PARAMETER WhatIf
    Nur anzeigen, was kopiert wuerde.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\backup_savegame.ps1
    powershell -ExecutionPolicy Bypass -File tools\backup_savegame.ps1 -Savegame 6
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [int]$Savegame = 0,
    [string]$Destination = ""
)

$ErrorActionPreference = 'Stop'

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

$profileFolder = Join-Path (Get-DocumentsFolder) 'My Games\FarmingSimulator2025'
if (-not (Test-Path -LiteralPath $profileFolder)) {
    throw "Spielprofil nicht gefunden: $profileFolder"
}

if (-not $Destination) {
    $Destination = Join-Path $profileFolder 'savegameBackup'
}

$savegames = Get-ChildItem -LiteralPath $profileFolder -Directory |
    Where-Object { $_.Name -match '^savegame\d+$' } |
    Sort-Object LastWriteTime -Descending

if ($savegames.Count -eq 0) {
    throw "Kein Spielstand (savegameN) in $profileFolder gefunden."
}

if ($Savegame -gt 0) {
    $source = Join-Path $profileFolder "savegame$Savegame"
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Spielstand nicht gefunden: $source"
    }
} else {
    $source = $savegames[0].FullName
    Write-Host "Zuletzt gespielter Spielstand: $(Split-Path -Leaf $source) (geaendert $($savegames[0].LastWriteTime))"
}

$stamp = Get-Date -Format 'yyyy-MM-dd_HH-mm-ss'
$target = Join-Path $Destination ("{0}_{1}" -f (Split-Path -Leaf $source), $stamp)

if ($PSCmdlet.ShouldProcess($target, "Spielstand sichern")) {
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    Copy-Item -Path (Join-Path $source '*') -Destination $target -Recurse -Force
    $files = (Get-ChildItem -LiteralPath $target -Recurse -File).Count
    Write-Host "Gesichert: $target ($files Dateien)"
    Write-Host ''
    Write-Host 'Zurueckholen (LS25 vorher beenden):'
    Write-Host "  1. aktuellen Ordner $source umbenennen, z.B. in $(Split-Path -Leaf $source)_kaputt"
    Write-Host "  2. $target nach $source kopieren"
}
