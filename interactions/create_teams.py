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
    "DIAMOND": 2550,  # Top 2.5% 
    "MASTER": 3500,  # Top 0.5%
    "GRANDMASTER": 4300,
    "CHALLENGER": 5600,
}

# Division weighting (75 MMR spread per tier)
DIVISION_OFFSET = {"1": 75, "2": 50, "3": 25, "4": 0}

# Role Penalties (Applied to Base MMR)
ROLE_PENALTY_MAP = {
    "PRIMARY": 0,
    "SECONDARY": -250,  # Skill drop when on 2nd choice
    "OFFROLE": -600,  # Significant penalty for off-role
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
            score = 0
            if role == p.primary:
                score = 50  # Heavy weight for Primary
            elif role == p.secondary:
                score = 15  # Medium weight for Secondary
            else:
                score = -100 # Heavy penalty for forcing an Off-role
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
        first_player = players[0]
        remaining_indices = range(1, 10)

        for combo in itertools.combinations(remaining_indices, 4):
            team_a_players = [first_player] + [players[i] for i in combo]
            team_a_indices_set = set([0] + list(combo))
            team_b_players = [
                players[i] for i in range(10) if i not in team_a_indices_set
            ]

            # Get assignments and the pref scores used to pick them
            assign_a, total_a = calculate_best_roles_for_team(team_a_players)
            assign_b, total_b = calculate_best_roles_for_team(team_b_players)
            
            # Sum of preference scores for both teams (higher is better)
            # You would need to return the score from calculate_best_roles_for_team
            # For now, we calculate it here based on the chosen assignment
            def get_p_score(assign):
                score = 0
                for item in assign:
                    if item['role'] == item['p'].primary: score += 10
                    elif item['role'] == item['p'].secondary: score += 5
                    else: score -= 20 # Heavy penalty for off-role in the selection rank
                return score

            matchup_pref_score = get_p_score(assign_a) + get_p_score(assign_b)
            gap = abs(total_a - total_b)
            
            all_matchups.append(
                {
                    "gap": gap,
                    "pref_score": matchup_pref_score,
                    "a": assign_a,
                    "total_a": total_a,
                    "b": assign_b,
                    "total_b": total_b,
                }
            )

        # SORTING LOGIC: 
        # Primary: Highest Preference Score (Role satisfaction)
        # Secondary: Lowest MMR Gap (Balance)
        all_matchups.sort(key=lambda x: (-x["pref_score"], x["gap"]))

        best = all_matchups[0]

        msg = "# 📖 TEAM ASSIGNMENTS\n"
        msg += f"## ⚔️ Option 1 **Gap: {int(best['gap'])}**\n"
        
        for label, team, total in [("Team A", best["a"], best["total_a"]), 
                                   ("Team B", best["b"], best["total_b"])]:
            msg += f"**{label}**\n"
            for item in team:
                msg += f"- {item['role']}: {item['p'].name} ({item['p'].rank_str}) → {int(item['base'])} + {int(item['penalty'])} = {int(item['effective'])}\n"
            msg += f"\n**{label} Total: {int(total)}**\n\n"

        url = f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original"
        requests.patch(url, json={"content": msg})

    except Exception as e:
        requests.patch(
            f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original",
            json={"content": f"❌ Error: {str(e)}"},
        )
