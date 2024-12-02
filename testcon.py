def test_connection(self):
    print(f"DEBUG: 接続テスト開始 - Server: {self.imap_server}, Email: {self.email_address}")
    try:
        self.connect()
        print("DEBUG: 接続成功")
        print("DEBUG: フォルダー一覧取得開始")
        _, folders = self.connection.list()
        print("DEBUG: 利用可能なフォルダー:")
        for folder in folders:
            print(f"DEBUG: - {folder.decode()}")
        return True
    except imaplib.IMAP4.error as e:
        print(f"DEBUG: IMAP接続エラー: {str(e)}")
        return False
    except Exception as e:
        print(f"DEBUG: 一般エラー: {str(e)}")
        print("DEBUG: 詳細なエラー情報:")
        traceback.print_exc()
        return False
    finally:
        print("DEBUG: 接続テスト終了")
        self.disconnect()