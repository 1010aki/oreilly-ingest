import logging
import requests
from core.http_client import HttpClient
from core import create_default_kernel

# ログ設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Probe")

def probe():
    print("--- 診断開始 ---")
    
    # 1. クライアントの初期化（クッキー読み込み）
    try:
        client = HttpClient()
        print("✅ HttpClient初期化: OK")
    except Exception as e:
        print(f"❌ HttpClient初期化失敗: {e}")
        return

    # 2. 認証テスト（マイページへのアクセスで確認）
    # APIではなく、通常のページにアクセスしてログイン状態を確認
    try:
        resp = client.session.get("https://learning.oreilly.com/home/")
        print(f"📡 接続テスト(Home): Status Code = {resp.status_code}")
        if resp.status_code in [200]:
            print("✅ 認証: OK (ログインできています)")
        elif resp.status_code in [401, 403]:
            print("❌ 認証: 失敗 (クッキーが無効か期限切れです)")
            return # ここで終了
        else:
            print(f"⚠️ 認証: 不明なステータス ({resp.status_code})")
    except Exception as e:
        print(f"❌ 接続エラー: {e}")
        return

    # 3. 英語の有名本（Control）の確認
    known_isbn = "9781449373320" # Designing Data-Intensive Applications
    print(f"\n--- コントロール実験 (ISBN: {known_isbn}) ---")
    check_book(client, known_isbn)

    # 4. 問題の日本語本（Target）の確認
    target_isbn = "9784798187181" # 失敗した本
    print(f"\n--- ターゲット実験 (ISBN: {target_isbn}) ---")
    check_book(client, target_isbn)

def check_book(client, isbn):
    # A. 検索APIテスト
    search_url = f"https://learning.oreilly.com/api/v2/search/?query={isbn}&limit=1"
    print(f"running search: {search_url}")
    try:
        s_resp = client.session.get(search_url)
        print(f"  [Search API] Status: {s_resp.status_code}")
        if s_resp.status_code == 200:
            data = s_resp.json()
            results = data.get("results", [])
            if results:
                print(f"  ✅ Search Hit: Found ID = {results[0].get('archive_id')}")
            else:
                print(f"  ⚠️ Search Miss: 結果が空でした")
        else:
            print(f"  ❌ Search Error")
    except Exception as e:
        print(f"  ❌ Search Exception: {e}")

    # B. 直接取得APIテスト (EPUB Endpoint)
    epub_url = f"https://learning.oreilly.com/api/v2/epubs/urn:orm:book:{isbn}/"
    print(f"running fetch: {epub_url}")
    try:
        e_resp = client.session.get(epub_url)
        print(f"  [EPUB API]   Status: {e_resp.status_code}")
        if e_resp.status_code == 200:
            print(f"  ✅ Fetch Success: データ取得成功")
        elif e_resp.status_code == 404:
            print(f"  ❌ Fetch Failed: 404 Not Found (このIDではアクセスできません)")
        else:
            print(f"  ❌ Fetch Error: {e_resp.status_code}")
    except Exception as e:
        print(f"  ❌ Fetch Exception: {e}")

if __name__ == "__main__":
    probe()