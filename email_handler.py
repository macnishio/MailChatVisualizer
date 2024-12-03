import os
import imaplib
import email
from email.header import decode_header
import socket
import time
import threading
import traceback
from datetime import datetime, timedelta
from typing import List, Set, Optional
import logging
import re
from email.utils import parsedate_to_datetime
from queue import Queue, Empty
import functools

# Configure logging
app_logger = logging.getLogger('mailchat')

# Constants
MAX_RETRIES = 5
RETRY_DELAY = 1
CONNECTION_TIMEOUT = 30
RECONNECT_INTERVAL = 300  # 5 minutes
RECONNECT_THRESHOLD = 600  # 10 minutes
BATCH_SIZE = 50
MAX_FOLDER_RETRY = 3

class ConnectionPool:
    def __init__(self, max_connections=5):
        self.pool = []
        self.max_connections = max_connections
        self.lock = threading.Lock()

    def get_connection(self, handler) -> Optional[imaplib.IMAP4_SSL]:
        with self.lock:
            if not self.pool:
                return None
            
            # Find a valid connection
            for i, conn in enumerate(self.pool):
                try:
                    status, _ = conn.noop()
                    if status == 'OK':
                        self.pool.pop(i)
                        return conn
                except:
                    continue
                    
            # Clean up invalid connections
            self.pool = []
            return None

    def put_connection(self, connection):
        with self.lock:
            try:
                if connection and len(self.pool) < self.max_connections:
                    status, _ = connection.noop()
                    if status == 'OK':
                        self.pool.append(connection)
                        return
            except:
                pass
                
            # If connection is invalid or pool is full, close it
            try:
                if connection:
                    connection.logout()
            except:
                pass

class EmailHandler:
    _pool = ConnectionPool()

    def __init__(self, email_address, password, imap_server):
        self.email_address = email_address
        self.password = password
        self.imap_server = imap_server
        self.connection = None
        self.lock = threading.Lock()
        self.last_activity = datetime.now()
        self.current_folder = None # Added to track current folder

    def connect(self) -> bool:
        """IMAPサーバーに接続する（タイムアウト対応改善版）"""
        with self.lock:
            if self.connection:
                try:
                    if self.verify_connection_state(['AUTH', 'SELECTED'], allow_reconnect=False):
                        self.last_activity = datetime.now()
                        return True
                except Exception as e:
                    app_logger.warning(f"Connection reuse failed: {str(e)}")
                self.disconnect()
            
            # Check connection pool first
            app_logger.debug(f"Creating new connection to {self.imap_server}...")
            self.connection = self._pool.get_connection(self)
            if self.connection:
                try:
                    if self.verify_connection_state(['AUTH', 'SELECTED'], allow_reconnect=False):
                        self.last_activity = datetime.now()
                        return True
                except Exception as e:
                    app_logger.warning(f"Connection reuse failed: {str(e)}")
                self.disconnect()
            
            retry_count = 0
            while retry_count < MAX_RETRIES:
                try:
                    app_logger.debug(f"Connection attempt {retry_count + 1}/{MAX_RETRIES}")
                    
                    # Set socket timeout for initial connection
                    original_timeout = socket.getdefaulttimeout()
                    socket.setdefaulttimeout(CONNECTION_TIMEOUT)
                    
                    try:
                        # Create new connection with SSL/TLS
                        self.connection = imaplib.IMAP4_SSL(self.imap_server)
                        
                        # Set read timeout for operations
                        self.connection.socket().settimeout(CONNECTION_TIMEOUT)
                        
                        app_logger.debug("Attempting login...")
                        self.connection.login(self.email_address, self.password)
                        app_logger.debug("Login successful")
                        
                        # Verify initial state
                        app_logger.debug("Current connection state: AUTH")
                        if not self.verify_connection_state(['AUTH'], allow_reconnect=False):
                            raise Exception("Connection not in AUTH state after connect")
                            
                        self.last_activity = datetime.now()
                        return True
                        
                    finally:
                        socket.setdefaulttimeout(original_timeout)
                        
                except (socket.timeout, socket.gaierror) as e:
                    app_logger.error(f"Connection timeout/network error: {str(e)}")
                    error_type = "timeout" if isinstance(e, socket.timeout) else "network"
                except imaplib.IMAP4.error as e:
                    app_logger.error(f"IMAP protocol error: {str(e)}")
                    error_type = "protocol"
                except Exception as e:
                    app_logger.error(f"Connection error: {str(e)}")
                    error_type = "general"
                    
                retry_count += 1
                if retry_count < MAX_RETRIES:
                    # Adjust backoff based on error type
                    if error_type == "timeout":
                        backoff_time = min(RETRY_DELAY * (2 ** retry_count), 30)
                    elif error_type == "network":
                        backoff_time = min(RETRY_DELAY * (2 ** retry_count), 60)
                    else:
                        backoff_time = min(RETRY_DELAY * (1.5 ** retry_count), 15)
                        
                    app_logger.debug(f"Retrying in {backoff_time} seconds...")
                    time.sleep(backoff_time)
                    self.disconnect()
                    
            app_logger.error(f"Connection attempt {retry_count} failed: Connection not in AUTH state after connect")
            return False

    def test_connection(self) -> bool:
        """接続テスト機能を実装する（改善版）"""
        app_logger.debug(f"DEBUG: 接続テスト開始 - Server: {self.imap_server}, Email: {self.email_address}")
        try:
            # 既存の接続を確認
            if self.connection and self.verify_connection_state(['AUTH', 'SELECTED']):
                app_logger.debug("DEBUG: 既存の接続を再利用")
                return True

            # 新規接続を試みる
            if not self.connect():
                app_logger.debug("DEBUG: 接続に失敗")
                return False

            app_logger.debug("DEBUG: 接続成功")

            # フォルダー一覧の取得を試みる
            app_logger.debug("DEBUG: フォルダー一覧取得開始")
            if not self.verify_connection_state(['AUTH']):
                app_logger.error("DEBUG: フォルダー一覧取得前の状態検証に失敗")
                return False

            try:
                status, folders = self.connection.list()
                if status != 'OK':
                    app_logger.error(f"DEBUG: フォルダー一覧取得失敗: {status}")
                    return False

                app_logger.debug("DEBUG: 利用可能なフォルダー:")
                for folder in folders:
                    app_logger.debug(f"DEBUG: - {folder.decode()}")

                return True

            except imaplib.IMAP4.error as e:
                app_logger.error(f"DEBUG: IMAPフォルダー一覧取得エラー: {str(e)}")
                return False

        except imaplib.IMAP4.error as e:
            app_logger.error(f"DEBUG: IMAP接続エラー: {str(e)}")
            return False
        except Exception as e:
            app_logger.error(f"DEBUG: 一般エラー: {str(e)}")
            app_logger.error("DEBUG: 詳細なエラー情報:")
            app_logger.error(traceback.format_exc())
            return False
        finally:
            app_logger.debug("DEBUG: 接続テスト終了")
            # 接続プールに返却する前に状態を確認
            if self.connection:
                if not self.verify_connection_state(['AUTH', 'SELECTED'], allow_reconnect=False):
                    self.disconnect()
                else:
                    self._pool.put_connection(self.connection)
                    self.connection = None

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
                
    def verify_connection_state(self, allowed_states: List[str], allow_reconnect: bool = True, force_folder: str = None, _retry_count: int = 0) -> bool:
        """接続状態を検証する（再帰制限・状態検証簡素化版）"""
        MAX_VERIFY_RETRIES = 3  # 再帰呼び出しの最大数を制限
        
        if _retry_count >= MAX_VERIFY_RETRIES:
            app_logger.error("Maximum verification retry count reached")
            return False
            
        if not self.connection:
            return False
            
        try:
            # Set shorter timeout for NOOP command
            original_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(10)
            
            try:
                # シンプルな状態確認
                current_state = getattr(self.connection, 'state', None)
                if current_state not in allowed_states:
                    app_logger.warning(f"Invalid state: {current_state}, expected: {allowed_states}")
                    raise imaplib.IMAP4.error("Invalid connection state")
                
                # NOOPで接続状態を確認
                status, _ = self.connection.noop()
                if status != 'OK':
                    raise imaplib.IMAP4.error("NOOP command failed")
                
                # 状態が変化していないことを確認
                if getattr(self.connection, 'state', None) != current_state:
                    raise imaplib.IMAP4.error("State changed during verification")
                
            except (socket.timeout, imaplib.IMAP4.error) as e:
                app_logger.warning(f"Connection check failed: {str(e)}")
                if not allow_reconnect:
                    return False
                    
                self.disconnect()
                # エラーに応じたバックオフ時間を設定
                backoff_time = min(10 * (2 ** _retry_count), 30)
                time.sleep(backoff_time)
                
                # 再接続を試みる（再帰呼び出し回数を制限）
                if self.connect():
                    if force_folder:
                        if not self.select_folder(force_folder):
                            return False
                    return self.verify_connection_state(
                        allowed_states, 
                        allow_reconnect=True,
                        force_folder=force_folder,
                        _retry_count=_retry_count + 1
                    )
                return False
                
            finally:
                socket.setdefaulttimeout(original_timeout)
            
            # AUTH状態への遷移を優先
            if 'AUTH' in allowed_states and current_state != 'AUTH':
                try:
                    self.connection.close()
                except:
                    pass
                return self.verify_connection_state(
                    ['AUTH'],
                    allow_reconnect=True,
                    _retry_count=_retry_count + 1
                )
            
            # SELECTEDが必要な場合のフォルダー選択
            if force_folder and 'SELECTED' in allowed_states:
                if not self.select_folder(force_folder):
                    return False
            
            self.last_activity = datetime.now()
            return True
            
        except Exception as e:
            app_logger.error(f"Verification error: {str(e)}")
            return False
                
            # Check connection age with dynamic threshold
            age = (datetime.now() - self.last_activity).total_seconds()
            dynamic_threshold = RECONNECT_THRESHOLD * (1.5 if 'SELECTED' in allowed_states else 1.0)
            
            if age > dynamic_threshold:
                app_logger.debug(f"Connection age ({age}s) exceeded threshold ({dynamic_threshold}s), reconnecting...")
                if allow_reconnect:
                    self.disconnect()
                    reconnected = self.connect()
                    if reconnected and force_folder:
                        if not self.select_folder(force_folder):
                            app_logger.error("Failed to select folder after age-based reconnect")
                            return False
                    return reconnected
                return False
                
            # 接続プールの状態管理の改善
            if current_state == 'AUTH' and not force_folder:
                try:
                    # プール内の接続を更新
                    self._pool.put_connection(self.connection)
                    self.connection = self._pool.get_connection(self)
                    if not self.connection:
                        app_logger.debug("Failed to get connection from pool, creating new connection")
                        return self.connect()
                except Exception as e:
                    app_logger.error(f"Connection pool management error: {str(e)}")
                    return self.connect() if allow_reconnect else False
                
            # Update last activity time
            self.last_activity = datetime.now()
            return True
            
        except Exception as e:
            app_logger.error(f"Connection state verification failed: {str(e)}")
            if allow_reconnect:
                self.disconnect()
                # エラータイプに基づくバックオフ時間の調整
                backoff_time = min(60, len(allowed_states) * 20)
                app_logger.debug(f"Verification failed, backing off for {backoff_time} seconds...")
                time.sleep(backoff_time)
                
                reconnected = self.connect()
                if reconnected and force_folder:
                    if not self.select_folder(force_folder):
                        app_logger.error("Failed to select folder after error recovery")
                        return False
                return reconnected
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
        """新着メールを確認し、パースして返す（バッチサイズ最適化版）"""
        new_emails = []
        last_reconnect = time.time()
        connection_error_count = 0
        consecutive_success = 0
        current_batch_size = 15  # より安全な初期バッチサイズ
        min_batch_size = 5
        max_batch_size = 25  # 最大バッチサイズを制限
        batch_delay = 0.5
        success_threshold = 0.8  # バッチサイズ増加の閾値
        
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
                    for i in range(0, total_messages, current_batch_size):
                        batch = message_nums[i:i + current_batch_size]
                        batch_errors = 0
                        
                        # バッチ処理前のNOOP確認
                        try:
                            status, _ = self.connection.noop()
                            if status != 'OK':
                                raise Exception("NOOP check failed before batch processing")
                        except Exception as e:
                            app_logger.error(f"NOOP check failed: {str(e)}")
                            self.disconnect()
                            if not self.connect() or not self.select_folder(folder):
                                raise Exception("Failed to recover connection after NOOP failure")
                        
                        for num in batch:
                            try:
                                # 接続状態を確認し、必要に応じて再接続（タイムアウト対応強化版）
                                current_time = time.time()
                                connection_age = current_time - last_reconnect
                                
                                # 定期的な再接続チェックに加えて、接続状態も確認
                                if connection_age > RECONNECT_INTERVAL or not self.verify_connection_state(['SELECTED'], allow_reconnect=False):
                                    app_logger.debug(f"Connection refresh needed (age: {connection_age}s)")
                                    
                                    # 再接続試行回数を制限
                                    max_reconnect_attempts = 3
                                    reconnect_attempt = 0
                                    
                                    while reconnect_attempt < max_reconnect_attempts:
                                        try:
                                            app_logger.debug(f"Reconnection attempt {reconnect_attempt + 1}/{max_reconnect_attempts}")
                                            self.disconnect()
                                            
                                            # 指数バックオフを使用
                                            backoff_time = min(2 ** reconnect_attempt, 10)
                                            time.sleep(backoff_time)
                                            
                                            if self.connect():
                                                if self.select_folder(folder):
                                                    app_logger.debug("Successfully reconnected and selected folder")
                                                    last_reconnect = current_time
                                                    break
                                            
                                            reconnect_attempt += 1
                                            
                                        except Exception as e:
                                            app_logger.error(f"Reconnection attempt {reconnect_attempt + 1} failed: {str(e)}")
                                            reconnect_attempt += 1
                                            
                                    if reconnect_attempt >= max_reconnect_attempts:
                                        raise Exception("Failed to re-establish connection after multiple attempts")

                                # FETCH操作前の追加の状態検証
                                if not self.verify_connection_state(['SELECTED'], force_folder=folder):
                                    app_logger.error(f"Connection not in SELECTED state before FETCH operation")
                                    raise Exception("Invalid connection state for FETCH")

                                # FETCH操作前のNOOP確認
                                try:
                                    status, _ = self.connection.noop()
                                    if status != 'OK':
                                        raise Exception("NOOP check failed before FETCH")
                                except Exception as e:
                                    app_logger.error(f"NOOP check failed: {str(e)}")
                                    raise

                                try:
                                    # メッセージを取得
                                    status, msg_data = self.connection.fetch(num, '(RFC822)')
                                    if status != 'OK' or not msg_data or not msg_data[0]:
                                        app_logger.warning(f"FETCH command failed or returned invalid data for message {num}")
                                        batch_errors += 1
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

                                    if parsed_msg and parsed_msg['message_id']:
                                        parsed_msg['folder'] = folder
                                        parsed_msg['is_sent'] = folder == sent_folder
                                        new_emails.append(parsed_msg)
                                        consecutive_success += 1

                                except Exception as e:
                                    app_logger.error(f"Error during FETCH operation: {str(e)}")
                                    batch_errors += 1
                                    consecutive_success = 0
                                    # 接続状態の回復を試みる
                                    if not self.verify_connection_state(['SELECTED'], force_folder=folder):
                                        raise Exception("Failed to recover connection state")
                                    continue

                            except Exception as e:
                                app_logger.error(f"Error processing message {num}: {str(e)}")
                                connection_error_count += 1
                                consecutive_success = 0
                                batch_errors += 1
                                
                                # エラー回数に応じてバックオフ時間を延長
                                backoff_time = min(30, (batch_errors * 5))
                                app_logger.debug(f"Backing off for {backoff_time} seconds...")
                                time.sleep(backoff_time)
                                
                                if connection_error_count > 5:
                                    raise Exception("Too many connection errors")
                                continue

                        # バッチサイズの動的調整
                        if batch_errors == 0 and consecutive_success >= current_batch_size:
                            # 成功が続いている場合、バッチサイズを増やす
                            current_batch_size = min(50, current_batch_size + 5)
                            app_logger.debug(f"Increasing batch size to: {current_batch_size}")
                        elif batch_errors > current_batch_size // 4:
                            # エラーが多い場合、バッチサイズを減らす
                            current_batch_size = max(5, current_batch_size - 5)
                            app_logger.debug(f"Decreasing batch size to: {current_batch_size}")
                            consecutive_success = 0

                        # バッチ処理後の待機（エラー数に応じて調整）
                        adjusted_delay = batch_delay * (1 + (batch_errors * 0.5))
                        time.sleep(adjusted_delay)

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