#!/usr/bin/env python3
"""Make the existing Predictive Input Buffer use a single delay field for all vehicles."""
from __future__ import annotations
import argparse
from pathlib import Path
import zipfile

DLL_NAME = "ai.predictivebuffer.dll"
EXPECTED_DLL_SIZE = 8_865_792
DELAY_READ_OFFSET = 0x76921
OLD_DELAY_READ = bytes.fromhex("8b 74 83 20")
NEW_DELAY_READ = bytes.fromhex("8b 73 24 90")
FORMAT_OFFSETS = (0x77905, 0x77CA5)
FORMAT_CAPACITY = 0xBB
FORMAT_STRING = (
    "[InputBuffer] *** SAVED +%d TICKS (%.2f frames @ 60fps) %s! "
    "(Frame0: %d -> Delayed: %d) "
    "[cube/ship/ball/UFO/robot/spider/swing | Limit: %d] ***\n"
).encode("ascii")


def patch_dll(dll: bytes) -> bytes:
    if len(dll) != EXPECTED_DLL_SIZE:
        raise ValueError(f"Unexpected DLL size: {len(dll)}")
    data = bytearray(dll)

    current = bytes(data[DELAY_READ_OFFSET:DELAY_READ_OFFSET + 4])
    if current == OLD_DELAY_READ:
        data[DELAY_READ_OFFSET:DELAY_READ_OFFSET + 4] = NEW_DELAY_READ
    elif current != NEW_DELAY_READ:
        raise ValueError(
            "Unexpected delay-selector instruction at 0x76921: "
            f"{current.hex(' ')}; refusing to patch a different binary"
        )

    if len(FORMAT_STRING) + 1 > FORMAT_CAPACITY:
        raise ValueError("Console format string exceeds reserved area")
    blob = FORMAT_STRING + b"\x00"
    for off in FORMAT_OFFSETS:
        existing = bytes(data[off:off + FORMAT_CAPACITY])
        if existing[:len(blob)] != blob:
            old_text = existing.split(b"\x00", 1)[0]
            if (
                b"[InputBuffer] *** SAVED +%d TICKS" not in old_text
                and existing != b"\xCC" * FORMAT_CAPACITY
            ):
                raise ValueError(f"Unexpected console format area at 0x{off:x}")
        data[off:off + FORMAT_CAPACITY] = blob + b"\xCC" * (FORMAT_CAPACITY - len(blob))

    return bytes(data)


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
    parser.add_argument("input", help="Existing F9 two-tick-step DLL or .geode")
    parser.add_argument("output", help="Output DLL or .geode")
    args = parser.parse_args()
    patch_file(Path(args.input), Path(args.output))
    print(f"Patched shared delay selection and console label: {args.output}")


if __name__ == "__main__":
    main()
