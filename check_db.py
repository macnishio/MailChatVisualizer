from app import create_app, db
from models import EmailMessage
from sqlalchemy import text

app = create_app()
with app.app_context():
    # 送信済みメールのみを取得（is_sent = True）
    result = db.session.execute(
        text('SELECT id, message_id, subject, body, from_address, to_address, date FROM email_message WHERE is_sent = true ORDER BY date DESC LIMIT 100')
    )

    print("\n=== 送信済みメール一覧 ===")
    for row in result:
        print(f"ID: {row.id}")
        print(f"Message ID: {row.message_id}")
        print(f"Subject: {row.subject}")
        print(f"Body: {row.body[:100] if row.body else None}")  # 本文は最初の100文字のみ表示
        print(f"From: {row.from_address}")
        print(f"To: {row.to_address}")
        print(f"Date: {row.date}")
        print("---")