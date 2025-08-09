import io
import re
from datetime import datetime
import requests
import pandas as pd
import streamlit as st
import altair as alt

st.set_page_config(page_title="Phosphate Rock Dashboard", layout="wide")

WB_URL_PRIMARY = "https://www.worldbank.org/content/dam/Worldbank/GEP/GEPcommodities/CMO-Historical-Data-Monthly.xlsx"
# 予備（World Bankの mirror 的ドキュメント配布）
WB_URL_FALLBACK = "https://thedocs.worldbank.org/en/doc/5d903e848db1d1b83e0ec8f744e55570-0350012021/related/CMO-Historical-Data-Monthly.xlsx"

# USGS ScienceBase (World production CSV for many commodities; filterでPHOSPHATE ROCKを抽出)
USGS_WORLD_ITEM_ID = "6798fd34d34ea8c18376e8ee"
USGS_WORLD_CSV = f"https://www.sciencebase.gov/catalog/file/get/{USGS_WORLD_ITEM_ID}?name=MCS2025_World_Data.csv"

st.title("Phosphate Rock Dashboard")
st.caption("Source: World Bank (Pink Sheet), USGS Mineral Commodity Summaries 2025")

@st.cache_data(ttl=24*3600)
def fetch_worldbank_price()->pd.DataFrame:
    """World Bank Pink Sheet（Monthly Excel）から 'Phosphate rock' 系列を抽出"""
    for url in (WB_URL_PRIMARY, WB_URL_FALLBACK):
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            xls = pd.ExcelFile(io.BytesIO(r.content))
            # 一般的に 'Monthly Prices' シートに横持ちで年次月次が並ぶ構造
            target_df = None
            try:
                df = pd.read_excel(xls, sheet_name="Monthly Prices")
                target_df = df
            except Exception:
                # フォールバック：全シート走査
                for sn in xls.sheet_names:
                    df = pd.read_excel(xls, sheet_name=sn)
                    if df.applymap(lambda x: isinstance(x, str) and "phosphate" in x.lower()).any().any():
                        target_df = df
                        break
            if target_df is None:
                continue

            # 1列目に商品名、以降が 1960M01 形式の列を想定
            # 列名を文字列化
            target_df.columns = [str(c) for c in target_df.columns]
            # 'Commodity' 的な列を推定（1列目）
            first_col = target_df.columns[0]
            row = target_df[target_df[first_col].astype(str).str.contains("phosphate rock", case=False, na=False)]
            if row.empty:
                # 'Phosphate' 単独で再検索（表記ゆれ対策）
                row = target_df[target_df[first_col].astype(str).str.contains("phosphate", case=False, na=False)]
            if row.empty:
                continue

            row = row.iloc[0]
            # 年月列を抽出（YYYYMmm 形式）
            ym_cols = [c for c in target_df.columns if re.match(r"^\d{4}M\d{2}$", c)]
            if not ym_cols:
                # 列方向で見つからない場合は、縦持ち構造を試す
                # 'Date' 列があり、他列に商品コードがある場合を想定
                # ここでは安全側でスキップして次URLへ
                continue

            data = pd.DataFrame({
                "date": [pd.to_datetime(f"{c[:4]}-{c[-2:]}-01") for c in ym_cols],
                "price_usd_per_t": [pd.to_numeric(row[c], errors="coerce") for c in ym_cols]
            }).dropna()
            data = data.sort_values("date").reset_index(drop=True)
            return data
        except Exception:
            continue
    raise RuntimeError("World Bankの価格データ取得に失敗しました")

@st.cache_data(ttl=24*3600)
def fetch_usgs_world()->pd.DataFrame:
    """USGS MCS 2025（World production CSV）から PHOSPHATE ROCK を抽出"""
    r = requests.get(USGS_WORLD_CSV, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.BytesIO(r.content))
    # 代表的な列名に合わせて汎用処理（列名は年によって微妙に変わることがある）
    # 期待カラム例：['Commodity','Country','Year','World_Production','Production']
    df.columns = [c.strip() for c in df.columns]
    # フィルタ（PHOSPHATE ROCK）
    mask_comm = df["Commodity"].astype(str).str.upper().str.contains("PHOSPHATE ROCK")
    df = df[mask_comm].copy()

    # 年度・国別の生産量推定列を選ぶ
    # 候補列（存在するものを使う）
    candidates = ["Production", "Mine Production", "Mine production", "World Production", "Production (kt)"]
    value_col = next((c for c in candidates if c in df.columns), None)
    if value_col is None:
        # ワイド→ロング形式の可能性（Yearが列名のケース）
        year_cols = [c for c in df.columns if re.match(r"^\d{4}$", str(c))]
        if year_cols:
            long_df = df.melt(
                id_vars=[c for c in df.columns if c not in year_cols],
                value_vars=year_cols,
                var_name="Year",
                value_name="Production"
            )
            long_df["Year"] = pd.to_numeric(long_df["Year"], errors="coerce")
            long_df["Production"] = pd.to_numeric(long_df["Production"], errors="coerce")
            long_df = long_df.rename(columns={"Country": "CountryName"})
            return long_df.dropna(subset=["Production", "Year"])
        else:
            raise RuntimeError("USGSの列構造が想定外でした（生産量列が見つかりません）")

    # すでにロング形式に近い場合
    # 列名標準化
    rename_map = {}
    if "Country" in df.columns:
        rename_map["Country"] = "CountryName"
    df = df.rename(columns=rename_map)

    # 型整形
    if "Year" in df.columns:
        df["Year"] = pd.to_numeric(df["Year"], errors="coerce")
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    df = df.rename(columns={value_col: "Production"})
    df = df.dropna(subset=["Production"])
    return df

# ---- データ取得 ----
price_ok, price_df = True, None
try:
    price_df = fetch_worldbank_price()
except Exception as e:
    price_ok = False

usgs_ok, usgs_df = True, None
try:
    usgs_df = fetch_usgs_world()
except Exception as e:
    usgs_ok = False

# ---- UI ----
left, right = st.columns([1.2, 1.0])

with left:
    st.subheader("Price: Rock Phosphate (Morocco), USD/t")
    if price_ok:
        latest = price_df.dropna().iloc[-1]
        prev_m = price_df.dropna().iloc[-2] if len(price_df) >= 2 else None
        prev_y = price_df[price_df["date"] == (latest["date"] - pd.DateOffset(years=1))]
        yoy = (latest["price_usd_per_t"] - prev_y["price_usd_per_t"].values[0]) / prev_y["price_usd_per_t"].values[0] * 100 if not prev_y.empty else None

        k1, k2, k3 = st.columns(3)
        k1.metric("Latest", f"{latest['price_usd_per_t']:.0f} USD/t", 
                  delta=f"{(latest['price_usd_per_t']-prev_m['price_usd_per_t']):+.0f}" if prev_m is not None else None)
        k2.metric("MoM", f"{((latest['price_usd_per_t']-prev_m['price_usd_per_t'])/prev_m['price_usd_per_t']*100):+.1f}%" if prev_m is not None else "–")
        k3.metric("YoY", f"{yoy:+.1f}%" if yoy is not None else "–")

        chart = (
            alt.Chart(price_df)
            .mark_line()
            .encode(x="date:T", y=alt.Y("price_usd_per_t:Q", title="USD per ton"), tooltip=["date:T","price_usd_per_t:Q"])
            .properties(height=320)
        )
        st.altair_chart(chart, use_container_width=True)
    else:
        st.warning("World Bankの価格データ取得に失敗しました。ページを再読み込みするか、後ほどお試しください。")

with right:
    st.subheader("Top Producers (Latest Year, USGS)")
    if usgs_ok:
        # 最新年
        latest_year = int(usgs_df["Year"].dropna().max())
        top = (
            usgs_df[usgs_df["Year"]==latest_year]
            .groupby("CountryName", as_index=False)["Production"].sum()
            .sort_values("Production", ascending=False)
            .head(10)
        )
        bar = (
            alt.Chart(top)
            .mark_bar()
            .encode(x=alt.X("Production:Q", title="Production"), y=alt.Y("CountryName:N", sort="-x", title="Country"), tooltip=["CountryName:N","Production:Q"])
            .properties(height=320)
        )
        st.altair_chart(bar, use_container_width=True)
        st.caption(f"USGS World production, Year={latest_year}")
    else:
        st.warning("USGS世界生産データ取得に失敗しました。後ほどお試しください。")

with st.expander("Show raw tables"):
    if price_ok:
        st.write("Price (World Bank, monthly)")
        st.dataframe(price_df.tail(36))
    if usgs_ok:
        st.write("USGS World Production (filtered: PHOSPHATE ROCK)")
        st.dataframe(usgs_df.head(200))

st.markdown("""
**Notes**  
- Price: World Bank *Commodities Price Data (“Pink Sheet”)* monthly Excel, series containing "Phosphate rock (Morocco)".  
- Supply: USGS *Mineral Commodity Summaries 2025* world CSV, filtered to "PHOSPHATE ROCK".  
- Caching: fetched once per day (24h TTL). Reload or revisit next day for fresh data.
""")
