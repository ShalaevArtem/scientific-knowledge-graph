# Offline dump of the local Neo4j database (Community, Windows) for deployment.
# Stops the server -> dumps to infra/neo4j-dump/neo4j.dump -> starts the server back.
# The dump is loaded into the containerized Neo4j on the stand
# (see infra/neo4j-dump/README.md).
#
# Run from the project root:  powershell -File infra\dump.ps1
#
# NOTE: ASCII-only on purpose. Windows PowerShell 5.1 reads .ps1 as ANSI/cp1251,
# so non-ASCII text requires a UTF-8 BOM. Keeping it ASCII avoids that fragility.

$ErrorActionPreference = "Stop"
$Home_  = ".neo4j\neo4j-community-2026.05.0"
$Admin  = Join-Path $Home_ "bin\neo4j-admin.bat"
$Neo    = Join-Path $Home_ "bin\neo4j.bat"
$OutDir = "infra\neo4j-dump"
New-Item -ItemType Directory -Force $OutDir | Out-Null
# neo4j-admin resolves a relative --to-path against NEO4J_HOME, not the CWD -> use absolute
$OutDir = (Resolve-Path $OutDir).Path

Write-Host "[dump] stopping Neo4j..."
try { & $Neo stop } catch { Write-Warning "neo4j stop returned an error: $_ (will keep waiting for the port)" }

# wait for bolt port 7687 to close (server fully stopped)
$up = $true
for ($i = 0; $i -lt 30; $i++) {
    $up = Test-NetConnection -ComputerName localhost -Port 7687 -WarningAction SilentlyContinue -InformationLevel Quiet
    if (-not $up) { break }
    Start-Sleep -Seconds 2
}
if ($up) {
    throw ("Neo4j still listens on 7687. If it was started with 'neo4j.bat console', " +
           "'neo4j stop' does not manage it - press Ctrl+C in that console window to stop it " +
           "gracefully, then re-run this script. A graceful stop is required for a consistent dump.")
}

if (Test-Path "$OutDir\neo4j.dump") { Remove-Item "$OutDir\neo4j.dump" -Force }
Write-Host "[dump] dumping database 'neo4j'..."
& $Admin database dump neo4j --to-path="$OutDir"

Write-Host "[dump] starting Neo4j back (console, hidden window)..."
Start-Process -FilePath $Neo -ArgumentList "console" -WindowStyle Hidden

$f = Get-ChildItem "$OutDir\neo4j.dump" -ErrorAction SilentlyContinue
if ($f) { Write-Host ("[dump] done: {0} ({1:N2} GB)" -f $f.FullName, ($f.Length / 1GB)) }
else { throw "Dump was not created" }
