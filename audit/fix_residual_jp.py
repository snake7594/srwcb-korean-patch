# -*- coding: utf-8 -*-
"""원문 바이트가 그대로 남은 자리를 **제자리에서** 한글로 바꾼다.

폰트를 한글로 갈아 끼웠기 때문에, 번역이 안 된 레코드는 화면에서 일본어가
아니라 **뜻 모를 한글**로 나온다(예: 分岐 선택지 `早乙女研究所/光子力研究所`
가 '건걷걸근귿글'). 감사기는 한글맵으로 읽어 한글이 보이면 통과시켜 왔기
때문에 이런 자리를 오래 놓쳤다(2026-08-10 제보 #8).

여기서는 **길이를 늘리지 않는** 치환만 한다. 짧아지는 만큼은 빈칸(0x00)으로
메워 레코드 길이·포인터·작전목적 블록의 줄 수가 전부 그대로 남는다.
"""
import os
import sys

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "srwcb_paths.py")):
    _d = os.path.dirname(_d)
for _s in ("", "tools"):
    _p = os.path.join(_d, _s) if _s else _d
    if _p not in sys.path:
        sys.path.insert(0, _p)

import srwcb_paths as _P                       # noqa: E402
import second_translation_codec as C           # noqa: E402
import json                                    # noqa: E402

_REVIEWED = json.loads(_P.FONT_MAPPING.read_text(encoding="utf-8")) \
    if str(_P.FONT_MAPPING).endswith(".json") else None


def _jp_index():
    """원문 글자 -> 글리프 번호 (레트일 폰트)."""
    rows = json.loads((_P.WORK / "research" /
                       "srwcb_embedded_font_mapping_reviewed.json").read_text(encoding="utf-8"))["rows"]
    out = {}
    for r in rows:
        ch = r.get("character")
        if ch and ch not in out:
            out[ch] = r["glyph_index"]
    return out


def _enc(text, table):
    return b"".join(C.encode_glyph_index(table[ch]) for ch in text)


# 파일 -> [(원문, 한국어), …]  길이가 늘면 실패시킨다.
_CONDITIONS = [
    ("フェイルロ-ド(デュラクシ-ル)を倒す事", "페일로드(듀락실) 격파"),
    ("ヴォルクルスを倒す事", "볼쿠르스격파"),
    ("移動要塞を倒す事", "이동 요새 격파"),
    ("敵のせん滅", "적 섬멸"),
    ("敵の全滅", "적 전멸"),
    ("味方の全滅", "아군 전멸"),
]

REPLACEMENTS = {
    "SECOND/2_SCE.BIN": _CONDITIONS,
    "THIRD/3_SCE.BIN": _CONDITIONS,
    "EX/E_SCE.BIN": [
        # 작전목적(승리조건). 시나리오 헤더 레코드 안이라 길이를 못 바꾼다.
        ("フェイルロ-ド(デュラクシ-ル)を倒す事", "페일로드(듀락실) 격파"),
        ("ヴォルクルスを倒す事", "볼쿠르스격파"),
        ("移動要塞を倒す事", "이동 요새 격파"),
        ("敵のせん滅", "적 섬멸"),
        ("敵の全滅", "적 전멸"),
    ],
    "THIRD/THIRD.WAR": [
        ("セ-ブ終了しました。ゲ-ムを続けますか?", "저장 완료. 계속할까요?"),
        ("セ-ブします。よろしいですか?", "저장할까요?"),
        ("ボ-ナス経験値", "보너스 EXP"),
        # '×' 는 한글 폰트에 없다 — 뒤의 '×2' 는 원문 그대로 둔다.
        ("残りの精神ポイント", "남은 정신 P"),
        ("味方の全滅", "아군 전멸"),
    ],
}


# 이미 한글인데 문구/자리를 손봐야 하는 것 — (현재 한국어, 바꿀 한국어).
# 길이가 늘면 실패시키고, 짧아지면 빈칸으로 메운다.
KO_FIXUPS = {
    "THIRD/THIRD.WAR": [
        # 출격 화면 상단 — 두 라벨이 붙어 버린 것을 떼어 놓는다 (제보 #6)
        ("출격유닛선택남은", "출격 유닛 남음"),
    ],
}

# 바이트 그대로 바꿔야 하는 것 — (찾을 바이트, 바꿀 바이트). 길이가 같아야 한다.
#   * `FC dx dy` 는 커서 이동이다. 원문은 첫 줄 16칸 자리에 숫자를 찍었는데
#     한국어 문구가 짧아져 그 자리가 글자 위로 왔다(제보 #5). 숫자를 한국어
#     빈칸(10칸)으로 옮기고, 바로 뒤 이동량을 같은 만큼 되돌려 '예/아니오'
#     위치는 그대로 둔다.
#   * 숫자 뒤의 글리프 0x1E1(機)은 번역이 안 돼 깨져 보인다 -> '기' (제보 #6)
BYTE_FIXUPS = {
    "THIRD/THIRD.WAR": [
        ("fc 10 fc f8 82 fc fe 05", "fc 0a fc f8 82 fc 04 05"),
        ("f8 82 ec e1", None),          # None = 뒤 2바이트를 '기' 로 (아래에서 처리)
    ],
}


def _byte_fixups(buf, name, ko_tab, log):
    n = 0
    for pat, rep in BYTE_FIXUPS.get(name, []):
        src = bytes.fromhex(pat)
        if rep is None:
            dst = src[:2] + _enc("기", ko_tab)
        else:
            dst = bytes.fromhex(rep)
        assert len(dst) == len(src), f"{name}: 바이트 길이가 다르다 {pat}"
        at = buf.find(src)
        while at >= 0:
            buf[at:at + len(src)] = dst
            n += 1
            at = buf.find(src, at + len(src))
    return n


def apply(files: dict, log=print) -> int:
    jp_tab = _jp_index()
    ko_tab = C.load_safe_glyph_map()
    total = 0
    for name, pairs in REPLACEMENTS.items():
        if name not in files:
            continue
        buf = bytearray(files[name])
        n = 0
        for jp, ko in sorted(pairs, key=lambda p: -len(p[0])):
            src = _enc(jp, jp_tab)
            dst = _enc(ko, ko_tab)
            if len(dst) > len(src):
                raise SystemExit(f"{name}: '{ko}' 가 '{jp}' 보다 {len(dst)-len(src)}바이트 깁니다")
            dst = dst + bytes(len(src) - len(dst))      # 남는 만큼 빈칸
            at = buf.find(src)
            while at >= 0:
                buf[at:at + len(src)] = dst
                n += 1
                at = buf.find(src, at + len(src))
        if n:
            files[name] = bytes(buf)
            log(f"  {name}: 원문 잔재 {n}곳 제자리 교체")
            total += n

    for name in set(list(KO_FIXUPS) + list(BYTE_FIXUPS)):
        if name not in files:
            continue
        buf = bytearray(files[name])
        n = 0
        for old, new in KO_FIXUPS.get(name, []):
            src, dst = _enc(old, ko_tab), _enc(new, ko_tab)
            if len(dst) > len(src):
                raise SystemExit(f"{name}: '{new}' 가 '{old}' 보다 깁니다")
            dst += bytes(len(src) - len(dst))
            at = buf.find(src)
            while at >= 0:
                buf[at:at + len(src)] = dst
                n += 1
                at = buf.find(src, at + len(src))
        n += _byte_fixups(buf, name, ko_tab, log)
        if n:
            files[name] = bytes(buf)
            log(f"  {name}: UI 문구·자리 {n}곳 손질")
            total += n
    return total


if __name__ == "__main__":
    from pathlib import Path
    fin = _P.BUILD / "final"
    files = {k: (fin / k.replace("/", "_")).read_bytes() for k in REPLACEMENTS
             if (fin / k.replace("/", "_")).exists()}
    print(f"교체 {apply(files)}곳")
