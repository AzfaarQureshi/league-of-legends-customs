import threading
import requests
import os
from flask import Flask, jsonify, request
from discord_interactions import (
    verify_key_decorator,
    InteractionType,
    InteractionResponseType,
)
import interactions.create_teams as create_teams

PUBLIC_KEY = os.getenv("DISCORD_PUBLIC_KEY")
app = Flask(__name__)


@app.route("/", methods=["POST"])
@verify_key_decorator(PUBLIC_KEY)
def interactions():
    interaction = request.json

    if interaction.get("type") == InteractionType.PING:
        return jsonify({"type": InteractionResponseType.PONG})

    if interaction.get("type") == InteractionType.APPLICATION_COMMAND:
        data = interaction.get("data", {})
        command_name = data.get("name")

        if command_name == "create_teams":
            # 1. Immediate ACK (Type 5: DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE)
            # This prevents the 3-second timeout error
            token = interaction.get("token")
            application_id = interaction.get("application_id")

            # 2. Spin up background thread for logic
            thread = threading.Thread(
                target=create_teams.run, args=(data, application_id, token)
            )
            thread.start()

            return jsonify({"type": 5})

    return jsonify({"error": "Unhandled request type"}), 400
