from fyers_apiv3 import fyersModel
from flask import Flask, request, render_template_string, jsonify, redirect
import webbrowser
import pandas as pd
import os

# ---- Credentials ----
client_id = "VMS68P9EK0-100"
secret_key = "ZJ0CFWZEL1"
redirect_uri = "https://mksajid.onrender.com/callback"
grant_type = "authorization_code"
response_type = "code"
state = "sample"
auth_file = "auth_code.txt"  # File to store auth code

# ---- Session ----
appSession = fyersModel.SessionModel(
    client_id=client_id,
    secret_key=secret_key,
    redirect_uri=redirect_uri,
    response_type=response_type,
    grant_type=grant_type,
    state=state
)

# ---- Flask ----
app = Flask(__name__)
app.secret_key = "sajid_secret"

# ---- Globals ----
access_token_global = None
fyers = None
atm_strike = None
initial_data = None

atm_ce_plus20 = 20
atm_pe_plus20 = 20
symbol_prefix = "NSE:NIFTY25SEP"

signals = []
placed_orders = set()


def load_auth_code():
    if os.path.exists(auth_file):
        with open(auth_file, "r") as f:
            return f.read().strip()
    return None


def save_auth_code(auth_code):
    with open(auth_file, "w") as f:
        f.write(auth_code)


def init_fyers(auth_code):
    global access_token_global, fyers
    try:
        appSession.set_token(auth_code)
        token_response = appSession.generate_token()
        access_token_global = token_response.get("access_token")
        fyers = fyersModel.FyersModel(
            client_id=client_id,
            token=access_token_global,
            is_async=False,
            log_path=""
        )
        print("✅ Fyers session initialized from auth code.")
    except Exception as e:
        print("❌ Failed to init Fyers:", e)


# ---- Load auth code on startup ----
auth_code = load_auth_code()
if auth_code:
    init_fyers(auth_code)


@app.route("/", methods=["GET", "POST"])
def index():
    global atm_ce_plus20, atm_pe_plus20, symbol_prefix
    if request.method == "POST":
        try:
            atm_ce_plus20 = float(request.form.get("atm_ce_plus20", atm_ce_plus20))
        except (ValueError, TypeError):
            atm_ce_plus20 = 20
        try:
            atm_pe_plus20 = float(request.form.get("atm_pe_plus20", atm_pe_plus20))
        except (ValueError, TypeError):
            atm_pe_plus20 = 20
        prefix = request.form.get("symbol_prefix")
        if prefix:
            symbol_prefix = prefix.strip()

    return render_template_string(
        TEMPLATE,
        atm_ce_plus20=atm_ce_plus20,
        atm_pe_plus20=atm_pe_plus20,
        symbol_prefix=symbol_prefix
    )


@app.route("/login")
def login():
    login_url = appSession.generate_authcode()
    webbrowser.open(login_url, new=1)
    return redirect(login_url)


@app.route("/callback")
def callback():
    global access_token_global, fyers
    auth_code = request.args.get("auth_code")
    if auth_code:
        save_auth_code(auth_code)
        init_fyers(auth_code)
        return "<h2>✅ Authentication Successful! You can return to the app 🚀</h2>"
    return "❌ Authentication failed. Please retry."


@app.route("/fetch")
def fetch_option_chain():
    global fyers, atm_strike, initial_data, atm_ce_plus20, atm_pe_plus20, signals, placed_orders, symbol_prefix
    if fyers is None:
        return jsonify({"error": "⚠ Please login first!"})
    try:
        data = {"symbol": "NSE:NIFTY50-INDEX", "strikecount": 20, "timestamp": ""}
        response = fyers.optionchain(data=data)

        if "data" not in response or "optionsChain" not in response["data"]:
            return jsonify({"error": f"Invalid response from API: {response}"})

        options_data = response["data"]["optionsChain"]
        if not options_data:
            return jsonify({"error": "No options data found!"})

        df = pd.DataFrame(options_data)
        df_pivot = df.pivot_table(
            index="strike_price",
            columns="option_type",
            values="ltp",
            aggfunc="first"
        ).reset_index()
        df_pivot = df_pivot.rename(columns={"CE": "CE_LTP", "PE": "PE_LTP"})

        # ---- ATM detection ----
        if atm_strike is None:
            nifty_spot = response["data"].get(
                "underlyingValue",
                df_pivot["strike_price"].iloc[len(df_pivot) // 2]
            )
            atm_strike = min(df_pivot["strike_price"], key=lambda x: abs(x - nifty_spot))
            initial_data = df_pivot.to_dict(orient="records")
            signals.clear()
            placed_orders.clear()

        # ---- ATM order placement ----
        for row in df_pivot.itertuples():
            strike = row.strike_price
            ce_ltp = getattr(row, "CE_LTP", None)
            pe_ltp = getattr(row, "PE_LTP", None)

            if strike == atm_strike and ce_ltp is not None:
                initial_ce = next((item["CE_LTP"] for item in initial_data if item["strike_price"] == strike), None)
                if initial_ce is not None and ce_ltp > initial_ce + atm_ce_plus20:
                    signal_name = f"ATM_CE_{strike}"
                    if signal_name not in placed_orders:
                        signals.append(f"{strike} {ce_ltp} ATM Strike CE")
                        place_order(f"{symbol_prefix}{strike}CE", ce_ltp, side=1)
                        placed_orders.add(signal_name)

            if strike == atm_strike and pe_ltp is not None:
                initial_pe = next((item["PE_LTP"] for item in initial_data if item["strike_price"] == strike), None)
                if initial_pe is not None and pe_ltp > initial_pe + atm_pe_plus20:
                    signal_name = f"ATM_PE_{strike}"
                    if signal_name not in placed_orders:
                        signals.append(f"{strike} {pe_ltp} ATM Strike PE")
                        place_order(f"{symbol_prefix}{strike}PE", pe_ltp, side=1)
                        placed_orders.add(signal_name)

        return df_pivot.to_json(orient="records")
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/reset", methods=["POST"])
def reset_orders():
    global placed_orders, signals, atm_strike, initial_data
    placed_orders.clear()
    signals.clear()
    atm_strike = None
    initial_data = None
    return jsonify({"message": "✅ Reset successful! You can trade again."})


def place_order(symbol, price, side):
    try:
        if fyers is None:
            return
        data = {
            "symbol": symbol,
            "qty": 75,
            "type": 2,  # Market order
            "side": side,
            "productType": "INTRADAY",
            "limitPrice": 0,
            "stopPrice": 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
            "orderTag": "signalorder"
        }
        response = fyers.place_order(data=data)
        print("✅ Order placed:", response)
    except Exception as e:
        print("❌ Order error:", e)



# ---- HTML Template ----
TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <title>Sajid Shaikh Algo Software</title>
  <style>
    body { font-family: Arial, sans-serif; background: #f4f4f9; padding: 20px; }
    h2 { color: #1a73e8; }
    table { border-collapse: collapse; width: 70%; margin-top: 10px; }
    th, td { border: 1px solid #aaa; padding: 8px; text-align: center; }
    th { background-color: #1a73e8; color: white; }
    tr:nth-child(even) { background-color: #f2f2f2; }
    tr.atm { background-color: #ffeb3b; font-weight: bold; }
    tr.ceMinus300 { background-color: #90ee90; font-weight: bold; }
    tr.pePlus300 { background-color: #ffb6c1; font-weight: bold; }
    a { text-decoration: none; padding: 8px 12px; background: #4caf50; color: white; border-radius: 4px; }
    a:hover { background: #45a049; }
    #specialStrikes { margin-top: 10px; font-weight: bold; }
    #signals { margin-top: 15px; font-weight: bold; color: red; }
    #profits { margin-top: 8px; font-weight: bold; color: green; }
    form { margin-top: 20px; }
    label { margin-right: 10px; }
    input[type="number"], input[type="text"] { padding: 5px; margin-right: 20px; }
    button { padding: 6px 12px; background-color: #1a73e8; color: white; border: none; border-radius: 4px; cursor: pointer; }
    button:hover { background-color: #155cb0; }
  </style>
  <script>
    var atmStrike = null;
    var initialLTP = {};
    var signals = [];

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

        // ---- Profit calculation (ATM + CE-300 + PE+300) ----
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
            profitsOutput += `
                <b>${signal}</b> - Strike: ${strike} | Initial LTP: ${initialLtp?.toFixed(2)} | Live LTP: ${liveLtp?.toFixed(2)} | Profit × 75 = ₹${totalProfit} <br>
            `;
        });

        // CE -300 profit display
        let ceMinus300 = data.find(r => r.strike_price === atmStrike - 300);
        if(ceMinus300){
            let base = initialLTP[atmStrike - 300]?.CE;
            if(base){
                let gainPct = ((ceMinus300.CE_LTP - base) / base) * 100;
                let profit = (ceMinus300.CE_LTP - base) * 75;
                profitsOutput += `<b>CE -300</b> Profit: ₹${profit.toFixed(2)} (Gain: ${gainPct.toFixed(1)}%)<br>`;
            }
        }

        // PE +300 profit display
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

        // ---- Table Build ----
        data.forEach(row=>{
            let cls = "";
            let CE_display = row.CE_LTP;
            let PE_display = row.PE_LTP;

            // ATM row
            if(row.strike_price === atmStrike){
                cls = "atm";
                CE_display = `${initialLTP[atmStrike]?.CE} / ${atmLive?.CE_LTP}`;
                PE_display = `${initialLTP[atmStrike]?.PE} / ${atmLive?.PE_LTP}`;
            }

            // CE -300 row
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

            // PE +300 row
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
    window.onload = fetchChain;

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
  <h2>Sajid Shaikh Algo Software : +91 9834370368</h2>
  <a href="/login" target="_blank">🔑 Login</a>

  <form method="POST" action="/">
    <label>CE Threshold (+ over initial):</label>
    <input type="number" id="atm_ce_plus20" name="atm_ce_plus20" step="0.1" value="{{ atm_ce_plus20 }}" required>
    <label>PE Threshold (+ over initial):</label>
    <input type="number" id="atm_pe_plus20" name="atm_pe_plus20" step="0.1" value="{{ atm_pe_plus20 }}" required>
    <br><br>
    <label>Symbol Prefix:</label>
    <input type="text" id="symbol_prefix" name="symbol_prefix" value="{{ symbol_prefix }}" required>
    <button type="submit">Update Settings</button>
  </form>

  <form onsubmit="return resetOrders();">
    <button type="submit">🔄 Reset Orders</button>
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
    app.run(host="0.0.0.0", port=port, debug=True)

