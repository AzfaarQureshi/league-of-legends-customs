import os
import discord
from dotenv import load_dotenv
from google.cloud import vision

# 1. Setup Environment
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# 2. Setup Google Vision Client
vision_client = vision.ImageAnnotatorClient()

# 3. Setup Discord Bot
intents = discord.Intents.default()
intents.message_content = True # Required to read messages
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f'✅ Logged in as {client.user}')

@client.event
async def on_message(message):
    # Ignore messages from the bot itself
    if message.author == client.user:
        return

    # Check if message has an attachment (Screenshot)
    if message.attachments:
        attachment = message.attachments[0]
        
        # Simple check: is it an image?
        if any(attachment.filename.lower().endswith(ext) for ext in ['png', 'jpg', 'jpeg']):
            await message.channel.send(f"👀 analyzing screenshot: {attachment.filename}...")
            
            try:
                # A. Download the image into memory
                image_content = await attachment.read()
                
                # B. Send to Google Vision API
                image = vision.Image(content=image_content)
                response = vision_client.text_detection(image=image)
                texts = response.text_annotations

                if texts:
                    # The first annotation contains the entire block of text
                    full_text = texts[0].description
                    
                    # C. Reply with the text
                    # Discord has a 2000 char limit, so we slice it just in case
                    await message.channel.send(f"**I found this text:**\n```{full_text[:1900]}```")
                else:
                    await message.channel.send("❌ Google Vision couldn't find any text.")

            except Exception as e:
                await message.channel.send(f"💥 Error processing image: {str(e)}")

# 4. Run the Bot
client.run(TOKEN)
