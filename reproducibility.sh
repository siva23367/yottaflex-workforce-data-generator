#!/usr/bin/env bash
set -euo pipefail
python generate.py --seed 20261022 --employees 50 --teams 5 --days 14 --leaves 100 --out data/repro_a
python generate.py --seed 20261022 --employees 50 --teams 5 --days 14 --leaves 100 --out data/repro_b
python - <<'PY'
import hashlib
from pathlib import Path
for name in sorted(p.name for p in Path('data/repro_a').glob('*.csv')):
    a=hashlib.sha256((Path('data/repro_a')/name).read_bytes()).hexdigest()
    b=hashlib.sha256((Path('data/repro_b')/name).read_bytes()).hexdigest()
    assert a == b, f'checksum mismatch: {name}'
print('Reproducibility PASS: all CSV checksums match')
PY
