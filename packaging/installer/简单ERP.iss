; 简单ERP Windows 安装包脚本（Inno Setup 6）
;
; 形态：per-user 安装（%LOCALAPPDATA%\Programs\简单ERP），全程不需要管理员。
; 原因：程序把可写 config.json 放在 EXE 旁边（erp/config.py: runtime_root()），
;       装进受保护的系统目录会让非管理员保存设置失败。
;
; 版本只有一个真源：仓库根 VERSION。安装包版本、显示版本、PE 版本字段都从它读。
;
; 命令行覆盖（默认值见下方 #ifndef）：
;   /DAppVersion=vX.Y.Z        强制版本（默认读 VERSION）
;   /DSourceDir=...\简单ERP    已构建的 one-folder 产物目录
;   /DOutputDir=...            安装包输出目录
;   /DAppIconFile=...          安装包图标

#ifndef AppVersion
  #define VerFile AddBackslash(SourcePath) + "..\..\VERSION"
  #if !FileExists(VerFile)
    #error 找不到仓库根 VERSION，无法确定安装包版本
  #endif
  #define AppVersion Trim(FileRead(FileOpen(VerFile)))
#endif

#ifndef SourceDir
  #define SourceDir AddBackslash(SourcePath) + "..\..\dist\local-" + AppVersion + "\简单ERP"
#endif

#ifndef OutputDir
  #define OutputDir AddBackslash(SourcePath) + "..\..\dist"
#endif

#ifndef AppIconFile
  #define AppIconFile AddBackslash(SourcePath) + "..\简单ERP.ico"
#endif

; PE 版本字段要纯数字：vX.Y.Z -> X.Y.Z.0
#define NumericVersion Copy(AppVersion, 2) + ".0"

#if !FileExists(SourceDir + "\简单ERP.exe")
  #error 未找到已构建的程序目录，请先运行 打包Windows安装包.bat（或 PyInstaller 构建 one-folder 产物）
#endif

[Setup]
AppId={{8F1C2A64-4D5B-4E31-9A7C-6B2E0D5A31F7}
AppName=简单ERP
AppVersion={#AppVersion}
AppVerName=简单ERP {#AppVersion}
AppPublisher=简单ERP
VersionInfoVersion={#NumericVersion}
VersionInfoDescription=简单ERP Windows 安装包
VersionInfoProductName=简单ERP
; 注意：不要显式写 VersionInfoProductVersion——Inno 要求它是纯数字版本，
; 而这里想保留产品版本字符串 vX.Y.Z；不写该指令时 Inno 会自动从 AppVersion 派生，
; 实测 PE ProductVersion 正好是 vX.Y.Z。
DefaultDirName={localappdata}\Programs\简单ERP
DefaultGroupName=简单ERP
DisableProgramGroupPage=yes
; per-user：不申请提权，也不允许用户切到提权模式（提权会装进 Program Files，配置将不可写）
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=
; 本机单机工具，固定 64 位（随包 _internal 内含 python311.dll / VCRUNTIME140 / GTK 全套）
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=simple-erp-setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#AppIconFile}
UninstallDisplayIcon={app}\简单ERP.exe
UninstallDisplayName=简单ERP {#AppVersion}
; 升级/卸载不删除用户写在程序目录旁的 config.json（见 [Files] 的 uninsneveruninstall）
Uninstallable=yes
AllowNoIcons=yes
InfoBeforeFile={#SourcePath}\安装说明.txt
MinVersion=10.0.17763

[Languages]
; 中文语言文件随仓库提供（Inno 发行包的 Languages\ 目录不含中文），
; 用 SourcePath 指向脚本同目录，避免依赖 Inno 安装目录被写入。
Name: "chinesesimplified"; MessagesFile: "{#SourcePath}\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项："; Flags: unchecked

[Files]
; 只安装程序本体。
; 注意：Excludes 里写裸 "config.json" 会按文件名匹配全树，连 _internal\config.json
; （冻结版的内置回退配置）一起排掉，所以下面显式把它补回来。
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "config.json"
Source: "{#SourceDir}\_internal\config.json"; DestDir: "{app}\_internal"; Flags: ignoreversion
; 用户配置单独一条：升级不覆盖、卸载不删除（保留店铺名/打印偏移/主题）
Source: "{#SourceDir}\config.json"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall

[Icons]
Name: "{group}\简单ERP"; Filename: "{app}\简单ERP.exe"; WorkingDir: "{app}"
Name: "{group}\卸载 简单ERP"; Filename: "{uninstallexe}"
Name: "{autodesktop}\简单ERP"; Filename: "{app}\简单ERP.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\简单ERP.exe"; Description: "立即启动 简单ERP"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只清理本安装器管理的 {app} 下的安装残留，不碰数据根目录。
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\简单ERP.exe"

[Code]
const
  WEBVIEW2_KEY = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WEBVIEW2_URL = 'https://developer.microsoft.com/microsoft-edge/webview2/';

function IsAppRunning(): Boolean;
var
  ResultCode: Integer;
begin
  // 用 tasklist 判断主程序是否在运行；不依赖任何随包组件。
  Result := Exec('cmd.exe', '/c tasklist /FI "IMAGENAME eq 简单ERP.exe" | find /I "简单ERP.exe" >nul', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function WebView2Installed(): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(HKLM, WEBVIEW2_KEY, 'pv', Version) and (Version <> '')
         or RegQueryStringValue(HKCU, WEBVIEW2_KEY, 'pv', Version) and (Version <> '');
end;

function InitializeSetup(): Boolean;
var
  ErrorCode: Integer;
begin
  Result := True;
  if IsAppRunning() then
  begin
    if MsgBox('检测到 简单ERP 正在运行。' + #13#10#13#10 +
              '安装/升级需要替换程序文件，请先退出程序再继续。' + #13#10 +
              '选择「是」将关闭程序并继续安装，选择「否」退出安装。',
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      Exec('taskkill.exe', '/IM 简单ERP.exe /F', '', SW_HIDE, ewWaitUntilTerminated, ErrorCode);
      Sleep(1500);
      if IsAppRunning() then
      begin
        MsgBox('程序仍在运行，请手动退出后重新安装。', mbError, MB_OK);
        Result := False;
      end;
    end
    else
      Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrorCode: Integer;
begin
  if (CurStep = ssPostInstall) and (not WebView2Installed()) then
  begin
    if MsgBox('本机未检测到 Microsoft Edge WebView2 运行时。' + #13#10#13#10 +
              '缺少它时，简单ERP 会改用系统默认浏览器打开界面（功能可用，但没有独立程序窗口）。' + #13#10#13#10 +
              '是否现在打开官方下载页面？',
              mbConfirmation, MB_YESNO) = IDYES then
      ShellExec('open', WEBVIEW2_URL, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataRoot: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataRoot := ExpandConstant('{userdocs}\简单ERP数据');
    MsgBox('简单ERP 的程序文件已卸载。' + #13#10#13#10 +
           '你的业务数据没有被删除，默认保存在：' + #13#10 + DataRoot + #13#10#13#10 +
           '如果在设置页里改过数据目录，请以设置页显示的路径为准。' + #13#10 +
           '需要彻底清理时请手动删除该目录（建议先备份 erp.db）。',
           mbInformation, MB_OK);
  end;
end;
