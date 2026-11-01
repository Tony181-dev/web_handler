; The Setup exe. tools/build_package.py compiles this with Inno Setup 6.
; It passes AppVersion, SourceDir and OutputDir.

[Setup]
AppId={{7E2C9A54-3B1D-4F6E-9C80-5D2A41B7E6F3}
AppName=Web Handler
AppVersion={#AppVersion}
AppVerName=Web Handler {#AppVersion}
DefaultDirName={autopf}\WebHandler
DefaultGroupName=Web Handler
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=WebHandler-Setup-{#AppVersion}
; The payload is mostly zip files already, so harder compression only costs time.
Compression=lzma2/fast
SolidCompression=no
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=Web Handler
UninstallDisplayIcon={sys}\shell32.dll,13
; The app is closed by PrepareToInstall below, which also ends its workers.
CloseApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a shortcut on the Desktop"

[Files]
Source: "{#SourceDir}\app\*"; DestDir: "{app}\app"; Flags: recursesubdirs ignoreversion
Source: "{#SourceDir}\offline\*"; DestDir: "{app}\offline"; Flags: recursesubdirs ignoreversion
Source: "{#SourceDir}\Start.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\README.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\README.vi.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Web Handler"; Filename: "{app}\Start.bat"; WorkingDir: "{app}"; IconFilename: "{sys}\shell32.dll"; IconIndex: 13
Name: "{autodesktop}\Web Handler"; Filename: "{app}\Start.bat"; WorkingDir: "{app}"; IconFilename: "{sys}\shell32.dll"; IconIndex: 13; Tasks: desktopicon

[Run]
Filename: "{app}\Start.bat"; Description: "Open Web Handler"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent shellexec

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c ""set WEB_HANDLER_NO_PAUSE=1&& ""{app}\Start.bat"" --stop"""; Flags: runhidden waituntilterminated; RunOnceId: "StopApp"

[UninstallDelete]
; Made by the app after installing. The data folder is handled below.
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\app"

[Code]
function RunStart(Argument: String; var ResultCode: Integer): Boolean;
begin
  Result := Exec(ExpandConstant('{cmd}'),
    '/c "set WEB_HANDLER_NO_PAUSE=1&& "' + ExpandConstant('{app}\Start.bat') + '" ' + Argument + '"',
    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  if FileExists(ExpandConstant('{app}\Start.bat')) then
  begin
    RunStart('--stop', ResultCode);
    if ResultCode <> 0 then
      Result := 'Could not close the running Web Handler. Close its window and install again.';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := 'Installing the libraries. This can take a few minutes...';
    if (not RunStart('--install', ResultCode)) or (ResultCode <> 0) then
      if not WizardSilent then
        MsgBox('The libraries are not fully installed. When you first open the app, click Install missing on the Setup screen.', mbInformation, MB_OK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Data := ExpandConstant('{app}\data');
    if DirExists(Data) and not UninstallSilent then
      if MsgBox('Also delete your data: flows, run history and login sessions?' + #13#10 +
                'Choose No to keep it in ' + Data + '.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Data, True, True, True);
  end;
end;
