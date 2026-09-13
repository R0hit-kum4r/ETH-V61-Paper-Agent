# 🤖 V6.1 — LIVE PAPER SIGNAL AGENT
# FIXED COINBASE DATA DOWNLOAD
# ETH/USD — 15 MINUTES
# NO REAL ORDERS
# ============================================================
import os
import requests
import pandas as pd
import numpy as np
import time


# ============================================================
# FROZEN V6.1 SETTINGS
# ============================================================

PRODUCT = "ETH-USD"
GRANULARITY = 900

CANDLES_NEEDED = 350
MAX_CANDLES_PER_REQUEST = 300

RR = 3.0
FVG_MIN_ATR = 0.10
DISPLACEMENT_ATR = 1.20
OB_LOOKBACK = 6
SL_BUFFER_ATR = 0.10
MAX_ZONE_AGE_HOURS = 24

URL = (
    "https://api.exchange.coinbase.com/"
    "products/ETH-USD/candles"
)


# ============================================================
# DOWNLOAD CANDLES — CHUNKED
# ============================================================

def get_live_candles():

    now = pd.Timestamp.now(
        tz="UTC"
    )

    # Need 350 candles
    total_minutes = (
        CANDLES_NEEDED * 15
    )

    start = (
        now
        - pd.Timedelta(
            minutes=total_minutes
        )
    )

    all_data = []

    cursor = start

    while cursor < now:

        # Maximum 300 candles/request
        chunk_end = min(
            cursor
            + pd.Timedelta(
                minutes=15
                * MAX_CANDLES_PER_REQUEST
            ),
            now
        )

        params = {
            "start":
                cursor.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),

            "end":
                chunk_end.strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),

            "granularity":
                GRANULARITY
        }

        response = requests.get(
            URL,
            params=params,
            timeout=30
        )

        response.raise_for_status()

        candles = response.json()

        if not isinstance(
            candles,
            list
        ):

            raise ValueError(
                "Coinbase returned unexpected data."
            )

        all_data.extend(
            candles
        )

        cursor = chunk_end

        time.sleep(0.2)


    # --------------------------------------------------------
    # DataFrame
    # --------------------------------------------------------

    df = pd.DataFrame(
        all_data,
        columns=[
            "time",
            "low",
            "high",
            "open",
            "close",
            "volume"
        ]
    )

    df["time"] = pd.to_datetime(
        df["time"],
        unit="s",
        utc=True
    )

    for col in [
        "low",
        "high",
        "open",
        "close",
        "volume"
    ]:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = (
        df
        .dropna()
        .drop_duplicates(
            subset=["time"]
        )
        .sort_values("time")
        .reset_index(drop=True)
    )

    return df


# ============================================================
# INDICATORS
# ============================================================

def prepare_indicators(df):

    data = df.copy()

    previous_close = (
        data["close"].shift(1)
    )

    tr1 = (
        data["high"]
        - data["low"]
    )

    tr2 = abs(
        data["high"]
        - previous_close
    )

    tr3 = abs(
        data["low"]
        - previous_close
    )

    data["TR"] = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(axis=1)

    data["ATR14"] = (
        data["TR"]
        .rolling(14)
        .mean()
    )

    data["EMA20"] = (
        data["close"]
        .ewm(
            span=20,
            adjust=False
        )
        .mean()
    )

    data["EMA50"] = (
        data["close"]
        .ewm(
            span=50,
            adjust=False
        )
        .mean()
    )

    return data


# ============================================================
# SIDEWAYS FILTER
# ============================================================

def is_sideways(data, i):

    if i < 32:
        return True

    atr = data.iloc[i]["ATR14"]

    if pd.isna(atr) or atr <= 0:
        return True

    range_2h = (
        data.iloc[i-7:i+1]["high"].max()
        -
        data.iloc[i-7:i+1]["low"].min()
    )

    range_4h = (
        data.iloc[i-15:i+1]["high"].max()
        -
        data.iloc[i-15:i+1]["low"].min()
    )

    range_8h = (
        data.iloc[i-31:i+1]["high"].max()
        -
        data.iloc[i-31:i+1]["low"].min()
    )

    return (
        range_2h < atr * 2.0
        and
        range_4h < atr * 3.0
        and
        range_8h < atr * 5.0
    )


# ============================================================
# FVG
# ============================================================

def get_fvg(data, i, side):

    if i < 2:
        return None

    atr = data.iloc[i]["ATR14"]

    if pd.isna(atr) or atr <= 0:
        return None


    # BUY FVG
    if side == "BUY":

        fvg_low = (
            data.iloc[i-2]["high"]
        )

        fvg_high = (
            data.iloc[i]["low"]
        )

        size = (
            fvg_high
            - fvg_low
        )

        if size <= 0:
            return None

        if size < (
            atr
            * FVG_MIN_ATR
        ):
            return None

        return {
            "fvg_time":
                data.iloc[i]["time"],

            "fvg_low":
                fvg_low,

            "fvg_high":
                fvg_high,

            "fvg_size":
                size,

            "fvg_size_atr":
                size / atr
        }


    # SELL FVG
    if side == "SELL":

        fvg_low = (
            data.iloc[i]["high"]
        )

        fvg_high = (
            data.iloc[i-2]["low"]
        )

        size = (
            fvg_high
            - fvg_low
        )

        if size <= 0:
            return None

        if size < (
            atr
            * FVG_MIN_ATR
        ):
            return None

        return {
            "fvg_time":
                data.iloc[i]["time"],

            "fvg_low":
                fvg_low,

            "fvg_high":
                fvg_high,

            "fvg_size":
                size,

            "fvg_size_atr":
                size / atr
        }

    return None


# ============================================================
# ORDER BLOCK
# ============================================================

def find_order_block(data, i, side):

    start = max(
        2,
        i - OB_LOOKBACK
    )

    for j in range(
        i - 1,
        start - 1,
        -1
    ):

        candle = data.iloc[j]

        atr = candle["ATR14"]

        if pd.isna(atr) or atr <= 0:
            continue

        bullish = (
            candle["close"]
            >
            candle["open"]
        )

        bearish = (
            candle["close"]
            <
            candle["open"]
        )


        # ----------------------------------------------------
        # BUY
        # ----------------------------------------------------

        if (
            side == "BUY"
            and bearish
        ):

            nxt = data.iloc[j + 1]

            displacement = (
                nxt["close"]
                - candle["close"]
            )

            displacement_atr = (
                displacement / atr
            )

            if (
                displacement_atr
                >= DISPLACEMENT_ATR
            ):

                return {
                    "ob_time":
                        candle["time"],

                    "ob_index":
                        j,

                    "zone_low":
                        candle["low"],

                    "zone_high":
                        candle["high"],

                    "displacement_atr":
                        displacement_atr
                }


        # ----------------------------------------------------
        # SELL
        # ----------------------------------------------------

        if (
            side == "SELL"
            and bullish
        ):

            nxt = data.iloc[j + 1]

            displacement = (
                candle["close"]
                - nxt["close"]
            )

            displacement_atr = (
                displacement / atr
            )

            if (
                displacement_atr
                >= DISPLACEMENT_ATR
            ):

                return {
                    "ob_time":
                        candle["time"],

                    "ob_index":
                        j,

                    "zone_low":
                        candle["low"],

                    "zone_high":
                        candle["high"],

                    "displacement_atr":
                        displacement_atr
                }

    return None


# ============================================================
# BUILD ZONES
# ============================================================

def build_zones(data, upto_index):

    zones = []

    for i in range(
        50,
        upto_index + 1
    ):

        current = data.iloc[i]

        # Weekend
        if current["time"].weekday() >= 5:
            continue

        # Sideways
        if is_sideways(
            data,
            i
        ):
            continue


        # Trend
        if (
            current["EMA20"]
            >
            current["EMA50"]
        ):

            trend = "BULLISH"

        elif (
            current["EMA20"]
            <
            current["EMA50"]
        ):

            trend = "BEARISH"

        else:
            continue


        # ====================================================
        # BUY
        # ====================================================

        if trend == "BULLISH":

            fvg = get_fvg(
                data,
                i,
                "BUY"
            )

            if fvg is None:
                continue

            ob = find_order_block(
                data,
                i,
                "BUY"
            )

            if ob is None:
                continue

            overlap_low = max(
                ob["zone_low"],
                fvg["fvg_low"]
            )

            overlap_high = min(
                ob["zone_high"],
                fvg["fvg_high"]
            )

            if (
                overlap_high
                >= overlap_low
            ):

                zones.append({

                    "side": "BUY",
                    "trend": trend,

                    "zone_low":
                        ob["zone_low"],

                    "zone_high":
                        ob["zone_high"],

                    "fvg_time":
                        fvg["fvg_time"],

                    "fvg_size":
                        fvg["fvg_size"],

                    "fvg_size_atr":
                        fvg["fvg_size_atr"],

                    "ob_time":
                        ob["ob_time"],

                    "ob_index":
                        ob["ob_index"],

                    "displacement_atr":
                        ob[
                            "displacement_atr"
                        ]
                })


        # ====================================================
        # SELL
        # ====================================================

        if trend == "BEARISH":

            fvg = get_fvg(
                data,
                i,
                "SELL"
            )

            if fvg is None:
                continue

            ob = find_order_block(
                data,
                i,
                "SELL"
            )

            if ob is None:
                continue

            overlap_low = max(
                ob["zone_low"],
                fvg["fvg_low"]
            )

            overlap_high = min(
                ob["zone_high"],
                fvg["fvg_high"]
            )

            if (
                overlap_high
                >= overlap_low
            ):

                zones.append({

                    "side": "SELL",
                    "trend": trend,

                    "zone_low":
                        ob["zone_low"],

                    "zone_high":
                        ob["zone_high"],

                    "fvg_time":
                        fvg["fvg_time"],

                    "fvg_size":
                        fvg["fvg_size"],

                    "fvg_size_atr":
                        fvg["fvg_size_atr"],

                    "ob_time":
                        ob["ob_time"],

                    "ob_index":
                        ob["ob_index"],

                    "displacement_atr":
                        ob[
                            "displacement_atr"
                        ]
                })

    return zones


# ============================================================
# LIVE SIGNAL
# ============================================================

def get_v61_live_signal():

    df = get_live_candles()

    print(
        "Downloaded candles:",
        len(df)
    )

    # --------------------------------------------------------
    # ONLY COMPLETED CANDLES
    # --------------------------------------------------------

    completed_cutoff = (
        pd.Timestamp.now(
            tz="UTC"
        ).floor("15min")
    )

    df = df[
        df["time"]
        < completed_cutoff
    ].copy()

    print(
        "Completed candles:",
        len(df)
    )

    if len(df) < 100:

        return {
            "action": "HOLD",
            "entry": 0.0,
            "stop_loss": 0.0,
            "take_profit": 0.0,
            "reason":
                "Not enough completed candles."
        }

    df = prepare_indicators(
        df
    )

    i = len(df) - 1

    current = df.iloc[i]

    current_time = current["time"]


    # ========================================================
    # WEEKEND
    # ========================================================

    if current_time.weekday() >= 5:

        return {
            "action": "HOLD",
            "entry":
                float(current["close"]),
            "stop_loss": 0.0,
            "take_profit": 0.0,
            "reason":
                "Weekend filter active."
        }


    # ========================================================
    # SIDEWAYS
    # ========================================================

    if is_sideways(
        df,
        i
    ):

        return {
            "action": "HOLD",
            "entry":
                float(current["close"]),
            "stop_loss": 0.0,
            "take_profit": 0.0,
            "reason":
                "Market is sideways / tight range."
        }


    # ========================================================
    # TREND
    # ========================================================

    if (
        current["EMA20"]
        >
        current["EMA50"]
    ):

        trend = "BULLISH"

    elif (
        current["EMA20"]
        <
        current["EMA50"]
    ):

        trend = "BEARISH"

    else:

        return {
            "action": "HOLD",
            "entry":
                float(current["close"]),
            "stop_loss": 0.0,
            "take_profit": 0.0,
            "reason":
                "No clear trend."
        }


    # ========================================================
    # HISTORICAL ZONES
    # ========================================================

    zones = build_zones(
        df,
        i - 1
    )

    zones = sorted(
        zones,
        key=lambda z: z["fvg_time"],
        reverse=True
    )


    bullish_close = (
        current["close"]
        >
        current["open"]
    )

    bearish_close = (
        current["close"]
        <
        current["open"]
    )


    # ========================================================
    # CHECK ZONES
    # ========================================================

    for zone in zones:

        zone_age_hours = (
            current_time
            -
            zone["fvg_time"]
        ).total_seconds() / 3600


        if (
            zone_age_hours <= 0
            or
            zone_age_hours
            > MAX_ZONE_AGE_HOURS
        ):
            continue


        if i <= zone["ob_index"]:
            continue


        zone_low = zone["zone_low"]
        zone_high = zone["zone_high"]


        # ====================================================
        # BUY
        # ====================================================

        if (
            zone["side"] == "BUY"
            and
            trend == "BULLISH"
        ):

            touched = (
                current["low"]
                <= zone_high
                and
                current["high"]
                >= zone_low
            )

            if (
                touched
                and
                bullish_close
            ):

                entry = float(
                    current["close"]
                )

                atr = float(
                    current["ATR14"]
                )

                stop_loss = (
                    zone_low
                    -
                    atr
                    * SL_BUFFER_ATR
                )

                risk = (
                    entry
                    -
                    stop_loss
                )

                if risk <= 0:
                    continue

                take_profit = (
                    entry
                    +
                    risk * RR
                )

                return {

                    "action":
                        "BUY",

                    "entry":
                        round(
                            entry,
                            2
                        ),

                    "stop_loss":
                        round(
                            stop_loss,
                            2
                        ),

                    "take_profit":
                        round(
                            take_profit,
                            2
                        ),

                    "reason":
                        (
                            "BULLISH trend + "
                            "FVG/OB demand retest + "
                            "bullish closed candle + "
                            f"zone age "
                            f"{zone_age_hours:.1f}h"
                        )
                }


        # ====================================================
        # SELL
        # ====================================================

        if (
            zone["side"] == "SELL"
            and
            trend == "BEARISH"
        ):

            touched = (
                current["high"]
                >= zone_low
                and
                current["low"]
                <= zone_high
            )

            if (
                touched
                and
                bearish_close
            ):

                entry = float(
                    current["close"]
                )

                atr = float(
                    current["ATR14"]
                )

                stop_loss = (
                    zone_high
                    +
                    atr
                    * SL_BUFFER_ATR
                )

                risk = (
                    stop_loss
                    -
                    entry
                )

                if risk <= 0:
                    continue

                take_profit = (
                    entry
                    -
                    risk * RR
                )

                return {

                    "action":
                        "SELL",

                    "entry":
                        round(
                            entry,
                            2
                        ),

                    "stop_loss":
                        round(
                            stop_loss,
                            2
                        ),

                    "take_profit":
                        round(
                            take_profit,
                            2
                        ),

                    "reason":
                        (
                            "BEARISH trend + "
                            "FVG/OB supply retest + "
                            "bearish closed candle + "
                            f"zone age "
                            f"{zone_age_hours:.1f}h"
                        )
                }


    # ========================================================
    # HOLD
    # ========================================================

    return {

        "action":
            "HOLD",

        "entry":
            round(
                float(
                    current["close"]
                ),
                2
            ),

        "stop_loss":
            0.0,

        "take_profit":
            0.0,

        "reason":
            (
                f"No valid V6.1 setup. "
                f"Trend={trend}. "
                "Waiting for FVG/OB retest."
            )
    }




# ============================================================
# LOAD JOURNAL
# ============================================================

if os.path.exists(JOURNAL_FILE):

    journal = pd.read_csv(
        JOURNAL_FILE
    )

else:

    journal = pd.DataFrame(
        columns=[
            "trade_id",
            "signal_time",
            "entry_time",
            "exit_time",
            "side",
            "entry",
            "stop_loss",
            "take_profit",
            "risk_pct",
            "reason",
            "status",
            "outcome",
            "R"
        ]
    )


# ============================================================
# DATE COLUMNS
# ============================================================

for col in [
    "signal_time",
    "entry_time",
    "exit_time"
]:

    if col in journal.columns:

        journal[col] = pd.to_datetime(
            journal[col],
            utc=True,
            errors="coerce"
        )


# ============================================================
# SAVE JOURNAL
# ============================================================

def save_paper_journal():

    journal.to_csv(
        JOURNAL_FILE,
        index=False
    )


# ============================================================
# PROCESS ONE PAPER CYCLE
# ============================================================

def process_paper_cycle():

    global journal

    print("\n" + "=" * 75)
    print("🔄 NEW PAPER MONITOR CYCLE")
    print("=" * 75)

    # --------------------------------------------------------
    # Get candles
    # --------------------------------------------------------

    df = get_live_candles()

    completed_cutoff = (
        pd.Timestamp.now(
            tz="UTC"
        ).floor("15min")
    )

    df = df[
        df["time"]
        < completed_cutoff
    ].copy()

    df = (
        df
        .sort_values("time")
        .drop_duplicates(
            subset=["time"]
        )
        .reset_index(drop=True)
    )

    if len(df) < 100:

        print(
            "❌ Not enough completed candles."
        )

        return None


    latest = df.iloc[-1]

    latest_time = latest["time"]

    print(
        "Latest CLOSED candle:",
        latest_time
    )

    print(
        "Close:",
        round(
            float(latest["close"]),
            2
        )
    )


    # ========================================================
    # 1️⃣ CHECK OPEN PAPER TRADE
    # ========================================================

    open_indices = journal.index[
        journal["status"] == "OPEN"
    ].tolist()


    if len(open_indices) > 0:

        print("\n🔎 Checking open paper trade...")

        for idx in open_indices:

            trade = journal.loc[idx]

            side = trade["side"]

            entry = float(
                trade["entry"]
            )

            sl = float(
                trade["stop_loss"]
            )

            tp = float(
                trade["take_profit"]
            )

            entry_time = pd.Timestamp(
                trade["entry_time"]
            )


            # Only candles after entry
            future = df[
                df["time"]
                > entry_time
            ].copy()


            closed = False


            for _, candle in future.iterrows():

                candle_time = candle["time"]

                high = float(
                    candle["high"]
                )

                low = float(
                    candle["low"]
                )


                # ------------------------------------------------
                # BUY
                # ------------------------------------------------

                if side == "BUY":

                    sl_hit = (
                        low <= sl
                    )

                    tp_hit = (
                        high >= tp
                    )

                    # Conservative:
                    # SL first if both occur
                    if sl_hit:

                        journal.loc[
                            idx,
                            "exit_time"
                        ] = candle_time

                        journal.loc[
                            idx,
                            "status"
                        ] = "CLOSED"

                        journal.loc[
                            idx,
                            "outcome"
                        ] = "LOSS"

                        journal.loc[
                            idx,
                            "R"
                        ] = -1.0

                        print(
                            f"🔴 TRADE #{trade['trade_id']} "
                            f"BUY → LOSS (-1R)"
                        )

                        closed = True

                        break


                    elif tp_hit:

                        journal.loc[
                            idx,
                            "exit_time"
                        ] = candle_time

                        journal.loc[
                            idx,
                            "status"
                        ] = "CLOSED"

                        journal.loc[
                            idx,
                            "outcome"
                        ] = "WIN"

                        journal.loc[
                            idx,
                            "R"
                        ] = 3.0

                        print(
                            f"🟢 TRADE #{trade['trade_id']} "
                            f"BUY → WIN (+3R)"
                        )

                        closed = True

                        break


                # ------------------------------------------------
                # SELL
                # ------------------------------------------------

                elif side == "SELL":

                    sl_hit = (
                        high >= sl
                    )

                    tp_hit = (
                        low <= tp
                    )


                    if sl_hit:

                        journal.loc[
                            idx,
                            "exit_time"
                        ] = candle_time

                        journal.loc[
                            idx,
                            "status"
                        ] = "CLOSED"

                        journal.loc[
                            idx,
                            "outcome"
                        ] = "LOSS"

                        journal.loc[
                            idx,
                            "R"
                        ] = -1.0

                        print(
                            f"🔴 TRADE #{trade['trade_id']} "
                            f"SELL → LOSS (-1R)"
                        )

                        closed = True

                        break


                    elif tp_hit:

                        journal.loc[
                            idx,
                            "exit_time"
                        ] = candle_time

                        journal.loc[
                            idx,
                            "status"
                        ] = "CLOSED"

                        journal.loc[
                            idx,
                            "outcome"
                        ] = "WIN"

                        journal.loc[
                            idx,
                            "R"
                        ] = 3.0

                        print(
                            f"🟢 TRADE #{trade['trade_id']} "
                            f"SELL → WIN (+3R)"
                        )

                        closed = True

                        break


    # ========================================================
    # 2️⃣ CHECK IF TRADE IS STILL OPEN
    # ========================================================

    open_indices = journal.index[
        journal["status"] == "OPEN"
    ].tolist()


    # ========================================================
    # 3️⃣ GET NEW SIGNAL
    # ========================================================

    signal = get_v61_live_signal()

    action = signal["action"]


    print("\n" + "-" * 75)
    print("🤖 V6.1 SIGNAL")
    print("-" * 75)

    print(
        "Action      :",
        action
    )

    print(
        "Entry       :",
        signal["entry"]
    )

    print(
        "Stop Loss   :",
        signal["stop_loss"]
    )

    print(
        "Take Profit :",
        signal["take_profit"]
    )

    print(
        "Reason      :",
        signal["reason"]
    )


    # ========================================================
    # 4️⃣ ONLY ONE PAPER TRADE AT A TIME
    # ========================================================

    if len(open_indices) > 0:

        print(
            "\n⏸️ Existing paper trade is OPEN."
        )

        print(
            "No new trade opened."
        )


    elif action in [
        "BUY",
        "SELL"
    ]:

        # ----------------------------------------------------
        # Prevent duplicate candle
        # ----------------------------------------------------

        duplicate = journal[
            journal["signal_time"]
            == latest_time
        ]

        if len(duplicate) > 0:

            print(
                "\n⚠️ Signal already recorded."
            )

        else:

            entry = float(
                signal["entry"]
            )

            sl = float(
                signal["stop_loss"]
            )

            tp = float(
                signal["take_profit"]
            )


            risk_pct = (
                abs(
                    entry - sl
                )
                / entry
                * 100
            )


            # ------------------------------------------------
            # Trade ID
            # ------------------------------------------------

            if len(journal) == 0:

                trade_id = 1

            else:

                ids = pd.to_numeric(
                    journal["trade_id"],
                    errors="coerce"
                )

                if ids.notna().any():

                    trade_id = (
                        int(ids.max())
                        + 1
                    )

                else:

                    trade_id = 1


            # ------------------------------------------------
            # Add paper trade
            # ------------------------------------------------

            new_trade = {

                "trade_id":
                    trade_id,

                "signal_time":
                    latest_time,

                "entry_time":
                    latest_time,

                "exit_time":
                    pd.NaT,

                "side":
                    action,

                "entry":
                    entry,

                "stop_loss":
                    sl,

                "take_profit":
                    tp,

                "risk_pct":
                    risk_pct,

                "reason":
                    signal["reason"],

                "status":
                    "OPEN",

                "outcome":
                    "OPEN",

                "R":
                    np.nan
            }


            journal = pd.concat(
                [
                    journal,
                    pd.DataFrame(
                        [new_trade]
                    )
                ],
                ignore_index=True
            )


            print("\n" + "=" * 75)
            print("🚨 NEW PAPER TRADE")
            print("=" * 75)

            print(
                "Trade ID:",
                trade_id
            )

            print(
                "SIDE:",
                action
            )

            print(
                "ENTRY:",
                entry
            )

            print(
                "SL:",
                sl
            )

            print(
                "TP:",
                tp
            )

            print(
                "Risk:",
                round(
                    risk_pct,
                    4
                ),
                "%"
            )

            print(
                "RR:",
                RR
            )


    else:

        print(
            "\n⏸️ No new trade."
        )


    # ========================================================
    # SAVE
    # ========================================================

    save_paper_journal()


    # ========================================================
    # PERFORMANCE
    # ========================================================

    closed = journal[
        journal["status"] == "CLOSED"
    ]

    open_now = journal[
        journal["status"] == "OPEN"
    ]


    print("\n" + "=" * 75)
    print("📒 PAPER JOURNAL")
    print("=" * 75)

    print(
        "Total trades:",
        len(journal)
    )

    print(
        "Open:",
        len(open_now)
    )

    print(
        "Closed:",
        len(closed)
    )


    if len(closed) > 0:

        wins = (
            closed["R"] > 0
        ).sum()

        losses = (
            closed["R"] < 0
        ).sum()

        total_R = (
            closed["R"].sum()
        )

        win_rate = (
            wins
            / len(closed)
            * 100
        )

        print(
            "Wins:",
            wins
        )

        print(
            "Losses:",
            losses
        )

        print(
            "Win rate:",
            round(
                win_rate,
                2
            ),
            "%"
        )

        print(
            "Total R:",
            round(
                total_R,
                2
            ),
            "R"
        )


    if len(open_now) > 0:

        print("\n🔴 OPEN PAPER TRADE")

        print(
            open_now[
                [
                    "trade_id",
                    "entry_time",
                    "side",
                    "entry",
                    "stop_loss",
                    "take_profit",
                    "risk_pct"
                ]
            ].to_string(index=False)
        )


    return latest_time


# ============================================================
# AUTOMATIC MONITOR
# ============================================================

print("=" * 75)


# ============================================================
# ☁️ CLOUD RUN — ONE PAPER CYCLE
# GitHub Actions runs this script every 15 minutes.
# NO REAL ORDERS.
# ============================================================

if __name__ == "__main__":
    process_paper_cycle()
