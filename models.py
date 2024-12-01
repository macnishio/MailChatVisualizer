from database import db
from datetime import datetime

class EmailSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    imap_server = db.Column(db.String(120), nullable=False)
    last_sync = db.Column(db.DateTime)

class EmailMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.String(255), unique=True)
    from_address = db.Column(db.String(255), index=True)
    to_address = db.Column(db.String(255), index=True)
    subject = db.Column(db.Text)
    body = db.Column(db.Text)
    date = db.Column(db.DateTime, index=True)
    is_sent = db.Column(db.Boolean, default=False)
    folder = db.Column(db.String(100))
    last_sync = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_email_search', 'from_address', 'to_address'),
        db.Index('idx_email_content', 'subject', 'body'),
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
