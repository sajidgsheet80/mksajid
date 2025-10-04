from fyers_apiv3 import fyersModel
from flask import Flask, request, render_template_string, jsonify, redirect, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import webbrowser
import pandas as pd
import os
import threading
import time

app = Flask(__name__)
app.secret_key = "sajid_secret_key_change_this"

# Text files for storing data
USERS_FILE = "users.txt"
CREDENTIALS_FILE = "user_credentials.txt"

# Initialize files
def init_files():
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'w') as f:
            f.write("")
    if not os.path.exists(CREDENTIALS_FILE):
        with open(CREDENTIALS_FILE, 'w') as f:
            f.write("")

init_files()

# ---- User Management Functions ----
def save_user(username, password, email):
    with open(USERS_FILE, 'a') as f:
        hashed_pw = generate_password_hash(password)
        f.write(f"{username}|{hashed_pw}|{email}\n")

def get_user(username):
    if not os.path.exists(USERS_FILE):
        return None
    with open(USERS_FILE, 'r') as f:
        for line in f:
            if line.strip():
                parts = line.strip().split('|')
                if len(parts) >= 3 and parts[0] == username:
                    return {'username': parts[0], 'password': parts[1], 'email': parts[2]}
    return None

def verify_user(username, password):
    user = get_user(username)
    if user and check_password_hash(user['password'], password):
        return user
    return None

def save_user_credentials(username, client_id=None, secret_key=None, auth_code=None):
    credentials = {}
    if os.path.exists(CREDENTIALS_FILE):
        with open(CREDENTIALS_FILE, 'r') as f:
            for line in f:
                if line.strip():
                    parts = line.strip().split('|')
                    if len(parts) >= 4:
                        credentials[parts[0]] = {'client_id': parts[1], 'secret_key': parts[2], 'auth_code': parts[3]}
    
    if username not in credentials:
        credentials[username] = {'client_id': '', 'secret_key': '', 'auth_code': ''}
    
    if client_id:
        credentials[username]['client_id'] = client_id
    if secret_key:
        credentials[username]['secret_key'] = secret_key
    if auth_code:
        credentials[username]['auth_code'] = auth_code
    
    with open(CREDENTIALS_FILE, 'w') as f:
        for user, creds in credentials.items():
            f.write(f"{user}|{creds['client_id']}|{creds['secret_key']}|{creds['auth_code']}\n")

def get_user_credentials(username):
    if not os.path.exists(CREDENTIALS_FILE):
        return None
    with open(CREDENTIALS_FILE, 'r') as f:
        for line in f:
            if line.strip():
                parts = line.strip().split('|')
                if len(parts) >= 4 and parts[0] == username:
                    return {'client_id': parts[1], 'secret_key': parts[2], 'auth_code': parts[3]}
    return None

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

# ---- User-specific sessions ----
user_sessions = {}

def get_user_session(username):
    if username not in user_sessions:
        user_sessions[username] = {
            'fyers': None,
            'atm_strike': None,
            'initial_data': None,
            'atm_ce_plus20': 20,
            'atm_pe_plus20': 20,
            'symbol_prefix': 'NSE:NIFTY25',
            'signals': [],
            'placed_orders': set(),
            'bot_running': False,
            'bot_thread': None,
            'redirect_uri': f'https://127.0.0.1/callback/{username}'
        }
    return user_sessions[username]

# ---- Fyers Functions ----
def init_fyers_for_user(username, client_id, secret_key, auth_code):
    user_sess = get_user_session(username)
    try:
        appSession = fyersModel.SessionModel(
            client_id=client_id,
            secret_key=secret_key,
            redirect_uri=user_sess['redirect_uri'],
            response_type="code",
            grant_type="authorization_code",
            state="sample"
        )
        appSession.set_token(auth_code)
        token_response = appSession.generate_token()
        access_token = token_response.get("access_token")
        user_sess['fyers'] = fyersModel.FyersModel(
            client_id=client_id,
            token=access_token,
            is_async=False,
            log_path=""
        )
        print(f"✅ Fyers initialized for {username}")
        return True
    except Exception as e:
        print(f"❌ Failed to init Fyers for {username}:", e)
        return False

def place_order(username, symbol, price, side):
    user_sess = get_user_session(username)
    try:
        if user_sess['fyers'] is None:
            return
        data = {
            "symbol": symbol,
            "qty": 75,
            "type": 1,
            "side": side,
            "productType": "INTRADAY",
            "limitPrice": price,
            "stopPrice": 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
            "orderTag": "signalorder"
        }
        response = user_sess['fyers'].place_order(data=data)
        print(f"✅ Order placed for {username}:", response)
    except Exception as e:
        print(f"❌ Order error for {username}:", e)

def background_bot_worker(username):
    user_sess = get_user_session(username)
    print(f"🤖 Background bot started for {username}")
    
    while user_sess['bot_running']:
        if user_sess['fyers'] is None:
            time.sleep(5)
            continue
            
        try:
            data = {"symbol": "NSE:NIFTY50-INDEX", "strikecount": 10, "timestamp": ""}
            response = user_sess['fyers'].optionchain(data=data)

            if "data" not in response or "optionsChain" not in response["data"]:
                time.sleep(2)
                continue

            options_data = response["data"]["optionsChain"]
            if not options_data:
                time.sleep(2)
                continue

            df = pd.DataFrame(options_data)
            df_pivot = df.pivot_table(index="strike_price", columns="option_type", values="ltp", aggfunc="first").reset_index()
            df_pivot = df_pivot.rename(columns={"CE": "CE_LTP", "PE": "PE_LTP"})

            if user_sess['atm_strike'] is None:
                nifty_spot = response["data"].get("underlyingValue", df_pivot["strike_price"].iloc[len(df_pivot) // 2])
                user_sess['atm_strike'] = min(df_pivot["strike_price"], key=lambda x: abs(x - nifty_spot))
                user_sess['initial_data'] = df_pivot.to_dict(orient="records")
                user_sess['signals'].clear()
                user_sess['placed_orders'].clear()

            for row in df_pivot.itertuples():
                strike = row.strike_price
                ce_ltp = getattr(row, "CE_LTP", None)
                pe_ltp = getattr(row, "PE_LTP", None)

                if strike == user_sess['atm_strike'] and ce_ltp is not None:
                    initial_ce = next((item["CE_LTP"] for item in user_sess['initial_data'] if item["strike_price"] == strike), None)
                    if initial_ce is not None and ce_ltp > initial_ce + user_sess['atm_ce_plus20']:
                        signal_name = f"ATM_CE_{strike}"
                        if signal_name not in user_sess['placed_orders']:
                            user_sess['signals'].append(f"{strike} {ce_ltp} ATM Strike CE")
                            place_order(username, f"{user_sess['symbol_prefix']}{strike}CE", ce_ltp, side=1)
                            user_sess['placed_orders'].add(signal_name)

                if strike == user_sess['atm_strike'] and pe_ltp is not None:
                    initial_pe = next((item["PE_LTP"] for item in user_sess['initial_data'] if item["strike_price"] == strike), None)
                    if initial_pe is not None and pe_ltp > initial_pe + user_sess['atm_pe_plus20']:
                        signal_name = f"ATM_PE_{strike}"
                        if signal_name not in user_sess['placed_orders']:
                            user_sess['signals'].append(f"{strike} {pe_ltp} ATM Strike PE")
                            place_order(username, f"{user_sess['symbol_prefix']}{strike}PE", pe_ltp, side=1)
                            user_sess['placed_orders'].add(signal_name)

        except Exception as e:
            print(f"❌ Background bot error for {username}: {e}")
        
        time.sleep(2)
    
    print(f"🤖 Background bot stopped for {username}")

# ---- Auth Routes ----
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        email = request.form.get('email')
        
        if not username or not password or not email:
            return render_template_string(SIGNUP_TEMPLATE, error="All fields are required!")
        
        if get_user(username):
            return render_template_string(SIGNUP_TEMPLATE, error="Username already exists!")
        
        save_user(username, password, email)
        return redirect(url_for('login_page'))
    
    return render_template_string(SIGNUP_TEMPLATE)

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = verify_user(username, password)
        
        if user:
            session['username'] = user['username']
            session['email'] = user['email']
            
            # Load saved credentials if available
            creds = get_user_credentials(username)
            if creds and creds['client_id'] and creds['secret_key'] and creds['auth_code']:
                init_fyers_for_user(username, creds['client_id'], creds['secret_key'], creds['auth_code'])
            
            return redirect(url_for('index'))
        else:
            return render_template_string(LOGIN_TEMPLATE, error="Invalid credentials!")
    
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/logout')
def logout():
    username = session.get('username')
    if username and username in user_sessions:
        user_sessions[username]['bot_running'] = False
    session.clear()
    return redirect(url_for('login_page'))

# ---- Main App Routes ----
@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    username = session['username']
    user_sess = get_user_session(username)
    
    if request.method == "POST":
        try:
            user_sess['atm_ce_plus20'] = float(request.form.get("atm_ce_plus20", 20))
        except (ValueError, TypeError):
            user_sess['atm_ce_plus20'] = 20
        try:
            user_sess['atm_pe_plus20'] = float(request.form.get("atm_pe_plus20", 20))
        except (ValueError, TypeError):
            user_sess['atm_pe_plus20'] = 20
        prefix = request.form.get("symbol_prefix")
        if prefix:
            user_sess['symbol_prefix'] = prefix.strip()

    return render_template_string(
        MAIN_TEMPLATE,
        atm_ce_plus20=user_sess['atm_ce_plus20'],
        atm_pe_plus20=user_sess['atm_pe_plus20'],
        symbol_prefix=user_sess['symbol_prefix'],
        bot_running=user_sess['bot_running'],
        username=username
    )

@app.route("/setup_credentials", methods=["GET", "POST"])
@login_required
def setup_credentials():
    username = session['username']
    creds = get_user_credentials(username)
    
    if request.method == "POST":
        client_id = request.form.get("client_id")
        secret_key = request.form.get("secret_key")
        
        if client_id and secret_key:
            save_user_credentials(username, client_id=client_id, secret_key=secret_key)
            return redirect(url_for('fyers_login'))
    
    return render_template_string(CREDENTIALS_TEMPLATE, 
                                   client_id=creds['client_id'] if creds else "",
                                   secret_key=creds['secret_key'] if creds else "")

@app.route("/fyers_login")
@login_required
def fyers_login():
    username = session['username']
    creds = get_user_credentials(username)
    user_sess = get_user_session(username)
    
    if not creds or not creds['client_id'] or not creds['secret_key']:
        return redirect(url_for('setup_credentials'))
    
    appSession = fyersModel.SessionModel(
        client_id=creds['client_id'],
        secret_key=creds['secret_key'],
        redirect_uri=user_sess['redirect_uri'],
        response_type="code",
        grant_type="authorization_code",
        state="sample"
    )
    
    login_url = appSession.generate_authcode()
    webbrowser.open(login_url, new=1)
    return redirect(login_url)

@app.route("/callback/<username>")
def callback(username):
    auth_code = request.args.get("auth_code")
    if auth_code:
        creds = get_user_credentials(username)
        if creds:
            save_user_credentials(username, auth_code=auth_code)
            if init_fyers_for_user(username, creds['client_id'], creds['secret_key'], auth_code):
                return "<h2>✅ Authentication Successful! You can return to the app 🚀</h2>"
    return "❌ Authentication failed. Please retry."

@app.route("/fetch")
@login_required
def fetch_option_chain():
    username = session['username']
    user_sess = get_user_session(username)
    
    if user_sess['fyers'] is None:
        return jsonify({"error": "⚠ Please setup credentials and login first!"})
    
    try:
        data = {"symbol": "NSE:NIFTY50-INDEX", "strikecount": 20, "timestamp": ""}
        response = user_sess['fyers'].optionchain(data=data)

        if "data" not in response or "optionsChain" not in response["data"]:
            return jsonify({"error": f"Invalid response from API"})

        options_data = response["data"]["optionsChain"]
        if not options_data:
            return jsonify({"error": "No options data found!"})

        df = pd.DataFrame(options_data)
        df_pivot = df.pivot_table(index="strike_price", columns="option_type", values="ltp", aggfunc="first").reset_index()
        df_pivot = df_pivot.rename(columns={"CE": "CE_LTP", "PE": "PE_LTP"})

        if user_sess['atm_strike'] is None:
            nifty_spot = response["data"].get("underlyingValue", df_pivot["strike_price"].iloc[len(df_pivot) // 2])
            user_sess['atm_strike'] = min(df_pivot["strike_price"], key=lambda x: abs(x - nifty_spot))
            user_sess['initial_data'] = df_pivot.to_dict(orient="records")
            user_sess['signals'].clear()
            user_sess['placed_orders'].clear()

        if not user_sess['bot_running']:
            for row in df_pivot.itertuples():
                strike = row.strike_price
                ce_ltp = getattr(row, "CE_LTP", None)
                pe_ltp = getattr(row, "PE_LTP", None)

                if strike == user_sess['atm_strike'] and ce_ltp is not None:
                    initial_ce = next((item["CE_LTP"] for item in user_sess['initial_data'] if item["strike_price"] == strike), None)
                    if initial_ce is not None and ce_ltp > initial_ce + user_sess['atm_ce_plus20']:
                        signal_name = f"ATM_CE_{strike}"
                        if signal_name not in user_sess['placed_orders']:
                            user_sess['signals'].append(f"{strike} {ce_ltp} ATM Strike CE")
                            place_order(username, f"{user_sess['symbol_prefix']}{strike}CE", ce_ltp, side=1)
                            user_sess['placed_orders'].add(signal_name)

                if strike == user_sess['atm_strike'] and pe_ltp is not None:
                    initial_pe = next((item["PE_LTP"] for item in user_sess['initial_data'] if item["strike_price"] == strike), None)
                    if initial_pe is not None and pe_ltp > initial_pe + user_sess['atm_pe_plus20']:
                        signal_name = f"ATM_PE_{strike}"
                        if signal_name not in user_sess['placed_orders']:
                            user_sess['signals'].append(f"{strike} {pe_ltp} ATM Strike PE")
                            place_order(username, f"{user_sess['symbol_prefix']}{strike}PE", pe_ltp, side=1)
                            user_sess['placed_orders'].add(signal_name)

        return df_pivot.to_json(orient="records")
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/start_bot", methods=["POST"])
@login_required
def start_bot():
    username = session['username']
    user_sess = get_user_session(username)
    
    if user_sess['fyers'] is None:
        return jsonify({"error": "⚠️ Please login first!"})
    
    if user_sess['bot_running']:
        return jsonify({"error": "⚠️ Bot is already running!"})
    
    user_sess['bot_running'] = True
    user_sess['bot_thread'] = threading.Thread(target=background_bot_worker, args=(username,), daemon=True)
    user_sess['bot_thread'].start()
    
    return jsonify({"message": "✅ Bot started! Running in background!"})

@app.route("/stop_bot", methods=["POST"])
@login_required
def stop_bot():
    username = session['username']
    user_sess = get_user_session(username)
    user_sess['bot_running'] = False
    return jsonify({"message": "✅ Bot stopped!"})

@app.route("/bot_status")
@login_required
def bot_status():
    username = session['username']
    user_sess = get_user_session(username)
    return jsonify({
        "running": user_sess['bot_running'],
        "signals": user_sess['signals'],
        "placed_orders": list(user_sess['placed_orders'])
    })

@app.route("/reset", methods=["POST"])
@login_required
def reset_orders():
    username = session['username']
    user_sess = get_user_session(username)
    user_sess['placed_orders'].clear()
    user_sess['signals'].clear()
    user_sess['atm_strike'] = None
    user_sess['initial_data'] = None
    return jsonify({"message": "✅ Reset successful!"})

# ---- HTML Templates ----
SIGNUP_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Sign Up</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: Arial, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
               display: flex; justify-content: center; align-items: center; min-height: 100vh; }
        .container { background: white; padding: 40px; border-radius: 10px; box-shadow: 0 10px 25px rgba(0,0,0,0.2); width: 400px; }
        h2 { color: #333; text-align: center; margin-bottom: 30px; }
        input { width: 100%; padding: 12px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px; }
        button { width: 100%; padding: 12px; background: #667eea; color: white; border: none; border-radius: 5px;
                 cursor: pointer; font-size: 16px; margin-top: 10px; }
        button:hover { background: #5568d3; }
        .error { color: red; text-align: center; margin-bottom: 10px; }
        .link { text-align: center; margin-top: 20px; }
        .link a { color: #667eea; text-decoration: none; }
    </style>
</head>
<body>
    <div class="container">
        <h2>📝 Sign Up</h2>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
        <form method="POST">
            <input type="text" name="username" placeholder="Username" required>
            <input type="email" name="email" placeholder="Email" required>
            <input type="password" name="password" placeholder="Password" minlength="6" required>
            <button type="submit">Create Account</button>
        </form>
        <div class="link">Already have an account? <a href="/login">Login</a></div>
    </div>
</body>
</html>
"""

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Login</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: Arial, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
               display: flex; justify-content: center; align-items: center; min-height: 100vh; }
        .container { background: white; padding: 40px; border-radius: 10px; box-shadow: 0 10px 25px rgba(0,0,0,0.2); width: 400px; }
        h2 { color: #333; text-align: center; margin-bottom: 30px; }
        input { width: 100%; padding: 12px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px; }
        button { width: 100%; padding: 12px; background: #667eea; color: white; border: none; border-radius: 5px;
                 cursor: pointer; font-size: 16px; margin-top: 10px; }
        button:hover { background: #5568d3; }
        .error { color: red; text-align: center; margin-bottom: 10px; }
        .link { text-align: center; margin-top: 20px; }
        .link a { color: #667eea; text-decoration: none; }
    </style>
</head>
<body>
    <div class="container">
        <h2>🔐 Login</h2>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
        <form method="POST">
            <input type="text" name="username" placeholder="Username" required>
            <input type="password" name="password" placeholder="Password" required>
            <button type="submit">Login</button>
        </form>
        <div class="link">Don't have an account? <a href="/signup">Sign Up</a></div>
    </div>
</body>
</html>
"""

CREDENTIALS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Setup Credentials</title>
    <style>
        body { font-family: Arial, sans-serif; background: #f4f4f9; padding: 20px; }
        .container { max-width: 600px; margin: 50px auto; background: white; padding: 40px;
                     border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h2 { color: #1a73e8; text-align: center; }
        input { width: 100%; padding: 12px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px; }
        button { width: 100%; padding: 12px; background: #1a73e8; color: white; border: none;
                 border-radius: 5px; cursor: pointer; font-size: 16px; margin-top: 10px; }
        .info { background: #e3f2fd; padding: 15px; border-radius: 5px; margin-bottom: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <h2>🔑 Setup Fyers Credentials</h2>
        <div class="info"><strong>Note:</strong> Enter your Fyers API credentials.</div>
        <form method="POST">
            <input type="text" name="client_id" placeholder="Fyers Client ID" value="{{ client_id }}" required>
            <input type="text" name="secret_key" placeholder="Fyers Secret Key" value="{{ secret_key }}" required>
            <button type="submit">Save & Continue to Login</button>
        </form>
    </div>
</body>
</html>
"""

MAIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <title>Sajid Shaikh Algo Software</title>
  <style>
    body { font-family: Arial, sans-serif; background: #f4f4f9; padding: 20px; }
    .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 15px 30px;
              display: flex; justify-content: space-between; align-items: center; border-radius: 8px; margin-bottom: 20px; }
    .logout-btn { padding: 8px 15px; background: rgba(255,255,255,0.2); color: white; text-decoration: none;
                  border-radius: 4px; border: 1px solid rgba(255,255,255,0.3); margin-left: 10px; }
    .cred-btn { padding: 8px 15px; background: #ff9800; color: white; text-decoration: none; border-radius: 4px; margin-left: 10px; }
    h2 { color: #1a73e8; }
    .bot-control { background: #fff; padding: 15px; margin: 20px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
    .bot-status { display: inline-block; padding: 5px 10px; border-radius: 4px; font-weight: bold; margin-right: 10px; }
    .status-running { background: #4caf50; color: white; }
    .status-stopped { background: #f44336; color: white; }
    table { border-collapse: collapse; width: 70%; margin-top: 10px; }
    th, td { border: 1px solid #aaa; padding: 8px; text-align: center; }
    th { background-color: #1a73e8; color: white; }
    tr:nth-child(even) { background-color: #f2f2f2; }
    tr.atm { background-color: #ffeb3b; font-weight: bold; }
    tr.ceMinus300 { background-color: #90ee90; font-weight: bold; }
    tr.pePlus300 { background-color: #ffb6c1; font-weight: bold; }
    a { text-decoration: none; padding: 8px 12px; background: #4caf50; color: white; border-radius: 4px; }
    a:hover { background: #45a049; }
    button { padding: 8px 12px; color: white; border: none; border-radius: 4px; cursor: pointer; margin-right: 5px; }
    .btn-start { background-color: #4caf50; }
    .btn-start:hover { background-color: #45a049; }
    .btn-stop { background-color: #f44336; }
    .btn-stop:hover { background-color: #da190b; }
    .btn-reset { background-color: #1a73e8; }
    .btn-reset:hover { background-color: #155cb0; }
    #signals { margin-top: 15px; font-weight: bold; color: red; }
    #profits { margin-top: 8px; font-weight: bold; color: green; }
    form { margin-top: 20px; }
    label { margin-right: 10px; }
    input[type="number"], input[type="text"] { padding: 5px; margin-right: 20px; }
  </style>
  <script>
    var atmStrike = null;
    var initialLTP = {};
    var signals = [];

    async function startBackgroundBot(){
        let res = await fetch("/start_bot", {method: "POST"});
        let data = await res.json();
        alert(data.message || data.error);
        checkBotStatus();
    }

    async function stopBackgroundBot(){
        let res = await fetch("/stop_bot", {method: "POST"});
        let data = await res.json();
        alert(data.message);
        checkBotStatus();
    }

    async function checkBotStatus(){
        let res = await fetch("/bot_status");
        let data = await res.json();
        let statusDiv = document.getElementById("botStatus");
        if(data.running){
            statusDiv.innerHTML = '<span class="bot-status status-running">🤖 Bot Running (Background)</span>';
            document.getElementById("startBtn").disabled = true;
            document.getElementById("stopBtn").disabled = false;
        } else {
            statusDiv.innerHTML = '<span class="bot-status status-stopped">⏸️ Bot Stopped</span>';
            document.getElementById("startBtn").disabled = false;
            document.getElementById("stopBtn").disabled = true;
        }
    }

    async function fetchChain(){
        let res = await fetch("/fetch");
        let data = await res.json();
        let tbl = document.getElementById("chain");
        tbl.innerHTML = "";
        let signalsDiv = document.getElementById("signals");
        let profitsDiv = document.getElementById("profits");

        if(data.error){
            tbl.innerHTML = `<tr><td colspan="3">${data.error}</td></tr>`;
            signalsDiv.innerHTML = "";
            profitsDiv.innerHTML = "";
            return;
        }

        if(atmStrike === null){
            atmStrike = data[Math.floor(data.length/2)].strike_price;
        }

        if(Object.keys(initialLTP).length === 0){
            data.forEach(r => {
                initialLTP[r.strike_price] = {CE: r.CE_LTP, PE: r.PE_LTP};
            });
        }

        let atmLive = data.find(r => r.strike_price === atmStrike);
        signals = [];

        let atm_ce_plus20 = parseFloat(document.getElementById("atm_ce_plus20").value) || 20;
        let atm_pe_plus20 = parseFloat(document.getElementById("atm_pe_plus20").value) || 20;

        if(atmLive){
            if(atmLive.CE_LTP > (initialLTP[atmStrike]?.CE + atm_ce_plus20)){
                signals.push("ATM Strike CE");
            }
            if(atmLive.PE_LTP > (initialLTP[atmStrike]?.PE + atm_pe_plus20)){
                signals.push("ATM Strike PE");
            }
        }

        signalsDiv.innerHTML = signals.length > 0 ? "📢 Capture Signals: " + signals.join(", ") : "No signals";

        let profitsOutput = "";
        signals.forEach(signal => {
            let strike = atmStrike;
            let initialLtp = null;
            let liveLtp = null;
            let profit = 0;

            if(signal === "ATM Strike CE") {
                initialLtp = initialLTP[atmStrike]?.CE;
                liveLtp = atmLive.CE_LTP;
                profit = (liveLtp - initialLtp);
            } else if(signal === "ATM Strike PE") {
                initialLtp = initialLTP[atmStrike]?.PE;
                liveLtp = atmLive.PE_LTP;
                profit = (liveLtp - initialLtp);
            }
            let totalProfit = (profit * 75).toFixed(2);
            profitsOutput += `<b>${signal}</b> - Strike: ${strike} | Initial LTP: ${initialLtp?.toFixed(2)} | Live LTP: ${liveLtp?.toFixed(2)} | Profit × 75 = ₹${totalProfit} <br>`;
        });

        let ceMinus300 = data.find(r => r.strike_price === atmStrike - 300);
        if(ceMinus300){
            let base = initialLTP[atmStrike - 300]?.CE;
            if(base){
                let gainPct = ((ceMinus300.CE_LTP - base) / base) * 100;
                let profit = (ceMinus300.CE_LTP - base) * 75;
                profitsOutput += `<b>CE -300</b> Profit: ₹${profit.toFixed(2)} (Gain: ${gainPct.toFixed(1)}%)<br>`;
            }
        }

        let pePlus300 = data.find(r => r.strike_price === atmStrike + 300);
        if(pePlus300){
            let base = initialLTP[atmStrike + 300]?.PE;
            if(base){
                let gainPct = ((pePlus300.PE_LTP - base) / base) * 100;
                let profit = (pePlus300.PE_LTP - base) * 75;
                profitsOutput += `<b>PE +300</b> Profit: ₹${profit.toFixed(2)} (Gain: ${gainPct.toFixed(1)}%)<br>`;
            }
        }

        profitsDiv.innerHTML = profitsOutput || "No profits to show.";

        data.forEach(row=>{
            let cls = "";
            let CE_display = row.CE_LTP;
            let PE_display = row.PE_LTP;

            if(row.strike_price === atmStrike){
                cls = "atm";
                CE_display = `${initialLTP[atmStrike]?.CE} / ${atmLive?.CE_LTP}`;
                PE_display = `${initialLTP[atmStrike]?.PE} / ${atmLive?.PE_LTP}`;
            }

            if(row.strike_price === atmStrike - 300){
                cls = "ceMinus300";
                let base = initialLTP[row.strike_price]?.CE;
                if(base){
                    let gainPct = ((row.CE_LTP - base) / base) * 100;
                    let steps = Math.floor(gainPct / 25);
                    CE_display = `${base} / ${row.CE_LTP} (${steps*25}% crossed)`;
                }
                let basePE = initialLTP[row.strike_price]?.PE;
                if(basePE){
                    PE_display = `${basePE} / ${row.PE_LTP}`;
                }
            }

            if(row.strike_price === atmStrike + 300){
                cls = "pePlus300";
                let base = initialLTP[row.strike_price]?.PE;
                if(base){
                    let gainPct = ((row.PE_LTP - base) / base) * 100;
                    let steps = Math.floor(gainPct / 25);
                    PE_display = `${base} / ${row.PE_LTP} (${steps*25}% crossed)`;
                }
                let baseCE = initialLTP[row.strike_price]?.CE;
                if(baseCE){
                    CE_display = `${baseCE} / ${row.CE_LTP}`;
                }
            }

            tbl.innerHTML += `<tr class="${cls}"><td>${row.strike_price}</td><td>${CE_display}</td><td>${PE_display}</td></tr>`;
        });
    }

    setInterval(fetchChain, 2000);
    setInterval(checkBotStatus, 3000);
    window.onload = function(){
        fetchChain();
        checkBotStatus();
    };

    async function resetOrders(){
        let res = await fetch("/reset", {method: "POST"});
        let data = await res.json();
        alert(data.message);
        atmStrike = null;
        initialLTP = {};
        return false;
    }
  </script>
</head>
<body>
  <div class="header">
    <h1>Sajid Shaikh Algo Software : +91 9834370368</h1>
    <div>
      <span>Welcome, <strong>{{ username }}</strong>!</span>
      <a href="/setup_credentials" class="cred-btn">⚙️ Credentials</a>
      <a href="/logout" class="logout-btn">Logout</a>
    </div>
  </div>
  
  <div class="bot-control">
    <div id="botStatus">
      <span class="bot-status status-stopped">⏸️ Bot Stopped</span>
    </div>
    <p style="margin: 10px 0; color: #666;">
      ℹ️ Start background bot to run continuously even when browser is minimized/closed
    </p>
    <button id="startBtn" class="btn-start" onclick="startBackgroundBot()">▶️ Start Background Bot</button>
    <button id="stopBtn" class="btn-stop" onclick="stopBackgroundBot()" disabled>⏸️ Stop Bot</button>
    <a href="/fyers_login" target="_blank">🔑 Fyers Login</a>
  </div>

  <form method="POST" action="/">
    <label>PHBKNHP8N3</label>
  <label>ACRN7OO49D</label>

  
    <label>CE Threshold (+ over initial):</label>
    <input type="number" id="atm_ce_plus20" name="atm_ce_plus20" step="0.1" value="{{ atm_ce_plus20 }}" required>
    <label>PE Threshold (+ over initial):</label>
    <input type="number" id="atm_pe_plus20" name="atm_pe_plus20" step="0.1" value="{{ atm_pe_plus20 }}" required>
    <br><br>
    <label>Symbol Prefix:</label>
    <input type="text" id="symbol_prefix" name="symbol_prefix" value="{{ symbol_prefix }}" required>
    <button type="submit" class="btn-reset">Update Settings</button>
  </form>

  <form onsubmit="return resetOrders();">
    <button type="submit" class="btn-reset">🔄 Reset Orders</button>
  </form>

  <div id="signals"></div>
  <div id="profits"></div>
  <h3>Option Chain</h3>
  <table>
    <thead><tr><th>Strike</th><th>CE LTP / Live</th><th>PE LTP / Live</th></tr></thead>
    <tbody id="chain"></tbody>
  </table>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    print("\n" + "="*60)
    print("🚀 Sajid Shaikh Algo Trading Bot - Multi User")
    print("="*60)
    print(f"📍 Server: http://127.0.0.1:{port}")
    print("📝 Users stored in: users.txt")
    print("🔑 Credentials stored in: user_credentials.txt")
    print("="*60 + "\n")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
