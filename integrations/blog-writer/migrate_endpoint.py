"""Migrate the two local blog writers without replacing their other edits."""
import argparse
import difflib
from pathlib import Path
import shutil

OLD_URL = "LLM_URL = os.environ.get('BLOG_LLM_URL', 'http://127.0.0.1:18435/v1/chat/completions')"
NEW_URL = "LLM_URL = os.environ.get('BLOG_LLM_URL', local_llm.DEFAULT_URL)"
OLD_REQUEST = "urllib.request.Request(LLM_URL, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})"
NEW_REQUEST = 'local_llm.make_request(LLM_URL, body)'


def migrate(project, backup, patch_output=None):
    writer = project / 'blog-rank/writer'
    changes = []
    for name in ('simple_write.py', 'blog_pipeline.py'):
        path = writer / name
        original = path.read_text(encoding='utf-8')
        updated = original
        if OLD_URL in updated:
            if updated.count(OLD_URL) != 1 or updated.count(OLD_REQUEST) != 1:
                raise ValueError(f'{name}: unexpected request structure; no overwrite')
            updated = updated.replace(OLD_URL, 'import local_llm\n\n' + NEW_URL).replace(OLD_REQUEST, NEW_REQUEST)
        elif NEW_URL not in updated or NEW_REQUEST not in updated:
            raise ValueError(f'{name}: source needs review; no overwrite')
        compile(updated, str(path), 'exec')
        changes.append((path, original, updated))
    backup.mkdir(parents=True, exist_ok=True)
    helper = writer / 'local_llm.py'
    if helper.exists() and not (backup / helper.name).exists():
        shutil.copy2(helper, backup / helper.name)
    shutil.copy2(Path(__file__).with_name('local_llm.py'), helper)
    diff = []
    for path, original, updated in changes:
        if original != updated:
            if not (backup / path.name).exists():
                shutil.copy2(path, backup / path.name)
            path.write_text(updated, encoding='utf-8')
        relative = path.relative_to(project).as_posix()
        diff.extend(difflib.unified_diff(original.splitlines(True), updated.splitlines(True),
                                        fromfile='a/' + relative, tofile='b/' + relative))
    if patch_output and diff:
        patch_output.write_text(''.join(diff), encoding='utf-8')
    return sum(original != updated for _, original, updated in changes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--backup-dir', type=Path, required=True)
    parser.add_argument('--patch-output', type=Path)
    args = parser.parse_args()
    print(f'Updated writer files: {migrate(args.project, args.backup_dir, args.patch_output)}')


if __name__ == '__main__':
    main()
