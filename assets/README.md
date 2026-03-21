# Video Assets

動画生成 (Phase D) で使用するアセットディレクトリ。

## ディレクトリ構成

```
assets/
├── backgrounds/          シーン別背景画像 (1920x1080 推奨)
│   ├── daily.png         日常シーン
│   ├── battle.png        戦闘シーン
│   ├── romance.png       ロマンスシーン
│   ├── tense.png         緊張シーン
│   ├── comedy.png        コメディシーン
│   ├── sad.png           哀愁シーン
│   └── horror.png        ホラーシーン
├── portraits/            キャラクター立ち絵 (RGBA PNG, 透過背景)
│   └── {character_name}/
│       ├── neutral.png   デフォルト表情
│       ├── happy.png     喜び
│       ├── sad.png       悲しみ
│       ├── angry.png     怒り
│       └── default.png   フォールバック
├── bgm/                  シーン別BGM (mp3/wav/ogg)
│   ├── daily.mp3
│   ├── battle.mp3
│   └── default.mp3       フォールバックBGM
└── se/                   サウンドエフェクト
    └── scene_break.mp3   シーン切替SE
```

## アセット解決ルール

### 背景
1. `backgrounds/{scene}.png|jpg|webp` → 見つかればそれを使用
2. 見つからなければ `config/default.yaml` の `scene_colors` から単色背景を生成

### 立ち絵
1. `portraits/{speaker}/{emotion}.png` → 正確な感情一致
2. `portraits/{speaker}/neutral.png` → 感情フォールバック
3. `portraits/{speaker}/default.png` → 最終フォールバック
4. ディレクトリが無ければ立ち絵なし（字幕のみモード）

### BGM
1. `bgm/{scene}.mp3|wav` → シーン一致
2. `bgm/default.mp3|wav` → フォールバック
3. 無ければBGMなし

## 同梱サンプル

- `backgrounds/` — 7シーン分のプレースホルダー背景（グラデーション付き単色）
- `portraits/SOMS/` — SOMSキャラクターの感情別シルエット（5種）
- `bgm/`, `se/` — 空（ユーザーが自分の音源を配置）
