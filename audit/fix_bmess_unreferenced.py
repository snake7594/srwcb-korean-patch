# -*- coding: utf-8 -*-
"""전투 대사 아카이브의 **비참조 인용 레코드**를 제자리에서 한글로 바꾼다.

`BMESS2/3/4` 는 잎 노드(0x10/0x11)가 가리키는 레코드만 원장이 다룬다. 그런데
잎이 안 가리키는데도 게임이 읽어 화면에 내는 레코드가 아카이브마다 수십 개
있다(제2차·제3차 각 52개, EX 51개). 이쪽이 통째로 번역에서 빠져 **전투 중에
일본어가 그대로 나왔다**(2026-08-12 제보: 제3차 전투대사).

재배치기(`rebuild_bmess_repack`)는 비참조 레코드를 **바이트 그대로** 남긴다 —
누가 가리키는지 모르기 때문이다. 그래서 여기서는 **길이를 절대 바꾸지 않고**
제자리에 쓴다. 남는 자리는 종결자 앞을 반각 빈칸(0x00)으로 채운다(닫는 낫표
뒤라 화면에는 안 보인다).

    python audit/fix_bmess_unreferenced.py        # 맞는지 확인만
"""
import json
import os
import sys

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "srwcb_paths.py")):
    _d = _os = os.path.dirname(_d)
for _s in ("", "tools"):
    _p = os.path.join(_d, _s) if _s else _d
    if _p not in sys.path:
        sys.path.insert(0, _p)

import srwcb_paths as _P                                   # noqa: E402
import second_translation_codec as C                       # noqa: E402
from analyze_second_message_archives import parse_bmess    # noqa: E402

TABLE = _P.TRANSLATION / "bmess_unreferenced_ko.json"
FILES = ("BMESS2.BIN", "BMESS3.BIN", "BMESS4.BIN")
#: 전투 대사창 한 줄 폭
BATTLE_ADVANCE = 29

_CTRL = {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0,
         0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1}


def _jp_index():
    rows = json.loads((_P.WORK / "research" /
                       "srwcb_embedded_font_mapping_reviewed.json").read_text(encoding="utf-8"))["rows"]
    out = {}
    for r in rows:
        ch = r.get("character")
        if ch and ch not in out:
            out[ch] = r["glyph_index"]
    return out


def decode(buf, s, e, table):
    """레코드를 사람이 읽는 꼴로 — 반각 빈칸은 ' ', 제어는 [Fx]."""
    out, p = [], s
    while p < e:
        b = buf[p]
        if b == 0xFF:
            break
        if b < 0xEB:
            out.append(table.get(b, " ") if b else " ")
            p += 1
        elif b <= 0xF5:
            out.append(table.get(((b - 0xEB) << 8) | buf[p + 1], "?"))
            p += 2
        else:
            out.append(f"[{b:02X}]")
            p += 1 + _CTRL.get(b, 0)
    return "".join(out)


def encode(text, ko_tab):
    """`[F6]` 표시와 반각 빈칸을 그대로 살려 바이트로."""
    out = bytearray()
    i = 0
    while i < len(text):
        if text[i] == "[" and text[i + 1:i + 2] == "F" and text[i + 3:i + 4] == "]":
            out.append(int(text[i + 1:i + 3], 16))
            i += 4
            continue
        ch = text[i]
        if ch == " ":
            out.append(0x00)
        else:
            out += C.encode_glyph_index(ko_tab[ch])
        i += 1
    return bytes(out)


def line_advances(raw):
    """줄별 렌더러 advance."""
    outs, adv, ph, p = [], 0, 0, 0
    while p < len(raw):
        b = raw[p]
        if b == 0xFF:
            break
        if b < 0xEB:
            idx, n = b, 1
        elif b <= 0xF5:
            idx, n = ((b - 0xEB) << 8) | raw[p + 1], 2
        else:
            if b in (0xF6, 0xF7):
                outs.append(adv); adv = 0; ph = 0
            p += 1 + _CTRL.get(b, 0)
            continue
        st, ph = C.glyph_advance(idx, ph)
        adv += st
        p += n
    outs.append(adv)
    return outs


def targets(data):
    """(시작, 끝) — 비참조 인용 레코드."""
    out = []
    for b in parse_bmess(data).blocks:
        for _t, rec in sorted(b.unreferenced_quoted_records.items()):
            out.append((b.file_start + 15 + rec.start, b.file_start + 15 + rec.end))
    return out


def apply(files: dict, log=print, check_only=False) -> int:
    table = json.loads(TABLE.read_text(encoding="utf-8"))["records"]
    jp_tab = _jp_index()
    ko_tab = C.load_safe_glyph_map()
    jp_chars = {i: ch for ch, i in jp_tab.items()}
    total = 0
    problems = []
    for name in FILES:
        if name not in files:
            continue
        buf = bytearray(files[name])
        n = 0
        for s, e in targets(bytes(buf)):
            jp = decode(buf, s, e, jp_chars)
            ko = table.get(jp)
            if ko is None:
                continue
            enc = encode(ko, ko_tab)
            budget = e - s - 1                      # 종결자 한 칸은 남긴다
            if len(enc) > budget:
                problems.append(f"{name} @{s}: {len(enc)}B > {budget}B  {ko}")
                continue
            over = [a for a in line_advances(enc) if a > BATTLE_ADVANCE]
            if over:
                problems.append(f"{name} @{s}: 폭 {over} > {BATTLE_ADVANCE}  {ko}")
                continue
            if not check_only:
                buf[s:e] = enc + bytes(budget - len(enc)) + b"\xff"
            n += 1
        if n and not check_only:
            files[name] = bytes(buf)
        if n:
            log(f"  {name}: 비참조 전투 대사 {n}곳 한글화")
        total += n
    if problems:
        for p in problems:
            print(f"  [안 맞음] {p}")
        raise SystemExit(f"비참조 전투 대사 {len(problems)}건이 자리에 안 맞습니다")
    return total


def main():
    files = {k: (_P.EXTRACTED / k).read_bytes() for k in FILES
             if (_P.EXTRACTED / k).exists()}
    print(f"맞춰 본 결과: {apply(files, check_only=True)}곳 적용 가능")


if __name__ == "__main__":
    main()
