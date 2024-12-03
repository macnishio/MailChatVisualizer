import imaplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
import re
import time
import logging
import traceback
import socket
from datetime import datetime, timedelta
import threading
from typing import Optional, List, Dict, Set, Any
from queue import Queue, Empty
import functools

# Configure logging
app_logger = logging.getLogger('mailchat')

# Constants
MAX_RETRIES = 5
MAX_FOLDER_RETRY = 3
RETRY_DELAY = 1  # Base delay for exponential backoff
CONNECTION_TIMEOUT = 30
MAX_POOL_SIZE = 3
KEEPALIVE_INTERVAL = 60
RECONNECT_THRESHOLD = 300  # 5 minutes

class ConnectionPool:
    def __init__(self, max_size: int = MAX_POOL_SIZE):
        self.pool: Queue = Queue(maxsize=max_size)
        self.lock = threading.Lock()
        self.last_cleanup = datetime.now()
        
    def get_connection(self, handler) -> Optional[imaplib.IMAP4_SSL]:
        try:
            connection = self.pool.get_nowait()
            if self._verify_connection(connection, handler):
                return connection
        except Empty:
            pass
        
        return None
        
    def put_connection(self, connection: imaplib.IMAP4_SSL):
        try:
            self.pool.put_nowait(connection)
        except:
            self._logout_connection(connection)
            
    def _verify_connection(self, connection: imaplib.IMAP4_SSL, handler) -> bool:
        try:
            status, _ = connection.noop()
            return status == 'OK'
        except:
            self._logout_connection(connection)
            return False
            
    def _logout_connection(self, connection: imaplib.IMAP4_SSL):
        try:
            connection.logout()
        except:
            pass
            
    def cleanup(self):
        with self.lock:
            while not self.pool.empty():
                try:
                    connection = self.pool.get_nowait()
                    self._logout_connection(connection)
                except Empty:
                    break

class EmailHandler:
    _pool = ConnectionPool()
    
    def __init__(self, email_address: str, password: str, imap_server: str):
        self.email_address = email_address
        self.password = password
        self.imap_server = imap_server
        self.connection: Optional[imaplib.IMAP4_SSL] = None
        self.last_activity = datetime.now()
        self.lock = threading.Lock()
        self.current_folder = None # Added to track current folder

    def connect(self) -> bool:
        """IMAPサーバーに接続する（改善版）"""
        with self.lock:
            if self.connection:
                if self.verify_connection_state(['AUTH', 'SELECTED']):
                    self.last_activity = datetime.now()
                    return True
                self.disconnect()
            
            # Check connection pool first
            self.connection = self._pool.get_connection(self)
            if self.connection:
                if self.verify_connection_state(['AUTH', 'SELECTED']):
                    self.last_activity = datetime.now()
                    return True
                self.disconnect()
            
            retry_count = 0
            while retry_count < MAX_RETRIES:
                try:
                    app_logger.debug(f"Connection attempt {retry_count + 1}/{MAX_RETRIES}")
                    
                    # Set socket timeout
                    socket.setdefaulttimeout(CONNECTION_TIMEOUT)
                    
                    # Create new connection
                    self.connection = imaplib.IMAP4_SSL(self.imap_server)
                    self.connection.login(self.email_address, self.password)
                    app_logger.debug("Login successful")
                    
                    # Verify connection state
                    if self.verify_connection_state(['AUTH']):
                        self.last_activity = datetime.now()
                        return True
                        
                except (socket.timeout, socket.gaierror) as e:
                    app_logger.error(f"Connection timeout: {str(e)}")
                except imaplib.IMAP4.error as e:
                    app_logger.error(f"IMAP error: {str(e)}")
                except Exception as e:
                    app_logger.error(f"Connection error: {str(e)}")
                    
                retry_count += 1
                if retry_count < MAX_RETRIES:
                    backoff_time = min(RETRY_DELAY * (2 ** retry_count), 30)
                    app_logger.debug(f"Retrying in {backoff_time} seconds...")
                    time.sleep(backoff_time)
                    self.disconnect()
                    
            app_logger.error("All connection attempts failed")
            return False
            
    def disconnect(self):
        """接続を切断する（改善版）"""
        if self.connection:
            try:
                if self.verify_connection_state(['SELECTED']):
                    try:
                        self.connection.close()
                    except Exception as e:
                        app_logger.debug(f"Error closing folder: {str(e)}")
                        
                try:
                    self.connection.logout()
                except Exception as e:
                    app_logger.debug(f"Error during logout: {str(e)}")
                    
            except Exception as e:
                app_logger.error(f"Disconnect error: {str(e)}")
            finally:
                if self.connection:
                    self._pool.put_connection(self.connection)
                self.connection = None
                
    def verify_connection_state(self, allowed_states: List[str], allow_reconnect: bool = True) -> bool:
        """接続状態を検証する（改善版）"""
        if not self.connection:
            if allow_reconnect:
                return self.connect()
            return False
            
        try:
            status, response = self.connection.noop()
            if status != 'OK':
                app_logger.warning(f"NOOP command failed: {response}")
                if allow_reconnect:
                    self.disconnect()
                    return self.connect()
                return False
                
            # Check connection age
            if (datetime.now() - self.last_activity).total_seconds() > RECONNECT_THRESHOLD:
                app_logger.debug("Connection age exceeded threshold, reconnecting...")
                if allow_reconnect:
                    self.disconnect()
                    return self.connect()
                return False
                
            return True
            
        except (socket.timeout, imaplib.IMAP4.error) as e:
            app_logger.error(f"Connection state verification failed: {str(e)}")
            if allow_reconnect:
                self.disconnect()
                return self.connect()
            return False
            
    def get_contacts(self, search_query=None, limit=100) -> List[str]:
        """メールの連絡先一覧を取得する（改善版）"""
        contacts: Set[str] = set()
        retry_count = 0
        
        while retry_count < MAX_RETRIES:
            try:
                if not self.verify_connection_state(['AUTH', 'SELECTED'], allow_reconnect=True):
                    app_logger.error("Failed to establish connection")
                    retry_count += 1
                    continue
                    
                # Select INBOX
                try:
                    status, _ = self.connection.select('INBOX', readonly=True)
                    if status != 'OK':
                        raise Exception("Failed to select INBOX")
                    app_logger.debug("Selected INBOX folder")
                except Exception as e:
                    app_logger.error(f"Folder selection error: {str(e)}")
                    raise
                    
                # Search for messages
                search_cmd = '(SINCE "1-Dec-2023")'
                status, messages = self.connection.search(None, search_cmd)
                if status != 'OK':
                    raise Exception("Search command failed")
                    
                if not messages[0]:
                    app_logger.debug("No messages found")
                    return []
                    
                # Process messages
                message_nums = messages[0].split()[-limit:]
                app_logger.debug(f"Processing {len(message_nums)} messages")
                
                for num in message_nums:
                    try:
                        if not self.verify_connection_state(['SELECTED']):
                            raise Exception("Connection state invalid")
                            
                        status, msg_data = self.connection.fetch(num, '(BODY[HEADER.FIELDS (FROM)])')
                        if status != 'OK' or not msg_data or not msg_data[0]:
                            continue
                            
                        header_data = msg_data[0][1]
                        if isinstance(header_data, bytes):
                            msg = email.message_from_bytes(header_data)
                            from_addr = self.decode_str(msg['from'])
                            if from_addr and (not search_query or search_query.lower() in from_addr.lower()):
                                contacts.add(from_addr)
                                
                    except (socket.timeout, imaplib.IMAP4.error) as e:
                        app_logger.error(f"Message processing error: {str(e)}")
                        if 'timeout' in str(e).lower():
                            raise  # Re-raise timeout errors for retry
                            
                # Success - return results
                return sorted(list(contacts))
                
            except (socket.timeout, imaplib.IMAP4.error) as e:
                app_logger.error(f"Connection error (attempt {retry_count + 1}): {str(e)}")
                self.disconnect()
                retry_count += 1
                if retry_count < MAX_RETRIES:
                    backoff_time = min(RETRY_DELAY * (2 ** retry_count), 30)
                    app_logger.debug(f"Retrying in {backoff_time} seconds...")
                    time.sleep(backoff_time)
                    
        app_logger.error("Maximum retry attempts reached")
        return sorted(list(contacts))  # Return any contacts found before failure
        
    def decode_str(self, s: Optional[str]) -> str:
        """文字列をデコードする"""
        if not s:
            return ""
        try:
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
        except Exception as e:
            app_logger.error(f"String decoding error: {str(e)}")
            return str(s)
            
    def select_folder(self, folder_name: str) -> bool:
        """フォルダを選択し、接続状態を確認する（最適化版）"""
        MAX_RETRIES = 3
        retry_count = 0
        last_error = None
        
        while retry_count < MAX_RETRIES:
            try:
                # フォルダ名の正規化
                if isinstance(folder_name, bytes):
                    folder_name = folder_name.decode('utf-8')
                
                # 接続状態の厳密な検証
                if not self.verify_connection_state(['AUTH'], allow_reconnect=True):
                    raise Exception("Failed to establish AUTH state")
                
                # 現在のフォルダーの状態確認
                if self.current_folder == folder_name:
                    try:
                        if self.verify_connection_state(['SELECTED'], allow_reconnect=False):
                            status, _ = self.connection.noop()
                            if status == 'OK':
                                app_logger.debug(f"Verified existing folder selection: {folder_name}")
                                return True
                    except Exception as e:
                        app_logger.warning(f"Current folder state invalid: {str(e)}")
                
                # SELECTED状態からの遷移処理
                if self.connection.state == 'SELECTED':
                    try:
                        self.connection.close()
                        app_logger.debug("Successfully closed previous folder selection")
                    except Exception as e:
                        app_logger.warning(f"Error during folder close: {str(e)}")
                        # CLOSEの失敗は致命的ではないため続行
                
                # 新しいフォルダの選択
                try:
                    # フォルダ名のエンコーディング
                    encoded_folder = folder_name.encode('utf-7').decode('ascii')
                    app_logger.debug(f"Attempting to select folder: {encoded_folder}")
                    
                    # readonly=Trueで選択（安全な操作のため）
                    status, response = self.connection.select(encoded_folder, readonly=True)
                    
                    if status != 'OK':
                        raise Exception(f"Folder selection failed: {response}")
                    
                    # 選択結果の検証
                    if not isinstance(response, list) or not response:
                        raise Exception("Invalid response from SELECT command")
                    
                    # メッセージ数の確認
                    try:
                        message_count = int(response[0])
                        app_logger.debug(f"Selected folder contains {message_count} messages")
                    except (ValueError, TypeError) as e:
                        app_logger.warning(f"Could not parse message count: {str(e)}")
                    
                    # 状態の最終確認
                    if self.connection.state != 'SELECTED':
                        raise Exception("Folder selection did not result in SELECTED state")
                    
                    self.current_folder = folder_name
                    app_logger.debug(f"Successfully selected folder: {folder_name}")
                    return True
                    
                except Exception as e:
                    last_error = str(e)
                    raise Exception(f"Folder selection operation failed: {str(e)}")
                
            except Exception as e:
                app_logger.error(f"Error selecting folder {folder_name} (attempt {retry_count + 1}/{MAX_RETRIES}): {str(e)}")
                retry_count += 1
                
                if retry_count < MAX_RETRIES:
                    wait_time = min(5, 1 * (2 ** retry_count))  # 指数バックオフ（最大5秒）
                    app_logger.debug(f"Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                    
                    # 再接続を試みる
                    self.disconnect()
                    if not self.connect():
                        app_logger.error("Failed to reconnect during retry")
                        continue
                else:
                    app_logger.error(f"All attempts to select folder failed. Last error: {last_error}")
                    self.disconnect()
                    return False
        
        return False

    def get_gmail_folders(self):
        """Gmailフォルダー一覧を取得する（改善版）"""
        try:
            # LIST操作前の状態検証
            if not self.verify_connection_state(['AUTH', 'SELECTED'], allow_reconnect=True):
                raise Exception("Invalid connection state for LIST operation")
            
            app_logger.debug("Retrieving Gmail folders...")
            retry_count = 0
            
            while retry_count < MAX_FOLDER_RETRY:
                try:
                    # LIST操作の実行
                    status, folder_list = self.connection.list()
                    
                    # レスポンス形式の厳密な検証
                    if status != 'OK':
                        raise Exception(f"LIST command failed with status: {status}")
                        
                    if not isinstance(folder_list, list):
                        raise Exception("Invalid response format from LIST command")
                        
                    # フォルダー情報の解析と検証
                    sent_folder = None
                    for folder_data in folder_list:
                        if isinstance(folder_data, bytes):
                            folder_str = folder_data.decode('utf-8')
                            # Gmailの送信済みフォルダーを識別
                            if '[Gmail]/送信済みメール' in folder_str or '[Gmail]/Sent Mail' in folder_str:
                                sent_folder = folder_str.split('"/"')[-1].strip('"').strip()
                                app_logger.debug(f"Found sent folder: {sent_folder}")
                                break
                    
                    return sent_folder
                    
                except Exception as e:
                    retry_count += 1
                    error_msg = str(e)
                    app_logger.error(f"Folder operation error (attempt {retry_count}): {error_msg}")
                    
                    # エラータイプに基づく適切なリカバリー処理
                    if 'timed out' in error_msg.lower():
                        # タイムアウトの場合は接続を再確立
                        self.disconnect()
                        if not self.connect():
                            raise Exception("Failed to reconnect after timeout")
                        time.sleep(min(RETRY_DELAY * (2 ** retry_count), 15))
                    elif 'BYE' in error_msg or 'LOGOUT' in error_msg:
                        # 接続が切断された場合
                        if not self.verify_connection_state(['AUTH'], allow_reconnect=True):
                            raise Exception("Failed to recover connection state")
                        time.sleep(1)
                    else:
                        # その他のエラーは通常のバックオフ
                        time.sleep(min(RETRY_DELAY * (2 ** retry_count), 30))
                    
                    if retry_count == MAX_FOLDER_RETRY:
                        raise Exception("Maximum retry attempts reached for folder operation")
                        
        except Exception as e:
            app_logger.error(f"Failed to retrieve Gmail folders: {str(e)}")
            raise
            
        return None

    def check_new_emails(self):
        """新着メールを確認し、パースして返す（最適化版）"""
        new_emails = []
        last_reconnect = time.time()
        connection_error_count = 0
        batch_delay = 0.5  # バッチ間の待機時間（秒）
        
        try:
            if not self.connect():
                raise Exception("Failed to establish initial connection")

            # INBOXと送信済みフォルダーの両方をチェック
            folders_to_check = ['INBOX']
            sent_folder = self.get_gmail_folders()
            if sent_folder:
                folders_to_check.append(sent_folder)
                app_logger.debug(f"Added sent folder to check: {sent_folder}")

            for folder in folders_to_check:
                try:
                    if not self.select_folder(folder):
                        app_logger.error(f"Failed to select folder: {folder}")
                        continue

                    # 過去30日分のメールを取得
                    date_since = (datetime.now() - timedelta(days=30)).strftime("%d-%b-%Y")
                    search_cmd = f'(SINCE "{date_since}")'
                    status, messages = self.connection.search(None, search_cmd)

                    if status != 'OK':
                        app_logger.error(f"Search failed in folder {folder}")
                        continue

                    message_nums = messages[0].split()
                    total_messages = len(message_nums)
                    app_logger.debug(f"Found {total_messages} messages in {folder}")

                    # バッチ処理でメッセージを取得
                    for i in range(0, total_messages, BATCH_SIZE):
                        batch = message_nums[i:i + BATCH_SIZE]
                        
                        for num in batch:
                            try:
                                # 接続状態を確認し、必要に応じて再接続
                                current_time = time.time()
                                if current_time - last_reconnect > RECONNECT_INTERVAL:
                                    app_logger.debug("Refreshing connection...")
                                    self.disconnect()
                                    if not self.connect() or not self.select_folder(folder):
                                        raise Exception("Failed to refresh connection")
                                    last_reconnect = current_time

                                # FETCH操作前の状態検証
                                if not self.verify_connection_state(['SELECTED'], force_folder=folder):
                                    app_logger.error(f"Connection not in SELECTED state before FETCH operation")
                                    raise Exception("Invalid connection state for FETCH")

                                try:
                                    # メッセージを取得
                                    status, msg_data = self.connection.fetch(num, '(RFC822)')
                                    if status != 'OK' or not msg_data or not msg_data[0]:
                                        app_logger.warning(f"FETCH command failed or returned invalid data for message {num}")
                                        continue

                                    # レスポンスの解析と状態チェック
                                    response_str = str(msg_data[0])
                                    if 'BYE' in response_str or 'LOGOUT' in response_str:
                                        app_logger.error("Connection received BYE/LOGOUT during FETCH")
                                        raise Exception("Connection state changed during FETCH")

                                    email_body = msg_data[0][1]
                                    parsed_msg = self.parse_email_message(email_body)

                                    # FETCH操作後の状態検証
                                    if not self.verify_connection_state(['SELECTED'], allow_reconnect=False):
                                        raise Exception("Connection state changed after FETCH")

                                except Exception as e:
                                    app_logger.error(f"Error during FETCH operation: {str(e)}")
                                    # 接続状態の回復を試みる
                                    if not self.verify_connection_state(['SELECTED'], force_folder=folder):
                                        raise Exception("Failed to recover connection state")
                                    continue
                                
                                if parsed_msg and parsed_msg['message_id']:
                                    parsed_msg['folder'] = folder
                                    parsed_msg['is_sent'] = folder == sent_folder
                                    new_emails.append(parsed_msg)

                            except Exception as e:
                                app_logger.error(f"Error processing message {num}: {str(e)}")
                                connection_error_count += 1
                                if connection_error_count > 5:
                                    raise Exception("Too many connection errors")
                                continue

                        # バッチ処理後の短い待機
                        time.sleep(0.1)

                except Exception as e:
                    app_logger.error(f"Error processing folder {folder}: {str(e)}")
                    continue

        except Exception as e:
            app_logger.error(f"Email sync error: {str(e)}")
        finally:
            self.disconnect()

        app_logger.debug(f"Total new emails synchronized: {len(new_emails)}")
        return new_emails


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

    def __del__(self):
        self.disconnect()

    def get_conversation(self, contact_email, search_query=None):
        """特定の連絡先とのメール会話を取得する"""
        messages = []
        try:
            self.connect()
            if not self.connection:
                raise Exception("IMAP接続に失敗しました。接続がNoneです。")

            sent_folder = self.get_gmail_folders()
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

    def parse_email_message(self, email_body):
        """メールメッセージをパースしてディクショナリを返す"""
        from models import Contact, db

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

            # メールアドレスを抽出
            from_str = self.decode_str(msg['from'])
            to_str = self.decode_str(msg['to'])
            
            def extract_email(header_str):
                if not header_str:
                    return None, None
                match = re.search(r'<(.+?)>', header_str)
                if match:
                    email = match.group(1)
                    display_name = header_str[:match.start()].strip(' "\'')
                    return email, display_name or email
                return header_str, header_str

            from_email, from_display = extract_email(from_str)
            to_email, to_display = extract_email(to_str)

            # ContactとEmailMessageの関連付け
            from_contact = None
            to_contact = None

            if from_email:
                from_contact = Contact.find_or_create(from_email, display_name=from_display)

            if to_email:
                to_contact = Contact.find_or_create(to_email, display_name=to_display)

            if from_contact or to_contact:
                db.session.commit()

            return {
                'message_id': msg['message-id'],
                'from': from_str,
                'to': to_str,
                'from_contact_id': from_contact.id if from_contact else None,
                'to_contact_id': to_contact.id if to_contact else None,
                'subject': subject,
                'body': body,
                'date': parsedate_to_datetime(msg['date']),
                'is_sent': self.email_address and self.email_address in from_str if self.email_address else False
            }

        except Exception as e:
            app_logger.error(f"メッセージパースエラー: {str(e)}")
            traceback.print_exc()
            return None