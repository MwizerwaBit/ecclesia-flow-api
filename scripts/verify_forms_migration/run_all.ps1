. "$PSScriptRoot\common.ps1"
$mig = $PSScriptRoot
$aids = "$api\docs\migration-aids"
$migSql = "$api\prisma\migrations\20261031090000_forms_module\migration.sql"
$backfillSql = "$api\prisma\migrations\20261031090100_forms_backfill\migration.sql"
$out = New-Object System.Collections.Generic.List[string]
function Say($t) { $out.Add($t); Write-Host $t }
function Section($t) { Say ""; Say ("#" * 3 + " " + $t) }
function Timed([string]$db, [string]$file) {
  $wrap = Join-Path $env:TEMP "timed_$([guid]::NewGuid().ToString('N')).sql"
  $fwd = $file.Replace('\','/')
  Set-Content -Path $wrap -Value "\timing on`n\i '$fwd'" -Encoding ascii
  $lines = Run-Sql $db $wrap -stop
  $ms = 0.0
  foreach ($l in $lines) { if ($l -match "^Time: ([0-9.]+) ms") { $ms += [double]$Matches[1] } elseif ($l -match "ERROR") { Say "    $l" } }
  Say ("    ran in {0:N1} s ({1})" -f ($ms / 1000), (Split-Path $file -Leaf))
}
function Counts([string]$db) { (Run-Sql $db "$mig\counts.sql" | Out-String).Trim() }

# ───────────────────────── A. clean database ─────────────────────────
Section "A. Empty database: every migration in order"
New-Db "forms_v_clean"
Deploy "forms_v_clean" | ForEach-Object { Say "    $_" }
(Run-Sql "forms_v_clean" "$mig\check2_structure.sql") | ForEach-Object { Say $_ }

# ───────────────────────── B. awkward legacy data ─────────────────────────
Section "B. Awkward legacy data (migration run by hand on a database at the previous schema)"
New-Db "forms_v_edge"
Deploy "forms_v_edge" -withoutForms | ForEach-Object { Say "    $_" }
Run-Sql "forms_v_edge" "$mig\seed_edge.sql" -stop | Out-Null
Say "legacy rows before: (tables not there yet) legacy " + ((Run-Sql "forms_v_edge" "$mig\legacy_counts.sql" | Out-String).Trim())
Say "  running the two migrations (structure, then backfill):"
Timed "forms_v_edge" $migSql
Timed "forms_v_edge" $backfillSql
Say "after migration: $(Counts 'forms_v_edge')"
(Run-Sql "forms_v_edge" "$mig\check3_edge.sql") | ForEach-Object { Say $_ }
Say ""
Say "  Generic parity checks on this data (every list below must be empty):"
(Run-Sql "forms_v_edge" "$mig\check1_parity.sql") | ForEach-Object { Say $_ }

# before-hash of the legacy responses, for the copy-back check
$h = Join-Path $env:TEMP "hash.sql"
Set-Content -Path $h -Encoding ascii -Value "\set QUIET on`n\pset tuples_only on`n\pset format unaligned`nselect md5(string_agg(r::text, '|' order by id)) from event_registrations r where id::text like 'b0%';"
$before = (Run-Sql "forms_v_edge" $h | Select-Object -Last 1).Trim()
Say "hash of the original legacy responses: $before"

# ───────────────────────── C. deploy window catch-up ─────────────────────────
Section "C. Deploy window: the previous release keeps writing the old tables, then the catch-up script runs"
Run-Sql "forms_v_edge" "$mig\late.sql" -stop | Out-Null
Say "before catch-up: $(Counts 'forms_v_edge')"
Timed "forms_v_edge" "$aids\forms-module-catch-up.sql"
$afterOnce = Counts 'forms_v_edge'
Say "after catch-up:  $afterOnce"
(Run-Sql "forms_v_edge" "$mig\check4_catchup.sql") | ForEach-Object { Say $_ }
Timed "forms_v_edge" "$aids\forms-module-catch-up.sql"
Timed "forms_v_edge" "$aids\forms-module-catch-up.sql"
$afterThrice = Counts 'forms_v_edge'
Say "after running it twice more: $afterThrice"
Say ("  Idempotent: " + ($(if ($afterOnce -eq $afterThrice) { "YES, nothing changed" } else { "NO: counts moved" })))

# ───────────────────────── D. application rollback ─────────────────────────
Section "D. Application rollback: the copy-back script"
Run-Sql "forms_v_edge" "$mig\newworld.sql" -stop | Out-Null
Say "before copy-back: $(Counts 'forms_v_edge')"
Timed "forms_v_edge" "$aids\forms-module-copy-back.sql"
$cb1 = Counts 'forms_v_edge'
Say "after copy-back:  $cb1"
$c5 = Join-Path $env:TEMP "check5.sql"
(Get-Content "$mig\check5_copyback.sql") | Set-Content $c5 -Encoding ascii
$args2 = @() + $pg + @("-d","forms_v_edge","-X","-v","before=$before","-f",$c5)
(& psql @args2 2>&1 | ForEach-Object { "$_" }) | ForEach-Object { Say $_ }
Timed "forms_v_edge" "$aids\forms-module-copy-back.sql"
Say ("  Idempotent: " + ($(if ($cb1 -eq (Counts 'forms_v_edge')) { "YES, nothing changed" } else { "NO: counts moved" })))

# ───────────────────────── E. volume ─────────────────────────
Section "E. Volume: 5,000 events, 15,000 questions, 200,000 responses"
New-Db "forms_v_vol"
Deploy "forms_v_vol" -withoutForms | ForEach-Object { Say "    $_" }
(Run-Sql "forms_v_vol" "$mig\seed_volume.sql" -stop) | Where-Object { $_ -match "\d" } | ForEach-Object { Say "    seed: $_" }
Say "  running the migration (single transaction):"
Timed "forms_v_vol" $migSql
Timed "forms_v_vol" $backfillSql
Say "after migration: $(Counts 'forms_v_vol')"
Say "  Parity checks at volume (every list below must be empty):"
(Run-Sql "forms_v_vol" "$mig\check1_parity.sql") | Where-Object { $_ -notmatch "^Pager" } | ForEach-Object { Say $_ }
Say "  catch-up script cost at this size when there is nothing to do:"
Timed "forms_v_vol" "$aids\forms-module-catch-up.sql"


# ───────────────────────── F. locks ─────────────────────────
Section "F. Locks each migration holds on tables that already exist (measured just before commit)"
New-Db "forms_v_lock"
Deploy "forms_v_lock" -withoutForms | Out-Null
$lockq = "select c.relname, string_agg(distinct l.mode, ', ') as modes from pg_locks l join pg_class c on c.oid = l.relation where l.pid = pg_backend_pid() and c.relname in ('event_registrations','event_registration_fields','events','organizations','hierarchy_units','role_permissions','users') group by 1 order by 1;"
foreach ($pair in @(@("1. forms_module (structure)", $migSql, $true), @("2. forms_backfill (data copy)", $backfillSql, $false))) {
  $fwd = $pair[1].Replace('\','/')
  $lf = Join-Path $env:TEMP "locks_$([guid]::NewGuid().ToString('N')).sql"
  Set-Content -Path $lf -Encoding ascii -Value "BEGIN;`n\i '$fwd'`n\echo === $($pair[0]) ===`n$lockq`nROLLBACK;"
  (Run-Sql "forms_v_lock" $lf | Where-Object { $_ -match "===|\||rows\)" -or $_ -match "Lock" }) | ForEach-Object { Say $_ }
  if ($pair[2]) { Run-Sql "forms_v_lock" $pair[1] -stop | Out-Null }   # apply it for real before testing the next one
}
Drop-Db "forms_v_lock"

Drop-Db "forms_v_clean"; Drop-Db "forms_v_edge"; Drop-Db "forms_v_vol"
Say ""; Say "scratch databases dropped"
$outFile = Join-Path $env:TEMP "forms_migration_verification.txt"
Set-Content -Path $outFile -Value $out -Encoding utf8
Write-Host "Full output: $outFile"
