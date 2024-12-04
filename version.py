"""
バージョン情報の管理モジュール
Semantic Versioning (https://semver.org/) に従ってバージョンを管理
"""

# メジャーバージョン：後方互換性のない変更
MAJOR = 1
# マイナーバージョン：後方互換性のある機能追加
MINOR = 0
# パッチバージョン：バグ修正
PATCH = 0

# バージョン文字列
VERSION = f"{MAJOR}.{MINOR}.{PATCH}"

def get_version():
    """現在のバージョン文字列を取得"""
    return VERSION

def get_version_info():
    """バージョン情報をタプルで取得"""
    return (MAJOR, MINOR, PATCH)
