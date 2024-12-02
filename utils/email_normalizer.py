from typing import Optional, Tuple
import re

def normalize_email(email: str) -> Tuple[str, Optional[str], str]:
    """
    メールアドレスを正規化し、表示名とメールアドレス部分を分離します。
    
    Args:
        email: 正規化するメールアドレス（例: "Display Name <email@example.com>"）
        
    Returns:
        Tuple[str, Optional[str], str]: (正規化されたメールアドレス, 表示名, 元のメールアドレス)
    """
    # メールアドレスから表示名とアドレス部分を抽出
    display_name = None
    email_address = email.strip()
    
    # 表示名付きのメールアドレスをパース
    match = re.match(r'"?([^"<]+)"?\s*<?([^>]+)>?', email)
    if match:
        display_name = match.group(1).strip()
        email_address = match.group(2).strip()
    
    # メールアドレスを正規化（小文字化）
    normalized_email = email_address.lower()
    
    # 特殊文字を削除（例：スペース、タブ、改行）
    normalized_email = re.sub(r'\s+', '', normalized_email)
    
    return normalized_email, display_name, email_address

def extract_email_parts(email_str: str) -> dict:
    """
    メールアドレス文字列から各パーツを抽出します。
    
    Args:
        email_str: パースするメールアドレス文字列
        
    Returns:
        dict: {
            'normalized_email': 正規化されたメールアドレス,
            'display_name': 表示名（存在する場合）,
            'original_email': 元のメールアドレス
        }
    """
    normalized_email, display_name, original_email = normalize_email(email_str)
    
    return {
        'normalized_email': normalized_email,
        'display_name': display_name,
        'original_email': original_email
    }
