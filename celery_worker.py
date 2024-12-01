from celery import Celery
from datetime import datetime, timedelta
from sqlalchemy import or_
from models import EmailMessage
from database import session_scope
from email_handler import EmailHandler

def make_celery(app_name=__name__):
    celery = Celery(
        app_name,
        broker='redis://localhost:6379/0',
        backend='redis://localhost:6379/0'
    )
    return celery

celery = make_celery('email_tasks')

@celery.task
def sync_emails(email, password, imap_server):
    """メールを同期するタスク"""
    from app import create_app
    import time
    
    start_time = time.time()
    print(f"メール同期開始: {email}, サーバー: {imap_server}")
    app = create_app()
    
    with app.app_context():
        try:
            handler = EmailHandler(email, password, imap_server)
            print("IMAPサーバーに接続成功")
            
            folders = [b'INBOX']
            sent_folder = handler.get_gmail_folders()
            if sent_folder:
                folders.append(sent_folder)
                print(f"送信済みフォルダを追加: {sent_folder}")
            
            stats = {
                'total_processed': 0,
                'total_new': 0,
                'total_updated': 0,
                'total_errors': 0,
                'folder_stats': {}
            }

            for folder in folders:
                if not handler.select_folder(folder):
                    print(f"フォルダ選択失敗: {folder}")
                    continue
                
                folder_start_time = time.time()
                print(f"\nフォルダ処理開始: {folder}")
                stats['folder_stats'][str(folder)] = {
                    'processed': 0,
                    'new': 0,
                    'updated': 0,
                    'errors': 0
                }
                
                try:
                    _, messages = handler.conn.search(None, 'ALL')
                    message_nums = messages[0].split()
                    message_count = len(message_nums)
                    print(f"フォルダ内のメッセージ数: {message_count}")
                    
                    # バッチ処理用の設定
                    batch_size = 50
                    for i in range(0, message_count, batch_size):
                        batch_nums = message_nums[i:i + batch_size]
                        batch_start_time = time.time()
                        
                        for num in batch_nums:
                            try:
                                with session_scope() as session:
                                    _, msg_data = handler.conn.fetch(num, '(RFC822)')
                                    email_body = msg_data[0][1]
                                    
                                    # デバッグ情報
                                    print("=== メッセージ同期開始 ===")
                                    print(f"Message number: {num}")
                                    
                                    msg = handler.parse_email_message(email_body)
                                    print(f"Parsed message: {msg['subject']}, Body length: {len(msg['body'])}")
                                    
                                    stats['total_processed'] += 1
                                    stats['folder_stats'][str(folder)]['processed'] += 1
                                    
                                    if not msg['message_id']:
                                        print(f"メッセージID不足: スキップ")
                                        continue
                                    
                                    try:
                                        # トランザクション内でメッセージの存在確認と更新
                                        existing_message = session.query(EmailMessage)\
                                            .filter_by(message_id=msg['message_id'])\
                                            .first()
                                        
                                        if existing_message:
                                            if (existing_message.subject != msg['subject'] or 
                                                existing_message.body != msg['body']):
                                                print(f"メッセージ更新: ID={msg['message_id']}")
                                                existing_message.subject = msg['subject']
                                                existing_message.body = msg['body']
                                                existing_message.last_sync = datetime.utcnow()
                                                stats['total_updated'] += 1
                                                stats['folder_stats'][str(folder)]['updated'] += 1
                                        else:
                                            print(f"新規メッセージ保存: ID={msg['message_id']}")
                                            new_message = EmailMessage(
                                                message_id=msg['message_id'],
                                                from_address=msg['from'],
                                                to_address=msg['to'],
                                                subject=msg['subject'],
                                                body=msg['body'],
                                                date=msg['date'],
                                                is_sent=msg['is_sent'],
                                                folder=str(folder),
                                                last_sync=datetime.utcnow()
                                            )
                                            session.add(new_message)
                                            stats['total_new'] += 1
                                            stats['folder_stats'][str(folder)]['new'] += 1
                                        
                                        session.commit()
                                    except Exception as e:
                                        session.rollback()
                                        raise e
                                    
                            except Exception as e:
                                stats['total_errors'] += 1
                                stats['folder_stats'][str(folder)]['errors'] += 1
                                print(f"メッセージ処理エラー: {str(e)}")
                                traceback.print_exc()
                                continue
                        
                        batch_time = time.time() - batch_start_time
                        print(f"バッチ処理完了 ({i+1}-{min(i+batch_size, message_count)}/{message_count}): {batch_time:.2f}秒")
                    
                    folder_time = time.time() - folder_start_time
                    print(f"\nフォルダ処理完了: {folder}")
                    print(f"処理時間: {folder_time:.2f}秒")
                    print(f"統計: {stats['folder_stats'][str(folder)]}")
                    
                except Exception as e:
                    print(f"フォルダ処理エラー ({folder}): {str(e)}")
                    traceback.print_exc()
                    continue

            handler.disconnect()
            total_time = time.time() - start_time
            print(f"\n同期完了サマリー:")
            print(f"総処理時間: {total_time:.2f}秒")
            print(f"処理済みメッセージ数: {stats['total_processed']}")
            print(f"新規保存メッセージ数: {stats['total_new']}")
            print(f"更新メッセージ数: {stats['total_updated']}")
            print(f"エラー数: {stats['total_errors']}")
            print("\nフォルダ別統計:")
            for folder, folder_stats in stats['folder_stats'].items():
                print(f"{folder}: {folder_stats}")
                
        except Exception as e:
            print(f"メール同期エラー: {str(e)}")
            traceback.print_exc()
            raise e  # エラーを再度発生させて、Celeryに失敗を通知

@celery.task
def sync_old_contacts():
    """古いメッセージを定期的に同期"""
    from app import create_app
    
    app = create_app()
    with app.app_context():
        try:
            with session_scope() as session:
                # 24時間以上同期していないメッセージを取得
                old_messages = session.query(EmailMessage)\
                    .filter(or_(
                        EmailMessage.last_sync == None,
                        EmailMessage.last_sync <= datetime.utcnow() - timedelta(hours=24)
                    ))\
                    .all()
                
                for msg in old_messages:
                    msg.last_sync = None
                session.commit()
                
        except Exception as e:
            print(f"古いメッセージの同期エラー: {str(e)}")
            traceback.print_exc()

@celery.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    # 5分ごとのメール同期
    sender.add_periodic_task(300.0, sync_emails.s(), name='sync-emails-every-5-minutes')
    
    # 1時間ごとの古いメール同期
    sender.add_periodic_task(
        3600.0,
        sync_old_contacts.s(),
        name='sync-old-contacts-every-hour'
    )
