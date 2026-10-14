Set fso = CreateObject("Scripting.FileSystemObject")
Set ws = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
py = "C:\Users\13367\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then py = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then py = "pythonw"
ws.CurrentDirectory = root
ws.Run """" & py & """ """ & root & "\run.py"" --no-browser", 0, False
