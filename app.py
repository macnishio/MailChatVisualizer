import os
from flask import Flask, render_template, request, session, redirect, url_for, flash, jsonify
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
        
    # キャッシュから連絡先を取得
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
            
            # キャッシュに保存（1時間）
            cache.set('contacts', contacts, timeout=3600)
        except Exception as e:
            print(f"連絡先取得エラー: {str(e)}")
            contacts = []
    
    # 検索フィルタリング
    contact_search = request.args.get('contact_search', '')
    if contact_search:
        contacts = [c for c in contacts if contact_search.lower() in c.lower()]
    
    messages = []
    selected_contact = request.args.get('contact')
    search_query = request.args.get('search')
    
    if selected_contact:
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
        
        messages = messages_query.paginate(
            page=page, 
            per_page=per_page, 
            error_out=False
        )
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            'messages': [msg.to_dict() for msg in messages.items],
            'has_next': messages.has_next,
            'next_page': messages.next_num if messages.has_next else None
        })
            
    return render_template('index.html', messages=messages, contacts=contacts)

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
