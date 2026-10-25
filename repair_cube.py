#!/usr/bin/env python3
"""Undo the broad hook-priority tweak in the F9 two-tick-step DLL/package."""
from __future__ import annotations
import argparse
from pathlib import Path
import zipfile

DLL_NAME = "ai.predictivebuffer.dll"
PATCHED_PRIORITY = bytes.fromhex("bf bd f0 ff")  # -1,000,001
ORIGINAL_PRIORITY = bytes.fromhex("c0 bd f0 ff")  # -1,000,000
HOTKEY_OFFSET = 0x77E63

def repair_dll(data: bytes) -> bytes:
    if len(data) != 8_865_792:
        raise ValueError(f"Unexpected DLL size: {len(data)}")
    hotkey = data[HOTKEY_OFFSET:HOTKEY_OFFSET+5]
    if hotkey != bytes.fromhex("b9 78 00 00 00"):
        raise ValueError("DLL does not match the expected F9 build")
    count = data.count(PATCHED_PRIORITY)
    if count != 26:
        raise ValueError(f"Expected 26 modified hook-priority constants; found {count}")
    fixed = data.replace(PATCHED_PRIORITY, ORIGINAL_PRIORITY)
    if fixed.count(PATCHED_PRIORITY) != 0:
        raise ValueError("Could not restore every modified priority constant")
    return fixed

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="F9 step-2 DLL or .geode package")
    parser.add_argument("output", help="Repaired DLL or .geode output")
    args = parser.parse_args()
    src, dst = Path(args.input), Path(args.output)
    if src.suffix.lower() == ".geode":
        with zipfile.ZipFile(src, "r") as zin:
            names = zin.namelist()
            if DLL_NAME not in names or "mod.json" not in names:
                raise ValueError("Not the expected Geode package")
            entries = {name: zin.read(name) for name in names}
        entries[DLL_NAME] = repair_dll(entries[DLL_NAME])
        with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zout:
            for name in names:
                zout.writestr(name, entries[name])
        with zipfile.ZipFile(dst, "r") as check:
            if check.testzip() is not None or check.namelist() != names:
                raise RuntimeError("Output .geode validation failed")
    else:
        dst.write_bytes(repair_dll(src.read_bytes()))
    print(f"Restored original hook priorities in {dst}")

if __name__ == "__main__":
    main()
