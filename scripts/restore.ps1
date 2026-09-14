param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile,

    [switch]$ConfirmRestore
)

$ErrorActionPreference = "Stop"

if (-not $ConfirmRestore) {
    throw "Geri yükleme mevcut veritabanı nesnelerini değiştirir. Devam etmek için -ConfirmRestore parametresini kullanın."
}

$resolvedBackup = (Resolve-Path -LiteralPath $BackupFile).Path
if (-not (Test-Path -LiteralPath $resolvedBackup -PathType Leaf)) {
    throw "Yedek dosyası bulunamadı."
}

$backupDirectory = Split-Path -Parent $resolvedBackup
$backupName = Split-Path -Leaf $resolvedBackup

docker compose run --rm --no-deps `
    -e "BACKUP_FILE=$backupName" `
    -v "${backupDirectory}:/backup:ro" `
    db sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore -h db -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges "/backup/$BACKUP_FILE"'

if ($LASTEXITCODE -ne 0) {
    throw "Veritabanı geri yüklenemedi."
}

Write-Host "Veritabanı geri yüklendi: $resolvedBackup"
