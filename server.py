# server.py
# Flask backend for BookBridge.
# Now backed by a real SQLite database (via flask-sqlalchemy) instead of
# the Flask session / in-memory JS array. See models.py for the schema.
#
# Setup:
#   pip install flask flask-sqlalchemy google-auth requests
#   python server.py
#
# First run creates bookbridge.db (SQLite file) and an /uploads folder
# in this same directory automatically.

import os
import time
from flask import Flask, request, jsonify, session, send_from_directory, redirect
from werkzeug.utils import secure_filename
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

from models import db, Student, Book, Request, Transaction

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

    if student is None:
        student = Student(
            google_id=google_id,
            name=payload.get("name") or email.split("@")[0],
            display_name=payload.get("name") or email.split("@")[0],
            email=email,
            picture=payload.get("picture"),
        )
        db.session.add(student)
    else:
        # Keep verified identity fields fresh; display_name stays as
        # whatever the student customized it to.
        student.name = payload.get("name") or student.name
        student.picture = payload.get("picture") or student.picture
        student.google_id = google_id

    if email.lower() in {e.lower() for e in ADMIN_EMAILS}:
        student.is_admin = True

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

    txn = Transaction(book_id=book.id, borrower_id=req.requester_id, owner_id=student.id)
    db.session.add(txn)

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
    book.approved = bool(data.get("approved", True))
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