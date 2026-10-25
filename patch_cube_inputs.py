#!/usr/bin/env python3
"""Patch the non-wave button-release branch so it also uses the shared tick delay."""
from __future__ import annotations
import argparse
from pathlib import Path
import zipfile

DLL_NAME = "ai.predictivebuffer.dll"
EXPECTED_DLL_SIZE = 8_865_792
BRANCH_OFFSET = 0x768AA
OLD = bytes.fromhex("75 2e")  # jne: skip delay path when the special mode condition is false
NEW = bytes.fromhex("eb 2e")  # jmp: route both press and release through the delay path


def patch_dll(data: bytes) -> bytes:
    if len(data) != EXPECTED_DLL_SIZE:
        raise ValueError(f"Unexpected DLL size: {len(data)}")
    current = data[BRANCH_OFFSET:BRANCH_OFFSET + 2]
    if current == NEW:
        raise ValueError("This DLL is already patched")
    if current != OLD:
        raise ValueError(
            f"Unexpected instruction at 0x{BRANCH_OFFSET:x}: {current.hex(' ')}"
        )
    patched = bytearray(data)
    patched[BRANCH_OFFSET:BRANCH_OFFSET + 2] = NEW
    result = bytes(patched)
    changed = [i for i, (a, b) in enumerate(zip(data, result)) if a != b]
    if changed != [BRANCH_OFFSET]:
        raise AssertionError(f"Unexpected changed offsets: {changed}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="Existing all-vehicles .dll or .geode")
    parser.add_argument("output", help="Output .dll or .geode")
    args = parser.parse_args()
    src, dst = Path(args.input), Path(args.output)

    if src.suffix.lower() == ".geode":
        with zipfile.ZipFile(src, "r") as zin:
            names = zin.namelist()
            if DLL_NAME not in names or "mod.json" not in names:
                raise ValueError("Input is not the expected Geode package")
            entries = {name: zin.read(name) for name in names}
        entries[DLL_NAME] = patch_dll(entries[DLL_NAME])
        with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zout:
            for name in names:
                zout.writestr(name, entries[name])
        with zipfile.ZipFile(dst, "r") as check:
            if check.testzip() is not None or check.namelist() != names:
                raise RuntimeError("Output Geode validation failed")
    else:
        dst.write_bytes(patch_dll(src.read_bytes()))

    print(f"Patched one branch at 0x{BRANCH_OFFSET:x}: {dst}")


if __name__ == "__main__":
    main()
