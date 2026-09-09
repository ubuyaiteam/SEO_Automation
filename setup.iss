[Setup]
AppId={{8B190204-7C7D-4FBA-BAE3-DDE8F4B8E1B9}
AppName=Ubuy SEO Automation Tool
AppVersion=9.3 beta
AppPublisher=Ubuy AI Team
AppPublisherURL=https://github.com/ubuyaiteam/SEO_Automation
AppSupportURL=https://github.com/ubuyaiteam/SEO_Automation
AppUpdatesURL=https://github.com/ubuyaiteam/SEO_Automation
DefaultDirName={autopf}\Ubuy SEO Tool
DisableProgramGroupPage=yes
; Output directory for the generated installer
OutputDir=.\Output
OutputBaseFilename=SEO_Auto_Installer
SetupIconFile=icon.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: ".\dist\SEO_Auto\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "icon.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "ubuy.gif"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Ubuy SEO Automation Tool"; Filename: "{app}\SEO_Auto.exe"; IconFilename: "{app}\icon.ico"
Name: "{autodesktop}\Ubuy SEO Automation Tool"; Filename: "{app}\SEO_Auto.exe"; Tasks: desktopicon; IconFilename: "{app}\icon.ico"

[Run]
Filename: "{app}\SEO_Auto.exe"; Description: "{cm:LaunchProgram,Ubuy SEO Automation Tool}"; Flags: nowait postinstall skipifsilent
