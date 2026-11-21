; Extra steps of the AiSignX Player setup (electron-builder includes this file).
; One .exe, run as administrator, installs the player for all users AND the privileged agent. The agent is installed unconnected:
; it asks the server for approval when it starts, the player tells the server which PC it is on, and the admin approves (or ticked
; "approve the agent" when pairing the screen). A wizard page asks for the server address, the Windows account the screen runs as
; (a list of this PC's accounts) and what the web page may do. Silent installs take the same things:
;   "AiSignX Player Setup.exe" /S /SERVER=http://192.168.1.19:5000 /KIOSKUSER=kiosk /ALLOW=update,startup
; (or the AISIGNX_SETUP_SERVER / _KIOSKUSER / _ALLOW environment variables, which keep them off the command line).
!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"

Var AgentServer
Var AgentKiosk
Var AgentAllow
Var hAgentServer
Var hAgentKiosk
Var hAllowReboot
Var hAllowStartup
Var hAllowUpdate
Var AccIndex
Var AccLen
Var AccItem
Var AccChar
Var AccList

!macro customInit
  ${GetParameters} $R0
  ${GetOptions} $R0 "/SERVER=" $AgentServer
  ${GetOptions} $R0 "/KIOSKUSER=" $AgentKiosk
  ${GetOptions} $R0 "/ALLOW=" $AgentAllow
  ${If} $AgentServer == ""
    ReadEnvStr $AgentServer AISIGNX_SETUP_SERVER
    ReadEnvStr $AgentKiosk AISIGNX_SETUP_KIOSKUSER
    ReadEnvStr $AgentAllow AISIGNX_SETUP_ALLOW
  ${EndIf}
!macroend

!macro customPageAfterChangeDir
  Page custom AgentPageCreate AgentPageLeave
!macroend

!ifndef BUILD_UNINSTALLER
Function AgentPageCreate
  !insertmacro MUI_HEADER_TEXT "Privileged agent" "Lets the web page restart, update and set up this PC"
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0 0 100% 36u "The agent is installed with the player. It connects to your AiSignX server and, after you pair this screen there (tick 'approve the agent' on the Pair page), the web page can restart the player, install updates and change how the player starts. Leave the address empty to connect later (Start menu: Connect AiSignX agent)."
  Pop $0
  ${NSD_CreateLabel} 0 42u 32% 10u "Server address"
  Pop $0
  ${NSD_CreateText} 33% 40u 67% 12u "$AgentServer"
  Pop $hAgentServer
  ${NSD_CreateLabel} 0 58u 32% 10u "Windows account that runs the screen"
  Pop $0
  ${NSD_CreateDropList} 33% 56u 67% 60u ""
  Pop $hAgentKiosk
  ${NSD_CB_AddString} $hAgentKiosk "(none: whoever signs in)"
  ; The accounts of this PC, from a small PowerShell script (names separated by |).
  InitPluginsDir
  File "/oname=$PLUGINSDIR\List-Accounts.ps1" "${BUILD_RESOURCES_DIR}\List-Accounts.ps1"
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\List-Accounts.ps1"'
  Pop $0
  Pop $AccList
  StrCpy $AccItem ""
  StrCpy $AccIndex 0
  StrLen $AccLen $AccList
  ${While} $AccIndex < $AccLen
    StrCpy $AccChar $AccList 1 $AccIndex
    ${If} $AccChar == "|"
      ${If} $AccItem != ""
        ${NSD_CB_AddString} $hAgentKiosk "$AccItem"
      ${EndIf}
      StrCpy $AccItem ""
    ${ElseIf} $AccChar != "$\r"
    ${AndIf} $AccChar != "$\n"
      StrCpy $AccItem "$AccItem$AccChar"
    ${EndIf}
    IntOp $AccIndex $AccIndex + 1
  ${EndWhile}
  ${If} $AccItem != ""
    ${NSD_CB_AddString} $hAgentKiosk "$AccItem"
  ${EndIf}
  ${If} $AgentKiosk != ""
    ${NSD_CB_SelectString} $hAgentKiosk "$AgentKiosk"
    Pop $0
  ${Else}
    SendMessage $hAgentKiosk ${CB_SETCURSEL} 0 0
  ${EndIf}
  ${NSD_CreateLabel} 0 78u 100% 10u "What may the web page do on this PC? (the admin can switch each off for a screen later)"
  Pop $0
  ${NSD_CreateCheckbox} 0 90u 33% 10u "Restart the PC"
  Pop $hAllowReboot
  ${NSD_CreateCheckbox} 34% 90u 33% 10u "Change how the player starts"
  Pop $hAllowStartup
  ${NSD_CreateCheckbox} 68% 90u 32% 10u "Install updates"
  Pop $hAllowUpdate
  ${NSD_Check} $hAllowReboot
  ${NSD_Check} $hAllowStartup
  ${NSD_Check} $hAllowUpdate
  nsDialogs::Show
FunctionEnd

Function AgentPageLeave
  ${NSD_GetText} $hAgentServer $AgentServer
  ${NSD_GetText} $hAgentKiosk $AgentKiosk
  ${If} $AgentKiosk == "(none: whoever signs in)"
    StrCpy $AgentKiosk ""
  ${EndIf}
  StrCpy $AgentAllow ""
  ${NSD_GetState} $hAllowReboot $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $AgentAllow "$AgentAllow,reboot"
  ${EndIf}
  ${NSD_GetState} $hAllowStartup $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $AgentAllow "$AgentAllow,startup"
  ${EndIf}
  ${NSD_GetState} $hAllowUpdate $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $AgentAllow "$AgentAllow,update"
  ${EndIf}
FunctionEnd
!endif

!macro customInstall
  ; The Start menu entry that connects (or reconnects) the agent later: it asks Windows for administrator permission itself.
  CreateShortCut "$SMPROGRAMS\Connect AiSignX agent.lnk" "$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" '-NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\resources\agent-install\Setup-Agent.ps1"' "$INSTDIR\${APP_EXECUTABLE_FILENAME}"
  ${If} $AgentServer != ""
    ; Values go to PowerShell through the environment of this process, never through a command line.
    System::Call 'Kernel32::SetEnvironmentVariable(t "AISIGNX_SETUP_SERVER", t "$AgentServer")i'
    System::Call 'Kernel32::SetEnvironmentVariable(t "AISIGNX_SETUP_KIOSKUSER", t "$AgentKiosk")i'
    System::Call 'Kernel32::SetEnvironmentVariable(t "AISIGNX_SETUP_ALLOW", t "$AgentAllow")i'
    DetailPrint "Installing the agent..."
    nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\resources\agent-install\Setup-Agent.ps1" -InstallDir "$INSTDIR"'
    Pop $0
    ${If} $0 != 0
      DetailPrint "The agent was NOT installed (exit code $0)."
      ${IfNot} ${Silent}
        MessageBox MB_ICONEXCLAMATION "The player is installed, but the agent could not be installed (see the details above). Use Start menu > Connect AiSignX agent to try again."
      ${EndIf}
      SetErrorLevel 3
    ${EndIf}
  ${EndIf}
!macroend

!macro customUnInstall
  ; (the uninstaller build warns about variables it never uses, and warnings are errors: touch each one)
  StrCpy $AgentServer ""
  StrCpy $AgentKiosk ""
  StrCpy $AgentAllow ""
  StrCpy $hAgentServer ""
  StrCpy $hAgentKiosk ""
  StrCpy $hAllowReboot ""
  StrCpy $hAllowStartup ""
  StrCpy $hAllowUpdate ""
  StrCpy $AccIndex ""
  StrCpy $AccLen ""
  StrCpy $AccItem ""
  StrCpy $AccChar ""
  StrCpy $AccList ""
  Delete "$SMPROGRAMS\Connect AiSignX agent.lnk"
  IfFileExists "$INSTDIR\resources\agent-install\Install-Agent.ps1" 0 +2
    nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\resources\agent-install\Install-Agent.ps1" -Uninstall -Yes -SkipSelfCheck'
!macroend
