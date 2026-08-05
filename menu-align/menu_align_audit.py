# -*- coding: utf-8 -*-
"""4개 실행파일 ui_master 정렬 전수 감사.

제2차(검증된 기준)의 규칙: 모든 텍스트 런의 renderer advance 를 레트일과 정확히
일치시킨다(끝 phase 는 무관). 부족분은 반각 0x00 으로 뒤를 채우고, 선행 공백
(들여쓰기)은 보존한다. 필요하면 앵커 인자(FC x y)도 보정한다.

이 감사는 게임별로:
  A) 레코드 k 마다 레트일/패치본 포인터를 따라가 런 단위 advance 를 대조
  B) 컨트롤 스켈레톤 차이(앵커 보정 등)를 보고
  C) 레트일 레코드가 제2차와 바이트 동일한지(=이식 가능) 표시
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
for _sub in ("tools", "third-ui", "ex-ui", "tr-ui", "audit", "menu-align", "second-fixes"):
    _p = _os.path.join(_d, _sub)
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.append(_p)
# ------------------------------------------------------------------
import json, math, struct, sys
from pathlib import Path

R = str(_P.WORK)
sys.path.insert(0, f"{R}/tools"); sys.path.insert(0, ".")
from extract_psx_iso import RawMode2Image, read_tree
from second_translation_codec import load_safe_glyph_map, add_extra_glyph_mapping

mp = json.load(open(f"{R}/research/srwcb_embedded_font_mapping_reviewed.json", encoding="utf-8"))
I2C = {r["glyph_index"]: (r.get("character") or "") for r in mp["rows"]}
gm = add_extra_glyph_mapping(load_safe_glyph_map(),
                             ['×', '…', '↑', '→', '↓', '□', '△', '○', '릭', '응'])
INV = {}
for ch, ix in gm.items():
    INV.setdefault(ix, ch)
CTRL = {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0, 0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1}
SEC, UDO, UDS = 2352, 24, 2048
IMG = f"{R}/test_build/third_full/Super Robot Taisen Complete Box Korean v0.10.5 (Track 1).bin"

GAMES = [  # (이름, retail 경로, ISO 파일명, ui_master 헤더, 개수)
    ("SECOND", f"{R}/extracted/SECOND/SECOND.WAR", "SECOND.WAR", 0x24320, 107),
    ("THIRD",  f"{R}/extracted/THIRD/THIRD.WAR",  "THIRD.WAR",  0x247CC, 107),
    ("EX",     f"{R}/extracted/EX/EX.WAR",        "EX.WAR",     0x188C4, 107),
    ("TR",     f"{R}/extracted/TR.WAR",           "TR.WAR",     0x188BC, 107),
]


def toks(b, s):
    p = s
    while p < len(b):
        x = b[p]
        if x == 0xFF:
            yield (p, 1, 'end'); return
        if x < 0xEB: yield (p, 1, 'g'); p += 1
        elif x <= 0xF5: yield (p, 2, 'g'); p += 2
        else:
            n = 1 + CTRL.get(x, 0); yield (p, n, 'c'); p += n


def parse(b, s):
    """[( 'c', bytes ) | ( 'r', [glyph_idx] )] — 런과 컨트롤의 시퀀스."""
    out = []; cur = []
    for p, n, k in toks(b, s):
        if k == 'end':
            break
        if k == 'g':
            cur.append(b[p] if n == 1 else ((b[p] - 0xEB) << 8) | b[p + 1])
        else:
            if cur: out.append(('r', cur)); cur = []
            out.append(('c', bytes(b[p:p + n])))
    if cur: out.append(('r', cur))
    return out


def sig(v):
    adv = 0; ph = 0; txt = ""
    for i in v:
        if i < 0x101: adv += 1
        else: adv += 1 + ph; ph ^= 1
        txt += ("␣" if i == 0 else "▉" if i == 0x3FF else (INV.get(i) or I2C.get(i, '?')))
    return adv, ph, txt


def rd(fp, lba, n):
    b = bytearray()
    with open(fp, "rb") as f:
        for i in range(math.ceil(n / UDS)):
            f.seek((lba + i) * SEC); b += f.read(SEC)[UDO:UDO + UDS]
    return bytes(b[:n])


def rec_bytes(b, s):
    e = s
    for p, n, k in toks(b, s):
        e = p + n
        if k == 'end':
            break
    return b[s:e]


def main():
    with RawMode2Image(Path(IMG)) as m:
        _, E = read_tree(m)
    P = {e.path.strip("/").split("/")[-1]: e for e in E}

    data = {}
    for name, rp, iso, hdr, cnt in GAMES:
        e = P[iso]
        data[name] = (open(rp, "rb").read(), rd(IMG, e.lba, e.size), hdr, cnt)

    sec_ret = data["SECOND"][0]
    sec_hdr = GAMES[0][3]

    report = {}
    for name, rp, iso, hdr, cnt in GAMES:
        ret, pat, hdr, cnt = data[name]
        rows = []
        for k in range(cnt):
            f = hdr + 4 + 4 * k
            rt = f + struct.unpack_from("<i", ret, f)[0]
            pt = f + struct.unpack_from("<i", pat, f)[0]
            rrec = parse(ret, rt); prec = parse(pat, pt)
            rruns = [v for t, v in rrec if t == 'r']
            pruns = [v for t, v in prec if t == 'r']
            rctl = [v for t, v in rrec if t == 'c']
            pctl = [v for t, v in prec if t == 'c']
            # 제2차와의 레트일 동일성
            sf = sec_hdr + 4 + 4 * k
            st = sf + struct.unpack_from("<i", sec_ret, sf)[0]
            same_as_second = rec_bytes(ret, rt) == rec_bytes(sec_ret, st)
            issues = []
            if len(rruns) != len(pruns) or len(rctl) != len(pctl):
                issues.append(("STRUCT", f"런 {len(rruns)}->{len(pruns)} 컨트롤 {len(rctl)}->{len(pctl)}"))
            else:
                for i, (a, b) in enumerate(zip(rruns, pruns)):
                    (a1, p1, t1), (a2, p2, t2) = sig(a), sig(b)
                    if a1 != a2:
                        issues.append(("ADV", i, a1, a2, t1, t2))
                for i, (a, b) in enumerate(zip(rctl, pctl)):
                    if a != b:
                        issues.append(("CTL", i, a.hex(" "), b.hex(" ")))
            if issues:
                rows.append((k, same_as_second, issues))
        report[name] = rows
        n_adv = sum(1 for _, _, iss in rows for x in iss if x[0] == "ADV")
        n_ctl = sum(1 for _, _, iss in rows for x in iss if x[0] == "CTL")
        n_st = sum(1 for _, _, iss in rows for x in iss if x[0] == "STRUCT")
        print(f"== {name}: 문제 레코드 {len(rows)}/107  (ADV {n_adv} / CTL {n_ctl} / STRUCT {n_st})")
        for k, same, iss in rows:
            head = f"  [{k:>3}]{'=2차' if same else '    '}"
            for x in iss:
                if x[0] == "ADV":
                    _, i, a1, a2, t1, t2 = x
                    print(f"{head} run{i}: {a1}->{a2}  '{t1[:20]}' -> '{t2[:24]}'")
                elif x[0] == "CTL":
                    print(f"{head} ctl{x[1]}: {x[2]} -> {x[3]}")
                else:
                    print(f"{head} {x[1]}")
                head = " " * len(head)
    json.dump({g: [(k, s, [list(map(str, x)) for x in iss]) for k, s, iss in rows]
               for g, rows in report.items()},
              open("tr/menu_align_audit.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
