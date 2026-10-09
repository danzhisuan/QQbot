@echo off
REM ============================================================
REM  SSH tunnels to the remote server
REM
REM    local 127.0.0.1:18080  -->  bot admin panel   (NoneBot side)
REM    local 127.0.0.1:6099   -->  napcat WebUI      (QQ account side)
REM
REM  Both NapCat and NoneBot run in Docker ON THE SERVER and talk to
REM  each other over the Docker network, so the BOT ITSELF NEEDS NO
REM  TUNNEL. These are only for opening the two web UIs locally.
REM
REM  After it starts, open in your browser:
REM      http://127.0.0.1:18080/admin      <- bot admin panel
REM      http://127.0.0.1:6099/webui       <- NapCat WebUI
REM
REM  Tokens:
REM      admin panel : WEBADMIN_TOKEN in ~/bot/.env
REM      NapCat WebUI: ssh myserver "docker logs napcat 2>&1 | grep 'WebUi Token'"
REM
REM  Keep this window OPEN while using the UIs. Ctrl+C to stop.
REM
REM  NOTE: this file is deliberately ASCII-only. cmd.exe reads .bat
REM  files using the OEM codepage, so UTF-8 Chinese comments become
REM  garbage. Keep it English.
REM
REM  NOTE: ">" inside an echo must be escaped as "^>", otherwise cmd
REM  treats it as a redirection and creates a junk file.
REM ============================================================

setlocal
set ADMIN_PORT=18080
set NAPCAT_PORT=6099
set SSH_HOST=myserver

REM ---- Preflight: is a previous tunnel still holding the ports? ----
REM Without this check, ssh fails to bind with ExitOnForwardFailure and
REM exits silently, so the OTHER port never gets forwarded either and the
REM browser just says "can't reach this page".
set BUSY=
for %%P in (%ADMIN_PORT% %NAPCAT_PORT%) do (
    netstat -ano | findstr /R /C:"LISTENING" | findstr /C:":%%P " >nul 2>&1
    if not errorlevel 1 (
        echo   [!] port %%P is already in use
        set BUSY=1
    )
)

if defined BUSY (
    echo.
    echo   A tunnel from a previous run is probably still alive.
    echo   It must be closed first, otherwise the new one cannot bind.
    echo.
    echo   WARNING: killing ssh.exe also closes any OTHER ssh session
    echo            you may have open ^(e.g. a terminal to the server^).
    echo.
    choice /C YN /N /M "  Kill all ssh.exe and retry? [Y/N] "
    if errorlevel 2 (
        echo.
        echo   Aborted. Close the old tunnel window manually, then re-run.
        pause
        exit /b 1
    )
    taskkill /F /IM ssh.exe >nul 2>&1
    timeout /t 2 /nobreak >nul
    echo   old tunnels closed.
)

echo.
echo  ================================================
echo   Opening two tunnels to %SSH_HOST%
echo.
echo   Bot admin panel :  http://127.0.0.1:%ADMIN_PORT%/admin
echo   NapCat WebUI    :  http://127.0.0.1:%NAPCAT_PORT%/webui
echo.
echo   Keep this window OPEN while using them.
echo   Press Ctrl+C to stop.
echo  ================================================
echo.

ssh -N ^
    -L %ADMIN_PORT%:127.0.0.1:%ADMIN_PORT% ^
    -L %NAPCAT_PORT%:127.0.0.1:%NAPCAT_PORT% ^
    -o ServerAliveInterval=30 ^
    -o ServerAliveCountMax=3 ^
    -o ExitOnForwardFailure=yes ^
    %SSH_HOST%

echo.
echo  [!] Tunnel closed.
echo      If it closed immediately, check:
echo        - server reachable?      ping 203.0.113.10
echo        - ports busy locally?    netstat -ano ^| findstr ":18080 :6099"
echo        - ssh key still valid?   ssh %SSH_HOST% "echo ok"
echo.
pause
