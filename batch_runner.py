import time
import random
import logging
from pathlib import Path
from core import create_default_kernel

# ---------------------------------------------------------
# トモアキさんのISBNリスト
# ---------------------------------------------------------
TARGET_ISBNS = [
    "9784798187181", # 先ほど失敗した本
    # ... 他のISBN
]

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    kernel = create_default_kernel()
    book_plugin = kernel["book"]
    downloader = kernel["downloader"]
    
    output_dir = Path("output")
    formats = ["pdf", "epub"]
    skip_images = False
    
    logger.info(f"開始: 合計 {len(TARGET_ISBNS)} 冊の処理を開始します。")

    for i, isbn in enumerate(TARGET_ISBNS):
        try:
            logger.info(f"[{i+1}/{len(TARGET_ISBNS)}] ISBN: {isbn} の処理中...")
            
            # 変数の初期化
            book_id = None
            title = "Unknown"

            # 1. まず検索してみる
            results = book_plugin.search(isbn)
            
            if results:
                # 検索ヒットした場合
                target_book = results[0]
                book_id = target_book['id']
                title = target_book['title']
                logger.info(f"  -> 検索ヒット: {title} (ID: {book_id})")
            else:
                # 検索ヒットしなかった場合（ここが修正ポイント！）
                logger.info(f"  -> 検索で見つかりませんでした。ISBNを直接IDとして試行します...")
                try:
                    # ISBNをIDと仮定して、直接メタデータを取得しに行く
                    # fetch()はIDが存在しなければエラーになるので、存在確認も兼ねる
                    book_info = book_plugin.fetch(isbn)
                    book_id = book_info['id']
                    title = book_info.get('title', 'Unknown Title')
                    logger.info(f"  -> 直接特定成功: {title}")
                except Exception as e:
                    logger.warning(f"  -> ❌ 失敗: このISBNは検索も直接指定もできませんでした。")
                    continue

            # 2. ダウンロード実行
            logger.info("  -> ダウンロード開始...")
            result = downloader.download(
                book_id=book_id,
                output_dir=output_dir,
                formats=formats,
                skip_images=skip_images,
                progress_callback=lambda p: print(f"    Progress: {p.percentage}% - {p.status}", end="\r")
            )
            
            print("") # 改行
            logger.info(f"  -> ✅ 完了: {title}")

            # 3. 待機（BAN回避）
            wait_time = random.uniform(10, 30) 
            logger.info(f"  -> 休憩中... ({wait_time:.1f}秒)")
            time.sleep(wait_time)

        except Exception as e:
            logger.error(f"エラー発生 ISBN: {isbn} - {str(e)}")
            time.sleep(5)

if __name__ == "__main__":
    main()