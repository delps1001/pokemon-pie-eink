#!/usr/bin/env python3
"""
Fetch Pokedex descriptions from PokeAPI for all Pokemon
Creates a cached data structure with earliest generation flavor text for each Pokemon
"""

import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from pokemon_data_with_types import POKEMON_DATA

def get_earliest_flavor_text(pokemon_id, retries=3):
    """
    Get the earliest generation English flavor text for a Pokemon
    Returns the description from the first game where it appeared
    """
    for attempt in range(retries):
        try:
            url = f'https://pokeapi.co/api/v2/pokemon-species/{pokemon_id}'
            with urllib.request.urlopen(url, timeout=10) as response:
                data = json.loads(response.read().decode())
            
            flavor_entries = data.get('flavor_text_entries', [])
            if not flavor_entries:
                return None
            
            # Filter for English entries only
            english_entries = [entry for entry in flavor_entries if entry['language']['name'] == 'en']
            
            if not english_entries:
                return None
            
            # Get the Pokemon's generation to find the earliest appropriate text
            pokemon_generation = POKEMON_DATA.get(pokemon_id, {}).get('generation', 1)
            
            # Game version preference based on generation (earliest games first)
            generation_games = {
                1: ['red', 'blue', 'yellow'],
                2: ['gold', 'silver', 'crystal'],
                3: ['ruby', 'sapphire', 'emerald', 'firered', 'leafgreen'],
                4: ['diamond', 'pearl', 'platinum', 'heartgold', 'soulsilver'],
                5: ['black', 'white', 'black-2', 'white-2'],
                6: ['x', 'y', 'omega-ruby', 'alpha-sapphire'],
                7: ['sun', 'moon', 'ultra-sun', 'ultra-moon'],
                8: ['sword', 'shield'],
                9: ['scarlet', 'violet']
            }
            
            # Try to find flavor text from the Pokemon's original generation first
            for gen in range(pokemon_generation, 10):  # Check from original gen upward
                if gen in generation_games:
                    for game in generation_games[gen]:
                        for entry in english_entries:
                            if entry['version']['name'] == game:
                                # Clean up the flavor text
                                text = entry['flavor_text'].replace('\n', ' ').replace('\f', ' ')
                                text = ' '.join(text.split())  # Normalize whitespace
                                return text
            
            # If no specific game match, take the first English entry
            if english_entries:
                text = english_entries[0]['flavor_text'].replace('\n', ' ').replace('\f', ' ')
                text = ' '.join(text.split())
                return text
            
            return None
            
        except urllib.error.URLError as e:
            print(f"Network error for Pokemon #{pokemon_id} (attempt {attempt + 1}): {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            continue
        except Exception as e:
            print(f"Error fetching Pokemon #{pokemon_id} (attempt {attempt + 1}): {e}")
            if attempt < retries - 1:
                time.sleep(1)
            continue
    
    return None

def load_existing_descriptions():
    """Load existing descriptions from cache file if it exists"""
    try:
        from pokemon_pokedex_descriptions import POKEDEX_DESCRIPTIONS
        return POKEDEX_DESCRIPTIONS
    except ImportError:
        return {}

def fetch_all_pokedex_descriptions():
    """Fetch Pokedex descriptions for all Pokemon and save to cache"""
    cache_file = Path('./pokemon_pokedex_descriptions.py')
    
    # Load existing descriptions to avoid re-fetching
    descriptions = load_existing_descriptions()
    existing_count = len(descriptions)
    
    total_pokemon = len(POKEMON_DATA)
    missing_pokemon = [pid for pid in POKEMON_DATA.keys() if pid not in descriptions]
    
    print(f"Found {existing_count} cached descriptions")
    print(f"Need to fetch {len(missing_pokemon)} missing descriptions out of {total_pokemon} total Pokemon")
    
    if not missing_pokemon:
        print("✅ All descriptions already cached!")
        return descriptions
    
    fetched_count = 0
    for i, pokemon_id in enumerate(missing_pokemon, 1):
        pokemon_name = POKEMON_DATA[pokemon_id]['name']
        print(f"[{i}/{len(missing_pokemon)}] Fetching #{pokemon_id}: {pokemon_name}")
        
        description = get_earliest_flavor_text(pokemon_id)
        if description:
            descriptions[pokemon_id] = description
            print(f"  ✓ Got description: {description[:60]}...")
            fetched_count += 1
        else:
            # descriptions[pokemon_id] = f"A {pokemon_name} Pokemon."  # Fallback
            print(f"  ⚠ Using fallback description")
        
        # Rate limiting - be respectful to PokeAPI
        time.sleep(0.1)
        
        # Save progress every 50 Pokemon
        if i % 50 == 0:
            save_descriptions_cache(descriptions, cache_file)
            print(f"  💾 Saved progress: {existing_count + i} total descriptions")
    
    # Final save
    save_descriptions_cache(descriptions, cache_file)
    print(f"✅ Complete! Fetched {fetched_count} new descriptions")
    print(f"   Total: {len(descriptions)} descriptions cached")
    
    return descriptions

def save_descriptions_cache(descriptions, cache_file):
    """Save descriptions to a Python module file"""
    content = '''# Pokemon Pokedex Descriptions
# Fetched from PokeAPI - earliest generation flavor text for each Pokemon
# This file is auto-generated. Do not edit manually.

POKEDEX_DESCRIPTIONS = {
'''
    
    for pokemon_id in sorted(descriptions.keys()):
        description = descriptions[pokemon_id].replace('"', '\\"')  # Escape quotes
        content += f'    {pokemon_id}: "{description}",\n'
    
    content += '''}

def get_pokedex_description(pokemon_id):
    """Get the Pokedex description for a Pokemon by ID"""
    return POKEDEX_DESCRIPTIONS.get(pokemon_id, f"A Pokemon with ID {pokemon_id}.")
'''
    
    with open(cache_file, 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == "__main__":
    descriptions = fetch_all_pokedex_descriptions()
    print(f"Fetched descriptions for {len(descriptions)} Pokemon")