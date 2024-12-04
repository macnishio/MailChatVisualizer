from database import db
from datetime import datetime
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy import text

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
        try:
            normalized_result = normalize_email(email)
            if not normalized_result[0]:  # 正規化されたメールアドレスが空の場合
                raise ValueError("Invalid email address")
                
            self.normalized_email = normalized_result[0]  # 正規化されたメールアドレス
            self.display_name = display_name or normalized_result[1] or email  # 表示名
            self.email = normalized_result[2] or email  # 元のメールアドレス
        except Exception as e:
            raise ValueError(f"Email normalization failed: {str(e)}")

    @classmethod
    def find_or_create(cls, email, display_name=None, session=None):
        """
        正規化されたメールアドレスに基づいて連絡先を検索または作成する

        Args:
            email (str): メールアドレス
            display_name (str, optional): 表示名
            session (Session, optional): 既存のSQLAlchemyセッション

        Returns:
            Contact: 作成または更新された連絡先
        """
        from utils.email_normalizer import normalize_email
        from sqlalchemy import exc

        try:
            normalized_result = normalize_email(email)
            if not normalized_result[0]:
                raise ValueError("Invalid email address")

            normalized_email = normalized_result[0]

            # 既存セッションを使用するか新しいセッションを作成
            if session is None:
                with session_scope() as new_session:
                    return cls._do_find_or_create(
                        normalized_email, 
                        email, 
                        display_name, 
                        new_session
                    )
            else:
                return cls._do_find_or_create(
                    normalized_email, 
                    email, 
                    display_name, 
                    session
                )

        except Exception as e:
            raise ValueError(f"Failed to process contact: {str(e)}")

    @classmethod
    def _do_find_or_create(cls, normalized_email, original_email, display_name, session):
        """実際の検索/作成処理を行う内部メソッド"""
        try:
            # 既存の連絡先を検索
            contact = session.query(cls)\
                .filter_by(normalized_email=normalized_email)\
                .with_for_update()\
                .first()

            if contact:
                # 既存の連絡先を更新
                if display_name and display_name != contact.display_name:
                    contact.display_name = display_name
                    contact.updated_at = datetime.utcnow()
            else:
                # 新しい連絡先を作成
                contact = cls(email=original_email, display_name=display_name)
                session.add(contact)

            return contact

        except exc.IntegrityError:
            session.rollback()
            # 一意性制約違反の場合、再度検索を試みる
            return session.query(cls)\
                .filter_by(normalized_email=normalized_email)\
                .first()

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
    last_sync_status = db.Column(db.String(50))  # SUCCESS, ERROR など
    sync_error = db.Column(db.Text)
    is_syncing = db.Column(db.Boolean, default=False)

class EmailMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.String(255), unique=True)
    from_contact_id = db.Column(db.Integer, db.ForeignKey('contact.id', ondelete='SET NULL'), nullable=True)
    to_contact_id = db.Column(db.Integer, db.ForeignKey('contact.id', ondelete='SET NULL'), nullable=True)
    from_address = db.Column(db.String(255))
    to_address = db.Column(db.String(255))
    subject = db.Column(db.Text)
    body = db.Column(db.Text)
    body_tsv = db.Column(TSVECTOR)
    body_hash = db.Column(db.String(32))  # MD5ハッシュ用
    body_preview = db.Column(db.String(1000))  # プレビュー用
    date = db.Column(db.DateTime, index=True)
    is_sent = db.Column(db.Boolean, default=False)
    folder = db.Column(db.String(100))
    last_sync = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_email_message_content_hash', 'body_hash'),
        db.Index('idx_email_message_fulltext', 'body_tsv', postgresql_using='gin'),
        db.Index('idx_email_message_from_contact', 'from_contact_id'),
        db.Index('idx_email_message_to_contact', 'to_contact_id'),
        db.Index('idx_email_message_addresses', 'from_address', 'to_address'),
        db.Index('idx_email_message_date_folder', 'date', 'folder'),
    )

    @staticmethod
    def create_body_hash(body):
        """本文のMD5ハッシュを生成"""
        if not body:
            return None
        return hashlib.md5(body.encode()).hexdigest()

    @staticmethod
    def create_body_preview(body, length=1000):
        """本文のプレビューを生成（HTMLタグを除去）"""
        if not body:
            return None
        # HTMLタグを除去してプレインテキスト化
        text = re.sub(r'<[^>]+>', '', body)
        # 連続する空白を1つに置換
        text = re.sub(r'\s+', ' ', text)
        return text[:length].strip()

    def update_body_fields(self):
        """本文関連のフィールドを更新"""
        if self.body:
            self.body_hash = self.create_body_hash(self.body)
            self.body_preview = self.create_body_preview(self.body)

    # EmailMessageモデルに追加するメソッド
    def update_contacts(self, session=None):
        """メッセージの送信者と受信者のContactレコードを更新または作成する"""
        try:
            # セッションが渡されない場合は新しいセッションを作成
            if session is None:
                with session_scope() as new_session:
                    return self._do_update_contacts(new_session)
            else:
                # 既存のセッションを使用
                return self._do_update_contacts(session)
        except Exception as e:
            app_logger.error(f"Failed to update contacts: {str(e)}")
            raise ValueError(f"Failed to update contacts: {str(e)}")

    def _do_update_contacts(self, session):
        """実際の連絡先更新処理を行う内部メソッド"""
        if self.from_address:
            from_contact = Contact.find_or_create(self.from_address, session=session)
            if from_contact:
                self.from_contact_id = from_contact.id

        if self.to_address:
            to_contact = Contact.find_or_create(self.to_address, session=session)
            if to_contact:
                self.to_contact_id = to_contact.id

    def to_dict(self):
        return {
            'id': self.id,
            'message_id': self.message_id,
            'from_address': self.from_address,
            'to_address': self.to_address,
            'subject': self.subject,
            'body': self.body,
            'body_preview': self.body_preview,
            'date': self.date,
            'is_sent': self.is_sent,
            'folder': self.folder
        }

    @classmethod
    def search_full_text(cls, query_text):
        """全文検索を実行するクラスメソッド"""
        return cls.query.filter(
            cls.body_tsv.match(
                db.func.plainto_tsquery('english', query_text)
            )
        )
