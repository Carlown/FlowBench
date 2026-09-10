; FlowBench Installer Script (Inno Setup 7)
; 中英双语安装程序
#define MyAppName "FlowBench"
#define MyAppVersion "1.2.2"
#define MyAppPublisher "FlowBench"
#define MyAppExeName "FlowBench.exe"
#define MyAppDirName "FlowBench"
#define MyAppAUMID "FlowBench.App"

[Setup]
AppId={{8F4A2C1E-9B3D-4E6A-B5C7-1A2B3C4D5E6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=installer
OutputBaseFilename=FlowBench-Setup-1.2.2
SetupIconFile=app.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[CustomMessages]
english.RunApp=Run FlowBench
chinesesimplified.RunApp=运行 FlowBench
english.DesktopIcon=Create a desktop shortcut
chinesesimplified.DesktopIcon=创建桌面快捷方式(&D)
english.AdditionalTasks=Additional tasks:
chinesesimplified.AdditionalTasks=附加任务：
english.ProgramComment=FlowBench Network Stress Testing Tool
chinesesimplified.ProgramComment=FlowBench 网络压力测试工具

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "{cm:AdditionalTasks}"

[Files]
Source: "dist\{#MyAppDirName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\FlowBench"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Comment: "{cm:ProgramComment}"; AppUserModelID: "{#MyAppAUMID}"
Name: "{autodesktop}\FlowBench"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Comment: "{cm:ProgramComment}"; Tasks: desktopicon; AppUserModelID: "{#MyAppAUMID}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:RunApp}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
