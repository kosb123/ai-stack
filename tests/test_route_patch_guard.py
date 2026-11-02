import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('guard', Path(__file__).parents[1] / 'hermes/llamacpp/ensure_route_patch.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class PatchGuardTests(unittest.TestCase):
    def test_apply_idempotence_and_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            source = root / 'route.py'
            source.write_text('provider = "custom"\n')
            patch = root / 'route.patch'
            patch.write_text('diff --git a/route.py b/route.py\n--- a/route.py\n+++ b/route.py\n@@ -1 +1 @@\n-provider = "custom"\n+provider = "llamacpp"\n')
            self.assertEqual(guard.ensure_patch(root, patch), 'applied_restart_required')
            self.assertEqual(source.read_text(), 'provider = "llamacpp"\n')
            self.assertEqual(guard.ensure_patch(root, patch), 'already_applied')
            source.write_text('provider = "upstream-new-implementation"\n')
            with self.assertRaises(RuntimeError):
                guard.ensure_patch(root, patch)
            self.assertEqual(source.read_text(), 'provider = "upstream-new-implementation"\n')


if __name__ == '__main__':
    unittest.main()
