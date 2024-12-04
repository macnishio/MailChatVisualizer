import os
import tempfile
import fcntl
import logging

app_logger = logging.getLogger('mailchat')

class ProcessLock:
    """プロセスロックを管理するクラス"""
    def __init__(self, lock_name: str):
        """
        Args:
            lock_name (str): ロックファイルの名前（一意の識別子）
        """
        self.lock_file = os.path.join(tempfile.gettempdir(), f"{lock_name}.lock")
        self.lock_fd = None
        self.lock_name = lock_name

    def acquire(self) -> bool:
        """
        ロックを取得する

        Returns:
            bool: ロック取得成功時True、失敗時False
        """
        try:
            self.lock_fd = open(self.lock_file, 'w')
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_fd.write(str(os.getpid()))
            self.lock_fd.flush()
            app_logger.debug(f"Lock acquired: {self.lock_name}")
            return True
        except (IOError, OSError) as e:
            if self.lock_fd:
                self.lock_fd.close()
                self.lock_fd = None
            app_logger.debug(f"Failed to acquire lock {self.lock_name}: {str(e)}")
            return False

    def release(self):
        """ロックを解放する"""
        if self.lock_fd:
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                self.lock_fd.close()
                os.remove(self.lock_file)
                app_logger.debug(f"Lock released: {self.lock_name}")
            except (IOError, OSError) as e:
                app_logger.error(f"Error releasing lock {self.lock_name}: {str(e)}")
            finally:
                self.lock_fd = None

    def __enter__(self):
        """コンテキストマネージャーのサポート"""
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """コンテキストマネージャーのサポート"""
        self.release()

    def __del__(self):
        """デストラクタでの確実なロック解放"""
        self.release()