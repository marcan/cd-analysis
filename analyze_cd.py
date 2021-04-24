import wave, sys, os.path, crcmod, json, types, struct
from cueparser import CueSheet

f_data = open(sys.argv[1], "rb")
f_cue = sys.argv[2]
f_report = sys.argv[3]

subq_crc = crcmod.mkCrcFun(poly=0x11021, initCrc=0xffff, rev=False, xorOut=0xffff)

print("Loading cuesheet...")
cuesheet = CueSheet()
cuesheet.setOutputFormat(None, None)
cuesheet.setData(open(f_cue, "r").read())
cuesheet.parse()

wav = wave.open(os.path.join(os.path.dirname(f_cue), cuesheet.file), "rb")

wav_frames = wav.getnframes()
assert wav_frames % (2352 // 4) == 0

frames = wav_frames // (2352 // 4)

print(f"Total frames: {frames}")

tracks = []

print(f"Cuesheet has {len(cuesheet.tracks)} tracks")

for t in cuesheet.tracks:
    print(f"    {t.number} @ {t.offset}")
    tmin, tsec, tframe = (int(i) for i in t.offset.split(":"))
    tstart = tframe + (tsec + tmin * 60) * 75
    if tracks:
        tracks[-1].end = tstart
        tracks[-1].length = tstart - tracks[-1].start
    trk = types.SimpleNamespace()
    trk.number = t.number
    trk.index = 1
    trk.start = tstart
    tracks.append(trk)

tracks[-1].end = frames
tracks[-1].length = frames - tracks[-1].start

trk = types.SimpleNamespace()
trk.number = 100
trk.index = 1
trk.length = 75 * 90
trk.start = frames
trk.end = frames + trk.length
tracks.append(trk)

def bits(bit, n, v):
    return [(bit if v & (1 << (n - 1 - i)) else 0) for i in range(n)]

def gen_subp(cuesheet):
    on = [0x80] * 96
    off = [0] * 96
    for trk in tracks:
        yield on
        for i in range(trk.length - 151):
            yield off
        for i in range(150):
            yield on

def bcd(i):
    if i == 100:
        return 0xaa
    return ((i // 10) << 4) | (i % 10)

def dbcd(i):
    if i == 0xaa:
        return 100
    return (i & 0xf) + 10 * (i >> 4)

def gen_subq(tracks):
    dt = 150
    for trk in tracks:
        for tt in range(trk.length):
            dmin, dsec, dframe = dt // (60 * 75), (dt // 75) % 60, dt % 75
            tmin, tsec, tframe = tt // (60 * 75), (tt // 75) % 60, tt % 75
            q = bytes([0x01,
                       bcd(trk.number), bcd(trk.index),
                       bcd(tmin), bcd(tsec), bcd(tframe),
                       0,
                       bcd(dmin), bcd(dsec), bcd(dframe)])
            crc = subq_crc(q)
            q += bytes([crc >> 8, crc & 0xff])

            yield sum((bits(0x40, 8, i) for i in q), [])
            dt += 1

def gen_sub(tracks):
    for p, q in zip(gen_subp(tracks), gen_subq(tracks)):
        yield bytes([ip | iq for ip, iq in zip(p, q)])

def dec_subq_frame(subpw):
    q = bytes(sum((1 << (7 - b) if subpw[i*8+b] & 0x40 else 0) for b in range(8)) for i in range(12))
    ecrc = subq_crc(q[:-2])
    crc = (q[10] << 8) | q[11]
    if ecrc != crc:
        return None
    dmin, dsec, dframe = dbcd(q[7]), dbcd(q[8]), dbcd(q[9])
    return ((dmin * 60) + dsec) * 75 + dframe - 150

total_subc_errors = 0
total_audio_errors = 0

errors = {}

read_frames = 0

subs_iter = gen_sub(tracks)
exp_subs = []

slip = 0
slips = {}

subdata = []

for frame in range(frames):
    audio = f_data.read(2352)
    if not audio:
        break
    refaudio = wav.readframes(2352 // 4)
    subpw = f_data.read(96)
    subdata.append(subpw)

    sframe = dec_subq_frame(subpw)
    slipped = False
    if sframe and sframe != frame + slip:
        slips[frame] = slip = sframe - frame
        print(f"{frame}: ??? slip is {slip} frames")
        slipped = True

    while len(exp_subs) <= frame + slip:
        exp_subs.append(next(subs_iter))

    if slipped:
        for i in range(frame - 1, -1, -1):
            if i not in errors or not errors[i]["subc"]:
                break

            exp_sub = exp_subs[i + slip]
            subc_errors = 0
            if exp_sub != subpw:
                for a, b in zip(exp_sub, subpw):
                    if a != b:
                        subc_errors += 1

            if subc_errors < errors[i]["subc"]:
                print(f"{i}: recalc: {subc_errors} subcode errors")
                if subc_errors:
                    errors[i]["subc"] = subc_errors
                else:
                    del errors[i]["subc"]
                    if not errors[i]:
                        del errors[i]

    exp_sub = exp_subs[frame + slip]

    subc_errors = 0
    if exp_sub != subpw:
        for a, b in zip(exp_sub, subpw):
            if a != b:
                subc_errors += 1

    audio_errors = 0
    if audio != refaudio:
        total_audio_errors += 1
        for a, b in zip(struct.unpack("<1176H", audio), struct.unpack("<1176H", refaudio)):
            if a != b:
                audio_errors += 1

    if audio_errors:
        print(f"{frame}: !!! {audio_errors} audio errors !!!")
        total_audio_errors += 1

    if subc_errors:
        print(f"{frame}: {subc_errors} subcode errors")
        #print(subpw.hex())
        #print(exp_sub.hex())
        total_subc_errors += subc_errors

    if audio_errors or subc_errors:
        errors[frame] = {"audio": audio_errors, "subc": subc_errors}

    read_frames += 1

rate = total_subc_errors / frames / 96 * 1000000

print()
print(f"Total: {total_audio_errors} sectors with audio errors, subchannel error rate: {rate:.02f} x 10^-6")

with open(f_report, "w") as fd:
    fd.write(json.dumps({
        "tracks": [i.__dict__ for i in tracks],
        "frames": frames,
        "read_frames": read_frames,
        "total_subc_errors": total_subc_errors,
        "total_audio_errors": total_audio_errors,
        "errors": errors,
        "slips": slips,
    }))

