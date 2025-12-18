import os
from flask import Flask, jsonify, request
from discord_interactions import verify_key_decorator, InteractionType, InteractionResponseType

PUBLIC_KEY = os.getenv('DISCORD_PUBLIC_KEY')

app = Flask(__name__)

@app.route('/', methods=['POST'])
@verify_key_decorator(PUBLIC_KEY)
def interactions():
    # Get the interaction data
    interaction = request.json

    # Handle the PING from Discord to verify the URL
    if interaction.get('type') == InteractionType.PING:
        return jsonify({
            'type': InteractionResponseType.PONG
        })

    # Handle Slash Commands
    if interaction.get('type') == InteractionType.APPLICATION_COMMAND:
        command_name = interaction.get('data', {}).get('name')

        if command_name == 'greet':
            return jsonify({
                'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
                'data': {
                    'content': 'Hello! I am running on Google Cloud Run Functions. 🚀'
                }
            })

    return jsonify({'error': 'Unhandled request type'}), 400

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
