# server.py
# Flask backend for BookBridge.
#
# CHANGELOG (score system):
#   - POINT_RULES + award_points() implement the BookBridge Score System.
#   - Hooked into: account registration, uploading a book, a donation being
#     approved, a request being accepted (borrow +5 / lend +30), request
#     cancellation (repeated -10).
#   - New routes for actions that had no existing flow to hang off of:
#     returning a book (on-time/late/damaged), reviewing another student,
#     verifying a received book's details, inviting a friend, and reading
#     your own score / the leaderboard.

import os
import time
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, session, send_from_directory, redirect
from werkzeug.utils import secure_filename
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from models import db, Student, Book, Request, Transaction, Review, ScoreEvent

GOOGLE_CLIENT_ID = "95544806282-vqlbvh9p0a1tt8rumqkhuatgb9jksd8q.apps.googleusercontent.com"
ALLOWED_DOMAIN = "sece.ac.in"

# Any @sece.ac.in account listed here is auto-promoted to admin the moment
# they sign in. Add your own email (and any teammate emails) before demoing.
# This only ever ADDS admin rights on sign-in — removing an email here does
# not revoke admin from someone already promoted; use the admin panel itself
# (or the DB) for that.
ADMIN_EMAILS = {
    "dharshan.s2025cys@sece.ac.in",
}

# ── BookBridge Score System ──────────────────────────────────────────
# Single source of truth for point values. Keep the frontend's own copy
# (if any) in sync with this, or better, fetch it from /api/scores/rules.
POINT_RULES = {
    "register":            10,
    "upload_book":         20,
    "donate_book":         50,
    "lend_book":            30,
    "return_on_time":       20,
    "borrow_book":           5,
    "give_review":          10,
    "verify_book_details":   5,
    "invite_friend":        15,
    "late_return":         -20,
    "damaged_book":        -50,
    "cancel_request":      -10,
}

LATE_RETURN_GRACE = timedelta(hours=0)  # room to add slack later if you want it

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "webp"}
MAX_IMAGES_PER_BOOK = 3

app = Flask(__name__, static_folder=".", static_url_path="")
app.secret_key = "replace-with-a-real-random-secret-before-you-ship"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(BASE_DIR, "bookbridge.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8MB upload cap

db.init_app(app)
with app.app_context():
    db.create_all()

_google_request = google_requests.Request()


def current_student():
    """Returns the logged-in Student row, or None."""
    sid = session.get("student_id")
    if not sid:
        return None
    return Student.query.get(sid)


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGE_EXT


def admin_required():
    """Returns (student, None) if the current user is an admin,
    or (None, error_response) if not — caller returns error_response directly."""
    student = current_student()
    if not student:
        return None, (jsonify({"error": "Not signed in."}), 401)
    if not student.is_admin:
        return None, (jsonify({"error": "Admin access required."}), 403)
    return student, None


def award_points(student, action, note=None):
    """Applies a POINT_RULES action to a student, logs a ScoreEvent, and
    updates their running total. Does NOT commit — caller should commit
    (usually alongside whatever else it's already saving) so the score
    change is atomic with the action that triggered it."""
    if student is None or action not in POINT_RULES:
        return None
    points = POINT_RULES[action]
    student.score = (student.score or 0) + points
    event = ScoreEvent(student_id=student.id, action=action, points=points, note=note)
    db.session.add(event)
    return event


# ── Static pages ──────────────────────────────────────────────────────

@app.route("/")
def home():
    return send_from_directory(".", "index.html")


@app.route("/dashboard")
def dashboard():
    if not session.get("student_id"):
        return redirect("/")
    return send_from_directory(".", "dashboard.html")


@app.route("/admin")
def admin_page():
    student = current_student()
    if not student:
        return redirect("/")
    if not student.is_admin:
        return redirect("/dashboard")
    return send_from_directory(".", "admin.html")


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


# ── Auth ──────────────────────────────────────────────────────────────

@app.route("/api/auth/google", methods=["POST"])
def auth_google():
    data = request.get_json(silent=True) or {}
    credential = data.get("credential")
    if not credential:
        return jsonify({"error": "Missing credential."}), 400

    try:
        payload = id_token.verify_oauth2_token(
            credential, _google_request, GOOGLE_CLIENT_ID
        )
    except ValueError as e:
        print("TOKEN VERIFY FAILED:", repr(e))  # TEMP: check terminal for the real reason
        return jsonify({"error": "Invalid Google token."}), 401

    if not payload.get("email_verified"):
        return jsonify({"error": "Email is not verified by Google."}), 401

    email = payload.get("email", "")
    email_domain = email.split("@")[-1].lower() if "@" in email else ""
    if email_domain != ALLOWED_DOMAIN:
        return jsonify({
            "error": f"Only @{ALLOWED_DOMAIN} accounts can sign in to BookBridge."
        }), 403

    # This is the real security boundary — everything above must pass
    # before we trust the identity.
    google_id = payload.get("sub")
    student = Student.query.filter_by(google_id=google_id).first()

    if student is None:
        # Also guard against an email match with a different google_id
        # (shouldn't normally happen, but keeps email unique constraint safe).
        student = Student.query.filter_by(email=email).first()

    is_new_student = student is None

    if student is None:
        student = Student(
            google_id=google_id,
            name=payload.get("name") or email.split("@")[0],
            display_name=payload.get("name") or email.split("@")[0],
            email=email,
            picture=payload.get("picture"),
        )
        db.session.add(student)
        db.session.flush()  # get student.id before we log a ScoreEvent against it
    else:
        # Keep verified identity fields fresh; display_name stays as
        # whatever the student customized it to.
        student.name = payload.get("name") or student.name
        student.picture = payload.get("picture") or student.picture
        student.google_id = google_id

    if email.lower() in {e.lower() for e in ADMIN_EMAILS}:
        student.is_admin = True

    if is_new_student:
        award_points(student, "register", note="Account created")

    db.session.commit()

    if student.banned:
        return jsonify({
            "error": "This account has been banned by an administrator. Contact your BookBridge admin if you think this is a mistake."
        }), 403

    session["student_id"] = student.id

    return jsonify({"ok": True})


@app.route("/api/me")
def me():
    student = current_student()
    if not student:
        return jsonify({"error": "Not signed in."}), 401
    return jsonify(student.to_dict())


@app.route("/api/update-profile", methods=["POST"])
def update_profile():
    student = current_student()
    if not student:
        return jsonify({"error": "Not signed in."}), 401

    data = request.get_json(silent=True) or {}

    new_name = data.get("display_name")
    if new_name is not None:
        new_name = new_name.strip()
        if not new_name:
            return jsonify({"error": "Display name can't be empty."}), 400
        if len(new_name) > 60:
            return jsonify({"error": "Display name is too long."}), 400
        student.display_name = new_name

    # Optional profile fields (student profile page, later phase) can
    # already be updated through this same route.
    for field in ("department", "year", "phone"):
        if field in data:
            setattr(student, field, (data[field] or "").strip()[:80])

    db.session.commit()
    return jsonify(student.to_dict())


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


# ── Books ─────────────────────────────────────────────────────────────

@app.route("/api/books", methods=["GET"])
def list_books():
    student = current_student()

    scope = request.args.get("scope", "all")  # all | mine
    query = Book.query.order_by(Book.created_at.desc())

    if scope == "mine":
        if not student:
            return jsonify({"error": "Not signed in."}), 401
        query = query.filter_by(owner_id=student.id)
    else:
        # Donations sit in a pending state until an admin approves them.
        # Owners can still see their own pending donation; admins see everything.
        if not (student and student.is_admin):
            owner_id = student.id if student else -1
            query = query.filter(db.or_(Book.approved.is_(True), Book.owner_id == owner_id))

    q = request.args.get("q", "").strip()
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(Book.title.ilike(like), Book.author.ilike(like), Book.course.ilike(like))
        )

    books = query.all()
    return jsonify([b.to_dict() for b in books])


@app.route("/api/books/<int:book_id>", methods=["GET"])
def get_book(book_id):
    book = Book.query.get(book_id)
    if not book:
        return jsonify({"error": "Book not found."}), 404
    return jsonify(book.to_dict())


@app.route("/api/books", methods=["POST"])
def create_book():
    student = current_student()
    if not student:
        return jsonify({"error": "Not signed in."}), 401

    # Accept both multipart/form-data (with images) and plain JSON
    # (no images) so the frontend can use one code path either way.
    if request.content_type and request.content_type.startswith("multipart/form-data"):
        form = request.form
    else:
        form = request.get_json(silent=True) or {}

    title = (form.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Title is required."}), 400

    listing_type = (form.get("listing_type") or "").strip().lower()
    if listing_type not in ("sell", "donate", "lend", "exchange"):
        return jsonify({"error": "Invalid listing type."}), 400

    book = Book(
        owner_id=student.id,
        title=title,
        author=(form.get("author") or "").strip(),
        course=(form.get("course") or "").strip(),
        department=(form.get("department") or "").strip(),
        semester=(form.get("semester") or "").strip(),
        condition=(form.get("condition") or "").strip(),
        listing_type=listing_type,
        status="available",
        approved=(listing_type != "donate"),
    )

    if listing_type == "sell":
        try:
            book.price = int(form.get("price") or 0)
        except ValueError:
            return jsonify({"error": "Price must be a number."}), 400
        if book.price < 0:
            return jsonify({"error": "Price can't be negative."}), 400
    elif listing_type == "lend":
        book.lend_duration = (form.get("lend_duration") or "Not specified").strip()
    elif listing_type == "exchange":
        book.wants_in_exchange = (form.get("wants_in_exchange") or "Open to offers").strip()

    # Optional images (up to 3), only present on multipart requests.
    saved_filenames = []
    files = request.files.getlist("images") if request.files else []
    for f in files[:MAX_IMAGES_PER_BOOK]:
        if f and f.filename and allowed_image(f.filename):
            safe = secure_filename(f.filename)
            unique = f"{student.id}_{int(time.time()*1000)}_{safe}"
            f.save(os.path.join(UPLOAD_DIR, unique))
            saved_filenames.append(unique)
    if saved_filenames:
        book.images = ",".join(saved_filenames)

    db.session.add(book)

    # Donations earn their (bigger) bonus once approved, not on upload —
    # see admin_approve_book(). Every other listing type earns the flat
    # upload bonus right away.
    if listing_type != "donate":
        award_points(student, "upload_book", note=f'Listed "{title}"')

    db.session.commit()

    return jsonify(book.to_dict()), 201


# ── Requests (Borrow / Buy / Claim / Propose swap all create a Request) ──
# This is the "what happens after I click Borrow" flow: a request goes to
# the owner, who accepts or rejects it. Accepting creates a Transaction and
# marks the book unavailable to further requests.

@app.route("/api/books/<int:book_id>/request", methods=["POST"])
def request_book(book_id):
    student = current_student()
    if not student:
        return jsonify({"error": "Not signed in."}), 401

    book = Book.query.get(book_id)
    if not book:
        return jsonify({"error": "Book not found."}), 404
    if not book.approved:
        return jsonify({"error": "This listing isn't live yet."}), 400
    if book.owner_id == student.id:
        return jsonify({"error": "You can't request your own listing."}), 400
    if book.status != "available":
        return jsonify({"error": "This book is no longer available."}), 400

    existing = Request.query.filter_by(
        book_id=book.id, requester_id=student.id, status="pending"
    ).first()
    if existing:
        return jsonify({"error": "You've already requested this book."}), 400

    req = Request(book_id=book.id, requester_id=student.id, status="pending")
    book.status = "requested"
    db.session.add(req)
    db.session.commit()

    return jsonify(req.to_dict()), 201


@app.route("/api/requests/incoming", methods=["GET"])
def requests_incoming():
    """Requests waiting on books the current student owns."""
    student = current_student()
    if not student:
        return jsonify({"error": "Not signed in."}), 401

    reqs = (
        Request.query.join(Book, Request.book_id == Book.id)
        .filter(Book.owner_id == student.id)
        .order_by(Request.created_at.desc())
        .all()
    )
    return jsonify([r.to_dict() for r in reqs])


@app.route("/api/requests/outgoing", methods=["GET"])
def requests_outgoing():
    """Requests the current student has made on other students' books."""
    student = current_student()
    if not student:
        return jsonify({"error": "Not signed in."}), 401

    query = Request.query.filter_by(requester_id=student.id)
    status_filter = request.args.get("status")
    if status_filter:
        query = query.filter_by(status=status_filter)
    reqs = query.order_by(Request.created_at.desc()).all()
    return jsonify([r.to_dict() for r in reqs])


@app.route("/api/requests/<int:request_id>/accept", methods=["POST"])
def accept_request(request_id):
    student = current_student()
    if not student:
        return jsonify({"error": "Not signed in."}), 401

    req = Request.query.get(request_id)
    if not req:
        return jsonify({"error": "Request not found."}), 404
    book = req.book
    if not book or book.owner_id != student.id:
        return jsonify({"error": "You don't own this listing."}), 403
    if req.status != "pending":
        return jsonify({"error": "This request has already been handled."}), 400

    req.status = "accepted"
    book.status = "unavailable"

    due_date = None
    if book.listing_type == "lend":
        due_date = datetime.utcnow() + timedelta(days=14)  # default 2-week loan

    txn = Transaction(
        book_id=book.id,
        borrower_id=req.requester_id,
        owner_id=student.id,
        due_date=due_date,
    )
    db.session.add(txn)

    # Score: the borrower always earns the flat "borrow" points; the owner
    # additionally earns the bigger "lend" bonus specifically for lend-type
    # listings (selling/donating/exchanging aren't "lending").
    borrower = Student.query.get(req.requester_id)
    award_points(borrower, "borrow_book", note=f'Borrowed "{book.title}"')
    if book.listing_type == "lend":
        award_points(student, "lend_book", note=f'Lent out "{book.title}"')

    # Any other pending requests on the same book are now moot.
    others = Request.query.filter(
        Request.book_id == book.id, Request.id != req.id, Request.status == "pending"
    ).all()
    for other in others:
        other.status = "rejected"

    db.session.commit()
    return jsonify(req.to_dict())


@app.route("/api/requests/<int:request_id>/reject", methods=["POST"])
def reject_request(request_id):
    student = current_student()
    if not student:
        return jsonify({"error": "Not signed in."}), 401

    req = Request.query.get(request_id)
    if not req:
        return jsonify({"error": "Request not found."}), 404
    book = req.book
    if not book or book.owner_id != student.id:
        return jsonify({"error": "You don't own this listing."}), 403
    if req.status != "pending":
        return jsonify({"error": "This request has already been handled."}), 400

    req.status = "rejected"

    # If no other pending requests remain, the book goes back on the shelf.
    remaining = Request.query.filter_by(book_id=book.id, status="pending").count()
    if remaining == 0 and book.status == "requested":
        book.status = "available"

    db.session.commit()
    return jsonify(req.to_dict())


@app.route("/api/requests/<int:request_id>/cancel", methods=["POST"])
def cancel_request(request_id):
    """A requester backing out of their own pending request. The first
    cancellation is free; from the 2nd one onward it's treated as
    'repeatedly' cancelling and costs points, per the score rules."""
    student = current_student()
    if not student:
        return jsonify({"error": "Not signed in."}), 401

    req = Request.query.get(request_id)
    if not req:
        return jsonify({"error": "Request not found."}), 404
    if req.requester_id != student.id:
        return jsonify({"error": "This isn't your request."}), 403
    if req.status != "pending":
        return jsonify({"error": "This request has already been handled."}), 400

    req.status = "cancelled"
    book = req.book
    if book:
        remaining = Request.query.filter(
            Request.book_id == book.id, Request.id != req.id, Request.status == "pending"
        ).count()
        if remaining == 0 and book.status == "requested":
            book.status = "available"

    student.cancel_count = (student.cancel_count or 0) + 1
    if student.cancel_count > 1:
        award_points(student, "cancel_request", note=f"Cancelled request #{req.id}")

    db.session.commit()
    return jsonify(req.to_dict())


# ── Transactions (return / verify) ──────────────────────────────────────

@app.route("/api/transactions/<int:txn_id>/return", methods=["POST"])
def return_transaction(txn_id):
    """Marks a lent book as returned. Awards the borrower on-time points,
    or deducts late/damaged points, based on due_date and the optional
    `damaged` flag. Either the owner or the borrower can record a return
    (in practice this'll usually be the owner confirming they got it back)."""
    student = current_student()
    if not student:
        return jsonify({"error": "Not signed in."}), 401

    txn = Transaction.query.get(txn_id)
    if not txn:
        return jsonify({"error": "Transaction not found."}), 404
    if student.id not in (txn.owner_id, txn.borrower_id):
        return jsonify({"error": "You're not part of this transaction."}), 403
    if txn.returned:
        return jsonify({"error": "This has already been marked returned."}), 400

    data = request.get_json(silent=True) or {}
    damaged = bool(data.get("damaged", False))

    txn.returned = True
    txn.returned_at = datetime.utcnow()
    txn.damaged = damaged

    borrower = Student.query.get(txn.borrower_id)

    if damaged:
        award_points(borrower, "damaged_book", note=f"Transaction #{txn.id} returned damaged")
    elif txn.due_date and txn.returned_at > txn.due_date + LATE_RETURN_GRACE:
        award_points(borrower, "late_return", note=f"Transaction #{txn.id} returned late")
    else:
        award_points(borrower, "return_on_time", note=f"Transaction #{txn.id} returned on time")

    book = txn.book
    if book and book.status == "unavailable":
        book.status = "available"

    db.session.commit()
    return jsonify(txn.to_dict())


@app.route("/api/transactions/<int:txn_id>/verify", methods=["POST"])
def verify_transaction(txn_id):
    """Borrower confirms the book they received matched its listed
    condition/details. Small trust-building bonus."""
    student = current_student()
    if not student:
        return jsonify({"error": "Not signed in."}), 401

    txn = Transaction.query.get(txn_id)
    if not txn:
        return jsonify({"error": "Transaction not found."}), 404
    if txn.borrower_id != student.id:
        return jsonify({"error": "Only the borrower can verify this transaction."}), 403
    if txn.verified:
        return jsonify({"error": "Already verified."}), 400

    txn.verified = True
    award_points(student, "verify_book_details", note=f"Verified transaction #{txn.id}")
    db.session.commit()
    return jsonify(txn.to_dict())


# ── Reviews ──────────────────────────────────────────────────────────────

@app.route("/api/reviews", methods=["POST"])
def create_review():
    student = current_student()
    if not student:
        return jsonify({"error": "Not signed in."}), 401

    data = request.get_json(silent=True) or {}
    target_id = data.get("student_id")
    rating = data.get("rating")
    comment = (data.get("comment") or "").strip()
    transaction_id = data.get("transaction_id")

    target = Student.query.get(target_id) if target_id else None
    if not target:
        return jsonify({"error": "That student doesn't exist."}), 404
    if target.id == student.id:
        return jsonify({"error": "You can't review yourself."}), 400
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        return jsonify({"error": "Rating must be a number from 1-5."}), 400
    if rating < 1 or rating > 5:
        return jsonify({"error": "Rating must be between 1 and 5."}), 400

    if transaction_id:
        txn = Transaction.query.get(transaction_id)
        if not txn or student.id not in (txn.owner_id, txn.borrower_id):
            return jsonify({"error": "That transaction doesn't involve you."}), 403
        existing = Review.query.filter_by(transaction_id=transaction_id, reviewer_id=student.id).first()
        if existing:
            return jsonify({"error": "You've already reviewed this transaction."}), 400

    review = Review(
        student_id=target.id,
        reviewer_id=student.id,
        transaction_id=transaction_id,
        rating=rating,
        comment=comment,
    )
    db.session.add(review)

    # Keep the target's average rating fresh.
    all_ratings = [r.rating for r in Review.query.filter_by(student_id=target.id).all() if r.rating]
    all_ratings.append(rating)
    target.rating = round(sum(all_ratings) / len(all_ratings), 2)

    award_points(student, "give_review", note=f"Reviewed {target.display_name}")
    db.session.commit()
    return jsonify(review.to_dict()), 201


# ── Invite a friend (stub) ────────────────────────────────────────────
# NOTE: there's no invite-tracking infrastructure yet (no invite codes,
# no verification the invitee actually joins). This awards points as soon
# as the current student says they've invited someone, which is
# honor-system only — replace with real verification (e.g. award points
# when the invitee's account is created and references an inviter id)
# before relying on this for anything that matters.
@app.route("/api/invite", methods=["POST"])
def invite_friend():
    student = current_student()
    if not student:
        return jsonify({"error": "Not signed in."}), 401

    data = request.get_json(silent=True) or {}
    invitee_email = (data.get("email") or "").strip()
    if not invitee_email:
        return jsonify({"error": "Enter the email you invited."}), 400

    award_points(student, "invite_friend", note=f"Invited {invitee_email}")
    db.session.commit()
    return jsonify({"ok": True, "score": student.score})


# ── Scores ─────────────────────────────────────────────────────────────

@app.route("/api/scores/rules", methods=["GET"])
def score_rules():
    return jsonify(POINT_RULES)


@app.route("/api/scores/me", methods=["GET"])
def score_me():
    student = current_student()
    if not student:
        return jsonify({"error": "Not signed in."}), 401

    events = (
        ScoreEvent.query.filter_by(student_id=student.id)
        .order_by(ScoreEvent.created_at.desc())
        .limit(50)
        .all()
    )
    rank = Student.query.filter(Student.score > student.score).count() + 1

    return jsonify({
        "score": student.score,
        "badge": student.badge_name(),
        "rank": rank,
        "history": [e.to_dict() for e in events],
    })


@app.route("/api/scores/leaderboard", methods=["GET"])
def score_leaderboard():
    limit = min(int(request.args.get("limit", 20)), 100)
    top = (
        Student.query.filter_by(banned=False)
        .order_by(Student.score.desc())
        .limit(limit)
        .all()
    )
    return jsonify([
        {
            "id": s.id,
            "display_name": s.display_name,
            "picture": s.picture,
            "score": s.score,
            "badge": s.badge_name(),
            "verified": s.verified,
        }
        for s in top
    ])


# ── Admin ─────────────────────────────────────────────────────────────
# All routes below require the signed-in student to have is_admin = True.
# Nothing here is reachable by a normal student account.

@app.route("/api/admin/students", methods=["GET"])
def admin_list_students():
    _, err = admin_required()
    if err:
        return err
    students = Student.query.order_by(Student.created_at.desc()).all()
    return jsonify([s.to_dict() for s in students])


@app.route("/api/admin/students/<int:student_id>/ban", methods=["POST"])
def admin_ban_student(student_id):
    admin, err = admin_required()
    if err:
        return err
    target = Student.query.get(student_id)
    if not target:
        return jsonify({"error": "Student not found."}), 404
    if target.id == admin.id:
        return jsonify({"error": "You can't ban your own account."}), 400

    data = request.get_json(silent=True) or {}
    target.banned = bool(data.get("banned", True))
    db.session.commit()
    return jsonify(target.to_dict())


@app.route("/api/admin/students/<int:student_id>/verify", methods=["POST"])
def admin_verify_student(student_id):
    _, err = admin_required()
    if err:
        return err
    target = Student.query.get(student_id)
    if not target:
        return jsonify({"error": "Student not found."}), 404

    data = request.get_json(silent=True) or {}
    target.verified = bool(data.get("verified", True))
    db.session.commit()
    return jsonify(target.to_dict())


@app.route("/api/admin/books", methods=["GET"])
def admin_list_books():
    _, err = admin_required()
    if err:
        return err

    query = Book.query.order_by(Book.created_at.desc())
    status_filter = request.args.get("filter")  # "pending" | None
    if status_filter == "pending":
        query = query.filter_by(approved=False)
    return jsonify([b.to_dict() for b in query.all()])


@app.route("/api/admin/books/<int:book_id>/approve", methods=["POST"])
def admin_approve_book(book_id):
    _, err = admin_required()
    if err:
        return err
    book = Book.query.get(book_id)
    if not book:
        return jsonify({"error": "Book not found."}), 404

    data = request.get_json(silent=True) or {}
    newly_approved = bool(data.get("approved", True))
    book.approved = newly_approved

    # Donation bonus fires the first time a donation listing goes live,
    # not on every approve/hide toggle.
    if newly_approved and book.listing_type == "donate" and not book.donate_bonus_awarded:
        owner = Student.query.get(book.owner_id)
        award_points(owner, "donate_book", note=f'Donation approved: "{book.title}"')
        book.donate_bonus_awarded = True

    db.session.commit()
    return jsonify(book.to_dict())


@app.route("/api/admin/books/<int:book_id>", methods=["DELETE"])
def admin_delete_book(book_id):
    _, err = admin_required()
    if err:
        return err
    book = Book.query.get(book_id)
    if not book:
        return jsonify({"error": "Book not found."}), 404

    # Best-effort cleanup of any uploaded images on disk.
    if book.images:
        for filename in book.images.split(","):
            path = os.path.join(UPLOAD_DIR, filename)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    db.session.delete(book)
    db.session.commit()
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=True)