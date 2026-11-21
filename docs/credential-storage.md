# Coding Agent時代のAPI key保管方針

> 調査日: 2026-08-30。Coding Agentを前提にした現行の公式資料だけを比較対象とした。

## 調査結果

- [OpenAI Codex authentication](https://developers.openai.com/codex/auth) は、OS資格情報ストアを
  選べる`keyring`、利用可能ならOSストアへ保存する`auto`、plaintextの`auth.json`へ保存する
  `file`を区別しています。file方式はパスワード同様に扱い、commit・ticket・chatへ出さない
  よう明記しています。外部Providerには秘密値そのものではなく環境変数名を指定できます。
- [Claude Code security](https://code.claude.com/docs/en/security) と
  [authentication](https://code.claude.com/docs/en/iam) は、macOSではKeychain、Linuxでは
  mode 0600の専用ファイルを使用します。vault等から短命tokenを得る`apiKeyHelper`も用意し、
  cloud agentでは実tokenをsandboxへ直接渡さず、scoped credentialをproxyで変換します。
- [GitHub Copilot cloud agent secrets](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/configure-secrets-and-variables)
  は、Actions/Codespacesとは分離したAgents専用secretをrepository/organization単位で付与し、
  session logでは値をmaskします。MCP用secretはprefixで対象プロセスだけへ限定します。
- [OpenCode providers](https://opencode.ai/docs/providers/) の`/connect`は対話入力を採用し、資格情報を
  `~/.local/share/opencode/auth.json`へ保存します。現環境の同ファイルも所有者専用mode 0600です。

## このプロジェクトでの決定

ローカル対話利用はKWalletを第一候補にします。API keyはコマンド引数、YAML、`.env`、ログ、
manifestへ書きません。`kwallet-query`には標準入力で渡し、実行時だけ標準出力をメモリへ読み、
OpenCode Go HTTP transportへ渡します。

KWalletが使えない場合は、次の順に選べます。

1. OpenCodeのmode 0600認証ファイルを参照する。
2. `OPENCODE_GO_API_KEY`を起動プロセスの環境へ注入する。

ユーザーYAMLへ保存するのは`source`、wallet/folder/entry、環境変数名などの参照情報だけです。
対話設定は次で起動します。

```bash
uv run yomiage configure
```

## 脅威境界

KWalletはディスク上の平文漏えいには強い一方、同じログインユーザーとして動き、unlock済みwalletを
読めるCoding Agentから完全に隔離するものではありません。Agentにも絶対に長期keyを見せない必要が
ある運用では、別OSユーザーまたは別ホストのgatewayにkeyを置き、用途・宛先・利用額を制限した
短命credentialだけをアプリへ渡す必要があります。OpenCode Go公式資料からは短命token発行機構を
確認できなかったため、本実装ではKWallet + 最小権限key + 定期rotationを現実的な標準とします。

