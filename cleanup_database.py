from app import create_app, db
from sqlalchemy import text

app = create_app()

with app.app_context():
    try:
        with db.session.begin():
            # メールデータのみを削除
            db.session.execute(text('DELETE FROM email_message;'))
            db.session.commit()
            print("メールデータを完全に削除しました")
    except Exception as e:
        print(f"エラーが発生しました: {str(e)}")
