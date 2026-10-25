#!/usr/bin/env python3
from __future__ import annotations
import argparse
import pathlib
import zipfile

DLL_NAME = "ai.predictivebuffer.dll"

def patch_dll(dll: bytes) -> bytes:
    data = bytearray(dll)

    def write_at(offset: int, old: bytes, new: bytes, label: str) -> None:
        actual = bytes(data[offset:offset + len(old)])
        if actual != old or len(old) != len(new):
            raise ValueError(f"Base DLL mismatch at {label} (offset 0x{offset:x})")
        data[offset:offset + len(new)] = new

    # Win32 VK_F6 -> VK_F9.
    write_at(0x77E63, bytes.fromhex("b9 75 00 00 00"),
             bytes.fromhex("b9 78 00 00 00"), "hotkey")
    # Maximum stage: 24 ticks for every mode, including wave.
    write_at(0x77ECD, bytes.fromhex("c7 43 20 10 00 00 00"),
             bytes.fromhex("c7 43 20 18 00 00 00"), "all-mode delay")
    write_at(0x77EDB, bytes.fromhex("c7 43 24 08 00 00 00"),
             bytes.fromhex("c7 43 24 18 00 00 00"), "wave delay")

    # Put the existing VeryEarly hooks just ahead of hooks at that same priority,
    # so reinjected delayed inputs can flow downstream to CBF or SIP individually.
    old_priority = bytes.fromhex("c0 bd f0 ff")   # -1,000,000
    new_priority = bytes.fromhex("bf bd f0 ff")   # -1,000,001
    text_end = 0x400 + 0x62B8C5
    count, pos = 0, 0x400
    while True:
        pos = data.find(old_priority, pos, text_end)
        if pos < 0:
            break
        data[pos:pos + 4] = new_priority
        count += 1
        pos += 4
    if count != 26:
        raise ValueError(f"Expected 26 hook-priority constants, found {count}")

    strings = [
        ("[InputBuffer] F6 Cycle -> STAGE 1: 2 Ticks All | 2 Ticks Wave (0.5f @ 60fps)",
         "[InputBuffer] F9 Cycle -> STAGE 1: 2 Ticks All | 2 Ticks Wave (0.5f @ 60fps)"),
        ("[InputBuffer] F6 Cycle -> STAGE 2: 4 Ticks All | 4 Ticks Wave (1.0f @ 60fps)",
         "[InputBuffer] F9 Cycle -> STAGE 2: 4 Ticks All | 4 Ticks Wave (1.0f @ 60fps)"),
        ("[InputBuffer] F6 Cycle -> STAGE 3: 8 Ticks All | 8 Ticks Wave (2.0f @ 60fps)",
         "[InputBuffer] F9 Cycle -> STAGE 3: 8 Ticks All | 8 Ticks Wave (2.0f @ 60fps)"),
        ("[InputBuffer] F6 Cycle -> STAGE 4: 16 Ticks All | 8 Ticks Wave (4.0f / 2.0f @ 60fps)",
         "[InputBuffer] F9 Cycle -> STAGE 4: 24 Ticks All | 24 Ticks Wave (6.0f / 6.0f @ 60fps)"),
        ("[InputBuffer] F6 Cycle -> STAGE 5: OFF (100%% Raw Input)",
         "[InputBuffer] F9 Cycle -> STAGE 5: OFF (100%% Raw Input)"),
        (" [InputBuffer] Press F6 in-level to cycle: 2/2 -> 4/4 -> 8/8 -> 16/8 -> OFF",
         " [InputBuffer] Press F9 in-level to cycle: 2/2 -> 4/4 -> 8/8 -> 24/24 -> OFF"),
    ]
    for old_text, new_text in strings:
        old, new = old_text.encode(), new_text.encode()
        start = data.find(old)
        if start < 0 or data.find(old, start + 1) >= 0:
            raise ValueError(f"Expected unique console string: {old_text}")
        suffix_start = start + len(old)
        if data[suffix_start:suffix_start + 2] == b"\n\x00":
            suffix = b"\n\x00"
        elif data[suffix_start:suffix_start + 1] == b"\x00":
            suffix = b"\x00"
        else:
            raise ValueError(f"Unexpected console-string terminator: {old_text}")
        data[start:start + len(new)] = new
        data[start + len(new):start + len(new) + len(suffix)] = suffix
    return bytes(data)

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="Original .geode package")
    parser.add_argument("output", help="Output .geode package")
    args = parser.parse_args()
    with zipfile.ZipFile(args.input, "r") as zin:
        names = zin.namelist()
        if DLL_NAME not in names or "mod.json" not in names:
            raise ValueError("Unexpected .geode package")
        entries = {name: zin.read(name) for name in names}
    entries[DLL_NAME] = patch_dll(entries[DLL_NAME])
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zout:
        for name in names:
            zout.writestr(name, entries[name])
    with zipfile.ZipFile(args.output, "r") as check:
        if check.testzip() is not None or check.namelist() != names:
            raise RuntimeError("Output .geode validation failed")

if __name__ == "__main__":
    main()
