# -*- coding: utf-8 -*-
"""제2차(SECOND.WAR)에 이미 있는 번역을 다른 실행파일로 그대로 옮긴다.

네 실행파일(SECOND / THIRD / EX / TR)은 같은 엔진에서 나와 **공용 문자열이
바이트까지 같은 자리에** 들어 있다. 그런데 번역은 제2차부터 해 왔기 때문에
제2차만 한글이고 나머지는 원문 그대로 남은 자리가 있었다.

폰트가 한글로 바뀐 뒤로 그런 자리는 화면에서 **뜻 모를 한글**로 나온다
(예고편 대사 풀 등). 2026-08-10 제보 #7 을 쫓다가 발견했다.

옮기는 조건 — 하나라도 어긋나면 건드리지 않는다:
  * 레트일 바이트가 두 파일에서 **완전히 같다**(같은 자리, 같은 내용)
  * 대상 파일은 아직 레트일 그대로다(= 번역 안 됨)
  * 레트일 내용이 **일본어 문장**이다(바이트코드·기호는 제외)
  * 제2차 한글본의 길이가 같다(제자리 교체라 항상 같다)
"""
import os
import re
import sys

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "srwcb_paths.py")):
    _d = os.path.dirname(_d)
for _s in ("", "tools", "audit"):
    _p = os.path.join(_d, _s) if _s else _d
    if _p not in sys.path:
        sys.path.insert(0, _p)

import srwcb_paths as _P        # noqa: E402
import audit_all as A           # noqa: E402

JP = re.compile(r"[぀-ヿ一-鿿]")
MIN_BYTES = 6
TARGETS = ["THIRD/THIRD.WAR", "EX/EX.WAR", "TR.WAR"]
SOURCE = "SECOND/SECOND.WAR"


def _records(buf):
    """FF 로 끊은 레코드. 제어코드 인자 안의 FF 는 종료자가 아니다."""
    out = []
    start = i = 0
    n = len(buf)
    while i < n:
        b = buf[i]
        if b == 0xFF:
            out.append((start, i))
            i += 1
            start = i
            continue
        if b < 0xEB:
            i += 1
        elif b <= 0xF5:
            i += 2
        else:
            i += 1 + A.ARG.get(b, 0)
    return out


def apply(files: dict, log=print) -> int:
    jt = {i: ch for i, ch in A.JP.items() if ch}
    src_ko = files[SOURCE]
    src_jp = (_P.EXTRACTED / SOURCE).read_bytes()
    total = 0
    # 내용으로도 찾을 수 있게: 제2차 원문 레코드가 **딱 한 번** 나오고 그 자리를
    # 번역했다면, 다른 실행파일의 같은 내용도 그 번역으로 바꾼다. 실행파일마다
    # 크기·배치가 달라(EX/TR) 오프셋만으로는 안 걸리는 것들이 여기서 잡힌다.
    by_text = {}
    for sa, sb in _records(src_jp):
        if sb - sa < MIN_BYTES:
            continue
        key = src_jp[sa:sb]
        if key in by_text:
            by_text[key] = None                     # 중복이면 쓰지 않는다
        elif src_ko[sa:sb] != key:
            by_text[key] = src_ko[sa:sb]

    for name in TARGETS:
        if name not in files:
            continue
        dst_ko = bytearray(files[name])
        dst_jp = (_P.EXTRACTED / name).read_bytes()
        if len(dst_jp) != len(dst_ko):
            continue
        n = 0
        for a, b in _records(dst_jp):
            if b - a < MIN_BYTES:
                continue
            if dst_ko[a:b] != dst_jp[a:b]:          # 이미 번역됨
                continue
            same_place = (b <= len(src_jp) and src_jp[a:b] == dst_jp[a:b]
                          and src_ko[a:b] != src_jp[a:b])
            ko = src_ko[a:b] if same_place else by_text.get(bytes(dst_jp[a:b]))
            if not ko or len(ko) != b - a:
                continue
            try:
                txt = A.decode(dst_jp, a, b, jt)
            except Exception:
                continue
            letters = [c for c in txt if not c.isspace()]
            if not letters or len(JP.findall(txt)) / len(letters) < 0.5:
                continue
            dst_ko[a:b] = ko
            n += 1
        if n:
            files[name] = bytes(dst_ko)
            log(f"  {name}: 제2차에서 {n}개 레코드 이식")
            total += n
    return total


if __name__ == "__main__":
    fin = _P.BUILD / "final"
    keys = [SOURCE] + TARGETS
    files = {k: (fin / k.replace("/", "_")).read_bytes() for k in keys
             if (fin / k.replace("/", "_")).exists()}
    print(f"이식 {apply(files)}건")
