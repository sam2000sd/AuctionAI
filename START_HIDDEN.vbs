Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
AppFolder = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = AppFolder
WshShell.Run "cmd /c """ & AppFolder & "\START_VISIBLE.bat""", 0, False
