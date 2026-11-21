; Extra steps of the AiSignX Player setup (electron-builder includes this file).
; One .exe installs the player for all users AND the privileged agent files. The agent is connected right away when a
; server address and an enrollment code are given (a wizard page, or /SERVER= /CODE= /KEY= on the command line for
; silent installs), otherwise later from the Start menu entry "Connect AiSignX agent".
;
; Silent example (the generated "Set up a Windows PC" script does this):
;   "AiSignX Player Setup.exe" /S /SERVER=http://192.168.1.19:5000 /CODE=ABCD-EFGH-JKLM-NPQR /KEY="ab12 cd34 ef56 7890" /KIOSKUSER=kiosk /ALLOW=update,startup
!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"

Var AgentServer
Var AgentCode
Var AgentKey
Var AgentKiosk
Var AgentAllow
Var hAgentServer
Var hAgentCode
Var hAgentKey
Var hAgentKiosk
Var hAllowReboot
Var hAllowStartup
Var hAllowUpdate

!macro customInit
  ${GetParameters} $R0
  ${GetOptions} $R0 "/SERVER=" $AgentServer
  ${GetOptions} $R0 "/CODE=" $AgentCode
  ${GetOptions} $R0 "/KEY=" $AgentKey
  ${GetOptions} $R0 "/KIOSKUSER=" $AgentKiosk
  ${GetOptions} $R0 "/ALLOW=" $AgentAllow
  ; Silent installs started by the generated PC script pass the values in the environment (not visible on a command line).
  ${If} $AgentCode == ""
    ReadEnvStr $AgentServer AISIGNX_SETUP_SERVER
    ReadEnvStr $AgentCode AISIGNX_SETUP_CODE
    ReadEnvStr $AgentKey AISIGNX_SETUP_KEY
    ReadEnvStr $AgentKiosk AISIGNX_SETUP_KIOSKUSER
    ReadEnvStr $AgentAllow AISIGNX_SETUP_ALLOW
  ${EndIf}
!macroend

!macro customPageAfterChangeDir
  Page custom AgentPageCreate AgentPageLeave
!macroend

!ifndef BUILD_UNINSTALLER
Function AgentPageCreate
  !insertmacro MUI_HEADER_TEXT "Privileged agent (optional)" "Lets the web page restart, update and start this PC's player"
  nsDialogs::Create 1018
  Pop $0
  ${NSD_CreateLabel} 0 0 100% 34u "The agent is installed with the player. To connect it now, first pair this screen, then in the admin open Displays > this screen > Device agent and copy the server address, the enrollment code and the key fingerprint here. Leave the code empty to connect later (Start menu: Connect AiSignX agent)."
  Pop $0
  ${NSD_CreateLabel} 0 38u 30% 10u "Server address"
  Pop $0
  ${NSD_CreateText} 31% 36u 69% 12u "$AgentServer"
  Pop $hAgentServer
  ${NSD_CreateLabel} 0 54u 30% 10u "Enrollment code"
  Pop $0
  ${NSD_CreateText} 31% 52u 69% 12u "$AgentCode"
  Pop $hAgentCode
  ${NSD_CreateLabel} 0 70u 30% 10u "Key fingerprint"
  Pop $0
  ${NSD_CreateText} 31% 68u 69% 12u "$AgentKey"
  Pop $hAgentKey
  ${NSD_CreateLabel} 0 86u 30% 10u "Player's Windows account"
  Pop $0
  ${NSD_CreateText} 31% 84u 69% 12u "$AgentKiosk"
  Pop $hAgentKiosk
  ${NSD_CreateCheckbox} 0 104u 33% 10u "May restart the PC"
  Pop $hAllowReboot
  ${NSD_CreateCheckbox} 34% 104u 33% 10u "May change how the player starts"
  Pop $hAllowStartup
  ${NSD_CreateCheckbox} 68% 104u 32% 10u "May install updates"
  Pop $hAllowUpdate
  ${NSD_Check} $hAllowReboot
  ${NSD_Check} $hAllowStartup
  ${NSD_Check} $hAllowUpdate
  nsDialogs::Show
FunctionEnd

Function AgentPageLeave
  ${NSD_GetText} $hAgentServer $AgentServer
  ${NSD_GetText} $hAgentCode $AgentCode
  ${NSD_GetText} $hAgentKey $AgentKey
  ${NSD_GetText} $hAgentKiosk $AgentKiosk
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
  ${If} $AgentCode != ""
    ${If} $AgentServer == ""
    ${OrIf} $AgentKey == ""
      MessageBox MB_ICONEXCLAMATION "To connect the agent now, fill in the server address, the code and the key fingerprint, or clear the code to connect later."
      Abort
    ${EndIf}
  ${EndIf}
FunctionEnd

!endif

!macro customInstall
  ; The Start menu entry that connects the agent later (it asks Windows for administrator permission itself).
  CreateShortCut "$SMPROGRAMS\Connect AiSignX agent.lnk" "$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" '-NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\resources\agent-install\Setup-Agent.ps1"' "$INSTDIR\${APP_EXECUTABLE_FILENAME}"
  ${If} $AgentCode != ""
    ; Values go to PowerShell through the environment of this process, never through a command line.
    System::Call 'Kernel32::SetEnvironmentVariable(t "AISIGNX_SETUP_SERVER", t "$AgentServer")i'
    System::Call 'Kernel32::SetEnvironmentVariable(t "AISIGNX_SETUP_CODE", t "$AgentCode")i'
    System::Call 'Kernel32::SetEnvironmentVariable(t "AISIGNX_SETUP_KEY", t "$AgentKey")i'
    System::Call 'Kernel32::SetEnvironmentVariable(t "AISIGNX_SETUP_KIOSKUSER", t "$AgentKiosk")i'
    System::Call 'Kernel32::SetEnvironmentVariable(t "AISIGNX_SETUP_ALLOW", t "$AgentAllow")i'
    DetailPrint "Connecting the agent..."
    nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\resources\agent-install\Setup-Agent.ps1" -InstallDir "$INSTDIR"'
    Pop $0
    ${If} $0 != 0
      DetailPrint "The agent was NOT connected (exit code $0)."
      ${IfNot} ${Silent}
        MessageBox MB_ICONEXCLAMATION "The player is installed, but the agent could not be connected (see the details above). Check the code, then use Start menu > Connect AiSignX agent."
      ${EndIf}
      SetErrorLevel 3
    ${EndIf}
    System::Call 'Kernel32::SetEnvironmentVariable(t "AISIGNX_SETUP_CODE", t "")i'
  ${EndIf}
!macroend

!macro customUnInstall
  ; (the uninstaller build warns about variables it never uses, and warnings are errors: touch each one)
  StrCpy $AgentServer ""
  StrCpy $AgentCode ""
  StrCpy $AgentKey ""
  StrCpy $AgentKiosk ""
  StrCpy $AgentAllow ""
  StrCpy $hAgentServer ""
  StrCpy $hAgentCode ""
  StrCpy $hAgentKey ""
  StrCpy $hAgentKiosk ""
  StrCpy $hAllowReboot ""
  StrCpy $hAllowStartup ""
  StrCpy $hAllowUpdate ""
  Delete "$SMPROGRAMS\Connect AiSignX agent.lnk"
  IfFileExists "$INSTDIR\resources\agent-install\Install-Agent.ps1" 0 +2
    nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\resources\agent-install\Install-Agent.ps1" -Uninstall -Yes -SkipSelfCheck'
!macroend
