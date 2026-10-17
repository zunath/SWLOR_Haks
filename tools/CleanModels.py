"""Pinned CleanModels CLI shared by model authoring and HAK generators."""
from pathlib import Path
import hashlib
import json
import os
import re
import tempfile
import shutil
import struct
import subprocess

VERSION = "4.4.2"
COMPILER_SHA256 = "7d402af35ec5aff6b93e7e9fdf03edd43b91390329d9c825f008a64fdcab8d4a"


def find_compiler(explicit=None):
    configured = explicit or os.environ.get("CLEANMODELS")
    installed = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share")) / "SWLOR/tools/cleanmodels-qt/1.1.7/cleanmodels-qt/cleanmodels.exe"
    path = Path(configured or (installed if installed.is_file() else shutil.which("cleanmodels") or installed)).resolve()
    if not path.is_file():
        raise RuntimeError("CleanModels is missing. Run SWLOR_Haks/tools/SetupModelCompiler.ps1 or set CLEANMODELS to its cleanmodels.exe.")
    if hashlib.sha256(path.read_bytes()).hexdigest() != COMPILER_SHA256:
        raise RuntimeError(f"Unsupported CleanModels executable: {path}; use v{VERSION} bundled with cleanmodels-qt v1.1.7.")
    return path


def validate_binary(path):
    data = path.read_bytes()
    if len(data) < 244:
        raise ValueError(f"Truncated compiled MDL: {path.name}")
    magic, model_size, raw_size = struct.unpack_from("<III", data)
    if magic != 0 or 12 + model_size + raw_size != len(data):
        raise ValueError(f"Invalid compiled MDL header: {path.name}")


def explicit_orientations(data):
    """Spell out omitted static identity rotations for CleanModels v4.4.2.

    Its parser initializes an omitted orientation as axis-angle (0,0,0,1),
    which compiles to a non-unit quaternion. Keep authored rotations untouched.
    Animation nodes inherit their transforms, so only prepare model geometry.
    """
    end = re.search(rb"(?im)^\s*endmodelgeom\b", data)
    geometry, animations = (data[:end.start()], data[end.start():]) if end else (data, b"")
    def prepare(match):
        body = match[2]
        if not re.search(rb"(?im)^\s*orientation(?:key|bezierkey)?\b", body):
            body += b"  orientation 0 0 1 0\n"
        return match[1] + body + match[3]
    geometry = re.sub(rb"(?im)^(\s*node\s+\S+\s+\S+[^\r\n]*\r?\n)([\s\S]*?)(^\s*endnode\b)", prepare, geometry)
    return geometry + animations


def run(compiler, staging, command, inputs, output, log, resources=(), game_dir=None):
    """Keep diagnostics and fail on any per-file error, even in a partial batch."""
    inputs = list(inputs)
    if not inputs:
        raise ValueError("No model compiler inputs")
    original_inputs = list(inputs)
    if command == "compile" or len(inputs) > 1:
        prepared = Path(tempfile.mkdtemp(prefix="cleanmodels-input-", dir=staging))
        inputs = []
        for source in original_inputs:
            target = prepared / source.name
            if command == "compile":
                target.write_bytes(explicit_orientations(source.read_bytes()))
            else:
                try:
                    os.link(source, target)
                except OSError:
                    shutil.copyfile(source, target)
            inputs.append(target)
    arguments = [str(compiler), command, "--json", "--overwrite"]
    for resource in dict.fromkeys([staging, *(path.parent for path in original_inputs), *resources]):
        arguments.extend(["--resource-dir", str(resource)])
    if game_dir is not None:
        arguments.extend(["--game-dir", str(game_dir)])
    destination = str(Path(output) / inputs[0].name) if len(inputs) == 1 and Path(output).is_dir() else str(output)
    # Directory batches avoid Windows' command-line limit for thousands of MDLs.
    selected = [str(prepared)] if len(inputs) > 1 else [str(inputs[0])]
    arguments.extend([*selected, destination])
    result = subprocess.run(arguments, cwd=staging, capture_output=True, text=True,
                            encoding="utf8", errors="replace", timeout=600)
    (staging / log).write_text(result.stdout + "\n" + result.stderr, encoding="utf8")
    if result.returncode:
        raise RuntimeError(f"CleanModels {command} failed; see {staging / log}: {result.stdout.strip()} {result.stderr.strip()}")
    try:
        records = json.loads(result.stdout)
        records = records if isinstance(records, list) else [records]
    except (ValueError, TypeError) as error:
        raise RuntimeError(f"Invalid CleanModels report; see {staging / log}") from error
    if len(records) != len(inputs) or any(not isinstance(record, dict) or record.get("error") for record in records):
        raise RuntimeError(f"CleanModels {command} reported failed files; see {staging / log}")
    return records
