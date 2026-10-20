"""Version resources by content so old WebView/service-worker caches cannot mix builds."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def main():
    web = ROOT / 'web'
    resources = {('/assets/' + path.name): hashlib.sha256(path.read_bytes()).hexdigest()[:12]
                 for path in sorted((web/'assets').iterdir()) if path.is_file()}
    pages = ['index.html','roleplay.html','scene.html','cards.html']
    for name in pages:
        path = web/name
        source = path.read_text(encoding='utf-8')
        def version(match):
            prefix, asset, suffix = match.groups()
            return prefix + asset + '?v=' + resources[asset] + suffix
        source = re.sub(r'((?:src|href)=")(/assets/[^"?]+)(?:\?v=[^"&]+)?(")',version,source)
        path.write_text(source,encoding='utf-8')
    fingerprint = hashlib.sha256(''.join(resources.values()).encode()+b''.join((web/name).read_bytes() for name in pages)).hexdigest()[:12]
    shell = ['/', '/roleplay.html','/scene.html','/cards.html','/vue.global.prod.js','/manifest.json']
    shell += [asset+'?v='+version for asset,version in resources.items()]
    path = web/'sw.js'
    source = path.read_text(encoding='utf-8')
    source = re.sub(r"const CACHE = .*?;", "const CACHE = 'novel-workshop-"+fingerprint+"';",source,count=1)
    source = re.sub(r'const SHELL = .*?;', 'const SHELL = '+json.dumps(shell)+';',source,count=1)
    path.write_text(source,encoding='utf-8')
    print('Frontend resource set:', fingerprint)


if __name__=='__main__':
    main()
