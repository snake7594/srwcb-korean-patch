# -*- coding: utf-8 -*-
"""inject_ex_ui.py -> inject_tr_ui.py 기계 변환.

손으로 500줄을 다시 쓰면 검증된 로직(스팬 폭 보존·앵커 가드·중간 패딩 등)을
잃기 쉽다. 그래서 **필요한 치환만** 적용하고, 치환이 전부 정확히 1회씩
일어났는지 확인한다. 하나라도 어긋나면 즉시 멈춘다.
"""
import os, sys

SP = os.path.dirname(os.path.abspath(__file__))
SRC = f"{SP}/inject_ex_ui.py"
DST = f"{SP}/inject_tr_ui.py"

REPL = [
    # ---- 헤더/입출력 ----
    ('"""EX.WAR UI injection — inject_third_ui5.py의 EX 적응판.',
     '"""TR.WAR(트레이닝 모드) UI injection — inject_ex_ui.py의 TR 적응판.\n\n'
     'TR.WAR 은 EX.WAR 과 같은 엔진이고 UI 테이블 본문은 헤더 4바이트만 빼면\n'
     '바이트까지 동일하다(실측). 오프셋만 다르므로 EX 에서 폭 검증된 번역을\n'
     '그대로 쓴다. 이 파일은 make_tr_injector.py 가 inject_ex_ui.py 에서\n'
     '기계 변환한 것이다 — 직접 고치지 말고 원본을 고친 뒤 다시 생성할 것.'),
    ('SRC = f"{ROOT}/test_build/ex_full/runtime/EX/EX.WAR"',
     'SRC = f"{ROOT}/test_build/ex_full/font_extracted/TR.WAR"'),
    ('assert war[:8] == b"PS-X EXE" and N == 0x124000, "SRC가 EX.WAR pre-inject 빌드가 아닙니다!"',
     'assert war[:8] == b"PS-X EXE" and N == 0x123800, "SRC가 TR.WAR pre-inject 빌드가 아닙니다!"'),
    ('RETAIL = open(f"{ROOT}/extracted/EX/EX.WAR", "rb").read()',
     'RETAIL = open(f"{ROOT}/extracted/TR.WAR", "rb").read()'),
    ('assert war[0x974b:0x974b + 76] == RETAIL[0x974b:0x974b + 76], (\n'
     '    "SRC가 이미 주입된 EX.WAR입니다 — build_ex_full.py를 먼저 실행해 pre-inject를 재생성하세요")',
     'assert war[0x9747:0x9747 + 76] == RETAIL[0x9747:0x9747 + 76], (\n'
     '    "SRC가 이미 주입된 TR.WAR입니다 — font_extracted/TR.WAR 을 다시 만드세요")'),
    # ---- 추가 레코드 모듈 ----
    ('import ex_extra_records as _XR0', 'import tr_extra_records as _XR0'),
    ('import ex_extra_records as _XR', 'import tr_extra_records as _XR'),
    # ---- 폰트 오프셋 ----
    ('font_off = next(v for k, v in FONT_EXE_LAYOUT.items() if str(k).replace("\\\\", "/").endswith("EX/EX.WAR"))',
     'font_off = next(v for k, v in FONT_EXE_LAYOUT.items() if str(k).replace("\\\\", "/") == "TR.WAR")'),
    # ---- 테이블 오프셋 ----
    ('TABLES = [("terrain_names", 0xbcb4, 144, 0xc184), ("spirit_commands", 0xc184, 94, 0xc850),\n'
     '          ("enhancement_parts", 0xc850, 64, 0xcbcc), ("weapon_names", 0xcbcc, 1344, 0xf258),\n'
     '          ("pilot_skills", 0xf258, 52, 0xf510), ("unit_abilities", 0xf510, 22, 0xf61c),\n'
     '          ("scenario_titles", 0xf61c, 192, 0xfc04), ("pilot_short_names", 0x10778c, 400, 0x1081bc),\n'
     '          ("pilot_full_names", 0x1081bc, 400, 0x108f64), ("unit_names", 0x108f64, 448, 0x10a000)]',
     '# TR 오프셋 (EX 대비 앞쪽 -8 / pilot·unit 계열 -0x24). verify_tr_offsets 가 검증한다.\n'
     'TABLES = [("terrain_names", 0xbcac, 144, 0xc17c), ("spirit_commands", 0xc17c, 94, 0xc848),\n'
     '          ("enhancement_parts", 0xc848, 64, 0xcbc4), ("weapon_names", 0xcbc4, 1344, 0xf250),\n'
     '          ("pilot_skills", 0xf250, 52, 0xf508), ("unit_abilities", 0xf508, 22, 0xf614),\n'
     '          ("scenario_titles", 0xf614, 192, 0xfbfc), ("pilot_short_names", 0x107768, 400, 0x108198),\n'
     '          ("pilot_full_names", 0x108198, 400, 0x108f40), ("unit_names", 0x108f40, 448, 0x109fdc)]'),
    ('MH, MC = 0x188C4, 107', 'MH, MC = 0x188BC, 107'),
    # ---- 명령 메뉴 ----
    ('assert len(cmd_ko) == 76 and war[0x974b + 76] == 0xFF, "명령 메뉴 레이아웃 이상"\n'
     'war[0x974b:0x974b + 76] = cmd_ko\n'
     'print("  명령 메뉴 @0x974b (76B) 한글")',
     'assert len(cmd_ko) == 76 and war[0x9747 + 76] == 0xFF, "명령 메뉴 레이아웃 이상"\n'
     'war[0x9747:0x9747 + 76] = cmd_ko\n'
     'print("  명령 메뉴 @0x9747 (76B) 한글")'),
    # ---- 정렬 오버라이드: TR 전용 파일 추가 ----
    ('_eo = f"{_P.REPO}/ex-ui/data/ex_align_overrides.json"\n'
     'if os.path.exists(_eo): _EX_OVR = json.load(open(_eo, encoding="utf-8"))',
     'for _eo in (f"{_P.REPO}/ex-ui/data/ex_align_overrides.json", f"{_P.REPO}/tr-ui/tr_align_overrides.json"):\n'
     '    if os.path.exists(_eo):\n'
     '        for _k, _v in json.load(open(_eo, encoding="utf-8")).items():\n'
     '            _EX_OVR.setdefault(_k, []).extend(_v if isinstance(_v, list) else [_v])'),
    # ---- 맵 라벨 델타 ----
    ('LABEL_DELTA = 0x484      # EX (THIRD는 0x2dc) — source_hex 유일매칭으로 실측',
     'LABEL_DELTA = 0x47c      # TR (EX는 0x484, THIRD는 0x2dc) — source_hex 유일매칭으로 실측'),
    # ---- 출력 ----
    ('out = f"{ROOT}/test_build/ex_full/runtime/EX/EX.WAR"',
     'out = f"{ROOT}/test_build/tr_full/runtime/TR.WAR"\n'
     'os.makedirs(os.path.dirname(out), exist_ok=True)'),
    ('json.dump({"extras": EXTRAS, "donor_used": arena_used, "assets": manifest},\n'
     '          open(f"{_P.BUILD}/ex/ex_inject_manifest.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)',
     'os.makedirs(f"{SP}/tr", exist_ok=True)\n'
     'json.dump({"extras": EXTRAS, "donor_used": arena_used, "assets": manifest},\n'
     '          open(f"{SP}/tr/tr_inject_manifest.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)'),
]

# 외래 레코드 블록은 통째로 교체(위치를 델타가 아니라 내용 검색으로 잡는다)
FOREIGN_START = "import pickle as _pickle"
FOREIGN_END = 'print(f"  외래 맵/시스템 레코드 {_fn}개 제자리 번역 (중간패딩)")'
FOREIGN_NEW = '_XR.patch_foreign_records(war, RETAIL, enc_ko)'


def main():
    s = open(SRC, encoding="utf-8").read()
    for old, new in REPL:
        n = s.count(old)
        if n != 1:
            print(f"!! 치환 대상이 {n}회 발견됨 (1회여야 함):\n---\n{old[:160]}\n---")
            sys.exit(1)
        s = s.replace(old, new)

    a = s.index(FOREIGN_START)
    b = s.index(FOREIGN_END) + len(FOREIGN_END)
    s = s[:a] + FOREIGN_NEW + s[b:]

    open(DST, "w", encoding="utf-8").write(s)
    print(f"WROTE {DST}  ({len(s.splitlines())}줄)")
    # 남은 EX 흔적 점검
    for bad in ("EX.WAR", "ex_extra_records", "0x974b", "0x188C4", "ex_inject_manifest"):
        if bad in s:
            for i, ln in enumerate(s.splitlines(), 1):
                if bad in ln and not ln.strip().startswith("#"):
                    print(f"  ?? {bad} 잔존: {i}: {ln.strip()[:100]}")


if __name__ == "__main__":
    main()
