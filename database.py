from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from contextlib import contextmanager

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

@contextmanager
def session_scope():
    try:
        yield db.session
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        raise
    finally:
        db.session.close()
