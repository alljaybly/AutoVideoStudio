@echo off
setlocal
pushd "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3.12 -c "import sys" >nul 2>nul
    if not errorlevel 1 (
        py -3.12 -m unittest discover -s tests -v
        goto done
    )
)
python -m unittest discover -s tests -v
:done
set RESULT=%ERRORLEVEL%
echo Test process exit code: %RESULT%
popd
pause
exit /b %RESULT%
