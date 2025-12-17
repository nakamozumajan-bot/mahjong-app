import streamlit as st
import pandas as pd
import plotly.express as px
from supabase import create_client, Client
import json

# --- 1. Supabase設定 (HTMLと同じもの) ---
SUPABASE_URL = "https://mikcjkqvdjkqkcgsufkh.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1pa2Nqa3F2ZGprcWtjZ3N1ZmtoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjM4NTU5ODIsImV4cCI6MjA3OTQzMTk4Mn0.O4gR7N9279zAsgunCDZre4FJAM2gHT4uwUlFkjEhm-k"

# ページ設定
st.set_page_config(page_title="🀄 麻雀データ分析", layout="wide")

@st.cache_resource
def init_connection():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_connection()

# --- 2. データ取得と加工 ---
@st.cache_data(ttl=600) # 10分間キャッシュ
def load_data():
    # app_dataテーブルから対局履歴を取得
    response = supabase.table("app_data").select("*").eq("key", "mahjong_variable_score_history_int").execute()
    
    if not response.data:
        return []
    
    # JSONデータをパース
    raw_data = response.data[0]['value']
    
    # 分析しやすい形式(縦持ちデータ)に変換
    records = []
    for game in raw_data:
        game_id = game.get('id')
        date = game.get('date')
        group = game.get('group')
        game_type = game.get('gameType', 'standard')
        
        # チップ精算や引き継ぎデータを除外したい場合はここでフィルタ
        if game_type == 'standard': 
            payouts = game.get('payouts', {})
            for player, result in payouts.items():
                records.append({
                    'id': game_id,
                    'date': pd.to_datetime(date),
                    'group': group,
                    'player': player,
                    'score': result.get('total', 0),
                    'rank': result.get('rank', 0),
                    'score_raw': result.get('scorePt', 0)
                })
    
    return pd.DataFrame(records)

# データを読み込む
df = load_data()

# --- 3. ダッシュボード表示 ---
st.title("🀄 公大中麻雀倶楽部 - 高度統計分析")

if df.empty:
    st.warning("データがありません。まずはWebアプリから対局結果を入力してください。")
else:
    # サイドバーでフィルタリング
    st.sidebar.header("絞り込み")
    selected_groups = st.sidebar.multiselect(
        "グループ選択", 
        options=df['group'].unique(),
        default=df['group'].unique()
    )
    
    # フィルタ適用
    filtered_df = df[df['group'].isin(selected_groups)].copy()

    # --- タブ1: 総合成績 ---
    tab1, tab2, tab3 = st.tabs(["📊 総合成績", "📈 推移グラフ", "🤝 相性分析"])

    with tab1:
        st.subheader("プレイヤー別成績概要")
        
        # 集計処理
        summary = filtered_df.groupby('player').agg(
            Games=('id', 'count'),
            Total_Score=('score', 'sum'),
            Avg_Score=('score', 'mean'),
            Avg_Rank=('rank', 'mean'),
            Top_Count=('rank', lambda x: (x==1).sum()),
            Last_Count=('rank', lambda x: (x==4).sum()) # 4人打ち想定
        ).reset_index()
        
        # トップ率・ラス率計算
        summary['Top_Rate'] = (summary['Top_Count'] / summary['Games'] * 100).round(1)
        summary['Last_Rate'] = (summary['Last_Count'] / summary['Games'] * 100).round(1)
        summary['Avg_Rank'] = summary['Avg_Rank'].round(2)
        summary['Total_Score'] = summary['Total_Score'].round(0)
        
        # スコア順にソート
        summary = summary.sort_values('Total_Score', ascending=False)
        
        # データフレーム表示（装飾付き）
        st.dataframe(
            summary[['player', 'Games', 'Total_Score', 'Avg_Rank', 'Top_Rate', 'Last_Rate']],
            column_config={
                "Top_Rate": st.column_config.NumberColumn("トップ率(%)", format="%.1f%%"),
                "Last_Rate": st.column_config.NumberColumn("ラス率(%)", format="%.1f%%"),
            },
            hide_index=True,
            use_container_width=True
        )

    # --- タブ2: 推移グラフ ---
    with tab2:
        st.subheader("累計スコア推移")
        
        # 日付・ID順にソートして累計を計算
        filtered_df = filtered_df.sort_values(['date', 'id'])
        filtered_df['cumulative_score'] = filtered_df.groupby('player')['score'].cumsum()
        
        # Plotlyでインタラクティブなグラフを作成
        fig = px.line(
            filtered_df, 
            x='date', 
            y='cumulative_score', 
            color='player',
            markers=True,
            title='累計スコア推移',
            labels={'date': '日時', 'cumulative_score': '累計スコア (pt)'}
        )
        fig.update_layout(hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)

    # --- タブ3: 相性分析 (ヒートマップ風) ---
    with tab3:
        st.subheader("同卓時の相性分析")
        st.caption("あるプレイヤー(行)が、別のプレイヤー(列)と同卓した時の「平均順位」を表示します。（低いほど良い）")
        
        # 全プレイヤーリスト
        players = summary['player'].tolist()
        
        # 相性マトリックス作成
        matrix = pd.DataFrame(index=players, columns=players)
        
        for p1 in players:
            for p2 in players:
                if p1 == p2:
                    matrix.loc[p1, p2] = None # 自分自身は除外
                else:
                    # p1とp2が同卓したゲームIDを取得
                    games_p1 = set(filtered_df[filtered_df['player'] == p1]['id'])
                    games_p2 = set(filtered_df[filtered_df['player'] == p2]['id'])
                    common_games = games_p1.intersection(games_p2)
                    
                    if common_games:
                        # 同卓時のp1の平均順位を計算
                        avg_rank = filtered_df[
                            (filtered_df['player'] == p1) & 
                            (filtered_df['id'].isin(common_games))
                        ]['rank'].mean()
                        matrix.loc[p1, p2] = round(avg_rank, 2)
                    else:
                        matrix.loc[p1, p2] = None

        # ヒートマップ表示 (Plotly)
        fig_heat = px.imshow(
            matrix.astype(float),
            text_auto=True,
            color_continuous_scale='RdBu_r', # 赤=悪い(順位大きい), 青=良い(順位小さい)
            labels=dict(x="同卓相手", y="対象プレイヤー", color="平均順位"),
            title="対戦相手別の平均順位 (青いほど得意・赤いほど苦手)"
        )
        st.plotly_chart(fig_heat, use_container_width=True)