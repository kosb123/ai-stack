"""Check a snapshot for prohibited files and obvious credential values."""
import argparse
import ast
import json
import re
import sys
from pathlib import Path

from snapshot import ASSIGNMENT, BEARER, PLACEHOLDER, URL_AUTH, URL_QUERY, is_secret_key, parse_structured

PROHIBITED = re.compile(r'(?i)(?:^\.env$|\.(?:db|sqlite|sqlite3|log)$|^(?:auth|credentials?|cookies?)\.(?:json|ya?ml|txt)$)')


def invalid_values(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if is_secret_key(key) and not isinstance(item, bool) and item not in (None, '', PLACEHOLDER):
                return True
            if invalid_values(item):
                return True
    elif isinstance(value, list):
        return any(invalid_values(item) for item in value)
    return False



def literal_assignment_found(text, is_python):
    if is_python:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return False
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                if (isinstance(value, ast.Constant) and isinstance(value.value, str)
                        and value.value and PLACEHOLDER not in value.value
                        and any(isinstance(target, ast.Name) and is_secret_key(target.id) for target in targets)):
                    return True
        return False
    return any(is_secret_key(m.group(1)) and PLACEHOLDER not in m.group(3)
               for m in ASSIGNMENT.finditer(text))


def invalid_env(text):
    for line in text.splitlines():
        match = re.match(r'^\s*(?:export\s+)?([\w.-]+)\s*=\s*(.*?)\s*$', line)
        if match and match.group(1).upper() == 'REQUIRE_API_KEY' and match.group(2).strip('\"\'').lower() in ('true', 'false', '0', '1'):
            continue
        if match and is_secret_key(match.group(1)) and match.group(2).strip('\"\'') not in ('', PLACEHOLDER):
            return True
    return False

def verify(root):
    problems = []
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if '.git' in relative.parts or '__pycache__' in relative.parts:
            continue
        if path.is_symlink():
            problems.append((relative, 'symlink'))
            continue
        if not path.is_file():
            continue
        if PROHIBITED.search(path.name):
            problems.append((relative, 'prohibited filename'))
            continue
        try:
            text = path.read_text(encoding='utf-8-sig')
        except (UnicodeError, OSError):
            problems.append((relative, 'unreadable or non-UTF-8 file'))
            continue
        if path.suffix.lower() in ('.json', '.yaml', '.yml'):
            try:
                kind = 'json' if path.suffix.lower() == '.json' else 'yaml'
                if invalid_values(parse_structured(text, kind)):
                    problems.append((relative, 'unredacted secret field'))
            except Exception:
                problems.append((relative, 'invalid or unsupported structured file'))
        if path.name.lower().startswith('.env.') or path.suffix.lower() == '.env':
            if invalid_env(text):
                problems.append((relative, 'unredacted secret field'))
        elif path.suffix.lower() not in ('.json', '.yaml', '.yml') and literal_assignment_found(text, path.suffix.lower() == '.py'):
            problems.append((relative, 'potential credential literal'))
        for pattern in (BEARER, URL_AUTH, URL_QUERY):
            if any(PLACEHOLDER not in m.group(2) for m in pattern.finditer(text)):
                problems.append((relative, 'potential credential literal'))
                break
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, nargs='?', default=Path.cwd())
    args = parser.parse_args()
    if not args.root.is_dir() or args.root.is_symlink():
        print('Invalid repository root', file=sys.stderr)
        return 1
    problems = verify(args.root)
    for path, reason in problems:
        print(f'{path}: {reason}')
    print(f'Found {len(problems)} potential issues')
    return int(bool(problems))


if __name__ == '__main__':
    sys.exit(main())
