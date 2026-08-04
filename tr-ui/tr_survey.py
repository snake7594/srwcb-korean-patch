# -*- coding: utf-8 -*-
"""트레이닝 모드(TR.WAR) 정찰.

1) 패치본 이미지에서 TR.WAR 를 뽑아 낸다 (폰트만 한글로 바뀐 상태).
2) 박혀 있는 BMESS2/3/4 외부 오프셋표가 재패킹본과 어긋나 있는지 확인한다.
   -> 어긋나면 전투 메시지 로드가 CPE 블록 중간에서 시작해 전투가 멈춘다.
3) 남아 있는 일본어 레코드를 전수 스캔한다.
"""

# --- 이식용 부트스트랩 (자동 삽입): 저장소 어디서 실행하든 동작 ---
import os as _os, sys as _sys
_d = _os.path.dirname(_os.path.abspath(__file__))
while _d != _os.path.dirname(_d) and not _os.path.exists(_os.path.join(_d, "srwcb_paths.py")):
    _d = _os.path.dirname(_d)
if _d not in _sys.path:
    _sys.path.insert(0, _d)
import srwcb_paths as _P
_P.ensure_dirs()
# ------------------------------------------------------------------
import json, math, os, re, struct, sys
from pathlib import Path

ROOT = str(_P.WORK)
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, str(_P.TOOLS))
sys.path.insert(0, SP)
from extract_psx_iso import RawMode2Image, read_tree

IMG = f"{ROOT}/test_build/third_full/Super Robot Taisen Complete Box Korean v0.10.4 (Track 1).bin"
SEC, UDO, UDS = 2352, 24, 2048
OUT = f"{SP}/tr"
os.makedirs(OUT, exist_ok=True)

_mpj = json.load(open(f"{ROOT}/research/srwcb_embedded_font_mapping_reviewed.json", encoding="utf-8"))
IDX2CH = {r["glyph_index"]: (r.get("character") or "") for r in _mpj["rows"]}
CTRL = {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0, 0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1}
JPRE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")


def read_file(img, lba, size):
    b = bytearray()
    with open(img, "rb") as f:
        for i in range(math.ceil(size / UDS)):
            f.seek((lba + i) * SEC)
            b += f.read(SEC)[UDO:UDO + UDS]
    return bytes(b[:size])


def decode(buf, off, maxlen=260):
    s = ""; i = off
    while i < min(len(buf), off + maxlen):
        x = buf[i]
        if x == 0xFF:
            return s, i + 1
        if x < 0xEB:
            c = IDX2CH.get(x)
            if not c: return None, 0
            s += c; i += 1
        elif x < 0xF6:
            k = ((x - 0xEB) << 8) | buf[i + 1]
            c = "_" if k == 0x3FF else IDX2CH.get(k)
            if not c: return None, 0
            s += c; i += 2
        else:
            n = CTRL.get(x)
            if n is None: return None, 0
            s += "<f6>" if x == 0xF6 else "<f7>" if x == 0xF7 else f"[{x:02X}]"
            i += 1 + n
    return None, 0


def plain(s):
    return re.sub(r"<f[67]>|\[..\]|_", "", s)


def main():
    with RawMode2Image(Path(IMG)) as m:
        _, entries = read_tree(m)
    P = {e.path.strip("/").split("/")[-1]: e for e in entries}

    e = P["TR.WAR"]
    tr = read_file(IMG, e.lba, e.size)
    Path(f"{OUT}/TR_patched.war").write_bytes(tr)
    print(f"TR.WAR  LBA {e.lba}  {e.size:,}B -> {OUT}/TR_patched.war")

    # --- BMESS 외부표 상태 ---
    print("\n[BMESS 외부 오프셋표]")
    fix = []
    for name in ("BMESS2", "BMESS3", "BMESS4"):
        old = Path(f"{ROOT}/extracted/{name}.BIN").read_bytes()
        old_tbl = old[:struct.unpack_from("<I", old, 0)[0]]
        pe = P[name + ".BIN"]
        new = read_file(IMG, pe.lba, pe.size)
        new_tbl = new[:struct.unpack_from("<I", new, 0)[0]]
        off = tr.find(old_tbl)
        assert len(old_tbl) == len(new_tbl)
        n = tr.count(old_tbl)
        print(f"  {name}: 표 {len(old_tbl)}B  TR.WAR @{off:#x} (x{n})  "
              f"{'낡음 → 교체 필요' if off >= 0 else '없음'}")
        if off >= 0:
            assert n == 1, f"{name} 표가 유일하지 않음"
            fix.append((name, off, old_tbl, new_tbl))
    json.dump([{"name": n, "off": o} for n, o, _, _ in fix],
              open(f"{OUT}/bmess_tables.json", "w"), indent=1)

    # --- 남은 일본어 레코드 ---
    print("\n[일본어 레코드 스캔]")
    recs = []
    i = 1
    while i < len(tr):
        if tr[i - 1] != 0xFF or tr[i] == 0xFF:
            i += 1; continue
        s, end = decode(tr, i)
        if not s:
            i += 1; continue
        body = plain(s)
        n = len(JPRE.findall(body))
        if body and n >= 1 and n / len(body) >= 0.34:
            recs.append({"off": i, "end": end, "len": end - i, "text": s})
            i = end
        else:
            i += 1
    # 구간별 집계
    print(f"  총 {len(recs)}개")
    buckets = {}
    for r in recs:
        b = r["off"] >> 12
        buckets.setdefault(b, []).append(r)
    for b in sorted(buckets):
        rs = buckets[b]
        print(f"    {b<<12:#08x}  {len(rs):>4}개   예: " +
              " | ".join(plain(x["text"])[:14] for x in rs[:3]))
    json.dump(recs, open(f"{OUT}/tr_jp_records.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"\nWROTE {OUT}/tr_jp_records.json")


if __name__ == "__main__":
    main()
