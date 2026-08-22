# -*- coding: utf-8 -*-
"""표 **밖**까지 훑는 보고 도구(게이트 아님).

UI 표는 audit/verify_name_canon.py 가 잡는다. 이 스크립트는 그 밖 — 메뉴 문구,
BGM·데모 제목, 예고편 제목 — 을 본다. 여기 남는 차이는 그 게임의 레코드 슬롯이
공백 한 칸을 더 못 받아 **물리적으로 강제된 것**이 섞여 있어 게이트로 못 쓴다.

    python audit/sweep_string_variants.py "<완성 이미지 Track1>" out.json

원리: 네 실행파일에서 한글 문자열을 전부 뽑아, 게임마다 다르게 적힌
같은 말을 찾는다. 판정 기준 두 가지 — 공백만 다른 것, 편집거리 1 인 것."""
import sys, json, re, collections
from pathlib import Path
R = Path(r"D:/ps1/roms/SRWCB/srwcb-korean-patch")
sys.path.insert(0, str(R/"image-build")); sys.path.insert(0, str(R/"tools"))
import assemble_image as AI
from second_translation_codec import load_safe_glyph_map
KO = {v: k for k, v in load_safe_glyph_map().items()}
IMG = Path(sys.argv[1])
WARS = {"제2차": "SECOND/SECOND.WAR", "제3차": "THIRD/THIRD.WAR",
        "EX": "EX/EX.WAR", "TR": "TR.WAR"}
with AI.RawMode2Image(IMG) as m: _, ents = AI.read_tree(m)
by = {e.path.strip("/"): e for e in ents}

def strings(buf):
    """레코드 경계(0xFF)로 끊으며 글리프를 푼다. 제어바이트는 구분자로 본다."""
    out, cur, p = [], [], 0
    n = len(buf)
    while p < n:
        b = buf[p]
        if b < 0xEB:
            ch = KO.get(b)
            if ch is None: cur and out.append("".join(cur)); cur = []
            else: cur.append(ch)
            p += 1
        elif b <= 0xF5:
            if p + 1 >= n: break
            ch = KO.get(((b - 0xEB) << 8) | buf[p+1])
            if ch is None: cur and out.append("".join(cur)); cur = []
            else: cur.append(ch)
            p += 2
        else:
            if cur: out.append("".join(cur)); cur = []
            p += 1
    if cur: out.append("".join(cur))
    return out

HAN = re.compile(r"[가-힣]")
sets = {}
for g, rel in WARS.items():
    buf = AI.read_file(IMG, by[rel].lba, by[rel].size)
    s = {t.strip() for t in strings(buf) if HAN.search(t) and 2 <= len(t.strip()) <= 28}
    sets[g] = s
    print(f"  {g}: 한글 문자열 {len(s)}개")

strip = lambda t: t.replace(" ", "").replace("\u3000", "")
def ed1(a, b):
    if abs(len(a)-len(b)) > 1: return False
    if len(a) == len(b): return sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b): a, b = b, a
    for i in range(len(b)):
        if b[:i] + b[i+1:] == a: return True
    return False

space_only, near = [], []
allg = list(WARS)
for i, g1 in enumerate(allg):
    for g2 in allg[i+1:]:
        only1 = sets[g1] - sets[g2]
        only2 = sets[g2] - sets[g1]
        idx2 = collections.defaultdict(set)
        for t in only2: idx2[strip(t)].add(t)
        for t in only1:
            k = strip(t)
            for u in idx2.get(k, ()):
                if u != t: space_only.append((g1, t, g2, u))
        by_len = collections.defaultdict(list)
        for u in only2: by_len[len(u)].append(u)
        for t in only1:
            if len(t) < 3: continue
            for L in (len(t)-1, len(t), len(t)+1):
                for u in by_len.get(L, ()):
                    if strip(t) != strip(u) and ed1(t, u): near.append((g1, t, g2, u))
def dedup(rows):
    seen, out = set(), []
    for g1, t, g2, u in rows:
        k = tuple(sorted((t, u)))
        if k in seen: continue
        seen.add(k); out.append((g1, t, g2, u))
    return out
space_only, near = dedup(space_only), dedup(near)
print(f"\n공백만 다른 짝 {len(space_only)}건")
for g1, t, g2, u in sorted(space_only, key=lambda r: r[1])[:80]:
    print(f"   {g1}:{t!r}   {g2}:{u!r}")
print(f"\n한 글자 다른 짝 {len(near)}건")
for g1, t, g2, u in sorted(near, key=lambda r: r[1])[:80]:
    print(f"   {g1}:{t!r}   {g2}:{u!r}")
json.dump({"space_only": space_only, "near": near},
          open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
