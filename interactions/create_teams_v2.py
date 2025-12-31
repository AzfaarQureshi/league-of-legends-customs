import json
import requests
import itertools
from google.cloud import firestore
from scipy.optimize import linear_sum_assignment
import numpy as np

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

DIVISION_OFFSET = {
    "1": 375,
    "2": 250,
    "3": 125,
    "4": 0,
}

# Role penalties for seeding new players
ROLE_PENALTIES = {
    "PRIMARY": 0,
    "SECONDARY": -200,
    "OFFROLE": -500,
}

ROLES = ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"]


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
        primary = p.get("primary role", "Fill").upper()
        secondary = p.get("secondary role", "Fill").upper()

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
            ) + DIVISION_OFFSET.get(division, 100)

            seed_mmr = {}

            for role in ROLES:
                if role == primary or (primary == "FILL" or secondary == "FILL"):
                    penalty = 0
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


def assign_roles_optimally(team):
    """
    Use Hungarian algorithm to assign roles to maximize total MMR.
    Favors primary roles to improve role satisfaction.
    Returns: (role_assignment, total_mmr, offrole_count)
    """
    # Create cost matrix (negative MMR since we want to maximize)
    cost_matrix = np.zeros((5, 5))
    PRIMARY_BONUS = 300  # Preference for primary roles
    SECONDARY_BONUS = 100  # Slight preference for secondary roles

    for i, player in enumerate(team):
        for j, role in enumerate(ROLES):
            mmr_value = player.mmr_map[role]
            # Add bonus for role preferences
            if role == player.primary:
                mmr_value += PRIMARY_BONUS
            elif role == player.secondary:
                mmr_value += SECONDARY_BONUS
            cost_matrix[i][j] = -mmr_value

    # Solve assignment problem
    row_ind, col_ind = linear_sum_assignment(cost_matrix)

    # Build result
    role_assignment = []
    total_mmr = 0
    offrole_count = 0

    for player_idx, role_idx in zip(row_ind, col_ind):
        player = team[player_idx]
        role = ROLES[role_idx]
        mmr = player.mmr_map[role]

        role_assignment.append((player, role, mmr))
        total_mmr += mmr
        if player.is_offrole(role):
            offrole_count += 1

    return role_assignment, total_mmr, offrole_count


def find_best_teams(players):
    """Find the best balanced team assignment using optimized search"""
    best_assignment = None
    best_delta = float("inf")
    best_totals = (0, 0)
    best_offrole_diff = float("inf")

    checked = 0
    valid_found = 0

    # Sort team splits to try balanced splits first (by total MMR)
    player_mmrs = [p.best_mmr for p in players]
    total_mmr = sum(player_mmrs)
    target_per_team = total_mmr / 2

    # Generate and sort team splits by how close they are to 50/50 split
    team_splits = []
    for team1_indices in itertools.combinations(range(10), 5):
        team1_mmr = sum(player_mmrs[i] for i in team1_indices)
        deviation = abs(team1_mmr - target_per_team)
        team_splits.append((deviation, team1_indices))

    team_splits.sort()  # Try most balanced splits first

    # Only iterate through team splits (252 combinations)
    for deviation, team1_indices in team_splits:
        team1 = [players[i] for i in team1_indices]
        team2 = [players[i] for i in range(10) if i not in team1_indices]

        # Use Hungarian algorithm to find optimal role assignment for each team
        t1_assignment, t1_total, t1_offroles = assign_roles_optimally(team1)
        t2_assignment, t2_total, t2_offroles = assign_roles_optimally(team2)

        checked += 1

        delta = abs(t1_total - t2_total)
        offrole_diff = abs(t1_offroles - t2_offroles)

        # Prefer assignments with similar offrole counts, then minimize MMR delta
        is_better = False
        if offrole_diff < best_offrole_diff:
            is_better = True
        elif offrole_diff == best_offrole_diff and delta < best_delta:
            is_better = True

        if is_better:
            valid_found += 1
            best_delta = delta
            best_offrole_diff = offrole_diff
            best_assignment = (t1_assignment, t2_assignment)
            best_totals = (t1_total, t2_total)
            print(
                f"New best! Delta: {delta}, Offroles: T1={t1_offroles} T2={t2_offroles}, Checked: {checked}"
            )

            # Early exit if within 100 MMR and equal offroles
            if delta <= 100 and offrole_diff == 0:
                print(
                    f"Found excellent match within 100 MMR with equal offroles! Stopping search."
                )
                break

    print(f"Total team splits checked: {checked}")
    print(f"Valid assignments found: {valid_found}")

    return best_assignment, best_delta, best_totals

def format_output(team1_assignment, team2_assignment, team1_total, team2_total, gap):
    """Format the team assignment output with specific role ordering"""
    
    # 1. Define the desired order
    role_order = ["TOP", "JG", "MID", "ADC", "SUPPORT"]
    
    # 2. Create a helper function to determine the sorting priority
    # We use .index() to find the position of the role in our list
    def sort_by_role(entry):
        role = entry[1]
        return role_order.index(role) if role in role_order else 99

    # 3. Sort both teams based on the role order
    team1_sorted = sorted(team1_assignment, key=sort_by_role)
    team2_sorted = sorted(team2_assignment, key=sort_by_role)

    output = "## TEAM ASSIGNMENTS\n\n**Team 1**\n"

    # Iterate through the sorted lists instead of the raw inputs
    for player, role, mmr in team1_sorted:
        rank_str = mmr_to_string(mmr)
        output += f"- {role}: {player.name} ({mmr} → {rank_str})\n"

    output += f"\n**Team 1 Total: {team1_total:,}**\n\n"
    output += "**Team 2**\n"

    for player, role, mmr in team2_sorted:
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

        print("Finding optimal team assignment...")
        # Find best team assignment
        assignment, gap, totals = find_best_teams(players)

        if assignment is None:
            raise ValueError("Could not find valid team assignment with constraints.")

        team1_assignment, team2_assignment = assignment
        team1_total, team2_total = totals

        # Format output
        output = format_output(
            team1_assignment, team2_assignment, team1_total, team2_total, gap
        )

        # Send response to Discord
        requests.patch(
            f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original",
            json={"content": output},
        )

    except Exception as e:
        error_msg = f"❌ Error: {str(e)}"
        print(error_msg)
        import traceback

        traceback.print_exc()
        requests.patch(
            f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original",
            json={"content": error_msg},
        )
