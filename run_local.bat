@echo off
cd /d %~dp0
python -m hub.run --out public
pause
