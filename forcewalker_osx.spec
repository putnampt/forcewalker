# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['forcewalker.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('app_data/splash.png', 'app_data'),  # Include the splash image
    ],
    hiddenimports=[
        'tkinter',
        'serial',
        'serial.tools.list_ports',
        'threading',
        'time',
        'pandas',
        'PIL',
        'PIL.Image',
        'PIL.ImageTk',
        'h5py',
        'numpy',
        'matplotlib',
        'matplotlib.backends.backend_tkagg',
        'nest_asyncio',
        'gdx'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'scikit-learn',
        'joblib'
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='WalkerForceMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Set to True if you want to see console output for debugging
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # Add icon='app_data/icon.icns' if you have an icon file
)

app = BUNDLE(
    exe,
    name='WalkerForceMonitor.app',
    icon=None,  # Add icon path here if you have one
    bundle_identifier='com.yourname.walkerforceMonitor',
    info_plist={
        'NSHighResolutionCapable': 'True',
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHumanReadableCopyright': 'Copyright © 2025 Your Name. All rights reserved.',
        'CFBundleDocumentTypes': [
            {
                'CFBundleTypeName': 'Walker Force Data',
                'CFBundleTypeExtensions': ['h5', 'xlsx'],
                'CFBundleTypeRole': 'Editor',
            }
        ]
    },
)