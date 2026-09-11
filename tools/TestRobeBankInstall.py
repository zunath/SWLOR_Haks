"""Interrupt bank publication at each boundary and recover without corrupting ownership."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import RepackRobeAnimationBanks as repack


class AbruptTermination(BaseException):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


class RobeBankInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        root_patch = patch.object(repack.robes, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.manifest = self.root / "tools/RobeRgbModels.json"
        self.manifest.parent.mkdir()
        manifest_patch = patch.object(repack.robes, "MANIFEST", self.manifest)
        manifest_patch.start()
        self.addCleanup(manifest_patch.stop)
        (self.root / "sw_anim_m").mkdir()
        (self.root / "sw_pt_root").mkdir()
        self.old = {
            "sw_anim_m/pmh_ra001.mdl": b"\0\0\0\0old complete animation family one",
            "sw_anim_m/pmh_ra002.mdl": b"\0\0\0\0old complete animation family two",
            "sw_pt_root/pmh34.mdl": b"\0\0\0\0untouched wearable root",
        }
        for relative, data in self.old.items():
            (self.root / relative).write_bytes(data)
        previous = {"files": {relative: digest(data) for relative, data in self.old.items()}}
        self.initial = (json.dumps(previous, indent=2) + "\n").encode()
        self.manifest.write_bytes(self.initial)
        stage = self.root / "output/staged-banks"
        stage.mkdir(parents=True)
        self.new = {"pmh_ra001": b"\0\0\0\0new family one head",
                    "pmh_ra001_b01": b"\0\0\0\0new family one child",
                    "pmh_ra002": b"\0\0\0\0new family two head"}
        self.sources = {}
        self.updated = {"files": dict(previous["files"]), "animation_bank_sets": {"installed": True}}
        for name, data in self.new.items():
            self.sources[name] = stage / f"{name}.mdl"
            self.sources[name].write_bytes(data)
            self.updated["files"][f"sw_anim_m/{name}.mdl"] = digest(data)
        self.transaction = self.root / "output/nwsync-bank-install"

    def install(self):
        repack.install_banks(self.sources, self.updated, self.initial)

    def test_sources_in_transaction_directory_are_rejected_before_recovery(self):
        source = self.transaction / "nested/pmh_ra001.mdl"
        source.parent.mkdir(parents=True)
        source.write_bytes(self.new["pmh_ra001"])
        self.sources["pmh_ra001"] = source
        with self.assertRaisesRegex(ValueError, "reserved.*install"):
            self.install()
        self.assertEqual(self.new["pmh_ra001"], source.read_bytes())
        self.assert_old()

    def assert_old(self):
        self.assertEqual(self.initial, self.manifest.read_bytes())
        for relative, data in self.old.items():
            self.assertEqual(data, (self.root / relative).read_bytes(), relative)
        self.assertFalse((self.root / "sw_anim_m/pmh_ra001_b01.mdl").exists())

    def assert_new(self):
        self.assertEqual(self.updated, json.loads(self.manifest.read_bytes()))
        for name, data in self.new.items():
            self.assertEqual(data, (self.root / "sw_anim_m" / f"{name}.mdl").read_bytes())
        self.assertEqual(self.old["sw_pt_root/pmh34.mdl"], (self.root / "sw_pt_root/pmh34.mdl").read_bytes())

    def interrupt_after(self, target_index):
        replace = repack.os.replace

        def crash(source, destination):
            result = replace(source, destination)
            if Path(source).name == f"new-{target_index}":
                raise AbruptTermination()
            return result

        with patch.object(repack.os, "replace", side_effect=crash):
            with self.assertRaises(AbruptTermination):
                self.install()

    def test_success_replaces_complete_resources_and_commits_the_manifest_last(self):
        replace = repack.os.replace
        installed = []

        def observe(source, destination):
            if Path(source).name.startswith("new-"):
                self.assertEqual(self.initial, self.manifest.read_bytes())
                journal = json.loads((self.transaction / "journal.json").read_text())
                self.assertEqual("installing", journal["state"])
                installed.append(Path(destination).relative_to(self.root).as_posix())
            return replace(source, destination)

        with patch.object(repack.os, "replace", side_effect=observe):
            self.install()
        self.assertEqual(["sw_anim_m/pmh_ra001.mdl", "sw_anim_m/pmh_ra001_b01.mdl",
                          "sw_anim_m/pmh_ra002.mdl", "tools/RobeRgbModels.json"], installed)
        self.assert_new()
        self.assertFalse(self.transaction.exists())

    def test_copy_failure_during_preparation_leaves_every_live_file_untouched(self):
        write = repack._write_durable

        def fail_backup(path, data):
            if path.name == "old-2":
                raise OSError("disk full while preparing backup")
            return write(path, data)

        with patch.object(repack, "_write_durable", side_effect=fail_backup):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.install()
        self.assert_old()
        self.assertFalse(self.transaction.exists())

    def test_every_install_replacement_failure_rolls_back_and_can_be_retried(self):
        replace = repack.os.replace
        for index in range(4):
            with self.subTest(index=index):
                def fail(source, destination):
                    if Path(source).name == f"new-{index}":
                        raise OSError("injected replacement failure")
                    return replace(source, destination)

                with patch.object(repack.os, "replace", side_effect=fail):
                    with self.assertRaisesRegex(OSError, "injected replacement failure"):
                        self.install()
                self.assert_old()
                self.assertFalse(self.transaction.exists())
        self.install()
        self.assert_new()

    def test_commit_marker_failure_restores_banks_and_the_old_manifest(self):
        write = repack._write_install_journal

        def fail_commit(directory, journal):
            if journal["state"] == "committed":
                raise OSError("commit marker failed")
            return write(directory, journal)

        with patch.object(repack, "_write_install_journal", side_effect=fail_commit):
            with self.assertRaisesRegex(OSError, "commit marker failed"):
                self.install()
        self.assert_old()
        self.assertFalse(self.transaction.exists())

    def test_abrupt_termination_after_each_move_recovers_on_the_next_invocation(self):
        for index in range(4):
            with self.subTest(index=index):
                self.interrupt_after(index)
                self.assertTrue((self.transaction / "journal.json").is_file())
                repack.recover_bank_install()
                self.assert_old()
                self.assertFalse(self.transaction.exists())

    def test_main_recovers_before_manifest_provenance_is_checked(self):
        self.interrupt_after(1)

        def stop_after_recovery(manifest):
            self.assert_old()
            self.assertEqual(json.loads(self.initial), manifest)
            raise RuntimeError("preflight reached after recovery")

        with patch("sys.argv", ["repack"]), patch.object(repack, "preflight", side_effect=stop_after_recovery):
            with self.assertRaisesRegex(RuntimeError, "preflight reached after recovery"):
                repack.main()
        self.assertFalse(self.transaction.exists())

    def test_abrupt_termination_during_rollback_recognizes_consumed_backups(self):
        self.interrupt_after(3)
        replace = repack.os.replace

        def interrupt_restore(source, destination):
            result = replace(source, destination)
            if Path(source).name == "old-0":
                raise AbruptTermination()
            return result

        with patch.object(repack.os, "replace", side_effect=interrupt_restore):
            with self.assertRaises(AbruptTermination):
                repack.recover_bank_install()
        self.assertFalse((self.transaction / "old-0").exists())
        self.assertEqual(self.old["sw_anim_m/pmh_ra001.mdl"],
                         (self.root / "sw_anim_m/pmh_ra001.mdl").read_bytes())
        repack.recover_bank_install()
        self.assert_old()
        self.assertFalse(self.transaction.exists())

    def test_terminated_preparation_without_a_journal_is_discarded_safely(self):
        write = repack._write_durable

        def terminate(path, data):
            write(path, data)
            if path.name == "new-1":
                raise AbruptTermination()

        with patch.object(repack, "_write_durable", side_effect=terminate):
            with self.assertRaises(AbruptTermination):
                self.install()
        self.assertFalse((self.transaction / "journal.json").exists())
        repack.recover_bank_install()
        self.assert_old()
        self.assertFalse(self.transaction.exists())

    def test_committed_transaction_cleanup_never_reverts_successful_installation(self):
        write = repack._write_install_journal

        def terminate_after_commit(directory, journal):
            write(directory, journal)
            if journal["state"] == "committed":
                raise AbruptTermination()

        with patch.object(repack, "_write_install_journal", side_effect=terminate_after_commit):
            with self.assertRaises(AbruptTermination):
                self.install()
        self.assert_new()
        repack.recover_bank_install()
        self.assert_new()
        self.assertFalse(self.transaction.exists())

    def test_external_edits_or_damaged_backups_stop_recovery_before_any_restoration(self):
        self.interrupt_after(1)
        child = self.root / "sw_anim_m/pmh_ra001_b01.mdl"
        child.write_bytes(b"external edit after interruption")
        with self.assertRaisesRegex(ValueError, "changed externally"):
            repack.recover_bank_install()
        self.assertEqual(self.new["pmh_ra001"], (self.root / "sw_anim_m/pmh_ra001.mdl").read_bytes())
        self.assertEqual(b"external edit after interruption", child.read_bytes())
        child.write_bytes(self.new["pmh_ra001_b01"])
        (self.transaction / "old-0").write_bytes(b"damaged backup")
        with self.assertRaisesRegex(ValueError, "backup changed"):
            repack.recover_bank_install()
        self.assertTrue(self.transaction.exists())
        (self.transaction / "old-0").write_bytes(self.old["sw_anim_m/pmh_ra001.mdl"])
        repack.recover_bank_install()
        self.assert_old()

    def test_another_installer_cannot_recover_an_active_transaction(self):
        with repack._install_lock(self.root):
            with self.assertRaisesRegex(RuntimeError, "Another animation bank install"):
                repack.recover_bank_install()

    def test_interrupted_rollback_cleanup_does_not_require_already_deleted_backups(self):
        self.interrupt_after(1)

        def interrupt_cleanup(directory):
            (directory / "old-2").unlink()
            raise AbruptTermination()

        with patch.object(repack.shutil, "rmtree", side_effect=interrupt_cleanup):
            with self.assertRaises(AbruptTermination):
                repack.recover_bank_install()
        self.assert_old()
        self.assertEqual("installing", json.loads((self.transaction / "journal.json").read_text())["state"])
        repack.recover_bank_install()
        self.assert_old()
        self.assertFalse(self.transaction.exists())

    def test_disk_full_after_live_changes_restores_models_and_manifest_without_copy_writes(self):
        write, replace = repack._write_durable, repack.os.replace
        disk_full = False
        failed_writes = []

        def fill_disk_after_manifest(source, destination):
            nonlocal disk_full
            result = replace(source, destination)
            if Path(source).name == "new-3":
                disk_full = True
            return result

        def fail_new_writes(path, data):
            if disk_full:
                failed_writes.append(path.name)
                raise OSError(28, "disk full")
            return write(path, data)

        with patch.object(repack.os, "replace", side_effect=fill_disk_after_manifest), \
                patch.object(repack, "_write_durable", side_effect=fail_new_writes):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.install()
        # Committing the journal failed after every live file was replaced. The
        # complete rollback needs only renames/unlinks, with no backup copies.
        self.assertEqual(["journal.next"], failed_writes)
        self.assert_old()
        self.assertFalse(self.transaction.exists())

    def test_missing_backup_is_rejected_if_the_original_is_not_already_restored(self):
        self.interrupt_after(1)
        (self.transaction / "old-0").unlink()
        with self.assertRaisesRegex(ValueError, "backup changed"):
            repack.recover_bank_install()
        self.assertEqual(self.new["pmh_ra001"], (self.root / "sw_anim_m/pmh_ra001.mdl").read_bytes())
        self.assertTrue((self.root / "sw_anim_m/pmh_ra001_b01.mdl").exists())


if __name__ == "__main__":
    unittest.main()
