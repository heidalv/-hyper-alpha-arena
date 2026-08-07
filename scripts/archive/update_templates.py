#!/usr/bin/env python3
"""
临时脚本：强制更新提示词模板到最新的中文版本
"""
import sys
sys.path.insert(0, '/app/backend')

from database.connection import SessionLocal
from sqlalchemy import text

def main():
    db = SessionLocal()
    try:
        # Update system templates to latest Chinese version
        result = db.execute(text("""
            UPDATE prompt_templates 
            SET template_text = system_template_text 
            WHERE key IN ('default', 'pro', 'hyperliquid') 
            AND is_system = 'true'
        """))
        db.commit()
        print(f"✅ Updated {result.rowcount} templates to latest system version")
        
        # Verify update
        templates = db.execute(text("""
            SELECT key, 
                   CASE 
                       WHEN template_text LIKE '%交易环境%' THEN 'Chinese ✓'
                       WHEN template_text LIKE '%TRADING ENVIRONMENT%' THEN 'English ✗'
                       ELSE 'Unknown'
                   END as language
            FROM prompt_templates
            WHERE key IN ('default', 'pro', 'hyperliquid')
        """)).fetchall()
        
        print("\n提示词模板状态：")
        for key, lang in templates:
            print(f"  - {key}: {lang}")
            
    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    main()
