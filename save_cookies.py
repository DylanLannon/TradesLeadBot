from playwright.async_api import async_playwright
import asyncio
import sqlite3
import logging
import json
import requests
from datetime import datetime
import time
from typing import List, Dict

# Configuration
CONFIG = {
    "telegram_token": "7773212671:AAF7sVtVWO1mc2aXsJxlAUNrlt2xusdczHU",
    "telegram_chat_id": "7407520493",
    "check_interval": 60,  # How often to check (in seconds)
    "headless": False,     # Show the browser
    "database_path": "leads.db",
    "wait_time": 120000,  # 120 seconds timeout
    "facebook_groups": [
        "https://www.facebook.com/groups/inglebybarwicknoticeboard",
    ],
    "keywords": {
        "plumber": [
            "plumber", "plumbing", "leak", "pipe", "boiler", "tap", "toilet", 
            "bathroom", "kitchen sink", "water leak", "heating", "hot water"
        ],
        "electrician": [
            "electrician", "electrical", "wiring", "socket", "consumer unit",
            "tripping", "fuse box", "power", "light", "switch", "electric",
            "plug socket", "RCD", "fuseboard", "sparky"
        ],
        "builder": [
            "builder", "building", "construction", "renovation", "extension",
            "walls", "brickwork", "plastering", "concrete", "foundation"
        ],
        "painter": [
            "painter", "painting", "decorator", "decorating", "wallpaper",
            "paint job", "walls", "ceiling"
        ],
        "joiner": [
            "joiner", "carpentry", "carpenter", "doors", "skirting",
            "wooden", "wood work", "fitted furniture", "kitchen fitting"
        ],
        "roofer": [
            "roofer", "roofing", "roof leak", "roof repair", "guttering",
            "tiles", "slate", "flat roof", "leaking roof"
        ]
    }
}

class Database:
    def __init__(self, db_path: str):
        print(f"📁 Initializing database at {db_path}")
        self.conn = sqlite3.connect(db_path)
        self.create_tables()

    def create_tables(self):
        print("📊 Setting up database tables...")
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY,
                source TEXT,
                content TEXT,
                url TEXT,
                timestamp DATETIME,
                job_type TEXT,
                processed BOOLEAN DEFAULT 0,
                UNIQUE(content, source)
            )
        ''')
        self.conn.commit()
        print("✅ Database ready")

    def save_lead(self, source: str, content: str, url: str, job_type: str):
        try:
            self.conn.execute('''
                INSERT INTO leads (source, content, url, timestamp, job_type)
                VALUES (?, ?, ?, ?, ?)
            ''', (source, content, url, datetime.now(), job_type))
            self.conn.commit()
            print(f"💾 Saved new {job_type} lead to database")
            return True
        except sqlite3.IntegrityError:
            print("📝 Duplicate post - already in database")
            return False
        except Exception as e:
            print(f"❌ Database error: {e}")
            return False

    def close(self):
        self.conn.close()

class LeadDetector:
    def __init__(self, keywords: Dict[str, List[str]]):
        self.keywords = keywords

    def analyze_post(self, content: str) -> List[str]:
        matches = []
        content_lower = content.lower()
        for job_type, keywords in self.keywords.items():
            if any(keyword.lower() in content_lower for keyword in keywords):
                matches.append(job_type)
        return matches

class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    async def send_message(self, message: str):
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        data = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        try:
            response = requests.post(url, data=data)
            response.raise_for_status()
            print("✅ Notification sent to Telegram")
        except Exception as e:
            print(f"❌ Failed to send notification: {e}")

class LeadBot:
    def __init__(self, config: dict):
        self.config = config
        self.db = Database(config["database_path"])
        self.detector = LeadDetector(config["keywords"])
        self.notifier = TelegramNotifier(
            config["telegram_token"],
            config["telegram_chat_id"]
        )
        self.setup_logging()

    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler("bot.log"),
                logging.StreamHandler()
            ]
        )

    async def scrape_facebook_group(self, page, group_url: str):
        try:
            print("\n🔍 Testing group access...")
            print(f"URL: {group_url}")
            
            # Navigate to group
            await page.goto(group_url)
            print("Waiting for page to load...")
            await asyncio.sleep(5)
            
            # More specific selector for main posts
            main_posts = await page.query_selector_all("div[role='article'][aria-label*='post']")
            print(f"\n📝 Found {len(main_posts)} main posts")
            
            for post in main_posts:
                try:
                    # Get the post content and author
                    content_element = await post.query_selector("div[data-ad-comet-preview='message']")
                    if not content_element:
                        continue  # Skip if no main content found
                    
                    post_text = await content_element.inner_text()
                    
                    # Skip if it's too short (likely a comment)
                    if len(post_text) < 10:
                        continue
                    
                    print(f"\n📄 Checking main post:")
                    print("-" * 30)
                    print(f"Preview: {post_text[:150]}...")
                    
                    # Check for keywords
                    job_types = self.detector.analyze_post(post_text)
                    if job_types:
                        print(f"✨ Found keywords for: {', '.join(job_types)}")
                        for job_type in job_types:
                            # Try to get the post URL
                            timestamp_element = await post.query_selector("a[href*='/groups/'][role='link']")
                            post_url = await timestamp_element.get_attribute("href") if timestamp_element else group_url
                            
                            # Save to database and send notification
                            if self.db.save_lead("facebook", post_text, post_url, job_type):
                                print(f"💾 Saved to database: {job_type}")
                                message = (
                                    f"🚨 New {job_type.title()} Lead 🚨\n\n"
                                    f"📝 Details:\n{post_text[:500]}...\n\n"
                                    f"🔗 Link: {post_url}\n\n"
                                    f"📍 Source: facebook"
                                )
                                await self.notifier.send_message(message)
                                print(f"✅ Sent alert to Telegram for {job_type}")
                            else:
                                print("⏩ Duplicate post - skipping")
                    else:
                        print("❌ No matching keywords found")
                    
                except Exception as e:
                    print(f"Error processing post: {e}")
                    continue
                
        except Exception as e:
            print(f"❌ Error accessing group: {e}")

    async def run(self):
        print("\n🤖 Bot Starting Up...")
        print("=" * 50)
        
        while True:
            try:
                print("\n📱 Starting new check cycle...")
                async with async_playwright() as p:
                    print("🌐 Launching browser...")
                    browser = await p.chromium.launch(headless=self.config["headless"])
                    
                    print("🔧 Setting up browser context...")
                    context = await browser.new_context(
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        viewport={'width': 1920, 'height': 1080}
                    )

                    try:
                        print("🍪 Loading Facebook cookies...")
                        with open('fb_cookies.json', 'r') as f:
                            cookies = json.load(f)
                            await context.add_cookies(cookies)
                        print("✅ Cookies loaded successfully")
                    except FileNotFoundError:
                        print("❌ No cookies found! Please run save_cookies.py first")
                        return
                    except Exception as e:
                        print(f"❌ Error loading cookies: {e}")
                        return

                    page = await context.new_page()

                    print("\n🔐 Navigating to Facebook...")
                    try:
                        await page.goto("https://www.facebook.com", timeout=self.config["wait_time"])
                        print("Waiting for Facebook to load...")
                        await asyncio.sleep(5)
                        
                        if "login" in page.url:
                            print("❌ Not logged in! Please run save_cookies.py again")
                            return
                        
                        print("✅ Successfully logged into Facebook")
                        await asyncio.sleep(3)

                        # Go directly to group
                        print("\n🔍 Accessing group...")
                        await self.scrape_facebook_group(page, self.config["facebook_groups"][0])

                        print("\n✅ Closing browser...")
                        await browser.close()

                    except Exception as e:
                        print(f"❌ Error during Facebook navigation: {e}")
                        if not page.is_closed():
                            await page.close()
                        await browser.close()

                print(f"\n✅ Completed checking at {datetime.now().strftime('%H:%M:%S')}")
                print(f"⏰ Waiting {self.config['check_interval']} seconds before next check...")
                print("=" * 50)
                await asyncio.sleep(self.config["check_interval"])

            except Exception as e:
                print(f"\n❌ Error: {e}")
                print("Retrying in 30 seconds...")
                await asyncio.sleep(30)

    def cleanup(self):
        self.db.close()

async def main():
    bot = LeadBot(CONFIG)
    try:
        await bot.run()
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
    finally:
        bot.cleanup()

if __name__ == "__main__":
    asyncio.run(main())