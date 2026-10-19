"""Generate Android package assets from the canonical web/ directory."""
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "web"
TARGET = ROOT / "android/app/src/main/python/web"


def main():
    checking = "--check" in sys.argv
    changed = []
    source_files = {p.relative_to(SOURCE): p for p in SOURCE.rglob("*") if p.is_file()}
    for relative, source in source_files.items():
        target = TARGET / relative
        if not target.is_file() or source.read_bytes() != target.read_bytes():
            changed.append(str(relative))
            if not checking:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    stale = [p for p in TARGET.rglob("*") if p.is_file() and p.relative_to(TARGET) not in source_files]
    for path in stale:
        changed.append("stale: " + str(path.relative_to(TARGET)))
        # Only generated frontend files, inside this exact destination.
        if not checking and path.resolve().is_relative_to(TARGET.resolve()):
            path.unlink()
    if checking and changed:
        print("Frontend copies differ:", ", ".join(changed))
        return 1
    print(f"Frontend {'verified' if checking else 'generated'}: {len(source_files)} files, {len(changed)} changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
