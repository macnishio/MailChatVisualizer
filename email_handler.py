import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta  # datetime.nowを使用するために修正
from email.utils import parsedate_to_datetime
import re
import traceback
import threading
import time
import logging
from models import db, EmailMessage  # EmailMessageもインポート

# email_handler.py でもロガーを取得
app_logger = logging.getLogger('mailchat')

# Connection pool for IMAP connections
_connection_pool = {}
_pool_lock = threading.Lock()
MAX_CONNECTIONS = 5
CONNECTION_TIMEOUT = 30  # seconds
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

class EmailHandler:
    def __init__(self, email_address=None, password=None, imap_server=None):
        self.email_address = email_address
        self.password = password
        self.imap_server = imap_server
        self.connection = None
        self.connection_key = f"{email_address}:{imap_server}"
        self._lock = threading.Lock()
        self.last_activity = None

    def connect(self):
        """IMAPサーバーに接続（コネクションプール対応）"""
        for retry in range(MAX_RETRIES):
            try:
                with _pool_lock:
                    # プール内の期限切れ接続をクリーンアップ
                    current_time = time.time()
                    for key in list(_connection_pool.keys()):
                        conn_info = _connection_pool[key]
                        if current_time - conn_info['last_activity'] > CONNECTION_TIMEOUT:
                            conn_info['connection'].logout()
                            del _connection_pool[key]

                    # 既存の接続を再利用
                    if self.connection_key in _connection_pool:
                        conn_info = _connection_pool[self.connection_key]
                        try:
                            conn_info['connection'].noop()
                            self.connection = conn_info['connection']
                            conn_info['last_activity'] = time.time()
                            app_logger.debug("Reusing existing connection")
                            return
                        except:
                            del _connection_pool[self.connection_key]

                    # プールが最大数に達している場合、最も古い接続を切断
                    if len(_connection_pool) >= MAX_CONNECTIONS:
                        oldest_key = min(_connection_pool.keys(),
                                       key=lambda k: _connection_pool[k]['last_activity'])
                        _connection_pool[oldest_key]['connection'].logout()
                        del _connection_pool[oldest_key]

                    # 新規接続を作成
                    app_logger.debug(f"Connecting to {self.imap_server}...")
                    self.connection = imaplib.IMAP4_SSL(self.imap_server)
                    self.connection.socket().settimeout(CONNECTION_TIMEOUT)
                    app_logger.debug("IMAP4_SSL connection established")

                    app_logger.debug("Attempting login...")
                    self.connection.login(self.email_address, self.password)
                    app_logger.debug("Login successful")

                    # プールに追加
                    _connection_pool[self.connection_key] = {
                        'connection': self.connection,
                        'last_activity': time.time()
                    }
                    return

            except imaplib.IMAP4.error as e:
                if "Too many simultaneous connections" in str(e) and retry < MAX_RETRIES - 1:
                    app_logger.warning(f"Too many connections, retrying in {RETRY_DELAY} seconds...")
                    time.sleep(RETRY_DELAY)
                    continue
                app_logger.error(f"IMAP connection error: {str(e)}")
                raise
            except Exception as e:
                app_logger.error(f"Connection error: {str(e)}")
                self.disconnect()
                raise

    def test_connection(self):
        """IMAPサーバーへの接続をテストする"""
        app_logger.debug(f"=== Test Connection Started ===")
        app_logger.debug(f"Server: {self.imap_server}")
        app_logger.debug(f"Email: {self.email_address}")

        try:
            app_logger.debug("Attempting IMAP connection...")
            self.connect()  # 修正した connect メソッドを使用
            app_logger.debug("IMAP connection successful")

            app_logger.debug("Listing folders...")
            _, folders = self.connection.list()

            app_logger.debug("Available folders:")
            for folder in folders:
                decoded_folder = self.decode_folder_name(folder)
                app_logger.debug(f"Folder: {decoded_folder}")

            app_logger.debug("=== Test Connection Successful ===")
            return True

        except Exception as e:
            app_logger.error(f"Connection test failed: {str(e)}", exc_info=True)
            return False
        finally:
            app_logger.debug("Closing connection...")
            self.disconnect()
            app_logger.debug("=== Test Connection Completed ===")
    
    def disconnect(self):
        """接続を切断（プール対応）"""
        with _pool_lock:
            try:
                if self.connection_key in _connection_pool:
                    try:
                        _connection_pool[self.connection_key]['connection'].close()
                        _connection_pool[self.connection_key]['connection'].logout()
                    except:
                        pass
                    finally:
                        del _connection_pool[self.connection_key]
                        self.connection = None
                        app_logger.debug("Connection removed from pool and logged out")
            except Exception as e:
                app_logger.error(f"Error during disconnect: {str(e)}")
                self.connection = None

    def encode_folder_name(self, folder):
        """フォルダー名をUTF-7でエンコードする"""
        if isinstance(folder, bytes):
            return folder
        try:
            # IMAPフォルダー名をUTF-7でエンコード
            return folder.encode('utf-7').decode('ascii')
        except Exception as e:
            app_logger.error(f"フォルダー名エンコードエラー: {str(e)}")
            return folder

    def decode_folder_name(self, folder):
        """フォルダー名をデコードする"""
        if isinstance(folder, bytes):
            try:
                folder = folder.decode('ascii', errors='replace')
                # Modified UTF-7からデコードを試みる
                return folder.encode('ascii').decode('utf-7')
            except UnicodeDecodeError as e:
                app_logger.error(f"フォルダー名デコードエラー: {str(e)}。フォルダー名をそのまま使用します: {folder}")
                return folder  # エラーが発生したらそのままフォルダー名を使う
        return folder

    def get_contacts(self, search_query=None, limit=100):
        """メールの連絡先一覧を取得する"""
        contacts = set()
        if not self.connection:
            self.connect()
            if not self.connection:  # Check again after attempting to connect
                app_logger.error("Failed to establish a connection.")
                return []  # Return early if the connection is not established

        try:
            self.connection.select('INBOX', readonly=True)
            app_logger.debug("Selected INBOX folder")

            search_cmd = '(SINCE "1-Dec-2023")'
            app_logger.debug(f"Executing search command: {search_cmd}")
            _, messages = self.connection.search(None, search_cmd)

            if not messages[0]:
                app_logger.debug("No messages found")
                return []

            message_nums = messages[0].split()[-limit:]
            app_logger.debug(f"Processing {len(message_nums)} messages")

            for num in message_nums:
                try:
                    _, msg_data = self.connection.fetch(num, '(BODY[HEADER.FIELDS (FROM)])')
                    if msg_data and msg_data[0]:
                        header_data = msg_data[0][1]
                        if isinstance(header_data, bytes):
                            msg = email.message_from_bytes(header_data)
                            from_addr = self.decode_str(msg['from'])
                            if from_addr:
                                if not search_query or search_query.lower() in from_addr.lower():
                                    contacts.add(from_addr)
                except Exception as e:
                    app_logger.error(f"ヘッダー処理エラー: {str(e)}")
                    continue

            app_logger.debug(f"Found {len(contacts)} unique contacts")
            return sorted(list(contacts))

        except Exception as e:
            app_logger.error(f"連絡先取得エラー: {str(e)}")
            self.disconnect()  # エラー時は接続を解放
            raise
        finally:
            # 接続は保持したままにする（プーリング対応）
            pass

    def check_new_emails(self):
        """新着メールを確認し、パースして返す"""
        new_emails = []
        try:
            self.connect()
            app_logger.debug(f"Connected to {self.imap_server}")

            # INBOXと送信済みフォルダーの両方をチェック
            folders_to_check = ['INBOX']
            sent_folder = None  # 送信済みフォルダーを保存する変数を追加

            # Gmail特有の送信済みフォルダーを取得
            if not self.connection:
                app_logger.debug("Attempting to connect to the server...")
                self.connect()  # Attempt to establish a connection

            if not self.connection:
                app_logger.error("Failed to establish a connection before listing folders.")
                return []  # Return early if the connection is not established

            status, folder_list = self.connection.list()

            if status != "OK":
                app_logger.error(f"Failed to retrieve folder list: {status}")
                return new_emails

            # すべてのフォルダー情報をログに出力
            app_logger.debug("Retrieved folder list:")
            for folder_data in folder_list:
                app_logger.debug(f"Folder data: {folder_data.decode('utf-8')}")

            # Gmailの送信済みフォルダーを検索 (\Sent 属性で判定)
            for folder_data in folder_list:
                folder_info = folder_data.decode('utf-8')
                app_logger.debug(f"Processing folder info: {folder_info}")

                # フォルダー属性が「\Sent」を含む場合、そのフォルダーを送信済みフォルダーと見なす
                if r'\Sent' in folder_info:
                    sent_match = re.search(r'"([^"]+)"$', folder_info)
                    if sent_match:
                        sent_folder = sent_match.group(1)  # 送信済みフォルダー名を取得
                        folders_to_check.append(sent_folder)
                        app_logger.debug(f"Found sent folder: {sent_folder}")
                        break

            if not sent_folder:
                app_logger.warning("No Gmail sent folder found. Please check your folder settings.")

            app_logger.debug(f"Folders to be checked: {folders_to_check}")

            # 各フォルダをチェックして新着メールを取得
            for folder in folders_to_check:
                try:
                    app_logger.debug(f"Processing folder: {folder}")
                    encoded_folder = folder.encode('utf-7').decode('ascii')
                    self.connection.select(encoded_folder, readonly=True)

                    # 過去30日分のメールを取得
                    date_since = (datetime.now() - timedelta(days=365)).strftime("%d-%b-%Y")
                    search_cmd = f'(SINCE "{date_since}")'
                    app_logger.debug(f"Search command: {search_cmd}")

                    _, messages = self.connection.search(None, search_cmd)
                    message_nums = messages[0].split()
                    app_logger.debug(f"Found {len(message_nums)} messages in {folder}")

                    for num in message_nums:
                        try:
                            _, msg_data = self.connection.fetch(num, '(RFC822)')
                            if msg_data and len(msg_data) > 0 and msg_data[0] is not None:
                                email_body = msg_data[0][1]
                                parsed_msg = self.parse_email_message(email_body)

                                if parsed_msg and parsed_msg['message_id']:
                                    with db.session.begin():
                                        # メッセージIDで重複チェック
                                        existing_email = EmailMessage.query.filter_by(
                                            message_id=parsed_msg['message_id']
                                        ).first()

                                        if existing_email:
###                                            app_logger.debug(f"Skipping existing email: {parsed_msg['subject']}")
                                            continue

                                        # メールの送受信フラグを設定
                                        is_sent = folder == sent_folder if sent_folder else False

                                        # 新規メールを保存
                                        new_email = EmailMessage(
                                            message_id=parsed_msg['message_id'],
                                            from_address=parsed_msg['from'],
                                            to_address=parsed_msg['to'],
                                            subject=parsed_msg['subject'],
                                            body=parsed_msg['body'],
                                            date=parsed_msg['date'],
                                            is_sent=is_sent,
                                            folder=folder
                                        )

                                        db.session.add(new_email)
                                        app_logger.debug(f"Added new email: {parsed_msg['subject']} ({folder})")
                                        new_emails.append(parsed_msg)
                            else:
                                app_logger.error(f"Received invalid message data for message number: {num}")                            
                        except Exception as e:
                            app_logger.error(f"Error processing message in {folder}: {str(e)}")
                            continue

                except Exception as e:
                    app_logger.error(f"Error processing folder {folder}: {str(e)}")
                    continue

        except Exception as e:
            app_logger.error(f"Email sync error: {str(e)}")
        finally:
            self.disconnect()

        app_logger.debug(f"Total new emails synchronized: {len(new_emails)}")
        return new_emails

    def get_conversation(self, contact_email, search_query=None):
        """特定の連絡先とのメール会話を取得する"""
        messages = []
        try:
            self.connect()
            if not self.connection:
                raise Exception("IMAP接続に失敗しました。接続がNoneです。")

            sent_folder = self.get_gmail_folders(self.connection)
            folders = ['INBOX']  # フォルダーは str 型で扱う
            if sent_folder:
                folders.append(sent_folder)

            for folder in folders:
                try:
                    # フォルダー名をUTF-7にエンコード（IMAPでの互換性のため）
                    encoded_folder = self.encode_folder_name(folder).decode('utf-8')  # Ensure it's a str
                    status, data = self.connection.select(encoded_folder)                    
                    if status != 'OK':
                        app_logger.error(f"フォルダー選択に失敗しました: {folder}")
                        continue

                    email_part = re.search(r'<(.+?)>', contact_email)
                    if email_part:
                        email = email_part.group(1)
                        search_criteria = f'(OR FROM "{email}" TO "{email}")'

                        if search_query:
                            search_terms = search_query.split()
                            for term in search_terms:
                                search_criteria += f' (OR SUBJECT "{term}" BODY "{term}")'

                        # メールを検索
                        status, nums = self.connection.uid('SEARCH', None, search_criteria)
                        if status != 'OK' or not nums:
                            app_logger.error(f"メールの検索に失敗しました: {search_criteria}")
                            continue

                        if not nums[0]:
                            app_logger.debug(f"No messages found for criteria: {search_criteria}")
                            continue

                        for num in nums[0].split():
                            try:
                                status, msg_data = self.connection.fetch(num, '(RFC822)')
                                if status != 'OK' or not msg_data:
                                    app_logger.error(f"メッセージの取得に失敗しました: UID {num}")
                                    continue

                                email_body = msg_data[0][1]
                                if not email_body:
                                    app_logger.error(f"メッセージのボディがNoneです: UID {num}")
                                    continue

                                parsed_msg = self.parse_email_message(email_body)
                                if parsed_msg:
                                    messages.append(parsed_msg)
                            except Exception as e:
                                app_logger.error(f"メッセージ処理エラー: UID {num} - {str(e)}")
                                continue
                except Exception as e:
                    app_logger.error(f"フォルダー処理エラー ({folder}): {str(e)}")
                    continue

        except Exception as e:
            app_logger.error(f"会話取得エラー: {str(e)}")
        finally:
            self.disconnect()

        return sorted(messages, key=lambda x: x['date'])

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

    def get_gmail_folders(self, connection):
        """Gmailフォルダー名を取得"""
        try:
            _, folders = connection.list(directory='""', pattern='%')
            sent_folder = None
            for folder_data in folders:
                folder_info = folder_data.decode('utf-8')
                if '[Gmail]' in folder_info and ('送信済み' in folder_info or 'Sent' in folder_info):
                    match = re.search(r'"([^"]+)"$', folder_info)
                    if match:
                        sent_folder = match.group(1)
                        break
            return sent_folder
        except Exception as e:
            print(f"Gmailフォルダー取得エラー: {str(e)}")
            return None

    def parse_email_message(self, email_body):
        """メールメッセージをパースしてディクショナリを返す"""
        msg = email.message_from_bytes(email_body)

        try:
            subject = self.decode_str(msg['subject'])
            body = None

            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload is not None:
                            charset = part.get_content_charset() or 'utf-8'
                            try:
                                body = payload.decode(charset)
                            except UnicodeDecodeError:
                                body = payload.decode('utf-8', errors='replace')
                                break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or 'utf-8'
                    try:
                        body = payload.decode(charset)
                    except UnicodeDecodeError:
                        body = payload.decode('utf-8', errors='replace')

            if not subject:
                subject = "(件名なし)"
            if not body:
                body = "(本文なし)"

            return {
                'message_id': msg['message-id'],
                'from': self.decode_str(msg['from']),
                'to': self.decode_str(msg['to']),
                'subject': subject,
                'body': body,
                'date': parsedate_to_datetime(msg['date']),
                'is_sent': self.email_address and self.email_address in self.decode_str(msg['from']) if self.email_address else False
            }

        except Exception as e:
            print(f"メッセージパースエラー: {str(e)}")
            traceback.print_exc()
            return None