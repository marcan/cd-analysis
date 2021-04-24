#!/usr/bin/python
import sys, json
from PIL import Image

f_in = sys.argv[1]
f_out = sys.argv[2]
fac = int(sys.argv[3])
h = int(sys.argv[4])
bright = int(sys.argv[5])

with open(f_in) as fd:
    data = json.loads(fd.read())

w = (data["frames"] + (fac * h - 1)) // (fac * h)

pix = {}

for k, v in data["errors"].items():
    k = int(k) // fac
    pix.setdefault(k, 0)
    if v["audio"]:
        pix[k] = True
    elif v["subc"] and pix[k] is not True:
        pix[k] += v["subc"]

im = Image.new("RGB", (w, h), (255, 0, 255))

d = bytearray(w * h * 3)

for i, cnt in pix.items():
    if not cnt:
        continue
    if cnt is True:
        col = (255, 0, 255)
    else:
        v = cnt * 3 / 16 * bright
        if v < 1:
            col = (int(128 + 127 * v), 0, 0)
        elif v < 2:
            col = (255, int(255 * (v - 1)), 0)
        elif v < 3:
            col = (255, 255, int(255 * (v - 2)))
        elif v < 4:
            col = (255 - int(255 * (v - 3)), 255, 255)
        else:
            col = (0, 255, 255)
    d[i * 3:i * 3+ 3] = col

im = Image.frombytes("RGB", (h, w), bytes(d))
im = im.transpose(Image.TRANSPOSE)
im = im.resize((w * 2, h * 2), resample=Image.NEAREST)
im.save(f_out)
#im.show()
