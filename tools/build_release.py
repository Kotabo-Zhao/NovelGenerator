"""Reproducible local Windows/Android build from the single source tree."""
from pathlib import Path
import argparse
import os
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--target',choices=['all','windows','android'],default='all')
args=parser.parse_args()


def run(*command):
    subprocess.run(command,cwd=ROOT,check=True)


run(sys.executable,str(ROOT/'tools/prepare_frontend.py'))
if args.target in ['all','windows']:
    run(sys.executable,str(ROOT/'tools/build_windows.py'))
if args.target in ['all','android']:
    run(sys.executable,str(ROOT/'tools/sync_frontend.py'))
    run(sys.executable,str(ROOT/'tools/sync_android_core.py'))
    env=os.environ.copy()
    env.setdefault('JAVA_HOME','C:/dev/jdk-17')
    env.setdefault('ANDROID_HOME','C:/dev/android-sdk')
    env.setdefault('ANDROID_SDK_ROOT',env['ANDROID_HOME'])
    env.setdefault('CHAQUOPY_BUILD_PYTHON','C:/dev/python-3.13/python.exe')
    subprocess.run([str(ROOT/'android/gradlew.bat'),'-p',str(ROOT/'android'),'--no-daemon','assembleDebug'],cwd=ROOT,env=env,check=True)
