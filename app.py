import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from email_handler import EmailHandler
from werkzeug.security import generate_password_hash, check_password_hash
from database import db
from models import EmailMessage, EmailSettings
from celery_worker import sync_emails
from datetime import datetime
from database import session_scope
from flask_caching import Cache

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or "your-secret-key"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///email_chat.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# キャッシュ設定
cache = Cache(config={
    'CACHE_TYPE': 'simple'
})
cache.init_app(app)
db.init_app(app)

@app.route('/')
def index():
    if 'email' not in session:
        return redirect(url_for('settings'))
        
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # 連絡先の取得
    contacts = cache.get('contacts')
    if not contacts:
        try:
            handler = EmailHandler(
                session['email'],
                session['password'],
                session['imap_server']
            )
            contacts = handler.get_contacts()
            handler.disconnect()
            cache.set('contacts', contacts, timeout=3600)
        except Exception as e:
            print(f"連絡先取得エラー: {str(e)}")
            contacts = []
    
    # メッセージデータの初期化
    messages = {
        'items': [],
        'total': 0,
        'has_next': False,
        'next_page': None
    }
    
    selected_contact = request.args.get('contact')
    search_query = request.args.get('search')
    
    if selected_contact:
        try:
            # データベースセッションを使用してクエリを実行
            with db.session.begin():
                # クエリの実行とデータの取得
                messages_query = EmailMessage.query.filter(
                    db.or_(
                        EmailMessage.from_address == selected_contact,
                        EmailMessage.to_address == selected_contact
                    )
                ).order_by(EmailMessage.date.desc())
                
                if search_query:
                    messages_query = messages_query.filter(
                        db.or_(
                            EmailMessage.subject.ilike(f'%{search_query}%'),
                            EmailMessage.body.ilike(f'%{search_query}%')
                        )
                    )
                
                # 全件数を取得
                total = messages_query.count()
                
                # 現在のページのメッセージを取得して、データを完全にロード
                current_messages = messages_query.offset((page - 1) * per_page).limit(per_page).all()
                
                # セッションがアクティブな間にデータを辞書に変換
                messages = {
                    'items': [{
                        'id': msg.id,
                        'subject': msg.subject,
                        'body': msg.body,
                        'date': msg.date,
                        'is_sent': msg.is_sent
                    } for msg in current_messages],
                    'total': total,
                    'has_next': (page * per_page) < total,
                    'next_page': page + 1 if (page * per_page) < total else None
                }
        except Exception as e:
            print(f"メッセージ取得エラー: {str(e)}")
            messages = {
                'items': [],
                'total': 0,
                'has_next': False,
                'next_page': None
            }
    
    # テンプレートにデータを渡す
    return render_template(
        'index.html',
        messages=messages,
        contacts=contacts,
        current_page=page
    )

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        imap_server = request.form.get('imap_server')
        
        try:
            # Test connection
            handler = EmailHandler(email, password, imap_server)
            handler.disconnect()
            
            # Save credentials in session
            session['email'] = email
            session['password'] = password
            session['imap_server'] = imap_server
            
            flash("Connection successful!", "success")
            return redirect(url_for('index'))
        except Exception as e:
            flash(f"Connection failed: {str(e)}", "error")
    
    return render_template('settings.html')

@app.route('/api/search_contacts')
def search_contacts():
    if 'email' not in session:
        return jsonify([])
        
    query = request.args.get('q', '')
    print(f"検索クエリ: {query}")
    
    if len(query) >= 2:
        try:
            contacts = EmailMessage.query\
                .with_entities(EmailMessage.from_address)\
                .filter(EmailMessage.from_address.ilike(f'%{query}%'))\
                .distinct()\
                .limit(10)\
                .all()
            
            print(f"検索結果: {contacts}")
            results = [contact[0] for contact in contacts if contact[0]]
            return jsonify(results)
        except Exception as e:
            print(f"連絡先検索エラー: {str(e)}")
            return jsonify([])
    return jsonify([])

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('settings'))

with app.app_context():
    db.create_all()
