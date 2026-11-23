@echo off
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "TARGET=%HERE%wop_editor.py"
set "LOGDIR=%HERE%logs"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"
del /q "%LOGDIR%\startup.ok" >nul 2>nul

set "PY=D:\Python\python.exe"
set "PYW=D:\Python\pythonw.exe"
if not exist "%PY%" goto use_launcher
if not exist "%PYW%" goto use_launcher
start "" "%PYW%" -X utf8 "%TARGET%"
goto wait

:use_launcher
where py >nul 2>nul
if errorlevel 1 goto no_python
start "" py -3 -X utf8 "%TARGET%"
goto wait

:wait
set /a tries=0
:loop
timeout /t 1 /nobreak >nul
if exist "%LOGDIR%\startup.ok" exit /b 0
set /a tries+=1
if %tries% lss 8 goto loop
echo.
echo 修改器没有成功启动，下面是最近的错误日志：
echo ------------------------------------------------------------
if exist "%LOGDIR%\crash.log" type "%LOGDIR%\crash.log"
if exist "%LOGDIR%\crash.log" goto done
echo 没有 crash.log。通常是 Python 缺少 tkinter，或游戏目录配置 config.json 不对。
echo 可以双击“启动修改器(调试).cmd”在控制台里直接看报错。
:done
echo ------------------------------------------------------------
pause
exit /b 1

:no_python
echo.
echo 没有找到可用的 Python 3（需要带 tkinter 的标准版）。
echo 当前脚本默认使用 D:\Python\python.exe，请安装 Python 3 或修改本文件里的路径。
echo.
pause
exit /b 1
