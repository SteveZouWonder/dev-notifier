; Inno Setup script for Dev Notifier (Windows).
;
; Wraps the PyInstaller one-file exe in a real installer so the app gets a
; stable install location, Start Menu entry, Add/Remove Programs entry and an
; uninstaller. The in-app updater downloads DevNotifier-<ver>-setup.exe and
; launches it; this script closes the running copy, replaces the exe in place,
; and relaunches it.
;
; Invoked by packaging/windows_package.ps1 as:
;   ISCC.exe /DAppVersion=1.0.0 /DSourceExe=dist\DevNotifier-1.0.0-portable.exe ^
;            /DOutputDir=dist [/DAppIcon=build\app-icon.ico] packaging\windows_installer.iss
;
; Inno Setup 6 is preinstalled on GitHub's windows-latest runners.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
; Relative paths resolve against this file's directory (packaging\).
#ifndef SourceExe
  #define SourceExe "..\dist\DevNotifier.exe"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
; VersionInfoVersion must be purely numeric (X.Y.Z); AppVersion may carry a
; pre-release suffix such as 1.6.0-rc.1, so the caller passes the numeric core.
#ifndef AppVersionNumeric
  #define AppVersionNumeric AppVersion
#endif

#define AppName "Dev Notifier"
#define AppExeName "DevNotifier.exe"
#define AppPublisher "SteveZou"
#define AppURL "https://github.com/SteveZouWonder/dev-notifier"
; Must match platform_backend/windows.py (RUN_VALUE_NAME) so the installer can
; migrate a start-at-login entry that still points at a portable exe.
#define RunValueName "DevNotifier"

[Setup]
; Fixed GUID so upgrades replace the previous install instead of adding a copy.
AppId={{AC9E7F69-2E11-4EC9-B42F-F024C0BE764A}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersionNumeric}
; Per-user install by default (no UAC prompt; matches the HKCU Run key the app
; manages). Users may still pick an all-users install from the dialog.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\DevNotifier
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=DevNotifier-{#AppVersion}-setup
#ifdef AppIcon
SetupIconFile={#AppIcon}
UninstallDisplayIcon={app}\{#AppExeName}
#endif
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; Replace the running app: ask Windows Restart Manager to close it, and fall
; back to taskkill in [Code] for the tray process (which has no window).
CloseApplications=yes
RestartApplications=no
UninstallDisplayName={#AppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "Start {#AppName} when I sign in"; GroupDescription: "Startup:"

[Files]
Source: "{#SourceExe}"; DestDir: "{app}"; DestName: "{#AppExeName}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
; Start-at-login (same HKCU Run value the app toggles from its menu).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#RunValueName}"; ValueData: """{app}\{#AppExeName}"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM {#AppExeName} /F"; Flags: runhidden; RunOnceId: "KillDevNotifier"

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

procedure KillRunningApp;
var
  ResultCode: Integer;
begin
  { The tray app has no top-level window, so Restart Manager may not close it.
    Best effort: terminate by image name; ignore failures (not running). }
  Exec(ExpandConstant('{cmd}'), '/C taskkill /IM {#AppExeName} /F', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillRunningApp;
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Existing: String;
begin
  if CurStep = ssPostInstall then
  begin
    { If start-at-login was enabled from a portable exe (which stored its own,
      now-stale path), repoint it at the installed copy. The "startup" task
      above handles the fresh-enable case. }
    if RegQueryStringValue(HKCU, RunKey, '{#RunValueName}', Existing) then
      RegWriteStringValue(HKCU, RunKey, '{#RunValueName}',
                          '"' + ExpandConstant('{app}\{#AppExeName}') + '"');
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKCU, RunKey, '{#RunValueName}');
end;
