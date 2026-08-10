# -*- coding: utf-8 -*-
"""`EFFECT.BIN` 의 시나리오 예고 타이틀 카드를 한글판으로 만든다.

바꾸는 것
  * 제목 스트립(일본어) -> 한국어      (`translation/eyecatch_titles_ko.json`)
  * 공용 글자 '第' -> '제', '話' -> '화' (멤버마다 레코드 10, 11)

바꾸지 않는 것
  * 스트립 폭·높이, 레코드 표, 데이터 오프셋, CLUT, 멤버 크기, 파일 크기.
    재압축본을 **원래 바이트 범위 안에** 넣고 꼬리는 원본 그대로 둔다.
    (디코더는 종료 마커에서 멈추므로 남은 꼬리는 읽히지 않는다.)

한글은 원본 일본어보다 노이즈가 적어 오히려 더 잘 압축된다 —
멤버마다 4,000바이트 이상 여유가 남는다.
"""
import json
import os
import struct
import sys

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "srwcb_paths.py")):
    _d = os.path.dirname(_d)
if _d not in sys.path:
    sys.path.insert(0, _d)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import srwcb_paths as _P            # noqa: E402
import eyecatch as EC               # noqa: E402
import eyecatch_draw as ED          # noqa: E402
from srw_lz_fast import decompress  # noqa: E402
from srw_lz_enc import compress     # noqa: E402

# 멤버 -> (압축 스트림 시작 오프셋, 제목 스트립 레코드 범위)
# 시작 오프셋은 아카이브 표에서 계산한 멤버 시작과 다르다(멤버마다 앞에 여백이
# 조금 있다). 실측값을 박아 두고 빌드 때 검증한다.
MEMBERS = {
    164: (0x1BA708, None),          # None = japanese_records() 로 고른다
    165: (0x1C3318, None),
    166: (0x1C65C2, None),
    167: (0x1CEEEB, None),
    168: (0x1D6F46, None),
    169: (0x1DC181, (12, 32)),      # EX 는 영문 부제가 없어 기준점이 달라
    170: (0x1E20D4, (12, 32)),      # 레코드 번호로 고른다
    171: (0x1E8F65, (12, 32)),
    172: (0x1F1ED3, (12, 16)),
}
TITLES = _P.TRANSLATION / "eyecatch_titles_ko.json"


def members(d):
    """C_SMAP 계열과 같은 skew 규칙."""
    n = struct.unpack_from("<I", d, 0)[0] // 4
    t = [struct.unpack_from("<I", d, 4 * i)[0] for i in range(n)]
    return [(t[2 * i] + 8 * i, t[2 * i + 1] + 8 * i + 4) for i in range(n // 2)]


def strips(recs, span):
    if span is None:
        return EC.japanese_records(recs)
    lo, hi = span
    return [r for r in recs[lo:hi] if r["w"] > 40]


def patch_member(raw, texts, label, log):
    recs, _ = EC.parse(raw)
    buf = bytearray(raw)
    span = MEMBERS[label][1]
    ss = strips(recs, span)
    if len(ss) != len(texts):
        raise SystemExit(f"m{label}: 스트립 {len(ss)}개인데 번역 {len(texts)}개")
    base = min(r["h"] for r in ss)
    touched = []
    for rec, ko in zip(ss, texts):
        want = max(1, round(rec["h"] / base))
        got = ko.count("\n") + 1
        if want != got:
            raise SystemExit(
                f"m{label} rec{rec['i']} {rec['w']}x{rec['h']}: 줄 수 {got} (원본 {want}) — '{ko}'")
        ED.redraw(buf, rec, ko)
        touched.append((rec["off"], rec["off"] + rec["w"] * rec["h"] // 2))
    for idx, ko in ((EC.DAI_INDEX, "제"), (EC.WA_INDEX, "화")):
        rec = recs[idx]
        ED.redraw(buf, rec, ko, ED.HEAD_SCHEME)
        touched.append((rec["off"], rec["off"] + rec["w"] * rec["h"] // 2))
    stray = [i for i in range(len(raw))
             if raw[i] != buf[i] and not any(lo <= i < hi for lo, hi in touched)]
    if stray:
        raise SystemExit(f"m{label}: 지정 범위 밖 변경 {len(stray)}바이트")
    after, _ = EC.parse(bytes(buf))
    if after != recs:
        raise SystemExit(f"m{label}: 레코드 표가 바뀜")
    log(f"  m{label}: 제목 {len(ss)} + 第/話 2 개 교체")
    return bytes(buf)


def build(src, log=print):
    out = bytearray(src)
    ms = members(src)
    data = json.loads(TITLES.read_text(encoding="utf-8"))["members"]
    for mi, (pos, _span) in MEMBERS.items():
        limit = next(e for s, e in ms if s <= pos < e)
        raw, used = decompress(src[pos:limit], 0)
        new_raw = patch_member(raw, data[str(mi)]["titles"], mi, log)
        new = compress(new_raw)
        if len(new) > used:
            raise SystemExit(f"m{mi}: 재압축 {len(new):,} > 자리 {used:,}")
        chk, u2 = decompress(new, 0)
        if chk != new_raw or u2 != len(new):
            raise SystemExit(f"m{mi}: 재압축 왕복 실패")
        out[pos:pos + len(new)] = new
        log(f"     압축 {used:,} -> {len(new):,} (여유 {used - len(new):,}B)")
    assert len(out) == len(src), "파일 크기가 바뀌었다"
    return bytes(out)


def main():
    src = (_P.EXTRACTED / "EFFECT.BIN").read_bytes()
    out = build(src)
    dst = _P.BUILD / "gfx" / "EFFECT_ko.BIN"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
    print(f"WROTE {dst} ({len(out):,}B, 원본과 같은 크기)")


if __name__ == "__main__":
    main()
