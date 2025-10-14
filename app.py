from fyers_apiv3 import fyersModel
from flask import Flask, redirect, request, render_template_string
import webbrowser
import pandas as pd
import os
import math
import traceback
import json
from datetime import datetime
from collections import deque
import pytz  # Added for timezone handling

# ---- Timezone Function ----
def get_mumbai_time():
    """Get current time in Mumbai (IST) timezone"""
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist)

# ---- Credentials ----
client_id = "VMS68P9EK0-100"
secret_key = "ZJ0CFWZEL1"
redirect_uri = "http://localhost:5000/callback"

# ---- Session ----
appSession = fyersModel.SessionModel(
    client_id=client_id,
    secret_key=secret_key,
    redirect_uri=redirect_uri,
    response_type="code",
    grant_type="authorization_code",
    state="sample"
)

# ---- Flask ----
app = Flask(__name__)
app.secret_key = "sajid_secret"
fyers = None

# ---- Symbol Mapping ----
symbols_map = {
    "NIFTY50": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "MIDCAPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX"
}

display_cols = ["ask", "bid", "ltp", "ltpch", "option_type", "strike_price",
                "oi", "oich", "oichp", "prev_oi", "volume"]

previous_data = {}  # Store previous rows for diff
scalping_positions = {}  # Store active scalping positions

# ---- Historical Data Storage ----
# Structure: {index_name: {strike_type_key: deque([(timestamp, volume, oi), ...])}}
historical_data = {}
TRACKING_INTERVALS = [1, 2, 5, 10]  # Minutes to track

def format_to_crore(value):
    """Format a number to crore (10 million) units"""
    if pd.isna(value) or value == 0:
        return "0.00"
    return f"{value/10000000:.2f} Cr"

def get_strike_key(strike, option_type):
    """Generate unique key for strike-option combination"""
    return f"{strike}_{option_type}"

def update_historical_data(index_name, strike, option_type, volume, oi):
    """Store historical volume and OI data"""
    if index_name not in historical_data:
        historical_data[index_name] = {}

    key = get_strike_key(strike, option_type)
    if key not in historical_data[index_name]:
        historical_data[index_name][key] = deque(maxlen=600)  # Keep 10 minutes at 1sec intervals

    # Use Mumbai time instead of local time
    timestamp = get_mumbai_time().timestamp()
    historical_data[index_name][key].append((timestamp, volume, oi))

def get_change_data(index_name, strike, option_type, minutes):
    """Calculate volume and OI change over specified minutes"""
    if index_name not in historical_data:
        return None, None

    key = get_strike_key(strike, option_type)
    if key not in historical_data[index_name]:
        return None, None

    data_queue = historical_data[index_name][key]
    if len(data_queue) < 2:
        return None, None

    # Use Mumbai time instead of local time
    current_time = get_mumbai_time().timestamp()
    target_time = current_time - (minutes * 60)

    # Get most recent data
    current_timestamp, current_volume, current_oi = data_queue[-1]

    # Find data point closest to target time
    old_data = None
    for timestamp, volume, oi in data_queue:
        if timestamp >= target_time:
            old_data = (timestamp, volume, oi)
            break

    if old_data is None:
        # Use oldest available data if not enough history
        old_data = data_queue[0]

    old_timestamp, old_volume, old_oi = old_data

    volume_change = current_volume - old_volume
    oi_change = current_oi - old_oi

    return volume_change, oi_change

@app.route("/")
def home():
    return """<center>
    <h1 style="fond-size:80;color:green">Sajid Shaikh Algo Software : +91 9834370368</h1></center>
    <a href="/login" target="_blank">🔑 Login</a> |
    <a href="/chain?index=NIFTY50" target="_blank">📊 View Option Chain</a> |
    <a href="/scalping?index=NIFTY50" target="_blank">⚡ Scalping Dashboard</a>
    <hr>
    <p>Use the dropdown on pages to switch indices. Auto-refresh every second.</p>
    """

@app.route("/login")
def login():
    login_url = appSession.generate_authcode()
    webbrowser.open(login_url, new=1)
    return redirect(login_url)

@app.route("/callback")
def callback():
    global fyers
    auth_code = request.args.get("auth_code")
    if auth_code:
        try:
            appSession.set_token(auth_code)
            token_response = appSession.generate_token()
            access_token = token_response.get("access_token")
            fyers = fyersModel.FyersModel(client_id=client_id, token=access_token, is_async=False)
            return "<h2>✅ Authentication Successful! You can return to the app 🚀</h2>"
        except Exception as e:
            return f"<h3>Callback error: {str(e)}</h3>"
    return "❌ Authentication failed. Please retry."

@app.route("/scalping")
def scalping_dashboard():
    global fyers
    if fyers is None:
        return "<h3>⚠ Please <a href='/login'>login</a> first!</h3>"

    index_name = request.args.get("index", "NIFTY50")
    vol_interval = int(request.args.get("vol_interval", 1))
    oi_interval = int(request.args.get("oi_interval", 1))

    html = f"""
    <!doctype html>
    <html>
    <head>
        <title>{index_name} Scalping Dashboard</title>
        <style>
            body {{ font-family: Arial, sans-serif; padding: 16px; background: #f5f5f5; }}
            h2 {{ text-align:center; color:#1a73e8; }}
            .container {{ max-width: 1600px; margin: 0 auto; }}
            .dropdown {{ margin:12px 0; text-align:center; background: white; padding: 15px; border-radius: 8px; }}

            .positions-section {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; }}
            .positions-table {{ width:100%; border-collapse: collapse; font-size:13px; }}
            .positions-table th {{ background:#1a73e8; color:#fff; padding: 10px; text-align: center; }}
            .positions-table td {{ border:1px solid #ddd; padding:8px; text-align:center; }}
            .positions-table tr:nth-child(even) {{ background:#f7f7f7; }}

            .profit {{ color: #0f9d58; font-weight: bold; }}
            .loss {{ color: #db4437; font-weight: bold; }}
            .neutral {{ color: #666; }}

            .btn {{ padding: 8px 16px; margin: 4px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; }}
            .btn-buy {{ background: #0f9d58; color: white; }}
            .btn-sell {{ background: #db4437; color: white; }}
            .btn-exit {{ background: #f4b400; color: white; }}
            .btn-clear {{ background: #666; color: white; }}

            .opportunities {{ background: white; padding: 20px; border-radius: 8px; margin: 20px 0; overflow-x: auto; }}
            .opp-table {{ width:100%; border-collapse: collapse; font-size:12px; }}
            .opp-table th {{ background:#f4b400; color:#000; padding: 10px; text-align: center; }}
            .opp-table td {{ border:1px solid #ddd; padding:8px; text-align:center; }}

            .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }}
            .stat-card {{ background: white; padding: 15px; border-radius: 8px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            .stat-value {{ font-size: 24px; font-weight: bold; margin: 10px 0; }}
            .stat-label {{ color: #666; font-size: 14px; }}

            .interval-selector {{ display: inline-block; margin: 0 10px; }}
            .interval-selector label {{ font-weight: bold; margin-right: 5px; }}
            .interval-selector select {{ padding: 5px; border-radius: 4px; }}

            /* Highlight styles for highest values */
            .highest-volume {{ background-color: #e3f2fd !important; font-weight: bold; color: #0d47a1; }}
            .highest-vol-change {{ background-color: #e8f5e9 !important; font-weight: bold; color: #1b5e20; }}
            .highest-oi {{ background-color: #fff3e0 !important; font-weight: bold; color: #e65100; }}
            .highest-oi-change {{ background-color: #fce4ec !important; font-weight: bold: color: #880e4f; }}
        </style>
    </head>
    <body>
        <div class="container">
        <center><h1 aligh=center style="color:green;fond-size:70">Sajid Shaikh | (+91) 9834370368</h1></center>
            <h2>⚡ {index_name} Scalping Dashboard</h2>

            <div class="dropdown">
                <form method="get" action="/scalping" id="mainForm">
                    <label for="index">Select Index: </label>
                    <select name="index" id="index" onchange="this.form.submit()">
                        <option value="NIFTY50" {"selected" if index_name=="NIFTY50" else ""}>NIFTY50</option>
                        <option value="BANKNIFTY" {"selected" if index_name=="BANKNIFTY" else ""}>BANKNIFTY</option>
                        <option value="FINNIFTY" {"selected" if index_name=="FINNIFTY" else ""}>FINNIFTY</option>
                        <option value="MIDCAPNIFTY" {"selected" if index_name=="MIDCAPNIFTY" else ""}>MIDCAPNIFTY</option>
                        <option value="SENSEX" {"selected" if index_name=="SENSEX" else ""}>SENSEX</option>
                    </select>

                    <div class="interval-selector">
                        <label for="vol_interval">Volume Δ Interval:</label>
                        <select name="vol_interval" id="vol_interval" onchange="this.form.submit()">
                            <option value="1" {"selected" if vol_interval==1 else ""}>1 min</option>
                            <option value="2" {"selected" if vol_interval==2 else ""}>2 min</option>
                            <option value="5" {"selected" if vol_interval==5 else ""}>5 min</option>
                            <option value="10" {"selected" if vol_interval==10 else ""}>10 min</option>
                        </select>
                    </div>

                    <div class="interval-selector">
                        <label for="oi_interval">OI Δ Interval:</label>
                        <select name="oi_interval" id="oi_interval" onchange="this.form.submit()">
                            <option value="1" {"selected" if oi_interval==1 else ""}>1 min</option>
                            <option value="2" {"selected" if oi_interval==2 else ""}>2 min</option>
                            <option value="5" {"selected" if oi_interval==5 else ""}>5 min</option>
                            <option value="10" {"selected" if oi_interval==10 else ""}>10 min</option>
                        </select>
                    </div>

                    <button type="button" class="btn btn-clear" onclick="clearAllPositions()">Clear All Positions</button>
                </form>
            </div>

            <div class="stats" id="stats-section">
                <div class="stat-card">
                    <div class="stat-label">Active Positions</div>
                    <div class="stat-value" id="active-count">0</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Total P&L</div>
                    <div class="stat-value" id="total-pnl">₹0.00</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Spot Price</div>
                    <div class="stat-value" id="spot-price">-</div>
                </div>
            </div>

            <div class="positions-section">
                <h3>📊 Active Positions (Qty: 75)</h3>
                <table class="positions-table">
                    <thead>
                        <tr>
                            <th>Strike</th>
                            <th>Type</th>
                            <th>Entry LTP</th>
                            <th>Current LTP</th>
                            <th>P&L per Lot</th>
                            <th>Entry Time (IST)</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody id="positions-body">
                        <tr><td colspan="7">No active positions. Add from opportunities below.</td></tr>
                    </tbody>
                </table>
            </div>

            <div class="opportunities">
                <h3>🎯 Scalping Opportunities (ATM ±2 strikes)</h3>
                <table class="opp-table">
                    <thead>
                        <tr>
                            <th>Strike</th>
                            <th>Type</th>
                            <th>LTP</th>
                            <th>Volume (Cr)</th>
                            <th>Vol Δ ({vol_interval}m)</th>
                            <th>OI (Cr)</th>
                            <th>OI Δ ({oi_interval}m)</th>
                            <th>OI Change %</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody id="opportunities-body">
                        <tr><td colspan="9">Loading...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <script>
            const indexName = "{index_name}";
            const volInterval = {vol_interval};
            const oiInterval = {oi_interval};
            const LOT_SIZE = 75;

            function addPosition(strike, type, ltp) {{
                fetch(`/add_position?index=${{indexName}}&strike=${{strike}}&type=${{type}}&ltp=${{ltp}}`, {{
                    method: 'POST'
                }}).then(() => refreshData());
            }}

            function exitPosition(posId) {{
                fetch(`/exit_position?index=${{indexName}}&id=${{posId}}`, {{
                    method: 'POST'
                }}).then(() => refreshData());
            }}

            function clearAllPositions() {{
                if (confirm('Clear all positions for ' + indexName + '?')) {{
                    fetch(`/clear_positions?index=${{indexName}}`, {{
                        method: 'POST'
                    }}).then(() => refreshData());
                }}
            }}

            async function refreshData() {{
                try {{
                    const resp = await fetch(`/scalping_data?index=${{indexName}}&vol_interval=${{volInterval}}&oi_interval=${{oiInterval}}`);
                    const data = await resp.json();

                    document.getElementById('positions-body').innerHTML = data.positions;
                    document.getElementById('opportunities-body').innerHTML = data.opportunities;
                    document.getElementById('active-count').innerText = data.active_count;
                    document.getElementById('total-pnl').innerText = data.total_pnl;
                    document.getElementById('total-pnl').className = 'stat-value ' + (data.total_pnl_num >= 0 ? 'profit' : 'loss');
                    document.getElementById('spot-price').innerText = data.spot_price;
                }} catch (err) {{
                    console.error("Error refreshing data:", err);
                }}
            }}

            setInterval(refreshData, 1000);
            refreshData();
        </script>
    </body>
    </html>
    """
    return render_template_string(html)

@app.route("/add_position", methods=["POST"])
def add_position():
    global scalping_positions
    index_name = request.args.get("index", "NIFTY50")
    strike = float(request.args.get("strike"))
    option_type = request.args.get("type")
    ltp = float(request.args.get("ltp"))

    if index_name not in scalping_positions:
        scalping_positions[index_name] = []

    # Use Mumbai time instead of local time
    mumbai_time = get_mumbai_time()
    pos_id = f"{strike}_{option_type}_{mumbai_time.timestamp()}"
    position = {
        "id": pos_id,
        "strike": strike,
        "type": option_type,
        "entry_ltp": ltp,
        "entry_time": mumbai_time.strftime("%H:%M:%S"),
        "lot_size": 75
    }
    scalping_positions[index_name].append(position)

    return json.dumps({"status": "success"})

@app.route("/exit_position", methods=["POST"])
def exit_position():
    global scalping_positions
    index_name = request.args.get("index", "NIFTY50")
    pos_id = request.args.get("id")

    if index_name in scalping_positions:
        scalping_positions[index_name] = [p for p in scalping_positions[index_name] if p["id"] != pos_id]

    return json.dumps({"status": "success"})

@app.route("/clear_positions", methods=["POST"])
def clear_positions():
    global scalping_positions
    index_name = request.args.get("index", "NIFTY50")
    scalping_positions[index_name] = []
    return json.dumps({"status": "success"})

@app.route("/scalping_data")
def scalping_data():
    global fyers, scalping_positions
    index_name = request.args.get("index", "NIFTY50")
    vol_interval = int(request.args.get("vol_interval", 1))
    oi_interval = int(request.args.get("oi_interval", 1))
    symbol = symbols_map.get(index_name, "NSE:NIFTY50-INDEX")

    try:
        data = {"symbol": symbol, "strikecount": 50}
        response = fyers.optionchain(data=data)
        data_section = response.get("data", {}) if isinstance(response, dict) else {}
        options_data = data_section.get("optionsChain") or data_section.get("options_chain") or []

        if not options_data:
            return json.dumps({"positions": "", "opportunities": "", "active_count": 0, "total_pnl": "₹0.00", "total_pnl_num": 0, "spot_price": "-"})

        df = pd.json_normalize(options_data)
        if "strike_price" not in df.columns:
            possible_strike_cols = [c for c in df.columns if "strike" in c.lower()]
            if possible_strike_cols:
                df = df.rename(columns={possible_strike_cols[0]: "strike_price"})

        num_cols = ["strike_price", "ltp", "oi", "oich", "oichp", "volume"]
        for col in num_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        spot_price = None
        for key in ("underlying_value", "underlyingValue", "underlying", "underlying_value_instrument"):
            if data_section.get(key) is not None:
                try:
                    spot_price = float(data_section.get(key))
                    break
                except Exception:
                    pass

        strikes_all = sorted(df["strike_price"].dropna().unique())
        if spot_price is None:
            spot_price = float(strikes_all[len(strikes_all)//2]) if strikes_all else 0

        atm_strike = min(strikes_all, key=lambda s: abs(s - spot_price)) if strikes_all else 0
        atm_index = strikes_all.index(atm_strike) if atm_strike in strikes_all else 0
        low = max(0, atm_index - 2)
        high = min(len(strikes_all), atm_index + 3)
        strikes_to_show = strikes_all[low:high]

        # Generate positions HTML
        positions_html = ""
        total_pnl = 0
        active_positions = scalping_positions.get(index_name, [])

        for pos in active_positions:
            strike = pos["strike"]
            option_type = pos["type"]
            entry_ltp = pos["entry_ltp"]

            current_row = df[(df["strike_price"] == strike) & (df["option_type"] == option_type)]
            current_ltp = current_row["ltp"].values[0] if not current_row.empty and "ltp" in current_row.columns else entry_ltp

            pnl = (current_ltp - entry_ltp) * pos["lot_size"]
            total_pnl += pnl

            pnl_class = "profit" if pnl >= 0 else "loss"
            pnl_symbol = "+" if pnl >= 0 else ""

            positions_html += f"""
            <tr>
                <td><b>{strike}</b></td>
                <td>{option_type}</td>
                <td>₹{entry_ltp:.2f}</td>
                <td>₹{current_ltp:.2f}</td>
                <td class="{pnl_class}">{pnl_symbol}₹{pnl:.2f}</td>
                <td>{pos['entry_time']}</td>
                <td><button class="btn btn-exit" onclick="exitPosition('{pos['id']}')">Exit</button></td>
            </tr>
            """

        if not positions_html:
            positions_html = "<tr><td colspan='7'>No active positions. Add from opportunities below.</td></tr>"

        # Generate opportunities HTML with change tracking
        opportunities_html = ""
        opp_df = df[df["strike_price"].isin(strikes_to_show)]

        # Track highest values
        highest_volume = {"value": 0, "strike": None, "type": None}
        highest_vol_change = {"value": 0, "strike": None, "type": None}
        highest_oi = {"value": 0, "strike": None, "type": None}
        highest_oi_change = {"value": 0, "strike": None, "type": None}

        # First pass to collect all data and find highest values
        temp_data = []
        for _, row in opp_df.iterrows():
            strike = row.get("strike_price", 0)
            option_type = row.get("option_type", "")
            ltp = row.get("ltp", 0)
            volume = row.get("volume", 0)
            oi = row.get("oi", 0)
            oichp = row.get("oichp", 0)

            # Update historical data
            update_historical_data(index_name, strike, option_type, volume, oi)

            # Get volume and OI changes
            vol_change, _ = get_change_data(index_name, strike, option_type, vol_interval)
            _, oi_change = get_change_data(index_name, strike, option_type, oi_interval)

            # Store temp data
            temp_data.append({
                "strike": strike,
                "option_type": option_type,
                "ltp": ltp,
                "volume": volume,
                "vol_change": vol_change,
                "oi": oi,
                "oi_change": oi_change,
                "oichp": oichp
            })

            # Track highest values
            if volume > highest_volume["value"]:
                highest_volume = {"value": volume, "strike": strike, "type": option_type}

            if vol_change is not None and vol_change > highest_vol_change["value"]:
                highest_vol_change = {"value": vol_change, "strike": strike, "type": option_type}

            if oi > highest_oi["value"]:
                highest_oi = {"value": oi, "strike": strike, "type": option_type}

            if oi_change is not None and oi_change > highest_oi_change["value"]:
                highest_oi_change = {"value": oi_change, "strike": strike, "type": option_type}

        # Second pass to generate HTML with highlighting
        for data in temp_data:
            strike = data["strike"]
            option_type = data["option_type"]
            ltp = data["ltp"]
            volume = data["volume"]
            vol_change = data["vol_change"]
            oi = data["oi"]
            oi_change = data["oi_change"]
            oichp = data["oichp"]

            # Format values in crore
            volume_cr = format_to_crore(volume)
            oi_cr = format_to_crore(oi)

            # Format changes
            vol_change_str = f"{vol_change:+,.0f}" if vol_change is not None else "N/A"
            vol_change_class = "profit" if (vol_change or 0) > 0 else ("loss" if (vol_change or 0) < 0 else "neutral")

            # Check if this is the highest volume
            if strike == highest_volume["strike"] and option_type == highest_volume["type"]:
                volume_class = "highest-volume"
            else:
                volume_class = ""

            # Check if this is the highest volume change
            if strike == highest_vol_change["strike"] and option_type == highest_vol_change["type"]:
                vol_change_class = "highest-vol-change"
            elif (vol_change or 0) > 0:
                vol_change_class = "profit"
            elif (vol_change or 0) < 0:
                vol_change_class = "loss"
            else:
                vol_change_class = "neutral"

            oi_change_str = f"{oi_change:+,.0f}" if oi_change is not None else "N/A"

            # Check if this is the highest OI
            if strike == highest_oi["strike"] and option_type == highest_oi["type"]:
                oi_class = "highest-oi"
            else:
                oi_class = ""

            # Check if this is the highest OI change
            if strike == highest_oi_change["strike"] and option_type == highest_oi_change["type"]:
                oi_change_class = "highest-oi-change"
            elif (oi_change or 0) > 0:
                oi_change_class = "profit"
            elif (oi_change or 0) < 0:
                oi_change_class = "loss"
            else:
                oi_change_class = "neutral"

            opportunities_html += f"""
            <tr>
                <td><b>{strike}</b></td>
                <td>{option_type}</td>
                <td>₹{ltp:.2f}</td>
                <td class="{volume_class}">{volume_cr}</td>
                <td class="{vol_change_class}">{vol_change_str}</td>
                <td class="{oi_class}">{oi_cr}</td>
                <td class="{oi_change_class}">{oi_change_str}</td>
                <td>{oichp:.2f}%</td>
                <td>
                    <button class="btn btn-buy" onclick="addPosition({strike}, '{option_type}', {ltp})">Add Position</button>
                </td>
            </tr>
            """

        total_pnl_str = f"₹{total_pnl:,.2f}" if total_pnl >= 0 else f"-₹{abs(total_pnl):,.2f}"

        return json.dumps({
            "positions": positions_html,
            "opportunities": opportunities_html,
            "active_count": len(active_positions),
            "total_pnl": total_pnl_str,
            "total_pnl_num": total_pnl,
            "spot_price": f"₹{spot_price:,.2f}"
        })

    except Exception as e:
        return json.dumps({
            "positions": f"<tr><td colspan='7'>Error: {str(e)}</td></tr>",
            "opportunities": f"<tr><td colspan='9'>Error: {str(e)}</td></tr>",
            "active_count": 0,
            "total_pnl": "₹0.00",
            "total_pnl_num": 0,
            "spot_price": "-"
        })

@app.route("/chain")
def fetch_option_chain():
    global fyers
    if fyers is None:
        return "<h3>⚠ Please <a href='/login'>login</a> first!</h3>"

    index_name = request.args.get("index", "NIFTY50")
    vol_interval = int(request.args.get("vol_interval", 1))
    oi_interval = int(request.args.get("oi_interval", 1))
    symbol = symbols_map.get(index_name, "NSE:NIFTY50-INDEX")

    try:
        table_html, spot_price, analysis_html, ce_headers, pe_headers = generate_full_table(index_name, symbol, vol_interval, oi_interval)
    except Exception as e:
        table_html = f"<p>Error fetching option chain: {str(e)}</p>"
        spot_price = ""
        analysis_html = ""

    html = f"""
    <!doctype html>
    <html>
    <head>
        <title>{index_name} Option Chain (ATM ±3)</title>
        <style>
            body {{ font-family: Arial, sans-serif; padding: 16px; }}
            h2 {{ text-align:center; color:#1a73e8; }}
            table {{ width:100%; border-collapse: collapse; font-size:12px; }}
            th, td {{ border:1px solid #ddd; padding:6px; text-align:center; }}
            th {{ background:#1a73e8; color:#fff; }}
            tr:nth-child(even) {{ background:#f7f7f7; }}
            .dropdown {{ margin:12px 0; text-align:center; }}
            #analysis {{ background:#eef; padding:10px; border-radius:5px; margin-top:15px; }}
            .profit {{ color: #0f9d58; font-weight: bold; }}
            .loss {{ color: #db4437; font-weight: bold; }}
            .neutral {{ color: #666; }}
            .interval-selector {{ display: inline-block; margin: 0 10px; }}
            .interval-selector label {{ font-weight: bold; margin-right: 5px; }}
            .interval-selector select {{ padding: 5px; border-radius: 4px; }}
        </style>
    </head>
    <body>
        <h2 id="spot-title">{index_name} Option Chain (ATM ±3) — Spot: {spot_price}</h2>

        <div class="dropdown">
            <form method="get" action="/chain">
                <label for="index">Select Index: </label>
                <select name="index" id="index" onchange="this.form.submit()">
                    <option value="NIFTY50" {"selected" if index_name=="NIFTY50" else ""}>NIFTY50</option>
                    <option value="BANKNIFTY" {"selected" if index_name=="BANKNIFTY" else ""}>BANKNIFTY</option>
                    <option value="FINNIFTY" {"selected" if index_name=="FINNIFTY" else ""}>FINNIFTY</option>
                    <option value="MIDCAPNIFTY" {"selected" if index_name=="MIDCAPNIFTY" else ""}>MIDCAPNIFTY</option>
                    <option value="SENSEX" {"selected" if index_name=="SENSEX" else ""}>SENSEX</option>
                </select>

                <div class="interval-selector">
                    <label for="vol_interval">Volume Δ:</label>
                    <select name="vol_interval" id="vol_interval" onchange="this.form.submit()">
                        <option value="1" {"selected" if vol_interval==1 else ""}>1 min</option>
                        <option value="2" {"selected" if vol_interval==2 else ""}>2 min</option>
                        <option value="5" {"selected" if vol_interval==5 else ""}>5 min</option>
                        <option value="10" {"selected" if vol_interval==10 else ""}>10 min</option>
                    </select>
                </div>

                <div class="interval-selector">
                    <label for="oi_interval">OI Δ:</label>
                    <select name="oi_interval" id="oi_interval" onchange="this.form.submit()">
                        <option value="1" {"selected" if oi_interval==1 else ""}>1 min</option>
                        <option value="2" {"selected" if oi_interval==2 else ""}>2 min</option>
                        <option value="5" {"selected" if oi_interval==5 else ""}>5 min</option>
                        <option value="10" {"selected" if oi_interval==10 else ""}>10 min</option>
                    </select>
                </div>
            </form>
        </div>

        <table id="option-chain-table">
            <thead><tr>{ce_headers}<th>STRIKE</th>{pe_headers}</tr></thead>
            <tbody>{table_html}</tbody>
        </table>

        <div id="analysis">{analysis_html}</div>

        <script>
            const indexName = "{index_name}";
            const volInterval = {vol_interval};
            const oiInterval = {oi_interval};

            async function refreshTableRows() {{
                try {{
                    const resp = await fetch(`/chain_rows_diff?index=${{indexName}}&vol_interval=${{volInterval}}&oi_interval=${{oiInterval}}`);
                    const result = await resp.json();
                    if (result.rows) {{
                        document.querySelector("#option-chain-table tbody").innerHTML = result.rows;
                        document.querySelector("#spot-title").innerHTML = `${{indexName}} Option Chain (ATM ±3) — Spot: ${{result.spot}}`;
                        document.querySelector("#analysis").innerHTML = result.analysis;
                    }}
                }} catch (err) {{
                    console.error("Error refreshing rows:", err);
                }}
            }}
            setInterval(refreshTableRows, 1000);
        </script>
    </body>
    </html>
    """
    return render_template_string(html)

@app.route("/chain_rows_diff")
def chain_rows_diff():
    global previous_data
    index_name = request.args.get("index", "NIFTY50")
    vol_interval = int(request.args.get("vol_interval", 1))
    oi_interval = int(request.args.get("oi_interval", 1))
    symbol = symbols_map.get(index_name, "NSE:NIFTY50-INDEX")

    rows_html, spot_price, analysis_html, _, _ = generate_rows(index_name, symbol, vol_interval, oi_interval)

    current_data = {"rows": rows_html, "analysis": analysis_html}

    diff_rows = ""
    if previous_data.get(index_name) != current_data["rows"]:
        diff_rows = current_data["rows"]
        previous_data[index_name] = current_data["rows"]

    return json.dumps({"rows": diff_rows, "spot": spot_price, "analysis": analysis_html})

def generate_full_table(index_name, symbol, vol_interval, oi_interval):
    rows_html, spot_price, analysis_html, ce_headers, pe_headers = generate_rows(index_name, symbol, vol_interval, oi_interval)
    return rows_html, spot_price, analysis_html, ce_headers, pe_headers

def generate_rows(index_name, symbol, vol_interval, oi_interval):
    global fyers
    data = {"symbol": symbol, "strikecount": 50}
    response = fyers.optionchain(data=data)
    data_section = response.get("data", {}) if isinstance(response, dict) else {}
    options_data = data_section.get("optionsChain") or data_section.get("options_chain") or []

    if not options_data:
        return "", "", "<p>No option chain data available.</p>", "", ""

    df = pd.json_normalize(options_data)
    if "strike_price" not in df.columns:
        possible_strike_cols = [c for c in df.columns if "strike" in c.lower()]
        if possible_strike_cols:
            df = df.rename(columns={possible_strike_cols[0]: "strike_price"})

    num_cols = ["strike_price", "ask", "bid", "ltp", "oi", "oich", "oichp", "prev_oi", "volume", "ltpch"]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    spot_price = None
    for key in ("underlying_value", "underlyingValue", "underlying", "underlying_value_instrument"):
        if data_section.get(key) is not None:
            try:
                spot_price = float(data_section.get(key))
                break
            except Exception:
                pass

    strikes_all = sorted(df["strike_price"].dropna().unique())
    if spot_price is None:
        spot_price = float(strikes_all[len(strikes_all)//2]) if strikes_all else 0

    atm_strike = min(strikes_all, key=lambda s: abs(s - spot_price)) if strikes_all else 0
    atm_index = strikes_all.index(atm_strike) if atm_strike in strikes_all else 0
    low = max(0, atm_index - 3)
    high = min(len(strikes_all), atm_index + 4)
    strikes_to_show = strikes_all[low:high] if strikes_all else []

    df = df[df["strike_price"].isin(strikes_to_show)]
    ce_df = df[df["option_type"] == "CE"].set_index("strike_price", drop=False) if "option_type" in df.columns else pd.DataFrame()
    pe_df = df[df["option_type"] == "PE"].set_index("strike_price", drop=False) if "option_type" in df.columns else pd.DataFrame()

    lr_cols = [c for c in ["ask", "bid", "ltp", "ltpch", "volume", "vol_change", "oi", "oi_change", "oich", "oichp", "prev_oi"] if c in df.columns or c in ["vol_change", "oi_change"]]

    ce_itm_df = ce_df[ce_df["strike_price"] < spot_price] if not ce_df.empty else pd.DataFrame()
    pe_itm_df = pe_df[pe_df["strike_price"] > spot_price] if not pe_df.empty else pd.DataFrame()

    rows_html = ""
    for strike in strikes_to_show:
        ce_cells = ""
        pe_cells = ""

        for c in lr_cols:
            if c == "vol_change":
                # CE Volume Change
                if not ce_df.empty and strike in ce_df.index:
                    ce_volume = ce_df.loc[strike, "volume"] if "volume" in ce_df.columns else 0
                    update_historical_data(index_name, strike, "CE", ce_volume, ce_df.loc[strike, "oi"] if "oi" in ce_df.columns else 0)
                    vol_change, _ = get_change_data(index_name, strike, "CE", vol_interval)
                    if vol_change is not None:
                        vol_class = "profit" if vol_change > 0 else ("loss" if vol_change < 0 else "neutral")
                        ce_cells += f"<td class='{vol_class}'>{vol_change:+,.0f}</td>"
                    else:
                        ce_cells += "<td>-</td>"
                else:
                    ce_cells += "<td>-</td>"

                # PE Volume Change
                if not pe_df.empty and strike in pe_df.index:
                    pe_volume = pe_df.loc[strike, "volume"] if "volume" in pe_df.columns else 0
                    update_historical_data(index_name, strike, "PE", pe_volume, pe_df.loc[strike, "oi"] if "oi" in pe_df.columns else 0)
                    vol_change, _ = get_change_data(index_name, strike, "PE", vol_interval)
                    if vol_change is not None:
                        vol_class = "profit" if vol_change > 0 else ("loss" if vol_change < 0 else "neutral")
                        pe_cells += f"<td class='{vol_class}'>{vol_change:+,.0f}</td>"
                    else:
                        pe_cells += "<td>-</td>"
                else:
                    pe_cells += "<td>-</td>"

            elif c == "oi_change":
                # CE OI Change
                if not ce_df.empty and strike in ce_df.index:
                    _, oi_change = get_change_data(index_name, strike, "CE", oi_interval)
                    if oi_change is not None:
                        oi_class = "profit" if oi_change > 0 else ("loss" if oi_change < 0 else "neutral")
                        ce_cells += f"<td class='{oi_class}'>{oi_change:+,.0f}</td>"
                    else:
                        ce_cells += "<td>-</td>"
                else:
                    ce_cells += "<td>-</td>"

                # PE OI Change
                if not pe_df.empty and strike in pe_df.index:
                    _, oi_change = get_change_data(index_name, strike, "PE", oi_interval)
                    if oi_change is not None:
                        oi_class = "profit" if oi_change > 0 else ("loss" if oi_change < 0 else "neutral")
                        pe_cells += f"<td class='{oi_class}'>{oi_change:+,.0f}</td>"
                    else:
                        pe_cells += "<td>-</td>"
                else:
                    pe_cells += "<td>-</td>"
            else:
                ce_val = ce_df.loc[strike, c] if (not ce_df.empty and strike in ce_df.index and c in ce_df.columns) else ""
                pe_val = pe_df.loc[strike, c] if (not pe_df.empty and strike in pe_df.index and c in pe_df.columns) else ""

                # Format volume and OI in crore
                if c == "volume" and ce_val != "":
                    ce_val = format_to_crore(ce_val)
                if c == "volume" and pe_val != "":
                    pe_val = format_to_crore(pe_val)
                if c == "oi" and ce_val != "":
                    ce_val = format_to_crore(ce_val)
                if c == "oi" and pe_val != "":
                    pe_val = format_to_crore(pe_val)

                ce_cells += f"<td>{ce_val}</td>"
                pe_cells += f"<td>{pe_val}</td>"

        row_style = "style='background-color: #ffeb3b; font-weight: bold;'" if strike == atm_strike else ""
        rows_html += f"<tr {row_style}>{ce_cells}<td><b>{strike}</b></td>{pe_cells}</tr>"

    # Calculate totals (excluding vol_change and oi_change from sum)
    sum_cols = [c for c in lr_cols if c not in ["vol_change", "oi_change"]]
    ce_totals = ce_df[sum_cols].sum(numeric_only=True) if not ce_df.empty else pd.Series(0, index=sum_cols)
    pe_totals = pe_df[sum_cols].sum(numeric_only=True) if not pe_df.empty else pd.Series(0, index=sum_cols)
    ce_itm_totals = ce_itm_df[sum_cols].sum(numeric_only=True) if not ce_itm_df.empty else pd.Series(0, index=sum_cols)
    pe_itm_totals = pe_itm_df[sum_cols].sum(numeric_only=True) if not pe_itm_df.empty else pd.Series(0, index=sum_cols)

    ce_headers, pe_headers = generate_headers(vol_interval, oi_interval)

    # CE Totals
    ce_totals_cells = ""
    for c in lr_cols:
        if c in ["vol_change", "oi_change"]:
            ce_totals_cells += "<td>-</td>"
        else:
            if c in ["volume", "oi"]:
                ce_totals_cells += f"<td><b>{format_to_crore(ce_totals[c])}</b></td>"
            else:
                ce_totals_cells += f"<td><b>{ce_totals[c]:.2f}</b></td>"
    rows_html += f"<tr style='background-color: #c8e6c9; font-weight: bold;'>{ce_totals_cells}<td>CE TOTAL</td>{'<td>-</td>' * len(lr_cols)}</tr>"

    # PE Totals
    pe_totals_cells = ""
    for c in lr_cols:
        if c in ["vol_change", "oi_change"]:
            pe_totals_cells += "<td>-</td>"
        else:
            if c in ["volume", "oi"]:
                pe_totals_cells += f"<td><b>{format_to_crore(pe_totals[c])}</b></td>"
            else:
                pe_totals_cells += f"<td><b>{pe_totals[c]:.2f}</b></td>"
    rows_html += f"<tr style='background-color: #c8e6c9; font-weight: bold;'>{'<td>-</td>' * len(lr_cols)}<td>PE TOTAL</td>{pe_totals_cells}</tr>"

    # CE ITM Totals
    ce_itm_totals_cells = ""
    for c in lr_cols:
        if c in ["vol_change", "oi_change"]:
            ce_itm_totals_cells += "<td>-</td>"
        else:
            if c in ["volume", "oi"]:
                ce_itm_totals_cells += f"<td><b>{format_to_crore(ce_itm_totals[c])}</b></td>"
            else:
                ce_itm_totals_cells += f"<td><b>{ce_itm_totals[c]:.2f}</b></td>"
    rows_html += f"<tr style='background-color: #b3e5fc; font-weight: bold;'>{ce_itm_totals_cells}<td>CE ITM TOTAL</td>{'<td>-</td>' * len(lr_cols)}</tr>"

    # PE ITM Totals
    pe_itm_totals_cells = ""
    for c in lr_cols:
        if c in ["vol_change", "oi_change"]:
            pe_itm_totals_cells += "<td>-</td>"
        else:
            if c in ["volume", "oi"]:
                pe_itm_totals_cells += f"<td><b>{format_to_crore(pe_itm_totals[c])}</b></td>"
            else:
                pe_itm_totals_cells += f"<td><b>{pe_itm_totals[c]:.2f}</b></td>"
    rows_html += f"<tr style='background-color: #b3e5fc; font-weight: bold;'>{'<td>-</td>' * len(lr_cols)}<td>PE ITM TOTAL</td>{pe_itm_totals_cells}</tr>"

    # All Totals
    all_totals = ce_totals.add(pe_totals, fill_value=0)
    all_totals_cells = ""
    for c in lr_cols:
        if c in ["vol_change", "oi_change"]:
            all_totals_cells += "<td>-</td>"
        else:
            if c in ["volume", "oi"]:
                all_totals_cells += f"<td><b>{format_to_crore(all_totals[c])}</b></td>"
            else:
                all_totals_cells += f"<td><b>{all_totals[c]:,.2f}</b></td>"
    rows_html += f"<tr style='background-color: #ffd699; font-weight: bold;'>{all_totals_cells}<td>ALL TOTAL</td>{all_totals_cells}</tr>"

    analysis_html = generate_market_insights(ce_df, pe_df, spot_price)

    return rows_html, spot_price, analysis_html, ce_headers, pe_headers

def generate_headers(vol_interval=1, oi_interval=1):
    cols = ["ASK", "BID", "LTP", "LTPCH", "VOLUME (Cr)", f"VOL Δ({vol_interval}m)", "OI (Cr)", f"OI Δ({oi_interval}m)", "OICH", "OICHP", "PREV_OI"]
    ce_headers = "".join([f"<th>{c}</th>" for c in cols])
    pe_headers = "".join([f"<th>{c}</th>" for c in cols])
    return ce_headers, pe_headers

def generate_market_insights(ce_df, pe_df, spot_price):
    try:
        total_ce_oi = ce_df["oi"].sum() if not ce_df.empty else 0
        total_pe_oi = pe_df["oi"].sum() if not pe_df.empty else 0
        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else None

        strongest_support = pe_df.loc[pe_df["oi"].idxmax(), "strike_price"] if not pe_df.empty else None
        strongest_resistance = ce_df.loc[ce_df["oi"].idxmax(), "strike_price"] if not ce_df.empty else None

        ce_vol = ce_df["volume"].sum() if not ce_df.empty else 0
        pe_vol = pe_df["volume"].sum() if not pe_df.empty else 0
        volume_trend = "CE Volume > PE Volume → Bullish" if ce_vol > pe_vol else "PE Volume > CE Volume → Bearish"

        ltp_trend = "LTP falling 📉" if (ce_df["ltpch"].mean() < 0 and pe_df["ltpch"].mean() < 0) else \
                    "LTP rising 📈" if (ce_df["ltpch"].mean() > 0 and pe_df["ltpch"].mean() > 0) else "Sideways ⚖️"

        trend_bias = ""
        if pcr is not None:
            if pcr > 1:
                trend_bias = "Bearish 📉"
            elif pcr < 0.8:
                trend_bias = "Bullish 📈"
            else:
                trend_bias = "Neutral ⚖️"

        return f"""
        <h3>🔎 Market Insights</h3>
        <ul>
            <li><b>Spot Price:</b> {spot_price}</li>
            <li><b>Total CE OI:</b> {format_to_crore(total_ce_oi)} Cr</li>
            <li><b>Total PE OI:</b> {format_to_crore(total_pe_oi)} Cr</li>
            <li><b>Put-Call Ratio (PCR):</b> {pcr}</li>
            <li><b>Volume Trend:</b> {volume_trend}</li>
            <li><b>Strongest Support (PE OI):</b> {strongest_support}</li>
            <li><b>Strongest Resistance (CE OI):</b> {strongest_resistance}</li>
            <li><b>Trend Bias:</b> {trend_bias}</li>
        </ul>
        """
    except Exception as e:
        return f"<p>Error in analysis: {e}</p>"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
