@echo off
rem Abre una aplicacion o juego. Usa herramientas.exe (version empaquetada) o, si no esta, el Python del entorno.
if exist "%~dp0..\herramientas.exe" goto exe
"%~dp0..\.venv\Scripts\python.exe" "%~dp0..\herramientas.py" abrir %*
exit /b %errorlevel%
:exe
"%~dp0..\herramientas.exe" abrir %*
