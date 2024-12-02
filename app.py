import os
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from models import db, EmailMessage
import traceback
from email_handler import EmailHandler  # EmailConnectionPoolのインポートを削除
from database import session_scope
from flask_caching import Cache
from sqlalchemy import text, or_, and_, case
import re
import unicodedata
import time
from functools import wraps  # 追加
import threading  # 追加
import logging
import sys

# ロガーの基本設定
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)

# アプリケーション専用のロガーを作成
app_logger = logging.getLogger('mailchat')
app_logger.setLevel(logging.DEBUG)

# IMAPライブラリのデバッグログも有効化
logging.getLogger('imaplib').setLevel(logging.DEBUG)

# キャッシュ設定
# グローバルスコープでキャッシュを定義
cache = Cache(config={'CACHE_TYPE': 'simple'})

def create_app():
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY") or "your-secret-key"
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///email_chat.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # キャッシュを初期化
    cache.init_app(app)
    db.init_app(app)

    return app

app = create_app()
# キャッシュを明示的に初期化

app = create_app()

def highlight(text, search_query):
    """検索キーワードをハイライト表示するフィルター（改善版）"""
    if not text or not search_query:
        return text if text else ""
    
    try:
        text = str(text)
        terms = [term.strip() for term in search_query.split() if term.strip()]
        
        # テキストを正規化（全角・半角の違いを無視）
        normalized_text = unicodedata.normalize('NFKC', text)
        original_positions = []
        current_pos = 0
        
        for term in terms:
            try:
                normalized_term = unicodedata.normalize('NFKC', term)
                pattern = re.compile(f'({re.escape(normalized_term)})', re.IGNORECASE | re.UNICODE | re.MULTILINE)
                
                # 元のテキストでの位置を保持しながらハイライト
                matches = list(pattern.finditer(normalized_text))
                for match in matches:
                    start, end = match.span()
                    original_text = text[start:end]
                    highlighted = f'<mark class="search-highlight" data-term="{term}">{original_text}</mark>'
                    original_positions.append((start + current_pos, end + current_pos, highlighted))
                    current_pos += len(highlighted) - len(original_text)
                
            except Exception as e:
                print(f"単語のハイライト処理エラー: {term}, {str(e)}")
                continue
        
        # 位置情報を使って元のテキストを変更
        result = text
        for start, end, highlighted in sorted(original_positions, reverse=True):
            result = result[:start] + highlighted + result[end:]
            
        return result
    except Exception as e:
        print(f"ハイライト処理エラー: {str(e)}")
        return text

app.jinja_env.filters['highlight'] = highlight

# アプリケーションレベルの接続制御を追加
app_lock = threading.Lock()
last_request_time = 0
MIN_REQUEST_INTERVAL = 2.0  # 2秒

def with_connection_control(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        global last_request_time

        with app_lock:
            current_time = time.time()
            elapsed = current_time - last_request_time

            if elapsed < MIN_REQUEST_INTERVAL:
                time.sleep(MIN_REQUEST_INTERVAL - elapsed)

            try:
                result = func(*args, **kwargs)
                last_request_time = time.time()
                return result
            except Exception as e:
                print(f"Error in {func.__name__}: {str(e)}")
                raise

    return wrapper

def sync_emails_background(email_address, password, imap_server):
    """バックグラウンドでメールを同期する"""
    with app.app_context():
        try:
            # 新しい接続を作成
            background_handler = EmailHandler(
                email_address=email_address,
                password=password,
                imap_server=imap_server
            )
            # 明示的に接続
            background_handler.connect()
            app_logger.debug("Background handler connected")

            try:
                new_emails = background_handler.check_new_emails()
                app_logger.debug(f"Background sync completed: {len(new_emails) if new_emails else 0} new emails")
            except Exception as e:
                app_logger.error(f"Background sync error: {str(e)}")
            finally:
                # 必ず接続を切断
                background_handler.disconnect()
                app_logger.debug("Background handler disconnected")

        except Exception as e:
            app_logger.error(f"Background handler error: {str(e)}")

@app.route('/')
@with_connection_control
def index():
   # セッションからフラッシュメッセージをクリア
   if '_flashes' in session:
       session.pop('_flashes')

   app_logger.debug(f"Session contents: {session}")

   if 'email' not in session:
       app_logger.debug("No email in session, redirecting to settings")
       flash("メールの設定が必要です", "info")
       return redirect(url_for('settings'))

   app_logger.debug(f"Attempting to connect with email: {session['email']}")

   # IMAPハンドラーの作成
   handler = EmailHandler(
       email_address=session['email'],
       password=session['password'],
       imap_server=session['imap_server']
    )
    
   try:
       app_logger.debug("Starting connection test...")
       test_result = handler.test_connection()
       app_logger.debug(f"Connection test result: {test_result}")

       if not test_result:
           flash("メールサーバーへの接続テストに失敗しました", "error")
           app_logger.error("Connection test failed")
           return redirect(url_for('settings'))

       # バックグラウンドでメール同期を開始
       sync_thread = threading.Thread(
           target=sync_emails_background,
           args=(session['email'], session['password'], session['imap_server']),
           daemon=True
       )
       sync_thread.start()
       app_logger.debug("Started background email sync")
       flash("メールの同期をバックグラウンドで開始しました", "info")  # ユーザーに通知を追加

   except Exception as e:
       app_logger.error(f"Connection error: {str(e)}", exc_info=True)
       flash(f"接続エラー: {str(e)}", "error")
       return redirect(url_for('settings'))
    
   page = request.args.get('page', 1, type=int)
   per_page = 20

   # 連絡先の取得（キャッシュあり）
   contacts = cache.get('contacts')
   if not contacts:
       try:
           contacts = handler.get_contacts()
           if contacts:
               cache.set('contacts', contacts, timeout=3600)
           else:
               contacts = []
       except Exception as e:
           app_logger.error(f"連絡先取得エラー: {str(e)}")
           contacts = []
           flash("連絡先の取得に失敗しました。", "error")

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
           cache_key = f'messages:{selected_contact}:{search_query}:{page}'
           messages_dict = cache.get(cache_key)

           if messages_dict is None:
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
                       search_conditions = []

                       for term in search_terms:
                           normalized_term = unicodedata.normalize('NFKC', term)
                           condition = or_(
                               EmailMessage.subject.ilike(f'%{normalized_term}%'),
                               EmailMessage.body.ilike(f'%{normalized_term}%'),
                               EmailMessage.from_address.ilike(f'%{normalized_term}%'),
                               EmailMessage.to_address.ilike(f'%{normalized_term}%')
                           )
                           search_conditions.append(condition)

                       if search_conditions:
                           messages_query = messages_query.filter(and_(*search_conditions))
                           messages_query = messages_query.order_by(
                               case(
                                   (EmailMessage.subject.ilike(f'%{search_terms[0]}%'), 3),
                                   (EmailMessage.body.ilike(f'%{search_terms[0]}%'), 2),
                                   else_=1
                               ).desc(),
                               EmailMessage.date.desc()
                           )
                   else:
                       messages_query = messages_query.order_by(EmailMessage.date.desc())

               app_logger.debug(f"SQL Query: {messages_query}")
               total = messages_query.count()
               app_logger.debug(f"Total messages found: {total}")

               current_messages = messages_query.offset((page - 1) * per_page).limit(per_page).all()
               app_logger.debug(f"Retrieved {len(current_messages)} messages for current page")

               messages_dict = {
                   'message_list': [{
                       'id': msg.id,
                       'subject': msg.subject,
                       'body': msg.body,
                       'date': msg.date,
                       'is_sent': msg.is_sent,
                       'from_address': msg.from_address,
                       'to_address': msg.to_address
                   } for msg in current_messages],
                   'total': total,
                   'has_next': (page * per_page) < total,
                   'next_page': page + 1 if (page * per_page) < total else None
               }

               cache.set(cache_key, messages_dict, timeout=600)

       except Exception as e:
           app_logger.error(f"メッセージ取得エラー: {str(e)}")
           app_logger.error(traceback.format_exc())

   return render_template(
       'index.html',
       messages=messages_dict,
       contacts=contacts,
       current_page=page,
       search_query=search_query
   )

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    app_logger.debug('Settings route accessed')
    if request.method == 'POST':
        email = request.form.get('email')
        imap_server = request.form.get('imap_server')
        app_logger.debug(f'Attempting connection to {imap_server} with email {email}')

        try:
            handler = EmailHandler(email, request.form.get('password'), imap_server)
            if handler.test_connection():
                app_logger.debug('Connection test successful')
                session['email'] = email
                session['password'] = request.form.get('password')
                session['imap_server'] = imap_server
                flash("接続に成功しました！", "success")
                return redirect(url_for('index'))
            else:
                app_logger.debug('Connection test failed')
                flash("接続テストに失敗しました。", "error")
        except Exception as e:
            app_logger.error(f'Connection error: {str(e)}', exc_info=True)
            flash(f"接続エラー: {str(e)}", "error")

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

if __name__ == '__main__':
    app.run(debug=True)
