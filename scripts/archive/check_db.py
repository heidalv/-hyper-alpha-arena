#!/usr/bin/env python3
"""Check database accounts"""
import sqlite3

def check_accounts():
    conn = sqlite3.connect('d:/001Alpha/Hyper-Alpha-Arena/data/alpha_arena.db')
    cursor = conn.cursor()
    
    # Get all table names
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print('Tables in DB:', [table[0] for table in tables])
    
    # Check if accounts table exists and has data
    if 'accounts' in [table[0] for table in tables]:
        cursor.execute('SELECT COUNT(*) FROM accounts')
        count = cursor.fetchone()[0]
        print(f'Total accounts: {count}')
        
        if count > 0:
            cursor.execute('SELECT id, name, account_type, is_active FROM accounts LIMIT 10')
            accounts = cursor.fetchall()
            for acc in accounts:
                print(f'Account: ID={acc[0]}, Name={acc[1]}, Type={acc[2]}, Active={acc[3]}')
        else:
            print('No accounts found in database - need to create one')
            
            # Create a default account
            import datetime
            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
                'false', 'gpt-4', 'https://api.openai.com/v1', 'temp-key',
                1000.0, 1000.0, 0.0,
                'false', 'testnet',
                10, 5, 'false',
                'spot', 'false', 20,
                now, now, None
            ))
            conn.commit()
            print('Default account created successfully!')
    else:
        print('Accounts table does not exist')
    
    conn.close()

if __name__ == "__main__":
    check_accounts()