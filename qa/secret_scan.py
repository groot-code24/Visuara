"""Secret scan for the repository or a release archive. Exit 1 if anything that looks like a credential is found.

    python qa/secret_scan.py [path ...]            # default: files tracked by git (or the working tree)
    python qa/secret_scan.py --zip release.zip
Detects private keys, cloud and provider keys, Neon/PostgreSQL URLs with passwords, Google OAuth client secrets,
SMTP passwords and generic high-entropy assignments. Known local-development placeholders are allowed.
"""
import argparse, math, re, subprocess, sys, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = {
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'aws_access_key': re.compile(r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'),
    'google_client_secret': re.compile(r'\bGOCSPX-[0-9A-Za-z_-]{20,}'),
    'google_api_key': re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b'),
    'github_token': re.compile(r'\bgh[pousr]_[0-9A-Za-z]{36,}\b'),
    'slack_token': re.compile(r'\bxox[abposr]-[0-9A-Za-z-]{10,}'),
    'stripe_key': re.compile(r'\b(?:sk|rk)_live_[0-9A-Za-z]{16,}'),
    'resend_key': re.compile(r'\bre_[0-9A-Za-z]{8,}_[0-9A-Za-z]{16,}'),
    'sendgrid_key': re.compile(r'\bSG\.[0-9A-Za-z_-]{16,}\.[0-9A-Za-z_-]{16,}'),
    'postgres_url_with_password': re.compile(r'postgres(?:ql)?://[^:\s/]+:([^@\s]+)@([^/\s?]+)'),
    'vercel_blob_token': re.compile(r'\bvercel_blob_rw_[0-9A-Za-z_-]{16,}'),
    'neon_password': re.compile(r'\bnpg_[0-9A-Za-z]{8,}'),
    'cloudflare_tunnel_token': re.compile(r'\beyJhIjoi[0-9A-Za-z_-]{40,}'),
}
ASSIGN = re.compile(r'(?i)\b(?:secret|password|passwd|token|api_key|signing_key)\b\s*[:=]\s*["\']?([^\s"\'#,;]{20,})')
ALLOWED_PASSWORDS = {'PASSWORD', 'cc-local-only', 'postgres', 'p', 'pass', 'password', 'test'}
ALLOWED_VALUES = {'local-development-signing-key-not-for-production', 'cc-local-secret-only', 'change-me-to-a-long-random-secret'}
ALLOWED_FIXTURES = {'vercel_blob_rw_TESTSTORE_secretsecretsecret', 'npg_X9y8Z7w6V5'}   # invented values the tests depend on
SKIP_DIRS = {'.git', '.venv', '.venv311', 'node_modules', '__pycache__', 'data', '.vercel'}
TEXT_EXT = {'.py', '.md', '.txt', '.json', '.yml', '.yaml', '.toml', '.cfg', '.ini', '.html', '.js', '.mjs', '.css', '.sh', '.ps1', '.example', '.env', ''}


def entropy(s):
    return -sum(s.count(c) / len(s) * math.log2(s.count(c) / len(s)) for c in set(s)) if s else 0


def scan_text(name, text):
    hits = []
    for kind, rx in PATTERNS.items():
        for m in rx.finditer(text):
            if kind == 'postgres_url_with_password' and (m.group(1) in ALLOWED_PASSWORDS or m.group(1).startswith(('USER', '$', '{', '<')) or 'localhost' in m.group(2) or m.group(2).startswith(('postgres', '127.0.0.1'))):
                continue
            if m.group(0) in ALLOWED_FIXTURES: continue
            hits.append((name, kind, m.group(0)[:12] + '…'))
    for m in ASSIGN.finditer(text):
        v = m.group(1)
        if v in ALLOWED_VALUES or v.startswith(('$', '{', '<', 'os.getenv', 'env.', 'process.env')) or '(' in v or '.' in v[:6]: continue
        if entropy(v) > 4.0 and re.search(r'\d', v) and re.search(r'[A-Za-z]', v): hits.append((name, 'high_entropy_assignment', v[:6] + '…'))
    return hits


def files_in_repo():
    try:
        out = subprocess.run(['git', 'ls-files', '-co', '--exclude-standard'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split('\n')
        return [ROOT / f for f in out if f]
    except Exception:
        return [p for p in ROOT.rglob('*') if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('paths', nargs='*'); ap.add_argument('--zip')
    a = ap.parse_args(); hits, n = [], 0
    if a.zip:
        with zipfile.ZipFile(a.zip) as z:
            for name in z.namelist():
                if name.endswith('/') or Path(name).suffix.lower() not in TEXT_EXT and not Path(name).name.startswith('.env'): continue
                n += 1; hits += scan_text(name, z.read(name).decode('utf-8', 'replace'))
                if Path(name).name == '.env' or (Path(name).name.startswith('.env.') and not name.endswith('.example')): hits.append((name, 'env_file_in_archive', ''))
    else:
        for p in ([Path(x) for x in a.paths] or files_in_repo()):
            if not p.is_file() or (p.suffix.lower() not in TEXT_EXT and not p.name.startswith('.env')) or p.stat().st_size > 2_000_000: continue
            n += 1; hits += scan_text(str(p.relative_to(ROOT) if p.is_absolute() and ROOT in p.parents else p), p.read_text('utf-8', 'replace'))
            if p.name.startswith('.env') and p.name != '.env.example': hits.append((p.name, 'env_file_must_not_be_committed', ''))
    for h in hits: print(f'{h[0]}: {h[1]} {h[2]}')
    print(f'secret scan: {n} files, {len(hits)} findings')
    sys.exit(1 if hits else 0)


if __name__ == '__main__':
    main()
