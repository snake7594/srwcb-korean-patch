"""Build a byte-precise WAR->SLPS_024.06 offset map.

Strategy: anchor every 0x40 bytes with unique 20-byte signatures, then
bisect between anchors of differing delta to find the exact boundary.
Outputs delta_map.json: list of [war_start, war_end_exclusive, delta].
Validates: full-file coverage accounting + spot checks.
"""
import json, sys

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
WAR_PATH = str(config.CB_RETAIL_SECOND)
SLP_PATH = str(config.SRW2_RETAIL_EXE)
OUT = str(config.DELTA_MAP)

war = open(WAR_PATH, "rb").read()
slp = open(SLP_PATH, "rb").read()

def find_unique(sig):
    i = slp.find(sig)
    if i < 0:
        return None
    if slp.find(sig, i + 1) >= 0:
        return None
    return i

# 1) anchors
anchors = []  # (war_off, delta)
STEP = 0x40
for off in range(0x800, len(war) - 32, STEP):
    sig = war[off:off + 20]
    if len(set(sig)) < 6:
        continue
    hit = find_unique(sig)
    if hit is not None:
        anchors.append((off, hit - off))
print(f"anchors: {len(anchors)}")

# 2) zone list with exact boundaries via bisection
zones = []  # [war_start, war_end_exclusive, delta]
cur_start, cur_delta = anchors[0]
prev_off = anchors[0][0]
for off, d in anchors[1:]:
    if d != cur_delta:
        # bisect between prev_off (cur_delta) and off (d) for exact switch
        lo, hi = prev_off, off  # lo maps with cur_delta, hi with d
        # boundary = smallest x where war[x:] no longer matches slp[x+cur_delta:]
        # use direct byte compare: find first mismatch position from lo
        x = lo
        limit = hi
        while x < limit and x + cur_delta < len(slp) and war[x] == slp[x + cur_delta]:
            x += 1
        boundary = x
        zones.append([cur_start, boundary, cur_delta])
        cur_start, cur_delta = boundary, d
    prev_off = off
zones.append([cur_start, len(war), cur_delta])

print(f"zones: {len(zones)}")
for z in zones:
    print(f"  {hex(z[0])}..{hex(z[1])} delta=+{hex(z[2])}")

# 3) validation: measure how many bytes in each zone actually match
total = 0
matched = 0
for zs, ze, zd in zones:
    n = ze - zs
    m = sum(1 for k in range(zs, ze, 97) if k + zd < len(slp) and war[k] == slp[k + zd])
    samples = len(range(zs, ze, 97))
    total += samples
    matched += m
    if samples and m / samples < 0.97:
        print(f"  WARN zone {hex(zs)}..{hex(ze)} match ratio {m}/{samples}")
print(f"sample match: {matched}/{total} ({matched/total*100:.2f}%)")

json.dump(zones, open(OUT, "w"))
print("saved", OUT)
