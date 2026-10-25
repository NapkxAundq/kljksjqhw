#!/usr/bin/env python3
"""Patch the existing Predictive Input Buffer DLL so every vehicle shares the tick delay."""
from __future__ import annotations
import argparse
from pathlib import Path
import zipfile

DLL_NAME = "ai.predictivebuffer.dll"
IMAGE_BASE = 0x180000000
TEXT_RAW = 0x400
TEXT_RVA = 0x1000
DATA_RAW = 0x7D2600
DATA_RVA = 0x7D4000
EXPECTED_DLL_SIZE = 8_865_792

INPUT_TRAMPOLINE_OFF = 0x76A68
INPUT_TRAMPOLINE_SIZE = 0xF8
LOG_FORMAT_SITE_OFF = 0x76A01
STAGE_ACTIVE_JMP_OFF = 0x5AEDD0
STAGE_OFF_LEA_OFF = 0x5AEDDF
ACTIVE_HELPER_OFF = 0x5AF1E8
OFF_HELPER_OFF = 0x5AF1F8
LOG_CONTINUE_OFF = 0x5AEDE6
OFF_MESSAGE_OFF = 0x5AF1AB
GLOBAL_DELAY_OFF = 0x7DC1C8

FORMAT_OFFSETS = (0x77905, 0x77CA5)
FORMAT_CAPACITY = 0xBB
FORMAT_STRING = (
    "[InputBuffer] *** SAVED +%d TICKS (%.2f frames @ 60fps) %s! "
    "(Frame0: %d -> Delayed: %d) "
    "[cube/ship/ball/UFO/robot/spider/swing | Limit: %d] ***\n"
).encode("ascii")


def text_va(raw_off: int) -> int:
    return IMAGE_BASE + TEXT_RVA + (raw_off - TEXT_RAW)


def data_va(raw_off: int) -> int:
    return IMAGE_BASE + DATA_RVA + (raw_off - DATA_RAW)


def rel32(target_va: int, next_va: int) -> bytes:
    return (target_va - next_va).to_bytes(4, "little", signed=True)


def emit_jmp(site_off: int, target_off: int) -> bytes:
    site_va = text_va(site_off)
    return b"\xE9" + rel32(text_va(target_off), site_va + 5)


def build_input_trampoline() -> bytes:
    code = bytearray()
    code += bytes.fromhex("88 54 24 53")  # Save input flag byte.
    code += bytes.fromhex("0f b6 c2")     # movzx eax, dl
    code += bytes.fromhex("4c 8b cd")     # mov r9, rbp
    code += bytes.fromhex("49 8b d2")     # mov rdx, r10
    code += bytes.fromhex("48 8b cb")     # mov rcx, rbx
    load_va = text_va(INPUT_TRAMPOLINE_OFF) + len(code)
    code += bytes.fromhex("8b 35") + rel32(data_va(GLOBAL_DELAY_OFF), load_va + 6)
    jmp_va = text_va(INPUT_TRAMPOLINE_OFF) + len(code)
    code += b"\xE9" + rel32(text_va(0x76925), jmp_va + 5)
    if len(code) > INPUT_TRAMPOLINE_SIZE:
        raise ValueError("Input trampoline exceeds its reserved cave")
    return bytes(code) + b"\xCC" * (INPUT_TRAMPOLINE_SIZE - len(code))


def build_active_helper() -> bytes:
    code = bytearray(bytes.fromhex("8b 43 20"))  # mov eax, [rbx+0x20]
    store_va = text_va(ACTIVE_HELPER_OFF) + len(code)
    code += bytes.fromhex("89 05") + rel32(data_va(GLOBAL_DELAY_OFF), store_va + 6)
    jmp_va = text_va(ACTIVE_HELPER_OFF) + len(code)
    code += b"\xE9" + rel32(text_va(LOG_CONTINUE_OFF), jmp_va + 5)
    return bytes(code)


def build_off_helper() -> bytes:
    code = bytearray()
    store_va = text_va(OFF_HELPER_OFF)
    # mov dword ptr [rip+GLOBAL_DELAY_OFF], 0
    code += bytes.fromhex("c7 05") + rel32(data_va(GLOBAL_DELAY_OFF), store_va + 10) + bytes(4)
    lea_va = text_va(OFF_HELPER_OFF) + len(code)
    code += bytes.fromhex("48 8d 0d") + rel32(text_va(OFF_MESSAGE_OFF), lea_va + 7)
    jmp_va = text_va(OFF_HELPER_OFF) + len(code)
    code += b"\xE9" + rel32(text_va(LOG_CONTINUE_OFF), jmp_va + 5)
    return bytes(code)


def patch_dll(dll: bytes) -> bytes:
    if len(dll) != EXPECTED_DLL_SIZE:
        raise ValueError(f"Unexpected DLL size: {len(dll)}")
    data = bytearray(dll)

    expected = {
        0x76921: bytes.fromhex("8b 74 83 20"),
        STAGE_ACTIVE_JMP_OFF: bytes.fromhex("e9 11 00 00 00"),
        STAGE_OFF_LEA_OFF: bytes.fromhex("48 8d 0d c5 03 00 00"),
        LOG_FORMAT_SITE_OFF: bytes.fromhex("e9 7a 00 00 00 90 90"),
        INPUT_TRAMPOLINE_OFF: bytes.fromhex("88 54 24 53 0f b6 c2 4c 8b cd e9 a4 fe ff ff"),
    }
    for offset, pattern in expected.items():
        if bytes(data[offset:offset + len(pattern)]) != pattern:
            raise ValueError(f"Unexpected base DLL bytes at 0x{offset:x}; refusing to patch")

    # Writable, unused tail of .data: the common delay starts at 2 ticks.
    if data[GLOBAL_DELAY_OFF:GLOBAL_DELAY_OFF + 4] != b"\x00" * 4:
        raise ValueError("Shared-delay data slot is not an unused zero-initialized word")
    data[GLOBAL_DELAY_OFF:GLOBAL_DELAY_OFF + 4] = (2).to_bytes(4, "little")

    for offset, code in ((ACTIVE_HELPER_OFF, build_active_helper()),
                         (OFF_HELPER_OFF, build_off_helper())):
        if data[offset:offset + len(code)] != b"\xCC" * len(code):
            raise ValueError(f"Expected unused code cave at 0x{offset:x}")
        data[offset:offset + len(code)] = code

    # Publishing the active delay and clearing it in the OFF stage keeps
    # keyboard-stage state and input-processing state synchronized.
    data[STAGE_ACTIVE_JMP_OFF:STAGE_ACTIVE_JMP_OFF + 5] = emit_jmp(
        STAGE_ACTIVE_JMP_OFF, ACTIVE_HELPER_OFF
    )
    data[STAGE_OFF_LEA_OFF:STAGE_OFF_LEA_OFF + 7] = (
        emit_jmp(STAGE_OFF_LEA_OFF, OFF_HELPER_OFF) + b"\x90\x90"
    )
    data[INPUT_TRAMPOLINE_OFF:INPUT_TRAMPOLINE_OFF + INPUT_TRAMPOLINE_SIZE] = (
        build_input_trampoline()
    )

    blob = FORMAT_STRING + b"\x00"
    if len(blob) > FORMAT_CAPACITY:
        raise ValueError("Console format string exceeds reserved area")
    for offset in FORMAT_OFFSETS:
        old = bytes(data[offset:offset + FORMAT_CAPACITY])
        if b"[InputBuffer] *** SAVED +%d TICKS" not in old:
            raise ValueError(f"Unexpected console format slot at 0x{offset:x}")
        data[offset:offset + FORMAT_CAPACITY] = (
            blob + b"\xCC" * (FORMAT_CAPACITY - len(blob))
        )

    # Use one static event label rather than selecting WAVE/OTHER VEHICLE.
    site_va = text_va(LOG_FORMAT_SITE_OFF)
    target_va = text_va(FORMAT_OFFSETS[0])
    data[LOG_FORMAT_SITE_OFF:LOG_FORMAT_SITE_OFF + 7] = (
        bytes.fromhex("48 8d 0d") + rel32(target_va, site_va + 7)
    )
    # Refresh stage messages in their existing slots. Do not move code or pointers.
    stage_slots = [
        (0x5AEDF0, 0x5AEE3E, 2, "0.5"),
        (0x5AEE3E, 0x5AEE8C, 4, "1.0"),
        (0x5AEE8C, 0x5AEEDA, 6, "1.5"),
        (0x5AEEDA, 0x5AEF28, 8, "2.0"),
        (0x5AEF28, 0x5AEF78, 10, "2.5"),
        (0x5AEF78, 0x5AEFC8, 12, "3.0"),
        (0x5AEFC8, 0x5AF018, 14, "3.5"),
        (0x5AF018, 0x5AF068, 16, "4.0"),
        (0x5AF068, 0x5AF0B8, 18, "4.5"),
        (0x5AF0B8, 0x5AF109, 20, "5.0"),
        (0x5AF109, 0x5AF15A, 22, "5.5"),
        (0x5AF15A, OFF_MESSAGE_OFF, 24, "6.0"),
    ]
    for slot_start, slot_end, ticks, frames in stage_slots:
        message = (
            f"[InputBuffer] F9 Cycle -> STAGE {ticks // 2}: {ticks} Ticks "
            f"({frames}f @ 60fps)\n"
        ).encode("ascii")
        capacity = slot_end - slot_start
        if len(message) + 1 > capacity:
            raise ValueError(f"Stage message does not fit at 0x{slot_start:x}")
        data[slot_start:slot_end] = (
            message + b"\x00" + b"\xCC" * (capacity - len(message) - 1)
        )

    off_message = b"[InputBuffer] F9 Cycle -> STAGE 13: OFF (100%% Raw Input)\n"
    off_capacity = ACTIVE_HELPER_OFF - OFF_MESSAGE_OFF
    if len(off_message) + 1 > off_capacity:
        raise ValueError("OFF status message exceeds its reserved slot")
    data[OFF_MESSAGE_OFF:ACTIVE_HELPER_OFF] = (
        off_message + b"\x00" + b"\xCC" * (off_capacity - len(off_message) - 1)
    )

    # Refresh legacy status strings, including the old stage-five 16-tick label.
    legacy_status = [
        (0x631DA0, "[InputBuffer] F9 Cycle -> STAGE 1: 2 Ticks (0.5f @ 60fps)\n"),
        (0x631E00, "[InputBuffer] F9 Cycle -> STAGE 2: 4 Ticks (1.0f @ 60fps)\n"),
        (0x631E60, "[InputBuffer] F9 Cycle -> STAGE 3: 6 Ticks (1.5f @ 60fps)\n"),
        (0x631EC0, "[InputBuffer] F9 Cycle -> STAGE 4: 8 Ticks (2.0f @ 60fps)\n"),
        (0x631F28, "[InputBuffer] F9 Cycle -> STAGE 5: 10 Ticks (2.5f @ 60fps)\n"),
    ]
    for slot, text in legacy_status:
        data[slot:slot + len(text.encode("ascii")) + 1] = text.encode("ascii") + b"\x00"

    startup = b" [InputBuffer] Starting Mode: STAGE 1 (2 Ticks for all vehicles)\n"
    startup_slot = 0x632140 - 0x6320F0
    if len(startup) + 1 > startup_slot:
        raise ValueError("Startup status message exceeds its reserved slot")
    data[0x6320F0:0x632140] = (
        startup + b"\x00" * (startup_slot - len(startup))
    )
    cycle_prompt = b" [InputBuffer] Press F9: +2 ticks each stage (2-24), then OFF\n\x00"
    data[0x632140:0x632140 + len(cycle_prompt)] = cycle_prompt

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
    print(f"Patched shared tick delay for all vehicles: {args.output}")


if __name__ == "__main__":
    main()
