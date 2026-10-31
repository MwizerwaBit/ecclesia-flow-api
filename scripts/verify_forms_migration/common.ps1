$ErrorActionPreference = "Continue"
$api = (Resolve-Path "$PSScriptRoot\..\..").Path
$mig = Join-Path $PSScriptRoot ""
Set-Location $api
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"; $env:PATH = "$api\.venv\Scripts;$env:PATH"
$url = (Get-Content "$api\.env" | Select-String "^DATABASE_URL=" | ForEach-Object { $_.Line.Split('=',2)[1] })
$m = [regex]::Match($url, 'postgresql://([^:]+):([^@]*)@([^:/]+):(\d+)/(.+)')
$env:PGPASSWORD = $m.Groups[2].Value
$script:pg = @("-h",$m.Groups[3].Value,"-p",$m.Groups[4].Value,"-U",$m.Groups[1].Value)
$script:devDb = $m.Groups[5].Value
$script:baseUrl = "postgresql://$($m.Groups[1].Value):$($m.Groups[2].Value)@$($m.Groups[3].Value):$($m.Groups[4].Value)"
function Run-Sql([string]$db, [string]$file, [switch]$stop) {
  $args2 = @() + $script:pg + @("-d", $db, "-X", "-f", $file)
  if ($stop) { $args2 += @("-v", "ON_ERROR_STOP=1") }
  & psql @args2 2>&1 | ForEach-Object { "$_" }
}
function New-Db([string]$name) {
  $f = Join-Path $env:TEMP "mkdb_$name.sql"
  Set-Content -Path $f -Value "DROP DATABASE IF EXISTS $name;`nCREATE DATABASE $name;" -Encoding ascii
  & psql @script:pg -d postgres -X -f $f 2>&1 | Out-Null
}
function Drop-Db([string]$name) {
  $f = Join-Path $env:TEMP "dropdb_$name.sql"
  Set-Content -Path $f -Value "DROP DATABASE IF EXISTS $name;" -Encoding ascii
  & psql @script:pg -d postgres -X -f $f 2>&1 | Out-Null
}
# Prisma deploy of every migration except the forms one (a copy of the prisma folder), or of all of them.
function Deploy([string]$name, [switch]$withoutForms) {
  $env:DATABASE_URL = "$($script:baseUrl)/$name"
  if ($withoutForms) {
    $tmp = Join-Path $env:TEMP "forms_prisma_copy_$name"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    New-Item -ItemType Directory $tmp | Out-Null
    Copy-Item -Recurse "$api\prisma" "$tmp\prisma"
    Remove-Item -Recurse -Force "$tmp\prisma\migrations\20261031090000_forms_module"
    Push-Location $tmp
    & prisma migrate deploy --schema "$tmp\prisma\schema.prisma" 2>&1 | Select-Object -Last 2 | ForEach-Object { "$_" }
    Pop-Location
    Remove-Item -Recurse -Force $tmp
  } else {
    Push-Location $api
    & prisma migrate deploy 2>&1 | Select-Object -Last 2 | ForEach-Object { "$_" }
    Pop-Location
  }
}
