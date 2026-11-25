# Real-Windows test of «Trio – الكاشير» (1.26.1, run by .github/workflows/cashier.yml). Owner = C:\pkg\Trio (Trio.exe),
# cashier = the setup file downloaded from /c with the owner's one-time code. Writes out\log.txt + out\*.png.
param([string]$App = "C:\pkg\Trio", [string]$Out = "out")
$ErrorActionPreference = "Continue"
New-Item -ItemType Directory -Force $Out | Out-Null
$Out = (Resolve-Path $Out).Path
$log = Join-Path $Out "log.txt"
$fails = 0
function L($s) { $t = (Get-Date -Format "HH:mm:ss") + " " + $s; Write-Host $t; Add-Content -Encoding utf8 $log $t }
function Check($ok, $what) { if ($ok) { L "PASS $what" } else { L "FAIL $what"; $script:fails++ } }
function WaitFor($sec, [scriptblock]$cond) { for ($i = 0; $i -lt $sec; $i++) { if (& $cond) { return $true }; Start-Sleep 1 }; return $false }
function Get($u, $h = @{}) { try { return Invoke-WebRequest -UseBasicParsing -TimeoutSec 20 -Headers $h $u } catch { if ($_.Exception.Response) { return $_.Exception.Response } ; return $null } }
function Status() { try { return Invoke-RestMethod -TimeoutSec 5 http://127.0.0.1:8010/__trio/status } catch { return $null } }
function Healthy() { try { return (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 http://127.0.0.1:8000/healthz/).StatusCode -eq 200 } catch { return $false } }
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
function Shot($name) {
  $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
  $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
  $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
  $bmp.Save((Join-Path $Out "$name.png"), [System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $bmp.Dispose()
  L "screenshot $name.png"
}
$py = Join-Path $App "python\python.exe"
$title = "Trio – الكاشير"

L "== 1. owner PC: database, shop, owner user"
Push-Location $App
& $py manage.py migrate --noinput *>> (Join-Path $Out "owner-setup.log")
& $py manage.py seed_shop --name "محل الاختبار" --allow-existing *>> (Join-Path $Out "owner-setup.log")
& $py manage.py shell -c "from core.models import CompanySettings as C;s=C.get();s.setup_done=True;s.save();from django.contrib.auth.models import User,Group;u,_=User.objects.get_or_create(username='ciowner');u.set_password('Ci-Pass-2026');u.save();u.groups.add(Group.objects.get_or_create(name='مدير المحل')[0]);print('owner ok')" *>> (Join-Path $Out "owner-setup.log")
Pop-Location
New-Item -ItemType Directory -Force "$App\data" | Out-Null
Set-Content "$App\data\.installed" (Get-Date -Format o)
$desk = [Environment]::GetFolderPath("Desktop"); $progs = [Environment]::GetFolderPath("Programs")
$deskBefore = @(Get-ChildItem $desk -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object Name)
Start-Process (Join-Path $App "Trio.exe") -ArgumentList "-quiet" -WorkingDirectory $App
Check (WaitFor 240 { Healthy }) "owner server up on :8000"

L "== 2. owner: «رمز للتنزيل عبر شبكة المحل» → the cashier PC opens /c and types the code"
$setup = Join-Path $Out "Trio Cashier Setup - CI.exe"
& $py setup\cashier_ci.py http://127.0.0.1:8000 ciowner Ci-Pass-2026 $setup *>> $log
Check ($LASTEXITCODE -eq 0 -and (Test-Path $setup)) "setup file downloaded with the one-time code (second use refused)"
L ("setup file size: " + (Get-Item $setup -ErrorAction SilentlyContinue).Length)
Check (Test-Path "$App\data\shop_id.txt") "owner has data\shop_id.txt"

L "== 3. install on the «cashier PC» (per user, no admin)"
$p = Start-Process -PassThru -Wait $setup -ArgumentList "-silent", "-nolaunch"
L ("setup exit " + $p.ExitCode)
$inst = Join-Path $env:LOCALAPPDATA "Trio Cashier"
Check (Test-Path "$inst\TrioCashier.exe") "installed TrioCashier.exe"
Check (Test-Path "$inst\window\TrioWindow.exe") "installed window\TrioWindow.exe"
Check (Test-Path "$inst\pair.json") "installed pair.json"
Check (-not (Test-Path "$inst\python")) "no Python on the cashier PC"
$deskAfter = @(Get-ChildItem $desk -Filter *.lnk | ForEach-Object Name)
$new = @($deskAfter | Where-Object { $deskBefore -notcontains $_ })
L ("new desktop shortcuts: " + ($new -join ", "))
Check ($new.Count -eq 1 -and $new[0] -eq "$title.lnk") "exactly one new desktop shortcut «$title»"
Check (Test-Path (Join-Path $progs "$title.lnk")) "Start-menu entry"
$un = Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\TrioCashier" -ErrorAction SilentlyContinue
Check ($un -and $un.DisplayName -eq $title -and $un.UninstallString -like "*-uninstall*") ("Apps entry (HKCU): " + $un.DisplayName + " | " + $un.UninstallString)
$tmpl = Join-Path $env:TEMP "check-cashier.lnk"; Copy-Item -LiteralPath (Join-Path $desk "$title.lnk") $tmpl -Force  # WScript.Shell reads ANSI names only
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($tmpl)
Check ($lnk.TargetPath -eq "$inst\TrioCashier.exe") ("shortcut → " + $lnk.TargetPath)

L "== 4. discovery: UDP 8001 on loopback with this shop's id"
$env:TRIO_DISCOVER_EXTRA = "127.0.0.1"
Start-Process -Wait "$inst\TrioCashier.exe" -ArgumentList "-find", "$Out\find.txt"
$found = Get-Content "$Out\find.txt" -ErrorAction SilentlyContinue
L ("discovery found: " + ($found -join ", "))
Check (($found -join " ") -match "127\.0\.0\.1:8000") "owner launcher answered the discovery"

L "== 5. the cashier app (proxy) → owner"
$capp = Start-Process -PassThru "$inst\TrioCashier.exe" -ArgumentList "-nowindow"
Check (WaitFor 30 { $s = Status; $s -and $s.online }) "cashier app connected to the owner PC"
$r = Get "http://127.0.0.1:8010/cashier/"
Check ($r -and $r.StatusCode -eq 200 -and $r.Content -match 'id="linkBox"' -and $r.Content -match "/__trio/boot.js") "/cashier/ → till page through the proxy (boot.js injected)"
$tok = (Get-Content "$inst\pair.json" -Raw | ConvertFrom-Json).token
$r = Get "http://127.0.0.1:8010/pos/api/catalog/" @{ "X-Device-Token" = $tok }
Check ($r -and $r.StatusCode -eq 200) "catalog through the proxy with the paired device key"
try { $s = Invoke-RestMethod -Method Post -TimeoutSec 30 -ContentType "application/json" -Headers @{ "X-Device-Token" = $tok } -Body '{"orders":[]}' http://127.0.0.1:8010/pos/api/sync/; Check ($null -ne $s.accepted) "sync POST through the proxy" } catch { Check $false ("sync POST: " + $_) }
Invoke-WebRequest -UseBasicParsing -Method Post -Headers @{ "X-Trio-Local" = "1" } http://127.0.0.1:8010/__trio/quit | Out-Null
Check (WaitFor 15 { $capp.HasExited }) "-nowindow app quit on request"

L "== 6. from the desktop shortcut: the till window"
Remove-Item Env:TRIO_DISCOVER_EXTRA
Start-Process (Join-Path $desk "$title.lnk")
Check (WaitFor 40 { $s = Status; $s -and $s.online }) "shortcut started the cashier app"
$w = WaitFor 40 { Get-Process TrioWindow -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle } }
Check $w ("till window open: " + ((Get-Process TrioWindow -ErrorAction SilentlyContinue | ForEach-Object MainWindowTitle) -join ", "))
Start-Sleep 12; Shot "1-till-online"

L "== 7. owner PC off → the till still opens (cache), API answers offline"
$key = (Get-Content "$App\data\secret.txt" -Raw).Trim().Substring(0, 16)
try { Invoke-WebRequest -UseBasicParsing -Method Post "http://127.0.0.1:8001/stop?k=$key" | Out-Null } catch { L ("stop: " + $_) }
Check (WaitFor 60 { -not (Healthy) }) "owner server stopped"
Check (WaitFor 40 { $s = Status; $s -and -not $s.online }) "cashier app shows offline"
$r = Get "http://127.0.0.1:8010/cashier/"
Check ($r -and $r.StatusCode -eq 200 -and $r.Content -match 'id="linkBox"') "till page served from the cache with the owner PC off"
Check ($r.Headers["X-Trio-From"] -eq "cache") ("served from: " + $r.Headers["X-Trio-From"])
$r = Get "http://127.0.0.1:8010/pos/api/catalog/" @{ "X-Device-Token" = $tok }
Check ($r -and [int]$r.StatusCode -eq 503) ("API offline answer: " + [int]$r.StatusCode)
Start-Sleep 6; Shot "2-till-offline"

L "== 8. owner PC back → reconnects"
Start-Process (Join-Path $App "Trio.exe") -ArgumentList "-quiet" -WorkingDirectory $App
Check (WaitFor 240 { Healthy }) "owner server up again"
Check (WaitFor 60 { $s = Status; $s -and $s.online }) "cashier app reconnected"
Start-Sleep 5; Shot "3-till-back"

L "== 9. re-run the setup file (update / re-pair) then uninstall"
Start-Process -Wait $setup -ArgumentList "-silent", "-nolaunch"
Check (Test-Path "$inst\TrioCashier.exe") "re-run keeps the install"
$deskAgain = @(Get-ChildItem $desk -Filter *.lnk | ForEach-Object Name | Where-Object { $deskBefore -notcontains $_ })
Check ($deskAgain.Count -eq 1) "still one shortcut after re-run"
Get-Content "$inst\cashier.log" -ErrorAction SilentlyContinue | Select-Object -Last 30 | ForEach-Object { L ("cashier.log: " + $_) }
Get-Process TrioWindow -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Process -Wait "$inst\TrioCashier.exe" -ArgumentList "-uninstall", "-silent"
Start-Sleep 6
Check (-not (Test-Path (Join-Path $desk "$title.lnk"))) "uninstall removed the desktop shortcut"
Check (-not (Test-Path (Join-Path $progs "$title.lnk"))) "uninstall removed the Start-menu entry"
Check (-not (Test-Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\TrioCashier")) "uninstall removed the Apps entry"
Check (WaitFor 15 { -not (Test-Path "$inst\TrioCashier.exe") }) "uninstall removed the folder"
Copy-Item "$env:TEMP\trio-cashier-*.log" $Out -ErrorAction SilentlyContinue

L "== result: $fails failed"
exit $fails
