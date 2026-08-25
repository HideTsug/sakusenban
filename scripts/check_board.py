#!/usr/bin/env python3
"""手書き経路（経路B）で作った盤 HTML のレイアウト不変条件を機械検証する。

renderer 経路は render.py の validate() が守るが、手書き盤には検証が無かった。
配布先での実走から「レーン×行のセル数と connector 位置を目視で数えるのは
現実的でない」と還流された検証スニペットを、ツールとして取り込んだもの。
還流元スニペットからの修正2点: connector の走査を各 flowgrid 内に限定
（異なるレーン数の盤が混在すると誤 NG になっていた）、行のセル数を
開始タグの出現順で数える（lanecell 内の入れ子 div に影響されない）。

検査項目:
  - --lanes:N とレーンヘッダ数の一致
  - 各行の .lanecell 数が N と一致
  - connector の --from/--to がレーン中心 (i+0.5)/N*100 に一致、
    --left=min(from,to)、--width=|from-to|

使い方: python3 scripts/check_board.py <盤.html> [...]
全項目 OK なら exit 0、NG があれば exit 1。
"""
import pathlib
import re
import sys


def check_file(path: str) -> bool:
    h = pathlib.Path(path).read_text(encoding="utf-8")
    ok = True

    def report(label: str, good: bool) -> None:
        nonlocal ok
        ok &= good
        print(f"  {label}: {'OK' if good else 'NG'}")

    chunks = [c for c in re.split(r'(?=<div class="flowgrid")', h)
              if c.startswith('<div class="flowgrid')]
    if not chunks:
        print("  flowgrid が見つからない: NG")
        return False
    for gi, chunk in enumerate(chunks, 1):
        opening = chunk.split(">", 1)[0]
        m = re.search(r"--lanes:\s*(\d+)", opening)
        if not m:
            report(f"grid {gi}: --lanes 宣言なし", False)
            continue
        n = int(m.group(1))
        body = chunk.split("</section>", 1)[0]

        heads = len(re.findall(r'<div class="lanehead', body))
        report(f"grid {gi}: lanes={n} laneheads={heads}", heads == n)

        # 行 = lanecell 開始タグの連続run（boxbody/connector が行の区切り）
        seq = re.findall(r'<div class="(lanecell|boxbody|connector)', body)
        rows, run = [], 0
        for kind in seq:
            if kind == "lanecell":
                run += 1
            elif run:
                rows.append(run)
                run = 0
        if run:
            rows.append(run)
        for i, cells in enumerate(rows, 1):
            report(f"grid {gi} row {i}: cells={cells} (期待 {n})", cells == n)

        centers = [round((i + 0.5) / n * 100, 3) for i in range(n)]
        for c in re.finditer(
            r"--from:([\d.]+)%;--to:([\d.]+)%;--left:([\d.]+)%;--width:([\d.]+)%",
            body,
        ):
            f, t, l, w = map(float, c.groups())
            geom = abs(l - min(f, t)) < 0.01 and abs(w - abs(f - t)) < 0.01
            onlane = all(
                any(abs(v - ctr) < 0.01 for ctr in centers) for v in (f, t)
            )
            report(f"grid {gi} connector {f} -> {t}", geom and onlane)
    return ok


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    all_ok = True
    for path in argv[1:]:
        print(path)
        all_ok &= check_file(path)
    print("RESULT:", "OK" if all_ok else "NG")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
