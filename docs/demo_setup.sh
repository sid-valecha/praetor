#!/bin/bash
set -euo pipefail

mkdir -p /tmp/praetor-demo-shim
python - <<'PY'
from pathlib import Path

shim = Path("/tmp/praetor-demo-shim/claude")
shim.write_text(
    """#!/bin/bash
set -euo pipefail
prompt="${*: -1}"
if [[ "$prompt" == *"greet"* ]]; then
  printf "def greet(name):\\n    return f\\"Hello, {name}!\\"\\n" > greet.py
  echo "Created greet.py"
elif [[ "$prompt" == *"square"* ]]; then
  printf "def square(n):\\n    return n * n\\n" > square.py
  echo "Created square.py"
elif [[ "$prompt" == *"reverse"* ]]; then
  printf "def reverse_text(s):\\n    return s[::-1]\\n" > reverse_text.py
  echo "Created reverse_text.py"
else
  echo "No matching demo task" >&2
  exit 1
fi
"""
)
shim.chmod(0o755)

repo = Path("/tmp/praetor-demo")
if repo.exists():
    import shutil

    shutil.rmtree(repo)
repo.mkdir(parents=True)
(repo / "README.md").write_text("# Praetor demo scratch\n")
(repo / "check_greet.py").write_text(
    """#!/usr/bin/env python
from greet import greet
assert greet("Praetor") == "Hello, Praetor!"
"""
)
(repo / "check_square.py").write_text(
    """#!/usr/bin/env python
from square import square
assert square(7) == 49
"""
)
(repo / "check_reverse.py").write_text(
    """#!/usr/bin/env python
from reverse_text import reverse_text
assert reverse_text("praetor") == "rotearp"
"""
)
for path in repo.glob("check_*.py"):
    path.chmod(0o755)
PY

cd /tmp/praetor-demo
git init -b main
git config user.name "Praetor Demo"
git config user.email praetor-demo@example.com
git add README.md check_*.py
git commit -m "Initial commit"
