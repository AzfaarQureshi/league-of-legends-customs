import os

from enum import Enum

from discord_interactions import InteractionResponseType
from flask import jsonify


Role = Enum("TOP", "JG", "MID", "ADC", "SUP")


class Player:
    name: str
    primary: Role
    secondary: Role
    mmr: int

    def __init__(self, name, primary, secondary, mmr):
        self.name = name
        self.primary = primary
        self.secondary = secondary
        self.rank = mmr


def run(interaction_data):
    print("test! test! test!")
    print("interaction_data: ", interaction_data)
    return jsonify(
        {
            "type": InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
            "data": {"content": "Howdy Summoner! This command is still a WIP"},
        }
    )
