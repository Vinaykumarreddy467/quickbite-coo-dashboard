"""
QuickBite COO dashboard: why did refunds jump in Sep-Oct 2026?

Reads QUICKBITE_DB.RAW (orders, refunds, weather, restaurants, zones) plus the
pre-computed AI chat themes in QUICKBITE_DB.ANALYTIC.
"""

import os

import altair as alt
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="QuickBite COO - Refund Spike",
    page_icon=":material/monitoring:",
    layout="wide",
)

RELEASE_DATE = "2026-09-08"
CHART_HEIGHT = 320


# =============================================================================
# Snowflake connection and data loading
# =============================================================================


def get_snowflake_connection():
    try:
        return st.connection(
            "snowflake",
            role="SYSADMIN",
            warehouse="COMPUTE_WH",
            database="QUICKBITE_DB",
            schema="RAW",
        )
    except Exception as e:
        st.error(f"Failed to connect to Snowflake: {e}")
        st.stop()


def run(sql: str, params=None) -> pd.DataFrame:
    df = get_snowflake_connection().query(sql, params=params, ttl=3600)
    df.columns = [c.lower() for c in df.columns]
    return df


REFUNDS_PER_ORDER = """
    r AS (SELECT ORDER_ID, SUM(REFUND_AMOUNT_INR) AS amt
          FROM QUICKBITE_DB.RAW.REFUNDS GROUP BY 1)
"""


@st.cache_data(ttl=3600, show_spinner=False)
def load_monthly() -> pd.DataFrame:
    return run(f"""
        WITH {REFUNDS_PER_ORDER}
        SELECT DATE_TRUNC('month', o.PLACED_AT)::DATE AS month,
               COUNT(*) AS orders,
               SUM(o.ORDER_VALUE_INR) AS gmv,
               COALESCE(SUM(r.amt), 0) AS refunds,
               100 * COALESCE(SUM(r.amt), 0) / SUM(o.ORDER_VALUE_INR) AS refund_pct
        FROM QUICKBITE_DB.RAW.ORDERS o LEFT JOIN r USING (ORDER_ID)
        WHERE YEAR(o.PLACED_AT) = 2026
        GROUP BY 1 ORDER BY 1
    """)


@st.cache_data(ttl=3600, show_spinner=False)
def load_zone_increase() -> pd.DataFrame:
    return run(f"""
        WITH {REFUNDS_PER_ORDER}
        SELECT o.ZONE_ID AS zone_id, z.CITY || ' / ' || z.ZONE_NAME AS zone,
               SUM(IFF(MONTH(o.PLACED_AT) = 8, r.amt, 0)) AS aug_refunds,
               SUM(IFF(MONTH(o.PLACED_AT) = 10, r.amt, 0)) AS oct_refunds,
               100 * SUM(IFF(MONTH(o.PLACED_AT) = 8, r.amt, 0))
                   / NULLIF(SUM(IFF(MONTH(o.PLACED_AT) = 8, o.ORDER_VALUE_INR, 0)), 0) AS aug_pct,
               100 * SUM(IFF(MONTH(o.PLACED_AT) = 10, r.amt, 0))
                   / NULLIF(SUM(IFF(MONTH(o.PLACED_AT) = 10, o.ORDER_VALUE_INR, 0)), 0) AS oct_pct
        FROM QUICKBITE_DB.RAW.ORDERS o
        LEFT JOIN r USING (ORDER_ID)
        JOIN QUICKBITE_DB.RAW.CITIES_ZONES z ON z.ZONE_ID = o.ZONE_ID
        WHERE o.PLACED_AT >= '2026-08-01' AND o.PLACED_AT < '2026-11-01'
        GROUP BY 1, 2
    """)


@st.cache_data(ttl=3600, show_spinner=False)
def load_weekly_late(zone_id: int) -> pd.DataFrame:
    return run("""
        SELECT DATE_TRUNC('week', PLACED_AT)::DATE AS week,
               IFF(ZONE_ID = ?, 'Selected zone', 'All other zones') AS grp,
               COUNT(*) AS orders,
               100 * COUNT_IF(DATEDIFF('second', PLACED_AT, DELIVERED_AT) > 2700) / COUNT(*) AS late_pct
        FROM QUICKBITE_DB.RAW.ORDERS
        WHERE YEAR(PLACED_AT) = 2026
        GROUP BY 1, 2 ORDER BY 1
    """, params=[int(zone_id)])


@st.cache_data(ttl=3600, show_spinner=False)
def load_rain(zone_id: int) -> pd.DataFrame:
    return run("""
        SELECT IFF(o.ZONE_ID = ?, 'Selected zone', 'All other zones') AS grp,
               IFF(o.PLACED_AT < '2026-09-08', 'Before v2.3', 'After v2.3') AS period,
               IFF(COALESCE(w.RAIN_MM, 0) > 0, 'Rain', 'Dry') AS weather,
               COUNT(*) AS orders,
               AVG(o.RIDER_ASSIGN_DISTANCE_KM) AS assign_km,
               AVG(DATEDIFF('second', o.ACCEPTED_AT, o.PICKED_UP_AT)) / 60 AS pickup_wait_min,
               100 * COUNT_IF(DATEDIFF('second', o.PLACED_AT, o.DELIVERED_AT) > 2700) / COUNT(*) AS late_pct
        FROM QUICKBITE_DB.RAW.ORDERS o
        LEFT JOIN QUICKBITE_DB.RAW.WEATHER_HOURLY w
               ON w.CITY = o.CITY AND w.HOUR_TS = DATE_TRUNC('hour', o.PLACED_AT)
        WHERE o.PLACED_AT >= '2026-08-01' AND o.PLACED_AT < '2026-11-01'
        GROUP BY 1, 2, 3
    """, params=[int(zone_id)])


@st.cache_data(ttl=3600, show_spinner=False)
def load_cloud_kitchens() -> pd.DataFrame:
    return run(f"""
        WITH {REFUNDS_PER_ORDER}
        SELECT IFF(s.CLUSTER = 'Cloud Kitchens Hub', 'Cloud Kitchens Hub',
                   IFF(o.ZONE_ID = 1, 'Whitefield-East', 'Everything else')) AS grp,
               SUM(IFF(MONTH(o.PLACED_AT) = 8, r.amt, 0)) AS aug_refunds,
               SUM(IFF(MONTH(o.PLACED_AT) = 10, r.amt, 0)) AS oct_refunds,
               COUNT_IF(MONTH(o.PLACED_AT) = 10) AS oct_orders,
               100 * SUM(IFF(MONTH(o.PLACED_AT) = 10, r.amt, 0))
                   / NULLIF(SUM(IFF(MONTH(o.PLACED_AT) = 10, o.ORDER_VALUE_INR, 0)), 0) AS oct_pct
        FROM QUICKBITE_DB.RAW.ORDERS o
        JOIN QUICKBITE_DB.RAW.RESTAURANTS s USING (RESTAURANT_ID)
        LEFT JOIN r USING (ORDER_ID)
        WHERE MONTH(o.PLACED_AT) IN (8, 10) AND YEAR(o.PLACED_AT) = 2026
        GROUP BY 1
    """)


@st.cache_data(ttl=3600, show_spinner=False)
def load_chat_themes() -> tuple[pd.DataFrame, pd.DataFrame]:
    themes = run("""
        SELECT THEME AS theme, COUNT(*) AS chats
        FROM QUICKBITE_DB.ANALYTIC.WFE_SUPPORT_CHAT_THEMES GROUP BY 1 ORDER BY 2 DESC
    """)
    summaries = run("""
        SELECT THEME AS theme, N AS n, SUMMARY AS summary
        FROM QUICKBITE_DB.ANALYTIC.WFE_SUPPORT_CHAT_SUMMARIES ORDER BY N DESC
    """)
    return themes, summaries


# =============================================================================
# Page
# =============================================================================

st.title("QuickBite: why did refunds jump in Sep–Oct 2026?")
st.caption(
    "Source: QUICKBITE_DB.RAW (refunds attributed to the month the order was placed; "
    "GMV = order value excl. delivery fee; late = >45 min placed→delivered)."
)

monthly = load_monthly()
zones = load_zone_increase()
zones["increase"] = zones["oct_refunds"] - zones["aug_refunds"]
zones = zones.sort_values("increase", ascending=False)

aug = monthly[monthly["month"].astype(str).str.startswith("2026-08")].iloc[0]
oct_ = monthly[monthly["month"].astype(str).str.startswith("2026-10")].iloc[0]
total_increase = oct_["refunds"] - aug["refunds"]
top_zone = zones.iloc[0]
top_share = 100 * top_zone["increase"] / total_increase

# --- KPI cards ---------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("October refunds", f"₹{oct_['refunds']:,.0f}",
          f"{100 * (oct_['refunds'] / aug['refunds'] - 1):+.0f}% vs Aug", delta_color="inverse",
          border=True)
c2.metric("Refund % of GMV (Oct)", f"{oct_['refund_pct']:.2f}%",
          f"{oct_['refund_pct'] - aug['refund_pct']:+.2f} pts vs Aug", delta_color="inverse",
          border=True)
c3.metric("October orders", f"{oct_['orders']:,.0f}",
          f"{100 * (oct_['orders'] / aug['orders'] - 1):+.1f}% vs Aug", border=True)
c4.metric("Share of refund increase from one zone", f"{top_share:.0f}%",
          top_zone["zone"], delta_color="off", border=True)

# --- Monthly trend -----------------------------------------------------------
left, right = st.columns(2)
with left:
    with st.container(border=True):
        st.subheader("Monthly trend")
        base = alt.Chart(monthly).encode(x=alt.X("month:T", title=None, axis=alt.Axis(format="%b")))
        gmv = base.mark_bar(opacity=0.35).encode(y=alt.Y("gmv:Q", title="GMV (₹)"))
        pct = base.mark_line(point=True, color="#d62728").encode(
            y=alt.Y("refund_pct:Q", title="Refund % of GMV"),
            tooltip=["month:T", alt.Tooltip("orders:Q", format=","),
                     alt.Tooltip("gmv:Q", format=",.0f"), alt.Tooltip("refund_pct:Q", format=".2f")],
        )
        st.altair_chart(alt.layer(gmv, pct).resolve_scale(y="independent").properties(height=CHART_HEIGHT),
                        use_container_width=True)

# --- Refund increase by zone -------------------------------------------------
with right:
    with st.container(border=True):
        st.subheader("Refund increase by zone, Aug → Oct")
        top = zones.head(12)
        bars = alt.Chart(top).mark_bar().encode(
            x=alt.X("increase:Q", title="Increase in refunds (₹)"),
            y=alt.Y("zone:N", sort="-x", title=None),
            color=alt.condition(alt.datum.zone_id == 1, alt.value("#d62728"), alt.value("#9ecae1")),
            tooltip=["zone", alt.Tooltip("aug_refunds:Q", format=",.0f"),
                     alt.Tooltip("oct_refunds:Q", format=",.0f"),
                     alt.Tooltip("aug_pct:Q", format=".2f"), alt.Tooltip("oct_pct:Q", format=".2f")],
        )
        st.altair_chart(bars.properties(height=CHART_HEIGHT), use_container_width=True)

# --- Zone selector: weekly late % -------------------------------------------
st.header("Delivery lateness by zone")
zone_options = zones.set_index("zone_id")["zone"].to_dict()
zone_id = st.selectbox("Zone", list(zone_options), format_func=zone_options.get, index=0)

with st.container(border=True):
    st.subheader(f"Weekly late % — {zone_options[zone_id]} vs all other zones")
    weekly = load_weekly_late(zone_id)
    line = alt.Chart(weekly).mark_line(point=True).encode(
        x=alt.X("week:T", title="Week"),
        y=alt.Y("late_pct:Q", title="Late deliveries (%)"),
        color=alt.Color("grp:N", title=None,
                        scale=alt.Scale(domain=["Selected zone", "All other zones"],
                                        range=["#d62728", "#7f7f7f"])),
        tooltip=["week:T", "grp", alt.Tooltip("orders:Q", format=","), alt.Tooltip("late_pct:Q", format=".1f")],
    )
    rule_df = pd.DataFrame({"date": [pd.Timestamp(RELEASE_DATE)], "label": ["rider-assign v2.3 (10 km radius)"]})
    rule = alt.Chart(rule_df).mark_rule(strokeDash=[6, 4], color="black").encode(x="date:T")
    label = alt.Chart(rule_df).mark_text(align="left", dx=5, dy=-130, fontSize=12).encode(x="date:T", text="label")
    st.altair_chart((line + rule + label).properties(height=CHART_HEIGHT), use_container_width=True)

# --- Rain effect --------------------------------------------------------------
with st.container(border=True):
    st.subheader(f"Rain effect — {zone_options[zone_id]} (Aug–Oct)")
    rain = load_rain(zone_id)
    rain["segment"] = rain["grp"] + " · " + rain["period"]
    rain_chart = alt.Chart(rain).mark_bar().encode(
        x=alt.X("weather:N", title=None),
        y=alt.Y("late_pct:Q", title="Late deliveries (%)"),
        color=alt.Color("weather:N", scale=alt.Scale(domain=["Dry", "Rain"], range=["#fdae6b", "#3182bd"]),
                        legend=None),
        column=alt.Column("segment:N", title=None,
                          sort=["Selected zone · Before v2.3", "Selected zone · After v2.3",
                                "All other zones · Before v2.3", "All other zones · After v2.3"]),
        tooltip=["segment", "weather", alt.Tooltip("orders:Q", format=","),
                 alt.Tooltip("assign_km:Q", format=".2f"), alt.Tooltip("pickup_wait_min:Q", format=".1f"),
                 alt.Tooltip("late_pct:Q", format=".1f")],
    ).properties(width=150, height=240)
    st.altair_chart(rain_chart)
    st.caption("Rain = RAIN_MM > 0 in the order's city and hour (WEATHER_HOURLY).")

# --- Cloud Kitchens red herring ----------------------------------------------
st.header("Red herring: Cloud Kitchens Hub (launched 2026-09-01)")
ck = load_cloud_kitchens()
ck["increase"] = ck["oct_refunds"] - ck["aug_refunds"]
ck["share_pct"] = 100 * ck["increase"] / ck["increase"].sum()
a, b = st.columns([2, 3])
with a:
    st.dataframe(
        ck[["grp", "aug_refunds", "oct_refunds", "increase", "share_pct", "oct_pct"]].sort_values(
            "increase", ascending=False),
        hide_index=True,
        column_config={
            "grp": "Group",
            "aug_refunds": st.column_config.NumberColumn("Aug refunds ₹", format="%,.0f"),
            "oct_refunds": st.column_config.NumberColumn("Oct refunds ₹", format="%,.0f"),
            "increase": st.column_config.NumberColumn("Increase ₹", format="%,.0f"),
            "share_pct": st.column_config.NumberColumn("Share of increase", format="%.0f%%"),
            "oct_pct": st.column_config.NumberColumn("Oct refund % GMV", format="%.2f%%"),
        },
    )
with b:
    st.info(
        "The 60 Cloud Kitchens Hub brands launched the same week the spike started, but they sit in "
        "HITEC City (Hyderabad), serve ~1% of orders, and explain only ~5% of the refund increase. "
        "Their refund rate (~6.5%) is worth watching, but they did not drive the spike."
    )

# --- AI chat themes -----------------------------------------------------------
st.header("What customers are saying — Whitefield-East chats since 2026-09-08")
themes, summaries = load_chat_themes()
t1, t2 = st.columns([2, 3])
with t1:
    theme_chart = alt.Chart(themes).mark_bar().encode(
        x=alt.X("chats:Q", title="Chats"),
        y=alt.Y("theme:N", sort="-x", title=None),
        color=alt.condition(alt.datum.theme == "Rider assigned too far away",
                            alt.value("#d62728"), alt.value("#9ecae1")),
        tooltip=["theme", "chats"],
    )
    st.altair_chart(theme_chart.properties(height=260), use_container_width=True)
    st.caption("Themes via AI_CLASSIFY; summaries via AI_SUMMARIZE_AGG (QUICKBITE_DB.ANALYTIC).")
with t2:
    for row in summaries.itertuples():
        with st.expander(f"{row.theme} ({row.n})", expanded=row.theme == "ALL THEMES"):
            st.write(row.summary)

# --- Recommendation -----------------------------------------------------------
st.header("Recommendation")
st.success(
    f"""
**Root cause:** rider-assign **v2.3** (released {RELEASE_DATE}, assignment radius expanded to 10 km).
In Whitefield-East it pulls riders from ~6 km away instead of ~2 km, pickup waits rise ~6 min, and late
deliveries go from ~7% to ~28% (~58% in rain). Other zones are unchanged. This one zone accounts for
~{top_share:.0f}% of the company-wide refund increase, driven by *Late delivery* and *Cold food* refunds.

**Actions:**
1. Cap the assignment radius for Whitefield-East (or roll back to v2.2 behaviour) immediately, at least during rain.
2. Investigate rider supply in Whitefield-East — the wider radius suggests too few local riders.
3. Add a per-zone guardrail metric (late % and refund % vs baseline) to future rider-assign rollouts.
4. Monitor Cloud Kitchens Hub refund rate (~6.5%) separately — real, but small.
"""
)
