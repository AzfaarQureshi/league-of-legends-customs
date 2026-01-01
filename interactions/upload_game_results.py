import json
import requests
import os
import base64
import hashlib
from google.cloud import firestore
import google.generativeai as genai
from fuzzywuzzy import fuzz, process
from datetime import datetime

try:
    db = firestore.Client()
    # Initialize Gemini with API key
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
except Exception as e:
    print(f"Initialization Error: {e}")

ROLE_ICONS_MAP = {
    "TOP": "Top",
    "JUNGLE": "Jungle",
    "MID": "Mid",
    "ADC": "ADC",
    "SUPPORT": "Support",
}


def generate_confirmation_id(winning_team, losing_team):
    """Generate a unique, stable ID for this game result based on team composition"""
    # Sort players by name to ensure consistency
    team1_str = ",".join(sorted([p["ign"] + p["role"] for p in winning_team]))
    team2_str = ",".join(sorted([p["ign"] + p["role"] for p in losing_team]))

    # Create hash of the game composition + timestamp (to prevent collisions)
    unique_str = f"{team1_str}|{team2_str}|{datetime.utcnow().isoformat()}"
    return hashlib.md5(unique_str.encode()).hexdigest()[:16]


def get_all_player_names():
    """Fetch all player names from Firestore for fuzzy matching"""
    try:
        players_ref = db.collection("players")
        docs = players_ref.stream()
        return [doc.id for doc in docs]
    except Exception as e:
        print(f"Error fetching player names: {e}")
        return []


def fuzzy_match_player_name(detected_name, all_player_names, threshold=80):
    """
    Use fuzzy matching to find the best matching player name.
    Returns: (matched_name, confidence_score, was_corrected)
    """
    if not all_player_names:
        return detected_name, 0, False

    # Check for exact match first (case-insensitive)
    for name in all_player_names:
        if name.lower() == detected_name.lower():
            return name, 100, False

    # Use fuzzy matching
    best_match, score = process.extractOne(
        detected_name, all_player_names, scorer=fuzz.ratio
    )

    if score >= threshold:
        was_corrected = best_match != detected_name
        return best_match, score, was_corrected
    else:
        # No good match found, return original
        return detected_name, score, False


def analyze_screenshot_with_gemini(image_url):
    """
    Use Gemini Vision to extract game results from screenshot.
    Returns: dict with winning_team, losing_team, player info, and name corrections
    """
    try:
        # Download image
        response = requests.get(image_url)
        image_bytes = response.content

        # Initialize model
        model = genai.GenerativeModel("gemini-2.5-flash")

        prompt = """
        Analyze this League of Legends post-game screenshot and extract the following information in JSON format:
        
        1. Determine if this is a VICTORY or DEFEAT screen (look for the text at the top)
        2. Identify which team is Team 1 and which is Team 2
        3. For each player, extract:
           - IGN (in-game name) - BE VERY CAREFUL with characters like O vs 0, I vs l, etc.
           - Role (based on the icon next to their name: Top, Jungle, Mid, ADC, Support)
           - Which team they're on (Team 1 or Team 2)
        
        Return ONLY valid JSON in this exact format:
        {
            "result": "VICTORY" or "DEFEAT",
            "player_perspective_team": 1 or 2,
            "team1": [
                {"ign": "PlayerName", "role": "TOP"},
                {"ign": "PlayerName", "role": "JUNGLE"},
                ...
            ],
            "team2": [
                {"ign": "PlayerName", "role": "TOP"},
                {"ign": "PlayerName", "role": "JUNGLE"},
                ...
            ]
        }
        
        Notes:
        - Use uppercase for roles: TOP, JUNGLE, MID, ADC, SUPPORT
        - The player perspective team is the team shown at the top (usually Team 1)
        - Extract names exactly as shown - be careful with similar characters
        """

        # Create image part
        image_part = {"mime_type": "image/png", "data": image_bytes}

        # Generate content
        response = model.generate_content([prompt, image_part])

        # Parse JSON from response
        response_text = response.text.strip()
        # Remove markdown code blocks if present
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]

        game_data = json.loads(response_text.strip())

        # Get all player names from database for fuzzy matching
        all_player_names = get_all_player_names()
        name_corrections = []

        # Apply fuzzy matching to all detected names
        for team_key in ["team1", "team2"]:
            for player in game_data[team_key]:
                detected_name = player["ign"]
                matched_name, score, was_corrected = fuzzy_match_player_name(
                    detected_name, all_player_names, threshold=80
                )

                if was_corrected:
                    name_corrections.append(
                        {
                            "detected": detected_name,
                            "corrected": matched_name,
                            "confidence": score,
                        }
                    )
                    player["ign"] = matched_name  # Update with corrected name
                elif score < 80:
                    # Low confidence match - might be a new/unknown player
                    print(
                        f"Warning: Low confidence match for '{detected_name}' (best: {matched_name}, score: {score})"
                    )

        # Determine winning and losing teams
        result = game_data["result"]
        perspective_team = game_data["player_perspective_team"]

        if result == "VICTORY":
            winning_team_num = perspective_team
            losing_team_num = 2 if perspective_team == 1 else 1
        else:
            losing_team_num = perspective_team
            winning_team_num = 2 if perspective_team == 1 else 1

        winning_team = game_data[f"team{winning_team_num}"]
        losing_team = game_data[f"team{losing_team_num}"]

        return {
            "winning_team": winning_team,
            "losing_team": losing_team,
            "name_corrections": name_corrections,
            "success": True,
        }

    except Exception as e:
        print(f"Error analyzing screenshot: {e}")
        import traceback

        traceback.print_exc()
        return {"success": False, "error": str(e)}


def format_confirmation_message(
    winning_team, losing_team, role_swaps=None, name_corrections=None
):
    """Format the confirmation message with team results, role swaps, and name corrections"""
    message = "## 🎮 Game Results Detected\n\n"

    # Show name corrections if any
    if name_corrections and len(name_corrections) > 0:
        message += "### ✏️ **NAME CORRECTIONS**\n"
        for correction in name_corrections:
            message += f"- Detected: `{correction['detected']}` → Corrected: **{correction['corrected']}** ({correction['confidence']}% match)\n"
        message += "\n"

    # Show role swaps if any
    if role_swaps and len(role_swaps) > 0:
        message += "### 🔄 **ROLE SWAPS DETECTED**\n"
        for swap in role_swaps:
            message += f"- **{swap['player']}**: Expected {swap['expected']} → Played **{swap['actual']}**\n"
        message += "\n"

    message += "### ✅ **WINNING TEAM**\n"
    for player in winning_team:
        message += f"- **{player['role']}**: {player['ign']}\n"

    message += "\n### ❌ **LOSING TEAM**\n"
    for player in losing_team:
        message += f"- **{player['role']}**: {player['ign']}\n"

    message += "\n*Please confirm these results are correct:*"

    return message


def create_confirmation_components(confirmation_id):
    """Create Discord button components for confirmation with embedded ID"""
    return [
        {
            "type": 1,  # Action Row
            "components": [
                {
                    "type": 2,  # Button
                    "style": 3,  # Success (green)
                    "label": "✓ Confirm",
                    "custom_id": f"confirm_results:{confirmation_id}",
                },
                {
                    "type": 2,  # Button
                    "style": 4,  # Danger (red)
                    "label": "✗ Cancel",
                    "custom_id": f"cancel_results:{confirmation_id}",
                },
            ],
        }
    ]


def calculate_mmr_changes(winning_team, losing_team):
    """
    Calculate MMR changes for all players based on lane matchups.
    Winners gain: 25 + min(35, (opponent_mmr - your_mmr) / 100)
    Losers lose: 25 (flat)

    Uses actual roles played (from screenshot) as source of truth.
    """
    mmr_changes = {}
    role_swaps = []

    # Get current MMRs for all players
    player_mmrs = {}
    player_data = {}
    for player in winning_team + losing_team:
        doc = db.collection("players").document(player["ign"]).get()
        if doc.exists:
            data = doc.to_dict()
            player_data[player["ign"]] = data
            player_mmrs[player["ign"]] = data.get("mmr_map", {})
        else:
            print(f"Warning: Player {player['ign']} not found in database")
            return None, None

    # Detect role swaps by comparing actual role vs primary/secondary
    for player in winning_team + losing_team:
        player_name = player["ign"]
        actual_role = player["role"]
        data = player_data[player_name]
        primary = data.get("primary", "FILL")
        secondary = data.get("secondary", "FILL")

        if actual_role != primary and actual_role != secondary and primary != "FILL":
            role_swaps.append(
                {
                    "player": player_name,
                    "expected": f"{primary}/{secondary}",
                    "actual": actual_role,
                }
            )

    # Match players by role and calculate MMR changes
    for winner in winning_team:
        winner_name = winner["ign"]
        winner_role = winner["role"]  # Actual role played from screenshot

        # Find opponent in same role
        opponent = next((p for p in losing_team if p["role"] == winner_role), None)

        if not opponent:
            print(f"Warning: No opponent found for {winner_name} in {winner_role}")
            continue

        opponent_name = opponent["ign"]

        # Get MMRs for the ACTUAL role played (from screenshot)
        winner_mmr = player_mmrs[winner_name].get(winner_role, 1500)
        opponent_mmr = player_mmrs[opponent_name].get(winner_role, 1500)

        # Calculate MMR gain for winner (capped at 60)
        mmr_diff = opponent_mmr - winner_mmr
        bonus = min(35, max(0, mmr_diff // 100))  # Cap bonus at 35 so total is 60
        winner_gain = 25 + bonus

        # Store changes
        mmr_changes[winner_name] = {
            "role": winner_role,  # Use actual role from screenshot
            "change": winner_gain,
            "old_mmr": winner_mmr,
            "new_mmr": winner_mmr + winner_gain,
            "opponent": opponent_name,
            "opponent_mmr": opponent_mmr,
        }

        mmr_changes[opponent_name] = {
            "role": winner_role,  # Use actual role from screenshot
            "change": -25,
            "old_mmr": opponent_mmr,
            "new_mmr": opponent_mmr - 25,
            "opponent": winner_name,
            "opponent_mmr": winner_mmr,
        }

    return mmr_changes, role_swaps


def commit_results_to_firestore(winning_team, losing_team, mmr_changes, role_swaps):
    """
    Update player MMRs in Firestore and store game history.
    Uses actual roles played from screenshot as source of truth.
    Returns: success boolean
    """
    try:
        game_id = db.collection("games").document().id
        timestamp = datetime.utcnow()

        # Update each player's MMR
        for player_name, change_data in mmr_changes.items():
            player_ref = db.collection("players").document(player_name)
            player_doc = player_ref.get()

            if player_doc.exists:
                player_data = player_doc.to_dict()
                mmr_map = player_data.get("mmr_map", {})

                # Update the specific role's MMR (using actual role played)
                role = change_data["role"]
                mmr_map[role] = change_data["new_mmr"]

                # Update player document
                player_ref.update({"mmr_map": mmr_map})

                # Store game history
                is_winner = change_data["change"] > 0

                # Check if this was a role swap for this player
                was_role_swap = any(
                    swap["player"] == player_name for swap in role_swaps
                )

                history_ref = db.collection("game_history").document()
                history_ref.set(
                    {
                        "game_id": game_id,
                        "player_name": player_name,
                        "role_played": role,  # Actual role from screenshot
                        "was_role_swap": was_role_swap,
                        "result": "WIN" if is_winner else "LOSS",
                        "mmr_before": change_data["old_mmr"],
                        "mmr_after": change_data["new_mmr"],
                        "mmr_change": change_data["change"],
                        "opponent": change_data["opponent"],
                        "opponent_mmr": change_data["opponent_mmr"],
                        "timestamp": timestamp,
                    }
                )

        return True

    except Exception as e:
        print(f"Error committing results: {e}")
        import traceback

        traceback.print_exc()
        return False


def run(interaction_data, app_id, token):
    """Main entry point for upload_game_results command"""
    try:
        # Extract screenshot URL from interaction
        options = interaction_data.get("options", [])
        attachment = next(
            opt["value"] for opt in options if opt["name"] == "result_screenshot"
        )

        # Get attachment URL from resolved data
        resolved = interaction_data.get("resolved", {})
        attachments = resolved.get("attachments", {})
        attachment_data = attachments.get(attachment)

        if not attachment_data:
            raise ValueError("Could not retrieve screenshot attachment")

        image_url = attachment_data.get("url")

        # Analyze screenshot with Gemini
        result = analyze_screenshot_with_gemini(image_url)

        if not result.get("success"):
            error_msg = f"❌ Failed to analyze screenshot: {result.get('error', 'Unknown error')}"
            requests.patch(
                f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original",
                json={"content": error_msg},
            )
            return

        winning_team = result["winning_team"]
        losing_team = result["losing_team"]
        name_corrections = result.get("name_corrections", [])

        # Calculate MMR changes (uses actual roles from screenshot)
        mmr_changes, role_swaps = calculate_mmr_changes(winning_team, losing_team)

        if mmr_changes is None:
            error_msg = "❌ Error: Some players not found in database. Please ensure all players are registered."
            requests.patch(
                f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original",
                json={"content": error_msg},
            )
            return

        # Generate a stable confirmation ID
        confirmation_id = generate_confirmation_id(winning_team, losing_team)

        # Store data temporarily for the confirmation callback
        confirmation_data = {
            "winning_team": winning_team,
            "losing_team": losing_team,
            "mmr_changes": mmr_changes,
            "role_swaps": role_swaps,
            "name_corrections": name_corrections,
            "timestamp": datetime.utcnow(),
        }

        # Store in Firestore with the stable ID (expires after 1 hour)
        temp_ref = db.collection("pending_confirmations").document(confirmation_id)
        temp_ref.set(confirmation_data)

        # Format confirmation message (shows name corrections, role swaps if any)
        message = format_confirmation_message(
            winning_team, losing_team, role_swaps, name_corrections
        )
        components = create_confirmation_components(confirmation_id)

        # Send confirmation message
        requests.patch(
            f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original",
            json={"content": message, "components": components},
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


def handle_confirmation(interaction_data, app_id, token):
    """Handle confirmation button clicks"""
    try:
        custom_id = interaction_data.get("data", {}).get("custom_id")

        # Extract the confirmation ID from the custom_id
        # Format is either "confirm_results:ID" or "cancel_results:ID"
        if ":" not in custom_id:
            requests.patch(
                f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original",
                json={"content": "❌ Error: Invalid button data.", "components": []},
            )
            return

        action, confirmation_id = custom_id.split(":", 1)

        if action == "cancel_results":
            # Delete pending confirmation
            db.collection("pending_confirmations").document(confirmation_id).delete()

            # Update message
            requests.patch(
                f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original",
                json={"content": "❌ Results cancelled.", "components": []},
            )
            return

        elif action == "confirm_results":
            # Retrieve pending data using the confirmation ID
            temp_ref = db.collection("pending_confirmations").document(confirmation_id)
            temp_doc = temp_ref.get()

            if not temp_doc.exists:
                requests.patch(
                    f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original",
                    json={
                        "content": "❌ Error: Confirmation data not found or expired.",
                        "components": [],
                    },
                )
                return

            confirmation_data = temp_doc.to_dict()
            winning_team = confirmation_data["winning_team"]
            losing_team = confirmation_data["losing_team"]
            mmr_changes = confirmation_data["mmr_changes"]
            role_swaps = confirmation_data.get("role_swaps", [])

            # Commit to Firestore
            success = commit_results_to_firestore(
                winning_team, losing_team, mmr_changes, role_swaps
            )

            if success:
                # Build summary of MMR changes
                summary = "## ✅ Results Updated!\n\n"

                # Show role swaps if any
                if role_swaps and len(role_swaps) > 0:
                    summary += "### 🔄 Role Swaps:\n"
                    for swap in role_swaps:
                        summary += f"- **{swap['player']}** played **{swap['actual']}** (expected {swap['expected']})\n"
                    summary += "\n"

                summary += "### MMR Changes:\n"

                for player_name, change_data in sorted(
                    mmr_changes.items(), key=lambda x: -x[1]["change"]
                ):
                    change = change_data["change"]
                    role = change_data["role"]
                    old_mmr = change_data["old_mmr"]
                    new_mmr = change_data["new_mmr"]

                    emoji = "📈" if change > 0 else "📉"
                    sign = "+" if change > 0 else ""
                    summary += f"{emoji} **{player_name}** ({role}): {old_mmr} → {new_mmr} ({sign}{change})\n"

                requests.patch(
                    f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original",
                    json={"content": summary, "components": []},
                )
            else:
                requests.patch(
                    f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original",
                    json={
                        "content": "❌ Error updating results in database.",
                        "components": [],
                    },
                )

            # Clean up pending confirmation
            temp_ref.delete()

    except Exception as e:
        error_msg = f"❌ Error: {str(e)}"
        print(error_msg)
        import traceback

        traceback.print_exc()
        requests.patch(
            f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/@original",
            json={"content": error_msg, "components": []},
        )
