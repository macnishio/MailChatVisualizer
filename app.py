import os
from flask import Flask, render_template, request, session, redirect, url_for, flash, jsonify
from email_handler import EmailHandler
from werkzeug.security import generate_password_hash, check_password_hash
from database import db
from models import EmailMessage, EmailSettings
from celery_worker import sync_emails
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or "your-secret-key"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///email_chat.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

@app.route('/')
def index():
    if 'email' not in session:
        return redirect(url_for('settings'))
    
    page = request.args.get('page', 1, type=int)
    per_page = 20
    contact_search = request.args.get('contact_search', '')
    
    # Get unique contacts from cached messages
    contacts_query = db.session.query(EmailMessage.from_address).distinct()
    if contact_search:
        contacts_query = contacts_query.filter(EmailMessage.from_address.ilike(f'%{contact_search}%'))
    contacts = [contact[0] for contact in contacts_query.all() if contact[0]]

    messages = []
    selected_contact = request.args.get('contact')
    search_query = request.args.get('search')
    
    if selected_contact:
        # Query cached messages
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
        
        messages = messages_query.paginate(page=page, per_page=per_page, error_out=False)
        
        # Trigger async sync if needed
        last_sync = EmailMessage.query.order_by(EmailMessage.last_sync.desc()).first()
        if not last_sync or (datetime.utcnow() - last_sync.last_sync).seconds > 300:
            sync_emails.delay(session['email'], session['password'], session['imap_server'])
    
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
    query = request.args.get('q', '')
    if len(query) >= 2:
        contacts = db.session.query(EmailMessage.from_address).distinct()\
            .filter(EmailMessage.from_address.ilike(f'%{query}%'))\
            .limit(10).all()
        return jsonify([contact[0] for contact in contacts if contact[0]])
    return jsonify([])

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('settings'))

with app.app_context():
    db.create_all()
