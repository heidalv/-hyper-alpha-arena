#!/usr/bin/env python3
"""
Fix inconsistent imports in backend directory
Convert all imports to be consistent (absolute imports from backend package)
"""
import os
import re
from pathlib import Path

def fix_imports_in_file(file_path):
    """Fix imports in a single file"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content

    # Pattern 1: Replace from ..database with from backend.database
    content = re.sub(r'from \.\.database\.', 'from backend.database.', content)

    # Pattern 2: Replace from ..services with from backend.services
    content = re.sub(r'from \.\.services\.', 'from backend.services.', content)

    # Pattern 3: Replace from ..schemas with from backend.schemas (if used)
    content = re.sub(r'from \.\.schemas\.', 'from backend.schemas.', content)

    # Pattern 4: Replace from ..repositories with from backend.repositories
    content = re.sub(r'from \.\.repositories\.', 'from backend.repositories.', content)

    # Pattern 5: Replace from ..api with from backend.api
    content = re.sub(r'from \.\.api\.', 'from backend.api.', content)

    # Pattern 6: Replace from ..utils with from backend.utils
    content = re.sub(r'from \.\.utils\.', 'from backend.utils.', content)

    # Pattern 7: Replace from ..config with from backend.config
    content = re.sub(r'from \.\.config\.', 'from backend.config.', content)

    # Pattern 8: Replace from ..factors with from backend.factors
    content = re.sub(r'from \.\.factors\.', 'from backend.factors.', content)

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
    total_files = 0

    # Find all Python files in backend directory
    for py_file in backend_dir.rglob('*.py'):
        total_files += 1
        if fix_imports_in_file(py_file):
            fixed_count += 1
            print(f"Fixed: {py_file}")

    print(f"\nTotal files scanned: {total_files}")
    print(f"Files fixed: {fixed_count}")

if __name__ == '__main__':
    main()
