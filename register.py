import json
import os
import requests
from dotenv import load_dotenv

load_dotenv()
APP_ID = os.getenv("APP_ID")
BOT_TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

# Run this script once locally to register the commands with Discord.
# Optional:
# URL = f"https://discord.com/api/v10/applications/{APP_ID}/guilds/{GUILD_ID}/commands"
# in order to get instant updates to my specific server.
URL = f"https://discord.com/api/v10/applications/{APP_ID}/commands"

commands_payload = [
    {
        "name": "create_teams",
        "description": "Upload a roster screenshot to generate balanced teams",
        "type": 1,
        "options": [
            {
                "name": "roster_screenshot",
                "description": "The screenshot containing player names/ranks",
                "type": 11,  # Type 11 is for ATTACHMENTS
                "required": True,
            }
        ],
    },
    {
        "name": "upload_game_results",
        "description": "Upload the post-game screen to update player scores",
        "type": 1,
        "options": [
            {
                "name": "result_screenshot",
                "description": "The screenshot of the final score screen",
                "type": 11,
                "required": True,
            }
        ],
    },
]

headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
r = requests.put(URL, headers=headers, json=commands_payload)
print(r.status_code, r.text)
