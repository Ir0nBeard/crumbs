import struct, zlib, os, sys

def make_png(path, size):
    color = (58, 90, 64, 255)
    raw = b"".join(b"\x00" + bytes(color) * size for _ in range(size))
    def chunk(tag, data):
        c = tag + data
        crc = zlib.crc32(c) % 0x100000000
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)

outdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
os.makedirs(outdir, exist_ok=True)
for s in (16, 48, 128):
    make_png(os.path.join(outdir, f"icon{s}.png"), s)
print("icons written:", sorted(os.listdir(outdir)))
