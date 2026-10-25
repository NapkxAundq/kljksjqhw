#!/usr/bin/env python3
"""Fix console line breaks and log whether the selected delay is Wave or another vehicle."""
from __future__ import annotations
import argparse
import pathlib
import zipfile

DLL_NAME = "ai.predictivebuffer.dll"
IMAGE_BASE = 0x180000000
CAVE_OFF = 0x7266C
CAVE_VA = IMAGE_BASE + 0x1000 + CAVE_OFF - 0x400
ENTRY_OFF = 0x77E80
ENTRY_VA = IMAGE_BASE + 0x1000 + ENTRY_OFF - 0x400
CONTINUE_VA = 0x180078B30
LOG_CALL_VA = 0x1800123AF
CAVE_SIZE = 804

MODE_SAVE_OFF = 0x76915
MODE_LOG_OFF = 0x76A01
MODE_CODE_CAVE_OFF = 0x76A68
MODE_CODE_CAVE_SIZE = 0xF8
WAVE_FMT_OFF = 0x77905
OTHER_FMT_OFF = 0x77CA5
MODE_FMT_CAVE_SIZE = 0xBB

MESSAGES = [
    "[InputBuffer] F9 Cycle -> STAGE 1: 2 Ticks All | 2 Ticks Wave (0.5f @ 60fps)",
    "[InputBuffer] F9 Cycle -> STAGE 2: 4 Ticks All | 4 Ticks Wave (1.0f @ 60fps)",
    "[InputBuffer] F9 Cycle -> STAGE 3: 6 Ticks All | 6 Ticks Wave (1.5f @ 60fps)",
    "[InputBuffer] F9 Cycle -> STAGE 4: 8 Ticks All | 8 Ticks Wave (2.0f @ 60fps)",
    "[InputBuffer] F9 Cycle -> STAGE 5: 16 Ticks All | 16 Ticks Wave (4.0f @ 60fps)",
    "[InputBuffer] F9 Cycle -> STAGE 6: 24 Ticks All | 24 Ticks Wave (6.0f @ 60fps)",
    "[InputBuffer] F9 Cycle -> STAGE 7: OFF (100%% Raw Input)",
]
MODE_WAVE_FORMAT = (
    "[InputBuffer] *** SAVED +%d TICKS (%.2f frames @ 60fps) %s! "
    "(Frame0: %d -> Delayed: %d) [Mode: WAVE | Limit: %d] ***\n"
)
MODE_OTHER_FORMAT = (
    "[InputBuffer] *** SAVED +%d TICKS (%.2f frames @ 60fps) %s! "
    "(Frame0: %d -> Delayed: %d) [Mode: OTHER VEHICLE "
    "(cube/ship/ball/UFO/robot/spider/swing) | Limit: %d] ***\n"
)

def raw_va(offset: int) -> int:
    return IMAGE_BASE + offset + 0xC00

class Asm:
    def __init__(self, base_va: int):
        self.base_va = base_va
        self.code = bytearray()
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str, int]] = []

    def label(self, name: str) -> None:
        self.labels[name] = len(self.code)

    def emit(self, code: bytes) -> None:
        self.code.extend(code)

    def branch(self, op: bytes, target: str) -> None:
        self.emit(op)
        pos = len(self.code)
        self.emit(b"\0\0\0\0")
        self.fixups.append((pos, target, pos + 4))

    def lea_rcx(self, target: str) -> None:
        self.branch(bytes.fromhex("48 8d 0d"), target)

    def jmp(self, target: str) -> None:
        self.branch(b"\xe9", target)

    def jcc(self, condition: int, target: str) -> None:
        self.branch(bytes([0x0f, condition]), target)

    def call_va(self, target: int) -> None:
        self.emit(b"\xe8")
        pos = len(self.code)
        next_va = self.base_va + pos + 4
        self.emit((target - next_va).to_bytes(4, "little", signed=True))


def build_stage_routine() -> bytes:
    a = Asm(CAVE_VA)
    a.emit(bytes.fromhex("8b 4b 18 ff c1 83 f9 07"))
    a.jcc(0x82, "state_valid")
    a.emit(bytes.fromhex("33 c9"))
    a.label("state_valid")
    a.emit(bytes.fromhex("89 4b 18"))
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
    a.emit(bytes.fromhex("c6 43 1c 01"))
    a.jmp("log_and_continue")
    a.label("off")
    a.emit(bytes.fromhex("33 c0 c6 43 1c 00 48 89 43 20"))
    a.lea_rcx("msg7")
    a.label("log_and_continue")
    a.call_va(LOG_CALL_VA)
    a.jmp("continue")
    a.label("continue")

    data_labels: dict[str, int] = {}
    for i, msg in enumerate(MESSAGES, 1):
        data_labels[f"msg{i}"] = len(a.code)
        a.emit(msg.encode("ascii") + b"\n\0")

    for pos, target, next_pos in a.fixups:
        target_pos = a.labels.get(target, data_labels.get(target))
        if target_pos is None:
            raise ValueError(f"Missing assembler label: {target}")
        a.code[pos:pos+4] = (target_pos-next_pos).to_bytes(4, "little", signed=True)

    jmp_start = a.labels["log_and_continue"] + 5
    disp_pos = jmp_start + 1
    next_pos = disp_pos + 4
    disp = (CONTINUE_VA - CAVE_VA) - next_pos
    a.code[disp_pos:disp_pos+4] = disp.to_bytes(4, "little", signed=True)
    if len(a.code) > CAVE_SIZE:
        raise ValueError(f"Stage code cave overflow: {len(a.code)} > {CAVE_SIZE}")
    return bytes(a.code)


def rel_jmp(source_off: int, next_off: int, target_off: int) -> bytes:
    return b"\xe9" + (raw_va(target_off) - raw_va(next_off)).to_bytes(4, "little", signed=True)


def build_mode_trampolines() -> bytes:
    b = bytearray()
    # Save DL (0=other vehicle, 1=wave), retain EAX=DL and restore R9.
    b.extend(bytes.fromhex("88 54 24 53 0f b6 c2 4c 8b cd"))
    b.extend(rel_jmp(MODE_CODE_CAVE_OFF + len(b),
                     MODE_CODE_CAVE_OFF + len(b) + 5, MODE_SAVE_OFF + 6))
    selector_off = 0x76A80
    while MODE_CODE_CAVE_OFF + len(b) < selector_off:
        b.append(0xCC)
    if MODE_CODE_CAVE_OFF + len(b) != selector_off:
        raise ValueError("Mode trampolines overlap")
    b.extend(bytes.fromhex("80 7c 24 53 00 74 0c"))
    for fmt_off in (WAVE_FMT_OFF,):
        lea_off = MODE_CODE_CAVE_OFF + len(b)
        disp = raw_va(fmt_off) - raw_va(lea_off + 7)
        b.extend(bytes.fromhex("48 8d 0d") + disp.to_bytes(4, "little", signed=True))
        jmp_off = MODE_CODE_CAVE_OFF + len(b)
        b.extend(rel_jmp(jmp_off, jmp_off + 5, MODE_LOG_OFF + 7))
    lea_off = MODE_CODE_CAVE_OFF + len(b)
    disp = raw_va(OTHER_FMT_OFF) - raw_va(lea_off + 7)
    b.extend(bytes.fromhex("48 8d 0d") + disp.to_bytes(4, "little", signed=True))
    jmp_off = MODE_CODE_CAVE_OFF + len(b)
    b.extend(rel_jmp(jmp_off, jmp_off + 5, MODE_LOG_OFF + 7))
    if len(b) > MODE_CODE_CAVE_SIZE:
        raise ValueError("Mode code cave overflow")
    return bytes(b)


def patch_mode_logging(d: bytearray) -> None:
    for off, fmt, name in (
        (WAVE_FMT_OFF, MODE_WAVE_FORMAT, "Wave"),
        (OTHER_FMT_OFF, MODE_OTHER_FORMAT, "Other vehicle"),
    ):
        blob = fmt.encode("ascii") + b"\0"
        if len(blob) > MODE_FMT_CAVE_SIZE:
            raise ValueError(f"{name} format string is too large")
        old = bytes(d[off:off + MODE_FMT_CAVE_SIZE])
        if old != b"\xCC" * MODE_FMT_CAVE_SIZE and not old.startswith(blob):
            raise ValueError(f"Unexpected reserved area for {name} format")
        d[off:off + MODE_FMT_CAVE_SIZE] = blob + b"\xCC" * (MODE_FMT_CAVE_SIZE - len(blob))

    save_expected = rel_jmp(MODE_SAVE_OFF, MODE_SAVE_OFF + 5, MODE_CODE_CAVE_OFF)
    save = bytes(d[MODE_SAVE_OFF:MODE_SAVE_OFF+5])
    if save == bytes.fromhex("0f b6 c2 4c 8b"):
        d[MODE_SAVE_OFF:MODE_SAVE_OFF+5] = save_expected
    elif save != save_expected:
        raise ValueError("Unexpected original mode selector bytes")

    log_expected = rel_jmp(MODE_LOG_OFF, MODE_LOG_OFF + 5, 0x76A80) + b"\x90\x90"
    logsite = bytes(d[MODE_LOG_OFF:MODE_LOG_OFF+7])
    if logsite[:3] == bytes.fromhex("48 8d 0d"):
        d[MODE_LOG_OFF:MODE_LOG_OFF+7] = log_expected
    elif logsite != log_expected:
        raise ValueError("Unexpected original log-string selection bytes")

    code = build_mode_trampolines()
    d[MODE_CODE_CAVE_OFF:MODE_CODE_CAVE_OFF+MODE_CODE_CAVE_SIZE] = (
        code + b"\xCC" * (MODE_CODE_CAVE_SIZE-len(code))
    )


def replace_string(d: bytearray, off: int, text: str) -> None:
    raw = text.encode("ascii")
    d[off:off+len(raw)+2] = raw + b"\n\0"


def patch_dll(dll: bytes) -> bytes:
    d = bytearray(dll)
    # This follow-up patch expects the F9 / six-stage version produced by patch.py.
    entry = bytes(d[ENTRY_OFF:ENTRY_OFF+5])
    if entry[0] != 0xE9:
        raise ValueError("This is not the already-patched F9 six-stage DLL")
    rel = int.from_bytes(entry[1:5], "little", signed=True)
    if ENTRY_VA + 5 + rel != CAVE_VA:
        raise ValueError("Stage-handler trampoline points to an unexpected location")

    routine = build_stage_routine()
    d[CAVE_OFF:CAVE_OFF+CAVE_SIZE] = routine + b"\xCC" * (CAVE_SIZE-len(routine))
    patch_mode_logging(d)

    for off, msg in (
        (0x631DA0, MESSAGES[0]),
        (0x631E00, MESSAGES[1]),
        (0x631E60, MESSAGES[2]),
        (0x631EC0, MESSAGES[3]),
        (0x631F28, MESSAGES[4]),
    ):
        replace_string(d, off, msg)
    replace_string(d, 0x632140,
                   " [InputBuffer] Press F9: 2/2 -> 4/4 -> 6/6 -> 8/8 -> 16/16 -> 24/24 -> OFF")
    return bytes(d)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="Existing F9 six-stage .geode")
    parser.add_argument("output", help="Console-fixed .geode")
    args = parser.parse_args()
    with zipfile.ZipFile(args.input) as zin:
        names = zin.namelist()
        if DLL_NAME not in names or "mod.json" not in names:
            raise ValueError("Input is not the expected .geode package")
        files = {name: zin.read(name) for name in names}
    files[DLL_NAME] = patch_dll(files[DLL_NAME])
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zout:
        for name in names:
            zout.writestr(name, files[name])
    with zipfile.ZipFile(args.output) as check:
        if check.testzip() is not None or check.namelist() != names:
            raise RuntimeError("Output .geode integrity check failed")
    print(f"Created {args.output}")

if __name__ == "__main__":
    main()
