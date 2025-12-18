from discord_interactions import InteractionResponseType


def run(interaction_data):
    print("interaction_data: ", interaction_data)
    return jsonify(
        {
            "type": InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
            "data": {"content": "Howdy Summoner! This command is still a WIP"},
        }
    )
