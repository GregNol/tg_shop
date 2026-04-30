import os
from dataclasses import dataclass
from typing import List, Dict
from dotenv import load_dotenv
import logging

logging.basicConfig(level=logging.INFO)
load_dotenv()

@dataclass
class BotConfig:
    bot_token: str
    admin_ids: List[int]
    support_contact: str

@dataclass
class VisualsConfig:
    img_url_main: str
    img_url_stars: str
    img_url_premium: str
    img_url_profile: str
    img_url_calculator: str

@dataclass
class LinksConfig:
    privacy_policy_url: str
    offer_url: str

@dataclass
class PaymentSettings:
    min_payment_amount: int
    payment_timeout_seconds: int

@dataclass
class LolzConfig:
    api_key: str
    user_id: str

@dataclass
class CryptoBotConfig:
    api_key: str

@dataclass
class XRocetConfig:
    api_key: str

@dataclass
class CrystalPayConfig:
    login: str
    secret: str

@dataclass
class YookassaConfig:
    shop_id: str
    secret_key: str

@dataclass
class TonConfig:
    api_ton: str
    wallet_seed: str
    ton_wallet_address: str

@dataclass
class FragmentConfig:
    cookies: Dict[str, str]
    hash: str
    public_key: str
    wallets: str
    address: str

@dataclass
class RollyPayConfig:
    api_key: str

@dataclass
class Config:
    bot: BotConfig
    visuals: VisualsConfig
    links: LinksConfig
    payments: PaymentSettings
    lolz: LolzConfig
    cryptobot: CryptoBotConfig
    xrocet: XRocetConfig
    crystalpay: CrystalPayConfig
    yookassa: YookassaConfig
    rollypay: RollyPayConfig
    ton: TonConfig
    fragment: FragmentConfig
    database_url: str

def load_config() -> Config:
    admin_ids_str = os.getenv("ADMIN_IDS", "")
    admin_ids_list = [int(id.strip()) for id in admin_ids_str.split(",") if id.strip()]
    
    mnemonic_str = os.getenv("MNEMONIC", "")
    wallet_seed_str = ' '.join([word.strip() for word in mnemonic_str.split(',') if word.strip()])

    fragment_cookies_dict = {
        'stel_ssid': os.getenv("STEL_SSID"),
        'stel_dt': os.getenv("STEL_DT"),
        'stel_ton_token': os.getenv("STEL_TON_TOKEN"),
        'stel_token': os.getenv("STEL_TOKEN"),
    }

    return Config(
        bot=BotConfig(
            bot_token=os.getenv("BOT_TOKEN"),
            admin_ids=admin_ids_list,
            support_contact=os.getenv("SUPPORT_CONTACT", "")
        ),
        visuals=VisualsConfig(
            img_url_main=os.getenv("IMG_URL_MAIN"),
            img_url_stars=os.getenv("IMG_URL_STARS"),
            img_url_premium=os.getenv("IMG_URL_PREMIUM"),
            img_url_profile=os.getenv("IMG_URL_PROFILE"),
            img_url_calculator=os.getenv("IMG_URL_CALCULATOR")
        ),
        links=LinksConfig(
            privacy_policy_url=os.getenv("PRIVACY_POLICY_URL", ""),
            offer_url=os.getenv("OFFER_URL", "")
        ),
        payments=PaymentSettings(
            min_payment_amount=int(os.getenv("MIN_PAYMENT_AMOUNT", 10)),
            payment_timeout_seconds=int(os.getenv("PAYMENT_TIMEOUT_SECONDS", 900))
        ),
        lolz=LolzConfig(
            api_key=os.getenv("LOLZ_API_KEY"),
            user_id=os.getenv("LOLZ_USER_ID")
        ),
        cryptobot=CryptoBotConfig(
            api_key=os.getenv("CRYPTOBOT_API_KEY")
        ),
        xrocet=XRocetConfig(
            api_key=os.getenv("XROCET_API_KEY")
        ),
        crystalpay=CrystalPayConfig(
            login=os.getenv("CRYSTALPAY_LOGIN"),
            secret=os.getenv("CRYSTALPAY_SECRET")
        ),
        yookassa=YookassaConfig(
            shop_id=os.getenv("YOOKASSA_SHOP_ID"),
            secret_key=os.getenv("YOOKASSA_SECRET_KEY")
        ),
        rollypay=RollyPayConfig(
            api_key=os.getenv("ROLLYPAY_API_KEY")
        ),
        ton=TonConfig(
            api_ton=os.getenv("API_TON"),
            wallet_seed=wallet_seed_str,
            ton_wallet_address=os.getenv("TON_WALLET_ADDRESS")
        ),
        fragment=FragmentConfig(
            cookies=fragment_cookies_dict,
            hash=os.getenv("FRAGMENT_HASH"),
            public_key=os.getenv("FRAGMENT_PUBLICKEY"),
            wallets=os.getenv("FRAGMENT_WALLETS"),
            address=os.getenv("FRAGMENT_ADDRES")
        ),
        database_url=os.getenv("DATABASE_URL", "postgresql://bot_user:bot_password@db:5432/tg_shop")
    )
