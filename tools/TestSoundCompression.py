"""Run with Python; pass --ffmpeg <executable> to enable real encoding tests."""

import argparse
import contextlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import CompressSoundResources as sound


FFMPEG = shutil.which(os.environ.get("SOUND_FFMPEG", "ffmpeg"))


def chunk(name, value):
    return name + struct.pack("<I", len(value)) + value + (b"\0" if len(value) & 1 else b"")


def wav(rate=22050, channels=1, samples=22050, metadata=(), tag=1):
    pcm = b"".join(struct.pack("<h", int(8000 * math.sin(index * math.tau * 400 / rate))) * channels
                   for index in range(samples))
    body = b"WAVE" + chunk(b"fmt ", struct.pack("<HHIIHH", tag, channels, rate, rate * channels * 2,
                                               channels * 2, 16))
    body += b"".join(chunk(name, value) for name, value in metadata)
    body += chunk(b"data", pcm)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def mp3_frame():
    header = b"\xff\xfb\x70\xc0"  # MPEG-1 Layer III, 96 kbps, 44100 Hz, mono.
    return header + bytes(sound.mp3_header(header)["frame_bytes"] - 4)


class SoundClassificationTests(unittest.TestCase):
    def test_apply_cli_requires_engine_loop_exclusion_manifest(self):
        error_output = io.StringIO()
        with patch.object(sys, "argv", ["CompressSoundResources.py", "--apply", "--manifest", "report.json"]):
            with contextlib.redirect_stderr(error_output), self.assertRaises(SystemExit) as error:
                sound.main()
        self.assertEqual(error.exception.code, 2)
        self.assertIn("--apply requires --exclude-manifest", error_output.getvalue())

    def test_pcm_reads_channels_rate_and_duration_after_odd_padded_chunk(self):
        result = sound.inspect_sound(wav(rate=44100, channels=2, samples=441, metadata=[(b"JUNK", b"x")]))
        self.assertEqual((result["format"], result["source_rate"], result["channels"]), ("PCM", 44100, 2))
        self.assertEqual(result["metadata_chunks"], ["JUNK"])
        self.assertAlmostEqual(result["duration_seconds"], .01)
        self.assertIsNone(result["reason"])

    def test_lossy_riff_codecs_are_never_pcm_candidates(self):
        for tag in (2, 17, 85, 65534):
            with self.subTest(tag=tag):
                result = sound.inspect_sound(wav(tag=tag))
                self.assertEqual(result["format"], f"WAV_FORMAT_{tag}")
                self.assertIn("non-PCM", result["reason"])

    def test_loop_cue_and_playlist_metadata_prevent_conversion(self):
        for name in (b"cue ", b"smpl", b"plst", b"acid", b"wsmp"):
            with self.subTest(name=name):
                result = sound.inspect_sound(wav(metadata=[(name, b"\0" * 20)]))
                self.assertEqual(result["format"], "PCM")
                self.assertIn(name.decode(), result["reason"])

    def test_wave_sample_loop_metadata_prevents_conversion(self):
        # A 20-byte wsmp header followed by one 16-byte forward sample loop.
        sample_loop = struct.pack("<IHhiII", 20, 60, 0, 0, 0, 1) + struct.pack("<IIII", 16, 0, 0, 22050)
        result = sound.inspect_sound(wav(metadata=[(b"wsmp", sample_loop)]))
        self.assertEqual(result["format"], "PCM")
        self.assertEqual(result["reason"], "preserve WAV metadata: wsmp")

    def test_truncated_chunk_header_and_overlong_chunk_fail_closed(self):
        for tail in (b"fmt ", b"fmt " + struct.pack("<I", 80) + b"abcd"):
            value = b"RIFF" + struct.pack("<I", 4 + len(tail)) + b"WAVE" + tail
            result = sound.inspect_sound(value)
            self.assertEqual(result["format"], "invalid_WAV")
            self.assertIsNotNone(result["reason"])

    def test_short_or_inconsistent_riff_and_duplicate_data_fail_closed(self):
        valid = wav(samples=16)
        duplicate = valid + chunk(b"data", b"\0\0")
        duplicate = duplicate[:4] + struct.pack("<I", len(duplicate) - 8) + duplicate[8:]
        for value in (b"RIFF", valid[:-1], valid + b"trailer", duplicate):
            with self.subTest(length=len(value)):
                self.assertEqual(sound.inspect_sound(value)["format"], "invalid_WAV")

    def test_invalid_pcm_alignment_and_empty_samples_fail_closed(self):
        value = bytearray(wav(samples=16))
        struct.pack_into("<H", value, 32, 4)  # Mono 16-bit PCM requires 2-byte blocks.
        self.assertEqual(sound.inspect_sound(value)["format"], "invalid_WAV")
        self.assertEqual(sound.inspect_sound(wav(samples=0))["format"], "invalid_WAV")

    def test_bmu_and_id3_wrapped_bmu_are_already_compressed(self):
        for payload in (mp3_frame(), b"ID3\x03\x00\x00\x00\x00\x00\x00" + mp3_frame()):
            with self.subTest(id3=payload.startswith(b"ID3")):
                result = sound.inspect_sound(sound.BMU_HEADER + payload)
                self.assertEqual(result["format"], "BMU_MP3")
                self.assertEqual(result["reason"], "already compressed")
                self.assertEqual(result["source_rate"], 44100)

    def test_truncated_bmu_is_held_instead_of_reencoded(self):
        for payload in (b"", b"ID3", mp3_frame()[:20]):
            result = sound.inspect_sound(sound.BMU_HEADER + payload)
            self.assertEqual(result["format"], "BMU_MP3")
            self.assertIn("invalid BMU", result["reason"])

    def test_reserved_mp3_headers_are_rejected(self):
        for value in (b"", b"\xff\xe0\x00\x00", b"\xff\xfb\xfc\x00", b"\xff\xfb\x0c\x00"):
            with self.assertRaises(ValueError):
                sound.mp3_header(value)

    def test_supported_rates_and_low_rate_bitrate_limit(self):
        self.assertEqual(sound.target_encoding(22222), (22050, 96))
        self.assertEqual(sound.target_encoding(11025), (11025, 64))
        self.assertEqual(sound.target_encoding(16000), (16000, 96))
        self.assertEqual(sound.target_encoding(96000), (48000, 96))

    def test_decode_forces_mp3_demuxer_and_rejects_truncated_frames(self):
        info = {"output_rate": 44100, "channels": 1, "bitrate_kbps": 96, "duration_seconds": 1152 / 44100}
        with patch.object(sound, "run", return_value=bytes(1152 * 2)) as process:
            sound.validate_payload(mp3_frame(), info, "ffmpeg", 5)
        command = process.call_args.args[0]
        self.assertEqual(command[command.index("-f"):command.index("-f") + 4], ["-f", "mp3", "-i", "pipe:0"])
        self.assertEqual(process.call_args.kwargs["data"], mp3_frame())
        with self.assertRaisesRegex(ValueError, "Truncated"):
            sound.validate_payload(mp3_frame()[:-1], info, "ffmpeg", 5)

    def test_wrong_rate_and_empty_or_truncated_decodes_are_rejected(self):
        info = {"output_rate": 44100, "channels": 1, "bitrate_kbps": 96, "duration_seconds": 1}
        with self.assertRaisesRegex(ValueError, "differs"):
            sound.validate_payload(mp3_frame(), dict(info, channels=2), "ffmpeg", 5)
        for decoded in (b"", b"x", bytes(20)):
            with patch.object(sound, "run", return_value=decoded), self.assertRaises(ValueError):
                sound.validate_payload(mp3_frame(), info, "ffmpeg", 5)

    def test_process_errors_and_timeouts_stop_the_operation(self):
        with patch.object(sound.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"bad audio")):
            with self.assertRaisesRegex(RuntimeError, "bad audio"):
                sound.run(["ffmpeg"], timeout=5)
        with patch.object(sound.subprocess, "run", side_effect=subprocess.TimeoutExpired("ffmpeg", 5)):
            with self.assertRaises(subprocess.TimeoutExpired):
                sound.run(["ffmpeg"], timeout=5)


@unittest.skipUnless(shutil.which("git"), "Git is required for provenance tests")
class SoundTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "sw_sound").mkdir()
        self.originals = {}
        for name in ("first.wav", "second.wav"):
            self.originals[name] = wav()
            (self.root / "sw_sound" / name).write_bytes(self.originals[name])
        self.git("init", "-q")
        self.git("add", "sw_sound")
        self.git("-c", "user.name=Sound Tests", "-c", "user.email=sound-tests@example.invalid", "commit", "-qm", "source")
        original_run = sound.run

        def test_run(command, **kwargs):
            return b"ffmpeg version test\n" if command == ["test-ffmpeg", "-version"] else original_run(command, **kwargs)

        self.addCleanup(patch.stopall)
        patch.object(sound, "run", side_effect=test_run).start()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], stderr=subprocess.PIPE, timeout=10)

    def fake_encode(self, source, target, info, ffmpeg, timeout):
        result = sound.BMU_HEADER + mp3_frame()
        target.write_bytes(result)
        return result

    def compress(self, **kwargs):
        return sound.compress(self.root, "test-ffmpeg", 5, kwargs.pop("excluded", {}), **kwargs)

    def assert_originals(self):
        for name, source in self.originals.items():
            self.assertEqual((self.root / "sw_sound" / name).read_bytes(), source)

    def test_staging_directory_is_local_and_removed_after_an_error(self):
        with self.assertRaisesRegex(RuntimeError, "test failure"):
            with sound.staging_directory(self.root) as stage:
                self.assertEqual(stage.parent, self.root)
                (stage / "partial-output.bmu").write_bytes(b"partial")
                raise RuntimeError("test failure")
        self.assertFalse(stage.exists())

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell"), "Windows ACL regression")
    def test_staging_and_applied_files_inherit_windows_access_rules(self):
        def assert_inherits(path):
            command = ("$ErrorActionPreference = 'Stop'; "
                       "$acl = if ([System.IO.Directory]::Exists($env:SOUND_TEST_ACL_PATH)) "
                       "{ [System.IO.Directory]::GetAccessControl($env:SOUND_TEST_ACL_PATH) } "
                       "else { [System.IO.File]::GetAccessControl($env:SOUND_TEST_ACL_PATH) }; "
                       "[int]$acl.AreAccessRulesProtected; "
                       "@($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) "
                       "| Where-Object IsInherited).Count")
            environment = dict(os.environ, SOUND_TEST_ACL_PATH=str(path))
            values = subprocess.check_output(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                                             env=environment, text=True, timeout=15).splitlines()
            self.assertEqual(int(values[0]), 0, "Access rules must not become owner-only/protected")
            self.assertGreater(int(values[1]), 0, "Repository access rules must be inherited")

        with sound.staging_directory(self.root) as stage:
            assert_inherits(stage)
        with patch.object(sound, "encode", side_effect=self.fake_encode):
            self.compress(apply=True, manifest_path=self.root / "manifest.json")
        for path in (self.root / "sw_sound").glob("*.wav"):
            assert_inherits(path)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_applied_files_preserve_source_permission_bits(self):
        for path in (self.root / "sw_sound").glob("*.wav"):
            path.chmod(0o640)
        with patch.object(sound, "encode", side_effect=self.fake_encode):
            self.compress(apply=True, manifest_path=self.root / "manifest.json")
        for path in (self.root / "sw_sound").glob("*.wav"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def test_dry_run_measures_output_without_changing_sources(self):
        with patch.object(sound, "encode", side_effect=self.fake_encode):
            result = self.compress()
        self.assert_originals()
        self.assertEqual(result["summary"]["converted_files"], 2)
        self.assertGreater(result["summary"]["saved_bytes"], 0)
        self.assertEqual({row["status"] for row in result["files"]}, {"would_convert"})
        self.assertNotIn("target_bitrate_kbps", result)
        self.assertEqual(result["encoding_policy"], sound.ENCODING_POLICY)
        result["encoding_policy"]["default_bitrate_kbps"] = 320
        self.assertEqual(sound.target_encoding(44100), (44100, 96))
        self.assertFalse(list(self.root.glob(".sound-compression-*")))

    def test_apply_records_provenance_and_second_run_preserves_existing_audio(self):
        manifest = self.root / "manifest.json"
        with patch.object(sound, "encode", side_effect=self.fake_encode):
            result = self.compress(apply=True, manifest_path=manifest)
        self.assertEqual(result["state"], "complete")
        self.assertEqual(result["source_git_commit"], self.git("rev-parse", "HEAD").decode().strip())
        for row in result["files"]:
            self.assertEqual(row["source_sha256"], sound.digest(self.originals[Path(row["path"]).name]))
            self.assertEqual(row["output_sha256"], sound.digest((self.root / row["path"]).read_bytes()))
        manifest_bytes = manifest.read_bytes()
        with patch.object(sound, "encode", side_effect=AssertionError("Must not recompress BMU")):
            second = self.compress(apply=True, manifest_path=self.root / "second-manifest.json")
        self.assertEqual(second["summary"]["converted_files"], 0)
        self.assertEqual(manifest.read_bytes(), manifest_bytes)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.compress(apply=True, manifest_path=manifest)

    def test_failed_encoding_does_not_replace_any_source_or_create_manifest(self):
        def fail_second(source, target, *args):
            if source.stem == "1":
                raise ValueError("decode failed")
            return self.fake_encode(source, target, *args)

        manifest = self.root / "manifest.json"
        with patch.object(sound, "encode", side_effect=fail_second), self.assertRaisesRegex(ValueError, "decode failed"):
            self.compress(apply=True, manifest_path=manifest)
        self.assert_originals()
        self.assertFalse(manifest.exists())

    def test_failed_replacement_rolls_back_completed_replacements(self):
        actual_replace = os.replace

        def fail_second(source, target):
            if Path(source).suffix == ".bmu" and Path(target).name == "second.wav":
                raise OSError("simulated disk failure")
            actual_replace(source, target)

        manifest = self.root / "manifest.json"
        with patch.object(sound, "encode", side_effect=self.fake_encode), patch.object(sound.os, "replace", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "simulated disk failure"):
                self.compress(apply=True, manifest_path=manifest)
        self.assert_originals()
        self.assertFalse(manifest.exists())

    def test_uncommitted_sources_and_changes_during_encoding_are_rejected(self):
        first = self.root / "sw_sound" / "first.wav"
        first.write_bytes(wav(rate=44100))
        with self.assertRaisesRegex(ValueError, "differs from recorded Git"):
            self.compress()
        first.write_bytes(self.originals["first.wav"])

        def edit_during_encoding(*args):
            first.write_bytes(b"a concurrent edit")
            return self.fake_encode(*args)

        with patch.object(sound, "encode", side_effect=edit_during_encoding), self.assertRaisesRegex(ValueError, "changed during"):
            self.compress(apply=True, manifest_path=self.root / "manifest.json")
        self.assertEqual(first.read_bytes(), b"a concurrent edit")
        self.assertEqual((self.root / "sw_sound" / "second.wav").read_bytes(), self.originals["second.wav"])

    def test_skipped_resource_edits_during_encoding_prevent_a_completed_manifest(self):
        first = self.root / "sw_sound" / "first.wav"
        manifest = self.root / "manifest.json"

        def edit_skipped_resource(*args):
            first.write_bytes(b"a concurrent edit to an excluded resource")
            return self.fake_encode(*args)

        with patch.object(sound, "encode", side_effect=edit_skipped_resource):
            with self.assertRaisesRegex(ValueError, "Resource changed during compression: sw_sound/first.wav"):
                self.compress(excluded={"first": "engine loop"}, apply=True, manifest_path=manifest)
        self.assertFalse(manifest.exists())
        self.assertEqual((self.root / "sw_sound" / "second.wav").read_bytes(), self.originals["second.wav"])
        self.assertEqual(first.read_bytes(), b"a concurrent edit to an excluded resource")

    def test_added_and_removed_wavs_during_encoding_prevent_a_completed_manifest(self):
        first = self.root / "sw_sound" / "first.wav"
        added = self.root / "sw_sound" / "added.wav"
        manifest = self.root / "manifest.json"
        for operation in ("add", "remove"):
            with self.subTest(operation=operation):
                def change_inventory(*args):
                    added.write_bytes(wav()) if operation == "add" else first.unlink()
                    return self.fake_encode(*args)

                with patch.object(sound, "encode", side_effect=change_inventory):
                    with self.assertRaisesRegex(ValueError, "WAV inventory changed during compression"):
                        self.compress(excluded={"first": "engine loop"}, apply=True, manifest_path=manifest)
                self.assertFalse(manifest.exists())
                self.assertEqual((self.root / "sw_sound" / "second.wav").read_bytes(), self.originals["second.wav"])
                if added.exists():
                    added.unlink()
                first.write_bytes(self.originals["first.wav"])

    def test_skipped_edits_during_replacement_roll_back_without_completing_manifest(self):
        first = self.root / "sw_sound" / "first.wav"
        manifest = self.root / "manifest.json"
        actual_replace = os.replace

        def edit_after_replace(source, target):
            actual_replace(source, target)
            if Path(source).suffix == ".bmu":
                first.write_bytes(b"a concurrent edit during replacement")
                self.assertEqual(json.loads(manifest.read_text())["state"], "prepared")

        with patch.object(sound, "encode", side_effect=self.fake_encode), patch.object(sound.os, "replace", side_effect=edit_after_replace):
            with self.assertRaisesRegex(ValueError, "Resource changed during compression: sw_sound/first.wav"):
                self.compress(excluded={"first": "engine loop"}, apply=True, manifest_path=manifest)
        self.assertFalse(manifest.exists())
        self.assertEqual((self.root / "sw_sound" / "second.wav").read_bytes(), self.originals["second.wav"])
        self.assertEqual(first.read_bytes(), b"a concurrent edit during replacement")

    def assert_concurrent_candidate_change_is_preserved(self, *, delete):
        second = self.root / "sw_sound" / "second.wav"
        manifest = self.root / "manifest.json"
        actual_replace = os.replace

        def change_converted_file(source, target):
            actual_replace(source, target)
            if Path(source).suffix == ".bmu" and Path(target) == second:
                if delete:
                    second.unlink()
                else:
                    second.write_bytes(b"a concurrent edit to a converted file")

        with patch.object(sound, "encode", side_effect=self.fake_encode), patch.object(sound.os, "replace", side_effect=change_converted_file):
            with self.assertRaisesRegex(ValueError, "changed during compression"):
                self.compress(apply=True, manifest_path=manifest)
        self.assertEqual((self.root / "sw_sound" / "first.wav").read_bytes(), self.originals["first.wav"])
        if delete:
            self.assertFalse(second.exists())
        else:
            self.assertEqual(second.read_bytes(), b"a concurrent edit to a converted file")
        self.assertFalse(manifest.exists())
        self.assertFalse(list(self.root.glob(".sound-compression-*")))

    def test_rollback_preserves_concurrent_edits_to_converted_files(self):
        self.assert_concurrent_candidate_change_is_preserved(delete=False)

    def test_rollback_preserves_concurrent_deletions_and_restores_other_files(self):
        self.assert_concurrent_candidate_change_is_preserved(delete=True)

    def test_candidate_edit_between_replacements_survives_and_prior_output_rolls_back(self):
        second = self.root / "sw_sound" / "second.wav"
        manifest = self.root / "manifest.json"
        actual_replace = os.replace

        def edit_next_candidate(source, target):
            actual_replace(source, target)
            if Path(source).suffix == ".bmu" and Path(target).name == "first.wav":
                second.write_bytes(b"a concurrent edit before replacement")

        with patch.object(sound, "encode", side_effect=self.fake_encode), patch.object(sound.os, "replace", side_effect=edit_next_candidate):
            with self.assertRaisesRegex(ValueError, "Resource changed during compression: sw_sound/second.wav"):
                self.compress(apply=True, manifest_path=manifest)
        self.assertEqual((self.root / "sw_sound" / "first.wav").read_bytes(), self.originals["first.wav"])
        self.assertEqual(second.read_bytes(), b"a concurrent edit before replacement")
        self.assertFalse(manifest.exists())
        self.assertFalse(list(self.root.glob(".sound-compression-*")))

    def assert_failed_rollback_retains_recovery_provenance(self, *, final_report_failure):
        manifest = self.root / "manifest.json"
        commit = self.git("rev-parse", "HEAD").decode().strip()
        actual_replace, actual_dump = os.replace, json.dump

        def fail_replacement_or_rollback(source, target):
            if Path(source).suffix == ".wav":
                raise OSError("simulated rollback failure")
            if not final_report_failure and Path(target).name == "second.wav":
                raise OSError("simulated replacement failure")
            actual_replace(source, target)

        def fail_final_report(report, *args, **kwargs):
            if final_report_failure and report["state"] == "complete":
                raise OSError("simulated final report write failure")
            return actual_dump(report, *args, **kwargs)

        with patch.object(sound, "encode", side_effect=self.fake_encode), patch.object(sound.os, "replace", side_effect=fail_replacement_or_rollback):
            with patch.object(sound.json, "dump", side_effect=fail_final_report), self.assertRaises(RuntimeError) as error:
                self.compress(apply=True, manifest_path=manifest)
        self.assertIn("simulated rollback failure", str(error.exception))
        self.assertIn(commit, str(error.exception))
        self.assertIn(str(manifest), str(error.exception))
        recovery = json.loads(manifest.read_text())
        self.assertEqual(recovery["state"], "prepared")
        self.assertEqual(recovery["source_git_commit"], commit)
        for row in recovery["files"]:
            original = self.originals[Path(row["path"]).name]
            self.assertEqual(row["source_sha256"], sound.digest(original))
            self.assertEqual(self.git("show", f"{commit}:{row['path']}"), original)
        self.assertTrue((self.root / "sw_sound" / "first.wav").read_bytes().startswith(sound.BMU_HEADER))
        self.assertFalse(list(self.root.glob(".sound-compression-*")))
        self.assertFalse(list(self.root.glob(".manifest.json.*.tmp")))

    def test_failed_rollback_retains_prepared_manifest_and_original_git_provenance(self):
        self.assert_failed_rollback_retains_recovery_provenance(final_report_failure=False)

    def test_final_report_write_and_rollback_failures_retain_valid_prepared_manifest(self):
        self.assert_failed_rollback_retains_recovery_provenance(final_report_failure=True)

    def test_acid_loop_resource_is_held_byte_for_byte_during_apply(self):
        first = self.root / "sw_sound" / "first.wav"
        acid_loop = struct.pack("<IHHfIHHf", 0, 60, 0, 0.0, 4, 4, 4, 120.0)
        source = wav(metadata=[(b"acid", acid_loop)])
        first.write_bytes(source)
        with patch.object(sound, "encode", side_effect=self.fake_encode) as encoder:
            report = self.compress(apply=True, manifest_path=self.root / "manifest.json")
        self.assertEqual(encoder.call_count, 1)
        self.assertEqual(first.read_bytes(), source)
        self.assertEqual(report["files"][0]["status"], "skipped")
        self.assertEqual(report["files"][0]["reason"], "preserve WAV metadata: acid")

    def test_exclusions_and_no_savings_leave_pcm_unchanged(self):
        def larger_output(source, target, *args):
            output = source.read_bytes() + b"extra"
            target.write_bytes(output)
            return output

        with patch.object(sound, "encode", side_effect=larger_output) as encoder:
            result = self.compress(excluded={"first": "engine loop"}, apply=True, manifest_path=self.root / "manifest.json")
        self.assertEqual(encoder.call_count, 1)
        self.assert_originals()
        self.assertEqual(result["summary"]["converted_files"], 0)
        self.assertIn("engine loop", result["files"][0]["reason"])
        self.assertIn("not smaller", result["files"][1]["reason"])

    def test_verification_checks_unchanged_files_too(self):
        manifest = self.root / "manifest.json"
        self.compress(excluded={"first": "engine loop", "second": "engine loop"}, apply=True, manifest_path=manifest)
        sound.verify_manifest(self.root, manifest, "test-ffmpeg", 5)
        (self.root / "sw_sound" / "first.wav").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "does not match manifest"):
            sound.verify_manifest(self.root, manifest, "test-ffmpeg", 5)

    def test_verification_rejects_added_wav_not_recorded_in_manifest(self):
        manifest = self.root / "manifest.json"
        self.compress(excluded={"first": "engine loop", "second": "engine loop"}, apply=True, manifest_path=manifest)
        (self.root / "sw_sound" / "added.wav").write_bytes(wav())
        with self.assertRaisesRegex(ValueError, "Current WAV inventory does not match manifest \\(1 unexpected, 0 missing\\)"):
            sound.verify_manifest(self.root, manifest, "test-ffmpeg", 5)

    def test_verification_rejects_duplicate_manifest_paths_even_when_membership_matches(self):
        manifest = self.root / "manifest.json"
        report = self.compress(excluded={"first": "engine loop", "second": "engine loop"}, apply=True, manifest_path=manifest)
        report["files"].append(dict(report["files"][0]))
        manifest.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "Manifest contains duplicate WAV paths: sw_sound/first.wav"):
            sound.verify_manifest(self.root, manifest, "test-ffmpeg", 5)

    def test_verification_reports_missing_wav_as_manifest_inventory_mismatch(self):
        manifest = self.root / "manifest.json"
        self.compress(excluded={"first": "engine loop", "second": "engine loop"}, apply=True, manifest_path=manifest)
        (self.root / "sw_sound" / "first.wav").unlink()
        with self.assertRaisesRegex(ValueError, "Current WAV inventory does not match manifest \\(0 unexpected, 1 missing\\)"):
            sound.verify_manifest(self.root, manifest, "test-ffmpeg", 5)

    def test_completed_manifest_rejects_dry_run_and_unknown_statuses(self):
        manifest = self.root / "manifest.json"
        report = self.compress(excluded={"first": "engine loop", "second": "engine loop"}, apply=True, manifest_path=manifest)
        for status in ("would_convert", "convertedd", "", None):
            with self.subTest(status=status):
                report["files"][0]["status"] = status
                manifest.write_text(json.dumps(report))
                with self.assertRaisesRegex(ValueError, "Invalid status in completed apply manifest: sw_sound/first.wav"):
                    sound.verify_manifest(self.root, manifest, "test-ffmpeg", 5)

    def test_skipped_manifest_rows_require_unchanged_source_hash_and_size(self):
        manifest = self.root / "manifest.json"
        report = self.compress(excluded={"first": "engine loop", "second": "engine loop"}, apply=True, manifest_path=manifest)
        row = report["files"][0]
        for field, changed_value in (("source_bytes", row["source_bytes"] + 1), ("source_sha256", "0" * 64)):
            with self.subTest(field=field):
                original = row[field]
                row[field] = changed_value
                manifest.write_text(json.dumps(report))
                with self.assertRaisesRegex(ValueError, "Skipped resource must have identical source and output hashes and sizes"):
                    sound.verify_manifest(self.root, manifest, "test-ffmpeg", 5)
                row[field] = original

    def test_converted_resource_cannot_bypass_validation_by_changing_status_to_skipped(self):
        manifest = self.root / "manifest.json"
        with patch.object(sound, "encode", side_effect=self.fake_encode):
            report = self.compress(apply=True, manifest_path=manifest)
        report["files"][0]["status"] = "skipped"
        manifest.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "Skipped resource must have identical source and output hashes and sizes"):
            sound.verify_manifest(self.root, manifest, "test-ffmpeg", 5)

    def test_unsafe_paths_and_missing_manifest_are_rejected(self):
        for path in ("../outside.wav", str(self.root / "sw_sound" / "first.wav")):
            with self.assertRaises(ValueError):
                sound.safe_path(self.root, path)
        with self.assertRaisesRegex(ValueError, "requires --manifest"):
            self.compress(apply=True)
        with self.assertRaisesRegex(ValueError, "outside sw_sound"):
            self.compress(manifest_path=self.root / "sw_sound" / "report.json")

    def test_exclusion_manifest_normalizes_case_and_supports_reasons(self):
        manifest = self.root / "exclusions.json"
        manifest.write_text(json.dumps({"excluded_resrefs": {"FiRsT.wav": "loop"}}))
        self.assertEqual(sound.exclusions(manifest, ["SECOND"]),
                         {"first": "loop", "second": "explicit command-line exclusion"})
        with self.assertRaises(ValueError):
            sound.exclusions(None, ["../outside"])


class RealMp3EncodingTests(unittest.TestCase):
    def test_short_low_rate_odd_rate_and_stereo_audio_decode(self):
        if not FFMPEG:
            self.skipTest("Pass --ffmpeg <path> or set SOUND_FFMPEG to enable real MP3 encode/decode tests")
        with tempfile.TemporaryDirectory() as temporary:
            for rate, channels, samples in ((44100, 1, 441), (11025, 1, 3000), (22222, 2, 22222), (8000, 1, 8000)):
                with self.subTest(rate=rate, channels=channels):
                    source, target = Path(temporary) / "source.wav", Path(temporary) / "output.bmu"
                    source.write_bytes(wav(rate=rate, channels=channels, samples=samples))
                    info = sound.inspect_sound(source.read_bytes())
                    info["output_rate"], info["bitrate_kbps"] = sound.target_encoding(rate)
                    output = sound.encode(source, target, info, FFMPEG, 15)
                    self.assertTrue(output.startswith(sound.BMU_HEADER))
                    header = sound.mp3_header(output[8:])
                    self.assertEqual(header["channels"], channels)
                    self.assertEqual(header["bitrate_kbps"], 64 if rate < 16000 else 96)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--ffmpeg")
    arguments, remaining = parser.parse_known_args()
    if arguments.ffmpeg:
        FFMPEG = shutil.which(arguments.ffmpeg)
        if not FFMPEG:
            parser.error("--ffmpeg executable was not found")
    unittest.main(argv=[sys.argv[0], *remaining])
