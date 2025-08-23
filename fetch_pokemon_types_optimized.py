#!/usr/bin/env python3
"""
Optimized Pokemon Type Data Fetcher using PokeAPI list endpoints
Uses pagination to efficiently fetch all Pokemon data with types and generations
"""

import urllib.request
import urllib.error
import json
import time
import os
from pathlib import Path

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

def fetch_pokemon_list(limit=1000, offset=0):
    """Fetch the complete Pokemon list using pagination"""
    print(f"📋 Fetching Pokemon list (limit={limit}, offset={offset})...")
    
    try:
        url = f"https://pokeapi.co/api/v2/pokemon/?limit={limit}&offset={offset}"
        request = urllib.request.Request(url)
        request.add_header('User-Agent', 'Pokemon-Calendar-TypeFetcher/1.0')
        
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode('utf-8'))
        
        print(f"✅ Found {len(data['results'])} Pokemon in this page")
        print(f"   Total available: {data['count']}")
        
        return data['results'], data.get('next'), data['count']
        
    except Exception as e:
        print(f"❌ Failed to fetch Pokemon list: {e}")
        return [], None, 0

def extract_pokemon_id_from_url(url):
    """Extract Pokemon ID from PokeAPI URL"""
    # URL format: https://pokeapi.co/api/v2/pokemon/25/
    try:
        return int(url.rstrip('/').split('/')[-1])
    except:
        return None

def fetch_pokemon_details(pokemon_url, pokemon_name, retry_count=3, delay=0.3):
    """Fetch detailed Pokemon data from individual endpoint"""
    for attempt in range(retry_count):
        try:
            request = urllib.request.Request(pokemon_url)
            request.add_header('User-Agent', 'Pokemon-Calendar-TypeFetcher/1.0')
            
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
            
            # Extract Pokemon ID and generation
            pokemon_id = data['id']
            generation = get_pokemon_generation(pokemon_id)
            
            # Extract type information
            types = []
            for type_info in data['types']:
                type_name = type_info['type']['name']
                types.append(type_name)
            
            # Clean up name for display
            clean_name = pokemon_name.replace('-', ' ').title()
            
            # Special name cases
            if 'Nidoran' in clean_name and pokemon_id == 29:
                clean_name = "Nidoran♀"
            elif 'Nidoran' in clean_name and pokemon_id == 32:
                clean_name = "Nidoran♂"
            elif clean_name == "Mr. Mime":
                clean_name = "Mr. Mime"
            
            # Rate limiting
            time.sleep(delay)
            
            return {
                'id': pokemon_id,
                'name': clean_name,
                'types': types,
                'generation': generation
            }
            
        except urllib.error.HTTPError as e:
            if e.code == 429:  # Rate limited
                wait_time = delay * (2 ** attempt)
                print(f"    Rate limited, waiting {wait_time:.1f}s...")
                time.sleep(wait_time)
                continue
            else:
                print(f"    HTTP Error {e.code} for {pokemon_name}")
                break
        except Exception as e:
            if attempt < retry_count - 1:
                wait_time = delay * (2 ** attempt)
                print(f"    Error: {e}, retrying in {wait_time:.1f}s...")
                time.sleep(wait_time)
            else:
                print(f"    Failed after {retry_count} attempts: {e}")
                break
    
    # Return fallback data
    pokemon_id = extract_pokemon_id_from_url(pokemon_url) or 0
    return {
        'id': pokemon_id,
        'name': pokemon_name.replace('-', ' ').title(),
        'types': ["normal"],
        'generation': get_pokemon_generation(pokemon_id)
    }

def main():
    """Main function to fetch all Pokemon type data using list API"""
    print("🔥 Optimized Pokemon Type Data Fetcher")
    print("=" * 50)
    print("Using PokeAPI list endpoints for efficient fetching...")
    print()
    
    # Check if output file already exists
    output_file = Path("pokemon_data_with_types.py")
    if output_file.exists():
        response = input(f"{output_file} already exists. Overwrite? (y/N): ").lower()
        if response != 'y':
            print("Operation cancelled.")
            return
    
    start_time = time.time()
    all_pokemon_data = {}
    
    # Step 1: Fetch the complete Pokemon list
    print("📋 Step 1: Fetching Pokemon list...")
    pokemon_list, next_url, total_count = fetch_pokemon_list(limit=2000)  # Get all at once
    
    if not pokemon_list:
        print("❌ Failed to fetch Pokemon list. Exiting.")
        return
    
    # Filter to only include Pokemon IDs 1-1025 (exclude forms and variants)
    valid_pokemon = []
    for pokemon in pokemon_list:
        pokemon_id = extract_pokemon_id_from_url(pokemon['url'])
        if pokemon_id and 1 <= pokemon_id <= 1025:
            valid_pokemon.append(pokemon)
    
    print(f"✅ Found {len(valid_pokemon)} valid Pokemon (IDs 1-1025)")
    print()
    
    # Step 2: Fetch detailed data for each Pokemon
    print("🔍 Step 2: Fetching detailed Pokemon data...")
    failed_pokemon = []
    
    for i, pokemon in enumerate(valid_pokemon, 1):
        pokemon_name = pokemon['name']
        pokemon_url = pokemon['url']
        pokemon_id = extract_pokemon_id_from_url(pokemon_url)
        
        print(f"[{i:4d}/{len(valid_pokemon)}] #{pokemon_id:04d}: {pokemon_name}... ", end="")
        
        pokemon_data = fetch_pokemon_details(pokemon_url, pokemon_name)
        if pokemon_data and pokemon_data['id']:
            all_pokemon_data[pokemon_data['id']] = pokemon_data
            types_display = '/'.join(pokemon_data['types']).upper()
            print(f"{types_display} (Gen {pokemon_data['generation']})")
        else:
            failed_pokemon.append(pokemon_name)
            print("FAILED")
        
        # Progress update every 100 Pokemon
        if i % 100 == 0:
            elapsed = time.time() - start_time
            rate = i / elapsed * 60  # Pokemon per minute
            estimated_remaining = (len(valid_pokemon) - i) / (i / elapsed) / 60  # minutes
            print(f"    Progress: {i}/{len(valid_pokemon)} ({i/len(valid_pokemon)*100:.1f}%)")
            print(f"    Rate: {rate:.1f} Pokemon/min, Est. remaining: {estimated_remaining:.1f} min")
            print()
    
    # Step 3: Generate the data file
    elapsed_total = time.time() - start_time
    print(f"\n✅ Data fetching complete!")
    print(f"Successfully fetched: {len(all_pokemon_data)}/{len(valid_pokemon)} Pokemon")
    print(f"Total time: {elapsed_total/60:.1f} minutes")
    if failed_pokemon:
        print(f"Failed Pokemon: {len(failed_pokemon)} ({', '.join(failed_pokemon[:10])}{'...' if len(failed_pokemon) > 10 else ''})")
    
    print(f"\n📝 Writing data to {output_file}...")
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('# Pokemon Data with Types and Generations\n')
        f.write('# Generated from PokeAPI using list endpoints\n')
        f.write('# This file contains comprehensive type information for Pokemon #1-1025\n')
        f.write('# This file is auto-generated. Do not edit manually.\n')
        f.write('\n')
        f.write('POKEMON_DATA = {\n')
        
        # Sort by Pokemon ID for clean output
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
        f.write('    return pokemon["generation"] if pokemon else 1\n\n')
        
        # Add statistics
        f.write(f'# Statistics:\n')
        f.write(f'# Total Pokemon: {len(all_pokemon_data)}\n')
        f.write(f'# Generation breakdown:\n')
        
        gen_counts = {}
        for data in all_pokemon_data.values():
            gen = data['generation']
            gen_counts[gen] = gen_counts.get(gen, 0) + 1
        
        for gen in sorted(gen_counts.keys()):
            f.write(f'# Generation {gen}: {gen_counts[gen]} Pokemon\n')
    
    print(f"✅ Complete! Pokemon data saved to: {output_file}")
    print(f"📊 Statistics: {len(all_pokemon_data)} Pokemon across {len(gen_counts)} generations")
    
    print("\n🎯 Next steps:")
    print("1. Review the generated file")
    print("2. Update pokemon_eink_calendar.py to use this new data structure")
    print("3. Add type icon display functionality")
    print("4. Test with --preview to see the results!")

if __name__ == "__main__":
    main()