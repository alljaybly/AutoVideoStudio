@echo off
setlocal
pushd "%~dp0"

if not exist "runtime\python.exe" (
    echo Error: The portable Python runtime is missing. Please extract the entire ZIP folder.
    pause
    exit /b 1
)

set TCL_LIBRARY=%~dp0runtime\Lib\tcl\tcl8.6
set TK_LIBRARY=%~dp0runtime\Lib\tcl\tk8.6

:: Run the app silently using pythonw.exe (the windowless version of Python)
start "" "runtime\pythonw.exe" -c "import sys, os; sys.path.insert(0, os.getcwd()); import main; sys.exit(main.main())"

popd
endlocal