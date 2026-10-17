@echo off
setlocal
pushd "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 -c "import sys" >nul 2>nul
    if not errorlevel 1 (
        py -3.12 main.py
        goto done
    )
)
where python >nul 2>nul
if not errorlevel 1 (
    python main.py
    goto done
)
echo Python was not found. Install Python 3.12 with Tcl/Tk from python.org.
:done
if errorlevel 1 echo Launch failed. Read the message above and README.md.
popd
pause
endlocal
