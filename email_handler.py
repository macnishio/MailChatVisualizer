import imaplib
from typing import List, Set, Optional, Dict, Any
import email
from email.utils import parsedate_to_datetime
from email.header import decode_header
import socket
import time
import random
import threading
import traceback
from datetime import datetime, timedelta
import logging
import re
from database import session_scope
import hashlib
from models import EmailMessage


# ロガーの設定
app_logger = logging.getLogger('mailchat')

# 定数の定義
MAX_RETRIES = 5
CONNECTION_TIMEOUT = 30
RECONNECT_INTERVAL = 300  # 5分
BATCH_DELAY = 1.0
RETRY_DELAY = 2  # 基本待機時間
MAX_BATCH_SIZE = 50
MIN_BATCH_SIZE = 5
MAX_BACKOFF_TIME = 30  # 最大バックオフ時間（秒）
SOCKET_TIMEOUT = 60  # ソケットタイムアウト（秒）
KEEPALIVE_INTERVAL = 60  # キープアライブ間隔（秒）
MAX_CONNECTION_AGE = 600  # 最大接続維持時間（秒）

class ConnectionState:
    DISCONNECTED = "DISCONNECTED"
    CONNECTED = "CONNECTED"
    AUTH = "AUTH"
    SELECTED = "SELECTED"
    ERROR = "ERROR"

class EmailHandler:
    def __init__(self, email_address: str, password: str, imap_server: str):
        self.email_address = email_address
        self.password = password
        self.imap_server = imap_server
        self.connection: Optional[imaplib.IMAP4_SSL] = None
        self.last_activity = None
        self.connection_lock = threading.Lock()
        self.current_state = ConnectionState.DISCONNECTED
        self.current_folder: Optional[str] = None
        self.last_error: Optional[str] = None
        self.connection_attempts = 0
        self.last_reconnect_time = 0

    def _update_state(self, new_state: str, error: Optional[str] = None) -> None:
        """接続状態を更新し、必要に応じてログを出力"""
        self.current_state = new_state
        if error:
            self.last_error = error
            app_logger.error(f"Connection state changed to {new_state}: {error}")
        else:
            app_logger.debug(f"Connection state changed to {new_state}")

    def _should_reconnect(self) -> bool:
        """再接続が必要かどうかを判断"""
        if not self.connection:
            return True
        
        current_time = time.time()
        if current_time - self.last_reconnect_time > RECONNECT_INTERVAL:
            return True

        try:
            status, _ = self.connection.noop()
            return status != 'OK'
        except Exception:
            return True

    def connect(self) -> bool:
        """IMAPサーバーに接続し、ログインする（タイムアウト対応強化版）"""
        with self.connection_lock:
            if self.current_state == ConnectionState.CONNECTED:
                try:
                    status, _ = self.connection.noop()
                    if status == 'OK':
                        # 接続が古すぎる場合は再接続
                        current_time = time.time()
                        if current_time - self.last_reconnect_time > MAX_CONNECTION_AGE:
                            app_logger.debug("Connection age exceeded maximum, forcing reconnection")
                            self.disconnect()
                        else:
                            return True
                except Exception:
                    pass

            self._update_state(ConnectionState.DISCONNECTED)
            self.connection_attempts += 1
            
            try:
                # 既存の接続をクリーンアップ
                self.disconnect()
                
                # タイムアウト設定の調整（ジッター追加）
                original_timeout = socket.getdefaulttimeout()
                jitter = random.uniform(0, 2)
                adjusted_timeout = min(
                    SOCKET_TIMEOUT * (1.5 ** min(self.connection_attempts - 1, 3)) + jitter,
                    MAX_BACKOFF_TIME
                )
                socket.setdefaulttimeout(adjusted_timeout)
                
                try:
                    app_logger.debug(f"Creating new connection to {self.imap_server}...")
                    self.connection = imaplib.IMAP4_SSL(self.imap_server)
                    
                    # ソケットレベルのタイムアウト設定
                    self.connection.socket().settimeout(adjusted_timeout)
                    self.connection.socket().setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                    if hasattr(socket, 'TCP_KEEPIDLE'):
                        self.connection.socket().setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, KEEPALIVE_INTERVAL)
                    if hasattr(socket, 'TCP_KEEPINTVL'):
                        self.connection.socket().setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, KEEPALIVE_INTERVAL // 3)
                    if hasattr(socket, 'TCP_KEEPCNT'):
                        self.connection.socket().setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                    
                    app_logger.debug("Attempting login...")
                    self.connection.login(self.email_address, self.password)
                    app_logger.debug("Login successful")
                    
                    # 接続状態の更新
                    self._update_state(ConnectionState.AUTH)
                    self.last_reconnect_time = time.time()
                    self.last_activity = datetime.now()
                    self.connection_attempts = 0
                    
                    # 接続の健全性を確認
                    status, _ = self.connection.noop()
                    if status != 'OK':
                        raise Exception("Connection health check failed after login")
                        
                    return True
                    
                finally:
                    socket.setdefaulttimeout(original_timeout)
                    
            except (socket.timeout, socket.error) as e:
                self._update_state(ConnectionState.ERROR, f"Network error: {str(e)}")
                self._handle_connection_error()
                return False
                
            except imaplib.IMAP4.error as e:
                self._update_state(ConnectionState.ERROR, f"IMAP error: {str(e)}")
                self._handle_connection_error()
                return False
                
            except Exception as e:
                self._update_state(ConnectionState.ERROR, f"Unexpected error: {str(e)}")
                self._handle_connection_error()
                return False

    def _handle_connection_error(self) -> None:
        """接続エラーの処理とバックオフ"""
        backoff_time = min(RETRY_DELAY * (2 ** (self.connection_attempts - 1)), 30)
        app_logger.debug(f"Backing off for {backoff_time} seconds...")
        time.sleep(backoff_time)
        self.disconnect()

    def disconnect(self) -> None:
        """IMAPサーバーから切断する（改善版）"""
        if self.connection:
            try:
                if self.current_state == ConnectionState.SELECTED:
                    try:
                        self.connection.close()
                    except Exception:
                        pass
                try:
                    self.connection.logout()
                except Exception:
                    pass
            finally:
                self.connection = None
                self.current_folder = None
                self._update_state(ConnectionState.DISCONNECTED)
                self.last_activity = None

    def verify_connection_state(self, expected_states: List[str], allow_reconnect: bool = True, force_folder: Optional[str] = None) -> bool:
        """接続状態を検証し、必要に応じて再接続を行う（タイムアウト対応強化版）"""
        with self.connection_lock:
            # タイムアウト検出を強化
            if self.connection:
                try:
                    status, _ = self.connection.noop()
                    if status != 'OK':
                        app_logger.warning("Connection test failed (NOOP check)")
                        self.current_state = ConnectionState.ERROR
                except Exception as e:
                    app_logger.warning(f"Connection test failed: {str(e)}")
                    self.current_state = ConnectionState.ERROR

            if self.current_state not in expected_states:
                app_logger.warning(f"Invalid connection state: {self.current_state}, expected one of {expected_states}")
                
                if not allow_reconnect:
                    return False
                
                # 再接続試行回数を制限し、バックオフを改善
                max_attempts = 3
                for attempt in range(max_attempts):
                    try:
                        if attempt > 0:
                            # 指数バックオフにジッターを追加
                            backoff_time = min(2 ** attempt + random.uniform(0, 1), 15)
                            app_logger.debug(f"Backing off for {backoff_time:.2f} seconds before reconnection attempt {attempt + 1}")
                            time.sleep(backoff_time)
                        
                        # 既存の接続を確実にクリーンアップ
                        self.disconnect()
                        
                        if self.connect():
                            app_logger.debug("Reconnection successful")
                            break
                            
                    except Exception as e:
                        app_logger.error(f"Reconnection attempt {attempt + 1} failed: {str(e)}")
                        if attempt < max_attempts - 1:
                            continue
                        return False
            
            if force_folder and (self.current_folder != force_folder or self.current_state != ConnectionState.SELECTED):
                app_logger.debug(f"Attempting to select folder: {force_folder}")
                try:
                    status, data = self.connection.select(force_folder)
                    if status != 'OK':
                        raise Exception(f"Failed to select folder: {force_folder}")
                    
                    message_count = int(data[0])
                    app_logger.debug(f"Selected folder contains {message_count} messages")
                    self.current_folder = force_folder
                    self._update_state(ConnectionState.SELECTED)
                    app_logger.debug(f"Successfully selected folder: {force_folder}")
                    return True
                    
                except Exception as e:
                    app_logger.error(f"Error selecting folder: {str(e)}")
                    if allow_reconnect:
                        return self.connect()
                    return False
            
            return True

    def refresh_connection(self) -> bool:
        """接続を更新する（改善版）"""
        app_logger.debug("Refreshing connection...")
        self.disconnect()
        
        # 指数バックオフを使用した再接続
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                backoff_time = min(2 ** attempt, 10)
                time.sleep(backoff_time)
                
                if self.connect():
                    app_logger.debug("Connection refreshed successfully")
                    return True
                    
            except Exception as e:
                app_logger.error(f"Connection refresh attempt {attempt + 1} failed: {str(e)}")
                if attempt < max_attempts - 1:
                    continue
                break
                
        app_logger.error("Failed to refresh connection after multiple attempts")
        return False

    def _handle_fetch_error(self, error: Exception, folder: str) -> bool:
        """FETCH操作のエラーハンドリング（タイムアウト対応強化版）"""
        error_str = str(error)
        
        # タイムアウトエラーの検出を改善
        is_timeout_error = any(err in error_str.lower() for err in [
            'timeout', 'bye', 'logout', 'eof occurred', 'socket error',
            'connection reset', 'broken pipe', 'connection refused'
        ])
        
        if is_timeout_error:
            app_logger.error(f"Connection error during FETCH: {error_str}")
            
            # 再接続試行回数を制限し、バックオフを改善
            max_attempts = 3
            base_delay = 2
            for attempt in range(max_attempts):
                try:
                    if attempt > 0:
                        # 指数バックオフにジッターを追加
                        jitter = random.uniform(0, 1)
                        backoff_time = min(base_delay * (2 ** attempt) + jitter, 20)
                        app_logger.debug(f"Backing off for {backoff_time:.2f} seconds before retry attempt {attempt + 1}/{max_attempts}")
                        time.sleep(backoff_time)
                    
                    # 接続状態をリセット
                    self.disconnect()
                    
                    # 新しい接続を確立
                    if self.connect():
                        # フォルダーの再選択
                        if self.verify_connection_state(['SELECTED'], force_folder=folder):
                            # 接続の健全性を確認
                            status, _ = self.connection.noop()
                            if status == 'OK':
                                app_logger.debug(f"Successfully recovered from FETCH error (attempt {attempt + 1})")
                                return True
                            
                except Exception as e:
                    app_logger.error(f"Error recovery attempt {attempt + 1} failed: {str(e)}")
                    if attempt < max_attempts - 1:
                        continue
                    
                # 最後の試行が失敗した場合はより長い待機時間を設定
                if attempt == max_attempts - 1:
                    time.sleep(min(base_delay * (2 ** (attempt + 1)), 30))
                        
            app_logger.error("Failed to recover from FETCH error after multiple attempts")
            # 接続の完全リセット
            self.disconnect()
        return False

    def get_gmail_folders(self):
        """Gmailの特殊フォルダーを取得する"""
        try:
            if not self.verify_connection_state(['AUTH', 'SELECTED']):
                raise Exception("Invalid connection state")

            _, folders = self.connection.list()
            sent_folder = None

            for folder_info in folders:
                folder_name = folder_info.decode().split('"/"')[-1].strip()
                if '[Gmail]/送信済みメール' in folder_name or '[Gmail]/Sent Mail' in folder_name:
                    sent_folder = folder_name.strip('"')
                    break

            return sent_folder

        except Exception as e:
            app_logger.error(f"フォルダー取得エラー: {str(e)}")
            return None

    def select_folder(self, folder):
        """フォルダーを選択し、必要に応じて再接続を行う"""
        if not self.verify_connection_state(['AUTH', 'SELECTED']):
            if not self.connect():
                return False

        try:
            status, data = self.connection.select(folder)
            if status != 'OK':
                raise Exception(f"フォルダー選択エラー: {folder}")
            
            # 選択したフォルダーのメッセージ数を取得
            message_count = int(data[0])
            app_logger.debug(f"Selected folder contains {message_count} messages")
            
            self.current_folder = folder
            self._update_state(ConnectionState.SELECTED)
            return True

        except Exception as e:
            app_logger.error(f"フォルダー選択エラー: {str(e)}")
            return False

    def check_new_emails(self):
        """新着メールをチェックし、バッチ処理で取得・保存する（改善版）"""
        new_emails = []
        sent_folder = self.get_gmail_folders()
        
        try:
            # 既存のメッセージIDをキャッシュとして取得
            with session_scope() as session:
                existing_message_ids = set(
                    id[0] for id in session.query(EmailMessage.message_id).all()
                )
                app_logger.debug(f"Loaded {len(existing_message_ids)} existing message IDs")

            folders = ['INBOX']
            if sent_folder:
                folders.append(sent_folder)

            for folder in folders:
                try:
                    if not self.select_folder(folder):
                        app_logger.warning(f"フォルダー {folder} の選択に失敗しました")
                        continue

                    _, message_nums = self.connection.search(None, 'ALL')
                    if not message_nums[0]:
                        app_logger.debug(f"フォルダー {folder} にメッセージが見つかりませんでした")
                        continue

                    message_nums = message_nums[0].split()
                    total_messages = len(message_nums)
                    app_logger.debug(f"Found {total_messages} messages in {folder}")

                    current_batch_size = MIN_BATCH_SIZE
                    batch_delay = BATCH_DELAY
                    connection_error_count = 0
                    consecutive_success = 0
                    total_processed = 0
                    total_saved = 0
                    total_skipped = 0  # スキップされたメッセージの合計
                    last_reconnect = time.time()

                    with session_scope() as session:  # トランザクション開始
                        for i in range(0, total_messages, current_batch_size):
                            batch_count = (i // current_batch_size) + 1
                            app_logger.debug(f"バッチ処理開始: {batch_count}/{(total_messages + current_batch_size - 1) // current_batch_size}")
                            
                            batch = message_nums[i:i + current_batch_size]
                            batch_errors = 0
                            batch_success = 0
                            batch_messages = []
                            batch_saved = 0
                            batch_skipped = 0  # スキップされたメッセージのカウンター

                            app_logger.debug(f"バッチサイズ: {current_batch_size}, 処理済み: {total_processed}/{total_messages}")

                            # バッチ処理前のNOOP確認
                            try:
                                status, _ = self.connection.noop()
                                if status != 'OK':
                                    raise Exception("NOOP確認失敗")
                            except Exception as e:
                                app_logger.error(f"NOOP確認エラー: {str(e)}")
                                self.disconnect()
                                if not self.connect() or not self.select_folder(folder):
                                    raise Exception("NOOP失敗後の接続回復失敗")

                            # メッセージの処理とDB保存
                            try:
                                for num in batch:
                                    try:
                                        # メッセージの取得と解析
                                        status, msg_data = self.connection.fetch(num, '(RFC822)')
                                        if status != 'OK' or not msg_data or not msg_data[0]:
                                            app_logger.warning(f"FETCH command failed for message {num}")
                                            batch_errors += 1
                                            continue

                                        email_body = msg_data[0][1]
                                        parsed_msg = self.parse_email_message(email_body)

                                        
                                        if parsed_msg and parsed_msg['message_id']:
                                            # メッセージIDによる重複チェック
                                            if parsed_msg['message_id'] in existing_message_ids:
                                                app_logger.debug(f"Skipping duplicate message: {parsed_msg['message_id']}")
                                                batch_skipped += 1
                                                continue

                                            # 新規メッセージの場合のみDBに保存
                                            new_email = EmailMessage(
                                                message_id=parsed_msg['message_id'],
                                                from_address=parsed_msg['from'],
                                                to_address=parsed_msg['to'],
                                                subject=parsed_msg['subject'],
                                                body=parsed_msg['body'],
                                                body_hash=parsed_msg['body_hash'],
                                                body_preview=parsed_msg['body_preview'],
                                                date=parsed_msg['date'],
                                                is_sent=folder == sent_folder,
                                                folder=folder
                                            )
                                            # 連絡先の更新を行う
                                            new_email.update_contacts()
                                            session.add(new_email)
                                            batch_saved += 1
                                            session.flush()  # 即時反映
                                                
                                            batch_success += 1
                                            total_processed += 1

                                    except Exception as e:
                                        app_logger.error(f"メッセージ処理エラー: {str(e)}")
                                        batch_errors += 1
                                        continue

                                # バッチ完了後のコミット
                                session.commit()
                                app_logger.debug(f"バッチ {batch_count} 完了: 成功={batch_success}, 新規保存={batch_saved}, スキップ={batch_skipped}, エラー={batch_errors}")
                                total_saved += batch_saved
                                total_skipped += batch_skipped  # 合計スキップ数を更新

                            except Exception as e:
                                app_logger.error(f"バッチ処理エラー: {str(e)}")
                                session.rollback()
                                raise
                        
                        # バッチ処理前のNOOP確認
                        try:
                            status, _ = self.connection.noop()
                            if status != 'OK':
                                raise Exception("NOOP確認失敗")
                        except Exception as e:
                            app_logger.error(f"NOOP確認エラー: {str(e)}")
                            self.disconnect()
                            if not self.connect() or not self.select_folder(folder):
                                raise Exception("NOOP失敗後の接続回復失敗")
                    
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
                                        batch_success += 1
                                        total_processed += 1

                                except Exception as e:
                                    if not self._handle_fetch_error(e, folder):
                                        app_logger.error(f"Error during FETCH operation: {str(e)}")
                                        batch_errors += 1
                                        consecutive_success = 0
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
                            current_batch_size = min(MAX_BATCH_SIZE, current_batch_size + 5)
                            app_logger.debug(f"Increasing batch size to: {current_batch_size}")
                        elif batch_errors > current_batch_size // 4:
                            # エラーが多い場合、バッチサイズを減らす
                            current_batch_size = max(MIN_BATCH_SIZE, current_batch_size - 5)
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

        app_logger.debug(f"同期完了: 処理済み={total_processed}, 新規保存={total_saved}, スキップ={total_skipped}")
        return new_emails

    def test_connection(self):
        """接続テストを行う"""
        app_logger.debug("Test Connection Started")
        try:
            self.connect()
            app_logger.debug("Connection successful")
            
            app_logger.debug("Retrieving folder list")
    def __del__(self):
        """Destructor to ensure proper cleanup of connections"""
        try:
            if hasattr(self, 'connection') and self.connection:
                self.disconnect()
        except Exception as e:
            app_logger.error(f"Error in destructor cleanup: {str(e)}")

    def _handle_timeout(self, error: Exception) -> bool:
        """Handle various timeout scenarios with improved recovery logic"""
        error_str = str(error).lower()
        is_timeout = any(err in error_str for err in [
            'timeout', 'timed out', 'eof occurred', 'connection reset',
            'broken pipe', 'connection refused', 'temporary failure'
        ])
        
        if not is_timeout:
            return False
            
        app_logger.warning(f"Timeout detected: {error_str}")
        
        # Implement exponential backoff with jitter
        max_attempts = 3
        base_delay = 2
        
        for attempt in range(max_attempts):
            try:
                jitter = random.uniform(0, 1)
                backoff_time = min(base_delay * (2 ** attempt) + jitter, 15)
                app_logger.debug(f"Backing off for {backoff_time:.2f} seconds (attempt {attempt + 1}/{max_attempts})")
                time.sleep(backoff_time)
                
                # Force disconnect and reconnect
                self.disconnect()
                if self.connect():
                    app_logger.debug(f"Successfully recovered from timeout (attempt {attempt + 1})")
                    return True
                    
            except Exception as e:
                app_logger.error(f"Recovery attempt {attempt + 1} failed: {str(e)}")
                if attempt == max_attempts - 1:
                    break
                    
        app_logger.error("Failed to recover from timeout after multiple attempts")
        return False

            _, folders = self.connection.list()
            app_logger.debug("Available folders:")
            for folder in folders:
                app_logger.debug(f"- {folder.decode()}")
            return True
            
        except imaplib.IMAP4.error as e:
            app_logger.error(f"IMAP connection error: {str(e)}")
            return False
            
        except Exception as e:
            app_logger.error(f"General error: {str(e)}")
            app_logger.error("Detailed error information:")
            traceback.print_exc()
            return False
            
        finally:
            app_logger.debug("Test Connection Ended")
            self.disconnect()

    def encode_folder_name(self, folder):
        """フォルダー名をUTF-7でエンコードする"""
        if isinstance(folder, bytes):
            return folder
        try:
            return folder.encode('utf-7').decode('ascii')
        except Exception as e:
            app_logger.error(f"フォルダー名エンコードエラー: {str(e)}")
            return folder

    def decode_folder_name(self, folder):
        """フォルダー名をデコードする"""
        if isinstance(folder, bytes):
            try:
                folder = folder.decode('ascii', errors='replace')
                return folder.encode('ascii').decode('utf-7')
            except UnicodeDecodeError as e:
                app_logger.error(f"フォルダー名デコードエラー: {str(e)}。フォルダー名をそのまま使用します: {folder}")
                return folder
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
            folders = ['INBOX']
            if sent_folder:
                folders.append(sent_folder)

            for folder in folders:
                try:
                    encoded_folder = self.encode_folder_name(folder)
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

                        status, nums = self.connection.uid('SEARCH', None, search_criteria)
                        if status != 'OK':
                            app_logger.error(f"メールの検索に失敗しました: {search_criteria}")
                            continue

                        if not nums[0]:
                            app_logger.debug(f"No messages found for criteria: {search_criteria}")
                            continue

                        for num in nums[0].split():
                            try:
                                status, msg_data = self.connection.fetch(num, '(RFC822)')
                                if status != 'OK':
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
        try:
            msg = email.message_from_bytes(email_body)
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

            # 本文のハッシュとプレビューを生成
            body_hash = None
            body_preview = None
            if body:
                # MD5ハッシュの生成
                body_hash = hashlib.md5(body.encode()).hexdigest()

                # プレビューの生成（HTMLタグを除去）
                text = re.sub(r'<[^>]+>', '', body)
                text = re.sub(r'\s+', ' ', text)  # 連続する空白を1つに
                body_preview = text[:1000].strip()

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

            return {
                'message_id': msg['message-id'],
                'from': from_str,
                'to': to_str,
                'subject': subject,
                'body': body,
                'body_hash': body_hash,
                'body_preview': body_preview,
                'date': parsedate_to_datetime(msg['date']),
                'is_sent': self.email_address and self.email_address in from_str if self.email_address else False
            }

        except Exception as e:
            app_logger.error(f"メッセージパースエラー: {str(e)}")
            traceback.print_exc()
            return None

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