@echo off
rem === Dai-2-Ji Super Robot Wars (Complete Box) Korean patch v0.8.7 ===
rem ASCII-only launcher. All Korean messages are printed by apply.ps1.
rem Double-click this file, or drag the retail "(Track 1).bin" onto it.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0apply.ps1" %*
rem Keep the window open no matter what (even if apply.ps1 fails to start).
pause >nul
