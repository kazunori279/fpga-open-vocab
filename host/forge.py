"""Forge Loader USB CDC protocol (Adiuvo forgix bitstream loader).

Frame: <IBBHIII> magic 'FLDR', version, type, flags, seq, length, crc32(payload)

The ACK/NACK payload is <III> = (code, detail, bytes_written) + optional UTF-8
message. IMPORTANT: `detail` is overloaded and its meaning depends on which
command you sent (see firmware src/main.c):

    HELLO, STATUS -> loader state enum (0 IDLE, 1 PROGRAMMING, 2 DONE, 3 ERROR)
    END (ACK)     -> FPGA pin bitmap: bit0 = CDONE, bit1 = STATUS
    START (ACK)   -> echoed chunk_size
    NACK          -> error-specific (frame length, crc, pin state, ...)

Decoding an END pin bitmap as a loader state (or vice versa) silently gives a
plausible-looking wrong answer, so always pass the command you sent.
"""

import struct
import time
import zlib

import serial

MAGIC = 0x52444C46  # 'FLDR'
VERSION = 1
HDR = "<IBBHIII"
HDR_LEN = struct.calcsize(HDR)
assert HDR_LEN == 20
MAX_PAYLOAD = 4096

HELLO, START, DATA, END, ABORT, STATUS = 0x01, 0x02, 0x03, 0x04, 0x05, 0x06
ACK, NACK = 0x80, 0x81

CMD_NAME = {HELLO: "HELLO", START: "START", DATA: "DATA", END: "END", ABORT: "ABORT", STATUS: "STATUS"}
STATE_NAME = {0: "IDLE", 1: "PROGRAMMING", 2: "DONE", 3: "ERROR"}

# Firmware waits up to 3 s for the host, and TinyUSB needs a moment after the
# host asserts DTR. Writing immediately after open can desync the frame reader.
SETTLE_S = 0.3


class ForgeError(Exception):
    pass


class Nack(ForgeError):
    def __init__(self, code, detail, written, msg):
        super().__init__(f"NACK code={code} detail=0x{detail:X} written={written} msg={msg!r}")
        self.code, self.detail, self.written, self.msg = code, detail, written, msg


def build(type_: int, seq: int = 0, payload: bytes = b"") -> bytes:
    return struct.pack(
        HDR, MAGIC, VERSION, type_, 0, seq, len(payload), zlib.crc32(payload)
    ) + payload


def open_port(dev: str, timeout: float = 5.0) -> serial.Serial:
    # write_timeout matters: if the firmware stops draining USB, writes would
    # otherwise block forever with no way to reset the board but unplugging it.
    port = serial.Serial(dev, 115200, timeout=timeout, write_timeout=3.0)
    time.sleep(SETTLE_S)
    port.reset_input_buffer()
    return port


class Reply:
    def __init__(self, cmd, rtype, code, detail, written, msg):
        self.cmd, self.rtype = cmd, rtype
        self.code, self.detail, self.written, self.msg = code, detail, written, msg

    @property
    def is_ack(self) -> bool:
        return self.rtype == ACK

    @property
    def loader_state(self):
        """Only meaningful for HELLO/STATUS ACKs."""
        if self.is_ack and self.cmd in (HELLO, STATUS):
            return STATE_NAME.get(self.detail, f"?{self.detail}")
        return None

    @property
    def pins(self):
        """(cdone, status) - only meaningful for an END ACK."""
        if self.is_ack and self.cmd == END:
            return bool(self.detail & 1), bool(self.detail & 2)
        return None

    def describe(self) -> str:
        kind = "ACK" if self.is_ack else "NACK"
        out = f"{kind} code={self.code}"
        if (st := self.loader_state) is not None:
            out += f" state={st}"
        elif (p := self.pins) is not None:
            out += f" CDONE={int(p[0])} STATUS={int(p[1])}"
        else:
            out += f" detail=0x{self.detail:X}"
        out += f" written={self.written}"
        return out + (f" msg={self.msg!r}" if self.msg else "")


def exchange(port: serial.Serial, type_: int, seq: int, payload: bytes = b"", raise_on_nack: bool = True) -> Reply:
    port.write(build(type_, seq, payload))
    port.flush()
    hdr = port.read(HDR_LEN)
    if len(hdr) < HDR_LEN:
        raise ForgeError(
            f"timeout waiting for reply to {CMD_NAME.get(type_, hex(type_))} "
            f"(got {len(hdr)}/{HDR_LEN} bytes)"
        )
    magic, _ver, rtype, _flags, _rseq, length, crc = struct.unpack(HDR, hdr)
    if magic != MAGIC:
        raise ForgeError(f"bad reply magic 0x{magic:08X}")
    body = port.read(length) if length else b""
    if len(body) < length:
        raise ForgeError(f"short payload {len(body)}/{length}")
    if zlib.crc32(body) != crc:
        raise ForgeError("reply CRC mismatch")

    code = detail = written = 0
    msg = ""
    if len(body) >= 12:
        code, detail, written = struct.unpack("<III", body[:12])
        msg = body[12:].split(b"\x00")[0].decode("utf-8", "replace")
    reply = Reply(type_, rtype, code, detail, written, msg)
    if rtype == NACK and raise_on_nack:
        raise Nack(code, detail, written, msg)
    return reply
