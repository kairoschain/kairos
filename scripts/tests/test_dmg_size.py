import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "dmg_size", Path(__file__).resolve().parents[1] / "release" / "dmg_size.py"
)
dmg_size = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dmg_size)


@unittest.skipUnless(os.name == "posix", "macOS packaging uses a POSIX filesystem")
class DmgSizeTests(unittest.TestCase):
    def test_sparse_binary_uses_file_length(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "Kairos.app" / "Contents" / "MacOS" / "kairos-node"
            binary.parent.mkdir(parents=True)
            with binary.open("wb") as stream:
                stream.truncate(512 * dmg_size.MIB)
            self.assertLess(binary.stat().st_blocks * 512, binary.stat().st_size)
            self.assertGreater(dmg_size.image_size_mib(root), 512)

    def test_hard_links_reserve_space_for_both_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "node"
            with binary.open("wb") as stream:
                stream.truncate(256 * dmg_size.MIB)
            os.link(binary, root / "wallet-node")
            self.assertGreater(dmg_size.image_size_mib(root), 512)

    def test_applications_link_does_not_include_external_files(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "image with spaces"
            root.mkdir()
            applications = base / "Applications"
            applications.mkdir()
            with (applications / "unrelated").open("wb") as stream:
                stream.truncate(8 * 1024 * dmg_size.MIB)
            (root / "Applications").symlink_to(applications, target_is_directory=True)
            self.assertEqual(dmg_size.image_size_mib(root), 256)


if __name__ == "__main__":
    unittest.main()
