#!/usr/bin/env python3
"""sakusenban renderer — board manifest (structure) + state file → self-contained swimlane HTML.

usage: render.py <board.(yaml|yml|json)> <state.json> [fields.json] [options] > board.html
  board.yaml : structure manifest (lanes / streams / boxes / tasks / bundles).
               Schema: schema/sakusenban.schema.json
  state.json : task state list [{"number": N, "title": "...", "state": "OPEN"|"CLOSED"}, ...]
               With GitHub Issues as SSoT:
                 gh issue list --state all -L 200 --json number,title,state > state.json
               Without GitHub: maintain the same JSON by hand (local-file SSoT mode).
  fields.json: optional extra per-task fields, e.g. target dates from GitHub Projects:
                 gh project item-list <N> --owner <owner> --format json > fields.json
               Any {"items":[{"content":{"number":N}, "<field>": "YYYY-MM-DD"}]} shape works.

options:
  --stamp "YYYY-MM-DD HH:MM"  generation timestamp shown in the header (default: now)
  --today YYYY-MM-DD          reference date for countdowns (default: today)
  --assets DIR                directory containing style.css / board.js
                              (default: <repo>/assets next to this script)

Design (unchanged from the battle-tested originals):
- State SSoT is external (Issues or a local state file). CLOSED tasks render locked ("done").
- Checkboxes are the human's "I did this" report signal, stored in localStorage only.
  "Copy report" emits machine-readable text; the AI verifies against the SSoT, updates it,
  and regenerates this board. The generated HTML must never be hand-edited.
- "Unreported" = checked locally but still OPEN in the SSoT.
"""
import argparse
import datetime
import html
import json
import pathlib
import sys

OWNER_LABEL = {"ai": "AI", "human": "人間", "joint": "人間+相手"}
STATE_LABEL = {"done": "完了", "ready": "着手可", "blocked": "待ち"}
LANE_KINDS = ("human", "ai", "joint")
MIT_LICENSE = """MIT License

Copyright (c) 2026 Hideyuki Tsuganezawa (HideTsug)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""
LICENSE_COMMENT = f"<!--\n{MIT_LICENSE}\n-->"


def fail(msg):
    print(f"render.py: {msg}", file=sys.stderr)
    return 1


def load_manifest(path):
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            raise SystemExit(
                "render.py: PyYAML is required for YAML manifests "
                "(pip install pyyaml) — or use a .json manifest instead.")
        return yaml.safe_load(text)
    return json.loads(text)


def esc(s):
    return html.escape(str(s), quote=True)


def script_safe_json(value):
    return json.dumps(value).replace("<", r"\u003c")


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def validate(manifest):
    """Mechanical invariant checks. Returns a list of error strings."""
    errors = []
    meta = manifest.get("meta") or {}
    for key in ("title", "key"):
        if not meta.get(key):
            errors.append(f"meta.{key} is required")
    for key in ("issue_url_base", "board_url"):
        if key in meta and (not isinstance(meta[key], str)
                            or not meta[key].startswith(("http://", "https://"))):
            errors.append(f"meta.{key} must start with http:// or https://")
    flow = manifest.get("flow") or {}
    lanes = flow.get("lanes") or []
    if not lanes:
        errors.append("flow.lanes is required (1-4 lanes)")
    lane_keys = set()
    for lane in lanes:
        if lane.get("kind") not in LANE_KINDS:
            errors.append(f'lane {lane.get("key")}: kind must be one of {LANE_KINDS}')
        lane_keys.add(lane.get("key"))
    tasks = manifest.get("tasks") or []
    task_ids = []
    for task_index, task in enumerate(tasks):
        task_id = task.get("id")
        if is_int(task_id):
            task_ids.append(task_id)
        else:
            errors.append(f"tasks[{task_index}].id must be an int")
    dupes = {i for i in task_ids if task_ids.count(i) > 1}
    if dupes:
        errors.append(f"duplicate task id(s): {sorted(dupes)}")
    id_set = set(task_ids)
    for task_index, task in enumerate(tasks):
        for dep_index, dep in enumerate(task.get("deps", []) or []):
            dep_id = dep.get("id")
            if not is_int(dep_id):
                errors.append(f"tasks[{task_index}].deps[{dep_index}].id must be an int")
            elif dep_id not in id_set:
                errors.append(f'task {task.get("id")}: dep references unknown task {dep_id}')
    bundles = manifest.get("bundles") or {}
    for bkey, b in bundles.items():
        for field in ("items", "after_issues"):
            for item_index, item in enumerate(b.get(field, []) or []):
                if not is_int(item):
                    errors.append(f"bundles.{bkey}.{field}[{item_index}] must be an int")
                elif item not in id_set:
                    errors.append(f"bundle {bkey}: references unknown task {item}")
    for stream_index, stream in enumerate(flow.get("streams", [])):
        for box_index, box in enumerate(stream.get("boxes", [])):
            where = f'{stream.get("key")}.{box.get("key")}'
            if box.get("lane") not in lane_keys:
                errors.append(f'{where}: unknown lane {box.get("lane")}')
            for task_index, task_id in enumerate(box.get("tasks", []) or []):
                if not is_int(task_id):
                    errors.append(
                        f"flow.streams[{stream_index}].boxes[{box_index}].tasks[{task_index}] "
                        "must be an int")
                elif task_id not in id_set:
                    errors.append(f"{where}: references unknown task {task_id}")
            bkey = box.get("bundle")
            if bkey and bkey not in bundles:
                errors.append(f"{where}: unknown bundle {bkey}")
    return errors


class Board:
    def __init__(self, manifest, state_rows, fields, today, stamp):
        self.manifest = manifest
        self.meta = manifest["meta"]
        self.tasks = {t["id"]: t for t in manifest.get("tasks", [])}
        self.bundles = manifest.get("bundles", {}) or {}
        self.flow = manifest["flow"]
        self.lanes = self.flow["lanes"]
        self.state = {r["number"]: r for r in state_rows}
        self.fields = fields
        self.today = today
        self.stamp = stamp
        self.goal_field = self.meta.get("goal_field", "目標日")
        self.lane_kind_map = {lane["key"]: lane["kind"] for lane in self.lanes}
        self.lane_label_map = {lane["key"]: lane.get("owner_label") or OWNER_LABEL[lane["kind"]]
                               for lane in self.lanes}

    # --- state helpers -------------------------------------------------
    def closed(self, n):
        return self.state.get(n, {}).get("state", "OPEN") == "CLOSED"

    def title(self, n):
        return self.state.get(n, {}).get("title", f"#{n}")

    def deps_of(self, t):
        return [d["id"] for d in t.get("deps", [])]

    def ready(self, t):
        return not self.closed(t["id"]) and all(self.closed(d) for d in self.deps_of(t))

    def goal(self, n):
        v = self.fields.get(n, {}).get(self.goal_field)
        return v if isinstance(v, str) else None

    def issue_href(self, n):
        if self.meta.get("repo"):
            return esc(f'https://github.com/{self.meta["repo"]}/issues/{n}')
        if self.meta.get("issue_url_base"):
            return esc(f'{self.meta["issue_url_base"]}{n}')
        return None

    def task_no(self, n):
        href = self.issue_href(n)
        if href:
            return f'<a class="tno" href="{href}">#{n}</a>'
        return f'<span class="tno">#{n}</span>'

    def blocker_chips(self, ids):
        parts = []
        for d in ids:
            href = self.issue_href(d)
            parts.append(f'<a href="{href}">#{d}</a>' if href else f'#{d}')
        return " ".join(parts)

    def owner_kind(self, t):
        o = t.get("owner", "")
        if o.startswith("ai:"):
            return "ai"
        if o.startswith("human:"):
            return "human"
        return "joint"

    def owner_label(self, t):
        return t.get("owner_label") or OWNER_LABEL[self.owner_kind(t)]

    @staticmethod
    def fmt_date(iso):
        return iso[5:].replace("-", "/")

    # --- checkbox / task blocks ---------------------------------------
    @staticmethod
    def checkbox(cid, label_html, done, enabled, sub=False):
        """One check row. done = locked by SSoT; enabled = clickable now."""
        attrs = ' checked disabled' if done else ('' if enabled else ' disabled')
        cls = "ck sub" if sub else "ck"
        if done:
            cls += " ssot-done"
        return (f'<label class="{cls}"><input type="checkbox" data-ck="{esc(cid)}"{attrs}>'
                f'<span class="ckbox" aria-hidden="true"></span>'
                f'<span class="cktext">{label_html}</span></label>')

    def task_block(self, t):
        n = t["id"]
        kind = self.owner_kind(t)
        is_done = self.closed(n)
        # Checks are the human's report signal — AI tasks never get one.
        is_ready = self.ready(t) and kind != "ai"
        st = "done" if is_done else ("ready" if self.ready(t) else "blocked")
        steps = t.get("steps", [])
        head_meta = [self.task_no(n)]
        if kind == "ai" and not is_done:
            head_meta.append(f'<span class="state {st}">{STATE_LABEL[st]}</span>')
        goal = self.goal(n)
        if goal:
            head_meta.append(f'<span class="chip goal">目標 {esc(self.fmt_date(goal))}</span>')
        if st == "blocked":
            open_deps = [d for d in self.deps_of(t) if not self.closed(d)]
            head_meta.append(f'<span class="chip block">待ち: {self.blocker_chips(open_deps)}</span>')
        if is_done:
            head_meta.append('<span class="chip okc">完了</span>')

        parts = [f'<div class="task {kind} {st}" data-task="{n}">']
        ttl = (f'<span class="owner {kind}">{esc(self.owner_label(t))}</span> '
               f'<b>{esc(self.title(n))}</b> ' + " ".join(head_meta))
        if kind == "ai":
            parts.append(f'<div class="taskhead">{ttl}</div>')
            if not is_done and t.get("next_action"):
                parts.append(f'<p class="next">{esc(t["next_action"])}</p>')
        elif steps and not is_done:
            # Task with sub-steps: parent shows a counter, steps are the check items.
            parts.append(f'<div class="taskhead">{ttl} <span class="stepcount" data-count="{n}"></span></div>')
            if t.get("next_action"):
                parts.append(f'<p class="next">{esc(t["next_action"])}</p>')
            parts.append('<div class="steps">')
            for s in steps:
                parts.append(self.checkbox(f'{n}.{s["key"]}', esc(s["text"]), False, is_ready, sub=True))
            parts.append('</div>')
        else:
            parts.append('<div class="taskhead">' + self.checkbox(str(n), ttl, is_done, is_ready) + '</div>')
            if not is_done and t.get("next_action"):
                parts.append(f'<p class="next">{esc(t["next_action"])}</p>')
        if t.get("review_gate") and not is_done:
            parts.append(f'<div class="gate">🔍 {esc(t["review_gate"])}</div>')
        acc = "".join(
            f'<li>{esc(a["text"])}' + (f' <code>{esc(a["verify"])}</code>' if a.get("verify") else "") + "</li>"
            for a in t.get("accepts", []))
        if acc and not is_done:
            parts.append(f'<details><summary>完了の条件（クローズの基準）</summary><ul>{acc}</ul></details>')
        parts.append("</div>")
        return "".join(parts)

    # --- bundles -------------------------------------------------------
    def bundle_ready(self, b):
        return (all(self.closed(d) for d in b.get("after_issues", []))
                and any(not self.closed(i) for i in b["items"]))

    def bundle_done(self, b):
        return all(self.closed(i) for i in b["items"])

    def bundle_note(self, box):
        key = box.get("bundle")
        if key not in self.bundles:
            return ""
        b = self.bundles[key]
        chips = []
        if b.get("window"):
            chips.append(f'<span class="chip goal">{esc(b["window"])}</span>')
        if b.get("deadline"):
            chips.append(f'<span class="chip warn">期限 {esc(self.fmt_date(b["deadline"]))}</span>')
        detail = f'<p class="bdetail">{esc(b["detail"])}</p>' if b.get("detail") else ""
        if not chips and not detail:
            return ""
        return f'<div class="bundle-note">{"".join(chips)}{detail}</div>'

    def bundle_extra_blocks(self, box):
        key = box.get("bundle")
        if not box.get("include_bundle_extras") or key not in self.bundles:
            return []
        b = self.bundles[key]
        if self.bundle_done(b):
            return []
        enabled = self.bundle_ready(b)
        st = "ready" if enabled else "blocked"
        return ['<div class="task human ' + st + '"><div class="taskhead">' +
                self.checkbox(f'{key}.{x["key"]}',
                              '<span class="owner human">人間</span> ' + esc(x["action"]),
                              False, enabled) + "</div></div>"
                for x in b.get("extra_items", [])]

    # --- boxes ---------------------------------------------------------
    def box_task_ids(self, box):
        return list(box.get("tasks", []))

    def _open_bundle_extras(self, box):
        key = box.get("bundle")
        return bool(box.get("include_bundle_extras") and key in self.bundles
                    and not self.bundle_done(self.bundles[key]))

    def box_state(self, box):
        ids = self.box_task_ids(box)
        has_extras = self._open_bundle_extras(box)
        if all(self.closed(n) for n in ids) and not has_extras:
            return "done"
        if (any(self.ready(self.tasks[n]) for n in ids)
                or (has_extras and self.bundle_ready(self.bundles[box["bundle"]]))):
            return "ready"
        return "blocked"

    def box_blockers(self, box):
        seen = []
        for n in self.box_task_ids(box):
            if self.closed(n):
                continue
            for d in self.deps_of(self.tasks[n]):
                if not self.closed(d) and d not in seen:
                    seen.append(d)
        return seen

    def box_goal(self, box):
        goals = [self.goal(n) for n in self.box_task_ids(box)
                 if not self.closed(n) and self.goal(n)]
        return min(goals) if goals else None

    def box_has_review_gate(self, box):
        return any(self.tasks[n].get("review_gate") and not self.closed(n)
                   for n in self.box_task_ids(box))

    def box_has_ready_human(self, box):
        if any(self.owner_kind(self.tasks[n]) != "ai" and self.ready(self.tasks[n])
               for n in self.box_task_ids(box)):
            return True
        return self._open_bundle_extras(box) and self.bundle_ready(self.bundles[box["bundle"]])

    def static_checkbox_progress(self, box):
        total = done = 0
        for n in self.box_task_ids(box):
            t = self.tasks[n]
            if self.owner_kind(t) == "ai":
                continue
            if t.get("steps") and not self.closed(n):
                total += len(t["steps"])
            else:
                total += 1
                done += 1 if self.closed(n) else 0
        if self._open_bundle_extras(box):
            total += len(self.bundles[box["bundle"]].get("extra_items", []))
        if total:
            return f'☑ {done}/{total}'
        ids = self.box_task_ids(box)
        return f'{sum(1 for n in ids if self.closed(n))}/{len(ids)}'

    @staticmethod
    def box_id(stream_key, box_key):
        return f'box-{stream_key}-{box_key}'

    @staticmethod
    def body_id(stream_key, box_key):
        return f'boxbody-{stream_key}-{box_key}'

    def render_box_head(self, stream, box, idx):
        bid = self.box_id(stream["key"], box["key"])
        bdid = self.body_id(stream["key"], box["key"])
        kind = self.lane_kind_map[box["lane"]]
        st = self.box_state(box)
        ids = self.box_task_ids(box)
        task_done = sum(1 for n in ids if self.closed(n))
        meta_bits = [
            f'<span class="owner {kind}">{esc(self.lane_label_map[box["lane"]])}</span>',
            f'<span class="state {st}">{STATE_LABEL[st]}</span>',
        ]
        if box.get("bundle"):
            meta_bits.append(f'<span class="bkey">束{esc(box["bundle"])}</span>')
        goal = self.box_goal(box)
        if goal:
            meta_bits.append(f'<span class="chip goal">目標 {esc(self.fmt_date(goal))}</span>')
        blocker_ids = self.box_blockers(box)
        if blocker_ids:
            meta_bits.append(f'<span class="chip block">待ち: {self.blocker_chips(blocker_ids)}</span>')
        if self.box_has_review_gate(box):
            meta_bits.append('<span class="reviewmark" aria-label="review gate">🔍</span>')
        meta_bits.append(
            f'<span class="boxprog" data-boxprog="{esc(bdid)}" data-task-done="{task_done}" '
            f'data-task-total="{len(ids)}">{self.static_checkbox_progress(box)}</span>')
        return (
            f'<article class="flowbox {kind} {st}" id="{esc(bid)}" data-box="{esc(bid)}">'
            f'<button class="boxhead" type="button" aria-expanded="false" '
            f'aria-controls="{esc(bdid)}">'
            f'<span class="boxtitle"><span class="boxnum">{idx}</span>'
            f'<span class="boxname">{esc(box["name"])}</span><span class="twisty" aria-hidden="true">▾</span></span>'
            f'<span class="boxmeta">{"".join(meta_bits)}</span>'
            f'</button></article>')

    def render_box_body(self, stream, box):
        bdid = self.body_id(stream["key"], box["key"])
        blocks = [self.bundle_note(box)]
        blocks.extend(self.task_block(self.tasks[n]) for n in self.box_task_ids(box))
        blocks.extend(self.bundle_extra_blocks(box))
        return f'<div class="boxbody" id="{esc(bdid)}" hidden>{"".join(blocks)}</div>'

    @staticmethod
    def connector(prev_box, next_box, used_lane_cols):
        total = len(used_lane_cols)
        from_center = (used_lane_cols[prev_box["lane"]] - 0.5) / total * 100
        to_center = (used_lane_cols[next_box["lane"]] - 0.5) / total * 100
        left = min(from_center, to_center)
        width = abs(to_center - from_center)
        return (
            '<div class="connector" aria-hidden="true" '
            f'style="--from:{from_center:.4f}%;--to:{to_center:.4f}%;'
            f'--left:{left:.4f}%;--width:{width:.4f}%">'
            '<span class="v1"></span><span class="h"></span><span class="v2"></span><span class="arrow"></span>'
            '</div>')

    def render_stream(self, stream):
        used_lane_keys = {box["lane"] for box in stream["boxes"]}
        used_lanes = [lane for lane in self.lanes if lane["key"] in used_lane_keys]
        used_lane_cols = {lane["key"]: idx + 1 for idx, lane in enumerate(used_lanes)}
        parts = [f'<section class="stream" id="stream-{esc(stream["key"])}">']
        parts.append(f'<h2>{esc(stream["name"])}</h2>')
        parts.append(f'<div class="flowgrid" style="--lanes:{len(used_lanes)}">')
        for lane in used_lanes:
            parts.append(f'<div class="lanehead lane-{lane["kind"]}">{esc(lane["label"])}</div>')
        prev = None
        for idx, box in enumerate(stream["boxes"], start=1):
            if prev:
                parts.append(self.connector(prev, box, used_lane_cols))
            for lane in used_lanes:
                content = self.render_box_head(stream, box, idx) if lane["key"] == box["lane"] else ""
                parts.append(f'<div class="lanecell lane-{lane["kind"]}">{content}</div>')
            parts.append(self.render_box_body(stream, box))
            prev = box
        parts.append('</div></section>')
        return "".join(parts)

    def quick_link(self, stream, box):
        bid = self.box_id(stream["key"], box["key"])
        prefix = f'束{box["bundle"]} ' if box.get("bundle") else ""
        return f'<a class="quickchip" href="#{esc(bid)}" data-open-box="{esc(bid)}">{esc(prefix + box["name"])}</a>'

    # --- page ----------------------------------------------------------
    def render(self, css, js):
        meta = self.meta
        title = meta["title"]
        flow_task_ids = {n for s in self.flow["streams"] for b in s["boxes"] for n in b.get("tasks", [])}

        ready_boxes = [self.quick_link(s, b) for s in self.flow["streams"] for b in s["boxes"]
                       if self.box_has_ready_human(b)]
        stream_sections = [self.render_stream(s) for s in self.flow["streams"]]
        flow_out_blocks = "".join(self.task_block(t) for t in self.manifest.get("tasks", [])
                                  if t["id"] not in flow_task_ids)

        # header: SSoT links
        ssot_links = []
        if meta.get("repo"):
            issues_url = f'https://github.com/{meta["repo"]}/issues'
            ssot_links.append(f'<a href="{esc(issues_url)}">Issues</a>')
        if meta.get("board_url"):
            ssot_links.append(f'<a href="{esc(meta["board_url"])}">Projects</a>')
        if not ssot_links:
            ssot_links.append(esc(meta.get("state_note", "state ファイル")))
        stamp_html = (f'<span class="stamp">生成 {esc(self.stamp)} ／ 状態の正本は '
                      + "・".join(ssot_links) + '</span>')

        # countdown tiles + auto progress tile
        counts = []
        for d in meta.get("deadlines", []):
            days = (datetime.date.fromisoformat(d["date"]) - self.today).days
            cls = "count urgent" if d.get("urgent") else "count"
            counts.append(f'<div class="{cls}"><b>{days}日</b><span>{esc(d["label"])}</span></div>')
        n_total = len(self.tasks)
        n_closed = sum(1 for n in self.tasks if self.closed(n))
        counts.append(f'<div class="count"><b>{n_closed}/{n_total}</b><span>完了 / 全タスク</span></div>')

        legend = (
            '<div class="legend">'
            '<span><span class="dot" style="background:var(--human-accent)"></span>青 = 人間の実行</span>'
            '<span><span class="dot" style="background:var(--ai-accent)"></span>灰 = AIの実行</span>'
            '<span>☑ = この端末のチェック（正本反映は下の「報告をコピー」→AIへ）</span>'
            '<span>箱をタップすると中のタスクが開く</span></div>')

        timeline = ""
        if meta.get("milestones"):
            tl = "".join(f'<div class="ms"><span class="msd">{esc(m["when"])}</span><span>{esc(m["label"])}</span></div>'
                         for m in meta["milestones"])
            timeline = f'<h2>タイムライン</h2><div class="timeline">{tl}</div>'

        foot_extra = f'（{esc(meta["foot_note"])}）' if meta.get("foot_note") else ""
        report_head = meta.get("report_head", f'[{title}チェック報告 v1] ')
        js = js.replace("'__BOARD_KEY__'", script_safe_json(meta["key"]))
        js = js.replace("'__REPORT_HEAD__'", script_safe_json(report_head))

        parts = ['<!doctype html>',
                 '<html lang="ja">',
                 '<head>',
                 '<meta charset="utf-8">',
                 '<meta name="viewport" content="width=device-width, initial-scale=1">',
                 f'<title>{esc(title)}</title>',
                 LICENSE_COMMENT,
                 f'<style>{css}</style>',
                 '</head>',
                 '<body>',
                 '<main>']
        parts.append(f'<div class="head"><h1>{esc(title)}</h1>{stamp_html}</div>')
        parts.append('<div class="counts">' + "".join(counts) + '</div>')
        parts.append(legend)
        parts.append('<h2>今すぐ<span class="sub">依存が満了した人間タスク</span></h2>')
        parts.append('<div class="quickstrip">' + "".join(ready_boxes) + '</div>' if ready_boxes
                     else '<p class="bdetail">着手可能な人間タスクはありません。AIレーンが進行中です。</p>')
        parts.extend(stream_sections)
        parts.append(timeline)
        if flow_out_blocks:
            parts.append('<h2>フロー外<span class="sub">flow の箱に属さないタスク</span></h2>')
            parts.append(flow_out_blocks)
        parts.append(
            '<div class="foot">運用: チェック→下部バーで「報告をコピー」→AIに貼る→AIが検証し正本へ反映→本盤を再生成'
            '（チェックは端末ローカル、完了の正本は状態SSoT）。このHTMLは生成物であり手編集禁止' + foot_extra + '。</div>')
        parts.append(
            '<div class="syncbar" id="syncbar"><span class="n">未報告のチェック <b id="pn">0</b> 件</span>'
            '<button id="copybtn" type="button">AIへ報告をコピー</button>'
            '<span class="copied" id="copied"></span>'
            '<textarea class="fallback" id="fallback" style="display:none" readonly></textarea>'
            '<span class="hint">貼り先: チャット or 該当issueコメント</span></div>')
        parts.append(f'<script>{js}</script></main>')
        parts.append('</body>')
        parts.append('</html>')
        return "\n".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("manifest")
    ap.add_argument("state")
    ap.add_argument("fields", nargs="?")
    ap.add_argument("--stamp")
    ap.add_argument("--today")
    ap.add_argument("--assets")
    args = ap.parse_args(argv)

    manifest = load_manifest(pathlib.Path(args.manifest))
    errors = validate(manifest)
    if errors:
        for e in errors:
            print(f"render.py: {e}", file=sys.stderr)
        return 1

    state_rows = json.loads(pathlib.Path(args.state).read_text(encoding="utf-8"))
    fields = {}
    if args.fields:
        for it in json.loads(pathlib.Path(args.fields).read_text(encoding="utf-8")).get("items", []):
            n = it.get("content", {}).get("number")
            if n:
                fields[n] = it

    today = datetime.date.fromisoformat(args.today) if args.today else datetime.date.today()
    stamp = args.stamp or datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    assets = pathlib.Path(args.assets) if args.assets else pathlib.Path(__file__).resolve().parent.parent / "assets"
    css = (assets / "style.css").read_text(encoding="utf-8")
    js = (assets / "board.js").read_text(encoding="utf-8")

    board = Board(manifest, state_rows, fields, today, stamp)
    print(board.render(css, js))
    return 0


if __name__ == "__main__":
    sys.exit(main())
