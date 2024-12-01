from celery import Celery
from email_handler import EmailHandler
from models import EmailMessage
from database import db
from datetime import datetime
import imaplib

celery = Celery('tasks', broker='redis://localhost:6379/0')

@celery.task
def sync_emails(email, password, imap_server):
    try:
        handler = EmailHandler(email, password, imap_server)
        folders = [b'INBOX']
        sent_folder = handler.get_gmail_folders()
        if sent_folder:
            folders.append(sent_folder)

        for folder in folders:
            if not handler.select_folder(folder):
                continue

            try:
                _, messages = handler.conn.search(None, 'ALL')
                for num in messages[0].split():
                    try:
                        _, msg_data = handler.conn.fetch(num, '(RFC822)')
                        email_body = msg_data[0][1]
                        msg = handler.parse_email_message(email_body)
                        
                        # Check if message already exists
                        existing_message = EmailMessage.query.filter_by(message_id=msg['message_id']).first()
                        if not existing_message:
                            new_message = EmailMessage(
                                message_id=msg['message_id'],
                                from_address=msg['from'],
                                to_address=msg['to'],
                                subject=msg['subject'],
                                body=msg['body'],
                                date=msg['date'],
                                is_sent=msg['is_sent'],
                                folder=str(folder)
                            )
                            db.session.add(new_message)
                            
                    except Exception as e:
                        print(f"Message processing error: {str(e)}")
                
                db.session.commit()
                
            except Exception as e:
                print(f"Folder processing error ({folder}): {str(e)}")
                db.session.rollback()
                
        handler.disconnect()
        
    except Exception as e:
        print(f"Email sync error: {str(e)}")
        db.session.rollback()

# Schedule periodic sync
@celery.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    sender.add_periodic_task(300.0, sync_emails.s(), name='sync-emails-every-5-minutes')
