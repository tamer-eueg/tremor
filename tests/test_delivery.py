import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import apply_generated_patches  # noqa: E402


class PatchDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Tremor Test")
        self.git("config", "user.email", "test@example.invalid")
        self.source = self.repo / "integration.py"
        self.source.write_text("value = 'old'\n")
        self.git("add", "integration.py")
        self.git("commit", "-qm", "initial")
        self.patch_dir = self.repo / ".tremor" / "runs" / "patches"
        self.patch_dir.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args, check=True):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=check,
            capture_output=True, text=True,
        )

    def make_patch(self, name, new_source):
        self.source.write_text(new_source)
        patch = self.git("diff", "--", "integration.py").stdout
        self.source.write_text("value = 'old'\n")
        path = self.patch_dir / name
        path.write_text(patch)
        return path

    def test_discovers_and_applies_only_generated_patch_files(self):
        self.make_patch("provider.patch", "value = 'new'\n")
        (self.patch_dir / "notes.txt").write_text("not a patch")

        found = apply_generated_patches.discover_new_patches(
            str(self.repo), str(self.patch_dir)
        )
        count = apply_generated_patches.apply_patches(str(self.repo), found)

        self.assertEqual(count, 1)
        self.assertEqual(self.source.read_text(), "value = 'new'\n")

    def test_rejects_patch_directory_escape(self):
        with self.assertRaisesRegex(ValueError, "inside the repository"):
            apply_generated_patches.discover_new_patches(
                str(self.repo), str(self.repo.parent / "elsewhere")
            )

    def test_rolls_back_if_a_later_patch_fails(self):
        first = self.make_patch("a.patch", "value = 'new'\n")
        second = self.patch_dir / "b.patch"
        second.write_text("not a valid patch\n")

        with self.assertRaisesRegex(RuntimeError, "does not apply cleanly"):
            apply_generated_patches.apply_patches(
                str(self.repo), [os.path.relpath(first, self.repo), os.path.relpath(second, self.repo)]
            )
        self.assertEqual(self.source.read_text(), "value = 'old'\n")


if __name__ == "__main__":
    unittest.main()
