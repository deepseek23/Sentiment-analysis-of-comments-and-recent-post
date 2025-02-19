from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import requests
import sqlite3
import time

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Change this to a secure secret key

# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# SQLite database setup
def init_db():
    with sqlite3.connect('users.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        ''')
        conn.commit()

init_db()

# User class for Flask-Login
class User(UserMixin):
    def __init__(self, user_id, username):
        self.id = user_id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    with sqlite3.connect('users.db') as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id, username FROM users WHERE id = ?', (user_id,))
        user_data = cursor.fetchone()
        if user_data:
            return User(user_data[0], user_data[1])
    return None

# Route for credentials
@app.route('/credentials', methods=['GET', 'POST'])
def credentials():
    if request.method == 'POST':
        # Store Instagram credentials in session
        session['instagram_user_id'] = request.form['user_id']
        session['instagram_access_token'] = request.form['access_token']
        
        # Store Twitter Bearer Token in session
        session['twitter_bearer_token'] = request.form['twitter_bearer_token']
        
        # Verify Instagram access token permissions
        verify_url = f"https://graph.instagram.com/me?fields=id,username&access_token={session['instagram_access_token']}"
        response = requests.get(verify_url)
        
        if response.status_code != 200:
            flash('Invalid Instagram credentials. Please check your User ID and Access Token.', 'error')
            return redirect(url_for('credentials'))
            
        # Check for Instagram comment permissions
        permissions_url = f"https://graph.instagram.com/me/permissions?access_token={session['instagram_access_token']}"
        permissions_response = requests.get(permissions_url)
        
        if permissions_response.status_code == 200:
            permissions = permissions_response.json().get('data', [])
            has_comment_permission = any(p['permission'] == 'instagram_graph_manage_comments' for p in permissions)
            
            if not has_comment_permission:
                flash('Your access token does not have permission to read comments. Please ensure your Instagram account is a Business or Creator account and that you have granted the "instagram_graph_manage_comments" permission.', 'warning')
        
        flash('Credentials saved successfully!', 'success')
        return redirect(url_for('analyze'))
    
    return render_template('credentials.html')

# Function to fetch Instagram posts
def get_instagram_posts(user_id, access_token):
    if not user_id or not access_token:
        flash('Instagram credentials not provided', 'error')
        return None
    
    # First get media posts
    media_url = f"https://graph.instagram.com/{user_id}/media?fields=id,caption,media_url,media_type,comments_count&access_token={access_token}"
    media_response = requests.get(media_url)
    
    if media_response.status_code != 200:
        flash('Error fetching Instagram posts', 'error')
        return None
        
    media_data = media_response.json()
    
    # For each post, fetch comments if available
    if "data" in media_data:
        for post in media_data["data"]:
            if post.get("id") and post.get("comments_count", 0) > 0:
                # Get comments with additional fields
                comments_url = f"https://graph.instagram.com/{post['id']}/comments?fields=id,text,timestamp,username&access_token={access_token}"
                comments_response = requests.get(comments_url)
                
                if comments_response.status_code == 200:
                    comments_data = comments_response.json()
                    post["comments"] = comments_data.get("data", [])
                    if not post["comments"]:
                        flash(f'Post {post["id"]} has comments but none were returned. Check API permissions.', 'warning')
                    else:
                        print(f"Found {len(post['comments'])} comments for post {post['id']}")  # Debug logging
                else:
                    post["comments"] = []
                    error_msg = f'Error fetching comments for post {post["id"]}. Status code: {comments_response.status_code}'
                    print(error_msg)  # Debug logging
                    flash(error_msg, 'warning')

    return media_data

# Function to analyze sentiment using VADER
def analyze_sentiment(text):
    analyzer = SentimentIntensityAnalyzer()
    return analyzer.polarity_scores(text)

@app.route('/')
def home():
    if current_user.is_authenticated:
        return render_template('home.html')
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        with sqlite3.connect('users.db') as conn:
            cursor = conn.cursor()
            try:
                cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, password))
                conn.commit()
                flash('Registration successful! Please log in.', 'success')
                return redirect(url_for('login'))
            except sqlite3.IntegrityError:
                flash('Username already exists. Please choose a different username.', 'error')

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        with sqlite3.connect('users.db') as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, username FROM users WHERE username = ? AND password = ?', (username, password))
            user_data = cursor.fetchone()

            if user_data:
                user = User(user_data[0], user_data[1])
                login_user(user)
                return redirect(url_for('credentials'))
            else:
                session['login_error'] = 'Invalid username or password.'
                return redirect(url_for('login'))

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('home'))

@app.route('/analyze')
@login_required
def analyze():
    # Instagram Analysis
    instagram_results = []
    if 'instagram_user_id' in session and 'instagram_access_token' in session:
        data = get_instagram_posts(session['instagram_user_id'], session['instagram_access_token'])
        if data and "data" in data:
            for post in data["data"]:
                media_url = post.get("media_url")
                media_type = post.get("media_type")
                caption = post.get("caption", "No caption available")
                comments = []
                if "comments" in post:
                    comments = [{"text": c.get("text"), "sentiment": analyze_sentiment(c.get("text"))} 
                               for c in post["comments"] if c.get("text")]
                caption_sentiment = analyze_sentiment(caption)
                instagram_results.append({
                    "media_url": media_url,
                    "media_type": media_type,
                    "caption": caption,
                    "caption_sentiment": caption_sentiment,
                    "comments": comments
                })

    # Twitter Analysis
    twitter_results = []
    if 'twitter_bearer_token' in session:
        tweets_data = get_tweets("AI", count=10, bearer_token=session['twitter_bearer_token'])
        
        if tweets_data:
            if "error" in tweets_data:
                flash(f"Twitter API Error: {tweets_data['error']}", "error")
            elif "data" in tweets_data:
                for tweet in tweets_data["data"]:
                    user_info = next((u for u in tweets_data.get("includes", {}).get("users", [])
                                   if u["id"] == tweet["author_id"]), {})
                    text = tweet["text"]
                    sentiment = analyze_sentiment(text)
                    sentiment_label = "Neutral"
                    if sentiment["compound"] > 0.05:
                        sentiment_label = "Positive 😊"
                    elif sentiment["compound"] < -0.05:
                        sentiment_label = "Negative 😡"
                    twitter_results.append({
                        "text": text,
                        "author": user_info.get("name", "Unknown"),
                        "username": user_info.get("username", ""),
                        "profile_image": user_info.get("profile_image_url", ""),
                        "created_at": tweet.get("created_at", ""),
                        "sentiment": sentiment,
                        "sentiment_label": sentiment_label
                    })
            else:
                flash("No tweets found for the given search criteria", "info")

    return render_template('analyze.html', instagram_results=instagram_results, twitter_results=twitter_results)

# Function to fetch recent tweets
def get_tweets(keyword, count=10, retries=5, bearer_token=None):
    if not bearer_token:
        print("Error: No Twitter Bearer Token provided")
        return None
        
    headers = {"Authorization": f"Bearer {bearer_token}"}
    params = {
        "query": keyword,
        "max_results": count,
        "tweet.fields": "text,created_at",
        "expansions": "author_id",
        "user.fields": "username,name,profile_image_url"
    }

    url = "https://api.twitter.com/2/tweets/search/recent"
    
    for i in range(retries):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=15)
            print("Response Status Code:", response.status_code)
            
            # Check rate limit headers
            if response.status_code == 429:
                rate_limit_reset = int(response.headers.get('x-rate-limit-reset', 0))
                current_time = int(time.time())
                wait_time = max(rate_limit_reset - current_time, 10)  # Minimum 10 seconds
                wait_time += (i * 5)  # Add jitter based on retry count
                print(f"Rate limit reached. Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
                continue
                
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401:
                print("Error: Invalid or expired Bearer Token")
                return {"error": "Invalid or expired Bearer Token. Please update your credentials."}
            else:
                error_msg = f"Error {response.status_code}: {response.text}"
                print(error_msg)
                return {"error": error_msg}
                
        except requests.exceptions.RequestException as e:
            print(f"Network error: {str(e)}")
            if i == retries - 1:  # Last retry
                return {"error": f"Network error: {str(e)}"}
            time.sleep(10 + (i * 5))  # Exponential backoff with jitter

    print("Max retries reached. Try again later.")
    return {"error": "Max retries reached. Please try again later."}


if __name__ == '__main__':
    app.run(debug=True)
