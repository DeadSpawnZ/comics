<#
.SYNOPSIS
    Genera un respaldo de la base de datos (via Django dumpdata) dentro del
    contenedor de la app y lo copia al equipo local.

.DESCRIPTION
    1. Verifica que el servicio de docker compose indicado este corriendo.
    2. Ejecuta dump.py dentro del contenedor con un nombre de archivo fijo.
    3. Copia el dump resultante a la carpeta local de respaldos.
    4. Borra el archivo temporal dentro del contenedor.
    5. Rota los respaldos locales, conservando solo los mas recientes.

.PARAMETER OutputDir
    Carpeta local donde se guardan los respaldos. Por defecto ".\backups".

.PARAMETER Keep
    Cuantos respaldos locales conservar (los mas antiguos se borran).
    Usa 0 para conservarlos todos.

.PARAMETER Service
    Nombre del servicio de docker compose que corre Django. Por defecto "web".

.EXAMPLE
    .\scripts\backup-db.ps1

.EXAMPLE
    .\scripts\backup-db.ps1 -OutputDir D:\backups\comi -Keep 20
#>
[CmdletBinding()]
param(
    [string]$OutputDir = (Join-Path $PSScriptRoot "..\backups"),
    [int]$Keep = 10,
    [string]$Service = "web"
)

$ErrorActionPreference = "Stop"

function Fail([string]$Message) {
    Write-Error $Message
    exit 1
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $RepoRoot
try {
    $runningServices = docker compose ps --status running --services 2>$null
    if (-not $runningServices -or -not ($runningServices -split "`r?`n" | Where-Object { $_.Trim() -eq $Service })) {
        Fail "El servicio '$Service' no esta corriendo. Levanta el proyecto con 'docker compose up -d' e intenta de nuevo."
    }

    if (-not (Test-Path $OutputDir)) {
        New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
    }
    $OutputDir = (Resolve-Path $OutputDir).Path

    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $remoteFile = "/code/dump-$timestamp.json"
    $localFile = Join-Path $OutputDir "dump-$timestamp.json"

    Write-Host "Generando dump dentro del contenedor '$Service'..."
    docker compose exec -T $Service python dump.py $remoteFile
    if ($LASTEXITCODE -ne 0) {
        Fail "Fallo al generar el dump dentro del contenedor (codigo $LASTEXITCODE)."
    }

    Write-Host "Copiando dump a $localFile ..."
    docker compose cp "${Service}:${remoteFile}" $localFile
    if ($LASTEXITCODE -ne 0) {
        Fail "Fallo al copiar el dump del contenedor al equipo local."
    }

    if (-not (Test-Path $localFile) -or (Get-Item $localFile).Length -eq 0) {
        Fail "El archivo copiado no existe o esta vacio: $localFile"
    }

    docker compose exec -T $Service rm -f $remoteFile | Out-Null

    $sizeKB = [math]::Round((Get-Item $localFile).Length / 1KB, 1)
    Write-Host "Listo. Respaldo guardado en: $localFile ($sizeKB KB)" -ForegroundColor Green

    if ($Keep -gt 0) {
        $old = Get-ChildItem $OutputDir -Filter "dump-*.json" |
            Sort-Object LastWriteTime -Descending |
            Select-Object -Skip $Keep
        foreach ($f in $old) {
            Write-Host "Eliminando respaldo antiguo: $($f.Name)"
            Remove-Item $f.FullName -Force
        }
    }
}
finally {
    Pop-Location
}
