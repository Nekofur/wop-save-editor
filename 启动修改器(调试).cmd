@echo off
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "TARGET=%HERE%wop_editor.py"

if exist "D:\Python\python.exe" goto run_default
where py >nul 2>nul
if errorlevel 1 goto no_python
echo 使用 py -3 启动（控制台可见报错）...
py -3 -X utf8 "%TARGET%"
goto end

:run_default
echo 使用 D:\Python\python.exe 启动（控制台可见报错）...
"D:\Python\python.exe" -X utf8 "%TARGET%"
goto end

:no_python
echo 没有找到可用的 Python 3。
pause
exit /b 1

:end
echo.
echo 程序已退出，退出码 %errorlevel%。
pause
