from flask import Flask, render_template, request, redirect, session, jsonify
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired
import sqlite3
from functools import wraps
from datetime import datetime
import requests
import xml.etree.ElementTree as ET
import html

app = Flask(__name__)
app.secret_key = 'your_secret_key_here' 
TESTING_MODE = True

def get_db():
   db = sqlite3.connect('boardgames.db')
   db.row_factory = sqlite3.Row
   return db

def summarize_description(description, max_points=5):
    # Keep first 2 sentences, max 50 words
    sentences = description.split('.')[:2]
    shortened = '. '.join(sentences).strip() + '.'
    words = shortened.split()[:50]
    return ' '.join(words)

def fetch_bgg_games(start_id=1, count=100):
    # Get popular games by rank
    url = f"https://boardgamegeek.com/xmlapi2/hot"
    response = requests.get(url)
    root = ET.fromstring(response.content)
    
    games_data = []
    for item in root.findall('item'):
        game_id = item.get('id')
        # Get detailed game info
        detail_url = f"https://boardgamegeek.com/xmlapi2/thing?id={game_id}&stats=1"
        detail = requests.get(detail_url)
        game = ET.fromstring(detail.content).find('item')
        
        try:
            games_data.append({
                'name': game.find('name').get('value'),
                'description': game.find('description').text,
                'image_url': game.find('image').text if game.find('image') is not None else None,
                'min_players': int(game.find('minplayers').get('value')),
                'max_players': int(game.find('maxplayers').get('value')),
                'avg_playtime': int(game.find('playingtime').get('value'))
            })
        except AttributeError:
            continue
            
    return games_data

def populate_db():
    db = get_db()
    games = fetch_bgg_games()
    for game in games:
        db.execute('''
            INSERT INTO games (name, description, image_url, min_players, max_players, avg_playtime)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', [game['name'], game['description'], game['image_url'], 
              game['min_players'], game['max_players'], game['avg_playtime']])
    db.commit()

def populate_genres():
   genres = ['Strategy', 'Family', 'Party', 'Thematic', 'Wargames', 
             'Abstract', 'Cooperative', 'Card Game', 'Dice', 'Economic']
   db = get_db()
   for genre in genres:
       db.execute('INSERT INTO genres (name) VALUES (?)', [genre])
   db.commit()

def init_db():
   with app.app_context():
       db = get_db()
       db.execute('''CREATE TABLE IF NOT EXISTS sessions (
           code TEXT PRIMARY KEY,
           created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
       )''')
       db.execute('''CREATE TABLE IF NOT EXISTS players (
           id INTEGER PRIMARY KEY,
           session_code TEXT,
           name TEXT,
           selected_genres TEXT,
           FOREIGN KEY (session_code) REFERENCES sessions(code)
       )''')
       db.execute('''CREATE TABLE IF NOT EXISTS genres (
           id INTEGER PRIMARY KEY,
           name TEXT NOT NULL
       )''')
       db.execute('''CREATE TABLE IF NOT EXISTS games (
           id INTEGER PRIMARY KEY,
           name TEXT NOT NULL,
           description TEXT,
           image_url TEXT,
           min_players INTEGER,
           max_players INTEGER,
           avg_playtime INTEGER
       )''')
       db.execute('''CREATE TABLE IF NOT EXISTS board_game_genres (
           game_id INTEGER,
           genre_id INTEGER,
           FOREIGN KEY (game_id) REFERENCES games(id),
           FOREIGN KEY (genre_id) REFERENCES genres(id)
       )''')
       db.execute('''CREATE TABLE IF NOT EXISTS swipes (
           player_id INTEGER,
           game_id INTEGER,
           liked BOOLEAN,
           swiped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
           FOREIGN KEY (player_id) REFERENCES players(id),
           FOREIGN KEY (game_id) REFERENCES games(id)
       )''')
       db.commit()
       
       # Populate genres if empty
       cursor = db.execute('SELECT COUNT(*) as count FROM genres')
       if cursor.fetchone()['count'] == 0:
           populate_genres()
           
       # Populate games if empty
       cursor = db.execute('SELECT COUNT(*) as count FROM games')
       if cursor.fetchone()['count'] == 0:
           populate_db()

def login_required(f):
   @wraps(f)
   def decorated_function(*args, **kwargs):
       if 'player_id' not in session:
           return redirect('/login')
       return f(*args, **kwargs)
   return decorated_function

class LoginForm(FlaskForm):
   name = StringField('Name', validators=[DataRequired()])
   session_code = StringField('Session Code', validators=[DataRequired()])
   submit = SubmitField('Join Game')

@app.route('/')
def home():
   return render_template('home.html')

@app.route('/create_session', methods=['POST'])
def create_session():
   # Generate unique 6-char code
   code = datetime.now().strftime('%H%M%S')
   db = get_db()
   db.execute('INSERT INTO sessions (code) VALUES (?)', [code])
   db.commit()
   return jsonify({'code': code})

@app.route('/login', methods=['GET', 'POST'])
def login():
   form = LoginForm()
   if form.validate_on_submit():
       db = get_db()
       session_exists = db.execute('SELECT 1 FROM sessions WHERE code = ?', 
                                 [form.session_code.data]).fetchone()
       
       if not session_exists:
           return "Invalid session code", 400
           
       cursor = db.execute('''INSERT INTO players (name, session_code) 
                          VALUES (?, ?) RETURNING id''',
                          [form.name.data, form.session_code.data])
       player_id = cursor.fetchone()[0]
       db.commit()
       
       session['player_id'] = player_id
       session['session_code'] = form.session_code.data
       session['name'] = form.name.data
       return redirect('/select_genres')
   return render_template('login.html', form=form)

@app.route('/select_genres', methods=['GET', 'POST'])
@login_required
def select_genres():
    db = get_db()
    
    # Fetch available genres from the database
    genres = db.execute('SELECT * FROM genres').fetchall()
    
    if request.method == 'POST':
        # Get selected genres from the form
        selected_genres = request.form.getlist('genres')
        
        # Ensure exactly 3 genres are selected
        if len(selected_genres) != 3:
            return "You must select exactly 3 genres", 400
        
        # Store selected genres in the session
        session['genres'] = selected_genres
        return redirect('/swipe')
    
    # Render the select_genres.html template
    return render_template('select_genres.html', genres=genres)

@app.route('/swipe', methods=['GET'])
@login_required
def swipe():
    db = get_db()
    games = db.execute('''
        SELECT * FROM games
        ORDER BY RANDOM()
        LIMIT 1
    ''').fetchall()

    if not games:
        return "No games found in the database."

    game_list = []
    for game in games:
        game_dict = dict(game)
        game_dict['description'] = summarize_description(game_dict['description'])
        game_list.append(game_dict)

    return render_template('swipe.html', games=game_list)


@app.route('/handle_swipe', methods=['POST'])
@login_required
def handle_swipe():
   game_id = request.form.get('game_id')
   liked = request.form.get('liked') == 'true'
   
   db = get_db()
   db.execute('''INSERT INTO swipes (player_id, game_id, liked) 
              VALUES (?, ?, ?)''',
              [session['player_id'], game_id, liked])
   db.commit()
   return redirect('/swipe')

@app.route('/matches')
@login_required
def show_matches():
   db = get_db()
   matches = db.execute('''
       SELECT g.*, COUNT(*) as like_count
       FROM games g
       JOIN swipes s ON g.id = s.game_id
       WHERE s.liked = TRUE
       AND s.player_id IN (
           SELECT id FROM players
           WHERE session_code = ?
       )
       GROUP BY g.id
       HAVING like_count = (
           SELECT COUNT(*) FROM players
           WHERE session_code = ?
       )
       ORDER BY g.name
   ''', [session['session_code'], session['session_code']]).fetchall()
   
   return render_template('matches.html', matches=matches)

@app.route('/waiting')
@login_required
def waiting_room():
    db = get_db()

    # Get number of players who have finished swiping
    finished_players = db.execute('''
        SELECT COUNT(DISTINCT player_id) as count
        FROM swipes 
        WHERE player_id IN (
            SELECT id FROM players
            WHERE session_code = ?
        )
        GROUP BY player_id
        HAVING COUNT(*) = (SELECT COUNT(*) FROM games)
    ''', [session['session_code']]).fetchone()

    # Get total players in the session
    total_players = db.execute('''
        SELECT COUNT(*) as count 
        FROM players 
        WHERE session_code = ?
    ''', [session['session_code']]).fetchone()

    # Redirect to matches if all players are done
    if finished_players and finished_players['count'] == total_players['count']:
        return redirect('/matches')

    # Otherwise, render the waiting room
    return render_template('waiting.html',
                           finished=finished_players['count'] if finished_players else 0,
                           total=total_players['count'])


@app.route('/end_session')
@login_required
def end_session():
    db = get_db()
    matches = db.execute('''
        SELECT g.*, COUNT(*) as like_count
        FROM games g
        JOIN swipes s ON g.id = s.game_id
        WHERE s.liked = TRUE
        AND s.player_id IN (
            SELECT id FROM players
            WHERE session_code = ?
        )
        GROUP BY g.id
        ORDER BY like_count DESC, g.name
    ''', [session['session_code']]).fetchall()
    
    # Apply summarization to each game description
    matches_list = []
    for game in matches:
        game_dict = dict(game)
        game_dict['description'] = summarize_description(game_dict['description'])
        matches_list.append(game_dict)
    
    return render_template('session_results.html', matches=matches_list)


if __name__ == '__main__':
    init_db()  # This will call populate_db()
    app.run(debug=True)