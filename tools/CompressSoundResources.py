#!/usr/bin/env python3
"""Compress eligible PCM sound resources without changing their NWN resrefs.

Dry runs encode and decode in a temporary directory to measure real savings.
Use --apply --manifest <new-path.json> to replace sources. Existing lossy audio,
WAV loop/cue metadata, and explicitly excluded resrefs are always preserved.
All output is validated before any source is replaced. The manifest records the
Git commit containing the original PCM files; keep it when publishing the assets.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import uuid


ROOT = Path(__file__).resolve().parents[1]
BMU_HEADER = b"BMU V1.0"
MP3_RATES = (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000)
LOOP_CHUNKS = {"cue ", "smpl", "plst", "acid", "wsmp"}
ENCODING_POLICY = {
    "default_bitrate_kbps": 96,
    "low_sample_rate_bitrate_kbps": 64,
    "low_sample_rate_threshold_hz": 16000,
    "low_sample_rate_condition": "output_sample_rate_hz < low_sample_rate_threshold_hz",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@contextmanager
def staging_directory(root: Path):
    # Python 3.14 tempfile uses mode 0700 on Windows, installing an owner-only
    # DACL that os.replace carries into the final resources. Normal mkdir
    # inherits repository access rules for both outputs and rollback copies.
    root = root.resolve(strict=True)
    stage = root / f".sound-compression-{uuid.uuid4().hex}"
    stage.mkdir()
    try:
        yield stage
    finally:
        if stage.resolve().parent != root or stage.is_symlink() or getattr(stage, "is_junction", lambda: False)():
            raise ValueError("Refusing to clean a staging directory outside the repository")
        shutil.rmtree(stage)


def mp3_header(data: bytes) -> dict:
    """Read a Layer III frame header, rejecting reserved/free-format values."""
    if len(data) < 4:
        raise ValueError("Truncated MP3 frame header")
    value = int.from_bytes(data[:4], "big")
    version, layer = (value >> 19) & 3, (value >> 17) & 3
    bitrate_index, rate_index = (value >> 12) & 15, (value >> 10) & 3
    if (value >> 21 != 0x7FF or version == 1 or layer != 1
            or bitrate_index in (0, 15) or rate_index == 3):
        raise ValueError("Invalid MP3 Layer III frame header")
    bitrates = ((0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
                if version == 3 else (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160))
    rate = (44100, 48000, 32000)[rate_index] // {3: 1, 2: 2, 0: 4}[version]
    bitrate = bitrates[bitrate_index]
    return {"sample_rate": rate, "channels": 1 if (value >> 6) & 3 == 3 else 2,
            "bitrate_kbps": bitrate,
            "frame_bytes": (144000 if version == 3 else 72000) * bitrate // rate + ((value >> 9) & 1)}


def inspect_sound(data: bytes) -> dict:
    """Classify by bytes, never by a .wav suffix. Malformed PCM fails closed."""
    info = {"format": "unknown", "source_rate": None, "channels": None,
            "metadata_chunks": [], "reason": "unsupported audio container"}
    if data.startswith(BMU_HEADER):
        info.update(format="BMU_MP3", reason="already compressed")
        try:
            payload = data[len(BMU_HEADER):]
            if payload.startswith(b"ID3"):
                if len(payload) < 10 or any(value & 0x80 for value in payload[6:10]):
                    raise ValueError("Invalid ID3 tag length")
                tag_size = sum(value << (7 * (3 - index)) for index, value in enumerate(payload[6:10]))
                payload = payload[10 + tag_size:]
            header = mp3_header(payload)
            if len(payload) < header["frame_bytes"]:
                raise ValueError("Truncated BMU MP3 frame")
            info.update(source_rate=header["sample_rate"], channels=header["channels"])
        except ValueError as error:
            info["reason"] = f"invalid BMU container: {error}"
        return info
    if data.startswith(b"ID3") or data[:2] == b"\xff\xfb":
        return dict(info, format="MP3", reason="already compressed")
    try:
        header = mp3_header(data)
        return dict(info, format="MP3", reason="already compressed",
                    source_rate=header["sample_rate"], channels=header["channels"])
    except ValueError:
        pass
    if not data.startswith(b"RIFF"):
        return info
    info["format"] = "invalid_WAV"
    try:
        if len(data) < 12 or data[8:12] != b"WAVE":
            raise ValueError("missing RIFF/WAVE header")
        if struct.unpack_from("<I", data, 4)[0] + 8 != len(data):
            raise ValueError("RIFF length does not match file length")
        chunks = {}
        offset = 12
        while offset < len(data):
            if offset + 8 > len(data):
                raise ValueError("truncated RIFF chunk header")
            name, size = struct.unpack_from("<4sI", data, offset)
            start, end = offset + 8, offset + 8 + size
            if end > len(data):
                raise ValueError("RIFF chunk extends past the end of the file")
            if name in (b"fmt ", b"data") and name in chunks:
                raise ValueError("duplicate format or data chunk")
            chunks[name] = data[start:end]
            if name not in (b"fmt ", b"data"):
                info["metadata_chunks"].append(name.decode("ascii", errors="replace"))
            offset = end + (size & 1)
            if offset > len(data):
                raise ValueError("missing RIFF chunk padding")
        if b"fmt " not in chunks or len(chunks[b"fmt "]) < 16 or b"data" not in chunks:
            raise ValueError("missing or truncated format/data chunk")
        tag, channels, rate, byte_rate, alignment, bits = struct.unpack_from("<HHIIHH", chunks[b"fmt "])
        info.update(format="PCM" if tag == 1 else f"WAV_FORMAT_{tag}",
                    source_rate=rate, channels=channels)
        if tag != 1:
            info["reason"] = "non-PCM WAV; never transcode lossy sources"
            return info
        if (channels not in (1, 2) or rate <= 0 or bits not in (8, 16, 24, 32)
                or alignment != channels * bits // 8 or byte_rate != rate * alignment):
            raise ValueError("unsupported or inconsistent PCM format")
        if not chunks[b"data"] or len(chunks[b"data"]) % alignment:
            raise ValueError("empty or incomplete PCM sample data")
        info["duration_seconds"] = len(chunks[b"data"]) / byte_rate
        metadata = sorted(LOOP_CHUNKS.intersection(info["metadata_chunks"]))
        info["reason"] = f"preserve WAV metadata: {', '.join(metadata)}" if metadata else None
    except (ValueError, struct.error) as error:
        info.update(format="invalid_WAV", reason=f"invalid WAV: {error}")
    return info


def target_encoding(source_rate: int) -> tuple[int, int]:
    rate = min(MP3_RATES, key=lambda supported: abs(supported - source_rate))
    bitrate = (ENCODING_POLICY["low_sample_rate_bitrate_kbps"]
               if rate < ENCODING_POLICY["low_sample_rate_threshold_hz"] else ENCODING_POLICY["default_bitrate_kbps"])
    return rate, bitrate


def run(command: list[str], *, timeout: float, data: bytes | None = None) -> bytes:
    result = subprocess.run(command, input=data, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def validate_payload(payload: bytes, info: dict, ffmpeg: str, timeout: float) -> None:
    """Check every MPEG frame and decode explicitly as MP3, including tiny clips."""
    offset = 0
    while offset < len(payload):
        header = mp3_header(payload[offset:])
        if any(header[key] != info[expected] for key, expected in
               (("sample_rate", "output_rate"), ("channels", "channels"), ("bitrate_kbps", "bitrate_kbps"))):
            raise ValueError("Encoded MP3 rate, channel count, or bitrate differs from requested output")
        offset += header["frame_bytes"]
        if offset > len(payload):
            raise ValueError("Truncated encoded MP3 frame")
    if not offset:
        raise ValueError("Empty encoded MP3")
    decoded = run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
                   "-err_detect", "explode", "-f", "mp3", "-i", "pipe:0", "-map", "0:a:0",
                   "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"], timeout=timeout, data=payload)
    if not decoded or len(decoded) % (2 * info["channels"]):
        raise ValueError("Decoded MP3 is empty or has an incomplete PCM sample")
    decoded_duration = len(decoded) / (2 * info["channels"] * info["output_rate"])
    # Tag-free MP3 has encoder delay and padded final frames. Allow at most three
    # MPEG-1 frames, scaled for the output rate, rather than a percentage of length.
    if abs(decoded_duration - info["duration_seconds"]) > 3 * 1152 / info["output_rate"]:
        raise ValueError("Decoded duration differs from the source beyond MP3 frame padding")


def encode(source: Path, target: Path, info: dict, ffmpeg: str, timeout: float) -> bytes:
    run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror", "-y", "-i", str(source),
         "-map", "0:a:0", "-map_metadata", "-1", "-c:a", "libmp3lame", "-b:a", f"{info['bitrate_kbps']}k",
         "-ar", str(info["output_rate"]), "-ac", str(info["channels"]), "-threads", "1",
         "-id3v2_version", "0", "-write_id3v1", "0", "-write_xing", "0", "-f", "mp3", str(target)],
        timeout=timeout)
    payload = target.read_bytes()
    validate_payload(payload, info, ffmpeg, timeout)
    output = BMU_HEADER + payload
    target.write_bytes(output)
    return output


def exclusions(manifest: Path | None, names: list[str]) -> dict[str, str]:
    values = {}
    if manifest:
        document = json.loads(manifest.read_text(encoding="utf-8-sig"))
        supplied = document["excluded_resrefs"]
        if isinstance(supplied, list):
            values.update({name: "explicit exclusion manifest" for name in supplied})
        elif isinstance(supplied, dict):
            values.update(supplied)
        else:
            raise ValueError("excluded_resrefs must be a list or a name-to-reason object")
    values.update({name: "explicit command-line exclusion" for name in names})
    normalized = {}
    for name, reason in values.items():
        if (not isinstance(name, str) or not name or "/" in name or "\\" in name
                or not isinstance(reason, str) or not reason.strip()):
            raise ValueError("Exclusions require a bare resource name and non-empty reason")
        normalized[Path(name).stem.casefold()] = reason
    return normalized


def safe_path(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts or not path.resolve().is_relative_to(root):
        raise ValueError(f"Path is outside the resource repository: {relative}")
    current = path
    while current != root:
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError(f"Resource path contains a symlink or junction: {relative}")
        current = current.parent
    return path


def git_snapshot(root: Path, timeout: float) -> tuple[str, dict[str, str]]:
    repository = run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], timeout=timeout).decode().strip()
    if Path(repository).resolve() != root:
        raise ValueError("--root must be the Git repository root")
    commit = run(["git", "-C", str(root), "rev-parse", "HEAD"], timeout=timeout).decode().strip()
    tree = run(["git", "-C", str(root), "ls-tree", "-rz", commit, "--", "sw_sound"], timeout=timeout)
    blobs = {}
    for entry in tree.split(b"\0"):
        if entry:
            metadata, path = entry.split(b"\t", 1)
            mode, kind, object_id = metadata.split()
            if kind == b"blob" and mode in (b"100644", b"100755"):
                blobs[path.decode("utf-8")] = object_id.decode("ascii")
    return commit, blobs


def require_committed_source(relative: str, data: bytes, blobs: dict[str, str]) -> None:
    expected = blobs.get(relative)
    if expected is None:
        raise ValueError(f"Source must be committed before compression: {relative}")
    algorithm = "sha256" if len(expected) == 64 else "sha1"
    actual = hashlib.new(algorithm, b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
    if actual != expected:
        raise ValueError(f"Source differs from recorded Git commit; commit it first: {relative}")


def wav_paths(sound_directory: Path) -> list[Path]:
    return sorted((path for path in sound_directory.rglob("*") if path.suffix.lower() == ".wav"),
                  key=lambda path: path.as_posix().casefold())


def validate_wav_membership(root: Path, rows: list[dict], *,
                            mismatch_message: str = "WAV inventory changed during compression") -> None:
    recorded = Counter(row["path"] for row in rows)
    duplicates = sorted(path for path, count in recorded.items() if count > 1)
    if duplicates:
        raise ValueError("Manifest contains duplicate WAV paths: " + ", ".join(duplicates))
    paths = {path.relative_to(root).as_posix() for path in wav_paths(safe_path(root, "sw_sound"))}
    expected = set(recorded)
    if paths != expected:
        raise ValueError(f"{mismatch_message} ({len(paths - expected)} unexpected, {len(expected - paths)} missing)")


def validate_inventory(root: Path, rows: list[dict], *, applied: bool = False) -> None:
    validate_wav_membership(root, rows)
    hash_key = "output_sha256" if applied else "source_sha256"
    for row in rows:
        path = safe_path(root, row["path"])
        if digest(path.read_bytes()) != row[hash_key]:
            raise ValueError(f"Resource changed during compression: {row['path']}")


def publish_complete_manifest(path: Path, report: dict) -> None:
    # Keep the durable prepared manifest intact until the complete report has
    # been written successfully. Use a sibling so replacement is atomic even
    # when the manifest is on a different volume from the sound repository.
    pending = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    published = False
    try:
        with pending.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(report, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(pending, path)
        published = True
    finally:
        if not published:
            pending.unlink(missing_ok=True)


def compress(root: Path, ffmpeg: str, timeout: float, excluded: dict[str, str],
             apply: bool = False, manifest_path: Path | None = None) -> dict:
    root = root.resolve(strict=True)
    sound_directory = safe_path(root, "sw_sound")
    if not sound_directory.is_dir():
        raise ValueError("Missing sw_sound directory")
    if apply and manifest_path is None:
        raise ValueError("--apply requires --manifest to preserve source provenance")
    if manifest_path:
        manifest_path = manifest_path.absolute()
        if manifest_path.exists() or manifest_path.is_symlink():
            raise ValueError("Manifest already exists; keep the original provenance and choose a new path")
        if manifest_path.resolve().is_relative_to(sound_directory):
            raise ValueError("The manifest must be outside sw_sound")
        if not manifest_path.parent.is_dir():
            raise ValueError("Manifest parent directory must already exist")
    commit, blobs = git_snapshot(root, timeout)
    version = run([ffmpeg, "-version"], timeout=timeout).decode(errors="replace").splitlines()[0]
    report = {"schema_version": 1, "source_git_commit": commit, "encoder_version": version,
              "mode": "apply" if apply else "dry_run", "state": "complete", "encoding_policy": deepcopy(ENCODING_POLICY),
              "files": []}
    with staging_directory(root) as stage:
        candidates = []
        paths = wav_paths(sound_directory)
        for index, path in enumerate(paths):
            relative = path.relative_to(root).as_posix()
            path = safe_path(root, relative)
            source = path.read_bytes()
            row = dict(inspect_sound(source), path=relative, status="skipped", source_bytes=len(source),
                       source_sha256=digest(source), output_bytes=len(source), output_sha256=digest(source),
                       output_rate=None, bitrate_kbps=None)
            if path.stem.casefold() in excluded:
                reason = "explicit exclusion: " + excluded[path.stem.casefold()]
                row["reason"] = f"{row['reason']}; {reason}" if row["reason"] else reason
            if row["reason"] is None:
                require_committed_source(relative, source, blobs)
                row["output_rate"], row["bitrate_kbps"] = target_encoding(row["source_rate"])
                original, encoded = stage / f"{index}.wav", stage / f"{index}.bmu"
                original.write_bytes(source)
                output = encode(original, encoded, row, ffmpeg, timeout)
                if len(output) < len(source):
                    row.update(status="converted" if apply else "would_convert", output_bytes=len(output),
                               output_sha256=digest(output))
                    candidates.append((path, original, encoded, row))
                else:
                    row.update(reason="encoded output is not smaller", output_rate=None, bitrate_kbps=None)
            report["files"].append(row)
            if (index + 1) % 100 == 0:
                print(f"Checked {index + 1}/{len(paths)} sound resources", flush=True)
        counts = Counter(row["status"] for row in report["files"])
        source_bytes = sum(row["source_bytes"] for row in report["files"])
        output_bytes = sum(row["output_bytes"] for row in report["files"])
        report["summary"] = {"total_files": len(paths), "converted_files": len(candidates),
                             "skipped_files": counts["skipped"], "source_bytes": source_bytes,
                             "output_bytes": output_bytes, "saved_bytes": source_bytes - output_bytes}
        # A completed manifest describes the whole WAV collection, including
        # skipped resources and its membership, not just converted candidates.
        validate_inventory(root, report["files"])
        manifest = None
        replaced = []
        try:
            if manifest_path:
                manifest = manifest_path.open("x", encoding="utf-8", newline="\n")
                report["state"] = "prepared" if apply else "complete"
                json.dump(report, manifest, indent=2)
                manifest.write("\n")
                manifest.flush()
                os.fsync(manifest.fileno())
            if apply:
                for path, original, encoded, row in candidates:
                    safe_path(root, row["path"])
                    if digest(path.read_bytes()) != row["source_sha256"]:
                        raise ValueError(f"Resource changed during compression: {row['path']}")
                    shutil.copymode(path, encoded)
                    os.replace(encoded, path)
                    replaced.append((path, original, row["output_sha256"]))
                validate_inventory(root, report["files"], applied=True)
                report["state"] = "complete"
                manifest.close()
                publish_complete_manifest(manifest_path, report)
        except BaseException as error:
            rollback_errors = []
            for path, original, output_hash in reversed(replaced):
                try:
                    # Restore only bytes written by this run. A concurrent
                    # edit, deletion, or replacement belongs to its author.
                    if path.is_symlink() or not path.is_file():
                        continue
                    safe_path(root, path.relative_to(root).as_posix())
                    try:
                        current_hash = digest(path.read_bytes())
                    except FileNotFoundError:
                        continue
                    if current_hash == output_hash:
                        shutil.copymode(path, original)
                        os.replace(original, path)
                except (OSError, ValueError) as rollback_error:
                    rollback_errors.append(f"{path.name}: {rollback_error}")
            if rollback_errors:
                raise RuntimeError(f"Compression failed ({error}); rollback incomplete. Original sources are in "
                                   f"Git commit {commit}; prepared recovery manifest retained at {manifest_path}. "
                                   "Restoration errors: " + "; ".join(rollback_errors)) from error
            if manifest:
                try:
                    manifest.close()
                finally:
                    manifest_path.unlink(missing_ok=True)
            raise
        finally:
            if manifest and not manifest.closed:
                manifest.close()
    return report


def verify_manifest(root: Path, manifest_path: Path, ffmpeg: str, timeout: float) -> dict:
    root = root.resolve(strict=True)
    report = json.loads(manifest_path.read_text(encoding="utf-8"))
    if report.get("schema_version") != 1 or report.get("mode") != "apply" or report.get("state") != "complete":
        raise ValueError("Verification requires a completed apply manifest")
    validate_wav_membership(root, report["files"], mismatch_message="Current WAV inventory does not match manifest")
    for row in report["files"]:
        if row.get("status") not in ("converted", "skipped"):
            raise ValueError(f"Invalid status in completed apply manifest: {row['path']} ({row.get('status')!r})")
        if row["status"] == "skipped" and (row["source_bytes"] != row["output_bytes"]
                                           or row["source_sha256"] != row["output_sha256"]):
            raise ValueError(f"Skipped resource must have identical source and output hashes and sizes: {row['path']}")
        data = safe_path(root, row["path"]).read_bytes()
        if len(data) != row["output_bytes"] or digest(data) != row["output_sha256"]:
            raise ValueError(f"Current resource does not match manifest: {row['path']}")
        if row["status"] == "converted":
            if not data.startswith(BMU_HEADER):
                raise ValueError(f"Missing BMU wrapper: {row['path']}")
            if row["output_bytes"] >= row["source_bytes"]:
                raise ValueError(f"Converted resource does not save space: {row['path']}")
            validate_payload(data[len(BMU_HEADER):], row, ffmpeg, timeout)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="HAK Git repository containing sw_sound")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg executable with libmp3lame support")
    parser.add_argument("--timeout", type=float, default=60, help="Maximum seconds per subprocess (default: 60)")
    parser.add_argument("--exclude-manifest", type=Path, help='JSON with an "excluded_resrefs" list or reason map')
    parser.add_argument("--exclude-resref", action="append", default=[], help="Preserve this resref; repeat as needed")
    parser.add_argument("--manifest", type=Path, help="Write a NEW provenance/measurement JSON (required for --apply)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Replace sources after every output passes validation")
    mode.add_argument("--verify-manifest", type=Path, help="Check hashes and decode outputs from an applied manifest")
    args = parser.parse_args()
    if not 0 < args.timeout <= 600:
        parser.error("--timeout must be greater than zero and at most 600 seconds")
    if args.apply and not args.exclude_manifest:
        parser.error("--apply requires --exclude-manifest; generate engine loop exclusions with the parent "
                     "repository's tools/AuditSoundCompression.py --write-exclusions <path.json>")
    try:
        ffmpeg = shutil.which(args.ffmpeg)
        if not ffmpeg:
            raise ValueError("FFmpeg was not found; pass --ffmpeg with its executable path")
        if args.verify_manifest:
            report = verify_manifest(args.root, args.verify_manifest, ffmpeg, args.timeout)
            print(f"Verified {len(report['files'])} unchanged resource hashes and decoded every converted file")
        else:
            report = compress(args.root, ffmpeg, args.timeout, exclusions(args.exclude_manifest, args.exclude_resref),
                              args.apply, args.manifest)
        print(json.dumps(report["summary"], indent=2))
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
