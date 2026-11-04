Set ws = CreateObject("Wscript.Shell")
ws.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""E:\hindsight\start-hindsight.ps1""", 0, False
