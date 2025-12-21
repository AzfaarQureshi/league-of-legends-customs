import json
import requests
import itertools
from google.cloud import firestore

try:
    db = firestore.Client()
except Exception as e:
    print(f"Firestore Initialization Error: {e}")

# Weighted MMR based on real-world distribution (approx. top % per rank)
# Note the +400 jump for Diamond to reflect the 2.5% vs 10% curve
RANK_MMR_DEFAULTS = {
    "IRON": 600,  # Bottom 10%
    "BRONZE": 900,  # Next 20%
    "SILVER": 1200,  # Median
    "GOLD": 1500,  # Top 40%
    "PLATINUM": 1800,  # Top 25%
    "EMERALD": 2100,  # Top 10%
    "DIAMOND": 2550,  # Top 2.5% (The requested "Much Better" jump)
    "MASTER": 3000,  # Top 0.5%
    "GRANDMASTER": 3300,
    "CHALLENGER": 3600,
}

# Division weighting (75 MMR spread per tier)
DIVISION_OFFSET = {"1": 75, "2": 50, "3": 25, "4": 0}

# Role Penalties (Applied to Base MMR)
ROLE_PENALTY_MAP = {
    "PRIMARY": 0,
    "SECONDARY": -150,  # Skill drop when on 2nd choice
    "OFFROLE": -350,  # Significant penalty for off-role
}

ROLES = ["Top", "Jungle", "Mid", "ADC", "Support"]


class Player:
    def __init__(self, name, primary, secondary, rank, db_mmr=None):
        self.name = name
        self.primary = primary.capitalize()
        self.secondary = secondary.capitalize()
        self.rank_str = rank.upper()
        self.base_mmr = db_mmr if db_mmr else RANK_MMR_DEFAULTS.get(self.rank_str, 1200)

    def get_effective_stats(self, assigned_role):
        if assigned_role == self.primary:
            penalty = ROLE_PENALTY_MAP["PRIMARY"]
        elif assigned_role == self.secondary:
            penalty = ROLE_PENALTY_MAP["SECONDARY"]
        else:
            penalty = ROLE_PENALTY_MAP["OFFROLE"]
        return self.base_mmr, penalty, (self.base_mmr + penalty)


def sync_players(roster_json):
    players = []
    for p in roster_json:
        # Mapping new JSON keys
        name = p.get("ign")
        primary = p.get("primary role", "Fill")
        secondary = p.get("secondary role", "Fill")

        # Parse "Silver 1" into Tier and Division
        rank_raw = p.get("rank", "Silver 4")
        rank_parts = rank_raw.split()
        rank_tier = rank_parts[0].upper()
        # Default to division 4 if not specified (e.g. for Master+)
        rank_div = rank_parts[1] if len(rank_parts) > 1 else "4"

        doc_ref = db.collection("players").document(name)
        doc = doc_ref.get()

        if doc.exists:
            player_data = doc.to_dict()
            players.append(
                Player(
                    name,
                    primary,
                    secondary,
                    rank_tier,
                    db_mmr=player_data.get("mmr"),
                )
            )
        else:
            # Seed new player with Tier base + Division offset
            base = RANK_MMR_DEFAULTS.get(rank_tier, 1200)
            offset = DIVISION_OFFSET.get(rank_div, 0)
            seed_mmr = base + offset

            doc_ref.set({"name": name, "mmr": seed_mmr, "rank": rank_raw.upper()})
            players.append(Player(name, primary, secondary, rank_tier, db_mmr=seed_mmr))
    return players


def calculate_best_roles_for_team(team_players):
    """Permutes roles for 5 players to find the assignment that respects preferences most."""
    best_assignment = []
    best_pref_score = -9999

    for role_perm in itertools.permutations(ROLES):
        current_score = 0
        assignment = []
        for i, role in enumerate(role_perm):
            p = team_players[i]
            base, penalty, effective = p.get_effective_stats(role)
            # Preference Score weighting: Primary(10), Secondary(5), Off(0)
            score = 10 if role == p.primary else (5 if role == p.secondary else 0)
            current_score += score
            assignment.append(
                {
                    "role": role,
                    "p": p,
                    "base": base,
                    "penalty": penalty,
                    "effective": effective,
                }
            )

        if current_score > best_pref_score:
            best_pref_score = current_score
            best_assignment = assignment

    best_assignment.sort(key=lambda x: ROLES.index(x["role"]))
    total_mmr = sum(x["effective"] for x in best_assignment)
    return best_assignment, total_mmr


def run(interaction_data, app_id, token):
    try:
        options = interaction_data.get("options", [])
        roster_str = next(
            opt["value"] for opt in options if opt["name"] == "roster_json"
        )
        players = sync_players(json.loads(roster_str))

        if len(players) != 10:
            raise ValueError("Roster must contain exactly 10 players.")

        all_matchups = []

        # ANCHOR METHOD:
        # Fix the first player (index 0) to Team A.
        # Then, pick 4 more players from the remaining 9 to join them.
        # This results in exactly 126 unique 5v5 splits (10C5 / 2).
        first_player = players[0]
        remaining_indices = range(1, 10)

        for combo in itertools.combinations(remaining_indices, 4):
            # Team A is Player 0 + the 4 players picked in the combo
            team_a_players = [first_player] + [players[i] for i in combo]

            # Team B is everyone else
            team_a_indices_set = set([0] + list(combo))
            team_b_players = [
                players[i] for i in range(10) if i not in team_a_indices_set
            ]

            assign_a, total_a = calculate_best_roles_for_team(team_a_players)
            assign_b, total_b = calculate_best_roles_for_team(team_b_players)

            gap = abs(total_a - total_b)
            all_matchups.append(
                {
                    "gap": gap,
                    "a": assign_a,
                    "total_a": total_a,
                    "b": assign_b,
                    "total_b": total_b,
                }
            )

        # Sort by smallest MMR gap
        all_matchups.sort(key=lambda x: x["gap"])

        # Because every entry is now a unique personnel split,
        # the top 3 are guaranteed to have different people on each team.
        top_3 = all_matchups[:2]

        emojis = ["⚔️", "🛡️", "🏹"]
        msg = "# 📖 UNIQUE TEAM OPTIONS\n"
        for i, opt in enumerate(top_3):
            msg += f"## {emojis[i]} OPTION {i+1} (MMR Gap: {int(opt['gap'])})\n"
            for label, team, total in [
                ("Team A", opt["a"], opt["total_a"]),
                ("Team B", opt["b"], opt["total_b"]),
            ]:
                msg += f"**{label}** (Total: {int(total)})\n"
                for item in team:
                    msg += f"- {item['role']}: {item['p'].name} ({item['p'].rank_str}) → {int(item['base'])} + ({int(item['penalty'])}) = **{int(item['effective'])}**\n"
                msg += "\n"
            msg += "---\n"

        url = (
            f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original"
        )
        requests.patch(url, json={"content": msg})

    except Exception as e:
        requests.patch(
            f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original",
            json={"content": f"❌ Error: {str(e)}"},
        )
