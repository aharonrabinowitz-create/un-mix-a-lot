; Un-Mix-a-Lot Windows installer (NSIS). Build with installer/build.sh.
; Per-user install (no administrator needed). The program files are small; Python and the libraries are
; downloaded during setup by "Install Un-Mix-a-Lot.bat".
Unicode True
!include "MUI2.nsh"
!include "LogicLib.nsh"

!define APP "Un-Mix-a-Lot"
!define VERSION "1.0"
!define UNKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP}"

Name "${APP}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\${APP}"
InstallDirRegKey HKCU "Software\${APP}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "${APP} ${VERSION}"

VIProductVersion "1.0.0.0"
VIAddVersionKey "ProductName" "${APP}"
VIAddVersionKey "FileDescription" "${APP} setup"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "Free to use"

!define MUI_ICON "stage\icon.ico"
!define MUI_UNICON "stage\icon.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Welcome to ${APP}"
!define MUI_WELCOMEPAGE_TEXT "This installs ${APP}, a free app that splits audio and video into voices, music and sound effects.$\r$\n$\r$\nSetup downloads Python and the audio libraries, so it needs an internet connection. It takes 5 to 15 minutes and can download up to about 3 GB (much less if your computer has no NVIDIA graphics card).$\r$\n$\r$\nNo administrator rights are needed."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Start ${APP}"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApp
!define MUI_FINISHPAGE_NOREBOOTSUPPORT

!define MUI_LICENSEPAGE_CHECKBOX
!define MUI_LICENSEPAGE_CHECKBOX_TEXT "I understand and accept this notice"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "stage\DISCLAIMER.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Function LaunchApp
  SetOutPath "$INSTDIR"
  Exec '"$INSTDIR\.venv\Scripts\pythonw.exe" "$INSTDIR\desktop.py"'
FunctionEnd

Section "Install"
  SetOutPath "$INSTDIR"
  DetailPrint "Copying program files ..."
  File /r "stage\*.*"

  DetailPrint "Downloading Python and the audio libraries. This is the slow part: please wait ..."
  System::Call 'kernel32::SetEnvironmentVariable(t "UNMIX_NOPAUSE", t "1")'
  System::Call 'kernel32::SetEnvironmentVariable(t "PIP_PROGRESS_BAR", t "off")'
  System::Call 'kernel32::SetEnvironmentVariable(t "PYTHONUTF8", t "1")'
  nsExec::ExecToLog '"$SYSDIR\cmd.exe" /c ""$INSTDIR\Install ${APP}.bat""'
  Pop $0
  ${If} $0 != "0"
    MessageBox MB_OK|MB_ICONSTOP "Setup could not finish installing the audio libraries (code $0).$\r$\n$\r$\nCheck your internet connection and run setup again. The details above show what went wrong."
    Abort
  ${EndIf}
  ${IfNot} ${FileExists} "$INSTDIR\.venv\Scripts\pythonw.exe"
    MessageBox MB_OK|MB_ICONSTOP "Python was not set up correctly. Please run setup again."
    Abort
  ${EndIf}

  DetailPrint "Creating shortcuts ..."
  SetOutPath "$INSTDIR"
  CreateDirectory "$SMPROGRAMS\${APP}"
  CreateShortcut "$SMPROGRAMS\${APP}\${APP}.lnk" "$INSTDIR\.venv\Scripts\pythonw.exe" '"$INSTDIR\desktop.py"' "$INSTDIR\icon.ico" 0
  CreateShortcut "$SMPROGRAMS\${APP}\Uninstall ${APP}.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\${APP}.lnk" "$INSTDIR\.venv\Scripts\pythonw.exe" '"$INSTDIR\desktop.py"' "$INSTDIR\icon.ico" 0

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\${APP}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNKEY}" "DisplayName" "${APP}"
  WriteRegStr HKCU "${UNKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNKEY}" "DisplayIcon" "$INSTDIR\icon.ico"
  WriteRegStr HKCU "${UNKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNKEY}" "NoRepair" 1
  DetailPrint "Done."
SectionEnd

Section "Uninstall"
  RMDir /r "$INSTDIR\.venv"
  RMDir /r "$INSTDIR\.tools"
  RMDir /r "$INSTDIR\stemsplit"
  Delete "$INSTDIR\*.py"
  Delete "$INSTDIR\*.bat"
  Delete "$INSTDIR\*.txt"
  Delete "$INSTDIR\*.md"
  Delete "$INSTDIR\*.ico"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\DISCLAIMER.txt"
  Delete "$INSTDIR\splash.html"
  Delete "$INSTDIR\Uninstall.exe"
  MessageBox MB_YESNO|MB_ICONQUESTION "Also delete your settings (including any MVSEP token) and logs?$\r$\n$\r$\nYour exported audio in Documents is never touched." IDNO keep
  Delete "$INSTDIR\settings.json"
  RMDir /r "$INSTDIR\logs"
  keep:
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\${APP}\${APP}.lnk"
  Delete "$SMPROGRAMS\${APP}\Uninstall ${APP}.lnk"
  RMDir "$SMPROGRAMS\${APP}"
  Delete "$DESKTOP\${APP}.lnk"
  DeleteRegKey HKCU "${UNKEY}"
  DeleteRegKey HKCU "Software\${APP}"
SectionEnd
