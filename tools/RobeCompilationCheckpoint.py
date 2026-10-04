"""Resume compilation only; every resumed output still requires fresh validation."""
import hashlib
import json


def digest(data):
    return hashlib.sha256(data).hexdigest()


def key(source, compiler, parent, original, base, processors):
    values = {"domain": "swlor-robe-compilation", "version": 1,
              "source": digest(source), "compiler": compiler,
              "parent": parent if isinstance(parent, str) else digest(parent),
              "original": digest(original), "base": digest(base), "processors": processors}
    return digest(json.dumps(values, sort_keys=True).encode())


def record(compile_key, binary):
    return {"compilationKey": compile_key, "binarySha256": digest(binary)}


def matches(saved, compile_key, binary):
    return (isinstance(saved, dict) and saved.get("compilationKey") == compile_key
            and saved.get("binarySha256") == digest(binary))
