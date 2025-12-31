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
import interactions.create_teams_v2 as create_teams_v2
import interactions.upload_game_results as upload_game_results

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
            # Immediate ACK (Type 5: DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE)
            token = interaction.get("token")
            application_id = interaction.get("application_id")

            # Spin up background thread for logic
            thread = threading.Thread(
                target=create_teams_v2.run, args=(data, application_id, token)
            )
            thread.start()

            return jsonify({"type": 5})

        elif command_name == "upload_game_results":
            # Immediate ACK
            token = interaction.get("token")
            application_id = interaction.get("application_id")

            # Spin up background thread for image analysis
            thread = threading.Thread(
                target=upload_game_results.run, args=(data, application_id, token)
            )
            thread.start()

            return jsonify({"type": 5})

    # Handle button/component interactions (Type 3: MESSAGE_COMPONENT)
    if interaction.get("type") == 3:  # MESSAGE_COMPONENT
        token = interaction.get("token")
        application_id = interaction.get("application_id")

        # Immediate ACK (Type 6: DEFERRED_UPDATE_MESSAGE)
        # This acknowledges the button click and allows us to edit the message
        thread = threading.Thread(
            target=upload_game_results.handle_confirmation,
            args=(interaction, application_id, token),
        )
        thread.start()

        return jsonify({"type": 6})

    return jsonify({"error": "Unhandled request type"}), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
