#!/usr/bin/env python3
"""
Fetch Pokemon type information from PokeAPI and generate comprehensive Pokemon data file
This script fetches type information for all 1025 Pokemon and stores it locally
"""

import urllib.request
import urllib.error
import json
import time
import os
from pathlib import Path

# Type ID to name mapping (PokeAPI uses IDs, but we want clean names)
TYPE_ID_TO_NAME = {
    1: "normal", 2: "fighting", 3: "flying", 4: "poison", 5: "ground",
    6: "rock", 7: "bug", 8: "ghost", 9: "steel", 10: "fire",
    11: "water", 12: "grass", 13: "electric", 14: "psychic", 15: "ice",
    16: "dragon", 17: "dark", 18: "fairy"
}

# Pokemon generation ranges for type icon selection
GENERATION_RANGES = {
    1: (1, 151),      # Generation I: Kanto
    2: (152, 251),    # Generation II: Johto  
    3: (252, 386),    # Generation III: Hoenn
    4: (387, 493),    # Generation IV: Sinnoh
    5: (494, 649),    # Generation V: Unova
    6: (650, 721),    # Generation VI: Kalos
    7: (722, 809),    # Generation VII: Alola
    8: (810, 905),    # Generation VIII: Galar
    9: (906, 1025),   # Generation IX: Paldea
}

def get_pokemon_generation(pokemon_id):
    """Determine which generation a Pokemon belongs to"""
    for gen, (start, end) in GENERATION_RANGES.items():
        if start <= pokemon_id <= end:
            return gen
    return 1  # Default to Generation I for safety

def fetch_pokemon_data(pokemon_id, retry_count=3, delay=0.5):
    """Fetch Pokemon data from PokeAPI with retry logic and rate limiting"""
    for attempt in range(retry_count):
        try:
            url = f"https://pokeapi.co/api/v2/pokemon/{pokemon_id}"
            request = urllib.request.Request(url)
            request.add_header('User-Agent', 'Pokemon-Calendar-TypeFetcher/1.0')
            
            with urllib.request.urlopen(request, timeout=15) as response:
                data = json.loads(response.read().decode('utf-8'))
            
            # Extract type information
            types = []
            for type_info in data['types']:
                type_name = type_info['type']['name']
                types.append(type_name)
            
            # Get proper name (clean up hyphens and special characters)
            name = data['name'].replace('-', ' ').title()
            
            # Special cases for proper names
            if 'Nidoran' in name and pokemon_id == 29:
                name = "Nidoran♀"
            elif 'Nidoran' in name and pokemon_id == 32:
                name = "Nidoran♂"
            elif name == "Mr. Mime":
                name = "Mr. Mime"
            
            generation = get_pokemon_generation(pokemon_id)
            
            result = {
                'name': name,
                'types': types,
                'generation': generation
            }
            
            print(f"#{pokemon_id:04d}: {name} - {'/'.join(types).upper()} (Gen {generation})")
            
            # Rate limiting: be respectful to PokeAPI
            time.sleep(delay)
            
            return result
            
        except urllib.error.HTTPError as e:
            if e.code == 429:  # Rate limited
                wait_time = delay * (2 ** attempt)  # Exponential backoff
                print(f"  Rate limited, waiting {wait_time:.1f}s before retry {attempt + 1}/{retry_count}")
                time.sleep(wait_time)
                continue
            else:
                print(f"  HTTP Error {e.code} for Pokemon {pokemon_id}")
                break
        except Exception as e:
            if attempt < retry_count - 1:
                wait_time = delay * (2 ** attempt)
                print(f"  Error fetching Pokemon {pokemon_id}: {e}")
                print(f"  Retrying in {wait_time:.1f}s (attempt {attempt + 1}/{retry_count})")
                time.sleep(wait_time)
            else:
                print(f"  Failed to fetch Pokemon {pokemon_id} after {retry_count} attempts: {e}")
                break
    
    # Return fallback data if all attempts failed
    return {
        'name': f"Pokemon #{pokemon_id:03d}",
        'types': ["normal"],  # Default type
        'generation': get_pokemon_generation(pokemon_id)
    }

def main():
    """Main function to fetch all Pokemon type data"""
    print("🔥 Pokemon Type Data Fetcher")
    print("=" * 50)
    print("Fetching type information for all 1025 Pokemon...")
    print("This will take approximately 10-15 minutes with rate limiting.")
    print("")
    
    # Check if output file already exists
    output_file = Path("pokemon_data_with_types.py")
    if output_file.exists():
        response = input(f"{output_file} already exists. Overwrite? (y/N): ").lower()
        if response != 'y':
            print("Operation cancelled.")
            return
    
    all_pokemon_data = {}
    failed_pokemon = []
    
    # Fetch data for all Pokemon (1-1025)
    total_pokemon = 1025
    start_time = time.time()
    
    for pokemon_id in range(1, total_pokemon + 1):
        print(f"[{pokemon_id:4d}/{total_pokemon}] ", end="")
        
        pokemon_data = fetch_pokemon_data(pokemon_id)
        if pokemon_data:
            all_pokemon_data[pokemon_id] = pokemon_data
        else:
            failed_pokemon.append(pokemon_id)
        
        # Progress update every 50 Pokemon
        if pokemon_id % 50 == 0:
            elapsed = time.time() - start_time
            rate = pokemon_id / elapsed * 60  # Pokemon per minute
            estimated_remaining = (total_pokemon - pokemon_id) / (pokemon_id / elapsed) / 60  # minutes
            print(f"\n  Progress: {pokemon_id}/{total_pokemon} ({pokemon_id/total_pokemon*100:.1f}%)")
            print(f"  Rate: {rate:.1f} Pokemon/min, Est. time remaining: {estimated_remaining:.1f} min")
            print("")
    
    # Write the comprehensive data file
    print(f"\n✅ Finished fetching Pokemon data!")
    print(f"Successfully fetched: {len(all_pokemon_data)}/{total_pokemon} Pokemon")
    if failed_pokemon:
        print(f"Failed Pokemon IDs: {failed_pokemon}")
    
    # Generate the Python file
    print(f"\n📝 Writing comprehensive Pokemon data to {output_file}...")
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('# Pokemon Data with Types and Generations\n')
        f.write('# Generated from PokeAPI - Complete Pokemon database with type information\n')
        f.write('# This file is auto-generated. Do not edit manually.\n')
        f.write('\n')
        f.write('POKEMON_DATA = {\n')
        
        for pokemon_id in sorted(all_pokemon_data.keys()):
            data = all_pokemon_data[pokemon_id]
            name = data['name']
            types = data['types']
            generation = data['generation']
            
            # Format types list for Python
            types_str = '[' + ', '.join(f'"{t}"' for t in types) + ']'
            
            f.write(f'    {pokemon_id}: {{"name": "{name}", "types": {types_str}, "generation": {generation}}},\n')
        
        f.write('}\n\n')
        
        # Add helper functions
        f.write('def get_pokemon_info(pokemon_id):\n')
        f.write('    """Get Pokemon information by ID"""\n')
        f.write('    return POKEMON_DATA.get(pokemon_id)\n\n')
        
        f.write('def get_pokemon_types(pokemon_id):\n')
        f.write('    """Get Pokemon types by ID"""\n')
        f.write('    pokemon = POKEMON_DATA.get(pokemon_id)\n')
        f.write('    return pokemon["types"] if pokemon else []\n\n')
        
        f.write('def get_pokemon_generation(pokemon_id):\n')
        f.write('    """Get Pokemon generation by ID"""\n')
        f.write('    pokemon = POKEMON_DATA.get(pokemon_id)\n')
        f.write('    return pokemon["generation"] if pokemon else 1\n')
    
    elapsed_total = time.time() - start_time
    print(f"✅ Complete! Total time: {elapsed_total/60:.1f} minutes")
    print(f"📁 Pokemon data saved to: {output_file}")
    print("\nNext steps:")
    print("1. Review the generated file")
    print("2. Update pokemon_eink_calendar.py to use this new data structure")
    print("3. Add type icon display functionality")

if __name__ == "__main__":
    main()