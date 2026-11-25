; Trio-Setup.exe — installs Trio to C:\Trio (the program keeps its data next to itself in data\, so not Program Files).
; Build (Windows, Inno Setup 6):
;   iscc /DSrc=<the «Trio» folder of the online package + window\TrioWindow.exe> /DAppVer=1.25.3 /DOut=<out dir> trio.iss
; <Src> also needs trio.ico and MicrosoftEdgeWebview2Setup.exe (https://go.microsoft.com/fwlink/p/?LinkId=2124703) next to
; this script. Upgrade keeps data\; uninstall keeps data\ unless the person says yes to deleting it.
#ifndef Src
  #define Src "..\..\dist\Trio"
#endif
#ifndef AppVer
  #define AppVer "1.0.0"
#endif
#ifndef Out
  #define Out "out"
#endif

[Setup]
AppId={{6E1B7C2A-5D3F-4A8E-9B1C-7F2E4D6A9C31}
AppName=Trio
AppVersion={#AppVer}
AppVerName=Trio {#AppVer}
AppPublisher=Trio
AppPublisherURL=https://trioerp.pages.dev
AppSupportURL=https://trioerp.pages.dev
AppContact=01228827915
DefaultDirName=C:\Trio
DisableDirPage=yes
DefaultGroupName=Trio
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#Out}
OutputBaseFilename=Trio-Setup
SetupIconFile=trio.ico
UninstallDisplayIcon={app}\trio.ico
UninstallDisplayName=Trio
VersionInfoVersion={#AppVer}
VersionInfoProductName=Trio
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=auto
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "ar"; MessagesFile: "compiler:Languages\Arabic.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
ar.DelData=هل تريد مسح بيانات المحل أيضًا (المبيعات والأصناف والحسابات في C:\Trio\data)؟%n%nاختر «لا» للاحتفاظ بها (ينفع ترجع تسطّب البرنامج وتلاقيها زي ما هي).
en.DelData=Also delete the shop data (sales, items, accounts in C:\Trio\data)?%n%nChoose «No» to keep it (a new install finds it as it is).
ar.Cashier=Trio Cashier
en.Cashier=Trio Cashier
ar.StopTrio=إيقاف Trio
en.StopTrio=Stop Trio
ar.WebView=بنجهّز Microsoft Edge WebView2 ...
en.WebView=Installing Microsoft Edge WebView2 ...
ar.Launch=شغّل Trio
en.Launch=Start Trio

[Dirs]
Name: "{app}"; Permissions: users-modify
Name: "{app}\data"; Permissions: users-modify; Flags: uninsneveruninstall

[Files]
Source: "{#Src}\*"; DestDir: "{app}"; Excludes: ".acc-flash,\data\*,__pycache__"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "trio.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsWebView2

[Icons]
Name: "{autodesktop}\Trio"; Filename: "{app}\Trio.exe"; WorkingDir: "{app}"; IconFilename: "{app}\trio.ico"
Name: "{autodesktop}\{cm:Cashier}"; Filename: "{app}\Trio Cashier.exe"; WorkingDir: "{app}"; IconFilename: "{app}\trio.ico"
Name: "{group}\Trio"; Filename: "{app}\Trio.exe"; WorkingDir: "{app}"; IconFilename: "{app}\trio.ico"
Name: "{group}\{cm:Cashier}"; Filename: "{app}\Trio Cashier.exe"; WorkingDir: "{app}"; IconFilename: "{app}\trio.ico"
Name: "{group}\{cm:StopTrio}"; Filename: "{app}\Trio.exe"; Parameters: "-stop"; WorkingDir: "{app}"; IconFilename: "{app}\trio.ico"

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "{cm:WebView}"; Flags: waituntilterminated; Check: NeedsWebView2
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""Trio 8000"" dir=in action=allow protocol=TCP localport=8000"; Flags: runhidden waituntilterminated
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""Trio 8001 UDP"""; Flags: runhidden waituntilterminated
Filename: "netsh"; Parameters: "advfirewall firewall add rule name=""Trio 8001 UDP"" dir=in action=allow protocol=UDP localport=8001"; Flags: runhidden waituntilterminated
Filename: "{app}\Trio.exe"; Description: "{cm:Launch}"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""Trio 8000"""; Flags: runhidden; RunOnceId: "fw"
Filename: "netsh"; Parameters: "advfirewall firewall delete rule name=""Trio 8001 UDP"""; Flags: runhidden; RunOnceId: "fwudp"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\staticfiles"
Type: filesandordirs; Name: "{app}\python"
Type: files; Name: "{autodesktop}\Trio - الكاشير.lnk"

[Code]
const WV2 = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function HasPv(Root: Integer; Key: String): Boolean;
var pv: String;
begin
  Result := RegQueryStringValue(Root, Key, 'pv', pv) and (pv <> '') and (pv <> '0.0.0.0');
end;

function NeedsWebView2(): Boolean;
begin
  Result := not (HasPv(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WV2) or
                 HasPv(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WV2) or
                 HasPv(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\' + WV2));
end;

// a running copy keeps its files busy: ask it to stop (the control port, like «Trio.exe -stop») and close its windows
procedure StopTrio();
var key: AnsiString; rc: Integer;
begin
  if LoadStringFromFile(ExpandConstant('{app}\data\secret.txt'), key) then begin
    key := Copy(Trim(key), 1, 16);
    Exec('powershell.exe', '-NoProfile -NonInteractive -WindowStyle Hidden -Command "try{Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 -Method Post ''http://127.0.0.1:8001/stop?k=' + key + '''|Out-Null;Start-Sleep 4}catch{}"',
      '', SW_HIDE, ewWaitUntilTerminated, rc);
  end;
  Exec('taskkill.exe', '/F /IM TrioWindow.exe', '', SW_HIDE, ewWaitUntilTerminated, rc);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopTrio();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopTrio();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent() then
    if MsgBox(CustomMessage('DelData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(ExpandConstant('{app}'), True, True, True);
end;
