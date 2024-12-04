# MailChatVisualizer

IMAPメールをLINE風チャットインターフェースで表示するWebアプリケーション。

## 主な機能

- メールサーバーとの接続
- メッセージの送受信表示
- レスポンシブデザイン
- IMAPの安定性を実現する接続プーリング
- 再試行ロジック
- 状態管理（AUTH/SELECTED）
- スロットリング制御
- バッチサイズの最適化
- エラーハンドリング
- ページネーション機能（表示件数: 10/20/50/100件）
- コンタクト総件数表示
- URLパラメータ保持機能

## 技術スタック

- Python (Flask)
- IMAPプロトコル実装
- レスポンシブHTML/CSS
- モダンJavaScript
- PostgreSQL
- Celery（非同期処理）

## セットアップ

1. 依存関係のインストール:
```bash
pip install -r requirements.txt
```

2. 環境変数の設定:
`.env`ファイルを作成し、以下の変数を設定:
```
DATABASE_URL=postgresql://...
IMAP_SERVER=...
IMAP_PORT=...
```

3. データベースのマイグレーション:
```bash
flask db upgrade
```

4. アプリケーションの起動:
```bash
python app.py
```

## ライセンス

MIT License
