"""Content proofs for reusing an already validated compiled robe model.

Callers must still run current whole-chain pose checks. A cache hit proves only
that this model's exact inputs and owned compiled output have not changed.
"""

import hashlib
import json
import re


_VERSION = 1
_DIGEST = re.compile(r"[0-9a-fA-F]{64}\Z")


def _digest(value):
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ValueError("Expected a SHA-256 hexadecimal digest.")
    return value.lower()


def _bytes(value):
    if not isinstance(value, bytes) or not value:
        raise ValueError("Expected nonempty immutable model bytes.")
    return value


def build_key(source, compiler_digest, parent_digest, validation_digest):
    """Hash exact desired ASCII bytes and the compiler, parent and validator."""
    inputs = {
        "domain": "swlor-robe-build",
        "version": _VERSION,
        "source": hashlib.sha256(_bytes(source)).hexdigest(),
        "compiler": _digest(compiler_digest),
        "parent": _digest(parent_digest),
        "validation": _digest(validation_digest),
    }
    payload = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def make_record(key, binary):
    """Record a successful compile and validation; never use before validation."""
    return {
        "version": _VERSION,
        "key": _digest(key),
        "binarySha256": hashlib.sha256(_bytes(binary)).hexdigest(),
    }


def reusable(record, key, binary, ownership_hash):
    """Fail closed unless cache, desired inputs, file bytes and ownership agree."""
    try:
        if not isinstance(record, dict) or type(record.get("version")) is not int:
            return False
        if record["version"] != _VERSION or _digest(record.get("key")) != _digest(key):
            return False
        actual = hashlib.sha256(_bytes(binary)).hexdigest()
        return actual == _digest(record.get("binarySha256")) == _digest(ownership_hash)
    except (ValueError, TypeError):
        return False
