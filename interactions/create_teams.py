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

        all_matchups = []
        # Check all 252 unique ways to split 10 players into two teams
        for team_a_indices in itertools.combinations(range(10), 5):
            team_a_players = [players[i] for i in team_a_indices]
            team_b_players = [players[i] for i in range(10) if i not in team_a_indices]

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

        # Get top 3 most balanced options
        all_matchups.sort(key=lambda x: x["gap"])
        top_3 = all_matchups[:3]

        # Format Response
        msg = "# 📖 TEAM ASSIGNMENTS\n"
        for i, opt in enumerate(top_3):
            msg += f"# ⚔️ OPTION {i+1} (MMR Gap: {int(opt['gap'])})\n"
            for label, team, total in [
                ("Team A", opt["a"], opt["total_a"]),
                ("Team B", opt["b"], opt["total_b"]),
            ]:
                msg += f"**{label}**\n"
                for item in team:
                    msg += f"- {item['role']}: {item['p'].name} ({item['p'].rank_str}) → {int(item['base'])} + ({int(item['penalty'])}) = **{int(item['effective'])}**\n"
                msg += f"**{label} Total: {int(total)}**\n\n"
            msg += "---\n"

        # Patch the deferred message
        url = (
            f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original"
        )
        requests.patch(url, json={"content": msg})

    except Exception as e:
        requests.patch(
            f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original",
            json={"content": f"❌ Error: {str(e)}"},
        )


# def calculate_best_roles_for_team(team_players):
#     """Finds the single best role assignment for a specific set of 5 players."""
#     best_assignment = []
#     best_pref_score = -9999

#     for role_perm in itertools.permutations(ROLES):
#         current_score = 0
#         assignment = []
#         for i, role in enumerate(role_perm):
#             p = team_players[i]
#             base, penalty, effective = p.get_effective_stats(role)
#             score = 10 if role == p.primary else (5 if role == p.secondary else 0)
#             current_score += score
#             assignment.append(
#                 {
#                     "role": role,
#                     "p": p,
#                     "base": base,
#                     "penalty": penalty,
#                     "effective": effective,
#                 }
#             )

#         if current_score > best_pref_score:
#             best_pref_score = current_score
#             best_assignment = assignment

#     best_assignment.sort(key=lambda x: ROLES.index(x["role"]))
#     total_mmr = sum(x["effective"] for x in best_assignment)
#     return best_assignment, total_mmr


# def run(interaction_data, app_id, token):
#     try:
#         options = interaction_data.get("options", [])
#         roster_str = next(
#             opt["value"] for opt in options if opt["name"] == "roster_json"
#         )

#         data = clean_and_load_json(roster_str)
#         players = sync_players(data)

#         if len(players) < 10:
#             raise ValueError(
#                 f"Found {len(players)} valid players, but 10 are required."
#             )

#         all_matchups = []
#         # combinations(range(10), 5) gives 252 ways to pick Team A.
#         # This includes mirror matchups (picking 1,2,3,4,5 is same as picking 6,7,8,9,10).
#         for team_a_indices in itertools.combinations(range(10), 5):
#             # DEDUPLICATION: We only process the matchup if the first player is on Team A.
#             # This cuts the 252 permutations to 126 unique player splits.
#             if 0 not in team_a_indices:
#                 continue

#             team_a_players = [players[i] for i in team_a_indices]
#             team_b_players = [players[i] for i in range(10) if i not in team_a_indices]

#             assign_a, total_a = calculate_best_roles_for_team(team_a_players)
#             assign_b, total_b = calculate_best_roles_for_team(team_b_players)

#             gap = abs(total_a - total_b)
#             all_matchups.append(
#                 {
#                     "gap": gap,
#                     "a": assign_a,
#                     "total_a": total_a,
#                     "b": assign_b,
#                     "total_b": total_b,
#                 }
#             )

#         # Get top 3 most balanced splits
#         all_matchups.sort(key=lambda x: x["gap"])
#         top_3 = all_matchups[:3]

#         msg = "# 📖 TEAM ASSIGNMENTS\n"
#         for i, opt in enumerate(top_3):
#             msg += f"# ⚔️ OPTION {i+1} (MMR Gap: {int(opt['gap'])})\n"
#             for label, team, total in [
#                 ("Team A", opt["a"], opt["total_a"]),
#                 ("Team B", opt["b"], opt["total_b"]),
#             ]:
#                 msg += f"**{label}**\n"
#                 for item in team:
#                     msg += f"- {item['role']}: {item['p'].name} ({item['p'].rank_str}) → {int(item['effective'])} MMR\n"
#                 msg += f"**Total: {int(total)}**\n\n"
#             msg += "---\n"

#         requests.patch(
#             f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original",
#             json={"content": msg},
#         )

#     except Exception as e:
#         requests.patch(
#             f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original",
#             json={"content": f"❌ Error: {str(e)}"},
#         )
