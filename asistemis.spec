# Receta de PyInstaller: compila con  .\compilar.ps1  (o: pyinstaller asistemis.spec)
# Resultado: dist\Asistemis\ con Asistemis.exe (sin consola) y herramientas.exe (la usa Claude).
# Todo queda junto al .exe (contents_directory="."), así ui/, claude/ y recursos/ están a mano.
import importlib.util

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

datas = [("ui", "ui"), ("claude", "claude"), ("recursos", "recursos")]
datas += collect_data_files("faster_whisper")   # detector de voz (Silero VAD)
datas += collect_data_files("webview")          # DLLs de WebView2 y el puente JS de pywebview
datas += collect_data_files("clr_loader")
datas += collect_data_files("pythonnet")

binaries = collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime")
for gpu in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_nvrtc"):  # solo si están instaladas (tarjeta NVIDIA)
    if importlib.util.find_spec(gpu):
        binaries += collect_dynamic_libs(gpu)

hidden = ["pystray._win32", "clr", "pynvml", "win32com.propsys", "win32com.shell"]
hidden += collect_submodules("webview.platforms")

app = Analysis(["asistemis.py"], datas=datas, binaries=binaries, hiddenimports=hidden,
               excludes=["tkinter", "customtkinter", "matplotlib", "IPython"])
tools = Analysis(["herramientas.py"], hiddenimports=["win32com.propsys", "win32com.shell"],
                 excludes=["tkinter", "numpy", "faster_whisper", "webview"])

app_exe = EXE(PYZ(app.pure), app.scripts, [], exclude_binaries=True, name="Asistemis",
              icon="recursos/asistemis.ico", version="recursos/version.txt", console=False,
              contents_directory=".")
tools_exe = EXE(PYZ(tools.pure), tools.scripts, [], exclude_binaries=True, name="herramientas",
                icon="recursos/asistemis.ico", console=True, contents_directory=".")

COLLECT(app_exe, app.binaries, app.datas, tools_exe, tools.binaries, tools.datas, name="Asistemis")
