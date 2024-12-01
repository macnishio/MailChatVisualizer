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

    def parse_email_message(self, email_body):
        """メールメッセージをパースしてディクショナリを返す"""
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

        return {
            'message_id': msg['message-id'],
            'from': self.decode_str(msg['from']),
            'to': self.decode_str(msg['to']),
            'subject': self.decode_str(msg['subject']),
            'body': body,
            'date': parsedate_to_datetime(msg['date']),
            'is_sent': self.email_address in self.decode_str(msg['from'])
        }

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

    def encode_folder_name(self, folder):
        """フォルダー名をUTF-7でエンコードする"""
        if isinstance(folder, bytes):
            return folder
        try:
            return folder.encode('utf-7')
        except Exception as e:
            print(f"フォルダー名エンコードエラー: {str(e)}")
            return folder

    def select_folder(self, folder):
        """フォルダーを選択する"""
        try:
            if isinstance(folder, bytes):
                encoded_folder = folder
            else:
                encoded_folder = self.encode_folder_name(folder)
            
            status, data = self.conn.select(encoded_folder, readonly=True)
            if status != 'OK':
                raise Exception(f"選択エラー: {data[0].decode('utf-8', errors='replace')}")
            return True
        except Exception as e:
            print(f"フォルダー選択エラー ({folder}): {str(e)}")
            return False

    def get_gmail_folders(self):
        """Gmailフォルダー名を取得"""
        try:
            # LISTコマンドの構文を修正
            _, folders = self.conn.list(directory='""', pattern='%')
            sent_folder = None
            for folder_data in folders:
                # フォルダー情報をデコード
                folder_info = folder_data.decode('utf-8')
                if '[Gmail]' in folder_info and ('送信済み' in folder_info or 'Sent' in folder_info):
                    # フォルダーパスを抽出
                    match = re.search(r'"([^"]+)"$', folder_info)
                    if match:
                        sent_folder = match.group(1)
                        break
            return sent_folder
        except Exception as e:
            print(f"Gmailフォルダー取得エラー: {str(e)}")
            return None

    def build_search_criteria(self, contact_email, search_query):
        """検索条件を構築する"""
        criteria = []
        if contact_email:
            email_part = re.search(r'<(.+?)>', contact_email)
            if email_part:
                email = email_part.group(1)
                criteria.append(f'(OR FROM "{email}" TO "{email}")')
        if search_query:
            try:
                # 検索クエリをUTF-8で処理
                search_terms = search_query.split()
                for term in search_terms:
                    criteria.append(f'(OR (OR SUBJECT "{term}" BODY "{term}") TEXT "{term}")')
            except Exception as e:
                print(f"検索クエリエラー: {str(e)}")
        return ' '.join(criteria) if criteria else 'ALL'

    def get_contacts(self, search_query=None, batch_size=1000):
        """メールの連絡先一覧を取得する"""
        contacts = set()
        try:
            self.check_connection()
            sent_folder = self.get_gmail_folders()
            folders = [b'INBOX']
            if sent_folder:
                folders.append(sent_folder)
            
            for folder in folders:
                if not self.select_folder(folder):
                    continue

                try:
                    _, messages = self.conn.search(None, 'ALL')
                    message_nums = messages[0].split()
                    total_messages = len(message_nums)
                    
                    # バッチ処理でメッセージを取得
                    for i in range(0, total_messages, batch_size):
                        batch = message_nums[i:i + batch_size]
                        for num in batch:
                            try:
                                _, msg_data = self.conn.fetch(num, '(RFC822)')
                                email_body = msg_data[0][1]
                                msg = email.message_from_bytes(email_body)
                                
                                if folder == b'INBOX':
                                    from_addr = self.decode_str(msg['from'])
                                    if from_addr and (not search_query or search_query.lower() in from_addr.lower()):
                                        contacts.add(from_addr)
                                else:
                                    to_addr = self.decode_str(msg['to'])
                                    if to_addr and (not search_query or search_query.lower() in to_addr.lower()):
                                        contacts.add(to_addr)
                            except Exception as e:
                                print(f"メッセージ処理エラー: {str(e)}")
                                continue
                                
                except Exception as e:
                    print(f"フォルダー処理エラー ({folder}): {str(e)}")
                    continue
                    
        except Exception as e:
            print(f"連絡先取得エラー: {str(e)}")
            self.check_connection()

        return sorted(list(contacts))

    def get_conversation(self, contact_email, search_query=None):
        """特定の連絡先とのメール会話を取得する"""
        messages = []
        try:
            self.check_connection()
            sent_folder = self.get_gmail_folders()
            folders = [b'INBOX']
            if sent_folder:
                folders.append(sent_folder)
            
            for folder in folders:
                if not self.select_folder(folder):
                    continue
                
                try:
                    # メールボックスを確実に選択
                    self.conn.select(folder)
                    
                    # 検索条件を構築
                    criteria = self.build_search_criteria(contact_email, search_query)
                    search_cmd = f'CHARSET UTF-8 {criteria}' if criteria != 'ALL' else 'ALL'
                    
                    # 検索を実行
                    try:
                        if search_query:
                            # UTF-8でエンコードして検索
                            search_query_encoded = search_query.encode('utf-8')
                            _, nums = self.conn.uid('SEARCH', 'CHARSET', 'UTF-8',
                                                  'OR', 'SUBJECT', search_query_encoded,
                                                  'OR', 'BODY', search_query_encoded)
                        else:
                            # 特定の連絡先のメールのみを検索
                            email_part = re.search(r'<(.+?)>', contact_email)
                            if email_part:
                                email = email_part.group(1)
                                _, nums = self.conn.uid('SEARCH', 'OR',
                                                      f'FROM "{email}"',
                                                      f'TO "{email}"')
                            else:
                                _, nums = self.conn.uid('SEARCH', 'ALL')
                    except imaplib.IMAP4.error as e:
                        print(f"検索エラー: {str(e)}")
                        # フォールバック: 基本的な検索を試みる
                        _, nums = self.conn.uid('SEARCH', 'ALL')
                    
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
