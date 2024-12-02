from database import db
from datetime import datetime
from sqlalchemy.ext.hybrid import hybrid_property
import re

class Contact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    display_name = db.Column(db.String(255))
    normalized_email = db.Column(db.String(255), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    sent_messages = db.relationship('EmailMessage', backref='sender', foreign_keys='EmailMessage.from_contact_id')
    received_messages = db.relationship('EmailMessage', backref='recipient', foreign_keys='EmailMessage.to_contact_id')

    def __init__(self, email, display_name=None):
        self.email = email
        self.display_name = display_name or email
        self.normalized_email = self.normalize_email(email)

    @staticmethod
    def normalize_email(email):
        """メールアドレスを正規化する"""
        if not email:
            return None
        return email.lower().strip()

class EmailSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    imap_server = db.Column(db.String(120), nullable=False)
    last_sync = db.Column(db.DateTime)

class EmailMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.String(255), unique=True)
    from_contact_id = db.Column(db.Integer, db.ForeignKey('contact.id'), nullable=True)
    to_contact_id = db.Column(db.Integer, db.ForeignKey('contact.id'), nullable=True)
    from_address = db.Column(db.String(255), index=True)  # 後方互換性のために保持
    to_address = db.Column(db.String(255), index=True)    # 後方互換性のために保持
    subject = db.Column(db.Text)
    body = db.Column(db.Text)
    date = db.Column(db.DateTime, index=True)
    is_sent = db.Column(db.Boolean, default=False)
    folder = db.Column(db.String(100))
    last_sync = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_email_content', 'subject', 'body'),
        db.Index('ix_email_message_from_contact', 'from_contact_id'),
        db.Index('ix_email_message_to_contact', 'to_contact_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'message_id': self.message_id,
            'from_address': self.from_address,
            'to_address': self.to_address,
            'subject': self.subject,
            'body': self.body,
            'date': self.date,
            'is_sent': self.is_sent,
            'folder': self.folder
        }