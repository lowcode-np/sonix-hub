@echo off
cd /d %~dp0
python -m bench.bench_llm
pause
