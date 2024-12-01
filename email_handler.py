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

    def escape_folder_name(self, folder):
        """フォルダー名をIMAPフォーマットでエスケープする"""
        try:
            # スペースと特殊文字を含むフォルダー名をエスケープ
            if '[' in folder or ']' in folder:
                return folder.encode('utf-7').decode()
            return f'"{folder}"'.encode('utf-7').decode()
        except Exception as e:
            print(f"フォルダー名エスケープエラー: {str(e)}")
            return folder

    def escape_imap_string(self, string):
        """IMAP検索文字列をエスケープする"""
        if not string:
            return '""'
        try:
            # 特殊文字をエスケープ
            escaped = re.sub(r'([\\"\(\)])', r'\\\1', string)
            # UTF-7エンコーディングを使用（日本語対応）
            return f'"{escaped}"'.encode('utf-7').decode()
        except Exception as e:
            print(f"文字列エスケープエラー: {str(e)}")
            return f'"{string}"'

    def select_folder(self, folder):
        """フォルダーを選択する"""
        try:
            folder_name = self.escape_folder_name(folder)
            status, data = self.conn.select(folder_name, readonly=True)
            if status != 'OK':
                print(f"フォルダー選択エラー ({folder}): {data[0].decode()}")
                return False
            return True
        except Exception as e:
            print(f"フォルダー選択エラー ({folder}): {str(e)}")
            return False

    def build_search_criteria(self, contact_email, search_query):
        """検索条件を構築する"""
        criteria = []
        if contact_email:
            escaped_contact = self.escape_imap_string(contact_email)
            criteria.append(f'(OR FROM {escaped_contact} TO {escaped_contact})')
        if search_query:
            escaped_query = self.escape_imap_string(search_query)
            criteria.append(f'(OR SUBJECT {escaped_query} BODY {escaped_query})')
        return ' '.join(criteria) if criteria else 'ALL'

    def get_contacts(self):
        """メールの連絡先一覧を取得する"""
        contacts = set()
        try:
            self.check_connection()
            for folder in ['INBOX', '[Gmail]/送信済みメール', '[Gmail]/Sent Mail']:
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
            for folder in ['INBOX', '[Gmail]/送信済みメール', '[Gmail]/Sent Mail']:
                if not self.select_folder(folder):
                    continue

                try:
                    # 検索条件の構築と実行
                    search_criteria = self.build_search_criteria(contact_email, search_query)
                    _, message_numbers = self.conn.search(None, search_criteria)
                    
                    if not message_numbers[0]:
                        continue

                    for num in message_numbers[0].split():
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
