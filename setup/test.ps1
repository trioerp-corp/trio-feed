# Real-Windows test of Trio-Setup.exe + TrioWindow.exe (run by .github/workflows/setup.yml). Writes out\log.txt + out\*.png.
param([string]$Setup = "out\Trio-Setup.exe", [string]$Out = "out")
$ErrorActionPreference = "Continue"
New-Item -ItemType Directory -Force $Out | Out-Null
$Out = (Resolve-Path $Out).Path
$log = Join-Path $Out "log.txt"
$fails = 0
function L($s) { $t = (Get-Date -Format "HH:mm:ss") + " " + $s; Write-Host $t; Add-Content -Encoding utf8 $log $t }
function Check($ok, $what) { if ($ok) { L "PASS $what" } else { L "FAIL $what"; $script:fails++ } }

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public class W {
  public delegate bool EP(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EP f, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int hh, bool r);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  public struct RECT { public int L, T, R, B; }
  public static List<string> All() {
    var o = new List<string>();
    EnumWindows((h, l) => { if (IsWindowVisible(h)) { var t = new StringBuilder(300); var c = new StringBuilder(200); GetWindowText(h, t, 300); GetClassName(h, c, 200);
      uint p; GetWindowThreadProcessId(h, out p); RECT r; GetWindowRect(h, out r);
      o.Add(p + "|" + c + "|" + t + "|" + r.L + "," + r.T + "," + (r.R - r.L) + "x" + (r.B - r.T) + "|" + h.ToInt64()); } return true; }, IntPtr.Zero);
    return o;
  }
}
"@
[W]::SetProcessDPIAware() | Out-Null

function Shot($name) {
  $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
  $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
  $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
  $bmp.Save((Join-Path $Out "$name.png"), [System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $bmp.Dispose()
  L "screenshot $name.png ($($b.Width)x$($b.Height))"
}
function Windows() {
  $names = @{}; Get-Process | ForEach-Object { $names[[uint32]$_.Id] = $_.ProcessName }
  [W]::All() | ForEach-Object { $f = $_.Split("|"); [pscustomobject]@{ Proc = $names[[uint32]$f[0]]; Pid = [int]$f[0]; Class = $f[1]; Title = $f[2]; Rect = $f[3]; H = [IntPtr][long]$f[4] } }
}
function MainWin() { Windows | Where-Object { $_.Proc -eq "TrioWindow" -and $_.Class -like "WindowsForms10*" } }
function Up() { try { $c = New-Object Net.Sockets.TcpClient; $c.Connect("127.0.0.1", 8000); $c.Close(); return $true } catch { return $false } }
function Healthy() { try { return (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 http://127.0.0.1:8000/healthz/).StatusCode -eq 200 } catch { return $false } }
function WaitFor($sec, [scriptblock]$cond) { for ($i = 0; $i -lt $sec; $i++) { if (& $cond) { return $true }; Start-Sleep 1 }; return $false }

L "== printer: «TrioPDF» (Microsoft Print to PDF on a file port = no Save dialog), default"
$pdf = "C:\trioprint\out.pdf"; New-Item -ItemType Directory -Force C:\trioprint | Out-Null
Add-PrinterPort -Name $pdf -ErrorAction SilentlyContinue
Add-Printer -Name TrioPDF -DriverName "Microsoft Print To PDF" -PortName $pdf -ErrorAction SilentlyContinue
$p = Get-CimInstance Win32_Printer -Filter "Name='TrioPDF'"; if ($p) { Invoke-CimMethod -InputObject $p -MethodName SetDefaultPrinter | Out-Null }
L ("default printer: " + (Get-CimInstance Win32_Printer | Where-Object Default).Name)

L "== 1. install: Trio-Setup.exe /VERYSILENT"
$wvBefore = Get-ItemProperty "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" -ErrorAction SilentlyContinue
L ("WebView2 runtime before setup: " + $wvBefore.pv)
$t = Measure-Command { Start-Process -Wait $Setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=$Out\setup-install.log" }
L ("setup took {0:n0} s" -f $t.TotalSeconds)
Check (Test-Path C:\Trio\Trio.exe) "C:\Trio\Trio.exe"
Check (Test-Path "C:\Trio\Trio Cashier.exe") "C:\Trio\Trio Cashier.exe"
Check (Test-Path C:\Trio\window\TrioWindow.exe) "C:\Trio\window\TrioWindow.exe"
Check (Test-Path C:\Trio\python\python.exe) "C:\Trio\python\python.exe"
Check (-not (Test-Path C:\Trio\.acc-flash)) "no .acc-flash mark in C:\Trio"
$pub = [Environment]::GetFolderPath("CommonDesktopDirectory"); $sm = Join-Path ([Environment]::GetFolderPath("CommonPrograms")) "Trio"
foreach ($s in @("$pub\Trio.lnk", "$pub\Trio Cashier.lnk", "$sm\Trio.lnk", "$sm\Trio Cashier.lnk")) { Check (Test-Path $s) "shortcut $s" }
$sh = New-Object -ComObject WScript.Shell; $lnk = $sh.CreateShortcut("$pub\Trio.lnk"); L ("desktop Trio.lnk → " + $lnk.TargetPath + " icon " + $lnk.IconLocation)
$un = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{6E1B7C2A-5D3F-4A8E-9B1C-7F2E4D6A9C31}_is1" -ErrorAction SilentlyContinue
Check ($un -and $un.DisplayName -eq "Trio") ("Apps entry: " + $un.DisplayName + " " + $un.DisplayVersion + " icon " + $un.DisplayIcon)
$wv = Get-ItemProperty "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" -ErrorAction SilentlyContinue
Check ($wv.pv) ("WebView2 runtime after setup: " + $wv.pv)

L "== 2. open from the desktop shortcut «Trio»"
Start-Process "$pub\Trio.lnk"
Check (WaitFor 60 { MainWin }) "a Trio window appears (loading page)"
Start-Sleep 3; Shot "01-loading"
Check (WaitFor 420 { Healthy }) "server answers on :8000"
Start-Sleep 12
$w = MainWin | Select-Object -First 1
L ("window: «" + $w.Title + "» " + $w.Rect + " maximized=" + [W]::IsZoomed($w.H))
Check ($w.Title -like "*Trio*") "window title has Trio"
Check ([W]::IsZoomed($w.H)) "first open = maximized"
Shot "02-window"
L "visible top-level windows:"; Windows | ForEach-Object { L ("   " + $_.Proc + " | " + $_.Class + " | " + $_.Title + " | " + $_.Rect) }
$bad = Windows | Where-Object { $_.Proc -in @("python", "pythonw", "cmd", "conhost", "waitress", "Trio", "Trio Cashier", "msedge", "chrome", "WindowsTerminal") -or $_.Class -in @("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "Chrome_WidgetWin_1") } | Where-Object { $_.Title -notlike "*GitHub\HostedComputeAgent*" }  # the runner's own agent terminal
Check (-not $bad) ("no console / browser window (" + (($bad | ForEach-Object { $_.Proc + ":" + $_.Class }) -join ", ") + ")")
Check (Get-Process python -ErrorAction SilentlyContinue) "python (the server) runs in the background"

L "== 3. printing: silent on the till (/pos/), a printer dialog elsewhere (test pages on :8123)"
New-Item -ItemType Directory -Force C:\ptest\pos, C:\ptest\rep | Out-Null
'<!doctype html><html><head><meta charset="utf-8"><title>till test</title></head><body><h1 id="x">RECEIPT-ONE-123</h1><script>setTimeout(function(){window.print();document.getElementById("x").textContent="CHANGED-AFTER-PRINT"},1500)</script></body></html>' | Set-Content -Encoding utf8 C:\ptest\pos\t.html
'<!doctype html><html><head><meta charset="utf-8"><title>report test</title></head><body><h1>REPORT</h1><script>setTimeout(function(){window.print()},1500)</script></body></html>' | Set-Content -Encoding utf8 C:\ptest\rep\t.html
$hs = Start-Process -PassThru -WindowStyle Hidden python -ArgumentList "-m", "http.server", "8123", "-d", "C:\ptest", "--bind", "127.0.0.1"
Start-Sleep 3
Remove-Item $pdf -ErrorAction SilentlyContinue
$pt = Start-Process -PassThru C:\Trio\window\TrioWindow.exe -ArgumentList "http://127.0.0.1:8123/pos/t.html", "--key", "ptest"
$printed = WaitFor 40 { (Test-Path $pdf) -and (Get-Item $pdf).Length -gt 500 }
Start-Sleep 3
$dlg = Windows | Where-Object { $_.Pid -eq $pt.Id -and $_.Class -eq "#32770" }
Check ($printed) "till page: window.print() printed to the default printer ($((Get-Item $pdf -ErrorAction SilentlyContinue).Length) bytes)"
Check (-not $dlg) "till page: no print dialog"
Shot "03-till-print"
python -m pip install -q pymupdf 2>$null
if ($printed) {
  Copy-Item $pdf "$Out\03-till-printed.pdf"
  $txt = python -c "import fitz,sys;d=fitz.open(sys.argv[1]);d[0].get_pixmap(dpi=60).save(sys.argv[2]);print(len(d),'page(s):',' '.join(p.get_text() for p in d).replace(chr(10),' '))" $pdf "$Out\03-till-printed-page.png"
  L ("printed: " + $txt); if ($txt -match "page\(s\):\s*$") { L "INFO Microsoft Print to PDF keeps no text layer — the printed page is 03-till-printed-page.png (must show RECEIPT-ONE-123, not CHANGED-AFTER-PRINT)" }
  else { Check ($txt -match "RECEIPT-ONE-123") "the receipt printed is the page at the moment of window.print()" } }
Stop-Process -Id $pt.Id -Force -ErrorAction SilentlyContinue
$pr = Start-Process -PassThru C:\Trio\window\TrioWindow.exe -ArgumentList "http://127.0.0.1:8123/rep/t.html", "--key", "ptest"
$d2 = WaitFor 30 { Windows | Where-Object { $_.Pid -eq $pr.Id -and $_.Class -eq "#32770" } }
Check ($d2) "report page: window.print() shows a printer dialog"
Shot "04-report-print-dialog"
Stop-Process -Id $pr.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $hs.Id -Force -ErrorAction SilentlyContinue

L "== 4. size the window (900x650 at 100,100), open Trio Cashier too"
$w = MainWin | Select-Object -First 1
[W]::ShowWindow($w.H, 9) | Out-Null; Start-Sleep 1; [W]::MoveWindow($w.H, 100, 100, 900, 650, $true) | Out-Null; Start-Sleep 2
L ("after move: " + (MainWin | Select-Object -First 1).Rect)
Start-Process "$pub\Trio Cashier.lnk"
Check (WaitFor 40 { (Get-Process TrioWindow -ErrorAction SilentlyContinue).Count -ge 2 }) "Trio Cashier opens a second window (through the running copy)"
Start-Sleep 8; Shot "05-two-windows"
$cash = Get-CimInstance Win32_Process -Filter "Name='TrioWindow.exe'" | Where-Object { $_.CommandLine -like "*--till*" }
L ("cashier window command line: " + $cash.CommandLine)
if ($cash) { $cw = Windows | Where-Object { $_.Pid -eq $cash.ProcessId -and $_.Class -like "WindowsForms10*" } | Select-Object -First 1; L ("cashier window: «" + $cw.Title + "»"); (Get-Process -Id $cash.ProcessId).CloseMainWindow() | Out-Null }
Start-Sleep 8
Check (Up) "closing ONE window keeps the server (the other window is open)"

L "== 5. close the last window → the server stops"
$mp = Get-Process TrioWindow | Where-Object { $_.Id -ne $cash.ProcessId } | Select-Object -First 1
$mp.CloseMainWindow() | Out-Null
Check (WaitFor 60 { -not (Up) }) "port 8000 free after the last window closed"
Start-Sleep 3
Check (-not (Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "C:\Trio\*" })) "no Trio python left"
Check (-not (Get-Process Trio -ErrorAction SilentlyContinue)) "no Trio.exe left"
if (Test-Path "$env:LOCALAPPDATA\Trio\window.json") { L ("window.json: " + (Get-Content "$env:LOCALAPPDATA\Trio\window.json")) }

L "== 6. reopen → the same size and place"
Start-Process "$pub\Trio.lnk"
Check (WaitFor 60 { MainWin }) "window again"
Check (WaitFor 300 { Healthy }) "server again"
Start-Sleep 10
$w = MainWin | Select-Object -First 1
L ("window: «" + $w.Title + "» " + $w.Rect + " maximized=" + [W]::IsZoomed($w.H))
Check ($w.Rect -eq "100,100,900x650" -and -not [W]::IsZoomed($w.H)) "size/place remembered (100,100,900x650)"
Shot "06-reopened"
(Get-Process TrioWindow | Select-Object -First 1).CloseMainWindow() | Out-Null
Check (WaitFor 60 { -not (Up) }) "stopped again"

L "== 7. uninstall (silent) — data kept"
Start-Process -Wait C:\Trio\unins000.exe -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=$Out\setup-uninstall.log"
Start-Sleep 3
Check (-not (Test-Path C:\Trio\Trio.exe)) "program files removed"
Check (Test-Path C:\Trio\data\acc-erp.sqlite3) ("data kept: " + ((Get-ChildItem C:\Trio\data -ErrorAction SilentlyContinue | ForEach-Object Name) -join ", "))
Check (-not (Test-Path "$pub\Trio.lnk")) "desktop shortcut removed"
Check (-not (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{6E1B7C2A-5D3F-4A8E-9B1C-7F2E4D6A9C31}_is1" -ErrorAction SilentlyContinue)) "Apps entry removed"
Shot "07-after-uninstall"
L "trio.log:"; if (Test-Path C:\Trio\data\trio.log) { Get-Content C:\Trio\data\trio.log -Tail 40 | ForEach-Object { L ("   " + $_) } }
L "== $fails failure(s)"
exit $fails
