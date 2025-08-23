#!/usr/bin/env python3
"""
Generate Pokemon Names Database from PokeAPI
Fetches accurate Pokemon names for IDs 1-1025 from the official Pokemon API
"""

import urllib.request
import urllib.error
import json
import time
import sys
from pathlib import Path

def fetch_pokemon_name(pokemon_id):
    """Fetch Pokemon name from PokeAPI for a given ID"""
    try:
        url = f"https://pokeapi.co/api/v2/pokemon/{pokemon_id}"
        
        # Create request with timeout
        request = urllib.request.Request(url)
        request.add_header('User-Agent', 'Pokemon-Calendar/1.0')
        
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
        
        # Get the Pokemon name and format it properly
        name = data['name']
        
        # Handle special cases and formatting
        formatted_name = format_pokemon_name(name)
        
        print(f"✓ #{pokemon_id:04d}: {formatted_name}")
        return formatted_name
        
    except urllib.error.URLError as e:
        print(f"✗ Failed to fetch #{pokemon_id}: {e}")
        return None
    except Exception as e:
        print(f"✗ Error processing #{pokemon_id}: {e}")
        return None

def format_pokemon_name(name):
    """Format Pokemon name for display"""
    # Handle special characters and formatting
    
    # Replace hyphens with spaces and title case
    formatted = name.replace('-', ' ').title()
    
    # Special cases for Pokemon with unusual names
    special_cases = {
        'Nidoran F': 'Nidoran♀',
        'Nidoran M': 'Nidoran♂',
        'Mr Mime': 'Mr. Mime',
        'Mime Jr': 'Mime Jr.',
        'Tapu Koko': 'Tapu Koko',
        'Tapu Lele': 'Tapu Lele', 
        'Tapu Bulu': 'Tapu Bulu',
        'Tapu Fini': 'Tapu Fini',
        'Type Null': 'Type: Null',
        'Jangmo O': 'Jangmo-o',
        'Hakamo O': 'Hakamo-o',
        'Kommo O': 'Kommo-o',
        'Mr Rime': 'Mr. Rime',
        'Sirfetch D': "Sirfetch'd",
        'Farfetch D': "Farfetch'd"
    }
    
    # Apply special case formatting
    if formatted in special_cases:
        formatted = special_cases[formatted]
    
    return formatted

def generate_pokemon_names_file():
    """Generate the pokemon_names.py file with data from PokeAPI"""
    
    print("🚀 Fetching Pokemon names from PokeAPI...")
    print("This will take a few minutes to avoid rate limiting.")
    print()
    
    pokemon_names = {}
    failed_ids = []
    
    # Fetch names for Pokemon 1-1025
    for pokemon_id in range(1, 1026):
        name = fetch_pokemon_name(pokemon_id)
        
        if name:
            pokemon_names[pokemon_id] = name
        else:
            failed_ids.append(pokemon_id)
            # Add a fallback name
            pokemon_names[pokemon_id] = f"Pokemon #{pokemon_id:03d}"
        
        # Rate limiting - be respectful to PokeAPI
        if pokemon_id % 50 == 0:
            print(f"\n📊 Progress: {pokemon_id}/1025 ({pokemon_id/1025*100:.1f}%)")
            print("⏳ Pausing briefly to respect API rate limits...\n")
            time.sleep(2)
        elif pokemon_id % 10 == 0:
            time.sleep(0.5)
        else:
            time.sleep(0.1)
    
    # Generate the Python file
    print("\n📝 Generating pokemon_names.py file...")
    
    file_content = '''# Pokemon Names Database
# Generated from PokeAPI - Complete list of Pokemon #1-1025 with proper names
# This file is auto-generated. Do not edit manually.

POKEMON_NAMES = {
'''
    
    # Add all Pokemon names in a nicely formatted way
    for i in range(1, 1026):
        name = pokemon_names.get(i, f"Pokemon #{i:03d}")
        # Escape quotes in names
        escaped_name = name.replace('"', '\\"')
        file_content += f'    {i}: "{escaped_name}",\n'
    
    file_content += '}\n'
    
    # Write to file
    output_path = Path("pokemon_names.py")
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(file_content)
    
    print(f"✅ Successfully generated {output_path}")
    print(f"📊 Total Pokemon: {len(pokemon_names)}")
    
    if failed_ids:
        print(f"⚠️  Failed to fetch {len(failed_ids)} Pokemon (using fallback names):")
        print(f"   {failed_ids}")
    
    print()
    print("🎉 Pokemon names database ready!")
    print("You can now use this file in your Pokemon calendar application.")
    
    return len(pokemon_names), len(failed_ids)

def main():
    """Main function"""
    print("🔍 Pokemon Names Generator")
    print("=" * 40)
    print("Fetching accurate Pokemon names from PokeAPI")
    print("This ensures proper names, capitalization, and special characters")
    print("=" * 40)
    print()
    
    try:
        total, failed = generate_pokemon_names_file()
        
        print("=" * 40)
        print("📋 SUMMARY")
        print("=" * 40)
        print(f"✅ Successfully fetched: {total - failed} Pokemon names")
        print(f"⚠️  Failed fetches: {failed} Pokemon")
        print(f"📁 Output file: pokemon_names.py")
        print()
        print("Next steps:")
        print("1. Deploy the updated files: ./deploy_to_pi.sh")
        print("2. Restart the Pokemon calendar service")
        print("3. Enjoy proper Pokemon names on your display!")
        
    except KeyboardInterrupt:
        print("\n\n⏹️  Generation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error generating Pokemon names: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()