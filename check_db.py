from app import create_app, db
from models import EmailMessage
from sqlalchemy import text

app = create_app()

with app.app_context():
    # メッセージテーブルの内容を確認
    result = db.session.execute(text('SELECT id, message_id, subject, body, from_address, to_address FROM email_message LIMIT 5'))
    
    print("\n=== データベース内容 ===")
    for row in result:
        print(f"ID: {row.id}")
        print(f"Message ID: {row.message_id}")
        print(f"Subject: {row.subject}")
        print(f"Body: {row.body[:100] if row.body else None}")  # 本文は最初の100文字のみ表示
        print(f"From: {row.from_address}")
        print(f"To: {row.to_address}")
        print("---")
