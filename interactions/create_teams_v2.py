import json
import requests
import itertools
from google.cloud import firestore

try:
    db = firestore.Client()
except Exception as e:
    print(f"Firestore Initialization Error: {e}")

# Linear MMR scale
RANK_MMR_DEFAULTS = {
    "IRON": 0,
    "BRONZE": 500,
    "SILVER": 1000,
    "GOLD": 1500,
    "PLATINUM": 2000,
    "EMERALD": 2500,
    "DIAMOND": 3000,
    "MASTER": 3500,
    "GRANDMASTER": 4000,
    "CHALLENGER": 4500,
}

DIVISION_OFFSET = {"1": 300, "2": 200, "3": 100, "4": 0}

# Role penalties for seeding new players
ROLE_PENALTIES = {
    "PRIMARY": 0,
    "SECONDARY": -200,
    "OFFROLE": -500,
}

ROLES = ["Top", "Jungle", "Mid", "ADC", "Support"]


class Player:
    def __init__(self, name, primary, secondary, mmr_map):
        self.name = name
        self.primary = primary
        self.secondary = secondary
        self.mmr_map = mmr_map  # Dict: {"Top": 1500, "Jungle": 1300, ...}
        self.best_mmr = max(mmr_map.values())

    def is_offrole(self, assigned_role):
        """Check if player is off-role (500+ MMR below their best role)"""
        return self.best_mmr - self.mmr_map[assigned_role] >= 500


def mmr_to_string(mmr: int):
    """Convert MMR to rank string like 'Gold 2' or 'Master'"""
    RANKS = list(RANK_MMR_DEFAULTS.keys())
    idx = min(mmr // 500, len(RANKS) - 1)

    if mmr >= RANK_MMR_DEFAULTS["MASTER"]:
        # High elo has no divisions
        return RANKS[int(idx)]
    else:
        # Calculate division (1-4)
        mmr_in_tier = mmr % 500
        div_idx = min(int(mmr_in_tier / 125), 3)  # 0-3 maps to divisions 4-1
        division = 4 - div_idx
        return f"{RANKS[int(idx)]} {division}"


def load_roster(roster_json):
    """Load players from JSON and fetch/seed their MMR from Firestore"""
    players = []
    for p in roster_json:
        name = p.get("ign")
        primary = p.get("primary role", "Fill")
        secondary = p.get("secondary role", "Fill")

        doc_ref = db.collection("players").document(name)
        doc = doc_ref.get()

        if doc.exists:
            player_data = doc.to_dict()
            mmr_map = player_data.get("mmr_map", {})
            players.append(Player(name, primary, secondary, mmr_map))
        else:
            # Seed new player with rank-based MMR
            rank_parts = p.get("rank", "Silver 3").split()
            rank = rank_parts[0].upper()
            division = rank_parts[1] if len(rank_parts) > 1 else "3"

            base_mmr = RANK_MMR_DEFAULTS.get(
                rank, RANK_MMR_DEFAULTS["SILVER"]
            ) + DIVISION_OFFSET.get(division, 0)

            seed_mmr = {}
            for role in ROLES:
                if role == primary:
                    penalty = ROLE_PENALTIES["PRIMARY"]
                elif role == secondary:
                    penalty = ROLE_PENALTIES["SECONDARY"]
                else:
                    penalty = ROLE_PENALTIES["OFFROLE"]
                seed_mmr[role] = base_mmr + penalty

            # Save to Firestore
            doc_ref.set(
                {
                    "name": name,
                    "primary": primary,
                    "secondary": secondary,
                    "mmr_map": seed_mmr,
                }
            )

            players.append(Player(name, primary, secondary, seed_mmr))

    return players


def generate_team_assignments(players):
    """Generate all possible team and role assignments"""
    # Split into 2 teams of 5
    for team1_indices in itertools.combinations(range(10), 5):
        team1 = [players[i] for i in team1_indices]
        team2 = [players[i] for i in range(10) if i not in team1_indices]

        # Generate all role assignments for each team
        for team1_roles in itertools.permutations(ROLES):
            for team2_roles in itertools.permutations(ROLES):
                yield (team1, team1_roles, team2, team2_roles)


def evaluate_assignment(team1, team1_roles, team2, team2_roles):
    """
    Evaluate a team assignment.
    Returns: (is_valid, mmr_delta, team1_total, team2_total)
    """
    team1_offroles = 0
    team2_offroles = 0
    team1_total = 0
    team2_total = 0

    # Calculate Team 1
    for player, role in zip(team1, team1_roles):
        mmr = player.mmr_map[role]
        team1_total += mmr
        if player.is_offrole(role):
            team1_offroles += 1

    # Calculate Team 2
    for player, role in zip(team2, team2_roles):
        mmr = player.mmr_map[role]
        team2_total += mmr
        if player.is_offrole(role):
            team2_offroles += 1

    # Check constraints
    if team1_offroles > 2 or team2_offroles > 2:
        return (False, float("inf"), 0, 0)

    if team1_offroles != team2_offroles:
        return (False, float("inf"), 0, 0)

    mmr_delta = abs(team1_total - team2_total)
    return (True, mmr_delta, team1_total, team2_total)


def find_best_teams(players):
    """Find the best balanced team assignment"""
    best_assignment = None
    best_delta = float("inf")
    best_totals = (0, 0)

    checked = 0
    for team1, team1_roles, team2, team2_roles in generate_team_assignments(players):
        is_valid, delta, t1_total, t2_total = evaluate_assignment(
            team1, team1_roles, team2, team2_roles
        )

        if is_valid and delta < best_delta:
            best_delta = delta
            best_assignment = (team1, team1_roles, team2, team2_roles)
            best_totals = (t1_total, t2_total)

        checked += 1
        if checked % 10000 == 0:
            print(f"Checked {checked} assignments...")

    print(f"Total assignments checked: {checked}")
    return best_assignment, best_delta, best_totals


def format_output(
    team1, team1_roles, team2, team2_roles, team1_total, team2_total, gap
):
    """Format the team assignment output"""
    output = "## TEAM ASSIGNMENTS\n\n**Team 1**\n"

    for player, role in zip(team1, team1_roles):
        mmr = player.mmr_map[role]
        rank_str = mmr_to_string(mmr)
        output += f"- {role}: {player.name} ({mmr} → {rank_str})\n"

    output += f"\n**Team 1 Total: {team1_total:,}**\n\n"
    output += "**Team 2**\n"

    for player, role in zip(team2, team2_roles):
        mmr = player.mmr_map[role]
        rank_str = mmr_to_string(mmr)
        output += f"- {role}: {player.name} ({mmr} → {rank_str})\n"

    output += f"\n**Team 2 Total: {team2_total:,}**\n\n"
    output += f"**Gap: {gap}**"

    return output


def run(interaction_data, app_id, token):
    """Main entry point called by Discord bot"""
    try:
        options = interaction_data.get("options", [])
        roster_str = next(
            opt["value"] for opt in options if opt["name"] == "roster_json"
        )
        roster_json = json.loads(roster_str)

        players = load_roster(roster_json)

        if len(players) != 10:
            raise ValueError("Roster must contain exactly 10 players.")

        # Find best team assignment
        assignment, gap, totals = find_best_teams(players)

        if assignment is None:
            raise ValueError("Could not find valid team assignment with constraints.")

        team1, team1_roles, team2, team2_roles = assignment
        team1_total, team2_total = totals

        # Format output
        output = format_output(
            team1, team1_roles, team2, team2_roles, team1_total, team2_total, gap
        )

        requests.patch(
            f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original",
            json={"content": output},
        )

    except Exception as e:
        error_msg = f"❌ Error: {str(e)}"
        print(error_msg)
        requests.patch(
            f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original",
            json={"content": error_msg},
        )
