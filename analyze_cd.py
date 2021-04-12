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
    return ((i // 10) << 4) | (i % 10)

def gen_subq(cuesheet):
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

total_subc_errors = 0
total_audio_errors = 0

errors = {}

for frame, exp_sub in zip(range(frames), gen_sub(tracks)):
    audio = f_data.read(2352)
    if not audio:
        break
    refaudio = wav.readframes(2352 // 4)
    subpw = f_data.read(96)

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

rate = total_subc_errors / frames / 96 * 1000000

print()
print(f"Total: {total_audio_errors} sectors with audio errors, subchannel error rate: {rate:.02f} x 10^-6")

with open(f_report, "w") as fd:
    fd.write(json.dumps({
        "tracks": [i.__dict__ for i in tracks],
        "frames": frames,
        "total_subc_errors": total_subc_errors,
        "total_audio_errors": total_audio_errors,
        "errors": errors,
    }))
    
