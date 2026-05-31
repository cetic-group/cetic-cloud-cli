# PyInstaller spec for cetic CLI
# Build: pyinstaller cetic.spec
# Cross-build: set PYI_TARGET_ARCH (e.g. x86_64) to override the target architecture.
import os

block_cipher = None
target_arch = os.environ.get("PYI_TARGET_ARCH") or None

a = Analysis(
    ['cetic/main.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[
        # shellingham posix/windows submodules needed for --install-completion
        'shellingham',
        'shellingham.posix',
        'shellingham.windows',
        # typer completion modules
        'typer',
        'typer.completion',
        'click',
        'click._compat',
        # keyring backends
        'keyring',
        'keyring.backends',
        'keyring.backends.SecretService',
        'keyring.backends.kwallet',
        'keyring.backends.macOS',
        'keyring.backends.Windows',
        'keyring.backends.null',
        'keyring.backends.fail',
        'keyrings.alt',
        'keyrings.alt.file',
        'keyrings.alt.Gnome',
        # platformdirs
        'platformdirs',
        'platformdirs.unix',
        'platformdirs.windows',
        'platformdirs.macos',
        # httpx
        'httpx',
        'httpcore',
        # pydantic
        'pydantic',
        'pydantic_core',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='cetic',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=target_arch,
    codesign_identity=None,
    entitlements_file=None,
)
