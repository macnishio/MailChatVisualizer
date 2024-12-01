import imaplib
import email
from email.header import decode_header
import datetime
from email.utils import parsedate_to_datetime

class EmailHandler:
    def __init__(self, email_address, password, imap_server):
        self.conn = imaplib.IMAP4_SSL(imap_server)
        self.conn.login(email_address, password)
        self.email_address = email_address

    def disconnect(self):
        try:
            self.conn.close()
            self.conn.logout()
        except:
            pass

    def decode_str(self, s):
        if s is None:
            return ""
        decoded_parts = decode_header(s)
        result = ""
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                if encoding:
                    result += part.decode(encoding)
                else:
                    result += part.decode()
            else:
                result += part
        return result

    def get_contacts(self):
        contacts = set()
        for folder in ['INBOX', '"[Gmail]/Sent Mail"']:
            try:
                self.conn.select(folder)
                _, messages = self.conn.search(None, 'ALL')
                
                for num in messages[0].split()[-100:]:  # Last 100 emails
                    _, msg_data = self.conn.fetch(num, '(RFC822)')
                    email_body = msg_data[0][1]
                    msg = email.message_from_bytes(email_body)
                    
                    if folder == 'INBOX':
                        from_addr = self.decode_str(msg['from'])
                        if from_addr:
                            contacts.add(from_addr)
                    else:
                        to_addr = self.decode_str(msg['to'])
                        if to_addr:
                            contacts.add(to_addr)
            except:
                continue
                
        return sorted(list(contacts))

    def get_conversation(self, contact_email, search_query=None):
        messages = []
        
        for folder in ['INBOX', '"[Gmail]/Sent Mail"']:
            try:
                self.conn.select(folder)
                search_criterion = f'(OR FROM "{contact_email}" TO "{contact_email}")'
                if search_query:
                    search_criterion = f'({search_criterion} SUBJECT "{search_query}" TEXT "{search_query}")'
                _, message_numbers = self.conn.search(None, search_criterion)
                
                for num in message_numbers[0].split():
                    _, msg_data = self.conn.fetch(num, '(RFC822)')
                    email_body = msg_data[0][1]
                    msg = email.message_from_bytes(email_body)
                    
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode()
                                break
                    else:
                        body = msg.get_payload(decode=True).decode()
                    
                    # 検索クエリがある場合、本文で絞り込み
                    if search_query and search_query.lower() not in body.lower():
                        continue
                    
                    date = parsedate_to_datetime(msg['date'])
                    from_addr = self.decode_str(msg['from'])
                    is_sent = self.email_address in from_addr
                    
                    messages.append({
                        'body': body,
                        'date': date,
                        'is_sent': is_sent,
                        'from': from_addr
                    })
            except Exception as e:
                print(f"Error in get_conversation: {str(e)}")
                continue
        
        return sorted(messages, key=lambda x: x['date'])
