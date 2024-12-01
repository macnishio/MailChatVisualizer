import os
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from models import db, EmailMessage
import traceback
from email_handler import EmailHandler
from database import session_scope
from flask_caching import Cache
from sqlalchemy import text, or_, and_
import re

def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or "your-secret-key"
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///email_chat.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    
    # キャッシュ設定
    cache = Cache(config={'CACHE_TYPE': 'simple'})
    cache.init_app(app)
    db.init_app(app)
    
    return app

app = create_app()

def highlight(text, search_query):
    """検索キーワードをハイライト表示するフィルター"""
    if not text or not search_query:
        return text
    
    terms = search_query.split()
    for term in terms:
        if term.strip():
            pattern = re.compile(f'({re.escape(term)})', re.IGNORECASE)
            text = pattern.sub(r'<mark>\1</mark>', str(text))
    return text

app.jinja_env.filters['highlight'] = highlight

# キャッシュ設定
cache = Cache(config={
    'CACHE_TYPE': 'simple'
})
cache.init_app(app)

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
    messages_dict = {
        'message_list': [],
        'total': 0,
        'has_next': False,
        'next_page': None
    }
    
    selected_contact = request.args.get('contact')
    search_query = request.args.get('search')
    
    if selected_contact or search_query:
        try:
            with db.session.begin():
                messages_query = EmailMessage.query

                if selected_contact:
                    messages_query = messages_query.filter(
                        or_(
                            EmailMessage.from_address == selected_contact,
                            EmailMessage.to_address == selected_contact
                        )
                    )
                
                if search_query:
                    search_terms = [term.strip() for term in search_query.split() if term.strip()]
                    for term in search_terms:
                        messages_query = messages_query.filter(
                            and_(
                                or_(
                                    EmailMessage.subject.ilike(f'%{term}%'),
                                    EmailMessage.body.ilike(f'%{term}%'),
                                    EmailMessage.from_address.ilike(f'%{term}%'),
                                    EmailMessage.to_address.ilike(f'%{term}%')
                                )
                            )
                        )
                
                messages_query = messages_query.order_by(EmailMessage.date.desc())
                
                # デバッグ用：クエリの内容を出力
                print(f"SQL Query: {messages_query}")
                
                # デバッグ用：取得したメッセージの数を出力
                total = messages_query.count()
                print(f"Total messages found: {total}")
                
                current_messages = messages_query.offset((page - 1) * per_page).limit(per_page).all()
                
                # デバッグ用：各メッセージの内容を確認
                for msg in current_messages:
                    print(f"Message ID: {msg.id}")
                    print(f"Subject: {msg.subject}")
                    print(f"Body length: {len(msg.body) if msg.body else 0}")
                    print("---")
                
                messages_dict['message_list'] = [{
                    'id': msg.id,
                    'subject': msg.subject,
                    'body': msg.body,
                    'date': msg.date,
                    'is_sent': msg.is_sent,
                    'from_address': msg.from_address,
                    'to_address': msg.to_address
                } for msg in current_messages]
                
                messages_dict['total'] = total
                messages_dict['has_next'] = (page * per_page) < total
                messages_dict['next_page'] = page + 1 if (page * per_page) < total else None
                
        except Exception as e:
            print(f"メッセージ取得エラー: {str(e)}")
            traceback.print_exc()
    
    return render_template(
        'index.html',
        messages=messages_dict,
        contacts=contacts,
        current_page=page,
        search_query=search_query
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
                .filter(
                    or_(
                        EmailMessage.from_address.ilike(f'%{query}%'),
                        EmailMessage.to_address.ilike(f'%{query}%')
                    )
                )\
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

@app.route('/api/search_messages')
def search_messages():
    if 'email' not in session:
        return jsonify({'messages': [], 'total': 0})
        
    query = request.args.get('q', '')
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    try:
        messages_query = EmailMessage.query
        
        if query:
            search_terms = query.split()
            for term in search_terms:
                messages_query = messages_query.filter(
                    or_(
                        EmailMessage.subject.ilike(f'%{term}%'),
                        EmailMessage.body.ilike(f'%{term}%'),
                        EmailMessage.from_address.ilike(f'%{term}%'),
                        EmailMessage.to_address.ilike(f'%{term}%')
                    )
                )
        
        total = messages_query.count()
        messages = messages_query.order_by(EmailMessage.date.desc())\
            .offset((page - 1) * per_page)\
            .limit(per_page)\
            .all()
            
        return jsonify({
            'messages': [{
                'id': msg.id,
                'subject': msg.subject,
                'body': msg.body,
                'date': msg.date.isoformat(),
                'from_address': msg.from_address,
                'to_address': msg.to_address,
                'is_sent': msg.is_sent
            } for msg in messages],
            'total': total,
            'has_next': (page * per_page) < total,
            'next_page': page + 1 if (page * per_page) < total else None
        })
    except Exception as e:
        print(f"メッセージ検索エラー: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('settings'))

with app.app_context():
    db.create_all()
