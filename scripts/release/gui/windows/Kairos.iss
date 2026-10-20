#ifndef MyAppVersion
  #error MyAppVersion is required
#endif
#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef NumericVersion
  #error NumericVersion is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef OutputBaseFilename
  #error OutputBaseFilename is required
#endif
#ifndef IconFile
  #error IconFile is required
#endif
#ifndef LicenseFile
  #error LicenseFile is required
#endif
#ifndef NoticeFile
  #error NoticeFile is required
#endif
#ifndef UserGuideFile
  #error UserGuideFile is required
#endif
#ifndef ContractGuideFile
  #error ContractGuideFile is required
#endif

[Setup]
AppId={{8EAD67A1-91AB-497A-81A5-8A73CF4A6F31}
AppName=Kairos
AppVersion={#MyAppVersion}
AppVerName=Kairos {#MyAppVersion}
AppPublisher=Paranoid Zero
AppPublisherURL=https://kairoschain.xyz/
AppSupportURL=https://github.com/ignotusnemo/kairos/issues
AppUpdatesURL=https://github.com/ignotusnemo/kairos/releases
DefaultDirName={localappdata}\Programs\Kairos
DefaultGroupName=Kairos
DisableProgramGroupPage=yes
AllowNoIcons=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
SetupIconFile={#IconFile}
LicenseFile={#LicenseFile}
UninstallDisplayIcon={app}\Kairos.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
VersionInfoVersion={#NumericVersion}
VersionInfoCompany=Paranoid Zero
VersionInfoDescription=Kairos Wallet Installer
VersionInfoProductName=Kairos
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "chinesesimplified"; MessagesFile: "{#SourcePath}\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\kairos-gui.exe"; DestDir: "{app}"; DestName: "Kairos.exe"; Flags: ignoreversion
Source: "{#SourceDir}\kairos.exe"; DestDir: "{app}"; DestName: "kairos-node.exe"; Flags: ignoreversion
Source: "{#LicenseFile}"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "{#NoticeFile}"; DestDir: "{app}"; DestName: "NOTICE.txt"; Flags: ignoreversion
Source: "{#UserGuideFile}"; DestDir: "{app}"; DestName: "README.txt"; Flags: ignoreversion
Source: "{#ContractGuideFile}"; DestDir: "{app}"; DestName: "CONTRACTS.md"; Flags: ignoreversion

[Icons]
Name: "{group}\Kairos"; Filename: "{app}\Kairos.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Kairos"; Filename: "{app}\Kairos.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Kairos.exe"; Description: "{cm:LaunchProgram,Kairos}"; Flags: nowait postinstall skipifsilent
