#!/usr/bin/env python3
"""
Pokemon Sprite Extractor - Extract Earliest Game Sprites
Extract sprites for Pokemon 1-1025 using the earliest possible sprite version from each Pokemon's debut game.
Sprites are copied byte for byte at their native resolution: resampling pixel art
blurs it, and the display scales each sprite by a whole number itself. Where a
folder only has an animated GIF, its first frame is saved as the PNG.

Also extracts the shiny variant of every sprite into earliest_pokemon_sprites_shiny/.
Shiny sprites follow the same "earliest generation" rule as the regular ones, with
two exceptions because shinies did not exist everywhere:
  * Gen I Pokemon (no shiny in Red/Blue/Yellow) use the Gen II Gold shiny.
  * Generations whose version folders have no shiny art (the icon sets used for
    Gen VII/VIII, and Gen IX) fall back to the Pokemon Home shiny.

Usage:
    python3 extract_earliest_sprites.py [--sprites-base PATH] [--shiny-only | --no-shiny]
"""

import argparse

import os
import sys
import shutil
from pathlib import Path

# Pokemon generation data - which Pokemon debuted in which generation
POKEMON_GENERATIONS = {
    1: list(range(1, 152)),      # Gen I: #1-151 (Red/Blue/Yellow)
    2: list(range(152, 252)),    # Gen II: #152-251 (Gold/Silver/Crystal)  
    3: list(range(252, 387)),    # Gen III: #252-386 (Ruby/Sapphire/Emerald)
    4: list(range(387, 494)),    # Gen IV: #387-493 (Diamond/Pearl/Platinum)
    5: list(range(494, 650)),    # Gen V: #494-649 (Black/White)
    6: list(range(650, 722)),    # Gen VI: #650-721 (X/Y)
    7: list(range(722, 810)),    # Gen VII: #722-809 (Sun/Moon/Ultra)
    8: list(range(810, 906)),    # Gen VIII: #810-905 (Sword/Shield)
    9: list(range(906, 1026)),   # Gen IX: #906-1025 (Scarlet/Violet)
}

# Generation sprite paths in order of preference (earliest first)
GENERATION_PATHS = {
    1: [
        "sprites/sprites/sprites/pokemon/versions/generation-i/red-blue",
        "sprites/sprites/sprites/pokemon/versions/generation-i/yellow"
    ],
    2: [
        "sprites/sprites/sprites/pokemon/versions/generation-ii/gold",
        "sprites/sprites/sprites/pokemon/versions/generation-ii/silver", 
        "sprites/sprites/sprites/pokemon/versions/generation-ii/crystal"
    ],
    3: [
        "sprites/sprites/sprites/pokemon/versions/generation-iii/ruby-sapphire",
        "sprites/sprites/sprites/pokemon/versions/generation-iii/emerald",
        "sprites/sprites/sprites/pokemon/versions/generation-iii/firered-leafgreen"
    ],
    4: [
        "sprites/sprites/sprites/pokemon/versions/generation-iv/diamond-pearl",
        "sprites/sprites/sprites/pokemon/versions/generation-iv/platinum",
        "sprites/sprites/sprites/pokemon/versions/generation-iv/heartgold-soulsilver"
    ],
    5: [
        "sprites/sprites/sprites/pokemon/versions/generation-v/black-white"
    ],
    6: [
        "sprites/sprites/sprites/pokemon/versions/generation-vi/x-y",
        "sprites/sprites/sprites/pokemon/versions/generation-vi/omegaruby-alphasapphire"
    ],
    7: [
        # Animated GIFs upstream; anything missing falls back to Pokemon Home,
        # not to the 40x30 box icons
        "sprites/sprites/sprites/pokemon/versions/generation-vii/ultra-sun-ultra-moon"
    ],
    8: [
        # Sword/Shield have no 2D battle sprites: Gen VIII uses the Pokemon Home sprite
        "sprites/sprites/sprites/pokemon"  # Pokemon Home fallback
    ],
    9: [
        # Gen IX Pokemon will fallback to Pokemon Home sprites
        "sprites/sprites/sprites/pokemon"  # Pokemon Home fallback
    ]
}

# Base folder of the PokeAPI/sprites checkout's "pokemon" directory. Every path in
# GENERATION_PATHS above starts with this prefix; --sprites-base swaps it out.
DEFAULT_SPRITES_BASE = "sprites/sprites/sprites/pokemon"
SPRITES_BASE = DEFAULT_SPRITES_BASE


def _rebase(path):
    """Point a hard-coded sprite path at the configured SPRITES_BASE."""
    return SPRITES_BASE + path[len(DEFAULT_SPRITES_BASE):]


# Shiny sprites live in a "shiny/" subfolder next to the regular ones. Gen I has none
# (shinies debuted in Gen II), so Gen I Pokemon use the Gen II Gold shiny instead.
SHINY_EXTRA_PATHS = {
    1: ["sprites/sprites/sprites/pokemon/versions/generation-ii/gold"],
}

def _sprite_in(folder, pokemon_id):
    """The sprite file for a Pokemon in a folder (PNG preferred, else GIF), or None."""
    for extension in ("png", "gif"):
        sprite_file = os.path.join(folder, f"{pokemon_id}.{extension}")
        if os.path.exists(sprite_file):
            return sprite_file
    return None


def save_sprite(sprite_path, output_path):
    """Copy a PNG unchanged; save the first frame of an animated GIF as a PNG."""
    if sprite_path.lower().endswith(".gif"):
        from PIL import Image
        with Image.open(sprite_path) as animation:
            animation.seek(0)
            animation.convert("RGBA").save(output_path, "PNG", optimize=True)
    else:
        shutil.copy2(sprite_path, output_path)


def get_pokemon_generation(pokemon_id):
    """Determine which generation a Pokemon belongs to."""
    for gen, pokemon_list in POKEMON_GENERATIONS.items():
        if pokemon_id in pokemon_list:
            return gen
    return 9  # Default to Gen IX for any new Pokemon

def find_earliest_sprite(pokemon_id):
    """Find the earliest available sprite for a Pokemon."""
    generation = get_pokemon_generation(pokemon_id)
    
    # Check generation-specific paths first
    if generation in GENERATION_PATHS:
        for sprite_path in GENERATION_PATHS[generation]:
            sprite_file = _sprite_in(_rebase(sprite_path), pokemon_id)
            if sprite_file:
                return sprite_file, f"Gen {generation} ({os.path.basename(sprite_path)})"
    
    # Fallback to Pokemon Home sprites
    home_sprite = f"{SPRITES_BASE}/{pokemon_id}.png"
    if os.path.exists(home_sprite):
        return home_sprite, "Pokemon Home (fallback)"
    
    return None, "Not found"

def find_earliest_shiny_sprite(pokemon_id):
    """Find the shiny sprite from the same generation as the regular sprite.

    Falls back to the Gen II Gold shiny for Gen I, and to the Pokemon Home shiny
    when the generation's version folders contain no shiny art.
    """
    generation = get_pokemon_generation(pokemon_id)

    for sprite_path in GENERATION_PATHS.get(generation, []) + SHINY_EXTRA_PATHS.get(generation, []):
        sprite_file = _sprite_in(os.path.join(_rebase(sprite_path), "shiny"), pokemon_id)
        if sprite_file:
            return sprite_file, f"Gen {generation} shiny ({os.path.basename(sprite_path)})"

    home_sprite = f"{SPRITES_BASE}/shiny/{pokemon_id}.png"
    if os.path.exists(home_sprite):
        return home_sprite, "Pokemon Home shiny (fallback)"

    return None, "Not found"

def extract_all(find_fn, output_dir, label):
    """Extract sprites for Pokemon 1-1025 using find_fn to locate each source file."""
    
    # Create output directory
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)
    
    print(f"🎯 Extracting earliest {label} sprites for Pokemon #1-1025")
    print(f"📁 Output directory: {output_dir.absolute()}")
    print("🎨 Sprites are copied unchanged, at native resolution")
    print()
    
    # Statistics
    found = 0
    not_found = 0
    
    # Track which sources we're using
    source_stats = {}
    
    for pokemon_id in range(1, 1026):
        sprite_path, source = find_fn(pokemon_id)
        
        if sprite_path:
            found += 1
            output_path = output_dir / f"{pokemon_id:04d}.png"  # Zero-padded filename
            
            # Track source statistics
            source_stats[source] = source_stats.get(source, 0) + 1
            
            save_sprite(sprite_path, str(output_path))

            # Progress indicator
            if pokemon_id % 100 == 0 or pokemon_id <= 10:
                print(f"✓ #{pokemon_id:04d}: {source}")
        else:
            not_found += 1
            print(f"✗ #{pokemon_id:04d}: Not found")
    
    # Print summary
    print()
    print("="*60)
    print("EXTRACTION SUMMARY")
    print("="*60)
    print(f"Total Pokemon processed: 1025")
    print(f"Sprites found: {found}")
    print(f"Sprites not found: {not_found}")

    print()
    print("SOURCE BREAKDOWN:")
    print("-" * 40)
    for source, count in sorted(source_stats.items()):
        print(f"{source}: {count} Pokemon")
    
    print()
    print(f"✅ Extraction complete! Sprites saved to: {output_dir.absolute()}")

def main():
    global SPRITES_BASE
    parser = argparse.ArgumentParser(description="Extract earliest-generation Pokemon sprites (regular and shiny).")
    parser.add_argument("--sprites-base", default=DEFAULT_SPRITES_BASE,
                        help="Path to the 'pokemon' folder of a PokeAPI/sprites checkout "
                             f"(default: {DEFAULT_SPRITES_BASE})")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--shiny-only", action="store_true", help="Only extract shiny sprites")
    group.add_argument("--no-shiny", action="store_true", help="Skip shiny sprites")
    args = parser.parse_args()
    SPRITES_BASE = args.sprites_base.rstrip("/")

    if not args.shiny_only:
        extract_all(find_earliest_sprite, "earliest_pokemon_sprites", "regular")
    if not args.no_shiny:
        extract_all(find_earliest_shiny_sprite, "earliest_pokemon_sprites_shiny", "shiny")

if __name__ == "__main__":
    main()