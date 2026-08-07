#!/usr/bin/env python3
"""Create a default account to fix wallet error"""
import requests
import json

# API endpoint
url = "http://localhost:8000/api/account/create"

# Account data
account_data = {
    "name": "Default Wallet",
    "account_type": "demo",
    "model": "gpt-4",
    "base_url": "https://api.openai.com/v1",
    "api_key": "temp-key-for-initialization",
    "initial_capital": 1000
}

headers = {
    "Content-Type": "application/json"
}

try:
    print("Creating default account...")
    response = requests.post(url, headers=headers, json=account_data)
    
    if response.status_code == 200:
        print("Account created successfully!")
        print(f"Response: {response.json()}")
    else:
        print(f"Failed to create account. Status code: {response.status_code}")
        print(f"Response: {response.text}")
        
except Exception as e:
    print(f"Error connecting to API: {e}")