import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
import re
import traceback
import threading
import time
import logging
from models import db, EmailMessage

# email_handler.py でもロガーを取得
app_logger = logging.getLogger('mailchat')

# Connection pool for IMAP connections
_connection_pool = {}
_pool_lock = threading.Lock()
# 接続設定の最適化
MAX_CONNECTIONS = 3  # 同時接続数を増加して並列処理を改善
CONNECTION_TIMEOUT = 30  # タイムアウトを増加して安定性を向上
MAX_RETRIES = 5  # リトライ回数を維持
RETRY_DELAY = 3  # 初期リトライ間隔
BATCH_SIZE = 10  # バッチサイズを適度に増加
RECONNECT_INTERVAL = 180  # 再接続間隔を3分に延長
KEEPALIVE_INTERVAL = 45  # キープアライブ間隔を延長
THROTTLE_BACKOFF = 60  # スロットリング時の待機時間（秒）
MAX_FOLDER_RETRY = 3  # フォルダー操作の最大リトライ回数

# スレッドプール管理の設定
import threading
from concurrent.futures import ThreadPoolExecutor
_thread_pool = ThreadPoolExecutor(max_workers=MAX_CONNECTIONS)
_connection_semaphore = threading.Semaphore(MAX_CONNECTIONS)

class EmailHandler:
    def __init__(self, email_address=None, password=None, imap_server=None):
        self.email_address = email_address
        self.password = password
        self.imap_server = imap_server
        self.connection = None
        self.connection_key = f"{email_address}:{imap_server}"
        self._lock = threading.Lock()
        self.last_activity = None
        self.current_folder = None

    def verify_connection_state(self, expected_states=None, allow_reconnect=True, force_folder=None):
        """接続状態を検証し、必要に応じて状態遷移を処理する（改善版）"""
        if not self.connection:
            if not allow_reconnect:
                return False
            try:
                if not self.connect():
                    return False
            except Exception as e:
                app_logger.error(f"Reconnection failed: {str(e)}")
                return False
            
        try:
            current_state = self.connection.state
            app_logger.debug(f"Current connection state: {current_state}")
            
            # LOGOUT状態のチェックと処理
            if current_state == 'LOGOUT':
                app_logger.warning("Connection in LOGOUT state, attempting recovery")
                if not allow_reconnect:
                    return False
                self.disconnect()
                if not self.connect():
                    return False
                current_state = self.connection.state
            
            if expected_states:
                if current_state not in expected_states:
                    app_logger.warning(f"Invalid state: {current_state}, expected: {expected_states}")
                    
                    # 状態遷移の体系的な処理
                    try:
                        # NONAUTH → AUTH
                        if current_state == 'NONAUTH' and 'AUTH' in expected_states:
                            status, _ = self.connection.login(self.email_address, self.password)
                            if status != 'OK':
                                raise Exception("Authentication failed")
                            current_state = 'AUTH'
                            app_logger.debug("Successfully transitioned to AUTH state")
                        
                        # AUTH → SELECTED
                        if current_state == 'AUTH' and 'SELECTED' in expected_states:
                            folder_to_select = force_folder or self.current_folder
                            if folder_to_select:
                                if not self.select_folder(folder_to_select):
                                    raise Exception("Failed to transition to SELECTED state")
                                current_state = 'SELECTED'
                                app_logger.debug(f"Successfully transitioned to SELECTED state with folder: {folder_to_select}")
                            else:
                                app_logger.warning("No folder specified for SELECTED state transition")
                                return False
                        
                        # 状態遷移後の検証
                        if current_state not in expected_states:
                            raise Exception(f"Failed to achieve expected state: {expected_states}")
                            
                    except Exception as e:
                        app_logger.error(f"State transition failed: {str(e)}")
                        if allow_reconnect:
                            app_logger.debug("Attempting reconnection after failed state transition")
                            self.disconnect()
                            return self.verify_connection_state(expected_states, False, force_folder)
                        return False
            
            # 接続の生存確認と状態保持の検証
            try:
                status, response = self.connection.noop()
                if status != 'OK':
                    raise Exception("NOOP check failed")
                    
                # レスポンスの詳細な解析
                if isinstance(response, list) and response:
                    response_str = str(response[0])
                    if 'BYE' in response_str or 'LOGOUT' in response_str:
                        raise Exception("Connection received BYE/LOGOUT response")
                
                return True
                
            except Exception as e:
                app_logger.error(f"Connection check failed: {str(e)}")
                if allow_reconnect:
                    app_logger.debug("Attempting reconnection after failed connection check")
                    self.disconnect()
                    return self.verify_connection_state(expected_states, False, force_folder)
                return False
                
        except Exception as e:
            app_logger.error(f"Connection state verification failed: {str(e)}")
            if allow_reconnect:
                return self.verify_connection_state(expected_states, False, force_folder)
            return False

    def connect(self):
        """IMAPサーバーに接続（スロットリング対応・最適化版）"""
        last_error = None
        for retry in range(MAX_RETRIES):
            try:
                if not _connection_semaphore.acquire(timeout=CONNECTION_TIMEOUT):
                    raise Exception("接続制限に達しました")
                    
                try:
                    with _pool_lock:
                        # キープアライブタイマーの初期化
                        self.last_keepalive = time.time()
                        # プール内の期限切れ接続をクリーンアップ
                        current_time = time.time()
                        for key in list(_connection_pool.keys()):
                            conn_info = _connection_pool[key]
                            if current_time - conn_info['last_activity'] > CONNECTION_TIMEOUT or \
                               current_time - conn_info['created_at'] > RECONNECT_INTERVAL or \
                               conn_info.get('throttled', False):
                                try:
                                    conn_info['connection'].logout()
                                except Exception as e:
                                    app_logger.warning(f"Logout failed for expired connection {key}: {str(e)}")
                                del _connection_pool[key]
                                app_logger.debug(f"Removed expired/throttled connection: {key}")

                        # スロットリング状態のチェック
                        if hasattr(self, 'throttled_until') and time.time() < self.throttled_until:
                            wait_time = self.throttled_until - time.time()
                            app_logger.debug(f"Waiting for throttling timeout: {wait_time:.1f} seconds")
                            time.sleep(min(wait_time, THROTTLE_BACKOFF))
                            continue

                        # 既存の接続を再利用
                        if self.connection_key in _connection_pool:
                            conn_info = _connection_pool[self.connection_key]
                            try:
                                # 厳密な接続状態チェック
                                status, response = conn_info['connection'].noop()
                                
                                # スロットリング検出
                                if isinstance(response, list) and response and b'THROTTLED' in response[0]:
                                    app_logger.warning("Connection throttled, backing off...")
                                    conn_info['throttled'] = True
                                    self.throttled_until = time.time() + THROTTLE_BACKOFF
                                    raise Exception("Connection throttled")
                                
                                if status != 'OK':
                                    raise Exception(f"NOOP failed with status: {status}")
                                
                                # 接続状態の検証と適切な遷移
                                try:
                                    state = conn_info['connection'].state
                                    app_logger.debug(f"Connection state: {state}")
                                    
                                    # 状態に応じた適切な処理
                                    if state == 'NONAUTH':
                                        # NONAUTHの場合は新規認証
                                        status, _ = conn_info['connection'].login(self.email_address, self.password)
                                        if status != 'OK':
                                            raise Exception("Authentication failed")
                                        app_logger.debug("Authenticated successfully")
                                    elif state == 'AUTH':
                                        # AUTH状態では再認証は行わない
                                        app_logger.debug("Connection already authenticated")
                                    elif state == 'SELECTED':
                                        # SELECTED状態の場合はリセット
                                        try:
                                            conn_info['connection'].close()
                                            app_logger.debug("Reset folder selection")
                                        except Exception as e:
                                            app_logger.warning(f"Error closing folder: {str(e)}")
                                    else:
                                        raise Exception(f"Unknown connection state: {state}")
                                    
                                    # 接続状態の最終確認
                                    if not self.verify_connection_state(['AUTH'], allow_reconnect=False):
                                        raise Exception("Failed to establish proper connection state")
                                except Exception as e:
                                    app_logger.warning(f"State verification failed: {str(e)}")
                                    raise
                                
                                self.connection = conn_info['connection']
                                conn_info['last_activity'] = time.time()
                                app_logger.debug("Reusing existing connection")
                                return True
                            except Exception as e:
                                app_logger.warning(f"Connection reuse failed: {str(e)}")
                                try:
                                    conn_info['connection'].logout()
                                except:
                                    pass
                                del _connection_pool[self.connection_key]

                        # プールが最大数に達している場合、最も古い接続を切断
                        if len(_connection_pool) >= MAX_CONNECTIONS:
                            oldest_key = min(_connection_pool.keys(),
                                           key=lambda k: _connection_pool[k]['last_activity'])
                            try:
                                _connection_pool[oldest_key]['connection'].logout()
                            except:
                                pass
                            del _connection_pool[oldest_key]
                            app_logger.debug("Removed oldest connection from pool")

                        # 新規接続を作成（タイムアウト処理改善）
                        app_logger.debug(f"Creating new connection to {self.imap_server}...")
                        self.connection = imaplib.IMAP4_SSL(self.imap_server)
                        self.connection.socket().settimeout(CONNECTION_TIMEOUT)

                        # ログイン試行
                        app_logger.debug("Attempting login...")
                        status, _ = self.connection.login(self.email_address, self.password)
                        if status != 'OK':
                            raise Exception("Login failed")
                        app_logger.debug("Login successful")

                        # プールに追加
                        _connection_pool[self.connection_key] = {
                            'connection': self.connection,
                            'last_activity': time.time(),
                            'created_at': time.time()
                        }
                        return True
                finally:
                    _connection_semaphore.release()

            except Exception as e:
                app_logger.error(f"Connection attempt {retry + 1} failed: {str(e)}")
                if retry < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (2 ** retry))  # 指数バックオフ
                else:
                    raise

        return False

    def select_folder(self, folder_name):
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

    def keep_alive(self):
        """接続をキープアライブするためのNOOPコマンドを実行"""
        try:
            if self.connection and time.time() - self.last_keepalive > KEEPALIVE_INTERVAL:
                status, _ = self.connection.noop()
                if status != 'OK':
                    raise Exception("NOOP failed")
                self.last_keepalive = time.time()
                app_logger.debug("Keep-alive NOOP successful")
                return True
            return True
        except Exception as e:
            app_logger.error(f"Keep-alive failed: {str(e)}")
            return False

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

    def test_connection(self):
        """IMAPサーバーへの接続をテストする（改善版）"""
        app_logger.debug(f"=== Test Connection Started ===")
        app_logger.debug(f"Server: {self.imap_server}")
        app_logger.debug(f"Email: {self.email_address}")

        try:
            for retry in range(MAX_RETRIES):
                try:
                    app_logger.debug(f"Connection attempt {retry + 1}/{MAX_RETRIES}")
                    if not self.connect():
                        raise Exception("Failed to establish connection")
                        
                    # 接続状態の検証
                    if not self.verify_connection_state(['AUTH']):
                        raise Exception("Connection not in AUTH state after connect")
                    app_logger.debug("IMAP connection successful")

                    # フォルダーリスト取得時のタイムアウト処理
                    def list_folders():
                        import socket
                        socket.setdefaulttimeout(CONNECTION_TIMEOUT)
                        return self.connection.list()

                    app_logger.debug("Listing folders...")
                    _, folders = list_folders()

                    if not folders:
                        raise Exception("No folders returned")

                    app_logger.debug("Available folders:")
                    for folder in folders:
                        decoded_folder = self.decode_folder_name(folder)
                        app_logger.debug(f"Folder: {decoded_folder}")

                    # 接続状態の最終確認
                    status, _ = self.connection.noop()
                    if status != 'OK':
                        raise Exception("Final NOOP check failed")

                    app_logger.debug("=== Test Connection Successful ===")
                    return True

                except Exception as e:
                    app_logger.error(f"Connection attempt {retry + 1} failed: {str(e)}")
                    if retry < MAX_RETRIES - 1:
                        backoff_time = RETRY_DELAY * (2 ** retry)  # 指数バックオフ
                        app_logger.debug(f"Retrying in {backoff_time} seconds...")
                        time.sleep(backoff_time)
                        self.disconnect()  # 再試行前に接続をクリーンアップ
                    else:
                        app_logger.error("All connection attempts failed")
                        return False
        finally:
            app_logger.debug("Closing connection...")
            self.disconnect()
            app_logger.debug("=== Test Connection Completed ===")

    
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

    def get_gmail_folders(self, connection=None):
        """Gmailフォルダー名を取得"""
        try:
            if not connection:
                if not self.connect():
                    return None
                connection = self.connection

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
            app_logger.error(f"Gmailフォルダー取得エラー: {str(e)}")
            return None

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