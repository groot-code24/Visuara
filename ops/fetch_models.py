"""Download object-detection models listed in app/detection.py and verify them before they are used.

    python ops/fetch_models.py                 # the configured model (CC_DETECTION_MODEL, or the default)
    python ops/fetch_models.py yolox-tiny nanodet-plus-m-416 --dir models
    python ops/fetch_models.py --all           # every candidate, for the benchmark

Each file is downloaded to a temporary name next to its destination, checked against the SHA-256 pinned in
`app.detection.MODELS`, and only then renamed into place. A file that is already present and matches is left alone. A
mismatch is an error and nothing is installed. The model weights are not kept in git; the production image runs this at
build time (Dockerfile.vercel). Licences: models/README.md.
"""
import argparse, os, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from app.detection import MODELS, model_name, models_dir, sha256  # noqa: E402


def fetch(name, directory, timeout=120):
    spec = MODELS[name]
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / spec['file']
    if dest.exists() and sha256(dest) == spec['sha256']:
        return 'present'
    part = dest.with_name(dest.name + '.download')
    req = urllib.request.Request(spec['url'], headers={'User-Agent': 'visuara-model-fetch'})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(part, 'wb') as f:
        while block := r.read(1 << 20): f.write(block)
    got = sha256(part)
    if got != spec['sha256']:
        part.unlink(missing_ok=True)
        raise SystemExit(f'{name}: the downloaded file does not match its pinned checksum; nothing was installed')
    os.replace(part, dest)
    return 'installed'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('names', nargs='*'); ap.add_argument('--all', action='store_true'); ap.add_argument('--dir')
    a = ap.parse_args(argv)
    names = sorted(MODELS) if a.all else (a.names or [model_name()])
    unknown = [n for n in names if n not in MODELS]
    if unknown: raise SystemExit(f'unknown model(s): {", ".join(unknown)}; known: {", ".join(sorted(MODELS))}')
    directory = Path(a.dir) if a.dir else models_dir()
    for n in names:
        print(f'{n}: {fetch(n, directory)} ({MODELS[n]["file"]}, {MODELS[n]["licence"]})')


if __name__ == '__main__':
    main()
