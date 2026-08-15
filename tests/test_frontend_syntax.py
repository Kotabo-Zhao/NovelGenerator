import json
import shutil
import subprocess
from pathlib import Path

import pytest


FRONTEND_FILES = (
    Path(__file__).parents[1] / "web" / "index.html",
    Path(__file__).parents[1] / "backend" / "web" / "index.html",
    Path(__file__).parents[1]
    / "android"
    / "app"
    / "src"
    / "main"
    / "python"
    / "web"
    / "index.html",
)


def test_embedded_frontend_scripts_are_valid_javascript():
    """Catch malformed inline scripts before the browser reports a syntax error."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to parse embedded frontend scripts")

    parser = r"""
const fs = require('fs');
const vm = require('vm');
const files = JSON.parse(process.argv[1]);
const errors = [];

for (const file of files) {
  const html = fs.readFileSync(file, 'utf8');
  const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/gi)];
  scripts.forEach((match, index) => {
    try {
      new vm.Script(match[1], {filename: `${file}#script-${index}`});
    } catch (error) {
      errors.push(`${file}#script-${index}: ${error.message}`);
    }
  });
}

if (errors.length) {
  console.error(errors.join('\n'));
  process.exit(1);
}
"""

    result = subprocess.run(
        [node, "-e", parser, json.dumps([str(path) for path in FRONTEND_FILES])],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
