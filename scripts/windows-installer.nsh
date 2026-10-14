!include "getProcessInfo.nsh"
Var pid
!ifndef BUILD_UNINSTALLER
  Var previousStartup
!endif

!macro customInit
  ReadRegStr $previousStartup HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Alshival.Ai"
!macroend

!macro customInstall
  ; Older uninstallers remove this value even during upgrades. Restore it.
  ${if} $previousStartup != ""
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Alshival.Ai" "$previousStartup"
  ${endif}
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\App Paths\alshitasks.exe" "" "$INSTDIR\Alshival.Ai.exe"
!macroend
!macro customUnInstall
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\App Paths\alshitasks.exe"
  ${ifNot} ${isUpdated}
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Alshival.Ai"
  ${endif}
!macroend
!macro customCheckAppRunning
  IfFileExists "$INSTDIR\resources\python\python.exe" 0 +5
    nsExec::ExecToStack '"$INSTDIR\resources\python\python.exe" "$INSTDIR\resources\app\service\alshitasks-cli.py" stop'
    Pop $0
    Pop $1
    Sleep 3000
  !insertmacro IS_POWERSHELL_AVAILABLE
  !insertmacro _CHECK_APP_RUNNING
!macroend
