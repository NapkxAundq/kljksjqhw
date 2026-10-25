#!/usr/bin/env python3
"""Raise the ground-mode prediction horizon from 48 to 90 ticks, preserving the rest of the DLL."""
from __future__ import annotations
import argparse
from pathlib import Path
import zipfile

DLL_NAME = "ai.predictivebuffer.dll"
EXPECTED_SIZE = 8_865_792
HORIZON_OFFSET = 0x7690B
OLD_INSTRUCTION = bytes.fromhex("41 be 30 00 00 00")  # mov r14d, 48
NEW_INSTRUCTION = bytes.fromhex("41 be 5a 00 00 00")  # mov r14d, 90
CUBE_BRANCH_OFFSET = 0x768AA
EXPECTED_CUBE_BRANCH = bytes.fromhex("eb 2e")


def patch_dll(dll: bytes) -> bytes:
    if len(dll) != EXPECTED_SIZE:
        raise ValueError(f"Unexpected DLL size: {len(dll)}")
    if dll[CUBE_BRANCH_OFFSET:CUBE_BRANCH_OFFSET+2] != EXPECTED_CUBE_BRANCH:
        raise ValueError("Expected the all-vehicle cube-input branch to be present")
    current = dll[HORIZON_OFFSET:HORIZON_OFFSET+len(OLD_INSTRUCTION)]
    if current == NEW_INSTRUCTION:
        raise ValueError("This DLL already has a 90-tick ground horizon")
    if current != OLD_INSTRUCTION:
        raise ValueError(
            f"Unexpected ground-horizon instruction at 0x{HORIZON_OFFSET:x}: "
            f"{current.hex(' ')}"
        )
    result = bytearray(dll)
    result[HORIZON_OFFSET:HORIZON_OFFSET+len(NEW_INSTRUCTION)] = NEW_INSTRUCTION
    changed = [i for i, (a, b) in enumerate(zip(dll, result)) if a != b]
    if changed != [HORIZON_OFFSET + 2]:
        raise AssertionError(f"Expected exactly one changed byte, got {changed}")
    return bytes(result)


def patch_file(src: Path, dst: Path) -> None:
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
                raise RuntimeError("Output Geode integrity check failed")
    else:
        dst.write_bytes(patch_dll(src.read_bytes()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="Existing all-vehicle .dll or .geode")
    parser.add_argument("output", help="Patched .dll or .geode")
    args = parser.parse_args()
    patch_file(Path(args.input), Path(args.output))
    print(f"Updated ground prediction horizon to 90 ticks: {args.output}")


if __name__ == "__main__":
    main()
