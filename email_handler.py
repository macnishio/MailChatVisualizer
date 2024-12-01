import imaplib
import email
from email.header import decode_header
import datetime
from email.utils import parsedate_to_datetime
import re

class EmailHandler:
    def __init__(self, email_address, password, imap_server):
        self.email_address = email_address
        self.password = password
        self.imap_server = imap_server
        self.connect()

    def connect(self):
        """IMAPサーバーに接続し、認証を行う"""
        try:
            self.conn = imaplib.IMAP4_SSL(self.imap_server)
            self.conn.login(self.email_address, self.password)
        except Exception as e:
            raise ConnectionError(f"IMAP接続エラー: {str(e)}")

    def check_connection(self):
        """接続状態を確認し、必要に応じて再接続する"""
        try:
            status = self.conn.noop()[0]
            if status != 'OK':
                raise ConnectionError("接続が切断されています")
        except Exception:
            self.connect()

    def disconnect(self):
        """IMAPサーバーから切断する"""
        try:
            self.conn.close()
            self.conn.logout()
        except:
            pass

    def decode_str(self, s):
        """文字列をデコードする"""
        if s is None:
            return ""
        decoded_parts = decode_header(s)
        result = ""
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                try:
                    if encoding:
                        result += part.decode(encoding)
                    else:
                        result += part.decode('utf-8', errors='replace')
                except Exception:
                    result += part.decode('utf-8', errors='replace')
            else:
                result += str(part)
        return result

    def select_folder(self, folder):
        """フォルダーを選択する"""
        try:
            if '[Gmail]' in folder:
                # Gmailフォルダー名を適切にエンコード
                folder = folder.replace('[Gmail]', '&BBkEWQQlBDsENQQ9BD0ESwQ1-')
            status, data = self.conn.select(f'"{folder}"', readonly=True)
            if status != 'OK':
                raise Exception(f"選択エラー: {data[0].decode()}")
            return True
        except Exception as e:
            print(f"フォルダー選択エラー ({folder}): {str(e)}")
            return False

    def build_search_criteria(self, contact_email, search_query):
        """検索条件を構築する"""
        criteria = []
        if contact_email:
            # メールアドレスからの検索条件
            email_part = re.search(r'<(.+?)>', contact_email)
            if email_part:
                email = email_part.group(1)
                criteria.append(f'(OR FROM "{email}" TO "{email}")')
        if search_query:
            # 検索クエリのエンコーディング
            query = search_query.encode('utf-7').decode()
            criteria.append(f'(OR SUBJECT "{query}" BODY "{query}")')
        return '(' + ' '.join(criteria) + ')' if criteria else 'ALL'

    def get_contacts(self):
        """メールの連絡先一覧を取得する"""
        contacts = set()
        try:
            self.check_connection()
            for folder in ['INBOX', '&BBkEWQQlBDsENQQ9BD0ESwQ1-/送信済みメール']:
                if not self.select_folder(folder):
                    continue

                try:
                    _, messages = self.conn.search(None, 'ALL')
                    for num in messages[0].split()[-100:]:  # 最新100件
                        try:
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
                        except Exception as e:
                            print(f"メッセージ処理エラー: {str(e)}")
                except Exception as e:
                    print(f"フォルダー処理エラー ({folder}): {str(e)}")
        except Exception as e:
            print(f"連絡先取得エラー: {str(e)}")
            self.check_connection()

        return sorted(list(contacts))

    def get_conversation(self, contact_email, search_query=None):
        """特定の連絡先とのメール会話を取得する"""
        messages = []
        try:
            self.check_connection()
            for folder in ['INBOX', '&BBkEWQQlBDsENQQ9BD0ESwQ1-/送信済みメール']:
                if not self.select_folder(folder):
                    continue
                
                try:
                    criteria = self.build_search_criteria(contact_email, search_query)
                    _, nums = self.conn.search(None, criteria)
                    if not nums[0]:
                        continue
                        
                    for num in nums[0].split():
                        try:
                            _, msg_data = self.conn.fetch(num, '(RFC822)')
                            email_body = msg_data[0][1]
                            msg = email.message_from_bytes(email_body)
                            
                            body = ""
                            if msg.is_multipart():
                                for part in msg.walk():
                                    if part.get_content_type() == "text/plain":
                                        payload = part.get_payload(decode=True)
                                        if payload:
                                            body = payload.decode('utf-8', errors='replace')
                                            break
                            else:
                                payload = msg.get_payload(decode=True)
                                if payload:
                                    body = payload.decode('utf-8', errors='replace')

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
                            print(f"メッセージ処理エラー: {str(e)}")
                except Exception as e:
                    print(f"フォルダー処理エラー ({folder}): {str(e)}")
        except Exception as e:
            print(f"会話取得エラー: {str(e)}")
            self.check_connection()
        
        return sorted(messages, key=lambda x: x['date'])