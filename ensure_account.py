#!/usr/bin/env python3
"""
Script to ensure at least one account exists in the database
to resolve "wallet must has at least one account" error
"""
import sqlite3
import datetime
import os

def ensure_default_account():
    db_path = 'd:/001Alpha/Hyper-Alpha-Arena/data/alpha_arena.db'
    
    if not os.path.exists(db_path):
        print(f"Database file does not exist at {db_path}")
        return False
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if accounts table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='accounts';")
        table_exists = cursor.fetchone()
        
        if not table_exists:
            print("Accounts table does not exist in the database")
            conn.close()
            return False
        
        # Check if any account exists
        cursor.execute('SELECT COUNT(*) FROM accounts')
        count = cursor.fetchone()[0]
        
        if count == 0:
            print("No accounts found, creating default account...")
            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Insert a default account
            cursor.execute('''
                INSERT INTO accounts (
                    user_id, version, name, account_type, is_active,
                    auto_trading_enabled, model, base_url, api_key,
                    initial_capital, current_cash, frozen_cash,
                    hyperliquid_enabled, hyperliquid_environment,
                    max_leverage, default_leverage, binance_enabled,
                    binance_market_type, binance_testnet, binance_max_leverage,
                    created_at, updated_at, llm_config_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                1, 'v1', 'Default Wallet', 'demo', 'true',
                'false', 'gpt-4', 'https://api.openai.com/v1', 'temp-key-for-initialization',
                1000.0, 1000.0, 0.0,
                'false', 'testnet',
                10, 5, 'false',
                'spot', 'false', 20,
                now, now, None
            ))
            conn.commit()
            print("Default account created successfully!")
        else:
            print(f"Found {count} existing accounts, no need to create default account")
            
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error accessing database: {e}")
        return False

if __name__ == "__main__":
    success = ensure_default_account()
    if success:
        print("Database operation completed successfully")
    else:
        print("Database operation failed")