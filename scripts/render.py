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
  --fragment                  output an Artifact-ready fragment without document scaffolding

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


def normalize_edges(stream):
    """Normalize a validated stream into ordered edges with stream-local box keys.

    label defaults to "", kind to None; type is main, skip, or return.
    """
    boxes = stream.get("boxes", [])
    positions = {box["key"]: index for index, box in enumerate(boxes)}
    default_targets = [None] * len(boxes)
    next_required = None
    for index in range(len(boxes) - 1, -1, -1):
        default_targets[index] = next_required
        if not boxes[index].get("optional", False):
            next_required = boxes[index]["key"]

    edges = []
    for index, box in enumerate(boxes):
        default_to = default_targets[index]
        outgoing = box.get("next", [] if default_to is None else [{"to": default_to}])
        for edge in outgoing:
            target = edge["to"]
            edge_type = ("return" if positions[target] < index
                         else "main" if target == default_to else "skip")
            edges.append({"stream": stream["key"], "from": box["key"], "to": target,
                          "label": edge.get("label", ""), "kind": edge.get("kind"),
                          "type": edge_type})
    return edges


def validate(manifest):
    """Return invariant errors and emit non-fatal warnings to stderr."""
    errors = []
    warnings = []
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
    if not 1 <= len(lanes) <= 6:
        errors.append("flow.lanes must contain 1-6 lanes")
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
    streams = flow.get("streams", [])
    stream_keys = set()
    duplicate_stream_keys = set()
    for stream in streams:
        stream_key = stream.get("key")
        if stream_key in stream_keys:
            errors.append(f"duplicate stream key: {stream_key}")
            duplicate_stream_keys.add(stream_key)
        stream_keys.add(stream_key)
    for stream_index, stream in enumerate(streams):
        stream_error_count = len(errors)
        boxes = stream.get("boxes", [])
        box_keys = set()
        for box in boxes:
            box_key = box.get("key")
            if box_key in box_keys:
                errors.append(f'{stream.get("key")}.{box_key}: duplicate box key')
            box_keys.add(box_key)
        for box_index, box in enumerate(boxes):
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
            if "optional" in box:
                if not isinstance(box["optional"], bool):
                    errors.append(f"{where}: optional must be a boolean")
                elif box_index == 0 and box["optional"]:
                    errors.append(f"{where}: the first box cannot be optional")
            fanout = box.get("fanout")
            if "fanout" in box and fanout not in ("exclusive", "parallel"):
                errors.append(f"{where}: fanout must be exclusive or parallel")
            criterion = box.get("criterion")
            if "criterion" in box:
                if not isinstance(criterion, str) or not criterion.strip():
                    errors.append(f"{where}: criterion must be a non-empty string")
                elif len(criterion) > 24:
                    warnings.append(f"{where}.criterion exceeds 24 characters ({len(criterion)})")
                if fanout == "parallel":
                    errors.append(f"{where}: criterion is only allowed for exclusive fanout")
            outgoing = box.get("next", [])
            if not isinstance(outgoing, list):
                errors.append(f"{where}: next must be an array")
                continue
            outgoing_count = len(outgoing) if "next" in box else 1
            if "fanout" in box and outgoing_count < 2:
                errors.append(f"{where}: fanout requires 2 or more outgoing edges")
            if outgoing_count >= 2 and "fanout" not in box:
                warnings.append(
                    f"{where}: specify fanout: exclusive or parallel for 2 or more outgoing edges")
            if fanout == "exclusive" and outgoing_count >= 3 and "criterion" not in box:
                errors.append(f"{where}: criterion is required for 3 or more exclusive outgoing edges")
            if outgoing_count >= 5:
                errors.append(f"{where}: 5 or more outgoing edges: move the routing into a table "
                              "or sub-board, or split the box")
            elif outgoing_count >= 3:
                if fanout == "exclusive":
                    advice = "confirm they share one exclusive criterion or split the box"
                elif fanout == "parallel":
                    advice = "confirm all targets really proceed together or split the box"
                else:
                    advice = ("confirm whether they share one exclusive criterion or all targets "
                              "really proceed together, or split the box")
                warnings.append(f"{where}: 3 or more exits: {advice}")
            for edge_index, edge in enumerate(outgoing):
                edge_where = f"{where}.next[{edge_index}]"
                if not isinstance(edge, dict):
                    errors.append(f"{edge_where} must be an object")
                    continue
                target = edge.get("to")
                if not isinstance(target, str):
                    errors.append(f"{edge_where}.to is required and must be a string")
                elif target not in box_keys:
                    errors.append(f"{edge_where}.to references unknown box {target} in this stream")
                elif target == box.get("key"):
                    errors.append(f"{edge_where}.to cannot reference its own box")
                label = edge.get("label")
                if "label" in edge and not isinstance(label, str):
                    errors.append(f"{edge_where}.label must be a string")
                elif len(outgoing) >= 2 and (label is None or not label.strip()):
                    errors.append(f"{edge_where}.label is required for 2 or more outgoing edges")
                if isinstance(label, str) and len(label) > 24:
                    warnings.append(f"{edge_where}.label exceeds 24 characters ({len(label)})")
                if "kind" in edge and edge["kind"] != "alt":
                    errors.append(f"{edge_where}.kind must be alt when specified")
                elif edge.get("kind") == "alt" and fanout == "parallel":
                    errors.append(f"{edge_where}: kind: alt is only allowed for exclusive fanout")

        # Only analyze topology once this stream's keys, references and fields are valid.
        if stream.get("key") not in duplicate_stream_keys and len(errors) == stream_error_count:
            edges = normalize_edges(stream)
            incoming = {edge["to"] for edge in edges}
            forward = {box["key"]: [] for box in boxes}
            for edge in edges:
                if edge["type"] in ("main", "skip"):
                    forward[edge["from"]].append(edge["to"])
            reachable = {boxes[0]["key"]} if boxes else set()
            for box in boxes:
                if box["key"] in reachable:
                    reachable.update(forward[box["key"]])
            for box in boxes[1:]:
                where = f'{stream.get("key")}.{box["key"]}'
                if box["key"] not in incoming:
                    warnings.append(f"{where}: box has no incoming edges")
                if box["key"] not in reachable:
                    warnings.append(f"{where}: box is unreachable from the first box via forward edges")
    for warning in warnings:
        print(f"render.py: warning: {warning}", file=sys.stderr)
    return errors


class Board:
    def __init__(self, manifest, state_rows, fields, today, stamp):
        self.manifest = manifest
        self.meta = manifest["meta"]
        self.tasks = {t["id"]: t for t in manifest.get("tasks", [])}
        self.bundles = manifest.get("bundles", {}) or {}
        self.flow = manifest["flow"]
        self.edges = [edge for stream in self.flow.get("streams", [])
                      for edge in normalize_edges(stream)]
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

    def task_href(self, n):
        return self.issue_href(n) if self.tasks[n].get("link", True) else None

    def task_label(self, n):
        return esc(self.tasks[n].get("display", f"#{n}"))

    def task_no(self, n):
        href = self.task_href(n)
        label = self.task_label(n)
        if href:
            return f'<a class="tno" href="{href}">{label}</a>'
        return f'<span class="tno">{label}</span>'

    def blocker_chips(self, ids):
        parts = []
        for d in ids:
            href = self.task_href(d)
            label = self.task_label(d)
            parts.append(f'<a href="{href}">{label}</a>' if href else label)
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

    def render_box_head(self, stream, box, idx, outgoing=0):
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
        if box.get("optional"):
            meta_bits.append('<span class="chip cond">条件付き</span>')
        if outgoing >= 2:
            if box.get("fanout") == "exclusive":
                title = f' title="判断基準: {esc(box["criterion"])}"' if box.get("criterion") else ""
                meta_bits.append(f'<span class="chip fork decision"{title}>◇ 判断 {outgoing}</span>')
            elif box.get("fanout") == "parallel":
                meta_bits.append(f'<span class="chip fork parallel">＋ 並列 {outgoing}</span>')
            else:
                meta_bits.append(f'<span class="chip fork">分岐 {outgoing}</span>')
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
            f'<article class="flowbox {kind} {st}{" optional" if box.get("optional") else ""}" '
            f'id="{esc(bid)}" data-box="{esc(bid)}">'
            f'<button class="boxhead" type="button" aria-expanded="false" '
            f'aria-controls="{esc(bdid)}">'
            f'<span class="boxtitle"><span class="boxnum">{idx}</span>'
            f'<span class="boxname">{esc(box["name"])}</span><span class="twisty" aria-hidden="true">▾</span></span>'
            f'<span class="boxmeta">{"".join(meta_bits)}</span>'
            f'</button></article>')

    def render_box_body(self, stream, box, placement="", exits=""):
        bdid = self.body_id(stream["key"], box["key"])
        blocks = [self.bundle_note(box)]
        blocks.extend(self.task_block(self.tasks[n]) for n in self.box_task_ids(box))
        blocks.extend(self.bundle_extra_blocks(box))
        style = f' style="{placement}"' if placement else ""
        return f'<div class="boxbody" id="{esc(bdid)}"{style} hidden>{exits}{"".join(blocks)}</div>'

    @staticmethod
    def connector(prev_box, next_box, used_lane_cols, placement="", edge=None):
        total = len(used_lane_cols)
        from_center = (used_lane_cols[prev_box["lane"]] - 0.5) / total * 100
        to_center = (used_lane_cols[next_box["lane"]] - 0.5) / total * 100
        left = min(from_center, to_center)
        width = abs(to_center - from_center)
        extra = f';{placement}' if placement else ""
        alt = " alt" if edge and edge["kind"] == "alt" else ""
        label = f'<span class="elabel">{esc(edge["label"])}</span>' if edge and edge["label"] else ""
        attributes = (f' data-edge-type="{edge["type"]}" data-route="{edge["route"]}" '
                      f'data-edge-from="{esc(Board.box_id(edge["stream"], edge["from"]))}" '
                      f'data-edge-to="{esc(Board.box_id(edge["stream"], edge["to"]))}"' if edge else "")
        arrival = ('<span class="v2"></span><span class="arrow"></span>'
                   if edge is None or edge["arrow"] else "")
        return (
            f'<div class="connector{alt}" aria-hidden="true"{attributes} '
            f'style="--from:{from_center:.4f}%;--to:{to_center:.4f}%;'
            f'--left:{left:.4f}%;--width:{width:.4f}%{extra}">'
            f'<span class="v1"></span><span class="h"></span>{arrival}'
            f'{label}</div>')

    @staticmethod
    def graph_routes(boxes, edges):
        """Order exits and assign reusable rails, numbered from the lanes outward."""
        positions = {box["key"]: i + 1 for i, box in enumerate(boxes)}
        routes = []
        for edge in edges:
            source, target = positions[edge["from"]], positions[edge["to"]]
            # Logical main edges can jump over optional boxes: route those via a rail.
            route = ("return" if target < source else "skip" if target > source + 1
                     or edge["type"] == "skip" or edge["kind"] == "alt" else "adjacent")
            routes.append(dict(edge, route=route, source=source, target=target))
        for box in boxes:
            outgoing = [r for r in routes if r["from"] == box["key"]]
            # Forward spans descend, return spans ascend; stable ties keep input order.
            outgoing.sort(key=lambda r: (0 if r["route"] == "adjacent" else
                                         2 if r["route"] == "return" else 1,
                                         r["source"] - r["target"]))
            for i, route in enumerate(outgoing):
                route["exit_index"] = i
        for box in boxes:
            # Keep adjacent labels above every incoming rail, clear of its horizontal line.
            # Parallel rail edges arrive in reverse exit order to avoid crossing at h2.
            incoming = sorted((r for r in routes if r["to"] == box["key"]),
                              key=lambda r: (r["route"] != "adjacent", r["source"],
                                             -r["exit_index"] if r["route"] != "adjacent" else 0))
            for i, route in enumerate(incoming):
                route["arrival"] = 14 + 8 * i
                route["arrow"] = i == len(incoming) - 1
        counts = {}
        for side in ("return", "skip"):
            intervals = [(min(4 * r["source"], 4 * r["target"] - 2),
                          max(4 * r["source"], 4 * r["target"] - 2), i)
                         for i, r in enumerate(routes) if r["route"] == side]
            assigned = []
            while intervals:
                # Assign inner rails first: lower exits forward, upper exits returning.
                eligible = [item for item in intervals if not any(
                    routes[j]["source"] == routes[item[2]]["source"]
                    and (routes[j]["exit_index"] > routes[item[2]]["exit_index"] if side == "skip"
                         else routes[j]["exit_index"] < routes[item[2]]["exit_index"])
                    for _, _, j in intervals)]
                start, end, i = min(eligible, key=lambda item: (item[1] - item[0], item[2]))
                overlapping = [routes[j]["rail"] for lo, hi, j in assigned
                               if start <= hi and lo <= end]
                routes[i]["rail"] = 1 + max(overlapping, default=0)
                assigned.append((start, end, i))
                intervals.remove((start, end, i))
            counts[side] = max((routes[i]["rail"] for _, _, i in assigned), default=0)
        return routes, counts["return"], counts["skip"]

    def render_graph_edge(self, stream, route, used_lane_cols, rails_l):
        source, target = route["source"], route["target"]
        to_col = rails_l + 2 * used_lane_cols[route["to_lane"]]
        returning = route["route"] == "return"
        from_col = rails_l + 2 * used_lane_cols[route["from_lane"]] + (-1 if returning else 1)
        rail_col = (rails_l - route["rail"] + 1 if returning
                    else rails_l + 2 * len(used_lane_cols) + route["rail"])
        h1_cols = f'{rail_col} / {from_col}' if returning else f'{from_col} / {rail_col + 1}'
        h2_cols = f'{rail_col} / {to_col}' if returning else f'{to_col} / {rail_col + 1}'
        v_rows = f'bx-{target} / ex-{source}' if returning else f'bd-{source} / ch-{target}'
        alt = " alt" if route["kind"] == "alt" else ""
        arrow = (f'<span class="arrow" style="grid-row:ch-{target} / bx-{target};grid-column:{to_col} / {to_col + 1}"></span>'
                 if route["arrow"] else "")
        return (
            f'<div class="edge {route["route"]}{alt}" aria-hidden="true" '
            f'data-edge-type="{route["type"]}" data-route="{route["route"]}" '
            f'data-edge-from="{esc(self.box_id(stream["key"], route["from"]))}" '
            f'data-edge-to="{esc(self.box_id(stream["key"], route["to"]))}" '
            f'data-rail="{route["rail"]}" style="--departure:calc({route["exit_index"]}*24px + 13px);'
            f'--arrival:{route["arrival"]}px">'
            f'<span class="h1" style="grid-row:ex-{source} / bd-{source};grid-column:{h1_cols}"></span>'
            f'<span class="v" style="grid-row:{v_rows};grid-column:{rail_col} / {rail_col + 1}"></span>'
            f'<span class="h2" style="grid-row:ch-{target} / bx-{target};grid-column:{h2_cols}"></span>'
            f'{arrow}</div>')

    def render_exits(self, stream, outgoing, by_key, full=False):
        box = by_key[outgoing[0]["from"]] if outgoing else {}
        fanout = box.get("fanout")
        items = []
        for route in outgoing:
            target = by_key[route["to"]]
            bid = esc(self.box_id(stream["key"], target["key"]))
            returning = route["route"] == "return"
            alt = route["kind"] == "alt"
            symbol = "↩" if returning else "⇢" if alt else "→"
            kind = ("戻り" if returning else "例外" if alt else "並列" if fanout == "parallel"
                    else "次段" if route["route"] == "adjacent" else "分岐")
            condition = route["label"] or kind
            title = f'{condition} {symbol} {route["target"]} {target["name"]}'
            name = target["name"]
            if not full and len(name) > 14:
                name = name[:14] + "…"
            detail = f' <span class="routekind">（{kind}）</span>' if full else ""
            items.append(
                f'<li class="exit {route["route"]}{" alt" if alt else ""}">'
                f'<a href="#{bid}" data-open-box="{bid}" title="{esc(title)}">'
                f'<b class="cond">{esc(condition)}</b>{detail} '
                f'<span class="to">{symbol} {route["target"]} {esc(name)}</span></a></li>')
        if full:
            heading = "出口"
            if fanout == "exclusive":
                criterion = f'{box["criterion"]}・' if box.get("criterion") else ""
                heading = f"◇ 出口（{criterion}いずれか1つへ進む）"
            elif fanout == "parallel":
                heading = "＋ 出口（すべてへ進む）"
            return f'<div class="exitlist"><b>{esc(heading)}</b><ol>{"".join(items)}</ol></div>'
        return f'<ol class="exits">{"".join(items)}</ol>'

    def render_graph_stream(self, stream, used_lanes, used_lane_cols):
        boxes = stream["boxes"]
        by_key = {box["key"]: box for box in boxes}
        edges = [edge for edge in self.edges if edge["stream"] == stream["key"]]
        routes, rails_l, rails_r = self.graph_routes(boxes, edges)
        lane_columns = f'{rails_l + 1} / {rails_l + 2 * len(used_lanes) + 1}'
        # repeat(0, ...) invalidates the entire CSS track list; omit empty sides.
        columns = ((f'repeat({rails_l},14px) ' if rails_l else '')
                   + 'repeat(calc(var(--lanes)*2),minmax(0,1fr))'
                   + (f' repeat({rails_r},14px)' if rails_r else ''))
        rows = 'auto ' + ' '.join(f'[ch-{i}] auto [bx-{i}] auto [ex-{i}] auto [bd-{i}] auto'
                                 for i in range(1, len(boxes) + 1)) + ' [end]'
        parts = [f'<section class="stream" id="stream-{esc(stream["key"])}">',
                 f'<h2>{esc(stream["name"])}</h2>',
                 '<div class="graphwrap">',
                 f'<div class="flowgrid graph" data-flow-mode="graph" '
                 f'style="--lanes:{len(used_lanes)};--rails-l:{rails_l};--rails-r:{rails_r};'
                 f'grid-template-columns:{columns};grid-template-rows:{rows}">']
        for col, lane in enumerate(used_lanes):
            parts.append(f'<div class="lanehead lane-{lane["kind"]}" '
                         f'style="grid-row:1;grid-column:{rails_l + col * 2 + 1} / span 2">'
                         f'{esc(lane["label"])}</div>')
        exit_boxes = set()
        for i, box in enumerate(boxes, 1):
            incoming = [r for r in routes if r["to"] == box["key"]]
            outgoing = sorted((r for r in routes if r["from"] == box["key"]),
                              key=lambda r: r["exit_index"])
            has_exits = len(outgoing) >= 2 or any(r["route"] != "adjacent" for r in outgoing)
            height = 28 + 8 * max(0, len(incoming) - 1)
            parts.append(f'<div class="channel" aria-hidden="true" '
                         f'style="grid-row:ch-{i};grid-column:{lane_columns};min-height:{height}px"></div>')
            adjacent = [r for r in incoming if r["route"] == "adjacent"]
            for index, route in enumerate(adjacent):
                label = ("／".join(r["label"] for r in adjacent if r["label"])
                         if index == 0 and route["from"] not in exit_boxes else "")
                parts.append(self.connector(by_key[route["from"]], box, used_lane_cols,
                                            f'grid-row:ch-{i} / bx-{i};grid-column:{lane_columns};'
                                            f'--arrival:{route["arrival"]}px', dict(route, label=label)))
            for col, lane in enumerate(used_lanes):
                content = ""
                occupied = lane["key"] == box["lane"]
                if occupied:
                    content = self.render_box_head(stream, box, i, len(outgoing))
                parts.append(f'<div class="lanecell lane-{lane["kind"]}{" occupied" if occupied else ""}" '
                             f'style="grid-row:bx-{i};grid-column:{rails_l + col * 2 + 1} / span 2">'
                             f'{content}</div>')
            if has_exits:
                exit_boxes.add(box["key"])
                col = rails_l + 2 * used_lane_cols[box["lane"]] - 1
                parts.append(f'<div class="exitcell" style="grid-row:ex-{i};grid-column:{col} / span 2">'
                             f'{self.render_exits(stream, outgoing, by_key)}</div>')
            full_exits = (self.render_exits(stream, outgoing, by_key, full=True)
                          if has_exits or any(r["label"] for r in outgoing) else "")
            parts.append(self.render_box_body(stream, box, f'grid-row:bd-{i};grid-column:{lane_columns}', full_exits))
        for route in routes:
            if route["route"] == "adjacent":
                if route["from"] in exit_boxes:
                    col = rails_l + 2 * used_lane_cols[by_key[route["from"]]["lane"]]
                    parts.append(
                        f'<div class="edge stem main" aria-hidden="true" '
                        f'data-edge-from="{esc(self.box_id(stream["key"], route["from"]))}" '
                        f'data-edge-to="{esc(self.box_id(stream["key"], route["to"]))}">'
                        f'<span class="v" style="grid-row:ex-{route["source"]} / ch-{route["target"]};'
                        f'grid-column:{col} / {col + 1};top:calc({route["exit_index"]}*24px + 13px)"></span></div>')
            else:
                route = dict(route, from_lane=by_key[route["from"]]["lane"], to_lane=by_key[route["to"]]["lane"])
                parts.append(self.render_graph_edge(stream, route, used_lane_cols, rails_l))
        parts.append('</div></div></section>')
        return "".join(parts)

    def render_stream(self, stream):
        used_lane_keys = {box["lane"] for box in stream["boxes"]}
        used_lanes = [lane for lane in self.lanes if lane["key"] in used_lane_keys]
        used_lane_cols = {lane["key"]: idx + 1 for idx, lane in enumerate(used_lanes)}
        if any("next" in box or "optional" in box for box in stream["boxes"]):
            return self.render_graph_stream(stream, used_lanes, used_lane_cols)
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
    def render(self, css, js, fragment=False):
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
        stamp_prefix = f'{esc(meta["subtitle"])} ／ ' if meta.get("subtitle") else ""
        stamp_html = (f'<span class="stamp">{stamp_prefix}生成 {esc(self.stamp)} ／ 状態の正本は '
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
        if any("next" in box or "optional" in box for stream in self.flow["streams"] for box in stream["boxes"]):
            legend = legend[:-6] + ('<span>実線=通常／破線=例外／点線=戻り／'
                                    '◇ 判断 N=結果でいずれか1本を選ぶ／＋ 並列 N=すべてへ進む／'
                                    '分岐 N=種別未指定</span></div>')

        timeline = ""
        if meta.get("milestones"):
            tl = "".join(f'<div class="ms"><span class="msd">{esc(m["when"])}</span><span>{esc(m["label"])}</span></div>'
                         for m in meta["milestones"])
            timeline = f'<h2>タイムライン</h2><div class="timeline">{tl}</div>'

        foot_extra = f'（{esc(meta["foot_note"])}）' if meta.get("foot_note") else ""
        report_head = meta.get("report_head", f'[{title}チェック報告 v1] ')
        js = js.replace("'__BOARD_KEY__'", script_safe_json(meta["key"]))
        js = js.replace("'__REPORT_HEAD__'", script_safe_json(report_head))

        if fragment:
            parts = [LICENSE_COMMENT,
                     f'<title>{esc(title)}</title>',
                     f'<style>{css}</style>',
                     '<main>']
        else:
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
        if not fragment:
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
    ap.add_argument("--fragment", action="store_true")
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
    print(board.render(css, js, fragment=args.fragment))
    return 0


if __name__ == "__main__":
    sys.exit(main())
