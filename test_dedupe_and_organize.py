import contextlib
import errno
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dedupe_and_organize as dedupe


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.roots = [self.base / name for name in ("first", "second", "third")]
        for root in self.roots:
            root.mkdir()

    def file(self, root_index, name="copy.txt", mtime=100):
        path = self.roots[root_index] / name
        path.write_bytes(b"identical content")
        os.utime(path, (mtime, mtime))
        return path

    def run_cli(self, *options):
        argv = ["dedupe_and_organize.py"] + [str(root) for root in self.roots]
        argv += ["--no-cache", "--no-color", "--workers", "1"] + list(options)
        with patch("sys.argv", argv), patch.object(dedupe, "tqdm", None):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                status = dedupe.main()
        return status, output.getvalue()

    def require_symlinks(self):
        source = self.base / "probe-source"
        link = self.base / "probe-link"
        source.touch()
        try:
            os.symlink(str(source), str(link))
        except OSError as error:
            self.skipTest(f"Symlinks unavailable: {error}")
        finally:
            if link.is_symlink():
                link.unlink()
            source.unlink()

    def test_hard_link_defaults_to_first_folder_and_reports_it(self):
        target = self.file(0, mtime=100)
        duplicate = self.file(1, mtime=300)
        report_path = self.base / "report.json"
        status, _ = self.run_cli("--duplicates-action", "link", "--apply",
                                 "--report-json", str(report_path))
        self.assertEqual(status, 0)
        self.assertTrue(target.samefile(duplicate))
        self.assertEqual(target.stat().st_mtime, 100)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["groups"][0]["kept"], str(target))
        self.assertEqual(report["groups"][0]["duplicates"], [str(duplicate)])

    def test_falls_back_to_first_folder_with_a_match_and_newest_within_it(self):
        older = self.file(1, "older.txt", 100)
        target = self.file(1, "newer.txt", 200)
        duplicate = self.file(2, mtime=300)
        status, _ = self.run_cli("--duplicate-action", "link", "--apply")
        self.assertEqual(status, 0)
        self.assertTrue(target.samefile(older))
        self.assertTrue(target.samefile(duplicate))
        self.assertEqual(target.stat().st_mtime, 200)
        self.assertEqual(list(self.roots[0].iterdir()), [])

    def test_dry_run_does_not_replace_files(self):
        target = self.file(0)
        duplicate = self.file(1)
        status, output = self.run_cli("--duplicates-action", "link")
        self.assertEqual(status, 0)
        self.assertIn("[DRY] LINK (hard)", output)
        self.assertFalse(target.samefile(duplicate))
        self.assertEqual(duplicate.read_bytes(), b"identical content")
        self.assertEqual(list(duplicate.parent.iterdir()), [duplicate])

    def test_symlink_points_to_first_folder_and_rerun_is_safe(self):
        self.require_symlinks()
        target = self.file(0)
        duplicate = self.file(1, mtime=300)
        for _ in range(2):
            status, _ = self.run_cli("--duplicates-action", "link",
                                     "--link-type", "symlink", "--apply")
            self.assertEqual(status, 0)
            self.assertTrue(duplicate.is_symlink())
            self.assertEqual(duplicate.resolve(), target)
            self.assertEqual(duplicate.read_bytes(), b"identical content")

    def test_failed_creation_or_replacement_preserves_duplicate(self):
        target = self.file(0)
        duplicate = self.file(1)
        for operation, error in (
            ("link", OSError(errno.EXDEV, "Cross-device link")),
            ("symlink", PermissionError("Symlink permission denied")),
            ("replace", PermissionError("Replacement denied")),
        ):
            with self.subTest(operation=operation):
                link_type = "symlink" if operation == "symlink" else "hard"
                with patch.object(dedupe.os, operation, side_effect=error):
                    with contextlib.redirect_stderr(io.StringIO()) as errors:
                        status, _ = self.run_cli("--duplicates-action", "link",
                                                 "--link-type", link_type, "--apply")
                self.assertEqual(status, 1)
                self.assertIn("Failed to link", errors.getvalue())
                self.assertFalse(duplicate.is_symlink())
                self.assertFalse(target.samefile(duplicate))
                self.assertEqual(duplicate.read_bytes(), b"identical content")
                self.assertEqual(list(duplicate.parent.iterdir()), [duplicate])

    def test_existing_hard_links_and_overlapping_roots_are_safe(self):
        target = self.file(0)
        duplicate = self.roots[1] / "copy.txt"
        os.link(str(target), str(duplicate))
        with patch.object(dedupe.os, "link", side_effect=AssertionError("Already linked")):
            status, _ = self.run_cli("--duplicates-action", "link", "--apply")
        self.assertEqual(status, 0)
        self.assertTrue(target.samefile(duplicate))
        self.roots.append(self.base)
        status, _ = self.run_cli("--duplicates-action", "link", "--apply")
        self.assertEqual(status, 0)
        self.assertTrue(target.samefile(duplicate))

    def test_existing_hard_link_to_another_file_is_unchanged(self):
        target = self.file(0)
        external = self.base / "external.txt"
        external.write_bytes(target.read_bytes())
        duplicate = self.roots[1] / "copy.txt"
        os.link(str(external), str(duplicate))
        original = duplicate.stat()
        for link_type in ("hard", "symlink"):
            for options in ([], ["--keep-existing-links"], ["--apply"],
                            ["--apply", "--keep-existing-links"]):
                with self.subTest(link_type=link_type, options=options):
                    status, output = self.run_cli("--duplication-action=link",
                                                  "--link-type", link_type, *options)
                    self.assertEqual(status, 0)
                    self.assertNotIn("[DRY] LINK", output)
                    self.assertTrue(duplicate.samefile(external))
                    self.assertFalse(duplicate.samefile(target))
                    self.assertEqual(duplicate.stat().st_ino, original.st_ino)
                    self.assertEqual(duplicate.stat().st_nlink, original.st_nlink)
                    self.assertEqual(duplicate.stat().st_mtime_ns, original.st_mtime_ns)

    def test_existing_symlink_to_another_file_is_unchanged(self):
        self.require_symlinks()
        target = self.file(0)
        external = self.base / "external.txt"
        external.write_bytes(target.read_bytes())
        duplicate = self.roots[1] / "copy.txt"
        os.symlink(str(external), str(duplicate))
        original = duplicate.lstat()
        original_target = os.readlink(duplicate)
        for link_type in ("hard", "symlink"):
            for options in ([], ["--keep-existing-links"], ["--apply"],
                            ["--apply", "--keep-existing-links"]):
                with self.subTest(link_type=link_type, options=options):
                    status, output = self.run_cli("--duplicates-action", "link",
                                                  "--link-type", link_type, *options)
                    self.assertEqual(status, 0)
                    self.assertNotIn("[DRY] LINK", output)
                    self.assertTrue(duplicate.is_symlink())
                    self.assertEqual(os.readlink(duplicate), original_target)
                    self.assertEqual(duplicate.resolve(), external)
                    self.assertEqual(duplicate.lstat().st_ino, original.st_ino)
                    self.assertEqual(duplicate.lstat().st_mtime_ns, original.st_mtime_ns)

    def test_disabling_keep_existing_links_replaces_hard_link(self):
        target = self.file(0)
        external = self.base / "external.txt"
        external.write_bytes(target.read_bytes())
        duplicate = self.roots[1] / "copy.txt"
        os.link(str(external), str(duplicate))
        status, _ = self.run_cli("--duplicates-action", "link", "--apply",
                                 "--no-keep-existing-links")
        self.assertEqual(status, 0)
        self.assertTrue(duplicate.samefile(target))
        self.assertFalse(duplicate.samefile(external))
        self.assertEqual(external.stat().st_nlink, 1)
        self.assertEqual(external.read_bytes(), target.read_bytes())

    def test_disabling_keep_existing_links_retargets_symlink(self):
        self.require_symlinks()
        target = self.file(0)
        external = self.base / "external.txt"
        external.write_bytes(target.read_bytes())
        duplicate = self.roots[1] / "copy.txt"
        os.symlink(str(external), str(duplicate))
        status, _ = self.run_cli("--duplicates-action", "link", "--link-type", "symlink",
                                 "--apply", "--no-keep-existing-links")
        self.assertEqual(status, 0)
        self.assertTrue(duplicate.is_symlink())
        self.assertEqual(duplicate.resolve(), target)
        self.assertEqual(external.read_bytes(), target.read_bytes())

    def test_organize_links_to_actual_collision_resolved_destination(self):
        self.require_symlinks()
        target = self.file(0)
        duplicate = self.file(1)
        organized = self.base / "organized"
        existing = organized / "_by_ext" / "txt" / target.name
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"unrelated content")
        status, _ = self.run_cli("--duplicates-action", "link", "--link-type", "symlink",
                                 "--apply", "--organize", "kept", "--org-mode", "ext",
                                 "--organize-root", str(organized))
        self.assertEqual(status, 0)
        self.assertFalse(target.exists())
        self.assertTrue(duplicate.is_symlink())
        self.assertEqual(duplicate.resolve(), existing.with_name("copy__dup1.txt"))
        self.assertEqual(duplicate.read_bytes(), b"identical content")
        self.assertEqual(existing.read_bytes(), b"unrelated content")

    def test_original_actions_still_choose_newest(self):
        older = self.file(0)
        newest = self.file(1, mtime=300)
        files = dedupe.iter_files(self.roots)
        kept, duplicates = dedupe.choose_kept(files)
        self.assertEqual(kept.path, newest)
        self.assertEqual([item.path for item in duplicates], [older])

    def test_invalid_link_type_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                self.run_cli("--duplicates-action", "link", "--link-type", "invalid")
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
