; Inno Setup script for Keys-Setup-<version>.exe. Build with tools/build_exe.py, which
; passes the three defines below; by hand:
;
;   ISCC /DAppVersion=0.13.0 /DSourceDir=..\dist\Keys /DOutputDir=..\dist packaging\keys.iss
;
; (Relative /D paths resolve against this file's folder, hence the ..\.)
;
; The installer wraps the same directory the release zip holds; it adds a Start menu
; entry, a desktop icon and an uninstaller, and nothing else. Three choices matter:
;
;   * Per-user, no admin, fixed folder: %LOCALAPPDATA%\Programs\Keys. The in-app updater
;     rewrites the application directory and stages next to it, so the folder and its
;     parent must be writable without elevation -- Program Files would turn every update
;     into "access denied". The folder page is hidden so nobody points it at Downloads.
;   * The updater only ever touches Keys.exe and _internal\, so unins000.exe/.dat in
;     the same folder survive every in-app update.
;   * The data in %LOCALAPPDATA%\Keys is never touched: not on install, not on uninstall.

#ifndef AppVersion
  #error Pass /DAppVersion=<x.y.z>
#endif
#ifndef SourceDir
  #error Pass /DSourceDir=<path to dist\Keys>
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

[Setup]
; Never change AppId: it is how a later installer finds this install to upgrade.
AppId={{23A2B197-4D31-42A7-9AC2-1BF132A82827}
AppName=Keys
AppVersion={#AppVersion}
AppPublisher=TerraByte-Dev
AppPublisherURL=https://github.com/TerraByte-Dev/Keys
AppSupportURL=https://github.com/TerraByte-Dev/Keys/issues
AppUpdatesURL=https://github.com/TerraByte-Dev/Keys/releases/latest
PrivilegesRequired=lowest
DefaultDirName={autopf}\Keys
DisableDirPage=yes
DisableProgramGroupPage=yes
UsePreviousAppDir=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
; Not "Keys-<version>-win-x64-setup.exe": GitHub lists assets alphabetically, and every
; Keys up to 0.13.0 downloads the FIRST .exe/.msi/.zip it sees. That name sorts before
; the zip ('-' < '.'), so those builds would fetch the installer and fail to extract it.
; "Keys-Setup-" sorts after "Keys-0".."Keys-9". tools/build_exe.py asserts this.
OutputBaseFilename=Keys-Setup-{#AppVersion}
SetupIconFile=keys.ico
UninstallDisplayIcon={app}\Keys.exe
UninstallDisplayName=Keys
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; force, not yes: a Keys with no window (--browser, --no-browser, or the browser
; fallback on a PC without WebView2) is an RmUnknownApp, which Restart Manager can only
; force-close. With `yes` it survived, [InstallDelete] then gutted the _internal it was
; running from, and Abort left both broken. A kill is safe: SQLite is in WAL mode and the
; update lock clears itself.
CloseApplications=force

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; A fresh _internal every install. Installing over a copy the updater has since moved to
; a different version would otherwise leave that version's extra files mixed in.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Keys"; Filename: "{app}\Keys.exe"
Name: "{autodesktop}\Keys"; Filename: "{app}\Keys.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Keys.exe"; Description: "{cm:LaunchProgram,Keys}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; An in-app update replaces _internal with files this installer never logged, so its
; own uninstall log cannot remove them. Named exactly; never {app} wholesale. The
; .old-<hex> entries are what the updater's helper sets aside and could not delete.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\_internal.old-*"
Type: files; Name: "{app}\Keys.exe.old-*"

[Code]
// Keys opens in its own window through Microsoft Edge WebView2, and falls back to a
// browser tab without it. Windows 11 ships it; some Windows 10 machines do not.
function WebView2Installed(): Boolean;
var
  Version: String;
begin
  Result :=
    (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

// A downloaded-but-not-installed update lives BESIDE the app ({app}.new and
// {app}.update.zip), outside anything the uninstall log covers. Same rule as
// updater._clear_staging(): only a tree carrying .keys-staged, or an empty one, is ours.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  NewDir: String;
  FindRec: TFindRec;
  Ours: Boolean;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  NewDir := ExpandConstant('{app}') + '.new';
  if not DirExists(NewDir) then
    Exit;
  Ours := FileExists(NewDir + '\.keys-staged');
  if not Ours then
  begin
    Ours := True;
    if FindFirst(NewDir + '\*', FindRec) then
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
          Ours := False;
      until (not Ours) or (not FindNext(FindRec));
    finally
      FindClose(FindRec);
    end;
  end;
  if Ours then
  begin
    DeleteFile(ExpandConstant('{app}') + '.update.zip');
    DelTree(NewDir, True, True, True);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrorCode: Integer;
begin
  if (CurStep = ssPostInstall) and not WizardSilent() and not WebView2Installed() then
    if MsgBox('Keys opens in its own window using Microsoft Edge WebView2, which this PC ' +
              'does not have. Without it, Keys opens in your browser instead.' + #13#10#13#10 +
              'Open the WebView2 download page now? (Choose "Evergreen Bootstrapper".)',
              mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', 'https://developer.microsoft.com/microsoft-edge/webview2/',
                '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;
