@echo off
setlocal enabledelayedexpansion

set "FQBN=arduino:renesas_uno:nanor4"
set "PORT=COM3"
set "SKETCH=%~dp0..\ink"

set "CMD=%~1"

if /I "%CMD%"=="compile" (
    call :do_compile
    exit /b !errorlevel!
)
if /I "%CMD%"=="upload" (
    call :do_upload
    exit /b !errorlevel!
)
if /I "%CMD%"=="monitor" (
    call :do_monitor
    exit /b !errorlevel!
)
if /I "%CMD%"=="execute" (
    call :do_compile
    if !errorlevel! neq 0 exit /b !errorlevel!
    call :do_upload
    if !errorlevel! neq 0 exit /b !errorlevel!
    call :do_monitor
    exit /b !errorlevel!
)

echo Usage: ink [compile^|upload^|monitor^|execute]
exit /b 1

:do_compile
echo [ink] compiling...
arduino-cli compile --fqbn %FQBN% "%SKETCH%"
goto :eof

:do_upload
echo [ink] uploading to %PORT%...
arduino-cli upload -p %PORT% --fqbn %FQBN% "%SKETCH%"
goto :eof

:do_monitor
echo [ink] monitoring %PORT% (Ctrl-C to exit)...
arduino-cli monitor -p %PORT% --config baudrate=115200
goto :eof
