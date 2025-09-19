import os
import re
from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev_secret")

# --- Database Setup ---
db_url = os.environ.get("DATABASE_URL")
if not db_url:
    db_url = "sqlite:///textbox.db"  # local testing fallback
elif db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# --- Login Setup ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "index"

# --- Models ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.String(280), nullable=False)
    likes = db.Column(db.Integer, default=0)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))

class Like(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey("post.id"), nullable=False)
    __table_args__ = (db.UniqueConstraint('user_id', 'post_id', name='_user_post_uc'),)

class Follow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    followed_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    __table_args__ = (db.UniqueConstraint('follower_id', 'followed_id', name='_follower_followed_uc'),)

# --- Login Manager ---
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Profanity Filter ---
def clean_content(text):
    banned_words = ["badword1", "badword2", "badword3"]
    for word in banned_words:
        text = re.sub(re.escape(word), "****", text, flags=re.IGNORECASE)
    return text

# --- Serialize posts ---
def serialize_post(post):
    user_liked = False
    user_following = False
    if current_user.is_authenticated:
        user_liked = Like.query.filter_by(user_id=current_user.id, post_id=post.id).first() is not None
        user_following = Follow.query.filter_by(follower_id=current_user.id, followed_id=post.user_id).first() is not None
    author = User.query.get(post.user_id).username if post.user_id else "anon"
    return {
        "id": post.id,
        "content": post.content,
        "likes": post.likes,
        "timestamp": post.timestamp.isoformat(),
        "author": author,
        "author_id": post.user_id,
        "user_liked": user_liked,
        "user_following": user_following
    }

# --- Routes ---
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/signup", methods=["POST"])
def signup():
    data = request.json
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    if not username or not password:
        return jsonify({"error": "Missing username or password"}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({"error": "Username exists"}), 400
    user = User(username=username, password_hash=generate_password_hash(password))
    db.session.add(user)
    db.session.commit()
    return jsonify({"message": "User created"}), 201

@app.route("/api/login", methods=["POST"])
def login():
    data = request.json
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    user = User.query.filter_by(username=username).first()
    if user and check_password_hash(user.password_hash, password):
        login_user(user)
        return jsonify({"message": "Login successful"})
    return jsonify({"error": "Invalid credentials"}), 401

@app.route("/api/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return jsonify({"message": "Logged out"})

@app.route("/api/posts", methods=["GET"])
def get_posts():
    cutoff = datetime.utcnow() - timedelta(hours=24)
    posts = Post.query.filter(Post.timestamp > cutoff).order_by(Post.timestamp.desc()).all()
    return jsonify([serialize_post(p) for p in posts])

@app.route("/api/posts/following", methods=["GET"])
@login_required
def following_posts():
    cutoff = datetime.utcnow() - timedelta(hours=24)
    followed_ids = [f.followed_id for f in Follow.query.filter_by(follower_id=current_user.id).all()]
    posts = Post.query.filter(Post.user_id.in_(followed_ids), Post.timestamp > cutoff).order_by(Post.timestamp.desc()).all()
    return jsonify([serialize_post(p) for p in posts])

@app.route("/api/posts", methods=["POST"])
@login_required
def add_post():
    data = request.json
    content = clean_content(data.get("content","").strip())
    if not content:
        return jsonify({"error": "Empty post"}), 400
    post = Post(content=content, user_id=current_user.id)
    db.session.add(post)
    db.session.commit()
    return jsonify({"message": "Post added"}), 201

@app.route("/api/posts/<int:post_id>/like", methods=["POST"])
@login_required
def toggle_like_post(post_id):
    post = Post.query.get_or_404(post_id)
    existing_like = Like.query.filter_by(user_id=current_user.id, post_id=post_id).first()
    if existing_like:
        db.session.delete(existing_like)
        post.likes = max(post.likes-1,0)
        db.session.commit()
        return jsonify({"likes": post.likes, "liked": False})
    new_like = Like(user_id=current_user.id, post_id=post_id)
    db.session.add(new_like)
    post.likes += 1
    db.session.commit()
    return jsonify({"likes": post.likes, "liked": True})

@app.route("/api/trending", methods=["GET"])
def trending_posts():
    cutoff = datetime.utcnow() - timedelta(hours=24)
    posts = Post.query.filter(Post.timestamp > cutoff).order_by(Post.likes.desc()).limit(10).all()
    return jsonify([serialize_post(p) for p in posts])

@app.route("/api/follow/<int:user_id>", methods=["POST"])
@login_required
def toggle_follow(user_id):
    if user_id == current_user.id:
        return jsonify({"error": "Cannot follow yourself"}), 400
    existing_follow = Follow.query.filter_by(follower_id=current_user.id, followed_id=user_id).first()
    if existing_follow:
        db.session.delete(existing_follow)
        db.session.commit()
        return jsonify({"following": False})
    new_follow = Follow(follower_id=current_user.id, followed_id=user_id)
    db.session.add(new_follow)
    db.session.commit()
    return jsonify({"following": True})

# --- Startup ---
if __name__ == "__main__":
    with app.app_context():
        db.create_all()  # ✅ this creates all tables
    app.run(host="0.0.0.0", port=5000)


