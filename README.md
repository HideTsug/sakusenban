# sakusenban（作戦盤）

**Expandable swimlane task boards for human + AI collaboration.** One self-contained HTML file: lanes per actor, boxes in dependency order, click a box to see steps / acceptance criteria / issue links, tick checkboxes and paste a machine-readable report back to your AI. State lives in GitHub Issues (or a local JSON file) — the board is always a generated view, never the source of truth.

複数の実行主体（人間・AI・相手側の人間/AI）が絡む仕事の段取りを、レーン別・依存順の「箱」に落とす業務フロー盤です。1枚の自己完結HTMLとして生成され、開いた人がチャット履歴を遡らずに「今なにをやるか」から動けます。

## 特徴

- **盤は生成ビュー、正本は外部SSoT** — 状態は GitHub Issues（またはローカル state.json）だけが持ち、盤は毎回再生成。手編集禁止
- **チェック→報告→反映のループ** — チェックは端末ローカルの報告シグナル。「AIへ報告をコピー」が機械可読テキストを吐き、AIが正本を検証・更新して盤を再生成する
- **人間レーンとAIレーンの分離** — 人間の箱は「人間にしかできないこと」だけ。束（bundle）で同質の人間作業を1回の腰上げにまとめる
- **依存の自動導出** — ready / blocked / done は deps と状態から renderer が計算する
- **外部リソース参照ゼロ** — 生成HTMLは自己完結（CSP の厳しい環境・オフラインでも開ける）

## 使い方

```bash
git clone https://github.com/HideTsug/sakusenban.git
cd sakusenban

# 1. 構造を書く（examples/board.yaml を複製して編集。スキーマ: schema/sakusenban.schema.json）
# 2. 状態を取る
gh issue list --state all -L 200 --json number,title,state > state.json
#    GitHub を使わない場合は同じ形式の JSON を手で維持すればよい（examples/state.json 参照）
# 3. 生成
python3 scripts/render.py board.yaml state.json > board.html

# Artifact 公開用（骨格なし断片）
python3 scripts/render.py board.yaml state.json --fragment > board-artifact.html
```

依存: Python 3（YAML マニフェストを使う場合のみ `pip install pyyaml`。JSON マニフェストなら標準ライブラリのみ）。

## AI に作らせる（推奨）

お使いの AI アシスタント（Claude Code 等）に、次の文をそのまま貼り付けてください。

> 複数人とAIでやる仕事の段取りを「作戦盤」にしたい。
> https://raw.githubusercontent.com/HideTsug/sakusenban/main/docs/authoring-guide.md
> を読んで、その流儀で私のプロジェクトの盤を作ってください。

## 構成

| パス | 中身 |
|---|---|
| `scripts/render.py` | レンダラ（board.yaml + state.json → 自己完結HTML） |
| `schema/sakusenban.schema.json` | 構造マニフェストのスキーマ |
| `assets/style.css` / `assets/board.js` | 盤の機構（CSS/JS の唯一の正本） |
| `templates/template.html` | 生成サンプル — file:// / ローカル配信用（完全な文書骨格つき） |
| `templates/fragment.html` | 生成サンプル — Artifact 公開用（骨格なし断片） |
| `docs/authoring-guide.md` | AI向けオーサリングガイド（設計思想・配色規範・報告プロトコル） |
| `examples/` | 汎用サンプル（人間2人+AI2体の月次レポート段取り） |
| `scripts/check.sh` | 機械検証ゲート（render 成功・両テンプレートの鮮度・出力モード・プレースホルダ） |

## ライセンス

MIT
