import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('home_check', Path(__file__).parents[1] / 'scripts/check_autotune_home.py')
home_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(home_check)


class AutotuneHomeTests(unittest.TestCase):
    def test_detects_lost_state_and_changed_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / 'old', Path(tmp) / 'new'
            source.mkdir()
            for name in home_check.CODE:
                text = '{}'
                if name == 'autotune.py':
                    text = "CONFIG = {'preferred_models': ['first']}\nQUALITY_TIERS = []\n"
                (source / name).parent.mkdir(parents=True, exist_ok=True)
                (source / name).write_text(text, encoding='utf-8')
            (source / 'state.json').write_text('{"last_tune": "before"}', encoding='utf-8')
            for folder in ('logs', 'backups', 'reports', 'survey'):
                (source / folder).mkdir(exist_ok=True)
                (source / folder / 'saved.txt').write_text('preserved', encoding='utf-8')
            shutil.copytree(source, target)
            self.assertTrue(all(home_check.compare(source, target).values()))
            (target / 'survey/survey.py').write_text('changed = True', encoding='utf-8')
            self.assertFalse(home_check.compare(source, target, False)['survey/survey.py'])
            (target / 'state.json').write_text('{}', encoding='utf-8')
            self.assertFalse(home_check.compare(source, target)['state.json_preserved'])
            (target / 'autotune.py').write_text("CONFIG = {'preferred_models': ['other']}\nQUALITY_TIERS = []\n", encoding='utf-8')
            self.assertFalse(home_check.compare(source, target)['policy_unchanged'])
