#!/usr/bin/env python3
"""Validation and rendering regressions for explicit fanout semantics."""
import contextlib
import copy
import datetime
import io
import json
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_board
import render


def make_manifest(exit_count=2, lane_count=1, **box_fields):
    box_count = max(3, (exit_count or 0) + 1)
    boxes = [{"key": f"b{i}", "name": f"Box {i}",
              "lane": f"l{(i - 1) % lane_count}", "tasks": [i]}
             for i in range(1, box_count + 1)]
    boxes[0].update(box_fields)
    if exit_count is not None:
        boxes[0]["next"] = [{"to": f"b{i + 2}", "label": f"Result {i + 1}"}
                            for i in range(exit_count)]
    return {
        "meta": {"title": "Test board", "key": "test-board"},
        "flow": {
            "lanes": [{"key": f"l{i}", "label": f"Lane {i}", "kind": "ai"}
                      for i in range(lane_count)],
            "streams": [{"key": "s", "name": "Stream", "boxes": boxes}],
        },
        "tasks": [{"id": i, "owner": "ai:test"} for i in range(1, box_count + 1)],
    }


def validate_with_warnings(manifest):
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        errors = render.validate(manifest)
    return errors, stderr.getvalue()


def make_board(manifest, state_rows=()):
    return render.Board(manifest, state_rows, {}, datetime.date(2026, 8, 20), "2026-08-20 00:00")


class ValidationTests(unittest.TestCase):
    def test_unspecified_fanout_warns(self):
        errors, warnings = validate_with_warnings(make_manifest())
        self.assertEqual(errors, [])
        self.assertIn("specify fanout: exclusive or parallel for 2 or more outgoing edges", warnings)

    def test_parallel_rejects_alt(self):
        manifest = make_manifest(fanout="parallel")
        manifest["flow"]["streams"][0]["boxes"][0]["next"][1]["kind"] = "alt"
        errors, _ = validate_with_warnings(manifest)
        self.assertIn("kind: alt is only allowed for exclusive fanout", "\n".join(errors))

    def test_three_exclusive_exits_with_criterion_warn(self):
        errors, warnings = validate_with_warnings(
            make_manifest(3, fanout="exclusive", criterion="規模"))
        self.assertEqual(errors, [])
        self.assertIn("3 or more exits: confirm they share one exclusive criterion or split the box",
                      warnings)

    def test_three_exclusive_exits_require_criterion(self):
        errors, _ = validate_with_warnings(make_manifest(3, fanout="exclusive"))
        self.assertIn("criterion is required for 3 or more exclusive outgoing edges", "\n".join(errors))

    def test_five_parallel_exits_are_rejected(self):
        errors, _ = validate_with_warnings(make_manifest(5, fanout="parallel"))
        self.assertEqual(errors, ["s.b1: 5 or more outgoing edges: move the routing into a table "
                                  "or sub-board, or split the box"])

    def test_single_exclusive_exit_is_rejected(self):
        errors, _ = validate_with_warnings(make_manifest(1, fanout="exclusive"))
        self.assertIn("fanout requires 2 or more outgoing edges", "\n".join(errors))

    def test_parallel_rejects_criterion(self):
        errors, _ = validate_with_warnings(make_manifest(fanout="parallel", criterion="規模"))
        self.assertIn("criterion is only allowed for exclusive fanout", "\n".join(errors))

    def test_invalid_fanout_values(self):
        for value in ("", "other", "EXCLUSIVE", None, True, 1, [], {}):
            with self.subTest(value=value):
                errors, _ = validate_with_warnings(make_manifest(fanout=value))
                self.assertIn("fanout must be exclusive or parallel", "\n".join(errors))

    def test_invalid_criterion_values(self):
        for value in ("", " \t\n", "\u3000", None, True, 1, [], {}):
            with self.subTest(value=value):
                errors, _ = validate_with_warnings(make_manifest(fanout="exclusive", criterion=value))
                self.assertIn("criterion must be a non-empty string", "\n".join(errors))

    def test_zero_or_implicit_exits_reject_fanout(self):
        for fanout in ("exclusive", "parallel"):
            for exit_count in (0, 1, None):
                with self.subTest(fanout=fanout, exit_count=exit_count):
                    errors, _ = validate_with_warnings(make_manifest(exit_count, fanout=fanout))
                    self.assertIn("fanout requires 2 or more outgoing edges", "\n".join(errors))
            manifest = make_manifest(exit_count=None)
            manifest["flow"]["streams"][0]["boxes"][-1]["fanout"] = fanout
            errors, _ = validate_with_warnings(manifest)
            self.assertIn("s.b3: fanout requires 2 or more outgoing edges", errors)

    def test_exit_warning_boundaries_and_modes(self):
        for fanout, advice in (("exclusive", "share one exclusive criterion"),
                               ("parallel", "all targets really proceed together"),
                               (None, "share one exclusive criterion or all targets really proceed together")):
            for count in (2, 3, 4, 5):
                with self.subTest(fanout=fanout, count=count):
                    fields = {"fanout": fanout} if fanout else {}
                    if fanout == "exclusive":
                        fields["criterion"] = "規模"
                    errors, warnings = validate_with_warnings(make_manifest(count, **fields))
                    if count == 5:
                        self.assertIn("5 or more outgoing edges", "\n".join(errors))
                    else:
                        self.assertEqual(errors, [])
                    if 3 <= count <= 4:
                        self.assertIn("3 or more exits:", warnings)
                        self.assertIn(advice, warnings)
                    else:
                        self.assertNotIn("3 or more exits:", warnings)

    def test_labels_remain_required_in_every_mode(self):
        for fanout in (None, "exclusive", "parallel"):
            for value in (None, "", " \t", 123):
                with self.subTest(fanout=fanout, label=value):
                    fields = {"fanout": fanout} if fanout else {}
                    manifest = make_manifest(**fields)
                    edge = manifest["flow"]["streams"][0]["boxes"][0]["next"][0]
                    if value is None:
                        del edge["label"]
                    else:
                        edge["label"] = value
                    errors, _ = validate_with_warnings(manifest)
                    self.assertIn("s.b1.next[0].label", "\n".join(errors))

    def test_text_length_warning_boundaries(self):
        for field in ("label", "criterion"):
            for length in (24, 25):
                with self.subTest(field=field, length=length):
                    manifest = make_manifest(fanout="exclusive")
                    box = manifest["flow"]["streams"][0]["boxes"][0]
                    target = box["next"][0] if field == "label" else box
                    target[field] = "検" * length
                    errors, warnings = validate_with_warnings(manifest)
                    self.assertEqual(errors, [])
                    if length == 25:
                        self.assertIn(f".{field} exceeds 24 characters (25)", warnings)
                    else:
                        self.assertEqual(warnings, "")

    def test_return_and_alt_edges_count_toward_limits(self):
        for count in (3, 4, 5):
            with self.subTest(count=count):
                manifest = make_manifest(exit_count=None)
                box = manifest["flow"]["streams"][0]["boxes"][1]
                box.update(fanout="exclusive", criterion="結果", next=[
                    {"to": "b1" if i % 2 else "b3", "label": f"Result {i}", "kind": "alt"}
                    for i in range(count)])
                errors, warnings = validate_with_warnings(manifest)
                if count == 5:
                    self.assertIn("5 or more outgoing edges", "\n".join(errors))
                else:
                    self.assertEqual(errors, [])
                    self.assertIn("3 or more exits:", warnings)

    def test_unspecified_fanout_allows_legacy_alt_and_criterion(self):
        manifest = make_manifest(criterion="規模")
        manifest["flow"]["streams"][0]["boxes"][0]["next"][1]["kind"] = "alt"
        errors, warnings = validate_with_warnings(manifest)
        self.assertEqual(errors, [])
        self.assertIn("specify fanout", warnings)

    def test_schema_declares_new_fields_without_hard_length_limit(self):
        schema = json.loads((ROOT / "schema/sakusenban.schema.json").read_text(encoding="utf-8"))
        box = schema["properties"]["flow"]["properties"]["streams"]["items"]["properties"]["boxes"]["items"]
        self.assertEqual(box["properties"]["fanout"]["enum"], ["exclusive", "parallel"])
        criterion = box["properties"]["criterion"]
        self.assertEqual(criterion["type"], "string")
        self.assertIsNone(re.search(criterion["pattern"], " \t\n"))
        self.assertIsNotNone(re.search(criterion["pattern"], "検算結果"))
        self.assertNotIn("maxLength", criterion)
        self.assertNotIn("fanout", box["required"])
        self.assertNotIn("criterion", box["required"])


class RenderingTests(unittest.TestCase):
    def test_example_has_exactly_one_decision_chip(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = render.main([str(ROOT / "examples/board.yaml"), str(ROOT / "examples/state.json"),
                                  "--stamp", "2026-08-20 00:00", "--today", "2026-08-20"])
        output = stdout.getvalue()
        self.assertEqual(result, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("◇ 判断 2</span>", output)
        self.assertIn("いずれか1つへ進む", output)
        self.assertEqual(output.count('class="chip fork decision"'), 1)
        self.assertIn('title="判断基準: 検算結果"', output)
        self.assertIn("◇ 出口（検算結果・いずれか1つへ進む）", output)

    def test_legacy_stream_has_no_fork_chip(self):
        for lane_count in (1, 2, 3):
            with self.subTest(lane_count=lane_count):
                manifest = make_manifest(exit_count=None, lane_count=lane_count)
                self.assertEqual(validate_with_warnings(manifest), ([], ""))
                output = make_board(manifest).render("", "")
                self.assertNotIn("chip fork", output)
                self.assertNotIn('data-flow-mode="graph"', output)
                self.assertNotIn("種別未指定", output)

    def test_fanout_chips_headings_and_kinds(self):
        for fanout, chip, heading, kinds in (
            ("exclusive", '<span class="chip fork decision">◇ 判断 2</span>',
             "◇ 出口（いずれか1つへ進む）", ("分岐", "次段")),
            ("parallel", '<span class="chip fork parallel">＋ 並列 2</span>',
             "＋ 出口（すべてへ進む）", ("並列", "並列")),
            (None, '<span class="chip fork">分岐 2</span>', "出口", ("分岐", "次段")),
        ):
            with self.subTest(fanout=fanout):
                fields = {"fanout": fanout} if fanout else {}
                output = make_board(make_manifest(**fields)).render("", "")
                self.assertIn(chip, output)
                self.assertIn(f'<div class="exitlist"><b>{heading}</b>', output)
                self.assertEqual(re.findall(r'<span class="routekind">（(.*?)）</span>', output), list(kinds))
                self.assertNotIn("既定の進行", output)
                self.assertIn("◇ 判断 N=結果でいずれか1本を選ぶ／＋ 並列 N=すべてへ進む／分岐 N=種別未指定", output)

    def test_criterion_is_escaped_in_title_and_heading(self):
        criterion = '"<結果>&'
        output = make_board(make_manifest(fanout="exclusive", criterion=criterion)).render("", "")
        self.assertIn('title="判断基準: &quot;&lt;結果&gt;&amp;"', output)
        self.assertIn("◇ 出口（&quot;&lt;結果&gt;&amp;・いずれか1つへ進む）", output)
        self.assertNotIn(criterion, output)

    def test_compact_exits_and_geometry_do_not_depend_on_fanout(self):
        baseline = None
        for fanout in (None, "exclusive", "parallel"):
            fields = {"fanout": fanout} if fanout else {}
            manifest = make_manifest(lane_count=3, **fields)
            board = make_board(manifest)
            output = board.render("", "")
            compact = re.findall(r'<ol class="exits">.*?</ol>', output)
            self.assertEqual(len(compact), 1)
            self.assertEqual(compact[0].count('<li class="exit '), 2)
            self.assertNotIn("出口", compact[0])
            geometry = (board.graph_routes(manifest["flow"]["streams"][0]["boxes"], board.edges),
                        re.findall(r'style="[^"]*(?:grid-row|grid-template|--departure)[^"]*"', output))
            if baseline is None:
                baseline = (compact, geometry)
            self.assertEqual((compact, geometry), baseline)

    def test_return_and_exception_words_take_precedence(self):
        for fanout, expected in (("exclusive", ["例外", "戻り", "次段"]),
                                 ("parallel", ["並列", "戻り", "並列"])):
            with self.subTest(fanout=fanout):
                manifest = make_manifest(3)
                boxes = manifest["flow"]["streams"][0]["boxes"]
                del boxes[0]["next"]
                boxes[1].update(fanout=fanout, next=[
                    {"to": "b1", "label": "Retry"}, {"to": "b3", "label": "A"},
                    {"to": "b4", "label": "B"}])
                if fanout == "exclusive":
                    boxes[1]["criterion"] = "結果"
                    boxes[1]["next"][0]["kind"] = "alt"
                    boxes[1]["next"][2]["kind"] = "alt"
                self.assertEqual(validate_with_warnings(manifest)[0], [])
                output = make_board(manifest).render("", "")
                self.assertEqual(re.findall(r'<span class="routekind">（(.*?)）</span>', output), expected)

    def test_edges_do_not_change_dependencies_or_task_states(self):
        for fanout in ("exclusive", "parallel"):
            with self.subTest(fanout=fanout):
                manifest = make_manifest(fanout=fanout)
                boxes = manifest["flow"]["streams"][0]["boxes"]
                boxes[1]["optional"] = True
                boxes[2]["next"] = [{"to": "b1", "label": "Retry"}]
                manifest["tasks"][2]["deps"] = [{"id": 2}]
                original = copy.deepcopy(manifest)
                for second_closed, expected in ((False, ["done", "ready", "blocked"]),
                                                 (True, ["done", "done", "ready"])):
                    state_rows = [{"number": 1, "state": "CLOSED"},
                                  {"number": 2, "state": "CLOSED" if second_closed else "OPEN"}]
                    board = make_board(manifest, state_rows)
                    board.render("", "")
                    self.assertEqual([board.box_state(box) for box in boxes], expected)
                    self.assertEqual([board.deps_of(task) for task in manifest["tasks"]], [[], [], [2]])
                    self.assertEqual(manifest, original)


class GraphExitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manifest = render.load_manifest(ROOT / "examples/board.yaml")
        state_rows = json.loads((ROOT / "examples/state.json").read_text(encoding="utf-8"))
        cls.output = make_board(manifest, state_rows).render("", "")

    def graph_checks(self, output):
        parser = check_board.GridParser()
        parser.feed(output)
        self.assertEqual(len(parser.grids), 1)
        checks = []
        check_board.check_graph(parser.grids[0], 1,
                                lambda label, good: checks.append((label, good)))
        return checks

    def test_example_main_exit_is_last_in_compact_and_full_lists(self):
        for pattern in (
            r'<div class="exitcell" style="grid-row:ex-3;[^"]*"><ol class="exits">(.*?)</ol>',
            r'<div class="boxbody" id="boxbody-report-r3"[^>]*><div class="exitlist">.*?<ol>(.*?)</ol>',
        ):
            with self.subTest(pattern=pattern):
                exits = re.search(pattern, self.output)
                self.assertIsNotNone(exits)
                self.assertEqual(re.findall(r'<b class="cond">(.*?)</b>', exits.group(1)),
                                 ["差異あり", "一致"])
                self.assertEqual(re.findall(r'<li class="exit ([^"]*)">', exits.group(1)),
                                 ["return alt", "adjacent"])

    def test_example_stem_starts_at_last_exit_bottom(self):
        stem = re.search(r'<div class="edge stem main"[^>]*data-edge-from="box-report-r3" '
                         r'data-edge-to="box-report-r4"><span class="v" style="([^"]*)">',
                         self.output)
        self.assertIsNotNone(stem)
        self.assertEqual(stem.group(1),
                         "grid-row:ex-3 / ch-4;grid-column:7 / 8;top:calc(1*24px + 24px)")

    def test_three_exits_put_adjacent_after_skip_and_return(self):
        manifest = make_manifest(3)
        boxes = manifest["flow"]["streams"][0]["boxes"]
        del boxes[0]["next"]
        boxes[1].update(fanout="exclusive", criterion="結果", next=[
            {"to": "b3", "label": "Continue"}, {"to": "b1", "label": "Retry"},
            {"to": "b4", "label": "Skip"}])
        self.assertEqual(validate_with_warnings(manifest)[0], [])
        board = make_board(manifest)
        routes, rails_l, rails_r = board.graph_routes(boxes, board.edges)
        outgoing = sorted((route for route in routes if route["from"] == "b2"),
                          key=lambda route: route["exit_index"])
        self.assertEqual([(route["to"], route["route"], route["exit_index"])
                          for route in outgoing],
                         [("b4", "skip", 0), ("b1", "return", 1), ("b3", "adjacent", 2)])
        self.assertEqual((rails_l, rails_r), (1, 1))
        self.assertEqual([(route.get("rail"), route["arrival"]) for route in outgoing],
                         [(1, 22), (1, 14), (None, 14)])
        output = board.render("", "")
        self.assertIn("top:calc(2*24px + 24px)", output)
        self.assertTrue(all(good for _, good in self.graph_checks(output)))

    def test_stem_check_uses_compact_exit_count(self):
        checks = self.graph_checks(self.output)
        self.assertTrue(all(good for _, good in checks), checks)
        self.assertEqual(sum("top 最後の出口の底" in label for label, _ in checks), 1)

    def test_stem_check_rejects_wrong_or_missing_top(self):
        correct = "top:calc(1*24px + 24px)"
        self.assertEqual(self.output.count(correct), 1)
        for incorrect in ("top:calc(0*24px + 13px)", "top:calc(1*24px + 13px)",
                          "top:calc(0*24px + 24px)", "top:calc(2*24px + 24px)", ""):
            with self.subTest(top=incorrect):
                checks = self.graph_checks(self.output.replace(correct, incorrect))
                failures = [label for label, good in checks if not good]
                self.assertEqual(len(failures), 1, failures)
                self.assertIn("top 最後の出口の底", failures[0])

    def test_stem_check_rejects_empty_exit_row(self):
        output, count = re.subn(
            r'(<div class="exitcell" style="grid-row:ex-3;[^"]*"><ol class="exits">).*?(</ol>)',
            r'\1\2', self.output)
        self.assertEqual(count, 1)
        output = output.replace("top:calc(1*24px + 24px)", "top:calc(-1*24px + 24px)")
        failures = [label for label, good in self.graph_checks(output) if not good]
        self.assertEqual(len(failures), 1, failures)
        self.assertIn("top 最後の出口の底", failures[0])

    def test_stem_check_keeps_grid_row_and_column_validation(self):
        correct = "grid-row:ex-3 / ch-4;grid-column:7 / 8;top:"
        self.assertEqual(self.output.count(correct), 1)
        for incorrect, expected in (
            ("grid-row:ex-3 / ch-3;grid-column:7 / 8;top:", "grid-row 両端"),
            ("grid-row:ex-3 / ch-4;grid-column:6 / 7;top:", "grid-column 両端"),
        ):
            with self.subTest(placement=incorrect):
                failures = [label for label, good in self.graph_checks(
                    self.output.replace(correct, incorrect)) if not good]
                self.assertEqual(len(failures), 1, failures)
                self.assertIn(expected, failures[0])


if __name__ == "__main__":
    unittest.main()
