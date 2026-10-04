"""Decompile large dummy animation banks in bounded, lossless validation chunks."""
from concurrent.futures import ThreadPoolExecutor

import CompileModels as mdl
import RobeAnimationBanks as banks
import RobeAnimations as anim


def decompile(compiler, stage, name, target_bytes=banks.TARGET_BYTES):
    data = (stage / "binary" / f"{name}.mdl").read_bytes()
    original = banks.CompiledBridge(data)
    parts = banks.split(data, target_bytes)
    banks.validate_split(data, list(parts.values()))
    jobs = []
    for index, part in enumerate(parts.values()):
        parsed = banks.CompiledBridge(part)
        # Validation chunks are independent models with the original name and
        # parent. Renaming and relocation never change controllers or events.
        subset = original.pack(name, original.parent, parsed.clips)
        folder = stage / "validation-chunks" / name / str(index)
        (folder / "decompiled").mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.mdl"
        path.write_bytes(subset)
        jobs.append((index, path, folder / "decompiled"))
    def export(job):
        index, path, destination = job
        mdl.run_compiler(compiler, stage, ["-de", str(path), str(destination) + "/"],
                         f"{name}.chunk{index}.log")
        return (destination / f"{name}.mdl").read_text(encoding="latin1")
    with ThreadPoolExecutor(max_workers=4) as executor:
        texts = list(executor.map(export, jobs))
    prefix, animations = None, []
    for text in texts:
        clips = list(anim.ANIMATION.finditer(text))
        if not clips:
            raise ValueError(f"{name}: validation chunk has no animations")
        geometry = text[:clips[0].start()]
        if prefix is None:
            prefix = geometry
        elif geometry != prefix:
            raise ValueError(f"{name}: chunk geometry differs")
        animations.extend(match[0] for match in clips)
    result = prefix + "\n".join(animations) + f"\ndonemodel {name}\n"
    names = [match[1].lower() for match in anim.ANIMATION.finditer(result)]
    if names != original.clip_names:
        raise ValueError(f"{name}: chunk export changed the animation inventory")
    (stage / "decompiled" / f"{name}.mdl").write_text(result, encoding="latin1")
    return result
