@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist backups mkdir backups
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmm"') do set TS=%%i
copy /Y data\gogreen.sqlite3 backups\shop_%TS%.sqlite3
echo تم: backups\shop_%TS%.sqlite3
echo انسخ الملف ده على فلاشة أو درايف (نسخة برة الجهاز) مرة في الأسبوع على الأقل.
pause
