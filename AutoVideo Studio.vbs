Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

' Get the folder where this .vbs file is located
appDir = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = appDir

' Set the TCL/TK environment variables for Python
WshShell.Environment("Process")("TCL_LIBRARY") = appDir & "\runtime\Lib\tcl\tcl8.6"
WshShell.Environment("Process")("TK_LIBRARY") = appDir & "\runtime\Lib\tcl\tk8.6"

' Run pythonw.exe with the path override, hidden
WshShell.Run "runtime\pythonw.exe -c ""import sys, os; sys.path.insert(0, os.getcwd()); import main; sys.exit(main.main())""", 0, False