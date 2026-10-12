"""Author checked-in LOD redirects for otherwise identical model resources.

Only self-identity fields are ignored when comparing models. Materials, bones,
inverse binds, supermodels, animations and every geometry byte must match.
Normal builds validate and package these resources; they do not generate MDLs.
"""
from __future__ import annotations

import argparse
import collections
import functools
import hashlib
import io
import json
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "tools/ModelLodAssets.json"
RESREF = re.compile(r"[a-z0-9_]{1,16}\Z")


def content(data):
    return data if data[:4] == bytes(4) else data.replace(b"\r\n", b"\n")


def digest(data):
    return hashlib.sha256(content(data)).hexdigest()


def model_name(data):
    if data[:4] == bytes(4):
        if len(data) < 244:
            raise ValueError("Short compiled model")
        return data[20:84].split(b"\0", 1)[0].decode("ascii")
    match = re.search(rb"(?im)^\s*newmodel\s+(\S+)", data)
    return match[1].decode("ascii") if match else ""


def rename_identity(data, target, mode="full"):
    """Change model/root identity only; never rewrite texture or bone names."""
    name = model_name(data).encode("ascii")
    target = target.encode("ascii")
    if not name or len(target) > 32:
        raise ValueError("Invalid model identity")
    if data[:4] != bytes(4):
        prefix = (rb"(?im)^([ \t]*(?:(?:newmodel|beginmodelgeom|endmodelgeom|donemodel|"
                  rb"setsupermodel|parent)[ \t]+|(?:node|newanim|doneanim)[ \t]+\S+[ \t]+))")
        # Self names are case sensitive here so reconstruction preserves exact bytes.
        pattern = prefix + rb"(?-i:" + re.escape(name) + rb")(?=[ \t\r\n]|$)"
        return re.sub(pattern, lambda match: match[1] + target, data)

    output = bytearray(data)
    uint = lambda offset: struct.unpack_from("<I", data, offset)[0]

    def field(offset, size):
        if offset < 12 or offset + size > len(data):
            raise ValueError("Invalid model identity field")
        # Nonzero padding is retained rather than silently normalized away.
        if data[offset:offset + size] == name.ljust(size, b"\0"):
            output[offset:offset + size] = target.ljust(size, b"\0")

    def array(pointer, count):
        offset = 12 + pointer
        if count > 100000 or offset + count * 4 > len(data):
            raise ValueError("Invalid model pointer array")
        return [uint(offset + i * 4) for i in range(count)]

    visited = set()

    def nodes(pointer):
        offset = 12 + pointer
        if offset in visited or offset + 112 > len(data):
            raise ValueError("Invalid or cyclic model node")
        visited.add(offset)
        field(offset + 32, 32)
        for child in array(uint(offset + 72), uint(offset + 76)):
            nodes(child)

    field(20, 64)
    if mode == "root":
        field(12 + uint(84) + 32, 32)
        return bytes(output)
    if mode != "full":
        raise ValueError("Unknown model identity mode")
    nodes(uint(84))
    for pointer in array(uint(132), uint(136)):
        animation = 12 + pointer
        if animation + 148 > len(data):
            raise ValueError("Invalid model animation")
        field(animation + 84, 64)
        nodes(uint(animation + 72))
    return bytes(output)


def identity(data, mode="full"):
    data = content(data)
    return rename_identity(data, "@shared", mode) if model_name(data) else data


# Client checks against master's MDLs: area tiles, placeables, a tower shield and a
# cross-race torso drew nothing or the wrong model through a redirect, and with
# graphics.lod.enabled off the client ignores every redirect (helmet heads vanished).
# Only generated robe-phenotype cloaks, which have no MDL to replace, may redirect.
ROBE_CLOAK_MANIFEST = "tools/RobeCloakModels.json"


@functools.lru_cache(maxsize=4)
def _robe_cloaks(path, modified):
    return frozenset(name.lower() for name in json.loads(Path(path).read_text())["models"])


def lod_supported(path, root):
    manifest = Path(root) / ROBE_CLOAK_MANIFEST
    path = Path(path)
    return (manifest.is_file() and path.parent.name.lower() == "sw_pt_cloak" and
            path.stem.lower() in _robe_cloaks(str(manifest), manifest.stat().st_mtime_ns))


def requires_native_attachment(path, data):
    """Empty robe MDLs stop the native client falling back to a visible base robe.

    The robe attachment lookup checks for an MDL before resolving LOD redirects.
    A redirect alone therefore loads the base-phenotype robe over the RGB body.
    Keep these tiny sentinels; their geometry already lives in the body root.
    """
    if not re.fullmatch(r"p[fm][a-z]\d+_robe\d{3}", Path(path).stem.lower()):
        return False
    if data[:4] != bytes(4):
        return (len(re.findall(rb"(?im)^\s*node\s+", data)) == 1 and
                re.search(rb"(?im)^\s*node\s+dummy\s+", data) is not None and
                re.search(rb"(?im)^\s*newanim\s+", data) is None)
    if len(data) < 244:
        raise ValueError("Short compiled robe attachment")
    root = 12 + struct.unpack_from("<I", data, 84)[0]
    if root + 112 > len(data):
        raise ValueError("Invalid compiled robe attachment root")
    children = struct.unpack_from("<I", data, root + 76)[0]
    flags = struct.unpack_from("<I", data, root + 108)[0]
    animations = struct.unpack_from("<I", data, 136)[0]
    return flags == 1 and children == 0 and animations == 0


@functools.lru_cache(maxsize=4)
def _catalog(path, modified):
    return json.loads(Path(path).read_text())["aliases"]


def catalog(root):
    path = Path(root) / MANIFEST
    return _catalog(str(path), path.stat().st_mtime_ns) if path.is_file() else {}


def safe_path(root, relative):
    path = (Path(root) / relative).resolve()
    if not path.is_relative_to(Path(root).resolve()) or path.suffix != ".mdl":
        raise ValueError(f"Invalid model path: {relative}")
    return path


def restore_identity(data, record):
    for name, mode in record.get("identity_steps", [[record["model_name"], record["identity_mode"]]]):
        if model_name(data):
            data = rename_identity(data, name, mode)
    return data


def read_alias(path, root):
    path = Path(path)
    relative = path.relative_to(root).as_posix()
    record = catalog(root).get(relative)
    if record is None:
        raise ValueError(f"Untracked model redirect: {path.with_suffix('.lod')}")
    canonical = safe_path(root, record["canonical"])
    if not RESREF.fullmatch(canonical.stem.lower()) or canonical.stem.lower() == path.stem.lower():
        raise ValueError(f"Invalid or self-referencing model redirect: {relative}")
    if canonical.with_suffix(".lod").exists():
        raise ValueError(f"Model redirect must point directly to an MDL: {relative}")
    if path.with_suffix(".lod").read_bytes().lower() != (canonical.stem.lower() + "\n").encode("ascii"):
        raise ValueError(f"Model redirect target changed: {relative}")
    data = content(canonical.read_bytes())
    if digest(data) != record["canonical_sha256"]:
        raise ValueError(f"Canonical model changed: {record['canonical']}")
    restored = restore_identity(data, record)
    if digest(restored) != record["source_sha256"] or len(restored) != record["source_bytes"]:
        raise ValueError(f"Model redirect cannot reproduce its original geometry/bindings: {relative}")
    return restored


class ModelPath(type(Path())):
    """Logical MDL path for authoring/audits; reads a checked LOD without writing an MDL."""

    def _alias(self):
        path = Path(self)
        return (path.suffix == ".mdl" and not path.is_file() and
                path.is_relative_to(ROOT) and path.with_suffix(".lod").is_file())

    def read_bytes(self):
        return read_alias(self, ROOT) if self._alias() else Path(self).read_bytes()

    def is_file(self, *, follow_symlinks=True):
        return Path(self).is_file() or self._alias()

    def exists(self, *, follow_symlinks=True):
        return Path(self).exists() or self._alias()

    def read_text(self, encoding=None, errors=None, newline=None):
        return self.read_bytes().decode(encoding or "utf-8", errors or "strict")

    def open(self, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        if not self._alias():
            return Path(self).open(mode, buffering, encoding, errors, newline)
        if mode not in {"r", "rb", "rt"}:
            raise ValueError("Expand shared models explicitly before editing their original sources")
        data = self.read_bytes()
        return io.BytesIO(data) if "b" in mode else io.StringIO(data.decode(encoding or "utf-8", errors or "strict"))

    def stat(self, *, follow_symlinks=True):
        path = Path(self)
        if self._alias():
            return path.with_suffix(".lod").stat(follow_symlinks=follow_symlinks)
        return path.stat(follow_symlinks=follow_symlinks)


def model_paths(directory):
    paths = {}
    for path in Path(directory).glob("*.mdl"):
        if path.stem.lower() in paths:
            raise ValueError(f"Duplicate case-insensitive model resource: {path}")
        paths[path.stem.lower()] = ModelPath(path)
    for path in Path(directory).glob("*.lod"):
        if path.stem.lower() in paths:
            raise ValueError(f"LOD shadows an MDL with the same name: {path}")
        paths[path.stem.lower()] = ModelPath(path.with_suffix(".mdl"))
    return list(paths.values())


def directories(root):
    raw = (Path(root) / "hakbuilder.json").read_text(encoding="utf-8-sig")
    config = json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
    result = []
    for hak in config["HakList"]:
        path = (Path(root) / hak["Path"]).resolve()
        if not path.is_relative_to(Path(root).resolve()) or not path.is_dir():
            raise ValueError(f"Missing or redirected HAK directory: {path}")
        result.append(path)
    return result


def check(root=ROOT):
    root = Path(root)
    aliases = catalog(root)
    hak_directories = directories(root)
    actual = {p.with_suffix(".mdl").relative_to(root).as_posix()
              for directory in hak_directories for p in directory.glob("*.lod")}
    errors = []
    if actual != aliases.keys():
        errors.append("Model LOD manifest does not cover exactly the checked-in redirects")
    manifest = root / MANIFEST
    stock_names = {name.lower() for name in
                   (json.loads(manifest.read_text()) if manifest.is_file() else {}).get("native_overrides", [])}
    # HAK priority decides which same-named resource the client loads; a name defined
    # once cannot be overridden by a resource other than the one validated here.
    resources = collections.defaultdict(list)
    for directory in hak_directories:
        for path in (*directory.glob("*.mdl"), *directory.glob("*.lod")):
            resources[path.stem.lower()].append(path)
    for name in sorted(stock_names):
        if not any(path.suffix.lower() == ".mdl" for path in resources.get(name, [])):
            errors.append(f"Override of a base-game model is missing its native MDL: {name}")
    for relative in sorted(aliases):
        try:
            path = safe_path(root, relative)
            if path.is_file():
                raise ValueError(f"LOD shadows its old MDL: {relative}")
            if path.stem.lower() in stock_names:
                raise ValueError(f"Override of a base-game model requires a native MDL: {relative}")
            if not lod_supported(path, root):
                raise ValueError(f"The client does not follow redirects for this model type; keep its MDL: {relative}")
            target = Path(aliases[relative]["canonical"]).stem.lower()
            for name in (path.stem.lower(), target):
                if len(resources.get(name, [])) > 1:
                    raise ValueError(f"Redirect resource is defined by more than one HAK: {name}")
            data = read_alias(path, root)
            if requires_native_attachment(path, data):
                raise ValueError(f"Empty robe attachment requires a native MDL: {relative}")
        except (ValueError, OSError, KeyError) as error:
            errors.append(str(error))
    return errors


def apply(root=ROOT, stock_models=frozenset()):
    root = Path(root)
    stock_models = {name.lower() for name in stock_models}
    all_paths = []
    for directory in directories(root):
        for path in model_paths(directory):
            if not RESREF.fullmatch(path.stem.lower()):
                continue
            all_paths.append(Path(path))
    aliases = {}
    dependents = collections.defaultdict(list)
    # Some client lookups test for an MDL before resolving a LOD. An override that only
    # exists as a LOD would then load the base-game MDL of the same name instead.
    native_attachments = {path.relative_to(root).as_posix(): content(ModelPath(path).read_bytes())
                          for path in all_paths
                          if path.stem.lower() in stock_models or
                          requires_native_attachment(path, ModelPath(path).read_bytes())}
    overrides = sorted({Path(relative).stem.lower() for relative in native_attachments} & stock_models)
    # Validate all comparisons and reconstruction before deleting any source file.
    for mode in ("root", "full"):
        groups = collections.defaultdict(list)
        names = collections.defaultdict(set)
        for path in all_paths:
            relative = path.relative_to(root).as_posix()
            if relative in aliases or relative in native_attachments:
                continue
            key = hashlib.sha256(identity(ModelPath(path).read_bytes(), mode)).hexdigest()
            groups[key].append(path)
            names[path.stem.lower()].add(key)
        for paths in groups.values():
            if len(paths) < 2:
                continue
            candidates = [p for p in paths if len(names[p.stem.lower()]) == 1 and
                          p.is_file() and not p.with_suffix(".lod").exists()]
            if not candidates:
                continue
            def rank(path):
                generated = bool(re.fullmatch(r"p[fm][a-z][1-9]\d*(?:_.*)?", path.stem))
                return generated, len(path.stem), path.as_posix()
            canonical = min(candidates, key=rank)
            canonical_data = content(ModelPath(canonical).read_bytes())
            canonical_relative = canonical.relative_to(root).as_posix()
            for path in paths:
                if path.stem.lower() == canonical.stem.lower() or not lod_supported(path, root):
                    continue
                original = content(ModelPath(path).read_bytes())
                name = model_name(original)
                restored = rename_identity(canonical_data, name, mode) if name else canonical_data
                if restored != original:
                    raise ValueError(f"Canonical model cannot reproduce the exact source: {path}")
                relative = path.relative_to(root).as_posix()
                # A later, broader identity comparison may also merge earlier
                # canonical models. Flatten their dependents to this retained
                # MDL while preserving an exact reconstruction proof for each.
                for dependent in dependents.pop(relative, []):
                    steps = dependent.get("identity_steps", [[dependent["model_name"], dependent["identity_mode"]]])
                    dependent["identity_steps"] = [[name, mode]] + steps
                    dependent["canonical"] = canonical_relative
                    dependent["canonical_sha256"] = digest(canonical_data)
                    if digest(restore_identity(canonical_data, dependent)) != dependent["source_sha256"]:
                        raise ValueError(f"Flattened model redirect lost its source identity: {path}")
                    dependents[canonical_relative].append(dependent)
                aliases[relative] = {
                    "canonical": canonical_relative, "identity_mode": mode,
                    "canonical_sha256": digest(canonical_data), "model_name": name,
                    "source_sha256": digest(original), "source_bytes": len(original)}
                dependents[canonical_relative].append(aliases[relative])
        print(f"Compared {mode} identities; {len(aliases)} redirects validated.", flush=True)
    old = catalog(root)
    unsupported = {relative: content(read_alias(safe_path(root, relative), root))
                   for relative in old if not lod_supported(safe_path(root, relative), root)}
    if set(old) - aliases.keys() - native_attachments.keys() - unsupported.keys():
        raise ValueError("Existing redirects would be orphaned; explicitly expand before changing canonical sources")
    for relative, data in {**native_attachments, **unsupported}.items():
        path = safe_path(root, relative)
        if relative in old:
            path.write_bytes(data)
            path.with_suffix(".lod").unlink()
    for relative, record in aliases.items():
        path = safe_path(root, relative)
        path.with_suffix(".lod").write_bytes((Path(record["canonical"]).stem.lower() + "\n").encode("ascii"))
    manifest = {"version": 1, "aliases": dict(sorted(aliases.items())), "native_overrides": overrides}
    (root / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
    for relative in aliases:
        safe_path(root, relative).unlink(missing_ok=True)
    print(f"Published {len(aliases)} checked-in redirects; replaced {sum(r['source_bytes'] for r in aliases.values()):,} MDL bytes.")


def expand(root=ROOT):
    root = Path(root)
    errors = check(root)
    if errors:
        raise ValueError("\n".join(errors[:20]))
    aliases = catalog(root)
    for relative in aliases:
        path = safe_path(root, relative)
        path.write_bytes(read_alias(path, root))
        path.with_suffix(".lod").unlink()
    (root / MANIFEST).write_text('{"version": 1, "aliases": {}}\n')
    print(f"Expanded {len(aliases)} original model identities for explicit authoring.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--expand", action="store_true")
    parser.add_argument("--game-data", type=Path, help="NWN data directory; --apply keeps base-game overrides as MDLs")
    args = parser.parse_args()
    if args.apply:
        if args.game_data is None:
            parser.error("--apply requires --game-data")
        from GenerateTintMapAssets import read_stock_key_models
        apply(stock_models=read_stock_key_models(args.game_data).keys())
    elif args.expand:
        expand()
    else:
        errors = check()
        if errors:
            raise SystemExit("\n".join(errors[:20]))
        print("All checked-in model redirects preserve their exact original geometry and bindings.")
