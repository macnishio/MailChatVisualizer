from app import create_app, db
from sqlalchemy import text

app = create_app()

with app.app_context():
    # 既存のデータをクリア
    try:
        with db.session.begin():
            db.session.execute(text('TRUNCATE TABLE email_message RESTART IDENTITY CASCADE;'))
            print("データベースのクリーンアップが完了しました。")
    except Exception as e:
        print(f"エラーが発生しました: {str(e)}")
