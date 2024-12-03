from database import db
from datetime import datetime
from sqlalchemy.ext.hybrid import hybrid_property
import re

class Contact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(255))
    normalized_email = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    sent_messages = db.relationship('EmailMessage', backref='sender', foreign_keys='EmailMessage.from_contact_id')
    received_messages = db.relationship('EmailMessage', backref='recipient', foreign_keys='EmailMessage.to_contact_id')

    __table_args__ = (
        db.UniqueConstraint('normalized_email', name='uq_contact_normalized_email'),
        db.Index('idx_contact_email', 'email'),
        db.Index('idx_contact_normalized_email', 'normalized_email'),
        db.Index('idx_contact_display_name', 'display_name'),
    )

    def __init__(self, email, display_name=None):
        from utils.email_normalizer import normalize_email
        normalized_result = normalize_email(email)
        self.normalized_email = normalized_result[0]  # 正規化されたメールアドレス
        self.display_name = display_name or normalized_result[1] or email  # 表示名
        self.email = normalized_result[2] or email  # 元のメールアドレス

    @classmethod
    def find_or_create(cls, email, display_name=None):
        """
        正規化されたメールアドレスに基づいて連絡先を検索または作成する
        重複を防ぎながら、既存のレコードがある場合は更新する
        """
        from utils.email_normalizer import normalize_email
        normalized_result = normalize_email(email)
        if not normalized_result[0]:  # 正規化されたメールアドレスがない場合
            return None

        contact = cls.query.filter_by(normalized_email=normalized_result[0]).first()
        if contact:
            # 既存のレコードがある場合、必要に応じて表示名を更新
            if display_name and display_name != contact.display_name:
                contact.display_name = display_name
                contact.updated_at = datetime.utcnow()
                db.session.commit()
        else:
            # 新しいレコードを作成
            contact = cls(email=email, display_name=display_name)
            db.session.add(contact)
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                raise e

        return contact

    @classmethod
    def merge_contacts(cls, source_id, target_id):
        """
        2つの連絡先を統合する
        source_idの連絡先をtarget_idの連絡先に統合し、関連するメールメッセージも更新する
        """
        if source_id == target_id:
            return False

        try:
            source = cls.query.get(source_id)
            target = cls.query.get(target_id)

            if not source or not target:
                return False

            # メールメッセージの関連を更新
            EmailMessage.query.filter_by(from_contact_id=source_id).update({'from_contact_id': target_id})
            EmailMessage.query.filter_by(to_contact_id=source_id).update({'to_contact_id': target_id})

            # source連絡先を削除
            db.session.delete(source)
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            raise e

class EmailSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    imap_server = db.Column(db.String(120), nullable=False)
    last_sync = db.Column(db.DateTime)

class EmailMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.String(255), unique=True)
    from_contact_id = db.Column(db.Integer, db.ForeignKey('contact.id', ondelete='SET NULL'), nullable=True)
    to_contact_id = db.Column(db.Integer, db.ForeignKey('contact.id', ondelete='SET NULL'), nullable=True)
    from_address = db.Column(db.String(255))  # 後方互換性のために保持
    to_address = db.Column(db.String(255))    # 後方互換性のために保持
    subject = db.Column(db.Text)
    body = db.Column(db.Text)
    date = db.Column(db.DateTime, index=True)
    is_sent = db.Column(db.Boolean, default=False)
    folder = db.Column(db.String(100))
    last_sync = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_email_message_content', 'subject', 'body'),
        db.Index('idx_email_message_from_contact', 'from_contact_id'),
        db.Index('idx_email_message_to_contact', 'to_contact_id'),
        db.Index('idx_email_message_addresses', 'from_address', 'to_address'),
        db.Index('idx_email_message_date_folder', 'date', 'folder'),
    )

    def update_contacts(self):
        """
        メッセージの送信者と受信者のContactレコードを更新または作成する
        """
        if self.from_address:
            from_contact = Contact.find_or_create(self.from_address)
            if from_contact:
                self.from_contact_id = from_contact.id
                self.sender = from_contact

        if self.to_address:
            to_contact = Contact.find_or_create(self.to_address)
            if to_contact:
                self.to_contact_id = to_contact.id
                self.recipient = to_contact

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