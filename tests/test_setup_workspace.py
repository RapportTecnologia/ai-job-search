"""Real Git checks for private setup outputs and immutable template sources."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('setup_workspace', REPO / 'tools/setup_workspace.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SetupWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git('init', '-q')
        (self.root / '.gitignore').write_bytes((REPO / '.gitignore').read_bytes())
        self.outputs = ['CLAUDE.md', 'cv/main_example.tex',
                        '.claude/skills/job-application-assistant/01-candidate-profile.md',
                        '.claude/skills/job-scraper/search-queries.md', 'documents/README.md']
        for name in self.outputs:
            p = self.root / 'templates/setup' / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('Pristine [PLACEHOLDER]\n')
        self.git('add', '.gitignore', 'templates')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.PIPE).decode()

    def test_personalization_and_arbitrary_documents_never_enter_index(self):
        setup.check_private(self.root)
        result = setup.initialize(self.root)
        self.assertEqual(set(result['created']), set(self.outputs))
        for name in self.outputs:
            (self.root / name).write_text('PRIVATE TEST DATA\n')
        cert = self.root / 'documents/Certificados/custom.txt'
        cert.parent.mkdir(parents=True)
        cert.write_text('PRIVATE CERTIFICATE')
        self.git('add', '-A')
        setup.check_private(self.root)
        staged = self.git('diff', '--cached')
        self.assertNotIn('PRIVATE', staged)
        for name in self.outputs:
            self.assertEqual((self.root / 'templates/setup' / name).read_text(), 'Pristine [PLACEHOLDER]\n')

    def test_rerun_preserves_user_data(self):
        setup.initialize(self.root)
        p = self.root / 'CLAUDE.md'
        p.write_text('USER CONTENT')
        self.assertEqual(setup.initialize(self.root)['created'], [])
        self.assertEqual(p.read_text(), 'USER CONTENT')

    def test_force_added_private_file_is_detected(self):
        setup.initialize(self.root)
        self.git('add', '-f', 'CLAUDE.md')
        with self.assertRaisesRegex(ValueError, 'Private paths'):
            setup.check_private(self.root)

    def test_missing_ignore_rule_fails_before_copying(self):
        (self.root / '.gitignore').write_text('')
        with self.assertRaisesRegex(ValueError, 'gitignored'):
            setup.initialize(self.root)
        self.assertFalse((self.root / 'CLAUDE.md').exists())

    def test_symlink_destination_is_rejected(self):
        target = self.root / 'target.md'
        target.write_text('KEEP')
        try:
            (self.root / 'CLAUDE.md').symlink_to(target)
        except OSError:
            self.skipTest('Symlinks unavailable')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            setup.initialize(self.root)
        self.assertEqual(target.read_text(), 'KEEP')


if __name__ == '__main__':
    unittest.main()
