from app import create_app, db
from sqlalchemy import text

app = create_app()

with app.app_context():
    # 既存のデータをクリア
    try:
        with db.session.begin():
            db.session.execute(text('DELETE FROM email_message;'))
            # SQLiteでは自動採番をリセットするためにSQLITE_SEQUENCEを更新
            db.session.execute(text("DELETE FROM sqlite_sequence WHERE name='email_message';"))
            print("データベースのクリーンアップが完了しました。")
    except Exception as e:
        print(f"エラーが発生しました: {str(e)}")
