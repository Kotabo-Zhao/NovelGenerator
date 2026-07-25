"""Patch android server.py WEB_DIR and remove load_dotenv."""
with open('android/app/src/main/python/api/server.py', 'r', encoding='utf-8') as f:
    c = f.read()

# Fix 1: WEB_DIR for Android (2 levels up vs 3 on PC)
old_web = '_WEB_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\nWEB_DIR = os.path.join(_WEB_BASE, "web")'
new_web = '_HERE = os.path.dirname(os.path.abspath(__file__))\n_env_web = os.environ.get("NOVELGEN_WEB_DIR", "")\nWEB_DIR = _env_web if _env_web else os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_HERE))), "web")'

if old_web in c:
    c = c.replace(old_web, new_web)
    print("Patched WEB_DIR")
else:
    print("WEB_DIR already patched or not found")

# Fix 2: Remove load_dotenv call (doesn't exist on Android)
c = c.replace('from dotenv import load_dotenv\n\nload_dotenv()\n\n', '')
c = c.replace('from dotenv import load_dotenv\nload_dotenv()', '')
print("Removed load_dotenv")

# Fix 3: Ensure import re exists
if 'import re' not in c[:500]:
    c = c.replace('import os\n', 'import os\nimport re\n', 1)
    print("Added import re")

with open('android/app/src/main/python/api/server.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Done")
