# -*- coding: utf-8 -*-
"""단독판(별매 CD) 이식을 위한 작업 폴더를 준비한다.

컴플리트 박스가 아니라 **따로 나온 CD** 세 장에도 같은 한글패치를 얹을 수 있다.
그 이미지는 각자 갖고 있어야 하므로 환경변수로 알려 준다.

    set SRWCB_SRW2_IMG=D:\\games\\Super Robot Taisen 2.img
    set SRWCB_SRW3_BIN=D:\\games\\Dai 3 Ji Super Robot Taisen.bin
    set SRWCB_SRWEX_IMG=D:\\games\\Super Robot Taisen EX (J).img
    python setup_standalone.py

하는 일: 작업 폴더에 이미지를 연결하고, 이식에 필요한 레트일 실행파일을 뽑고,
저장소가 가진 델타 맵(레트일 CB ↔ 단독판 오프셋 대응)을 제자리에 둔다.
"""
import argparse
import math
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
import srwcb_paths as P  # noqa: E402

sys.path.insert(0, str(P.TOOLS))
from extract_psx_iso import RawMode2Image, read_tree  # noqa: E402

SEC, UDO, UDS = 2352, 24, 2048

GAMES = {
    "srw2": dict(env="SRWCB_SRW2_IMG", img=P.SRW2_IMG,
                 name="Super Robot Taisen 2.img", exe="SLPS_024.06",
                 maps=[("standalone/delta_map.json", "delta_map.json"),
                       ("standalone/music_pointer_fields.json", "music_pointer_fields.json")]),
    "srw3": dict(env="SRWCB_SRW3_BIN", img=P.SRW3_BIN,
                 name="Dai 3 Ji Super Robot Taisen.bin", exe="SLPS_025.30",
                 maps=[("standalone3/delta_map3.json", "delta_map3.json")]),
    "srwex": dict(env="SRWCB_SRWEX_IMG", img=P.SRWEX_IMG,
                  name="Super Robot Taisen EX (J).img", exe="SLPS_025.29",
                  maps=[("standalone_ex/delta_map_ex.json", "delta_map_ex.json")]),
}


def link_or_copy(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)          # 같은 볼륨이면 하드링크 (수 GB 를 두 번 안 쓴다)
    except OSError:
        shutil.copyfile(src, dst)


def pull(img: Path, name: str, out: Path) -> int:
    with RawMode2Image(img) as m:
        _, entries = read_tree(m)
    e = next(x for x in entries if x.path.strip("/").split("/")[-1] == name)
    buf = bytearray()
    with open(img, "rb") as f:
        for i in range(math.ceil(e.size / UDS)):
            f.seek((e.lba + i) * SEC)
            buf += f.read(SEC)[UDO:UDO + UDS]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bytes(buf[:e.size]))
    return e.size


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=sorted(GAMES), help="한 판만 준비")
    a = ap.parse_args()
    done = 0
    for key, cfg in GAMES.items():
        if a.only and key != a.only:
            continue
        src = cfg["img"]
        if not (src and Path(src).exists()):
            print(f"[건너뜀] {key}: {cfg['env']} 가 없습니다")
            continue
        root = P.WORK / key
        link_or_copy(Path(src), root / cfg["name"])
        n = pull(root / cfg["name"], cfg["exe"], root / "extracted" / cfg["exe"])
        for rel, dst in cfg["maps"]:
            shutil.copyfile(REPO / rel, root / "extracted" / dst)
        print(f"{key}: {cfg['name']} 연결, {cfg['exe']} {n:,}B 추출, "
              f"맵 {len(cfg['maps'])}개")
        done += 1
    if not done:
        raise SystemExit(
            "준비된 단독판이 없습니다. 환경변수로 이미지 경로를 지정하세요:\n  "
            + "\n  ".join(f"{c['env']}" for c in GAMES.values()))
    print(f"\n완료. 이제 `python build_all.py --only 9` 로 단독판을 만듭니다.")


if __name__ == "__main__":
    main()
