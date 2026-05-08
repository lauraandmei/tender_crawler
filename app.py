import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
from datetime import date, timedelta
import urllib3
import re
import io
import time

# 忽略 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(page_title="政府標案自動化檢索系統", layout="wide")
st.title("🏛️ 儲能標案抓取系統")

# --- 邏輯優化：日期預填 ---
today = date.today()
this_monday = today - timedelta(days=today.weekday())
default_end_date = today 

if 'search_results' not in st.session_state:
    st.session_state.search_results = None
if 'extra_keywords' not in st.session_state:
    st.session_state.extra_keywords = []

# --- 側邊欄設定 ---
with st.sidebar:
    st.header("查詢設定")
    base_options = ["電網", "儲能" ]  #"變壓器", "配電", "停電", "太陽能", "風電"
    selected_base = st.multiselect("從選單勾選關鍵字", options=base_options, default=["電網"])
    custom_input = st.text_input("➕ 手動新增關鍵字", help="可用逗點隔開，例如：綠,電")
    
    if custom_input:
        new_kws = [k.strip() for k in re.split('[,，]', custom_input) if k.strip()]
        for nk in new_kws:
            if nk not in st.session_state.extra_keywords and nk not in selected_base:
                st.session_state.extra_keywords.append(nk)
    
    if st.session_state.extra_keywords:
        st.write("自訂關鍵字：")
        st.info(", ".join(st.session_state.extra_keywords))
        if st.button("清空自訂關鍵字"):
            st.session_state.extra_keywords = []
            st.rerun()

    all_keywords = list(set(selected_base + st.session_state.extra_keywords))
    st.markdown("---")
    start_date = st.date_input("公告起始日", value=this_monday)
    end_date = st.date_input("公告結束日", value=default_end_date)
    st.markdown("---")
    search_btn = st.button("🚀 執行深度查詢", type="primary", use_container_width=True)

def get_award_method(session, detail_url):
    """深度優化：解決 Session 遺失與精準定位問題"""
    if not detail_url or "http" not in detail_url:
        return "連結無效"
    
    # 💡 模擬更真實的 Header
    custom_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://web.pcc.gov.tw/prkms/tender/common/basic/readTenderBasic"
    }

    try:
        # 安全延遲，避免過快被封鎖
        time.sleep(0.8)
        
        # 💡 使用原本的 session 帶入 custom_headers 進行請求
        res = session.get(detail_url, headers=custom_headers, verify=False, timeout=15)
        res.encoding = 'utf-8' # 強制編碼
        
        if res.status_code != 200:
            return f"連線異常({res.status_code})"

        detail_soup = BeautifulSoup(res.text, "html.parser")
        
        # --- 定位策略 1：精準尋找 th 內容為「決標方式」的隔壁 td ---
        # 針對你截圖中的結構優化
        all_ths = detail_soup.find_all("th")
        for th in all_ths:
            th_text = th.get_text(strip=True)
            if "決標方式" == th_text: # 精確比對
                td = th.find_next_sibling("td")
                if td:
                    full_text = td.get_text(separator=" ", strip=True)
                    # 剔除按鈕文字
                    final_text = full_text.replace("採購評選委員名單", "").strip()
                    if final_text: return final_text

        # --- 定位策略 2：如果策略 1 失敗，改用正則表達式模糊搜尋 ---
        target = detail_soup.find(string=re.compile(r"決標方式"))
        if target:
            parent = target.find_parent(["th", "td"])
            if parent:
                next_td = parent.find_next_sibling("td")
                if next_td:
                    txt = next_td.get_text(strip=True).replace("採購評選委員名單", "").strip()
                    if txt: return txt

        return "未發現欄位"
        
    except requests.exceptions.Timeout:
        return "連線超時"
    except Exception as e:
        return f"解析出錯"

def scrape_formal_115(key, s_date, e_date, progress_placeholder):
    """深度爬蟲核心邏輯"""
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
        
        valid_rows = []
        for row in all_rows:
            tds = row.find_all(["td", "th"])
            if len(tds) < 9: continue
            org_name = tds[1].get_text(strip=True)
            pub_date = tds[6].get_text(strip=True)
            if not org_name or org_name in ["機關名稱", "註：", "◎", "標案名稱"] or "/" not in pub_date: continue
            valid_rows.append(tds)

        total_valid = len(valid_rows)
        for idx, tds in enumerate(valid_rows):
            org_name = tds[1].get_text(strip=True)
            td2 = tds[2]
            
            # 解析標案名稱
            tender_name = ""
            script_tag = td2.find("script")
            if script_tag and script_tag.string:
                match = re.search(r'pageCode2Img\(\s*["\'](.*?)["\']\s*\)', script_tag.string)
                if match: tender_name = match.group(1).strip()
            if not tender_name:
                a_tag = td2.find("a")
                if a_tag: tender_name = a_tag.get_text(strip=True)
            tender_name = tender_name.replace("(更正公告)", "").strip()
            
            # 案號與連結
            clean_strings = [s for s in td2.stripped_strings if "Geps3" not in s and "var hw" not in s and "更正公告" not in s]
            tender_id = clean_strings[0] if clean_strings else ""
            pub_date = tds[6].get_text(strip=True)
            real_a = td2.find("a", href=True) or tds[-1].find("a", href=True)
            href = real_a.get("href", "") if real_a else ""
            full_link = base_url + href if href and not href.lower().startswith('javascript') else index_url

            # 進入內頁抓取決標方式
            progress_placeholder.write(f"🔍 關鍵字【{key}】: 正在分析內頁 ({idx+1}/{total_valid})...")
            award_method = get_award_method(session, full_link)

            data_list.append({
                "搜尋關鍵字": key,
                "機關名稱": org_name,
                "標案案號": tender_id,
                "標案名稱": tender_name,
                "決標方式": award_method,
                "公告日期": pub_date,
                "截止投標": tds[7].get_text(strip=True),
                "預算金額": tds[8].get_text(strip=True),
                "功能連結": full_link
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
            temp_df = scrape_formal_115(kw, start_date, end_date, status_msg)
            if not temp_df.empty:
                all_dfs.append(temp_df)
            progress_bar.progress((i + 1) / len(all_keywords))
        
        status_msg.empty()
        if all_dfs:
            combined_df = pd.concat(all_dfs, ignore_index=True).drop_duplicates(subset=['標案案號', '機關名稱'])
            st.session_state.search_results = combined_df
        else:
            st.session_state.search_results = None
            st.error(f"❌ 查無資料。")

if st.session_state.search_results is not None:
    df = st.session_state.search_results
    st.success(f"✅ 完成深度檢索！")
    
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='標案清單')
    
    st.download_button(
        label="📥 下載 Excel",
        data=buffer.getvalue(),
        file_name=f"標案清單_{date.today().strftime('%Y%m%d')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary"
    )
    
    st.markdown("---")
    st.dataframe(
        df,
        column_config={"功能連結": st.column_config.LinkColumn("🔗 連結", display_text="點擊")},
        use_container_width=True,
        hide_index=True
    )