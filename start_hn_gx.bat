@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion

rem Simple start script for hn_gx branch
rem Start only. No git, dependency install, port cleanup or health checks.

set "PROJECT_ROOT=%~dp0"
set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "BACKEND_DIR=%PROJECT_ROOT%\backend"
set "FRONTEND_DIR=%PROJECT_ROOT%\frontend"
set "MCP_DIR=%PROJECT_ROOT%\backend-mcp"

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

set "BACKEND_LOG=%PROJECT_ROOT%\backend.log"
set "FRONTEND_LOG=%PROJECT_ROOT%\frontend.log"
set "MCP_LOG=%PROJECT_ROOT%\mcp.log"

if not exist "%BACKEND_LOG%" type nul > "%BACKEND_LOG%"
if not exist "%FRONTEND_LOG%" type nul > "%FRONTEND_LOG%"
if not exist "%MCP_LOG%" type nul > "%MCP_LOG%"

if defined MCP_TRANSPORT_MODE (
    set "TRANSPORT_MODE=%MCP_TRANSPORT_MODE%"
) else (
    set "TRANSPORT_MODE=sse"
)

set "VENV_DIR=%BACKEND_DIR%\.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"

cd /d "%BACKEND_DIR%"
set "SAU_PORT=5409"
if exist "%VENV_PYTHON%" (
    start "SAU-Backend" /B cmd /c ""%VENV_PYTHON%" app.py > "%BACKEND_LOG%" 2>&1"
) else (
    start "SAU-Backend" /B cmd /c "python app.py > "%BACKEND_LOG%" 2>&1"
)

cd /d "%FRONTEND_DIR%"
start "SAU-Frontend" /B cmd /c "npm run dev > "%FRONTEND_LOG%" 2>&1"

cd /d "%MCP_DIR%"
start "SAU-MCP" /B cmd /c "set TRANSPORT_MODE=%TRANSPORT_MODE%&& npm start > "%MCP_LOG%" 2>&1"

cd /d "%PROJECT_ROOT%"
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
echo Press Ctrl+C to stop this log window.
echo.
echo --- Backend Log ---
powershell -Command "Get-Content '%BACKEND_LOG%' -Wait -Tail 50"
