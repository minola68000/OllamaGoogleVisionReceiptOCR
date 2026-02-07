#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2025OCRフォルダ内の全PDF/画像ファイルを処理してMermaid表を作成
google_vision_ollama_prompt_only.pyを逐次呼び出して処理

提案2: デバッグログ機能を追加
"""

import sys
import json
import subprocess
import time
from pathlib import Path
from datetime import datetime
import argparse  # 提案2: コマンドライン引数解析

def call_google_vision_ollama(file_path, model_name="gemma3:12b", max_retries=3, retry_delay=5):
    """google_vision_ollama_prompt_only.pyを呼び出して結果を取得（失敗時リトライ付き）"""
    script_path = Path(__file__).parent / "google_vision_ollama_prompt_only.py"

    if not script_path.exists():
        print(f"エラー: {script_path} が見つかりません", file=sys.stderr)
        return None, None  # 提案2: subprocess結果も返す

    last_result = None  # 提案2: 最後のsubprocess結果を保存
    for attempt in range(1, max_retries + 1):
        try:
            # サブプロセスでスクリプトを実行
            result = subprocess.run(
                [sys.executable, str(script_path), str(file_path), model_name],
                capture_output=True,
                text=True,
                timeout=300  # 5分のタイムアウト
            )
            last_result = result  # 提案2: 結果を保存

            if result.returncode != 0:
                print(f"  エラー: 処理に失敗しました（{attempt}/{max_retries}回目）", file=sys.stderr)
                print(f"  {result.stderr}", file=sys.stderr)
                if attempt < max_retries:
                    print(f"  {retry_delay}秒後にリトライします...", file=sys.stderr)
                    time.sleep(retry_delay)
                    continue
                return None, last_result  # 提案2: subprocess結果も返す
            
            # 出力からJSONを抽出
            output = result.stdout
            
            # "JSON形式:" の後のJSONを探す
            json_section_start = output.find("JSON形式:")
            if json_section_start != -1:
                # "JSON形式:" の後の部分からJSONを探す
                json_candidate = output[json_section_start:]
                json_start = json_candidate.find('{')
                if json_start != -1:
                    json_candidate = json_candidate[json_start:]
                    # 最初の完全なJSONオブジェクトを抽出
                    brace_count = 0
                    json_end = 0
                    for i, char in enumerate(json_candidate):
                        if char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                json_end = i + 1
                                break
                    
                    if json_end > 0:
                        json_text = json_candidate[:json_end]
                        try:
                            result_data = json.loads(json_text)
                            return result_data, last_result  # 提案2: subprocess結果も返す
                        except json.JSONDecodeError as e:
                            print(f"  JSON解析エラー: {e}（{attempt}/{max_retries}回目）", file=sys.stderr)
                            # フォールバック: 最初の{から最後の}まで
                            # リトライ可能性があるので、すぐには諦めない
            
            # フォールバック: 最初の{から最後の}までを探す
            json_start = output.find('{')
            if json_start != -1:
                # 最初の完全なJSONオブジェクトを抽出
                brace_count = 0
                json_end = 0
                for i in range(json_start, len(output)):
                    if output[i] == '{':
                        brace_count += 1
                    elif output[i] == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            json_end = i + 1
                            break
                
                if json_end > json_start:
                    json_text = output[json_start:json_end]
                    try:
                        result_data = json.loads(json_text)
                        return result_data, last_result  # 提案2: subprocess結果も返す
                    except json.JSONDecodeError as e:
                        print(f"  JSON解析エラー: {e}（フォールバック）（{attempt}/{max_retries}回目）", file=sys.stderr)
                        # この後リトライ

            print(f"  警告: JSONが見つかりませんでした（{attempt}/{max_retries}回目）", file=sys.stderr)
            if attempt < max_retries:
                print(f"  {retry_delay}秒後にリトライします...", file=sys.stderr)
                time.sleep(retry_delay)
                continue
            return None, last_result  # 提案2: subprocess結果も返す

        except subprocess.TimeoutExpired:
            print(f"  タイムアウト: 処理が5分を超えました（{attempt}/{max_retries}回目）", file=sys.stderr)
            if attempt < max_retries:
                print(f"  {retry_delay}秒後にリトライします...", file=sys.stderr)
                time.sleep(retry_delay)
                continue
            return None, last_result  # 提案2: subprocess結果も返す
        except Exception as e:
            print(f"  エラー: {e}（{attempt}/{max_retries}回目）", file=sys.stderr)
            if attempt < max_retries:
                print(f"  {retry_delay}秒後にリトライします...", file=sys.stderr)
                time.sleep(retry_delay)
                continue
            return None, last_result  # 提案2: subprocess結果も返す

def save_debug_info(file_path, error_dir, reason, result=None, exception=None, ocr_output=None, llm_output=None):
    """提案2: デバッグ情報をファイルに保存"""
    base_name = file_path.stem

    # エラー情報をJSONで保存
    error_info = {
        'filename': file_path.name,
        'reason': reason,
        'timestamp': datetime.now().isoformat(),
        'result': result,
        'exception': exception
    }

    error_file = error_dir / f"{base_name}_error.json"
    with open(error_file, 'w', encoding='utf-8') as f:
        json.dump(error_info, f, ensure_ascii=False, indent=2)

    # OCR出力を保存（ある場合）
    if ocr_output:
        ocr_file = error_dir / f"{base_name}_ocr.txt"
        with open(ocr_file, 'w', encoding='utf-8') as f:
            f.write(ocr_output)

    # LLM応答を保存（ある場合）
    if llm_output:
        llm_file = error_dir / f"{base_name}_llm_response.txt"
        with open(llm_file, 'w', encoding='utf-8') as f:
            f.write(llm_output)

    print(f"  デバッグ情報を保存: {error_file.name}", file=sys.stderr)

def generate_failure_report(results, error_dir):
    """提案2: 失敗レポートを生成"""
    failed_results = [r for r in results if r['amount'] <= 0]
    success_results = [r for r in results if r['amount'] > 0]

    report = f"""# レシート処理失敗レポート

**生成日時**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## サマリー
- 総件数: {len(results)}件
- 成功: {len(success_results)}件 ({len(success_results)/len(results)*100:.1f}%)
- 失敗: {len(failed_results)}件 ({len(failed_results)/len(results)*100:.1f}%)

## 失敗内訳

"""

    # 失敗理由を分類
    failure_categories = {}
    for result in failed_results:
        company = result.get('company_name', '不明')
        if company == '抽出失敗':
            category = 'AMOUNT_ZERO'
        elif 'エラー' in company:
            category = 'EXCEPTION'
        elif result['amount'] == 0:
            category = 'AMOUNT_ZERO'
        else:
            category = 'OTHER'

        if category not in failure_categories:
            failure_categories[category] = []
        failure_categories[category].append(result)

    for category, items in failure_categories.items():
        report += f"### {category}: {len(items)}件\n"
        for item in items:
            report += f"- {item['filename']}\n"
        report += "\n"

    report += "## 失敗詳細\n\n"
    for result in failed_results:
        report += f"### {result['filename']}\n"
        report += f"- 事業者名: {result.get('company_name', '不明')}\n"
        report += f"- 金額: {result.get('amount', 0):,}円\n"
        report += f"- 日付: {result.get('date', '不明')}\n"

        # エラーファイルがあれば参照
        error_file = error_dir / f"{Path(result['filename']).stem}_error.json"
        if error_file.exists():
            report += f"- エラー詳細: [{error_file.name}]({error_file.name})\n"
        report += "\n"

    # レポートを保存
    report_file = error_dir / "failure_report.md"
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f"\n失敗レポートを保存: {report_file}", file=sys.stderr)
    return report_file

def process_all_files(folder_path, model_name="gemma3:12b", prefix_filter=None, debug_mode=False):
    """フォルダ内の全PDF/画像ファイルを処理"""
    folder = Path(folder_path)
    
    # PDFと画像ファイルを取得
    pdf_files = sorted([f for f in folder.glob('*.pdf') if not f.name.startswith('._')])
    image_files = sorted([
        f for f in folder.glob('*.*') 
        if f.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp'}
        and not f.name.startswith('._')
    ])
    
    all_files = sorted(set(pdf_files + image_files))
    
    # プレフィックスフィルタを適用（prefix_filterがNoneの場合は全ファイル）
    if prefix_filter:
        if isinstance(prefix_filter, list):
            # リストの場合は、いずれかのプレフィックスに一致するファイルを抽出
            all_files = [f for f in all_files if any(f.name.startswith(p) for p in prefix_filter)]
        else:
            # 文字列の場合は、そのプレフィックスに一致するファイルを抽出
            all_files = [f for f in all_files if f.name.startswith(prefix_filter)]
    
    if not all_files:
        print(f"警告: 処理対象のファイルが見つかりません: {folder_path}", file=sys.stderr)
        return []
    
    results = []
    processed = 0
    failed = 0

    # 提案2: デバッグモードの場合、エラーディレクトリを作成
    error_dir = None
    if debug_mode:
        error_dir = folder.parent / "errors"
        error_dir.mkdir(exist_ok=True)
        print(f"デバッグモード: エラー情報を {error_dir} に保存します", file=sys.stderr)

    print(f"処理対象ファイル数: {len(all_files)}件", file=sys.stderr)
    
    for file_path in all_files:
        print(f"\n処理中 ({processed + failed + 1}/{len(all_files)}): {file_path.name}", file=sys.stderr)
        
        try:
            # google_vision_ollama_prompt_only.pyを呼び出し
            result_data, subprocess_result = call_google_vision_ollama(file_path, model_name=model_name)  # 提案2: subprocess結果も取得
            
            if result_data:
                # 結果を整形
                company_name = result_data.get('company_name', '')
                total_amount = result_data.get('total_amount', 0)
                total_excluding_children = result_data.get('total_excluding_children', 0)
                date_time_str = result_data.get('date_time', '')
                
                # 日時をパース
                date_time = None
                date_str = None
                time_str = None
                
                if date_time_str:
                    try:
                        # "YYYY-MM-DD HH:MM:SS"形式をパース
                        date_time = datetime.strptime(date_time_str, '%Y-%m-%d %H:%M:%S')
                        date_str = date_time.strftime('%Y-%m-%d')
                        time_str = date_time.strftime('%H:%M:%S')
                    except ValueError:
                        try:
                            # "YYYY-MM-DD"形式をパース
                            date_time = datetime.strptime(date_time_str, '%Y-%m-%d')
                            date_str = date_time.strftime('%Y-%m-%d')
                            time_str = None
                        except ValueError:
                            pass
                
                # ファイル名から日付を取得（フォールバック）
                if not date_str:
                    import re
                    filename_match = re.search(r'(\d{8})', file_path.name)
                    if filename_match:
                        date_str = filename_match.group(1)
                        try:
                            date_time = datetime.strptime(date_str, '%Y%m%d')
                            date_str = date_time.strftime('%Y-%m-%d')
                        except ValueError:
                            pass
                
                if company_name and total_excluding_children > 0:
                    results.append({
                        'filename': file_path.name,
                        'company_name': company_name,
                        'amount': total_excluding_children,  # お子様向け商品除外後の金額
                        'total_amount': total_amount,  # 元の合計金額
                        'date_time': date_time.strftime('%Y-%m-%d %H:%M:%S') if date_time else None,
                        'date': date_str,
                        'time': time_str,
                    })
                    print(f"  ✓ 抽出成功: {company_name}, {total_excluding_children:,}円（合計: {total_amount:,}円）", file=sys.stderr)
                    processed += 1
                else:
                    print(f"  ✗ 抽出失敗: 事業者名={company_name}, 金額={total_excluding_children}", file=sys.stderr)

                    # 提案2: デバッグ情報を保存
                    if debug_mode and error_dir:
                        reason = 'AMOUNT_ZERO' if total_excluding_children == 0 else 'COMPANY_EMPTY'
                        save_debug_info(
                            file_path, error_dir,
                            reason=reason,
                            result=result_data,
                            ocr_output=subprocess_result.stdout if subprocess_result else None
                        )

                    # 最低限の情報を保存
                    results.append({
                        'filename': file_path.name,
                        'company_name': company_name or '抽出失敗',
                        'amount': total_excluding_children,
                        'total_amount': total_amount,
                        'date_time': date_time.strftime('%Y-%m-%d %H:%M:%S') if date_time else None,
                        'date': date_str,
                        'time': time_str,
                    })
                    failed += 1
            else:
                print(f"  ✗ 処理失敗: {file_path.name}", file=sys.stderr)

                # 提案2: デバッグ情報を保存
                if debug_mode and error_dir:
                    save_debug_info(
                        file_path, error_dir,
                        reason='NO_RESULT',
                        ocr_output=subprocess_result.stdout if subprocess_result else None,
                        llm_output=subprocess_result.stderr if subprocess_result else None
                    )

                # ファイル名から最低限の情報を取得
                import re
                filename_match = re.search(r'(\d{8})', file_path.name)
                date_time = None
                date_str = None
                if filename_match:
                    date_str = filename_match.group(1)
                    try:
                        date_time = datetime.strptime(date_str, '%Y%m%d')
                        date_str = date_time.strftime('%Y-%m-%d')
                    except ValueError:
                        pass

                # ファイル名から事業者名を推測
                company_name = file_path.stem.split('_', 1)[-1] if '_' in file_path.stem else file_path.stem
                company_name = company_name.replace('_', ' ')

                results.append({
                    'filename': file_path.name,
                    'company_name': company_name,
                    'amount': 0,
                    'total_amount': 0,
                    'date_time': date_time.strftime('%Y-%m-%d %H:%M:%S') if date_time else None,
                    'date': date_str,
                    'time': None,
                })
                failed += 1
                
        except Exception as e:
            print(f"  ✗ エラー: {file_path.name} - {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)

            # 提案2: デバッグ情報を保存
            if debug_mode and error_dir:
                save_debug_info(
                    file_path, error_dir,
                    reason='EXCEPTION',
                    exception=str(e) + '\n' + traceback.format_exc()
                )

            results.append({
                'filename': file_path.name,
                'company_name': f'エラー: {str(e)[:50]}',
                'amount': 0,
                'total_amount': 0,
                'date_time': None,
                'date': None,
                'time': None,
            })
            failed += 1

    # 提案2: デバッグモードの場合、失敗レポートを生成
    if debug_mode and error_dir and failed > 0:
        generate_failure_report(results, error_dir)

    return results

def generate_mermaid_table(results):
    """Mermaid表を生成"""
    # 日付でソート
    results_sorted = sorted(results, key=lambda x: x['date'] or '0000-00-00')
    
    mermaid = "```mermaid\n"
    mermaid += "%%{init: {'theme':'base', 'themeVariables': {'primaryColor':'#ff6b6b','primaryTextColor':'#fff','primaryBorderColor':'#7C0000','lineColor':'#F8B229','secondaryColor':'#006100','tertiaryColor':'#fff'}}}%%\n"
    mermaid += "erDiagram\n"
    mermaid += "    RECEIPTS {\n"
    mermaid += "        string 発生日時\n"
    mermaid += "        string 事業者名\n"
    mermaid += "        int 金額_円\n"
    mermaid += "        string ファイル名\n"
    mermaid += "    }\n\n"
    
    # データ行（金額が0より大きいもののみ、または全て表示）
    for result in results_sorted:
        date_str = result['date'] or '不明'
        time_str = f" {result['time']}" if result['time'] else ''
        company = result['company_name'].replace('"', '\\"').replace("'", "\\'")
        amount = result['amount']
        filename = result['filename'].replace('"', '\\"').replace("'", "\\'")
        
        # 改行をエスケープ
        company = company.replace('\n', ' ')
        filename = filename.replace('\n', ' ')
        
        # Mermaidの表形式で出力
        mermaid += f'    RECEIPTS ||--o{{ "{date_str}{time_str}\\n{company}\\n{amount:,}円\\n{filename}" : contains\n'
    
    mermaid += "```\n"
    
    return mermaid

def generate_markdown_table(results):
    """Markdown表を生成（より見やすい形式）"""
    # 日付でソート
    results_sorted = sorted(results, key=lambda x: x['date'] or '0000-00-00')
    
    markdown = "# レシート一覧（お子様向け商品除外）\n\n"
    markdown += "| 発生日時 | 事業者名 | 金額（円） | ファイル名 |\n"
    markdown += "|----------|---------|----------|------------|\n"
    
    total_amount = 0
    
    for result in results_sorted:
        date_str = result['date'] or '不明'
        time_str = f" {result['time']}" if result['time'] else ''
        company = result['company_name']
        amount = result['amount']
        filename = result['filename']
        
        markdown += f"| {date_str}{time_str} | {company} | {amount:,} | {filename} |\n"
        total_amount += amount
    
    markdown += f"\n**合計金額: {total_amount:,}円**\n"
    
    return markdown

def main():
    # 提案2: argparseでコマンドライン引数を解析
    parser = argparse.ArgumentParser(
        description='レシート一括処理ツール（提案1,2,6実装版）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  python process_all_receipts.py 2025OCR gemma3:12b
  python process_all_receipts.py 2025OCR llama3.1:70b --debug
  python process_all_receipts.py 2025OCR gemma3:12b --prefix 202501,202502
        """
    )
    parser.add_argument('folder', type=str, nargs='?', default=None,
                       help='処理対象フォルダ（未指定時はコマンドラインで入力を促す）')
    parser.add_argument('model', type=str, nargs='?', default='gemma3:12b',
                       help='使用するOllamaモデル（デフォルト: gemma3:12b）')
    parser.add_argument('--prefix', type=str, help='カンマ区切りでファイル名プレフィックスを指定（例: 202501,202502）')
    parser.add_argument('--debug', action='store_true', help='デバッグモード（エラー情報を詳細に保存）')

    args = parser.parse_args()

    folder_str = args.folder
    if not folder_str or not folder_str.strip():
        folder_str = input('処理対象フォルダを指定してください: ').strip()
    if not folder_str:
        print('エラー: フォルダが指定されていません', file=sys.stderr)
        sys.exit(1)
    folder_path = Path(folder_str)
    model_name = args.model
    prefix_filters = None
    if args.prefix:
        prefix_filters = [p.strip() for p in args.prefix.split(',') if p.strip()]

    if not folder_path.exists():
        print(f"エラー: フォルダが見つかりません: {folder_path}", file=sys.stderr)
        sys.exit(1)

    print(f"指定フォルダ内のPDF/画像ファイルを処理中...", file=sys.stderr)
    print(f"使用モデル: {model_name}", file=sys.stderr)
    print(f"処理方法: google_vision_ollama_prompt_only.pyを逐次呼び出し", file=sys.stderr)
    print(f"改善実装: 提案1（プロンプト最適化+Few-shot）, 提案2（デバッグログ）, 提案6（構造化情報活用）", file=sys.stderr)
    if args.debug:
        print(f"デバッグモード: 有効", file=sys.stderr)
    if prefix_filters:
        print(f"処理対象: {', '.join(prefix_filters)}で始まるファイル", file=sys.stderr)
    else:
        print(f"処理対象: 全ファイル", file=sys.stderr)

    # 全ファイルを処理
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"全ファイルを処理中...", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    all_results = process_all_files(
        folder_path,
        model_name=model_name,
        prefix_filter=prefix_filters,
        debug_mode=args.debug
    )
    
    # 成功と失敗をカウント
    success_count = sum(1 for r in all_results if r['amount'] > 0)
    failed_count = len(all_results) - success_count
    
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"全処理完了: {success_count}件成功, {failed_count}件失敗", file=sys.stderr)
    print(f"合計: {len(all_results)}件", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    
    # Mermaid表を生成
    mermaid_table = generate_mermaid_table(all_results)
    
    # Markdown表も生成（より見やすい形式）
    markdown_table = generate_markdown_table(all_results)
    
    # 結果を出力
    output_file = folder_path.parent / "2025OCR_receipts_summary.md"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(markdown_table)
        f.write("\n\n---\n\n")
        f.write("## Mermaid表\n\n")
        f.write(mermaid_table)
    
    print(f"\n結果を保存しました: {output_file}", file=sys.stderr)
    print(f"\n{markdown_table}", file=sys.stdout)

if __name__ == '__main__':
    main()
