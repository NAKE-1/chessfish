; Inno Setup script. Built by installer\build.ps1 (needs Inno Setup 6: ISCC.exe).
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define Root ".."

[Setup]
AppId={{49B99EF1-3E48-41F9-9BBD-15BF0562CD0C}
AppName=Chessfish
AppVersion={#AppVersion}
AppPublisher=Chessfish
; default = the folder the setup .exe is run from (decision 24)
DefaultDirName={src}\Chessfish
DisableDirPage=no
UsePreviousAppDir=no
DefaultGroupName=Chessfish
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir={#Root}\build\installer
OutputBaseFilename=ChessfishSetup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\Chessfish.exe
WizardStyle=modern

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#Root}\build\dist\Chessfish\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Root}\build\stockfish-dist\*"; DestDir: "{app}\stockfish"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Chessfish"; Filename: "{app}\Chessfish.exe"
Name: "{autodesktop}\Chessfish"; Filename: "{app}\Chessfish.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Chessfish.exe"; Description: "Run Chessfish"; Flags: nowait postinstall skipifsilent

; config\, logs\ and games\ are created by the app and are kept on uninstall, so saved games survive.
