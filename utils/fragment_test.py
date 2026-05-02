#!/usr/bin/env python3

import asyncio
import logging
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import load_config
from services.fragment_sender import FragmentSender
from services.fragment_auth import FragmentAuth
from services.ton_api import get_ton_balance
from aiogram import Bot

async def test_fragment_comprehensive():
    logging.basicConfig(level=logging.INFO)
    
    config = load_config()
    bot = Bot(token=config.bot.bot_token)
    
    print("🔍 Comprehensive Fragment Test")
    print("=" * 50)
    
    fragment_auth = FragmentAuth(config)
    fragment_sender = FragmentSender(config, bot)
    
    print("1. Testing Fragment authentication...")
    auth_status = await fragment_auth.check_auth_status()
    print(f"   Auth status: {'✅ OK' if auth_status else '❌ FAILED'}")
    
    print("\n2. Testing Fragment wallet balance...")
    fragment_balance, fragment_error = await fragment_auth.get_wallet_balance()
    if fragment_error:
        print(f"   Fragment balance: ❌ {fragment_error}")
    else:
        print(f"   Fragment balance: ✅ {fragment_balance:.4f} TON")
    
    print("\n3. Testing TON wallet balance...")
    ton_balance, ton_error = await get_ton_balance(config.fragment.address)
    if ton_error:
        print(f"   TON balance: ❌ {ton_error}")
    else:
        print(f"   TON balance: ✅ {ton_balance:.4f} TON")
    
    print("\n4. Testing recipient search...")
    test_username = "telegram"  # Public username for testing
    
    try:
        import httpx
        async with httpx.AsyncClient(
            cookies=config.fragment.cookies,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://fragment.com",
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15",
            },
            timeout=10.0
        ) as client:
            url = f"https://fragment.com/api?hash={config.fragment.hash}"
            data = {"query": test_username, "method": "searchStarsRecipient"}
            response = await client.post(url, data=data)
            result = response.json()
            
            if result.get("found", {}).get("recipient"):
                print(f"   Recipient search: ✅ Found @{test_username}")
            else:
                print(f"   Recipient search: ❌ Not found")
                print(f"   API Response: {result}")
    except Exception as e:
        print(f"   Recipient search: ❌ Error: {e}")
    
    print("\n5. Configuration check...")
    required_fields = [
        ('fragment_hash', config.fragment.hash),
        ('fragment_address', config.fragment.address),
        ('fragment_wallets', config.fragment.wallets),
        ('fragment_public_key', config.fragment.public_key),
        ('wallet_seed', config.ton.wallet_seed),
        ('api_ton', config.ton.api_ton)
    ]
    
    for field_name, field_value in required_fields:
        status = "✅ OK" if field_value else "❌ MISSING"
        print(f"   {field_name}: {status}")
    
    required_cookies = [
        ('stel_ssid', config.fragment.cookies.get('stel_ssid')),
        ('stel_dt', config.fragment.cookies.get('stel_dt')),
        ('stel_ton_token', config.fragment.cookies.get('stel_ton_token')),
        ('stel_token', config.fragment.cookies.get('stel_token'))
    ]
    
    for cookie_name, cookie_value in required_cookies:
        status = "✅ OK" if cookie_value else "❌ MISSING"
        print(f"   {cookie_name}: {status}")
    
    print("\n" + "=" * 50)
    
    all_checks_passed = (
        auth_status and 
        not fragment_error and 
        not ton_error and
        all(field_value for _, field_value in required_fields) and
        all(cookie_value for _, cookie_value in required_cookies)
    )
    
    if all_checks_passed:
        print("🎉 All tests passed! Fragment integration is ready.")
        
        confirm = input("\nDo you want to test actual star sending? (y/N): ")
        if confirm.lower() == 'y':
            test_username = input("Enter username to send 1 star (without @): ")
            print(f"Sending 1 star to @{test_username}...")
            success = await fragment_sender.send_stars(test_username, 1)
            print(f"Result: {'✅ SUCCESS' if success else '❌ FAILED'}")
    else:
        print("❌ Some tests failed. Please check your configuration.")
    
    await bot.session.close()

if __name__ == "__main__":
    asyncio.run(test_fragment_comprehensive())