"""A plain-Python GIF decoder: a third, independent check on the encoder's streams.

decode(gif) -> [(indexes, how_it_ended)] per frame. Strict: a bad code, a stream
that runs out before EOI, or the wrong pixel count raises.
"""
import struct


def lzw_decode(data: bytes, min_size: int) -> bytes:
    clear, eoi = 1 << min_size, (1 << min_size) + 1
    out, pos, acc, bits = bytearray(), 0, 0, 0
    table, size, prev = None, min_size + 1, None
    while True:
        while bits < size:
            if pos >= len(data):
                raise ValueError("stream ended before EOI")
            acc |= data[pos] << bits
            pos += 1
            bits += 8
        code = acc & ((1 << size) - 1)
        acc >>= size
        bits -= size
        if code == clear:
            table, size, prev = [bytes([i]) for i in range(clear)] + [b"", b""], min_size + 1, None
            continue
        if code == eoi:
            return bytes(out)
        if table is None:
            raise ValueError("stream must start with CLEAR")
        if prev is None:
            if code >= clear:
                raise ValueError(f"first code after CLEAR is {code}")
            entry = table[code]
        elif code < len(table):
            entry = table[code]
            if len(table) < 4096:
                table.append(prev + entry[:1])
        elif code == len(table) and len(table) < 4096:
            entry = prev + prev[:1]
            table.append(entry)
        else:
            raise ValueError(f"code {code} but table has {len(table)}")
        out += entry
        prev = entry
        if len(table) == (1 << size) and size < 12:
            size += 1


def decode(gif: bytes) -> list[bytes]:
    if gif[:6] not in (b"GIF89a", b"GIF87a"):
        raise ValueError("not a GIF")
    w, h, packed, _, _ = struct.unpack("<HHBBB", gif[6:13])
    p = 13 + (3 * (2 << (packed & 7)) if packed & 0x80 else 0)
    frames = []
    while True:
        b = gif[p]
        p += 1
        if b == 0x3B:
            return frames
        if b == 0x21:  # extension: skip its sub-blocks
            p += 1
            while gif[p]:
                p += gif[p] + 1
            p += 1
            continue
        if b != 0x2C:
            raise ValueError(f"unexpected block {b:#x}")
        _, _, fw, fh, fp = struct.unpack("<HHHHB", gif[p:p + 9])
        p += 9 + (3 * (2 << (fp & 7)) if fp & 0x80 else 0)
        min_size = gif[p]
        p += 1
        data = bytearray()
        while gif[p]:
            data += gif[p + 1:p + 1 + gif[p]]
            p += gif[p] + 1
        p += 1
        idx = lzw_decode(bytes(data), min_size)
        if len(idx) != fw * fh:
            raise ValueError(f"frame has {len(idx)} pixels, wants {fw * fh}")
        frames.append(idx)
