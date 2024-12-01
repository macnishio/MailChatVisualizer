from app import create_app, db
from models import EmailMessage

app = create_app()

with app.app_context():
    with db.session.begin():
        # メッセージの内容を確認
        messages = db.session.query(EmailMessage).all()
        print("\n=== データベース内容確認 ===")
        for msg in messages:
            print(f"ID: {msg.id}")
            print(f"Message ID: {msg.message_id}")
            print(f"Subject: {msg.subject}")
            print(f"Body length: {len(msg.body) if msg.body else 0}")
            print(f"From: {msg.from_address}")
            print(f"To: {msg.to_address}")
            print("---")
