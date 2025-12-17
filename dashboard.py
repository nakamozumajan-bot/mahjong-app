import streamlit as st
import pandas as pd
import plotly.express as px
from supabase import create_client, Client

# --- 1. Supabase設定 ---
SUPABASE_URL = "https://mikcjkqvdjkqkcgsufkh.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1pa2Nqa3F2ZGprcWtjZ3N1ZmtoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjM4NTU5ODIsImV4cCI6MjA3OTQzMTk4Mn0.O4gR7N9279zAsgunCDZre4FJAM2gHT4uwUlFkjEhm-k"

st.set_page_config(page_title="🀄 麻雀データ分析", layout="wide")

@st.cache_resource
def init_connection():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_connection()

# --- 2. データ取得と加工 ---
@st.cache_data(ttl=600)
def load_data():
    response = supabase.table("app_data").select("*").eq("key", "mahjong_variable_score_history_int").execute()
    
    if not response.data:
        return pd.DataFrame()
    
    raw_data = response.data[0]['value']
    records = []
    
    for game in raw_data:
        game_id = game.get('id')
        date = game.get('date')
        group = game.get('group')
        game_type = game.get('gameType', 'standard')
        # ★修正: その対局の参加人数を取得 (デフォルトは4)
        player_count = int(game.get('playerCount', 4))
        
        if game_type == 'standard': 
            payouts = game.get('payouts', {})
            for player, result in payouts.items():
                rank = result.get('rank', 0)
                records.append({
                    'id': game_id,
                    'date': pd.to_datetime(date),
                    'group': group,
                    'player': player,
                    'score': result.get('total', 0),
                    'rank': rank,
                    'score_raw': result.get('scorePt', 0),
                    'player_count_in_game': player_count, # この対局の人数
                    # ★修正: 人数と順位が同じならラスと判定
                    'is_last': rank == player_count
                })
    
    return pd.DataFrame(records)

df = load_data()

# ▼▼▼ 日付フィルタ (必要に応じて変更してください) ▼▼▼
# start_date = pd.Timestamp("2024-12-02")
# if not df.empty:
#    df = df[df['date'] >= start_date]
# ▲▲▲ ここまで ▲▲▲

# --- 3. ダッシュボード表示 ---
st.title("🀄 公大中麻雀倶楽部 - 高度統計分析")

if df.empty:
    st.warning("データがありません。")
else:
    st.sidebar.header("絞り込み")
    
    # グループフィルタ
    all_groups = list(df['group'].unique())
    selected_groups = st.sidebar.multiselect("グループ選択", options=all_groups, default=all_groups)
    
    # プレイヤーフィルタ（表示人数が多いと見づらいため）
    all_players = sorted(list(df['player'].unique()))
    selected_players = st.sidebar.multiselect("プレイヤー選択", options=all_players, default=all_players)

    # フィルタ適用
    filtered_df = df[
        (df['group'].isin(selected_groups)) & 
        (df['player'].isin(selected_players))
    ].copy()

    tab1, tab2, tab3 = st.tabs(["📊 総合成績", "📈 推移グラフ", "🤝 相性分析"])

    with tab1:
        st.subheader("プレイヤー別成績概要")
        
        # 集計
        summary = filtered_df.groupby('player').agg(
            Games=('id', 'count'),
            Total_Score=('score', 'sum'),
            Avg_Score=('score', 'mean'),
            Avg_Rank=('rank', 'mean'),
            Top_Count=('rank', lambda x: (x==1).sum()),
            # ★修正: 事前に計算した is_last フラグを集計
            Last_Count=('is_last', 'sum') 
        ).reset_index()
        
        # 計算
        summary['Top_Rate'] = (summary['Top_Count'] / summary['Games'] * 100).round(1)
        summary['Last_Rate'] = (summary['Last_Count'] / summary['Games'] * 100).round(1)
        summary['Avg_Rank'] = summary['Avg_Rank'].round(2)
        summary['Total_Score'] = summary['Total_Score'].round(0)
        
        summary = summary.sort_values('Total_Score', ascending=False)
        
        st.dataframe(
            summary[['player', 'Games', 'Total_Score', 'Avg_Rank', 'Top_Rate', 'Last_Rate']],
            column_config={
                "Games": "対局数",
                "Total_Score": "合計スコア",
                "Avg_Rank": "平均順位",
                "Top_Rate": st.column_config.NumberColumn("トップ率", format="%.1f%%"),
                "Last_Rate": st.column_config.NumberColumn("ラス率", format="%.1f%%"),
            },
            hide_index=True,
            use_container_width=True
        )

    with tab2:
        st.subheader("累計スコア推移")
        if not filtered_df.empty:
            filtered_df_sorted = filtered_df.sort_values(['date', 'id'])
            filtered_df_sorted['cumulative_score'] = filtered_df_sorted.groupby('player')['score'].cumsum()
            
            fig = px.line(
                filtered_df_sorted, 
                x='date', y='cumulative_score', color='player', markers=True,
                title='累計スコア推移', labels={'date': '日時', 'cumulative_score': '累計スコア (pt)'}
            )
            fig.update_layout(hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)

    with tab3:
        st.subheader("同卓時の相性分析")
        st.caption("あるプレイヤー(行)が、別のプレイヤー(列)と同卓した時の「平均順位」")
        
        target_players = summary['player'].tolist()
        matrix = pd.DataFrame(index=target_players, columns=target_players)
        
        for p1 in target_players:
            for p2 in target_players:
                if p1 == p2:
                    matrix.loc[p1, p2] = None
                else:
                    games_p1 = set(filtered_df[filtered_df['player'] == p1]['id'])
                    games_p2 = set(filtered_df[filtered_df['player'] == p2]['id'])
                    common_games = games_p1.intersection(games_p2)
                    
                    if common_games:
                        avg_rank = filtered_df[
                            (filtered_df['player'] == p1) & 
                            (filtered_df['id'].isin(common_games))
                        ]['rank'].mean()
                        matrix.loc[p1, p2] = round(avg_rank, 2)
                    else:
                        matrix.loc[p1, p2] = None

        if not matrix.empty and not matrix.dropna(how='all').empty:
            fig_heat = px.imshow(
                matrix.astype(float), text_auto=True, color_continuous_scale='RdBu_r',
                labels=dict(x="同卓相手", y="対象プレイヤー", color="平均順位"),
            )
            st.plotly_chart(fig_heat, use_container_width=True)
        else:
            st.info("データが不足しているため表示できません")