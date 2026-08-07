#!/usr/bin/env python3
"""
Fix all imports to use absolute imports with backend. prefix
"""
import os
import re
from pathlib import Path

def fix_all_imports_in_file(file_path):
    """Fix all imports in a single file"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content

    # Fix imports without backend prefix but with package names
    # Pattern: from schemas. -> from backend.schemas.
    content = re.sub(r'^from schemas\.', 'from backend.schemas.', content, flags=re.MULTILINE)
    content = re.sub(r'^from repositories\.', 'from backend.repositories.', content, flags=re.MULTILINE)
    content = re.sub(r'^from services\.', 'from backend.services.', content, flags=re.MULTILINE)
    content = re.sub(r'^from database\.', 'from backend.database.', content, flags=re.MULTILINE)
    content = re.sub(r'^from api\.', 'from backend.api.', content, flags=re.MULTILINE)
    content = re.sub(r'^from utils\.', 'from backend.utils.', content, flags=re.MULTILINE)
    content = re.sub(r'^from config\.', 'from backend.config.', content, flags=re.MULTILINE)
    content = re.sub(r'^from factors\.', 'from backend.factors.', content, flags=re.MULTILINE)

    if content != original_content:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    return False

def main():
    backend_dir = Path('backend')

    if not backend_dir.exists():
        print(f"Error: {backend_dir} directory not found")
        return

    fixed_count = 0

    # Only process files in backend/api, backend/services, backend/repositories
    for pattern in ['api/*.py', 'services/*.py', 'repositories/*.py', 'schemas/*.py']:
        for py_file in backend_dir.glob(pattern):
            if fix_all_imports_in_file(py_file):
                fixed_count += 1
                print(f"Fixed: {py_file}")

    print(f"\nFiles fixed: {fixed_count}")

if __name__ == '__main__':
    main()
