from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "scripts" / "blender_env.sh"
BASH = shutil.which("bash") or "/bin/bash"


class BlenderEnvironmentTests(unittest.TestCase):
    def _resolve(self, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                BASH,
                "-c",
                'source "$1"; resolve_blender_bin',
                "test-blender-env",
                str(HELPER),
            ],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def _fake_blender(self, directory: Path, name: str = "blender") -> Path:
        executable = directory / name
        executable.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        return executable

    def test_blender_bin_has_highest_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            preferred = self._fake_blender(tmp_path, "preferred blender")
            legacy = self._fake_blender(tmp_path, "legacy blender")
            completed = self._resolve(
                {
                    **os.environ,
                    "BLENDER_BIN": str(preferred),
                    "BLENDER": str(legacy),
                }
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), str(preferred))

    def test_legacy_blender_variable_remains_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = self._fake_blender(Path(tmp))
            completed = self._resolve(
                {
                    **os.environ,
                    "BLENDER": str(executable),
                    "BLENDER_BIN": "",
                }
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), str(executable))

    def test_path_lookup_is_the_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = self._fake_blender(Path(tmp))
            env = {
                **os.environ,
                "PATH": tmp,
                "BLENDER": "",
                "BLENDER_BIN": "",
            }
            completed = self._resolve(env)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), str(executable))

    def test_invalid_explicit_path_fails_clearly(self):
        env = {
            **os.environ,
            "BLENDER_BIN": "/missing/blender",
            "BLENDER": "",
        }
        completed = self._resolve(env)
        self.assertEqual(completed.returncode, 127)
        self.assertIn("Set BLENDER_BIN", completed.stderr)


if __name__ == "__main__":
    unittest.main()
