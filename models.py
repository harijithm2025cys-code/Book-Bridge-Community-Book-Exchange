# models.py
# SQLite models for BookBridge, via flask-sqlalchemy.
#
# CHANGELOG (score system):
#   - Student gains `score` (running total) and `cancel_count` (for the
#     "repeated cancellation" penalty).
#   - New ScoreEvent model logs every point-earning/losing action, so the
#     score is always reconstructable/auditable and the frontend can show
#     a history feed, not just a total.
#   - Transaction gains `due_date` handling support (`damaged`, `verified`)
#     used by the new /api/transactions/<id>/return and /verify routes.

from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Student(db.Model):
    __tablename__ = "students"

    id = db.Column(db.Integer, primary_key=True)          # StudentID
    google_id = db.Column(db.String(64), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)        # verified Google name
    display_name = db.Column(db.String(60), nullable=False) # editable, defaults to name
    email = db.Column(db.String(120), unique=True, nullable=False)
    picture = db.Column(db.String(300))
    department = db.Column(db.String(80))
    year = db.Column(db.String(20))
    phone = db.Column(db.String(20))
    rating = db.Column(db.Float, default=0.0)
    is_admin = db.Column(db.Boolean, default=False)
    verified = db.Column(db.Boolean, default=False)   # admin-verified, separate from email-domain check
    banned = db.Column(db.Boolean, default=False)

    # ── Score system ──
    score = db.Column(db.Integer, default=0, nullable=False)
    cancel_count = db.Column(db.Integer, default=0, nullable=False)  # total requests they've cancelled

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    books = db.relationship("Book", backref="owner", lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "email": self.email,
            "picture": self.picture,
            "department": self.department,
            "year": self.year,
            "phone": self.phone,
            "rating": self.rating,
            "is_admin": self.is_admin,
            "verified": self.verified,
            "banned": self.banned,
            "score": self.score,
            "badge": self.badge_name(),
        }

    def badge_name(self):
        """Simple score-tier badges, computed on the fly (not stored) so
        thresholds can be tuned without a migration."""
        if self.score >= 600:
            return "Platinum"
        if self.score >= 300:
            return "Gold"
        if self.score >= 150:
            return "Silver"
        if self.score >= 50:
            return "Bronze"
        return None


class Book(db.Model):
    __tablename__ = "books"

    id = db.Column(db.Integer, primary_key=True)             # BookID
    owner_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)

    title = db.Column(db.String(200), nullable=False)
    author = db.Column(db.String(150))
    course = db.Column(db.String(40))
    department = db.Column(db.String(80))
    semester = db.Column(db.String(20))
    condition = db.Column(db.String(20))   # New / Good / Fair / Worn

    listing_type = db.Column(db.String(20), nullable=False)  # sell/donate/lend/exchange (Mode)
    price = db.Column(db.Integer)                    # sell only
    lend_duration = db.Column(db.String(80))         # lend only
    wants_in_exchange = db.Column(db.String(200))    # exchange only

    status = db.Column(db.String(20), default="available")  # available/requested/unavailable
    images = db.Column(db.Text)  # comma-separated filenames under /uploads
    approved = db.Column(db.Boolean, default=True)  # donations start False, need admin approval

    # Set once, the first time a donation is approved, so re-toggling
    # approval on/off can't be used to farm the +50 donate bonus repeatedly.
    donate_bonus_awarded = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "owner": self.owner.display_name if self.owner else None,
            "owner_email": self.owner.email if self.owner else None,
            "owner_verified": self.owner.verified if self.owner else False,
            "title": self.title,
            "author": self.author,
            "course": self.course,
            "department": self.department,
            "semester": self.semester,
            "condition": self.condition,
            "listing_type": self.listing_type,
            "price": self.price,
            "lend_duration": self.lend_duration,
            "wants_in_exchange": self.wants_in_exchange,
            "status": self.status,
            "images": self.images.split(",") if self.images else [],
            "approved": self.approved,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Request(db.Model):
    __tablename__ = "requests"

    id = db.Column(db.Integer, primary_key=True)   # RequestID
    book_id = db.Column(db.Integer, db.ForeignKey("books.id"), nullable=False)
    requester_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    status = db.Column(db.String(20), default="pending")  # pending/accepted/rejected/cancelled
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    book = db.relationship("Book")
    requester = db.relationship("Student")

    def to_dict(self):
        return {
            "id": self.id,
            "book_id": self.book_id,
            "book_title": self.book.title if self.book else None,
            "book_listing_type": self.book.listing_type if self.book else None,
            "owner_id": self.book.owner_id if self.book else None,
            "requester_id": self.requester_id,
            "requester_name": self.requester.display_name if self.requester else None,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Transaction(db.Model):
    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)   # TransactionID
    book_id = db.Column(db.Integer, db.ForeignKey("books.id"), nullable=False)
    borrower_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    due_date = db.Column(db.DateTime)
    returned = db.Column(db.Boolean, default=False)
    returned_at = db.Column(db.DateTime)
    damaged = db.Column(db.Boolean, default=False)
    verified = db.Column(db.Boolean, default=False)  # borrower confirmed book matched description
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    book = db.relationship("Book")
    borrower = db.relationship("Student", foreign_keys=[borrower_id])
    owner = db.relationship("Student", foreign_keys=[owner_id])

    def to_dict(self):
        return {
            "id": self.id,
            "book_id": self.book_id,
            "book_title": self.book.title if self.book else None,
            "borrower_id": self.borrower_id,
            "owner_id": self.owner_id,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "returned": self.returned,
            "returned_at": self.returned_at.isoformat() if self.returned_at else None,
            "damaged": self.damaged,
            "verified": self.verified,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Review(db.Model):
    __tablename__ = "reviews"

    id = db.Column(db.Integer, primary_key=True)   # ReviewID
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)  # who is being reviewed
    reviewer_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)  # who wrote it
    transaction_id = db.Column(db.Integer, db.ForeignKey("transactions.id"))
    rating = db.Column(db.Integer)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "student_id": self.student_id,
            "reviewer_id": self.reviewer_id,
            "transaction_id": self.transaction_id,
            "rating": self.rating,
            "comment": self.comment,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ScoreEvent(db.Model):
    """Audit log of every point award/deduction. The Student.score column
    is a running total kept in sync with these rows so we never need to
    SUM() the whole table on a hot path, but this table is the source of
    truth for 'why does this student have this score' and for a history
    feed in the UI."""
    __tablename__ = "score_events"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("students.id"), nullable=False)
    action = db.Column(db.String(40), nullable=False)   # key into POINT_RULES
    points = db.Column(db.Integer, nullable=False)       # signed
    note = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    student = db.relationship("Student")

    def to_dict(self):
        return {
            "id": self.id,
            "action": self.action,
            "points": self.points,
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }