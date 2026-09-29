@echo off
rem Anota en el bloc de Asistemis. Usa herramientas.exe (version empaquetada) o, si no esta, el Python del entorno.
if exist "%~dp0..\herramientas.exe" goto exe
"%~dp0..\.venv\Scripts\python.exe" "%~dp0..\herramientas.py" anotar %*
exit /b %errorlevel%
:exe
"%~dp0..\herramientas.exe" anotar %*
