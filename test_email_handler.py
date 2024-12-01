from app import create_app, db
from models import EmailMessage
from email_handler import EmailHandler
import email
from email.message import EmailMessage as EmailMsg
from datetime import datetime

def create_test_email():
    # テスト用のメールメッセージを作成
    msg = EmailMsg()
    msg['Subject'] = 'テスト件名'
    msg['From'] = 'sender@example.com'
    msg['To'] = 'recipient@example.com'
    msg['Message-ID'] = '<test123@example.com>'
    msg['Date'] = 'Thu, 1 Dec 2024 10:00:00 +0900'
    msg.set_content('テスト本文')
    return msg.as_bytes()

def test_parse_email():
    app = create_app()
    
    with app.app_context():
        # テストデータ作成
        email_body = create_test_email()
        
        # EmailHandlerのインスタンス作成（接続なし）
        handler = EmailHandler(None, None, None)
        
        try:
            # メッセージのパース
            parsed_msg = handler.parse_email_message(email_body)
            
            print("=== パース結果 ===")
            print(f"Subject: {parsed_msg['subject']}")
            print(f"From: {parsed_msg['from']}")
            print(f"To: {parsed_msg['to']}")
            print(f"Body: {parsed_msg['body'][:100]}")
            print(f"Message-ID: {parsed_msg['message_id']}")
            
            # 結果の検証
            assert parsed_msg['subject'] is not None, "Subject should not be None"
            assert parsed_msg['body'] is not None, "Body should not be None"
            assert parsed_msg['to'] is not None, "To should not be None"
            
            # データベースへの保存テスト
            message = EmailMessage(
                message_id=parsed_msg['message_id'],
                from_address=parsed_msg['from'],
                to_address=parsed_msg['to'],
                subject=parsed_msg['subject'],
                body=parsed_msg['body'],
                date=parsed_msg['date'],
                is_sent=False,
                folder='INBOX',
                last_sync=datetime.utcnow()
            )
            
            db.session.add(message)
            db.session.commit()
            
            # 保存されたデータの確認
            saved_message = EmailMessage.query.filter_by(message_id=parsed_msg['message_id']).first()
            assert saved_message is not None
            assert saved_message.subject == parsed_msg['subject']
            assert saved_message.body == parsed_msg['body']
            assert saved_message.to_address == parsed_msg['to']
            
            print("\n=== テスト成功 ===")
            print("メッセージが正しくパースされ、保存されました")
            
        except Exception as e:
            print(f"テストエラー: {str(e)}")
            raise e

if __name__ == '__main__':
    test_parse_email()
