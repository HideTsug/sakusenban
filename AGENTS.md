---
audience: ai
---

# AGENTS.md — AI向け入口

このリポジトリは「作戦盤」（人間+AI協働のスイムレーンタスク盤）の生成キット。あなた（AI）が利用者のプロジェクトの盤を作る・更新するときは、まず `docs/authoring-guide.md` を読むこと。設計思想・作成手順・配色規範・チェック報告の受け方まで自己完結で書いてある。

## 鉄則（ガイドの要約）

- 盤は生成ビュー。正本（GitHub Issues / state.json）を先に更新し、盤は再生成する。生成HTMLの手編集は禁止
- タスクを発明しない。全タスクに出典を付ける
- 人間レーンには人間にしかできないことだけを置く
- チェック報告を受けたら、正本で検証してから反映する。未検証のものを完了扱いしない

## 変更時の検証

```bash
bash scripts/check.sh   # render 成功 + templates/template.html の鮮度 + placeholder 残存
```

`assets/style.css` / `assets/board.js` が機構の唯一の正本。`templates/template.html` は生成物なので直接編集せず、examples を編集して check.sh の指示どおり再生成する。
