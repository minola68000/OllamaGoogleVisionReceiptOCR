#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Google Cloud Vision APIのOCR結果を、詳細なプロンプトでOllamaに処理させる
テキストを直接処理せず、プロンプトエンジニアリングで精度を向上
"""

import sys
import json
import requests
from pathlib import Path
from google.cloud import vision
import os
import re
import tempfile
import shutil
from datetime import datetime

# PDF変換ライブラリのインポート
try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    try:
        from pdf2image import convert_from_path
        PDF2IMAGE_AVAILABLE = True
    except ImportError:
        PDF2IMAGE_AVAILABLE = False

# 画像処理ライブラリのインポート
try:
    from PIL import Image, ImageEnhance, ImageFilter
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

def enhance_image_for_ocr(image_path):
    """OCR精度向上のための画像前処理"""
    if not PIL_AVAILABLE:
        return image_path
    
    try:
        from PIL import Image as _PILImageModule
        try:
            _PILImageModule.MAX_IMAGE_PIXELS = max(_PILImageModule.MAX_IMAGE_PIXELS or 0, 300_000_000)
        except Exception:
            pass
        img = Image.open(image_path)
        
        # グレースケールに変換（カラー画像の場合）
        if img.mode != 'L':
            img = img.convert('L')
        
        # コントラストを強化（1.5倍）
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.5)
        
        # シャープ化
        img = img.filter(ImageFilter.SHARPEN)
        
        # 明度を調整
        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(1.1)
        
        # 処理済み画像を保存
        enhanced_path = str(Path(image_path).parent / f"enhanced_{Path(image_path).name}")
        img.save(enhanced_path)
        return enhanced_path
    except Exception as e:
        print(f"  画像前処理エラー: {e}（元の画像を使用）")
        return image_path


def shrink_image_for_vision(image_path, max_pixels=30_000_000, max_side=8000):
    """Vision APIに投げる前に、巨大画像を縮小してBad image対策を行う"""
    if not PIL_AVAILABLE:
        return image_path
    
    try:
        # PillowのDecompressionBombWarningを回避しつつ開く
        from PIL import Image as _PILImageModule
        try:
            # 巨大PNGを開いて縮小するため一時的に緩める
            _PILImageModule.MAX_IMAGE_PIXELS = max(_PILImageModule.MAX_IMAGE_PIXELS or 0, 300_000_000)
        except Exception:
            pass

        img = Image.open(image_path)
        width, height = img.size
        pixels = width * height
        
        # 制限内ならそのまま
        if pixels <= max_pixels and max(width, height) <= max_side:
            return image_path
        
        # 縮小スケールを計算
        scale_by_pixels = (max_pixels / pixels) ** 0.5 if pixels > 0 else 1.0
        scale_by_side = max_side / max(width, height)
        scale = min(scale_by_pixels, scale_by_side, 1.0)
        
        new_width = max(1, int(width * scale))
        new_height = max(1, int(height * scale))
        
        if new_width == width and new_height == height:
            return image_path
        
        img = img.resize((new_width, new_height), Image.LANCZOS)
        img.save(image_path)  # 同じパスに上書き（すべて一時ファイルのため）
        print(f"  画像が大きすぎるため縮小: {width}x{height} → {new_width}x{new_height}")
        return image_path
    except Exception as e:
        print(f"  画像縮小エラー: {e}（元の画像サイズで続行）")
        return image_path

def pdf_to_png(pdf_path, output_dir=None, dpi=600):
    """PDFをPNG画像に変換（PyMuPDFまたはpdf2imageを使用）
    
    注意:
    - 通常は600DPIで高精細に変換
    - ただし、ページが巨大すぎる場合はDecompressionBomb対策として
      DPIを段階的に下げて再試行する
    """
    if output_dir is None:
        output_dir = tempfile.mkdtemp()
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"  PDFをPNGに変換中（解像度: {dpi} DPI）...")
    png_paths = []
    
    # PyMuPDFを使用（推奨）
    if PYMUPDF_AVAILABLE:
        # 巨大ページ対策として、DPIを段階的に下げて再試行
        dpi_candidates = [dpi, 400, 300, 200]
        last_error = None
        for current_dpi in dpi_candidates:
            try:
                print(f"  PyMuPDFで変換試行中（{current_dpi} DPI）...")
                doc = fitz.open(str(pdf_path))
                for page_num in range(len(doc)):
                    page = doc[page_num]
                    # 指定DPIでレンダリング
                    mat = fitz.Matrix(current_dpi/72, current_dpi/72)
                    pix = page.get_pixmap(matrix=mat)
                    png_path = Path(output_dir) / f"page_{page_num+1}.png"
                    pix.save(str(png_path))
                    png_paths.append(png_path)
                    print(f"    ページ {page_num+1} を {png_path.name} に保存（{current_dpi} DPI）")
                doc.close()
                return png_paths, output_dir
            except Exception as e:
                print(f"  PyMuPDF変換エラー（{current_dpi} DPI）: {e}")
                png_paths = []  # 失敗した場合はリストをクリア
                last_error = e
                # 次の低DPIで再試行
        # すべてのDPIで失敗した場合のみフォールバック
        if PDF2IMAGE_AVAILABLE:
            print(f"  PyMuPDFでの変換に連続して失敗したため、pdf2imageにフォールバック...")
        else:
            # PyMuPDFのみで失敗した場合は最後のエラーを投げる
            raise last_error if last_error else RuntimeError("PyMuPDF変換に失敗しました")
    
    # pdf2imageを使用（フォールバック）
    if PDF2IMAGE_AVAILABLE:
        try:
            images = convert_from_path(str(pdf_path), dpi=dpi)
            for i, image in enumerate(images):
                png_path = Path(output_dir) / f"page_{i+1}.png"
                image.save(png_path, 'PNG')
                png_paths.append(png_path)
                print(f"    ページ {i+1} を {png_path.name} に保存")
            return png_paths, output_dir
        except Exception as e:
            print(f"  pdf2image変換エラー: {e}")
            raise
    
    raise ImportError("PDF変換ライブラリが利用できません")

def is_pdf_file(file_path):
    """ファイルがPDFかどうかを判定"""
    path = Path(file_path)
    return path.suffix.lower() == '.pdf'

def is_image_file(file_path):
    """ファイルが画像かどうかを判定"""
    path = Path(file_path)
    image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp'}
    return path.suffix.lower() in image_extensions

def prepare_image_file(input_path):
    """PDFの場合は画像に変換、画像の場合はそのまま返す"""
    input_path = Path(input_path)
    temp_dir = None
    temp_image_paths = []  # 変換された画像ファイルのパスを追跡
    
    if is_pdf_file(input_path):
        print(f"  PDFファイルを検出: {input_path.name}")
        try:
            # 高解像度（600 DPI）で変換
            png_paths, temp_dir = pdf_to_png(input_path, dpi=600)
            # 最初のページのみを使用
            image_path = png_paths[0]
            temp_image_paths = [str(p) for p in png_paths]  # 全ての変換された画像を追跡
            print(f"  変換完了: {image_path.name}（{len(png_paths)}ページ中1ページ目を使用）")
            
            # 画像前処理を適用（OCR精度向上のため）
            print(f"  画像前処理を適用中（コントラスト調整、シャープ化）...")
            enhanced_path = enhance_image_for_ocr(str(image_path))
            if enhanced_path != str(image_path):
                temp_image_paths.append(enhanced_path)
                image_path = enhanced_path
                print(f"  前処理完了: {Path(enhanced_path).name}")
            
            return str(image_path), temp_dir, temp_image_paths
        except Exception as e:
            print(f"  PDF変換エラー: {e}")
            raise
    elif is_image_file(input_path):
        print(f"  画像ファイルを検出: {input_path.name}")
        # 画像前処理を適用
        print(f"  画像前処理を適用中（コントラスト調整、シャープ化）...")
        enhanced_path = enhance_image_for_ocr(str(input_path))
        if enhanced_path != str(input_path):
            # 一時ファイルとして扱う
            temp_dir = tempfile.mkdtemp()
            return enhanced_path, temp_dir, [enhanced_path]
        return str(input_path), None, []  # 元の画像ファイルは削除しない
    else:
        raise ValueError(f"サポートされていないファイル形式です: {input_path.suffix}")

def extract_info_from_filename(file_path):
    """ファイル名から日付と事業者名を抽出"""
    filename = Path(file_path).stem  # 拡張子を除いたファイル名
    
    # 日付を抽出（YYYYMMDD形式）
    date_match = re.match(r'^(\d{8})', filename)
    date_str = None
    if date_match:
        date_str = date_match.group(1)
        try:
            date_obj = datetime.strptime(date_str, '%Y%m%d')
            date_str = date_obj.strftime('%Y-%m-%d')
        except ValueError:
            date_str = None
    
    # 事業者名を抽出（_の後）
    company_name = None
    parts = filename.split('_', 1)
    if len(parts) > 1:
        # _の後の部分から事業者名を抽出
        company_part = parts[1]
        
        # 日付パターン（YYYY-MM-DD）や時刻パターン（HH-MM-SS）を含む場合は除外
        # 事業者名は通常、日付や時刻の前に来る
        # 例: "skylark-receipt-2025-01-03-14-31-49" → "skylark-receipt"
        
        # 日付パターン（YYYY-MM-DD）を探す
        date_pattern = r'\d{4}-\d{2}-\d{2}'
        if re.search(date_pattern, company_part):
            # 日付の前までを事業者名とする
            match = re.search(date_pattern, company_part)
            company_name = company_part[:match.start()].rstrip('-').rstrip('_')
        else:
            # さらに_がある場合は最初の部分を事業者名とする
            if '_' in company_part:
                company_name = company_part.split('_')[0]
            else:
                company_name = company_part
        
        # 事業者名が空または短すぎる場合は、最初の有効な部分を使用
        if not company_name or len(company_name) < 2:
            # 最初の_までの部分を再取得
            if '_' in company_part:
                company_name = company_part.split('_')[0]
            else:
                # 最初の-までの部分を試す
                if '-' in company_part:
                    first_part = company_part.split('-')[0]
                    if len(first_part) >= 2:
                        company_name = first_part
    
    return {
        'date': date_str,
        'company_name': company_name,
        'filename': filename
    }

def calculate_distance(pos1, pos2):
    """2点間の距離を計算（簡易版）"""
    if not pos1 or not pos2:
        return float('inf')
    try:
        # 位置情報がある場合は中心座標を計算
        x1 = (pos1[0].x + pos1[2].x) / 2 if hasattr(pos1[0], 'x') else 0
        y1 = (pos1[0].y + pos1[2].y) / 2 if hasattr(pos1[0], 'y') else 0
        x2 = (pos2[0].x + pos2[2].x) / 2 if hasattr(pos2[0], 'x') else 0
        y2 = (pos2[0].y + pos2[2].y) / 2 if hasattr(pos2[0], 'y') else 0
        return ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    except:
        return float('inf')

def find_keyword_positions(response, keyword):
    """指定キーワードの位置情報を取得"""
    positions = []
    if not response.full_text_annotation:
        return positions

    for page in response.full_text_annotation.pages:
        for block in page.blocks:
            for paragraph in block.paragraphs:
                paragraph_text = ''
                for word in paragraph.words:
                    word_text = ''.join([symbol.text for symbol in word.symbols])
                    paragraph_text += word_text

                if keyword in paragraph_text:
                    # このparagraphの位置情報を保存
                    if paragraph.bounding_box and paragraph.bounding_box.vertices:
                        positions.append(paragraph.bounding_box.vertices)

    return positions

def extract_structured_ocr_info(response):
    """OCR結果から構造化された情報を抽出（金額の位置情報など）- 提案6: 強化版"""
    structured_info = {
        'full_text': response.full_text_annotation.text if response.full_text_annotation else '',
        'amount_candidates': [],
        'company_name_candidates': [],
        'keyword_positions': {}  # 提案6: キーワード位置情報を追加
    }

    if not response.full_text_annotation:
        return structured_info

    # 提案6: 重要キーワードの位置を取得
    important_keywords = ['合計', '総合計', '請求金額', '税込', 'お預かり', 'お預り', 'おつり', 'お釣り']
    for keyword in important_keywords:
        positions = find_keyword_positions(response, keyword)
        if positions:
            structured_info['keyword_positions'][keyword] = positions

    # 電話番号・ID・郵便番号など明らかに金額でない数字を判定
    def is_likely_phone_or_id(context, amount_num, raw_amount_str):
        if not context:
            return False
        c = context.replace('\n', ' ')
        # 文脈に電話・ID・郵便番号を示す語があれば除外
        non_amount_indicators = [
            'TEL', 'Tel', '電話', '〒', 'No.', 'No ', 'ID', 'id', '番号', '取引', 'コード', '通番',
            'Fax', 'FAX', '登録番号', '扱', '印', '係印'
        ]
        if any(kw in c for kw in non_amount_indicators):
            return True
        # 0X-XXXX-XXXX のような電話番号の一部（4桁・3桁の塊）を除外
        if re.search(r'0\d{1,4}[-\s]?\d{2,4}[-\s]?\d', c) or re.search(r'03[-\s]?\d{4}[-\s]?\d{4}', c):
            return True
        # 長い数字の羅列（10桁以上）はID等とみなす
        s = raw_amount_str.replace(',', '').replace('¥', '')
        if s.isdigit() and len(s) >= 10:
            return True
        # 7桁かつ文脈に数字のハイフン区切り（郵便番号）がありそう
        if amount_num >= 1000000 and amount_num <= 9999999 and re.search(r'\d{3}[-\s]?\d{4}', c):
            return True
        return False

    # 金額らしい数字を探す（位置情報付き）
    import re
    for page in response.full_text_annotation.pages:
        for block in page.blocks:
            for paragraph in block.paragraphs:
                paragraph_text = ''
                paragraph_position = paragraph.bounding_box.vertices if paragraph.bounding_box else None

                for word in paragraph.words:
                    word_text = ''.join([symbol.text for symbol in word.symbols])
                    paragraph_text += word_text

                # 金額パターンを探す（カンマを含む数字、¥記号付きも含む）
                # 「¥4650」「¥3,850」などのパターンも検出
                amount_patterns = re.findall(r'¥?[\d,]+', paragraph_text)
                for raw_amount_str in amount_patterns:
                    # ¥記号を除去
                    amount_str = raw_amount_str.replace('¥', '').replace(',', '').strip()
                    # 空文字や数字以外（例: 記号だけ）の場合はスキップ
                    if not amount_str or not re.search(r'\d', amount_str):
                        continue
                    # 数値に変換
                    try:
                        amount_num = int(amount_str)
                    except ValueError:
                        # 安全のため、ここで例外を外に投げない
                        continue

                    # 妥当な金額範囲（10円以上、10,000,000円以下）
                    if 10 <= amount_num <= 10000000:
                        # 前後の文脈を取得
                        # 文脈抽出は元の文字列（raw_amount_str）で位置を探す
                        idx = paragraph_text.find(raw_amount_str)
                        context_start = max(0, idx - 30)
                        context_end = min(len(paragraph_text), idx + len(raw_amount_str) + 30)
                        context = paragraph_text[context_start:context_end]

                        # 電話番号・ID・郵便番号などは金額候補から外す
                        if is_likely_phone_or_id(context, amount_num, raw_amount_str):
                            continue

                        # 除外すべきキーワードをチェック
                        exclude_keywords = ['お預かり', 'お預り', '預かり', '預り', 'おつり', 'お釣り', '釣り', '支払い', 'お支払い', 'お返し', '返し']
                        is_excluded = any(keyword in context for keyword in exclude_keywords)

                        # 提案6: 「合計」との距離を計算
                        distance_to_total = float('inf')
                        if '合計' in structured_info['keyword_positions']:
                            distances = [calculate_distance(paragraph_position, pos)
                                       for pos in structured_info['keyword_positions']['合計']]
                            if distances:
                                distance_to_total = min(distances)

                        # 除外対象でない場合のみ追加（または、除外対象でも情報として残す）
                        structured_info['amount_candidates'].append({
                            'amount': amount_num,
                            'text': raw_amount_str,
                            'context': context,
                            'excluded': is_excluded,  # 除外フラグを追加
                            'distance_to_total': distance_to_total,  # 提案6: 距離情報を追加
                            'position': paragraph_position  # 提案6: 位置情報を追加
                        })

                # 「¥465-0」「465-0」のようにハイフンで区切られ0が別になったパターンを補正（末尾0欠落）
                for m in re.finditer(r'¥?(\d+)-0\b', paragraph_text):
                    amount_num = int(m.group(1)) * 10
                    if 10 <= amount_num <= 10000000:
                        raw_str = m.group(0)
                        idx = m.start()
                        context_start = max(0, idx - 30)
                        context_end = min(len(paragraph_text), idx + len(raw_str) + 30)
                        context = paragraph_text[context_start:context_end]
                        if is_likely_phone_or_id(context, amount_num, raw_str + '0'):
                            continue
                        exclude_keywords = ['お預かり', 'お預り', '預かり', '預り', 'おつり', 'お釣り', '釣り', '支払い', 'お支払い', 'お返し', '返し']
                        is_excluded = any(kw in context for kw in exclude_keywords)
                        distance_to_total = float('inf')
                        if '合計' in structured_info['keyword_positions']:
                            distances = [calculate_distance(paragraph_position, pos)
                                       for pos in structured_info['keyword_positions']['合計']]
                            if distances:
                                distance_to_total = min(distances)
                        structured_info['amount_candidates'].append({
                            'amount': amount_num,
                            'text': raw_str + '0',
                            'context': context,
                            'excluded': is_excluded,
                            'distance_to_total': distance_to_total,
                            'position': paragraph_position
                        })

    # 消費税逆算値は候補に追加しない（丸めて無理に回答にしない）
    # 先頭1桁を¥の誤認として除去した候補は、逆算と整合するときだけ追加する（1〜9のいずれでも試す）
    full_text = structured_info.get('full_text', '')
    tax_inclusive_ref = None
    tax_match = re.search(r'消費[税稅]額等\s*\([^)]*\)\s*(\d+)', full_text)
    if not tax_match:
        # 閉じ括弧なし（改行で切れている）例: 消費税額等(995
        tax_match = re.search(r'消費[税稅]額等\s*\(\s*(\d+)', full_text)
    ocr_tax_amount = None
    if tax_match:
        try:
            tax_amount = int(tax_match.group(1))
            structured_info['ocr_tax_amount'] = tax_amount  # 後段で「合計と消費税の取り違え」判定に利用
            if 1 <= tax_amount <= 5000:
                tax_inclusive_ref = round(tax_amount / 0.1 + tax_amount)
                structured_info['tax_inclusive_ref'] = tax_inclusive_ref  # 消費税から妥当性チェックに利用
                ocr_tax_amount = tax_amount
        except (ValueError, IndexError):
            pass

    # 領収証で「金額」はあるが税込が読めていない場合の補正（消費税額の1桁誤読を汎用扱い）
    # 判断基準: OCRで読んだ消費税額の各桁を「字形で混同しやすい数字」に1桁だけ置換した候補を生成し、
    # その候補から税込を逆算して妥当な範囲なら金額候補に追加する
    _CONFUSABLE_DIGITS = {
        '0': ['6', '8'], '1': ['7'], '2': ['7'], '3': ['8', '5'], '4': ['9', '5'], '5': ['6', '3', '4'],
        '6': ['5', '0'], '7': ['1', '2'], '8': ['3', '9', '0'], '9': ['4', '8'],
    }
    def _tax_alternatives(tax_val):
        """消費税額の1桁を confusable に置換した候補を列挙（重複なし）"""
        s = str(tax_val)
        if not s.isdigit() or len(s) > 4:
            return set()
        out = set()
        for i, ch in enumerate(s):
            for sub in _CONFUSABLE_DIGITS.get(ch, []):
                if sub.isdigit():
                    alt = s[:i] + sub + s[i+1:]
                    out.add(int(alt))
        return out

    if tax_match and '金額' in full_text and ocr_tax_amount is not None:
        try:
            seen_ref = set()
            for tax_alt in _tax_alternatives(ocr_tax_amount):
                if tax_alt == ocr_tax_amount or not (1 <= tax_alt <= 5000):
                    continue
                ref_alt = round(tax_alt / 0.1 + tax_alt)
                ref_round = (ref_alt + 5) // 10 * 10
                if ref_round in seen_ref or not (1000 <= ref_round <= 10000000):
                    continue
                seen_ref.add(ref_round)
                structured_info['amount_candidates'].append({
                    'amount': ref_round,
                    'text': f'（消費税{tax_alt}円から逆算・消費税額等の1桁誤読補正）',
                    'context': '金額・消費税額等',
                    'excluded': False,
                    'distance_to_total': -3,
                    'position': 0,
                    'tax_alt': tax_alt,
                })
        except (ValueError, IndexError):
            pass

    for c in list(structured_info['amount_candidates']):
        amt = c.get('amount', 0)
        if amt < 10000:
            continue
        num_digits = len(str(amt))
        divisor = 10 ** (num_digits - 1)
        stripped = amt % divisor
        if stripped < 100 or stripped >= divisor:
            continue
        # 消費税逆算値と整合するときだけ追加（逆算値で丸めて無理に回答にしない）
        if tax_inclusive_ref is None or abs(stripped - tax_inclusive_ref) > 100:
            continue
        structured_info['amount_candidates'].append({
            'amount': stripped,
            'text': f'（{amt}の先頭1桁を¥誤認として除去）{stripped}',
            'context': c.get('context', ''),
            'excluded': False,
            'distance_to_total': -2,
            'position': c.get('position')
        })

    # 提案6: 距離でソート（近い順）
    structured_info['amount_candidates'].sort(key=lambda x: x.get('distance_to_total', float('inf')))

    return structured_info

def test_google_vision_ocr(image_path):
    """Google Cloud Vision APIでOCR処理"""
    try:
        # Vision APIに渡す前に、画像が巨大すぎる場合は縮小する
        shrink_image_for_vision(image_path)
        
        client = vision.ImageAnnotatorClient()
        
        with open(image_path, 'rb') as image_file:
            content = image_file.read()
        
        image = vision.Image(content=content)
        
        print("  OCR処理を実行中...")
        response = client.document_text_detection(image=image)
        
        if response.error.message:
            raise Exception(f"エラー: {response.error.message}")
        
        # テキストを抽出
        if response.full_text_annotation:
            text = response.full_text_annotation.text
            # 構造化情報も取得
            structured_info = extract_structured_ocr_info(response)
            return text, structured_info
        else:
            return None, None
            
    except Exception as e:
        print(f"エラーが発生しました: {e}")
        return None, None

def detect_receipt_type(filename):
    """ファイル名からレシート種類を判定 - 提案1"""
    filename_lower = filename.lower()

    if any(k in filename_lower for k in ['カフェ', 'coffee', 'cafe', 'クリエ', '珈琲']):
        return 'cafe'
    elif any(k in filename_lower for k in ['タクシー', '交通', '自動車']):
        return 'taxi'
    elif '固定資産税' in filename or '納付' in filename:
        return 'tax_payment'
    else:
        return 'general'

def get_fewshot_examples(receipt_type='general'):
    """レシート種類別のFew-shot例を返す - 提案1"""

    general_examples = """
【Few-shot学習例】

例1: カフェのレシート
入力テキスト: "上島珈琲店 本日のご利用 合計 ¥780 お預り ¥1,000 おつり ¥220"
出力JSON: {"company_name": "上島珈琲店", "total_amount": 780, "date_time": "2025-03-08 14:43:00"}
理由: 「合計」の金額780円を使用。「お預り」1,000円と「おつり」220円は除外。

例2: 誤認識の修正（先頭桁の誤り）
入力テキスト: "金額 ¥95650 消費税（10%） 514円"
出力JSON: {"total_amount": 5650}
理由: 消費税514円から逆算→税抜5,140円→税込5,654円≒5,650円。「95650」の先頭「9」は誤認識。

例3: タクシー運賃
入力テキスト: "日の丸交通株式会社 運賃 ¥3,400"
出力JSON: {"company_name": "日の丸交通株式会社", "total_amount": 3400}
理由: 「運賃」の金額を使用。
"""

    if receipt_type == 'cafe':
        return """
【Few-shot学習例 - カフェ】

例1: お預かりとの区別
入力: "カフェ・ド・クリエ 合計 ¥480 お預かり ¥500"
出力: {"company_name": "カフェ・ド・クリエ", "total_amount": 480}
理由: 「合計」を使用、「お預かり」は除外。

例2: ¥記号付き金額
入力: "ドトールコーヒー 金額 ¥780"
出力: {"company_name": "ドトールコーヒー", "total_amount": 780}
理由: ¥記号の後の数字をそのまま使用。
"""
    elif receipt_type == 'taxi':
        return """
【Few-shot学習例 - タクシー】

例1: タクシー運賃
入力: "飛鳥自動車株式会社 運賃 1,700円"
出力: {"company_name": "飛鳥自動車株式会社", "total_amount": 1700}
理由: 「運賃」の金額を使用。
"""
    else:
        return general_examples

def process_with_ollama_prompt_only(ocr_text, structured_info=None, filename_info=None, model_name="gemma3:12b", receipt_type='general'):
    """Ollama LLMでテキストを処理（詳細なプロンプトのみ、テキストを直接処理しない）"""
    try:
        # Ollama APIエンドポイント
        ollama_url = "http://localhost:11434/api/generate"
        
        # 提案1&6: 構造化情報をプロンプトに追加（簡潔化 + 位置情報活用）
        structured_context = ""
        if structured_info and structured_info.get('amount_candidates'):
            structured_context = "\n【OCRで検出された金額候補（「合計」に近い順）】\n"
            # 除外されていない候補を優先的に表示（提案6: 距離順にソート済み）
            non_excluded = [c for c in structured_info['amount_candidates'] if not c.get('excluded', False)]
            excluded = [c for c in structured_info['amount_candidates'] if c.get('excluded', False)]

            # 除外されていない候補を先に表示（最大5個に制限）
            for i, candidate in enumerate(non_excluded[:5], 1):
                dist = candidate.get('distance_to_total', float('inf'))
                dist_str = "（合計に近い）" if dist != float('inf') and dist < 1000 else ""
                structured_context += f"{i}. {candidate['text']}円{dist_str} - 文脈: {candidate['context'][:30]}...\n"

            # 除外された候補も参考として表示（最大3個）
            if excluded:
                structured_context += "\n【除外すべき金額（お預かり・おつり）】\n"
                for i, candidate in enumerate(excluded[:3], 1):
                    structured_context += f"× {candidate['text']}円 - {candidate['context'][:30]}...\n"

            structured_context += "\n【重要】除外リスト以外から、最も適切な合計金額を選択してください。\n"
        
        # ファイル名情報をプロンプトに追加
        filename_context = ""
        if filename_info:
            filename_context = "\n【ファイル名から抽出された情報】\n"
            if filename_info.get('date'):
                filename_context += f"- 取引日（ファイル名から）: {filename_info['date']}\n"
                filename_context += "  この日付を発生日時として使用してください。時刻が不明な場合は 00:00:00 としてください。\n"
            if filename_info.get('company_name'):
                filename_context += f"- 事業者名候補（ファイル名から）: {filename_info['company_name']}\n"
                filename_context += "  この情報を事業者名の判断に利用してください。\n"
                filename_context += "  レシートテキストから事業者名が抽出できない場合、または「不明」になる場合は、このファイル名の事業者名を使用してください。\n"
        
        # 提案1: 簡潔化されたプロンプト（200行程度、Few-shot例を含む）
        fewshot_examples = get_fewshot_examples(receipt_type)

        prompt = f"""あなたはレシート情報を正確に抽出する専門家です。

{fewshot_examples}
{structured_context}{filename_context}

【抽出する情報】
1. 事業者名（会社名・店舗名）
2. 合計金額（請求金額）
3. お子様向け商品（該当する場合のみ）
4. お子様向け商品除外後の金額
5. 発生日時

【重要なルール（3つのみ厳守）】

1. 金額は「合計」「総合計」「請求金額」の金額のみ使用
   - 絶対に除外：「お預かり」「お預り」「おつり」「お釣り」「支払い金額」
   - ¥記号付き金額はそのまま使用（例：¥4650 → 4650円）
   - 「金額」欄が「¥465-0」「465-0」のように数字-0とある場合は末尾の0を付けた金額を使用（例：4650円）。465円ではなく4650円。
   - 消費税額がある場合、逆算して妥当性確認（税率10%）
     例：消費税514円 → 税抜5,140円 → 税込5,654円 ≈ 5,650円

2. 金額の誤認識を補正
   - 消費税逆算値と大きな乖離があり、先頭1桁を削る（¥の誤認と仮定する）場合に整合性がとれるときだけ、除去後の金額を採用する
   - 先頭の数字は1〜9のいずれでも誤認の可能性がある（9に限らない）。例：92100→2,100、73650→3,650、55650→5,650
   - 例：金額欄92100・消費税191円→逆算約2,101。先頭1桁除去で2,100が逆算と整合するので2,100円を採用（2,101に丸めて無理にしない）
   - 「除去した金額」が候補にある場合は、逆算値に丸めた数値よりその金額を優先する
   - 例：消費税332円、金額欄「73650」→ 正しくは「3650」円（先頭の7は誤認識）

3. 事業者名はOCR誤認識を修正
   - ファイル名情報があれば優先的に参照
   - 住所・電話番号・郵便番号は除外
   - 「不明」と出力せず、テキストから可能な限り抽出

【お子様向け商品の処理】
- キーワード：「おこさま」「お子さま」「お子様」「Jr」「ジュニア」「キッズ」「子供」「こども」に該当する商品のみ
- 以下はお子様向けに含めない: テイクアウト行・「Plz」・「内2」「内税」などの軽減税率・品数表示。該当がなければ child_items は []、total_excluding_children は total_amount と同じ
- 除外後の金額 = 合計金額 - お子様向け商品の合計

【発生日時】
- ファイル名の日付を最優先
- テキストから「ご利用日」「取引時刻」を抽出
- 時刻不明の場合は 00:00:00

【レシートテキスト】
{ocr_text}

【出力形式】
JSON形式のみ出力。説明文不要。

{{
  "company_name": "事業者名",
  "total_amount": 合計金額（数値のみ）,
  "child_items": [{{"item": "商品名", "amount": 金額}}],
  "total_excluding_children": お子様向け商品除外後の金額（数値のみ）,
  "date_time": "YYYY-MM-DD HH:MM:SS"
}}"""

        print(f"\nOllama ({model_name})で処理中...")
        print("（詳細なプロンプトで指示を実行、テキストを直接処理しません）")
        
        # Ollama APIを呼び出し
        response = requests.post(
            ollama_url,
            json={
                "model": model_name,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,  # 低い温度で一貫性のある結果を取得
                    "num_predict": 2000,  # 十分な長さの応答を許可
                }
            },
            timeout=120  # タイムアウトを120秒に設定
        )
        
        if response.status_code == 200:
            result = response.json()
            return result.get("response", "")
        else:
            print(f"Ollama APIエラー: {response.status_code} - {response.text}")
            return None
            
    except requests.exceptions.ConnectionError:
        print("エラー: Ollamaサーバーに接続できません。Ollamaが起動しているか確認してください。")
        return None
    except Exception as e:
        print(f"エラーが発生しました: {e}")
        import traceback
        traceback.print_exc()
        return None

def extract_json_from_response(response_text):
    """LLMの応答からJSONを抽出"""
    # ```json または ``` で囲まれたJSONを探す
    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
    if json_match:
        return json_match.group(1)
    
    # 直接JSONオブジェクトを探す
    json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
    if json_match:
        return json_match.group(0)
    
    return response_text

def validate_result(result, structured_info=None):
    """結果の妥当性を検証"""
    errors = []
    warnings = []
    
    if not result.get('company_name'):
        errors.append("事業者名が抽出されていません")
    else:
        # 事業者名の妥当性チェック
        company_name = result.get('company_name', '')
        # 電話番号や郵便番号が含まれていないか
        if re.search(r'\d{2,4}-\d{2,4}-\d{4}', company_name) or re.search(r'〒\d{3}-\d{4}', company_name):
            warnings.append("事業者名に電話番号や郵便番号が含まれている可能性があります")
    
    total_amount = result.get('total_amount', 0)
    if not total_amount or total_amount <= 0:
        errors.append("合計金額が正しく抽出されていません")
    else:
        # 金額の妥当性チェック
        if total_amount < 50:
            warnings.append(f"金額が異常に小さいです（{total_amount}円）。OCRが部分的にしか認識していない可能性があります。")
        elif total_amount > 10000000:
            warnings.append(f"金額が異常に大きいです（{total_amount:,}円）。誤認識の可能性があります。")
        
        # 構造化情報と比較
        if structured_info and structured_info.get('amount_candidates'):
            candidate_amounts = [c['amount'] for c in structured_info['amount_candidates']]
            if total_amount not in candidate_amounts:
                # 最も近い候補を探す
                closest = min(candidate_amounts, key=lambda x: abs(x - total_amount))
                if abs(closest - total_amount) < total_amount * 0.1:  # 10%以内の差
                    warnings.append(f"抽出された金額（{total_amount:,}円）がOCR候補（{closest:,}円）と異なります。")
    
    child_items = result.get('child_items', [])
    child_total = sum(item.get('amount', 0) for item in child_items)
    expected_excluding = result.get('total_amount', 0) - child_total
    
    if result.get('total_excluding_children') != expected_excluding:
        errors.append(f"お子様向け商品除外後の金額が正しく計算されていません（期待値: {expected_excluding}円、実際: {result.get('total_excluding_children')}円）")
    
    if not result.get('date_time'):
        errors.append("発生日時が抽出されていません")
    
    return errors, warnings

def main():
    if len(sys.argv) < 2:
        print("使用方法: python3 google_vision_ollama_prompt_only.py <ファイルパス> [モデル名]")
        print("例: python3 google_vision_ollama_prompt_only.py image.png gemma3:12b")
        print("例: python3 google_vision_ollama_prompt_only.py receipt.pdf gemma3:12b")
        sys.exit(1)
    
    input_path = sys.argv[1]
    model_name = sys.argv[2] if len(sys.argv) > 2 else "gemma3:12b"
    
    if not os.path.exists(input_path):
        print(f"エラー: ファイルが見つかりません: {input_path}")
        sys.exit(1)
    
    print("=" * 60)
    print("Google Cloud Vision API + Ollama（詳細プロンプト）")
    print("=" * 60)
    print(f"入力ファイル: {Path(input_path).name}")
    print(f"モデル: {model_name}")
    print(f"\n注意: テキストを直接処理せず、プロンプトで詳細な指示を与えます")
    
    temp_dir = None
    temp_image_paths = []
    try:
        # Step 0: ファイル形式を判定し、必要に応じて変換
        print("\n[Step 0] ファイル形式の確認と変換...")
        image_path, temp_dir, temp_image_paths = prepare_image_file(input_path)
        
        # Step 0.5: ファイル名から情報を抽出
        print("\n[Step 0.5] ファイル名から情報を抽出中...")
        filename_info = extract_info_from_filename(input_path)
        if filename_info.get('date'):
            print(f"  取引日（ファイル名）: {filename_info['date']}")
        if filename_info.get('company_name'):
            print(f"  事業者名候補（ファイル名）: {filename_info['company_name']}")
        
        # Step 1: Google Cloud Vision APIでOCR
        print("\n[Step 1] Google Cloud Vision APIでOCR処理中...")
        ocr_text, structured_info = test_google_vision_ocr(image_path)
        
        if not ocr_text:
            print("OCR処理に失敗しました")
            sys.exit(1)
        
        print(f"  OCR完了: {len(ocr_text)}文字")
        if structured_info and structured_info.get('amount_candidates'):
            print(f"  金額候補: {len(structured_info['amount_candidates'])}件検出")
        print(f"  （テキストの内容は直接処理せず、プロンプトで指示します）")
        if os.environ.get("DEBUG_OCR"):
            print("\n[DEBUG] === OCR生テキスト ===")
            print(repr(ocr_text))
            print("\n[DEBUG] === 金額候補（text, amount, context） ===")
            for i, c in enumerate(structured_info.get('amount_candidates', [])[:25]):
                print(f"  {i+1}. text={c.get('text')!r} amount={c.get('amount')} context={c.get('context', '')[:60]!r}")
        
        # Step 2: Ollamaで処理（提案1: 簡潔化されたプロンプト + Few-shot）
        print("\n[Step 2] Ollamaで情報抽出中...")
        receipt_type = detect_receipt_type(Path(input_path).name)
        print(f"  検出されたレシート種類: {receipt_type}")
        llm_response = process_with_ollama_prompt_only(
            ocr_text,
            structured_info=structured_info,
            filename_info=filename_info,
            model_name=model_name,
            receipt_type=receipt_type
        )
        
        if not llm_response:
            print("LLM処理に失敗しました")
            sys.exit(1)
        
        print(f"\nLLM応答（最初の500文字）:")
        print(llm_response[:500])
        if len(llm_response) > 500:
            print("...")
        
        # Step 3: JSONを抽出して解析
        print("\n[Step 3] JSONを抽出中...")
        json_text = extract_json_from_response(llm_response)
        
        try:
            result = json.loads(json_text)
            original_llm_total = result.get('total_amount') or 0  # 消費税妥当性チェックで使用

            # 判断基準: (1) 返却額がOCR消費税額と一致 → 税込と消費税の取り違えとみなす
            #           (2) 返却額が1000円未満かつ消費税欄がある → 本合計が読めていない可能性
            # いずれも「消費税1桁誤読から逆算した税込候補」があれば、その最小額で上書きする
            ocr_tax = structured_info.get('ocr_tax_amount')
            total_from_llm = result.get('total_amount') or 0
            inverse_candidates = []
            if structured_info and structured_info.get('amount_candidates'):
                inverse_candidates = [c for c in structured_info['amount_candidates']
                                      if c.get('distance_to_total') == -3 and '逆算' in (c.get('text') or '')
                                      and isinstance(c.get('amount'), (int, float))
                                      and 1000 <= c.get('amount') <= 10000000]
            should_override = (
                (ocr_tax is not None and total_from_llm == ocr_tax) or  # 返却額が消費税欄と一致
                total_from_llm < 1000  # 返却額が1000円未満で本合計が読めていない可能性
            )
            apply_inverse = inverse_candidates and should_override
            if apply_inverse:
                near_ocr = [c for c in inverse_candidates if c.get('tax_alt') is not None
                            and ocr_tax is not None and abs(c.get('tax_alt') - ocr_tax) <= 100]
                if near_ocr:
                    override_amt = max(c['amount'] for c in near_ocr)
                else:
                    override_amt = min(c['amount'] for c in inverse_candidates)
                result['total_amount'] = override_amt
                result['total_excluding_children'] = override_amt
                reason = "返却額が消費税欄の値と一致" if (ocr_tax is not None and total_from_llm == ocr_tax) else "返却額が1000円未満で逆算候補あり"
                print(f"  （補正: {reason}のため、逆算候補の税込{override_amt:,}円に修正）")

            # 消費税から妥当性チェック: LLMの返却額がOCR消費税と整合しない場合、税込と整合する候補で上書き
            # （先頭1桁誤認の74,900→4,900など。元の返却額で判定するため original_llm_total を使用）
            tax_ref = structured_info.get('tax_inclusive_ref')
            ocr_tax = structured_info.get('ocr_tax_amount')
            if (tax_ref is not None and ocr_tax is not None and original_llm_total >= 10000 and
                    structured_info.get('amount_candidates')):
                implied_tax = round(original_llm_total / 1.1 * 0.1)
                if abs(implied_tax - ocr_tax) > max(50, ocr_tax * 0.15):  # 乖離が大きい
                    tax_consistent = [c for c in structured_info['amount_candidates']
                                      if isinstance(c.get('amount'), (int, float))
                                      and 1000 <= c['amount'] <= 10000000
                                      and abs(c['amount'] - tax_ref) <= 100]
                    if tax_consistent:
                        # 税込に最も近い候補を採用（先頭1桁除去候補を優先）
                        strip_first = [c for c in tax_consistent if c.get('distance_to_total') == -2]
                        candidates = strip_first if strip_first else tax_consistent
                        override_amt = min(candidates, key=lambda x: abs(x['amount'] - tax_ref))['amount']
                        result['total_amount'] = override_amt
                        result['total_excluding_children'] = override_amt
                        print(f"  （補正: 消費税から妥当性をチェックし、OCR消費税{ocr_tax}円と整合する税込{override_amt:,}円に修正）")

            # 返却額が5桁以上で「先頭1桁を¥誤認として除去」した候補がある場合、除去後の金額で上書き
            if structured_info and structured_info.get('amount_candidates') and (result.get('total_amount') or 0) >= 10000:
                total_from_llm = result.get('total_amount') or 0
                num_digits = len(str(total_from_llm))
                divisor = 10 ** (num_digits - 1)
                stripped = total_from_llm % divisor
                strip_candidates = [c for c in structured_info['amount_candidates']
                                    if c.get('distance_to_total') == -2 and '先頭1桁' in (c.get('text') or '')
                                    and c.get('amount') == stripped and 1000 <= stripped <= 10000000]
                if strip_candidates:
                    override_amt = stripped
                    result['total_amount'] = override_amt
                    result['total_excluding_children'] = override_amt
                    print(f"  （補正: 返却額{total_from_llm:,}円は先頭1桁誤認のため、除去後{override_amt:,}円に修正）")

            # ファイル名情報を最終結果に適用
            if filename_info:
                # 発生日時が不明またはファイル名の日付と大きく異なる場合は、ファイル名の日付を使用
                result_date = result.get('date_time', '')
                if filename_info.get('date'):
                    # ファイル名の日付を優先
                    if result_date:
                        # 時刻部分を保持
                        time_part = '00:00:00'
                        if ' ' in result_date:
                            time_part = result_date.split(' ')[1] if len(result_date.split(' ')) > 1 else '00:00:00'
                        result['date_time'] = f"{filename_info['date']} {time_part}"
                    else:
                        result['date_time'] = f"{filename_info['date']} 00:00:00"
                
                # 事業者名が不明または空の場合は、ファイル名の事業者名を使用
                company_name = result.get('company_name', '').strip()
                if not company_name or company_name.lower() in ['不明', 'n/a', 'none', '']:
                    if filename_info.get('company_name'):
                        result['company_name'] = filename_info['company_name']
                        print(f"  事業者名が不明のため、ファイル名から抽出: {filename_info['company_name']}")
            
            # total_excluding_children が null/None の場合はフォールバック
            if result.get('total_excluding_children') is None:
                total_amount_val = result.get('total_amount')
                if isinstance(total_amount_val, (int, float)):
                    # 合計金額が取れているなら、少なくともそこにフォールバック
                    result['total_excluding_children'] = int(total_amount_val)
                else:
                    # それも無い場合は 0 にしておく
                    result['total_excluding_children'] = 0
            
            # お子様向けの合計が合計金額と一致して total_excluding_children が 0 の場合は誤分類の可能性
            # （例: 「Plz」「内2」などをお子様向けと誤判定し、除外後0円になっている）
            child_items_list = result.get('child_items') or []
            child_total = sum(item.get('amount', 0) for item in child_items_list)
            total_amt = result.get('total_amount') or 0
            if (result.get('total_excluding_children') == 0 and total_amt > 0 and
                    child_total == total_amt and child_items_list):
                result['total_excluding_children'] = int(total_amt)
                print(f"  （補正: お子様向け合計=合計金額のため誤分類と判断し、除外後金額を合計金額に合わせました）")
            
            print("\n" + "=" * 60)
            print("抽出結果")
            print("=" * 60)
            print(f"事業者名: {result.get('company_name', 'N/A')}")
            print(f"合計金額: {result.get('total_amount', 0):,}円")
            
            child_items = result.get('child_items', [])
            if child_items:
                print(f"\nお子様向け商品: {len(child_items)}件")
                for item in child_items:
                    print(f"  - {item.get('item', 'N/A')}: {item.get('amount', 0):,}円")
                child_total = sum(item.get('amount', 0) for item in child_items)
                print(f"お子様向け商品合計: {child_total:,}円")
            else:
                print("\nお子様向け商品: なし")
            
            print(f"お子様向け商品除外後の金額: {result.get('total_excluding_children', 0):,}円")
            print(f"発生日時: {result.get('date_time', 'N/A')}")
            
            # 検証
            print("\n" + "=" * 60)
            print("検証結果")
            print("=" * 60)
            errors, warnings = validate_result(result, structured_info)
            if errors:
                print("❌ 以下の問題が見つかりました:")
                for error in errors:
                    print(f"  - {error}")
            if warnings:
                print("\n⚠️ 以下の警告:")
                for warning in warnings:
                    print(f"  - {warning}")
            if not errors and not warnings:
                print("✅ すべての項目が正しく抽出・計算されています")
            elif not errors:
                print("✅ 基本的な項目は抽出されていますが、警告を確認してください")
            
            print("\n" + "=" * 60)
            print("JSON形式:")
            print("=" * 60)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            
        except json.JSONDecodeError as e:
            print(f"JSON解析エラー: {e}")
            print(f"\n抽出されたテキスト:")
            print(json_text)
            sys.exit(1)
    finally:
        # 一時ファイルをクリーンアップ（PDFから変換された画像ファイルを削除）
        if temp_image_paths:
            print(f"\n[クリーンアップ] PDFから変換された一時画像ファイルを削除中...")
            for img_path in temp_image_paths:
                try:
                    if os.path.exists(img_path):
                        os.remove(img_path)
                        print(f"  削除: {Path(img_path).name}")
                except Exception as e:
                    print(f"  警告: {Path(img_path).name} の削除に失敗: {e}")
        
        if temp_dir:
            try:
                # ディレクトリが空でない場合は再試行
                if os.path.exists(temp_dir):
                    # ディレクトリ内のファイルを確認
                    remaining_files = list(Path(temp_dir).glob('*'))
                    if remaining_files:
                        print(f"  残っているファイルを削除中...")
                        for f in remaining_files:
                            try:
                                if f.is_file():
                                    f.unlink()
                            except Exception as e:
                                print(f"    警告: {f.name} の削除に失敗: {e}")
                    # ディレクトリを削除
                    shutil.rmtree(temp_dir)
                    print(f"  一時ディレクトリを削除しました: {Path(temp_dir).name}")
            except Exception as e:
                print(f"  警告: 一時ディレクトリの削除に失敗しました: {e}")

if __name__ == '__main__':
    main()
