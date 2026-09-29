@echo off
rem Cierra una aplicacion. Usa herramientas.exe (version empaquetada) o, si no esta, el Python del entorno.
if exist "%~dp0..\herramientas.exe" goto exe
"%~dp0..\.venv\Scripts\python.exe" "%~dp0..\herramientas.py" cerrar %*
exit /b %errorlevel%
:exe
"%~dp0..\herramientas.exe" cerrar %*
