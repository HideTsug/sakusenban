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
from html.parser import HTMLParser


class GridParser(HTMLParser):
    """Keep graph checks inside the actual flowgrid, including nested box content."""

    def __init__(self):
        super().__init__()
        self.stack = [{"tag": "", "attrs": {}, "children": []}]
        self.grids = []

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "children": []}
        self.stack[-1]["children"].append(node)
        if "flowgrid" in (node["attrs"].get("class") or "").split():
            self.grids.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                break


def check_graph(grid, gi, report):
    def has(node, cls):
        return cls in (node["attrs"].get("class") or "").split()

    def style(node):
        return {key.strip(): value.strip()
                for part in (node["attrs"].get("style") or "").split(";") if ":" in part
                for key, value in [part.split(":", 1)]}

    def descendants(node):
        for child in node["children"]:
            if has(child, "flowgrid"):
                continue
            yield child
            yield from descendants(child)

    declaration = style(grid).get("--lanes", "").strip()
    if not declaration.isdigit() or not 1 <= int(declaration) <= 6:
        report(f"grid {gi}: --lanes 宣言不正", False)
        return
    n = int(declaration)
    heads = sum(has(child, "lanehead") for child in grid["children"])
    report(f"grid {gi}: lanes={n} laneheads={heads}", heads == n)
    rows = {}
    for child in grid["children"]:
        if has(child, "lanecell"):
            row = style(child).get("grid-row", "").strip()
            rows.setdefault(row, []).append(child)
        elif has(child, "boxbody"):
            # A body also witnesses its box row, so deleting a whole cell row fails.
            row = style(child).get("grid-row", "").strip().replace("bd-", "bx-", 1)
            rows.setdefault(row, [])
    for row, cells in rows.items():
        valid_row = bool(re.fullmatch(r"bx-[1-9]\d*", row))
        report(f"grid {gi} row {row}: cells={len(cells)} (期待 {n})", valid_row and len(cells) == n)
    nodes = list(descendants(grid))
    ids = {node["attrs"].get("id") for node in nodes if has(node, "flowbox")}
    ids.discard(None)
    box_rows = {node["attrs"]["id"]: row.removeprefix("bx-")
                for row, cells in rows.items() for cell in cells
                for node in descendants(cell) if has(node, "flowbox") and node["attrs"].get("id")}
    exit_counts = {style(child).get("grid-row"): sum(
                       has(item, "exit") for group in descendants(child) if has(group, "exits")
                       for item in group["children"])
                   for child in grid["children"] if has(child, "exitcell")}
    stems = [node for node in nodes if has(node, "edge") and has(node, "stem")]
    adjacent_mains = [node for node in nodes if has(node, "connector")
                      and node["attrs"].get("data-edge-type") == "main"
                      and node["attrs"].get("data-route") == "adjacent"]
    for node in nodes:
        attrs = node["attrs"]
        if has(node, "edge"):
            source, target = attrs.get("data-edge-from"), attrs.get("data-edge-to")
            report(f"grid {gi} edge {source} -> {target}", source in ids and target in ids)
        if has(node, "exit"):
            links = ([node] if node["tag"] == "a" else
                     [child for child in descendants(node) if child["tag"] == "a"])
            report(f"grid {gi}: exit link exists", bool(links))
            for link in links:
                href = link["attrs"].get("href") or ""
                open_box = link["attrs"].get("data-open-box")
                report(f"grid {gi} exit {href} (data-open-box={open_box})",
                       href.startswith("#") and href[1:] in ids and open_box == href[1:])
        if has(node, "connector"):
            values = style(node)
            try:
                raw = [values[key].strip() for key in ("--from", "--to", "--left", "--width")]
                f, t, l, w = [float(value[:-1]) for value in raw if value.endswith("%")]
            except (KeyError, ValueError):
                report(f"grid {gi}: connector 幾何宣言不正", False)
                continue
            centers = [(i + 0.5) / n * 100 for i in range(n)]
            geom = abs(l - min(f, t)) < 0.01 and abs(w - abs(f - t)) < 0.01
            onlane = all(any(abs(v - ctr) < 0.01 for ctr in centers) for v in (f, t))
            report(f"grid {gi} connector {f} -> {t}", geom and onlane)
            source, target = attrs.get("data-edge-from"), attrs.get("data-edge-to")
            source_row, target_row = box_rows.get(source), box_rows.get(target)
            if (attrs.get("data-edge-type") != "main" or attrs.get("data-route") != "adjacent"
                    or f"ex-{source_row}" not in exit_counts):
                continue
            exit_count = exit_counts[f"ex-{source_row}"]
            matching = [stem for stem in stems if stem["attrs"].get("data-edge-from") == source
                        and stem["attrs"].get("data-edge-to") == target]
            expected = sum(connector["attrs"].get("data-edge-from") == source
                           and connector["attrs"].get("data-edge-to") == target
                           for connector in adjacent_mains)
            label = f"grid {gi} stem {source} -> {target}"
            report(f"{label}: count={len(matching)} (期待 {expected})", len(matching) == expected)
            for stem in matching:
                segments = [child for child in stem["children"] if has(child, "v")]
                report(f"{label}: v count={len(segments)} (期待 1)", len(segments) == 1)
                for segment in segments:
                    placement = style(segment)
                    row_span = [part.strip() for part in placement.get("grid-row", "").split("/")]
                    report(f"{label}: grid-row 両端", target_row is not None and
                           row_span == [f"ex-{source_row}", f"ch-{target_row}"])
                    try:
                        start, end = [int(part.strip()) for part in values["grid-column"].split("/")]
                        # Match rounded connector percentages to a lane before converting to grid lines.
                        lane = next(i for i, center in enumerate(centers) if abs(f - center) < 0.01)
                        column = start + lane * 2 + 1
                        col_start, col_end = [int(part.strip())
                                              for part in placement["grid-column"].split("/")]
                        aligned = (start > 0 and end - start == 2 * n
                                   and (col_start, col_end) == (column, column + 1))
                    except (KeyError, ValueError, StopIteration):
                        aligned = False
                    report(f"{label}: grid-column 両端・connector --from レーン中心", aligned)
                    expected_start = f"calc({exit_count - 1}*24px + 24px)"
                    report(f"{label}: --stem-start 最後の出口の底 (期待 {expected_start})",
                           exit_count > 0 and placement.get("--stem-start") == expected_start)
                    report(f"{label}: inline top なし",
                           all(key.lower() != "top" for key in placement))


def check_file(path: str) -> bool:
    h = pathlib.Path(path).read_text(encoding="utf-8")
    ok = True

    def report(label: str, good: bool) -> None:
        nonlocal ok
        ok &= good
        print(f"  {label}: {'OK' if good else 'NG'}")

    parser = GridParser()
    parser.feed(h)
    graphs = [grid for grid in parser.grids if grid["attrs"].get("data-flow-mode") == "graph"]
    for gi, grid in enumerate(graphs, 1):
        check_graph(grid, gi, report)

    chunks = [c for c in re.split(r'(?=<div class="flowgrid")', h)
              if c.startswith('<div class="flowgrid')]
    chunks = [chunk for chunk in chunks if not re.search(r'\bdata-flow-mode=[\"\']graph[\"\']', chunk.split(">", 1)[0])]
    if not chunks and not graphs:
        print("  flowgrid が見つからない: NG")
        return False
    for gi, chunk in enumerate(chunks, len(graphs) + 1):
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
