# -*- coding: utf-8 -*-
"""제2차·제3차·EX 한글패치 전면 재검증 (CB + 단독판).

검사 항목
  A) 잔여 일본어 — 화면에 나오는 미번역 레코드 (가나/한자 비율 기준)
  B) 폭/줄수 초과 — 시나리오 대사(폭18·3줄) / 전투·사망 대사(폭40)
  C) 무결성 — 깨진 글리프(도너 회수 슬롯 참조), 폰트 범위 밖, 스테일 포인터,
     레코드 종결자

렌더러 규칙(역분석 확정)
  * 글리프 advance: index<0x101 → 1, 아니면 phase0=1/phase1=2 + phase 토글
  * F6=개행(phase 리셋), F7=페이지/컬럼폭, FF=종결
  * 시나리오 대사 박스 폭 18, 페이지당 3줄
  * 전투/사망 대사 폭 40 (F7 없으면 X≥40 래핑), 사실상 1~2줄
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
import sys, math, json, re, struct
from pathlib import Path

R = str(_P.WORK)
sys.path.insert(0, f"{R}/tools")
from extract_psx_iso import RawMode2Image, read_tree
from second_translation_codec import (glyph_advance, load_safe_glyph_map,
                                      add_extra_glyph_mapping)
import analyze_sce_relocation as ASR
from analyze_sce_relocation import TEXT_POINTER_OPCODES
from analyze_second_message_archives import parse_bmess

SEC, UDO, UDS = 2352, 24, 2048
ARG = {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0, 0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1}
CB = f"{R}/test_build/third_full/Super Robot Taisen Complete Box Korean v0.10.7 (Track 1).bin"

_mp = json.load(open(f"{R}/research/srwcb_embedded_font_mapping_reviewed.json", encoding="utf-8"))
JP = {r["glyph_index"]: (r.get("character") or "") for r in _mp["rows"]}
PINNED = ['×', '…', '↑', '→', '↓', '□', '△', '○', '릭', '응']
EX15 = PINNED + ['맀', '뿠', '삤', '읏', '햣']
JPRE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
GB, GC = 32, 2816


def ko_table(extras):
    gm = add_extra_glyph_mapping(load_safe_glyph_map(), extras)
    t = {}
    for i, ch in JP.items():
        if i < 0x101 and ch:
            t[i] = ch
    for ch, i in gm.items():
        t.setdefault(i, ch)
    return t


def read_iso(img, path, cache={}):
    key = (img,)
    if key not in cache:
        with RawMode2Image(Path(img)) as m:
            _, E = read_tree(m)
        cache[key] = {e.path.strip("/"): e for e in E}
    e = cache[key][path]
    b = bytearray()
    with open(img, "rb") as f:
        for i in range(math.ceil(e.size / UDS)):
            f.seek((e.lba + i) * SEC)
            b += f.read(SEC)[UDO:UDO + UDS]
    return bytes(b[:e.size])


def read_lba(img, lba, size):
    b = bytearray()
    with open(img, "rb") as f:
        for i in range(math.ceil(size / UDS)):
            f.seek((lba + i) * SEC)
            b += f.read(SEC)[UDO:UDO + UDS]
    return bytes(b[:size])


def toks(buf, s, e=None):
    """(offset, nbytes, kind, glyph) kind: g/c/end"""
    p = s
    e = e if e is not None else len(buf)
    while p < e:
        x = buf[p]
        if x == 0xFF:
            yield (p, 1, 'end', None); return
        if x < 0xEB:
            yield (p, 1, 'g', x); p += 1
        elif x < 0xF6:
            if p + 1 >= e:
                return
            yield (p, 2, 'g', ((x - 0xEB) << 8) | buf[p + 1]); p += 2
        else:
            n = 1 + ARG.get(x, 0)
            yield (p, n, 'c', x); p += n


def rec_end(buf, s):
    for off, n, k, g in toks(buf, s):
        if k == 'end':
            return off + 1
    return s


def decode(buf, s, e, table):
    out = []
    for off, n, k, g in toks(buf, s, e):
        if k == 'end':
            break
        if k == 'g':
            out.append(table.get(g, f"[{g:x}]"))
        elif g == 0xF6:
            out.append("/")
        elif g == 0xF7:
            out.append("¶")
    return "".join(out)


def line_widths(buf, s, e):
    """(줄별 advance 리스트, 페이지별 줄수 리스트, 최대글리프)"""
    lines = []; pages = [1]; adv = 0; ph = 0; mx = 0
    for off, n, k, g in toks(buf, s, e):
        if k == 'end':
            break
        if k == 'c':
            if g == 0xF6:
                lines.append(adv); adv = 0; ph = 0; pages[-1] += 1
            elif g == 0xF7:
                lines.append(adv); adv = 0; ph = 0; pages.append(1)
            continue
        mx = max(mx, g)
        st, ph = glyph_advance(g, ph)
        adv += st
    lines.append(adv)
    return lines, pages, mx


def jp_ratio(text):
    body = re.sub(r"[/¶]|\[[0-9a-f]+\]", "", text)
    if not body:
        return 0, 0
    return len(JPRE.findall(body)), len(body)


def sce_records(data):
    """[(scenario_index, ordinal, start, end)]"""
    out = []
    for s in ASR.parse_scenarios(data):
        for i, r in enumerate(s.records):
            out.append((s.index, i, r.start, r.end))
    return out


def bmess_records(data):
    """[(block, target, abs_start, abs_end)] — 참조되는(=화면에 나오는) 것만"""
    ar = parse_bmess(data)
    out = []; seen = set()
    for blk in ar.blocks:
        for tgt in blk.text_references:
            rec = blk.text_records[tgt]
            s = blk.file_start + 15 + rec.start
            if s in seen:
                continue
            seen.add(s)
            out.append((blk.index, tgt, s, blk.file_start + 15 + rec.end))
    return out


def dead_records(data):
    """표(선두 u32 배열)에서 짝수 인덱스=레코드 시작"""
    tb = struct.unpack_from("<I", data, 0)[0]
    out = []
    for i in range(0, tb // 4, 2):
        t = i * 4 + struct.unpack_from("<I", data, i * 4)[0]
        if 0 < t < len(data):
            out.append((i // 2, t, rec_end(data, t)))
    return out


def live_glyphs_exe(war, tables, MH=None):
    """실행파일에서 살아있는 레코드가 쓰는 글리프"""
    from patch_second_exe_ui import parse_second_ui_vm_record as PV
    used = set()
    for ptr, cnt in tables:
        for k in range(cnt):
            f = ptr + 4 + 4 * k
            t = f + struct.unpack_from("<i", war, f)[0]
            if not (0x800 <= t < len(war)):
                continue
            for off, n, kk, g in toks(war, t, rec_end(war, t)):
                if kk == 'g':
                    used.add(g)
    if MH is not None:
        for k in range(107):
            f = MH + 4 + 4 * k
            t = f + struct.unpack_from("<i", war, f)[0]
            if not (0x800 <= t < len(war)):
                continue
            try:
                _e, tk = PV(bytes(war), t)
            except Exception:
                continue
            for x in tk:
                if x.kind == 'glyph':
                    r = x.raw
                    used.add(r[0] if len(r) == 1 else ((r[0] - 0xEB) << 8) | r[1])
    return used
