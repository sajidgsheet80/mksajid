from fyers_apiv3 import fyersModel
from flask import Flask, redirect, request, render_template_string, jsonify
import webbrowser
import pandas as pd
import os
import json
from datetime import datetime

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

symbols_map = {
    "NIFTY50": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "MIDCAPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX"
}

# Store ATM CE data history (per index)
atm_ce_history = {}

@app.route("/")
def home():
    return render_template_string(MAIN_TEMPLATE)

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
            return "<h2>✅ Authentication Successful! Return to the app 🚀</h2>"
        except Exception as e:
            return f"<h3>Callback error: {str(e)}</h3>"
    return "❌ Authentication failed. Please retry."

@app.route("/fetch_chain")
def fetch_chain():
    global fyers
    if fyers is None:
        return jsonify({"error": "⚠ Please login first!"})

    index_name = request.args.get("index", "NIFTY50")
    symbol = symbols_map.get(index_name, "NSE:NIFTY50-INDEX")

    try:
        table_html, spot_price, analysis_html, ce_headers, pe_headers, atm_ce_data = generate_full_table(index_name, symbol)
        return jsonify({
            "table": table_html,
            "spot": spot_price,
            "analysis": analysis_html,
            "ce_headers": ce_headers,
            "pe_headers": pe_headers,
            "atm_ce_data": atm_ce_data
        })
    except Exception as e:
        return jsonify({"error": str(e)})

def generate_full_table(index_name, symbol):
    rows_html, spot_price, analysis_html, ce_headers, pe_headers, atm_ce_data = generate_rows(index_name, symbol)
    return rows_html, spot_price, analysis_html, ce_headers, pe_headers, atm_ce_data

def generate_rows(index_name, symbol):
    global fyers, atm_ce_history
    data = {"symbol": symbol, "strikecount": 50}
    response = fyers.optionchain(data=data)
    data_section = response.get("data", {}) if isinstance(response, dict) else {}
    options_data = data_section.get("optionsChain") or data_section.get("options_chain") or []

    if not options_data:
        return "", "", "<p>No option chain data available.</p>", "", "", {}

    df = pd.json_normalize(options_data)
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

    df_display = df[df["strike_price"].isin(strikes_to_show)]
    ce_df = df[df["option_type"] == "CE"].set_index("strike_price", drop=False) if "option_type" in df.columns else pd.DataFrame()
    pe_df = df[df["option_type"] == "PE"].set_index("strike_price", drop=False) if "option_type" in df.columns else pd.DataFrame()
    lr_cols = [c for c in ["ask", "bid", "ltp", "ltpch", "oi", "oich", "oichp", "prev_oi", "volume"] if c in df.columns]

    # Get ATM CE data
    atm_ce_data = get_atm_ce_data(ce_df, atm_strike, index_name)

    rows_html = ""
    for strike in strikes_to_show:
        ce_cells = "".join([f"<td>{float(ce_df.loc[strike, c]) if strike in ce_df.index and c in ce_df.columns and pd.notna(ce_df.loc[strike, c]) else ''}</td>" for c in lr_cols])
        pe_cells = "".join([f"<td>{float(pe_df.loc[strike, c]) if strike in pe_df.index and c in pe_df.columns and pd.notna(pe_df.loc[strike, c]) else ''}</td>" for c in lr_cols])
        row_style = "style='background-color: #ffeb3b; font-weight: bold;'" if strike == atm_strike else ""
        rows_html += f"<tr {row_style}>{ce_cells}<td><b>{strike}</b></td>{pe_cells}</tr>"

    ce_headers, pe_headers = generate_headers()
    analysis_html = generate_market_insights(ce_df, pe_df, spot_price)
    return rows_html, spot_price, analysis_html, ce_headers, pe_headers, atm_ce_data

def get_atm_ce_data(ce_df, atm_strike, index_name):
    global atm_ce_history
    
    # Convert atm_strike to native Python type
    atm_strike = float(atm_strike) if pd.notna(atm_strike) else 0
    
    atm_ce_data = {
        "strike": atm_strike,
        "ltp": 0,
        "high": 0,
        "low": 0,
        "signal": "",
        "timestamp": datetime.now().strftime("%H:%M:%S")
    }
    
    if not ce_df.empty and atm_strike in ce_df.index:
        current_ltp = float(ce_df.loc[atm_strike, "ltp"]) if "ltp" in ce_df.columns and pd.notna(ce_df.loc[atm_strike, "ltp"]) else 0
        
        # Initialize history for this index if not exists
        if index_name not in atm_ce_history:
            atm_ce_history[index_name] = {
                "ltp_history": [],
                "high": current_ltp,
                "low": current_ltp
            }
        
        history = atm_ce_history[index_name]
        
        # Update high and low (reset every minute - keep last 60 seconds)
        if len(history["ltp_history"]) >= 30:  # 30 updates at 2-second intervals = 1 minute
            history["ltp_history"] = history["ltp_history"][-29:]  # Keep last 29
            # Recalculate high/low from history
            if history["ltp_history"]:
                history["high"] = max(history["ltp_history"])
                history["low"] = min(history["ltp_history"])
        
        # Add current LTP to history
        history["ltp_history"].append(current_ltp)
        
        # Update high and low
        if current_ltp > history["high"]:
            history["high"] = current_ltp
        if current_ltp < history["low"] or history["low"] == 0:
            history["low"] = current_ltp
        
        # Determine signal - BUY if price is rising
        signal = ""
        if len(history["ltp_history"]) >= 2:
            prev_ltp = history["ltp_history"][-2]
            if current_ltp > prev_ltp:
                signal = "🟢 BUY"
        
        atm_ce_data = {
            "strike": atm_strike,
            "ltp": round(current_ltp, 2),
            "high": round(history["high"], 2),
            "low": round(history["low"], 2),
            "signal": signal,
            "timestamp": datetime.now().strftime("%H:%M:%S")
        }
    
    return atm_ce_data

def generate_headers():
    lr_cols = ["ask", "bid", "ltp", "ltpch", "oi", "oich", "oichp", "prev_oi", "volume"]
    ce_headers = "".join([f"<th>{c.upper()}</th>" for c in lr_cols])
    pe_headers = "".join([f"<th>{c.upper()}</th>" for c in lr_cols])
    return ce_headers, pe_headers

def generate_market_insights(ce_df, pe_df, spot_price):
    try:
        total_ce_oi = float(ce_df["oi"].sum()) if not ce_df.empty else 0
        total_pe_oi = float(pe_df["oi"].sum()) if not pe_df.empty else 0
        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else None
        strongest_support = float(pe_df.loc[pe_df["oi"].idxmax(), "strike_price"]) if not pe_df.empty else None
        strongest_resistance = float(ce_df.loc[ce_df["oi"].idxmax(), "strike_price"]) if not ce_df.empty else None
        ce_vol = float(ce_df["volume"].sum()) if not ce_df.empty else 0
        pe_vol = float(pe_df["volume"].sum()) if not pe_df.empty else 0
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
            <li><b>Total CE OI:</b> {total_ce_oi:,.0f}</li>
            <li><b>Total PE OI:</b> {total_pe_oi:,.0f}</li>
            <li><b>Put-Call Ratio (PCR):</b> {pcr}</li>
            <li><b>Volume Trend:</b> {volume_trend}</li>
            <li><b>Strongest Support (PE OI):</b> {strongest_support}</li>
            <li><b>Strongest Resistance (CE OI):</b> {strongest_resistance}</li>
            <li><b>Trend Bias:</b> {trend_bias}</li>
        </ul>
        """
    except Exception as e:
        return f"<p>Error in analysis: {e}</p>"

MAIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <title>Single Page Login + Option Chain</title>
  <style>
    body { font-family: Arial, sans-serif; padding: 20px; background-color: #f5f5f5; }
    table { border-collapse: collapse; width: 100%; font-size: 12px; background-color: white; }
    th, td { border: 1px solid #aaa; padding: 6px; text-align: center; }
    th { background-color: #1a73e8; color: white; }
    tr:nth-child(even) { background-color: #f2f2f2; }
    tr.atm { background-color: #ffeb3b; font-weight: bold; }
    .msg { margin-top: 10px; font-size: 14px; }
    .atm-tracker {
      margin-top: 30px;
      padding: 20px;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      border-radius: 10px;
      box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    .atm-tracker h3 {
      color: white;
      margin-top: 0;
      text-align: center;
    }
    .atm-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 15px;
      margin-top: 15px;
    }
    .atm-card {
      background-color: white;
      padding: 15px;
      border-radius: 8px;
      box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .atm-card h4 {
      margin: 0 0 10px 0;
      color: #333;
      font-size: 14px;
      text-transform: uppercase;
    }
    .atm-card .value {
      font-size: 24px;
      font-weight: bold;
      color: #1a73e8;
    }
    .atm-card.signal {
      grid-column: 1 / -1;
      text-align: center;
      background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
    }
    .atm-card.signal .value {
      color: white;
      font-size: 32px;
      animation: pulse 1s infinite;
    }
    .atm-card.signal h4 {
      color: white;
    }
    @keyframes pulse {
      0%, 100% { transform: scale(1); }
      50% { transform: scale(1.05); }
    }
    .timestamp {
      text-align: center;
      color: white;
      margin-top: 10px;
      font-size: 12px;
    }
  </style>
</head>
<body>
  <h2>Sajid Shaikh Algo Software</h2>
  <a href="/login" target="_blank">🔑 Login</a>
  <select id="index" onchange="fetchChain()">
    <option value="NIFTY50">NIFTY50</option>
    <option value="BANKNIFTY">BANKNIFTY</option>
    <option value="FINNIFTY">FINNIFTY</option>
    <option value="MIDCAPNIFTY">MIDCAPNIFTY</option>
    <option value="SENSEX">SENSEX</option>
  </select>
  <h3 id="spot-title">Option Chain</h3>
  <table id="option-chain-table">
    <thead id="chain-header"></thead>
    <tbody id="chain-body"></tbody>
  </table>
  <div id="analysis" class="msg"></div>

  <!-- ATM CE Live Tracker -->
  <div class="atm-tracker">
    <h3>📊 ATM CE Live Tracker (1 Minute)</h3>
    <div class="atm-grid">
      <div class="atm-card">
        <h4>Strike</h4>
        <div class="value" id="atm-strike">-</div>
      </div>
      <div class="atm-card">
        <h4>LTP</h4>
        <div class="value" id="atm-ltp">-</div>
      </div>
      <div class="atm-card">
        <h4>High (1m)</h4>
        <div class="value" id="atm-high">-</div>
      </div>
      <div class="atm-card">
        <h4>Low (1m)</h4>
        <div class="value" id="atm-low">-</div>
      </div>
      <div class="atm-card signal" id="signal-card" style="display: none;">
        <h4>Signal</h4>
        <div class="value" id="atm-signal">-</div>
      </div>
    </div>
    <div class="timestamp">Last Update: <span id="atm-timestamp">-</span></div>
  </div>

  <script>
    async function fetchChain() {
      const index = document.getElementById("index").value;
      const resp = await fetch(`/fetch_chain?index=${index}`);
      const data = await resp.json();
      if (data.error) {
        document.getElementById("chain-body").innerHTML = "<tr><td colspan='20'>" + data.error + "</td></tr>";
        return;
      }
      document.getElementById("spot-title").innerHTML = `${index} Option Chain — Spot: ${data.spot}`;
      document.getElementById("chain-header").innerHTML = `<tr>${data.ce_headers}<th>STRIKE</th>${data.pe_headers}</tr>`;
      document.getElementById("chain-body").innerHTML = data.table;
      document.getElementById("analysis").innerHTML = data.analysis;
      
      // Update ATM CE Tracker
      if (data.atm_ce_data) {
        document.getElementById("atm-strike").textContent = data.atm_ce_data.strike || "-";
        document.getElementById("atm-ltp").textContent = data.atm_ce_data.ltp || "-";
        document.getElementById("atm-high").textContent = data.atm_ce_data.high || "-";
        document.getElementById("atm-low").textContent = data.atm_ce_data.low || "-";
        document.getElementById("atm-timestamp").textContent = data.atm_ce_data.timestamp || "-";
        
        const signalCard = document.getElementById("signal-card");
        const signalElement = document.getElementById("atm-signal");
        
        if (data.atm_ce_data.signal) {
          signalCard.style.display = "block";
          signalElement.textContent = data.atm_ce_data.signal;
        } else {
          signalCard.style.display = "none";
        }
      }
    }
    setInterval(fetchChain, 2000);
    window.onload = fetchChain;
  </script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
