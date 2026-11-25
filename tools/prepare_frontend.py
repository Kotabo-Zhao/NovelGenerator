"""Version resources by content so old WebView/service-worker caches cannot mix builds."""
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    checking = "--check" in sys.argv
    web = ROOT / 'web'
    resources = {('/assets/' + path.name): hashlib.sha256(path.read_bytes()).hexdigest()[:12]
                 for path in sorted((web / 'assets').iterdir()) if path.is_file()}
    pages = ['index.html', 'roleplay.html', 'scene.html', 'cards.html']
    stale = []
    updated_pages = {}
    for name in pages:
        path = web / name
        original = path.read_text(encoding='utf-8')
        def version(match):
            prefix, asset, suffix = match.groups()
            return prefix + asset + '?v=' + resources[asset] + suffix
        updated = re.sub(r'((?:src|href)=")(/assets/[^"?]+)(?:\?v=[^"&]+)?(")', version, original)
        updated_pages[name] = updated.encode('utf-8')
        if updated != original:
            stale.append(name)
            if not checking:
                path.write_text(updated, encoding='utf-8')
    fingerprint = hashlib.sha256(
        ''.join(resources.values()).encode() + b''.join(updated_pages[name] for name in pages)
    ).hexdigest()[:12]
    shell = ['/', '/roleplay.html', '/scene.html', '/cards.html', '/vue.global.prod.js', '/manifest.json']
    shell += [asset + '?v=' + ver for asset, ver in resources.items()]
    sw_path = web / 'sw.js'
    sw_orig = sw_path.read_text(encoding='utf-8')
    sw_new = re.sub(r"const CACHE = .*?;", "const CACHE = 'novel-workshop-" + fingerprint + "';", sw_orig, count=1)
    sw_new = re.sub(r'const SHELL = .*?;', 'const SHELL = ' + json.dumps(shell) + ';', sw_new, count=1)
    if sw_new != sw_orig:
        stale.append('sw.js')
        if not checking:
            sw_path.write_text(sw_new, encoding='utf-8')
    if checking and stale:
        print('Frontend cache hashes out of date:', ', '.join(stale))
        return 1
    print(f"Frontend resource set ({'verified' if checking else 'updated'}): {fingerprint}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
