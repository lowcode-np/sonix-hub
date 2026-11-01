@echo off
cd /d %~dp0
python -m hub.server --port 8080 --out public
pause
