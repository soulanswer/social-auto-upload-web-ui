@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions EnableDelayedExpansion

rem Start/stop script for the hn_gx branch.
rem It owns ports 5409 (backend), 5173 (frontend), and 5410 (MCP).

set "PROJECT_ROOT=%~dp0"
set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "BACKEND_DIR=%PROJECT_ROOT%\backend"
set "FRONTEND_DIR=%PROJECT_ROOT%\frontend"
set "MCP_DIR=%PROJECT_ROOT%\backend-mcp"
set "BACKEND_LOG=%PROJECT_ROOT%\backend.log"
set "FRONTEND_LOG=%PROJECT_ROOT%\frontend.log"
set "MCP_LOG=%PROJECT_ROOT%\mcp.log"
set "VENV_DIR=%BACKEND_DIR%\.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"

if /i "%~1"=="stop" (
    call :stop_project_services
    exit /b !errorlevel!
)

if not "%~1"=="" (
    echo Usage: %~nx0 [stop]
    exit /b 2
)

if exist "%PROJECT_ROOT%\dependency\bin" (
    set "PATH=%PROJECT_ROOT%\dependency\bin;%PATH%"
)
if exist "%PROJECT_ROOT%\dependency\python" (
    set "PATH=%PROJECT_ROOT%\dependency\python;%PROJECT_ROOT%\dependency\python\Scripts;%PATH%"
)
if exist "%PROJECT_ROOT%\dependency\git\cmd" (
    set "PATH=%PROJECT_ROOT%\dependency\git\cmd;%PATH%"
)
if exist "%PROJECT_ROOT%\dependency\node" (
    set "PATH=%PROJECT_ROOT%\dependency\node;%PATH%"
)
if exist "%PROJECT_ROOT%\dependency\cloakbrowser\chrome.exe" (
    set "CLOAKBROWSER_BINARY_PATH=%PROJECT_ROOT%\dependency\cloakbrowser\chrome.exe"
)

if defined MCP_TRANSPORT_MODE (
    set "TRANSPORT_MODE=%MCP_TRANSPORT_MODE%"
) else (
    set "TRANSPORT_MODE=sse"
)

call :stop_project_services
if errorlevel 1 (
    echo Failed to release application ports. Startup cancelled.
    exit /b 1
)

rem Clear stale output so readiness checks only inspect this launch.
type nul > "%BACKEND_LOG%"
type nul > "%FRONTEND_LOG%"
type nul > "%MCP_LOG%"

cd /d "%BACKEND_DIR%"
set "SAU_PORT=5409"
if exist "%VENV_PYTHON%" (
    start "SAU-Backend" /B "%ComSpec%" /d /s /c ""%VENV_PYTHON%" app.py > "%BACKEND_LOG%" 2>&1"
) else (
    start "SAU-Backend" /B "%ComSpec%" /d /s /c "python app.py > "%BACKEND_LOG%" 2>&1"
)
call :wait_for_http "http://127.0.0.1:5409/api/health" "Backend"
if errorlevel 1 goto :startup_failed

cd /d "%FRONTEND_DIR%"
rem Vite closes its dev server when a detached child inherits a closed stdin.
rem CI=true disables that stdin-end handler while preserving normal serving.
set "VITE_PARENT_CI=%CI%"
set "CI=true"
start "SAU-Frontend" /B "%ComSpec%" /d /s /c "call npm run dev -- --host 127.0.0.1 --strictPort > "%FRONTEND_LOG%" 2>&1"
set "CI=%VITE_PARENT_CI%"
set "VITE_PARENT_CI="
call :wait_for_http "http://127.0.0.1:5173/" "Frontend"
if errorlevel 1 goto :startup_failed

cd /d "%MCP_DIR%"
start "SAU-MCP" /B "%ComSpec%" /d /s /c "set TRANSPORT_MODE=%TRANSPORT_MODE%&& call npm start > "%MCP_LOG%" 2>&1"
if /i not "%TRANSPORT_MODE%"=="stdio" (
    call :wait_for_port 5410 "MCP"
    if errorlevel 1 goto :startup_failed
)

call :write_pid_file 5409 "%PROJECT_ROOT%\.backend.pid"
call :write_pid_file 5173 "%PROJECT_ROOT%\.frontend.pid"
if /i not "%TRANSPORT_MODE%"=="stdio" call :write_pid_file 5410 "%PROJECT_ROOT%\.mcp.pid"

cd /d "%PROJECT_ROOT%"
echo.
echo ============================================
echo   Frontend: http://localhost:5173
echo   Backend:  http://localhost:5409
if /i "%TRANSPORT_MODE%"=="sse" (
    echo   MCP SSE:  http://localhost:5410/sse
) else if /i "%TRANSPORT_MODE%"=="both" (
    echo   MCP SSE:  http://localhost:5410/sse
) else (
    echo   MCP mode: %TRANSPORT_MODE%
)
echo ============================================
echo.
echo This window only follows the backend log.
echo To stop all application services, run: %~nx0 stop
echo.
echo --- Backend Log ---
powershell -NoProfile -Command "Get-Content '%BACKEND_LOG%' -Wait -Tail 50"
exit /b 0

:startup_failed
echo.
echo Service startup failed. The partial launch will be stopped.
call :stop_project_services
echo Review these logs before trying again:
echo   %BACKEND_LOG%
echo   %FRONTEND_LOG%
echo   %MCP_LOG%
pause
exit /b 1

:stop_project_services
rem Stop every process tree listening on this application's fixed ports.
echo.
echo Releasing application ports...
call :stop_port 5409 "Backend"
if errorlevel 1 exit /b 1
call :stop_port 5173 "Frontend"
if errorlevel 1 exit /b 1
call :stop_port 5410 "MCP"
if errorlevel 1 exit /b 1
exit /b 0

:stop_port
rem Terminate listeners and their child processes, then wait until the port is free.
set "PORT=%~1"
set "SERVICE_NAME=%~2"
set "FOUND_LISTENER="
for /f "tokens=5" %%P in ('netstat -aon ^| findstr /R /C:":%PORT% .*LISTENING" 2^>nul') do (
    set "FOUND_LISTENER=1"
    echo   Stopping !SERVICE_NAME! listener ^(PID %%P^) on port !PORT!...
    taskkill /F /T /PID %%P >nul 2>&1
)

if not defined FOUND_LISTENER (
    echo   Port !PORT! is already free.
    exit /b 0
)

set /a "PORT_WAIT=0"
:wait_port_release
set /a "PORT_WAIT+=1"
netstat -aon | findstr /R /C:":%PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    if !PORT_WAIT! GEQ 10 (
        echo   Port !PORT! is still occupied after 10 seconds.
        exit /b 1
    )
    call :sleep_one_second
    goto wait_port_release
)
echo   Port !PORT! released.
exit /b 0

:wait_for_http
rem Poll an HTTP endpoint so a stale process can never be reported as ready.
set "HEALTH_URL=%~1"
set "HEALTH_NAME=%~2"
set /a "HEALTH_WAIT=0"
:wait_for_http_loop
set /a "HEALTH_WAIT+=1"
curl.exe -s -o nul -w "%%{http_code}" --max-time 2 "%HEALTH_URL%" 2>nul | findstr "200" >nul
if not errorlevel 1 (
    echo   !HEALTH_NAME! is ready.
    exit /b 0
)
if !HEALTH_WAIT! GEQ 30 (
    echo   !HEALTH_NAME! did not become ready within 30 seconds.
    exit /b 1
)
    call :sleep_one_second
goto wait_for_http_loop

:wait_for_port
rem Poll a listener-only service such as MCP SSE.
set "WAIT_PORT=%~1"
set "WAIT_NAME=%~2"
set /a "LISTENER_WAIT=0"
:wait_for_port_loop
set /a "LISTENER_WAIT+=1"
netstat -aon | findstr /R /C:":%WAIT_PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo   !WAIT_NAME! is ready.
    exit /b 0
)
if !LISTENER_WAIT! GEQ 30 (
    echo   !WAIT_NAME! did not become ready within 30 seconds.
    exit /b 1
)
    call :sleep_one_second
goto wait_for_port_loop

:write_pid_file
rem Persist the single listener PID created by this launch for diagnostics.
set "PID_PORT=%~1"
set "PID_FILE=%~2"
set "LISTENER_PID="
for /f "tokens=5" %%P in ('netstat -aon ^| findstr /R /C:":%PID_PORT% .*LISTENING" 2^>nul') do set "LISTENER_PID=%%P"
if defined LISTENER_PID (
    > "%PID_FILE%" echo !LISTENER_PID!
) else (
    if exist "%PID_FILE%" del /q "%PID_FILE%" >nul 2>&1
)
exit /b 0

:sleep_one_second
rem Wait without depending on an interactive console input handle.
powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>&1
exit /b 0
