from PyInstaller.utils.hooks import collect_data_files, copy_metadata


analysis = Analysis(
    ["app.py"],
    datas=collect_data_files("rapidocr_onnxruntime") + copy_metadata("onnxruntime")
    + [("character-guides.json", "."), ("kuro-character-guides.json", ".")],
    excludes=["paddle", "paddleocr", "pandas", "scipy", "matplotlib", "IPython", "torch",
              "jupyter", "pytest"],
)
archive = PYZ(analysis.pure)
exe = EXE(
    archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="MingchaoHelp",
    console=False,
    upx=False,
)
