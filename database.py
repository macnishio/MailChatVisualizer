from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from contextlib import contextmanager

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

@contextmanager
def session_scope():
    """トランザクション用のセッションスコープを提供する"""
    session = db.session
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        raise
    finally:
        session.close()