' Launch the GPU watchdog with no visible window.
'
' Task Scheduler starts this under wscript.exe (//B), which runs gpu_watchdog.ps1
' with a hidden window (Run's window style 0). Running the .ps1 directly from a
' scheduled task flashes a PowerShell console every time the task fires; this
' launcher avoids that. The task itself is documented in
' docs/user/02_configuration.md.
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = fso.BuildPath(scriptDir, "gpu_watchdog.ps1")
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & ps1 & """", 0, False
