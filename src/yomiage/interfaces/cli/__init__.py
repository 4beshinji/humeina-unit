"""Typer CLI のコマンド実装。

各サブモジュールはコマンド定義だけを持ち、構築処理は ``composition`` と
``yomiage.bootstrap`` に委譲します。公開エントリポイントは引き続き
``yomiage.cli:app`` です。
"""
