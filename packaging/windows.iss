#ifndef AppVersion
  #define AppVersion "0.8.1"
#endif

[Setup]
AppId={{75A6AE26-95E8-45DC-99B5-488E0F7016DE}
AppName=AUB Yearbook Station
AppVersion={#AppVersion}
AppPublisher=AUB Campus Yearbook contributors
AppPublisherURL=https://github.com/Jiany-S/aub-yearbook-station
DefaultDirName={localappdata}\Programs\AUBYearbookStation
DefaultGroupName=AUB Yearbook Station
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=..\dist-assets
OutputBaseFilename=AUBYearbookStation-{#AppVersion}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\AUBYearbookStation.exe
LicenseFile=..\LICENSE
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\AUBYearbookStation\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AUB Yearbook Station"; Filename: "{app}\AUBYearbookStation.exe"
Name: "{autodesktop}\AUB Yearbook Station"; Filename: "{app}\AUBYearbookStation.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AUBYearbookStation.exe"; Description: "Launch AUB Yearbook Station"; Flags: nowait postinstall skipifsilent
