# Локальное развёртывание Neo4j Community на Windows без Docker.
# Скачивает zip-дистрибутив (с бандлированным JDK), распаковывает в .neo4j/,
# задаёт начальный пароль из .env и устанавливает службу Windows.
#
# Запуск из корня проекта:  powershell -File infra\neo4j-local\install.ps1

$ErrorActionPreference = "Stop"
$Version = "2026.05.0"
$Root = ".neo4j"
$Home_ = Join-Path $Root "neo4j-community-$Version"
$Zip = Join-Path $Root "neo4j-community-$Version-windows.zip"

# пароль берём из .env (NEO4J_PASSWORD=...)
$envLine = Select-String -Path ".env" -Pattern '^NEO4J_PASSWORD=(.+)$'
if (-not $envLine) { throw "Заполните NEO4J_PASSWORD в .env (см. .env.example)" }
$Password = $envLine.Matches[0].Groups[1].Value

if (-not (Test-Path $Home_)) {
    New-Item -ItemType Directory -Force $Root | Out-Null
    if (-not (Test-Path $Zip)) {
        Invoke-WebRequest -Uri "https://dist.neo4j.org/neo4j-community-$Version-windows.zip" -OutFile $Zip
    }
    Expand-Archive -Path $Zip -DestinationPath $Root -Force
}

& "$Home_\bin\neo4j-admin.bat" dbms set-initial-password $Password
Write-Host "Готово. Запуск в консоли:  $Home_\bin\neo4j.bat console"
Write-Host "Либо службой:             $Home_\bin\neo4j.bat windows-service install; neo4j.bat start"
