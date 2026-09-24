; Hooks do instalador NSIS (Tauri 2 `bundle.windows.nsis.installerHooks`).
;
; Iteração 5 (§46–§48): fechar a janela do EdgeFut AI NÃO encerra o coletor — o sidecar
; `edgefut-engine.exe` continua a correr na bandeja. O instalador padrão só verifica o executável
; principal, por isso uma atualização falhava com "Erro ao abrir o arquivo pra gravação:
; ...\edgefut-engine.exe" (ficheiro em uso). Estes hooks encerram o motor antes de escrever/apagar.
;
; taskkill só termina processos deste utilizador (installMode currentUser); nada além do EdgeFut é tocado.

!macro _EdgeFutStopProcesses
  DetailPrint "Encerrando o EdgeFut AI e o motor em segundo plano (edgefut-engine.exe)..."
  nsExec::ExecToLog 'taskkill /F /T /IM "${MAINBINARYNAME}.exe"'
  Pop $0
  nsExec::ExecToLog 'taskkill /F /T /IM "edgefut-engine.exe"'
  Pop $0
  ; dá tempo ao SO para libertar os handles dos ficheiros antes de sobrescrever/apagar
  Sleep 1500
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro _EdgeFutStopProcesses
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro _EdgeFutStopProcesses
!macroend
