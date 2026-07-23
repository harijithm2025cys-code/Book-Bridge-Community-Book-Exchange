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

from models import db, Student, Book

GOOGLE_CLIENT_ID = "95544806282-vqlbvh9p0a1tt8rumqkhuatgb9jksd8q.apps.googleusercontent.com"
ALLOWED_DOMAIN = "sece.ac.in"

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


# ── Static pages ──────────────────────────────────────────────────────

@app.route("/")
def home():
    return send_from_directory(".", "index.html")


@app.route("/dashboard")
def dashboard():
    if not session.get("student_id"):
        return redirect("/")
    return send_from_directory(".", "dashboard.html")


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

    db.session.commit()
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port, debug=True)