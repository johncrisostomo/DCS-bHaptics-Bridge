@echo off
cd /d %~dp0
python -m bhaptics_bridge %*
pause
