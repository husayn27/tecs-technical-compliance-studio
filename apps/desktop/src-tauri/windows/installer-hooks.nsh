!macro TECS_STOP_RUNNING_PROCESSES
  DetailPrint "Closing any running TECS background service..."
  nsExec::ExecToLog 'taskkill /F /T /IM tecs-engine.exe'
  nsExec::ExecToLog 'taskkill /F /T /IM tecs-lighting-quotation.exe'
  nsExec::ExecToLog 'taskkill /F /T /IM "TECS Technical Compliance Studio.exe"'
  Sleep 1500
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro TECS_STOP_RUNNING_PROCESSES
  ; A Windows process locks its executable. Remove the released sidecar after
  ; stopping it so an upgrade can never retain a mixed-version engine.
  Delete /REBOOTOK "$INSTDIR\tecs-engine.exe"
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro TECS_STOP_RUNNING_PROCESSES
!macroend
