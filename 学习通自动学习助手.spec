# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.hooks import copy_metadata

datas = []
binaries = []
hiddenimports = ['playwright', 'playwright.sync_api', 'greenlet', 'pyee', 'typing_extensions']
datas += copy_metadata('playwright')
tmp_ret = collect_all('playwright')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('greenlet')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# 验证码识别：ddddocr + onnxruntime（离线模型）+ opencv + Pillow
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

hiddenimports += collect_submodules('ddddocr')
# 只带默认识别模型 common_old.onnx（ddddocr 默认加载它），省掉 52MB
datas += collect_data_files('ddddocr', includes=['common_old.onnx'])
for _pkg in ('onnxruntime', 'cv2', 'numpy', 'PIL',
             'google.protobuf', 'flatbuffers'):
    try:
        _ret = collect_all(_pkg)
        datas += _ret[0]; binaries += _ret[1]; hiddenimports += _ret[2]
    except Exception:
        pass
hiddenimports += ['ddddocr', 'onnxruntime', 'onnxruntime.capi',
                  'onnxruntime.capi._pybind_state', 'cv2']


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='学习通自动学习助手',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
