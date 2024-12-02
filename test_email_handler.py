from app import create_app, db
from models import EmailMessage
from sqlalchemy import text
import email
from email.message import EmailMessage as EmailMsg
from datetime import datetime

def test_parse_email():
    app = create_app()
    
    with app.app_context():
        try:
            # データベースをクリア
            db.session.execute(text('DELETE FROM email_message;'))
            db.session.commit()
            
            # テストメールを作成（日本語含む）
            msg = EmailMsg()
            msg['Subject'] = 'テスト件名：確認用メッセージ'
            msg['From'] = '"テスト送信者" <sender@example.com>'
            msg['To'] = '"テスト受信者" <recipient@example.com>'
            msg['Message-ID'] = '<test123@example.com>'
            msg['Date'] = 'Thu, 1 Dec 2024 10:00:00 +0900'
            msg.set_content('これはテスト本文です。\nメッセージの保存テスト用。')
            email_body = msg.as_bytes()
            
            # EmailHandlerのインスタンス作成（IMAP接続なし）
            handler = EmailHandler()
            
            # メッセージのパース
            parsed_msg = handler.parse_email_message(email_body)
            
            print("\n=== パース結果 ===")
            print(f"Subject: {parsed_msg['subject']}")
            print(f"From: {parsed_msg['from']}")
            print(f"To: {parsed_msg['to']}")
            print(f"Body: {parsed_msg['body'][:100]}")
            print(f"Message-ID: {parsed_msg['message_id']}")
            
            # データベースへの保存
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
            
            # 保存されたデータを確認
            saved_message = EmailMessage.query.filter_by(message_id=parsed_msg['message_id']).first()
            
            print("\n=== 保存されたデータ ===")
            print(f"Subject: {saved_message.subject}")
            print(f"From: {saved_message.from_address}")
            print(f"To: {saved_message.to_address}")
            print(f"Body length: {len(saved_message.body)}")
            
            assert saved_message is not None, "メッセージが保存されていません"
            assert saved_message.subject == parsed_msg['subject'], "件名が一致しません"
            assert saved_message.body == parsed_msg['body'], "本文が一致しません"
            assert saved_message.to_address == parsed_msg['to'], "宛先が一致しません"
            
            print("\n=== テスト成功 ===")
            print("メッセージが正しくパースされ、保存されました")
            
        except Exception as e:
            print(f"テストエラー: {str(e)}")
            raise e

def test_message_search():
    """メッセージ検索機能のテスト"""
    app = create_app()
    
    with app.app_context():
        try:
            # データベースをクリア
            db.session.execute(text('DELETE FROM email_message;'))
            db.session.commit()
            
            # テストデータの作成
            messages = [
                {
                    'subject': 'テスト件名：重要なお知らせ',
                    'body': 'これは重要なテスト本文です。',
                    'from_address': 'sender1@example.com',
                    'to_address': 'recipient1@example.com',
                    'message_id': '<test1@example.com>',
                },
                {
                    'subject': '会議の案内',
                    'body': 'これは重要な会議についてのお知らせです。',
                    'from_address': 'sender2@example.com',
                    'to_address': 'recipient2@example.com',
                    'message_id': '<test2@example.com>',
                },
                {
                    'subject': '一般的な連絡',
                    'body': 'これは一般的な連絡事項です。',
                    'from_address': 'sender3@example.com',
                    'to_address': 'recipient3@example.com',
                    'message_id': '<test3@example.com>',
                }
            ]
            
            # テストデータの保存
            for msg_data in messages:
                message = EmailMessage(
                    message_id=msg_data['message_id'],
                    from_address=msg_data['from_address'],
                    to_address=msg_data['to_address'],
                    subject=msg_data['subject'],
                    body=msg_data['body'],
                    date=datetime.utcnow(),
                    is_sent=False,
                    folder='INBOX',
                    last_sync=datetime.utcnow()
                )
                db.session.add(message)
            db.session.commit()
            
            # 検索テスト1：件名での検索
            results = EmailMessage.query.filter(
                EmailMessage.subject.ilike('%重要%')
            ).all()
            assert len(results) == 1, "件名での検索結果が正しくありません"
            assert results[0].subject == 'テスト件名：重要なお知らせ'
            
            # 検索テスト2：本文での検索
            results = EmailMessage.query.filter(
                EmailMessage.body.ilike('%重要%')
            ).all()
            assert len(results) == 2, "本文での検索結果が正しくありません"
            
            print("\n=== 検索機能テスト成功 ===")
            print("メッセージ検索が正しく動作しています")
            
        except Exception as e:
            print(f"検索機能テストエラー: {str(e)}")
            raise e

if __name__ == '__main__':
    test_parse_email()
    test_message_search()
