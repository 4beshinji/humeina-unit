# Firefox拡張

`browser-extension/firefox/`は、Firefoxで表示中のページまたは選択範囲をローカルの
`yomiage serve`へ送り、VoiSona / VOICEVOXで読み上げるWebExtensionです。公開ページのURLだけを
渡す簡易クライアントではありません。ログイン済みページやJavaScriptで描画されたページでは、
Firefox内のDOMから本文を抽出して`POST /api/yomiage/read-text`へ送ります。

## 前提と起動

先に利用するTTSエンジンとhumeina-unitを起動します。

```bash
uv run yomiage serve --host 127.0.0.1 --port 8030
```

既定の接続先は`http://127.0.0.1:8030`です。別portを使う場合は拡張の設定画面で変更します。
拡張は安全のため`localhost`または`127.0.0.1`のHTTPサーバーにだけ接続します。

## 開発版の読み込み

1. Firefoxで`about:debugging#/runtime/this-firefox`を開きます。
2. 「一時的なアドオンを読み込む」を選びます。
3. `browser-extension/firefox/manifest.json`を指定します。
4. ツールバーへHumeina Unit Readerをピン留めします。

一時的なアドオンはFirefoxの再起動で解除されます。継続配布する場合はMozilla Add-onsの署名、
または管理環境のExtensionSettings policyを使ってください。開発中はMozillaの`web-ext`を使って
次のように起動することもできます。

```bash
npx web-ext run --source-dir browser-extension/firefox
```

## 操作

- ツールバーボタン: 選択範囲があれば選択範囲、なければ抽出したページ本文を読み上げます。
- 「ページ本文」: 選択範囲を無視し、記事本文を抽出します。
- 「URLから取得」: URLだけを`/api/yomiage/read`へ送り、サーバー側のサイト別抽出を使います。
- 右クリック: ページ本文または選択範囲を直接開始できます。
- `Alt+Shift+R`: 既定の取得方法で開始します。
- `Alt+Shift+S`: 読み上げを停止します。
- ポップアップ: VoiSona/VOICEVOXの選択、一時停止、再開、停止、進捗確認ができます。

音声エンジンの「自動」は`config/default.yaml`の`tts.primary_provider`を使い、失敗時は
`fallback_provider`へ切り替えます。VoiSonaまたはVOICEVOXを明示した場合は、そのProviderだけを
使用するため、自動fallbackは行いません。

## 本文抽出

拡張は`article`、`main`、`role=main`、一般的な記事本文classを候補にし、本文量、段落量、
リンク密度から読み上げ対象を選びます。ナビゲーション、広告、コメント、関連記事、フォーム、
コードなどは除外します。抽出結果はローカルAPIへだけ送られ、拡張自身は本文をstorageへ保存
しません。

抽出が適切でないサイトでは、次の順に切り替えてください。

1. 読みたい本文を選択して実行する。
2. 公開ページなら「URLから取得」でサーバー側の`trafilatura`抽出を使う。
3. サイト固有の構造が安定している場合は`content/extractor.js`の候補selectorを追加する。

Firefoxの内部ページ、Mozillaの保護ページ、PDF viewerなど、content scriptを注入できないページは
DOM抽出できません。公開HTTP(S) URLなら「URLから取得」を試せます。

## 権限とデータ

拡張は常時すべてのページを読む`<all_urls>`権限を要求しません。ユーザーがツールバーボタン、
右クリックメニュー、ショートカットを操作した現在のタブだけを`activeTab`で読みます。

- `activeTab` / `scripting`: 操作対象タブから本文または選択範囲を取得します。
- `host_permissions`: `127.0.0.1`と`localhost`のローカルAPIへ接続します。
- `storage`: 接続先、Provider、取得方法、任意のBearer tokenをFirefoxローカル領域へ保存します。
- `menus` / `notifications`: 右クリック操作とbackground操作の失敗通知に使います。

API tokenは`storage.local`へ保存し、Firefox Syncへ送りません。通常はloopback bindのまま使い、
LANやインターネットへAPIを公開しないでください。

## 検証とパッケージ

リポジトリ内の軽量検証はmanifest、必要ファイル、権限境界、JavaScript構文を確認します。

```bash
uv run python -m pytest -q tests/test_firefox_extension.py
node --check browser-extension/firefox/background.js
node --check browser-extension/firefox/content/extractor.js
node --check browser-extension/firefox/popup/popup.js
node --check browser-extension/firefox/options/options.js
```

`web-ext`を導入済みなら、Mozilla固有のmanifest検証と配布用zip作成もできます。

```bash
npx web-ext lint --source-dir browser-extension/firefox
npx web-ext build --source-dir browser-extension/firefox --artifacts-dir output/firefox-extension
```

## トラブル対応

- 「サーバー未接続」: `uv run yomiage serve`、port、設定画面のURLを確認します。
- Providerが選択不可: `/api/yomiage/capabilities`に出ていません。`tts.primary_provider`と
  `fallback_provider`を確認してサーバーを再起動します。
- `401`/`403`: サーバーの`YOMIAGE_API_TOKEN`と拡張設定のtokenを一致させます。
- `413`: 本文が`server.max_text_chars`を超えています。選択範囲を使うか上限を調整します。
- 開始後に失敗: ポップアップを開くと`/api/yomiage/jobs/{job_id}`のerrorを表示します。
