param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "..\backups")
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$resolvedOutput = (Resolve-Path -LiteralPath $OutputDirectory).Path
$backupFile = "akaryakit-{0}.dump" -f (Get-Date -Format "yyyyMMdd-HHmmss")

docker compose run --rm --no-deps `
    -e "BACKUP_FILE=$backupFile" `
    -v "${resolvedOutput}:/backup" `
    db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h db -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f "/backup/$BACKUP_FILE"'

if ($LASTEXITCODE -ne 0) {
    throw "Veritabanı yedeği oluşturulamadı."
}

$backupPath = Join-Path $resolvedOutput $backupFile
if (-not (Test-Path -LiteralPath $backupPath)) {
    throw "Docker komutu tamamlandı ancak yedek dosyası bulunamadı."
}

Get-Item -LiteralPath $backupPath
