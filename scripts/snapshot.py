"""Export explicitly listed local files with credential values removed."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PureWindowsPath

PLACEHOLDER = '${LOCAL_SECRET}'
SECRET_KEY = re.compile(r'(?:^|_)(?:password|passwd|pgpassword|api_?key|token|secret|credential|private_?key|encryption_?key|signing_?key|server_?key|salt|cookie|authorization|webhook|database_?url)(?:$|_)', re.I)
ASSIGNMENT = re.compile(r'''(?im)\b([\w.-]+)\s*=\s*(["'])([^"'\r\n]+)\2\s*(?=$|[#;])''')
BEARER = re.compile(r'(?i)(\bbearer\s+)([A-Za-z0-9._~+/=-]{8,})')
URL_AUTH = re.compile(r'(?i)((?:https?|postgres(?:ql)?)://)([^\s/@:]+(?::[^\s/@]*)?@)')
URL_QUERY = re.compile(r'(?i)([?&](?:api[_-]?key|access[_-]?token|token|secret|password|signature|sig|key)=)([^&#\s"\']+)')

try:
    import yaml
except ImportError:
    try:
        import hermes_yaml as yaml
    except ImportError:
        yaml = None


def is_secret_key(key):
    normalized = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', str(key)).replace('-', '_').lower()
    if normalized in ('max_tokens', 'token_budget', 'token_limit', 'token_count') or normalized.endswith(('_max_tokens', '_token_budget', '_token_limit', '_token_count')):
        return False
    return bool(SECRET_KEY.search(normalized))


def scrub_text(text, corpus=()):
    for secret in sorted(corpus, key=len, reverse=True):
        text = text.replace(secret, PLACEHOLDER)
    text = BEARER.sub(lambda m: m.group(1) + PLACEHOLDER, text)
    text = URL_AUTH.sub(lambda m: m.group(1) + PLACEHOLDER + '@', text)
    text = URL_QUERY.sub(lambda m: m.group(1) + PLACEHOLDER, text)
    return text


def transform(value, corpus):
    if isinstance(value, dict):
        return {key: PLACEHOLDER if is_secret_key(key) and not isinstance(value[key], bool) and value[key] not in (None, '') else transform(value[key], corpus) for key in value}
    if isinstance(value, list):
        return [transform(item, corpus) for item in value]
    return scrub_text(value, corpus) if isinstance(value, str) else value


def parse_structured(text, kind):
    if kind == 'json':
        return json.loads(text)
    if yaml is None:
        raise ValueError('YAML parser unavailable; install PyYAML or hermes_yaml')
    return yaml.safe_load(text)


def collect_structured(value):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if is_secret_key(key) and isinstance(item, (str, int, float)) and len(str(item)) >= 8:
                found.add(str(item))
            else:
                found.update(collect_structured(item))
    elif isinstance(value, list):
        for item in value:
            found.update(collect_structured(item))
    return found


def scrub_env(text, corpus):
    lines = []
    for line in text.splitlines(keepends=True):
        match = re.match(r'^(\s*(?:export\s+)?([\w.-]+)\s*=)(.*?)(\r?\n)?$', line)
        if match and match.group(2).upper() == 'REQUIRE_API_KEY' and match.group(3).strip().strip('"\'').lower() in ('true', 'false', '0', '1'):
            lines.append(line)
        elif match and is_secret_key(match.group(2)):
            lines.append(match.group(1) + PLACEHOLDER + (match.group(4) or ''))
        else:
            lines.append(scrub_text(line, corpus))
    return ''.join(lines)


def collect_env(text):
    values = set()
    for line in text.splitlines():
        match = re.match(r'^\s*(?:export\s+)?([\w.-]+)\s*=\s*(.*?)\s*$', line)
        if match and is_secret_key(match.group(1)):
            value = match.group(2).strip('\'"')
            if len(value) >= 8:
                values.add(value)
    return values


def collect_secret_file(path):
    text = path.read_text(encoding='utf-8-sig')
    suffix = path.suffix.lower()
    if suffix in ('.json', '.yaml', '.yml'):
        return collect_structured(parse_structured(text, 'json' if suffix == '.json' else 'yaml'))
    return collect_env(text)


def safe_target(root, target):
    if not isinstance(target, str) or not target or '\\' in target or PureWindowsPath(target).is_absolute():
        raise ValueError('invalid target path')
    relative = Path(target)
    if relative.is_absolute() or any(part in ('.', '..') for part in target.split('/')) or ':' in target:
        raise ValueError('invalid target path')
    dest = root / relative
    if root.is_symlink() or any(p.is_symlink() for p in [dest, *dest.parents] if p != root.parent):
        raise ValueError('symlink target rejected')
    if not dest.resolve().is_relative_to(root.resolve()):
        raise ValueError('target outside output')
    return dest


def export(manifest, root, secret_files):
    entries = json.loads(manifest.read_text(encoding='utf-8-sig'))
    if isinstance(entries, dict):
        entries = entries['entries']
    if not isinstance(entries, list):
        raise ValueError('manifest must contain entries')
    corpus = set()
    for path in secret_files:
        corpus.update(collect_secret_file(path))
    root = root.absolute()
    planned = []
    seen = set()
    for entry in entries:
        source, target, kind = entry['source'], entry['target'], entry['kind']
        if not isinstance(source, str) or not (Path(source).is_absolute() or PureWindowsPath(source).is_absolute()):
            raise ValueError('source must be absolute')
        source = Path(source)
        if not source.is_file() or source.is_symlink():
            raise ValueError('source must be a regular file')
        if kind not in ('copy', 'json', 'yaml', 'env'):
            raise ValueError('unknown entry kind')
        dest = safe_target(root, target)
        key = str(dest).casefold()
        if key in seen or target.replace('\\', '/').casefold() == 'provenance/sources.json':
            raise ValueError('duplicate or reserved target')
        seen.add(key)
        raw = source.read_bytes()
        original = raw.decode('utf-8-sig')
        if kind in ('json', 'yaml'):
            data = transform(parse_structured(original, kind), corpus)
            result = json.dumps(data, ensure_ascii=False, indent=2) + '\n' if kind == 'json' else yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        elif kind == 'env':
            result = scrub_env(original, corpus)
        else:
            result = scrub_text(original, corpus)
        result = result.replace('\r\n', '\n')
        planned.append((source, target, dest, raw, result.encode('utf-8')))
    provenance = safe_target(root, 'provenance/sources.json')
    source_paths = {str(source.resolve()).casefold() for source, *_rest in planned}
    if any(str(destination.resolve()).casefold() in source_paths
           for destination in [provenance, *(item[2] for item in planned)]):
        raise ValueError('output overlaps a source file')
    records = []
    for source, target, dest, raw, result in planned:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(result)
        records.append({'source': str(source), 'target': target, 'source_sha256': hashlib.sha256(raw).hexdigest(), 'snapshot_sha256': hashlib.sha256(result).hexdigest(), 'redacted': raw != result})
    provenance.parent.mkdir(parents=True, exist_ok=True)
    provenance.write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return len(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--secret-file', type=Path, action='append', default=[])
    args = parser.parse_args()
    try:
        count = export(args.manifest, args.output, args.secret_file)
    except Exception as exc:
        print(f'Export failed ({type(exc).__name__}); inspect input paths and formats.', file=sys.stderr)
        return 1
    print(f'Exported {count} files to {args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
