#ifndef AppVersion
  #define AppVersion "0.8.5 Beta"
#endif
#ifndef AppVersionNumeric
  #define AppVersionNumeric "0.8.5.0"
#endif

[Setup]
AppId={{C9E0772F-DB8C-4525-8B38-683344112F2D}
AppName=RaG PBO Tools
AppVersion={#AppVersion}
AppVerName=RaG PBO Tools {#AppVersion}
AppPublisher=RaG Tyson
AppPublisherURL=https://github.com/Tyson89/RaG-PBO-Builder
AppSupportURL=https://github.com/Tyson89/RaG-PBO-Builder/issues
AppUpdatesURL=https://github.com/Tyson89/RaG-PBO-Builder/releases
LicenseFile=LICENSE.txt
VersionInfoVersion={#AppVersionNumeric}
DefaultDirName={localappdata}\Programs\RaG PBO Tools
DefaultGroupName=RaG PBO Tools
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist\installer
OutputBaseFilename=RaG_PBO_Tools_Setup
SetupIconFile=assets\HEADONLY_SQUARE_2k.ico
UninstallDisplayIcon={app}\RaG_PBO_Builder.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
RestartApplications=no
SourceDir=..

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut for RaG PBO Builder"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "dist\RaG_PBO_Builder.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\RaG_PBO_Inspector.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\RaG PBO Builder"; Filename: "{app}\RaG_PBO_Builder.exe"
Name: "{autoprograms}\RaG PBO Inspector"; Filename: "{app}\RaG_PBO_Inspector.exe"
Name: "{autodesktop}\RaG PBO Builder"; Filename: "{app}\RaG_PBO_Builder.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RaG_PBO_Builder.exe"; Description: "Launch RaG PBO Builder"; Flags: nowait postinstall skipifsilent
