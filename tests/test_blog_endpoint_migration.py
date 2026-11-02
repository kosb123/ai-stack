import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('migration', Path(__file__).parents[1] / 'integrations/blog-writer/migrate_endpoint.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class MigrationTests(unittest.TestCase):
    def test_preserves_other_edits_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            writer = root / 'blog-rank/writer'
            writer.mkdir(parents=True)
            source = '# existing quality edits\n' + migration.OLD_URL + '\nrequest = ' + migration.OLD_REQUEST + '\n'
            for name in ('simple_write.py', 'blog_pipeline.py'):
                (writer / name).write_text(source)
            backup = root / 'backups'
            self.assertEqual(migration.migrate(root, backup), 2)
            self.assertEqual(migration.migrate(root, backup), 0)
            self.assertIn('# existing quality edits', (writer / 'simple_write.py').read_text())
            self.assertEqual((backup / 'simple_write.py').read_text(), source)
            self.assertTrue((writer / 'local_llm.py').is_file())


if __name__ == '__main__':
    unittest.main()
