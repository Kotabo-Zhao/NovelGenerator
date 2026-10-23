"""Build the portable desktop folder. Never include user data or credentials."""
from pathlib import Path
import argparse
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--output-dir', type=Path, default=ROOT/'dist/windows-4.3.3')
args = parser.parse_args()
subprocess.run([sys.executable, str(ROOT/'tools/prepare_frontend.py')], cwd=ROOT, check=True)
command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
           '--name', 'NovelGenerator', '--distpath', str(args.output_dir.resolve()),
           '--workpath', str(ROOT/'dist/windows-build'), '--specpath', str(ROOT/'dist/windows-build'),
           '--paths', str(ROOT/'backend'), '--add-data', str(ROOT/'web')+';web',
           '--collect-all', 'webview', '--hidden-import', 'uvicorn.logging',
           '--hidden-import', 'uvicorn.loops.auto', '--hidden-import', 'uvicorn.protocols.http.h11_impl',
           '--hidden-import', 'uvicorn.protocols.websockets.websockets_impl', '--hidden-import', 'uvicorn.lifespan.on',
           '--exclude-module', 'PyQt5', '--exclude-module', 'PyQt6', '--exclude-module', 'PySide6',
           str(ROOT/'desktop/main.py')]
subprocess.run(command, cwd=ROOT, check=True)
