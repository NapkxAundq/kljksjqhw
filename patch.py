#!/usr/bin/env python3
"""Patch the supplied Predictive Input Buffer .geode without rebuilding its DLL."""
from __future__ import annotations
import argparse
import pathlib
import zipfile

DLL_NAME = "ai.predictivebuffer.dll"
CAVE_OFF = 0x7266C
CAVE_VA = 0x180001000 + CAVE_OFF - 0x400
ENTRY_OFF = 0x77E80
ENTRY_VA = 0x180001000 + ENTRY_OFF - 0x400
CONTINUE_VA = 0x180078B30
LOG_CALL_VA = 0x1800123AF
CAVE_SIZE = 804

MESSAGES = [
    "[InputBuffer] F9 Cycle -> STAGE 1: 2 Ticks All | 2 Ticks Wave (0.5f @ 60fps)",
    "[InputBuffer] F9 Cycle -> STAGE 2: 4 Ticks All | 4 Ticks Wave (1.0f @ 60fps)",
    "[InputBuffer] F9 Cycle -> STAGE 3: 6 Ticks All | 6 Ticks Wave (1.5f @ 60fps)",
    "[InputBuffer] F9 Cycle -> STAGE 4: 8 Ticks All | 8 Ticks Wave (2.0f @ 60fps)",
    "[InputBuffer] F9 Cycle -> STAGE 5: 16 Ticks All | 16 Ticks Wave (4.0f @ 60fps)",
    "[InputBuffer] F9 Cycle -> STAGE 6: 24 Ticks All | 24 Ticks Wave (6.0f @ 60fps)",
    "[InputBuffer] F9 Cycle -> STAGE 7: OFF (100%% Raw Input)",
]


class Assembler:
    def __init__(self, base_va: int):
        self.base_va = base_va
        self.code = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str, int]] = []

    def label(self, name: str) -> None:
        if name in self.labels:
            raise ValueError(f"Duplicate assembler label: {name}")
        self.labels[name] = len(self.code)

    def emit(self, b: bytes) -> None:
        self.code.extend(b)

    def rel32(self, opcode: bytes, target: str) -> None:
        self.emit(opcode)
        disp_pos = len(self.code)
        self.emit(b"\x00\x00\x00\x00")
        self.fixups.append((disp_pos, target, disp_pos + 4))

    def lea_rcx(self, target: str) -> None:
        self.rel32(bytes.fromhex("48 8d 0d"), target)

    def jmp(self, target: str) -> None:
        self.rel32(b"\xe9", target)

    def jcc(self, condition: int, target: str) -> None:
        self.rel32(b"\x0f" + bytes([condition]), target)

    def call_abs(self, target_va: int) -> None:
        self.emit(b"\xe8")
        disp_pos = len(self.code)
        next_va = self.base_va + disp_pos + 4
        self.emit((target_va - next_va).to_bytes(4, "little", signed=True))


def build_stage_routine() -> bytes:
    a = Assembler(CAVE_VA)
    # Six delay stages plus OFF, with both all-mode and wave delays set equally.
    a.emit(bytes.fromhex("8b 4b 18 ff c1 83 f9 07"))  # mov ecx,[rbx+18]; inc ecx; cmp ecx,7
    a.jcc(0x82, "state_valid")                       # jb state_valid
    a.emit(bytes.fromhex("33 c9"))                    # xor ecx,ecx
    a.label("state_valid")
    a.emit(bytes.fromhex("89 4b 18"))                  # mov [rbx+18],ecx

    for stage in range(6):
        a.emit(bytes.fromhex("83 f9") + bytes([stage]))
        a.jcc(0x84, f"stage{stage}")
    a.jmp("off")

    for stage, delay in enumerate([2, 4, 6, 8, 16, 24]):
        a.label(f"stage{stage}")
        a.emit(bytes.fromhex("c7 43 20") + delay.to_bytes(4, "little"))
        a.emit(bytes.fromhex("c7 43 24") + delay.to_bytes(4, "little"))
        a.lea_rcx(f"msg{stage + 1}")
        a.jmp("active")

    a.label("active")
    a.emit(bytes.fromhex("c6 43 1c 01"))               # active flag = true
    a.jmp("log_and_continue")

    a.label("off")
    a.emit(bytes.fromhex("33 c0 c6 43 1c 00 48 89 43 20"))  # active=false; both delays=0
    a.lea_rcx("msg7")

    a.label("log_and_continue")
    a.call_abs(LOG_CALL_VA)
    # A direct jump back to the original handler's continuation.
    a.emit(b"\xe9")
    disp_pos = len(a.code)
    next_va = CAVE_VA + disp_pos + 4
    a.emit((CONTINUE_VA - next_va).to_bytes(4, "little", signed=True))

    data_labels: dict[str, int] = {}
    for i, message in enumerate(MESSAGES, 1):
        data_labels[f"msg{i}"] = len(a.code)
        a.emit(message.encode("ascii") + b"\x00")

    for disp_pos, target, next_offset in a.fixups:
        if target not in a.labels and target not in data_labels:
            raise ValueError(f"Undefined assembler label: {target}")
        target_offset = a.labels.get(target, data_labels.get(target))
        disp = target_offset - next_offset
        a.code[disp_pos:disp_pos + 4] = disp.to_bytes(4, "little", signed=True)

    if len(a.code) > CAVE_SIZE:
        raise ValueError(f"Code/data cave overflow: {len(a.code)} > {CAVE_SIZE}")
    return bytes(a.code)


def replace_string_at(data: bytearray, offset: int, new_text: str, label: str) -> None:
    raw = new_text.encode("ascii")
    if offset + len(raw) + 2 > len(data):
        raise ValueError(f"String location out of bounds: {label}")
    data[offset:offset + len(raw) + 2] = raw + b"\n\x00"


def patch_dll(dll: bytes) -> bytes:
    data = bytearray(dll)

    # F6 -> F9; already patched F9 input is accepted too.
    hotkey_offset = 0x77E63
    hotkey = bytes(data[hotkey_offset:hotkey_offset + 5])
    if hotkey == bytes.fromhex("b9 75 00 00 00"):
        data[hotkey_offset:hotkey_offset + 5] = bytes.fromhex("b9 78 00 00 00")
    elif hotkey != bytes.fromhex("b9 78 00 00 00"):
        raise ValueError(f"Unexpected hotkey bytes: {hotkey.hex(' ')}")

    # Give the existing buffer hooks an earlier priority than VeryEarly hooks.
    priority = bytes.fromhex("c0 bd f0 ff")
    earlier = bytes.fromhex("bf bd f0 ff")
    text_end = 0x400 + 0x62B8C5
    matches = []
    pos = 0x400
    while True:
        pos = data.find(priority, pos, text_end)
        if pos < 0:
            break
        matches.append(pos)
        pos += 4
    if len(matches) == 26:
        for pos in matches:
            data[pos:pos + 4] = earlier
    elif matches or data.count(earlier) < 26:
        raise ValueError(f"Unexpected hook-priority pattern count: old={len(matches)}, new={data.count(earlier)}")

    # Replace the original state switch with a trampoline in unused INT3 padding.
    entry_prefix = bytes.fromhex("8b 4b 18")
    entry = bytes(data[ENTRY_OFF:ENTRY_OFF + 5])
    already_patched = False
    if entry[0] == 0xE9:
        rel = int.from_bytes(entry[1:5], "little", signed=True)
        already_patched = ENTRY_VA + 5 + rel == CAVE_VA and data[CAVE_OFF:CAVE_OFF + 5] == bytes.fromhex("8b 4b 18 ff c1")
    if not already_patched:
        if data[ENTRY_OFF:ENTRY_OFF + 3] != entry_prefix:
            raise ValueError(f"Unexpected stage-handler bytes at 0x{ENTRY_OFF:x}: {entry.hex(' ')}")
        if data[CAVE_OFF:CAVE_OFF + CAVE_SIZE] != b"\xcc" * CAVE_SIZE:
            raise ValueError("Expected unused INT3 padding at selected code cave")
        routine = build_stage_routine()
        disp = CAVE_VA - (ENTRY_VA + 5)
        data[ENTRY_OFF:ENTRY_OFF + 5] = b"\xe9" + disp.to_bytes(4, "little", signed=True)
        data[ENTRY_OFF + 5:0x77F30] = b"\x90" * (0x77F30 - (ENTRY_OFF + 5))
        data[CAVE_OFF:CAVE_OFF + len(routine)] = routine

    # Refresh readable status strings; runtime stage messages are embedded in the cave.
    static_strings = [
        (0x631DA0, MESSAGES[0]),
        (0x631E00, MESSAGES[1]),
        (0x631E60, MESSAGES[2]),
        (0x631EC0, MESSAGES[3]),
        (0x631F28, MESSAGES[4]),
    ]
    for off, msg in static_strings:
        replace_string_at(data, off, msg, f"stage string at 0x{off:x}")
    startup = " [InputBuffer] Press F9: 2/2 -> 4/4 -> 6/6 -> 8/8 -> 16/16 -> 24/24 -> OFF"
    replace_string_at(data, 0x632140, startup, "startup cycle string")
    return bytes(data)


def patch_package(src: pathlib.Path, dst: pathlib.Path) -> None:
    with zipfile.ZipFile(src, "r") as zin:
        names = zin.namelist()
        if DLL_NAME not in names or "mod.json" not in names:
            raise ValueError("Input is not the expected Predictive Input Buffer .geode package")
        entries = {name: zin.read(name) for name in names}
    entries[DLL_NAME] = patch_dll(entries[DLL_NAME])
    with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zout:
        for name in names:
            zout.writestr(name, entries[name])
    with zipfile.ZipFile(dst, "r") as check:
        if check.testzip() is not None or check.namelist() != names:
            raise RuntimeError("Package integrity check failed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="Original or previously patched .geode package")
    parser.add_argument("output", help="Output .geode package")
    args = parser.parse_args()
    patch_package(pathlib.Path(args.input), pathlib.Path(args.output))
    print(f"Wrote {args.output}; the original {DLL_NAME} binary was patched in place.")

if __name__ == "__main__":
    main()
