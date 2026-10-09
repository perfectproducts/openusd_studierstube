"""Compile fmu_src/Rotator with MSVC (win64) and package it as fmus/Rotator.fmu.

Needs the Visual Studio C++ build tools. The FMU contains the sources and a win64 binary only.
"""
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).parent.resolve()
SRC = HERE / "fmu_src" / "Rotator"
OUT = HERE / "fmus" / "Rotator.fmu"
NAME = "Rotator"
VS = Path(r"C:\Program Files\Microsoft Visual Studio\2022")
VCVARS = [VS / edition / r"VC\Auxiliary\Build\vcvars64.bat" for edition in ("Community", "Professional", "Enterprise")]
VCVARS.append(Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"))

vcvars = next(p for p in VCVARS if p.exists())
build = Path(tempfile.mkdtemp(prefix="rotator_fmu_"))
dll = build / f"{NAME}.dll"
cmd = (f'"{vcvars}" >nul && cl /nologo /LD /O2 /W3 /I"{SRC}" "{SRC / "all.c"}" '
       f'/Fo"{build.as_posix()}/" /Fe"{dll}"')
subprocess.run(cmd, shell=True, check=True, cwd=build)

OUT.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    z.write(SRC / "modelDescription.xml", "modelDescription.xml")
    for name in ("all.c", "config.h", "cosimulation.c", "cosimulation.h", "fmi2Functions.c", "fmi2Functions.h", "fmi2FunctionTypes.h", "fmi2TypesPlatform.h", "model.c", "model.h"):
        z.write(SRC / name, f"sources/{name}")
    z.write(dll, f"binaries/win64/{NAME}.dll")
shutil.rmtree(build, ignore_errors=True)
print("wrote", OUT)
