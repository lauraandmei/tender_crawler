import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import date, timedelta
import urllib3
import re
import io

# 忽略 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(page_title="政府標案多關鍵字抓取", layout="wide")
st.title("🏛️ 儲能標案抓取系統")

# --- 邏輯優化：計算本日所屬週的週一與今日 ---
today = date.today()
this_monday = today - timedelta(days=today.weekday())
default_end_date = today 

# --- 初始化 Session State ---
if 'search_results' not in st.session_state:
    st.session_state.search_results = None
if 'extra_keywords' not in st.session_state:
    st.session_state.extra_keywords = []

# --- 側邊欄設定 ---
with st.sidebar:
    st.header("查詢設定")
    
    # 1. 預設多選關鍵字
    base_options = ["電網", "儲能" ]  #"變壓器", "配電", "停電", "太陽能", "風電"
    selected_base = st.multiselect(
        "從選單勾選關鍵字",
        options=base_options,
        default=["電網", "儲能"]
    )
    
    # 2. 手動新增 (支援逗點拆分)
    st.markdown("---")
    custom_input = st.text_input("➕ 手動新增關鍵字", help="可用逗點隔開多個詞，例如：綠,電")
    
    if custom_input:
        # 💡 優化：支援全形「，」與半形「,」拆分，並去除多餘空格
        new_kws = [k.strip() for k in re.split('[,，]', custom_input) if k.strip()]
        for nk in new_kws:
            if nk not in st.session_state.extra_keywords and nk not in selected_base:
                st.session_state.extra_keywords.append(nk)
    
    # 3. 顯示目前「手動新增」的標籤，並提供清空按鈕
    if st.session_state.extra_keywords:
        st.write("自訂關鍵字：")
        st.info(", ".join(st.session_state.extra_keywords))
        if st.button("清空自訂關鍵字"):
            st.session_state.extra_keywords = []
            st.rerun()

    # 彙整所有關鍵字
    all_keywords = list(set(selected_base + st.session_state.extra_keywords))
    
    st.markdown("---")
    # 4. 日期範圍設定
    start_date = st.date_input("公告起始日", value=this_monday)
    end_date = st.date_input("公告結束日", value=default_end_date)
    
    st.write("結束日請勿選取未來日期")
    st.markdown("---")
    search_btn = st.button("🚀 執行多關鍵字查詢", type="primary", use_container_width=True)

def scrape_formal_115(key, s_date, e_date):
    """破解 JS 陷阱的單一關鍵字爬蟲核心"""
    s_date_str = s_date.strftime("%Y/%m/%d")
    e_date_str = e_date.strftime("%Y/%m/%d")
    
    base_url = "https://web.pcc.gov.tw"
    index_url = f"{base_url}/prkms/tender/common/basic/indexTenderBasic"
    action_url = f"{base_url}/prkms/tender/common/basic/readTenderBasic"
    
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": index_url,
    }

    try:
        session.get(index_url, headers=headers, verify=False, timeout=10)
        payload = {
            "pageSize": "50", "firstSearch": "true", "searchType": "basic",
            "isBinding": "N", "isLogIn": "N", "level_1": "on",
            "orgName": "", "orgId": "", "tenderName": key,
            "tenderId": "", "tenderType": "TENDER_DECLARATION",
            "tenderWay": "TENDER_WAY_ALL_DECLARATION", "dateType": "isDate",
            "tenderStartDate": s_date_str, "tenderEndDate": e_date_str,
            "radProctrgCate": "RAD_PROCTRG_CATE_2", "policyAdvocacy": ""
        }
        res = session.get(action_url, params=payload, headers=headers, verify=False, timeout=20)
        soup = BeautifulSoup(res.text, "html.parser")
        data_list = []
        all_rows = soup.find_all("tr")
        
        for row in all_rows:
            tds = row.find_all(["td", "th"])
            if len(tds) < 9: continue
            org_name = tds[1].get_text(strip=True)
            pub_date = tds[6].get_text(strip=True)
            if not org_name or org_name in ["機關名稱", "註：", "◎", "標案名稱"] or "/" not in pub_date: continue

            td2 = tds[2]
            tender_name = ""
            script_tag = td2.find("script")
            if script_tag and script_tag.string:
                match = re.search(r'pageCode2Img\(\s*["\'](.*?)["\']\s*\)', script_tag.string)
                if match: tender_name = match.group(1).strip()
            
            if not tender_name:
                a_tag = td2.find("a")
                if a_tag: tender_name = a_tag.get_text(strip=True)
            
            tender_name = tender_name.replace("(更正公告)", "").strip()
            
            clean_strings = [s for s in td2.stripped_strings if "Geps3" not in s and "var hw" not in s and "更正公告" not in s]
            tender_id = clean_strings[0] if clean_strings else ""

            real_a = td2.find("a", href=True) or tds[-1].find("a", href=True)
            href = real_a.get("href", "") if real_a else ""
            full_link = base_url + href if href and not href.lower().startswith('javascript') else index_url

            data_list.append({
                "搜尋關鍵字": key,
                "機關名稱": org_name, "標案案號": tender_id, "標案名稱": tender_name,
                "公告日期": pub_date, "截止投標": tds[7].get_text(strip=True),
                "預算金額": tds[8].get_text(strip=True), "功能連結": full_link
            })

        return pd.DataFrame(data_list)
    except Exception:
        return pd.DataFrame()

# --- 邏輯處理區 ---

if search_btn:
    if not all_keywords:
        st.warning("⚠️ 請至少選擇或輸入一個關鍵字。")
    else:
        all_dfs = []
        status_msg = st.empty()
        progress_bar = st.progress(0)
        
        for i, kw in enumerate(all_keywords):
            status_msg.write(f"⏳ 正在檢索關鍵字：**{kw}** ({i+1}/{len(all_keywords)})")
            temp_df = scrape_formal_115(kw, start_date, end_date)
            if not temp_df.empty:
                all_dfs.append(temp_df)
            progress_bar.progress((i + 1) / len(all_keywords))
        
        status_msg.empty()
        
        if all_dfs:
            combined_df = pd.concat(all_dfs, ignore_index=True)
            combined_df = combined_df.drop_duplicates(subset=['標案案號', '機關名稱'])
            st.session_state.search_results = combined_df
        else:
            st.session_state.search_results = None
            st.error(f"❌ 在指定期間內，所有關鍵字 ({', '.join(all_keywords)}) 均查無資料。")

# 顯示結果
if st.session_state.search_results is not None:
    df = st.session_state.search_results
    st.success(f"✅ 彙整完成！不重複標案共計：{len(df)} 筆")
    
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='政府標案清單')
    
    st.download_button(
        label="📥 下載彙整 Excel 檔案",
        data=buffer.getvalue(),
        file_name=f"標案彙整_{date.today().strftime('%Y%m%d')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary"
    )
    
    st.markdown("---")
    st.dataframe(
        df,
        column_config={"功能連結": st.column_config.LinkColumn("🔗 查看詳情", display_text="開啟連結")},
        use_container_width=True,
        hide_index=True
    )