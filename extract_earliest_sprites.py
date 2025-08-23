#!/usr/bin/env python3
"""
Pokemon Sprite Extractor - Extract Earliest Game Sprites
Extract sprites for Pokemon 1-1025 using the earliest possible sprite version from each Pokemon's debut game.
Resizes all sprites to match Pokemon Home dimensions (96x96 pixels).
"""

import os
import sys
import shutil
from pathlib import Path

# Try to import PIL, but handle gracefully if not available
try:
    from PIL import Image
    HAS_PIL = True
    print("✓ PIL/Pillow found - will resize sprites to 96x96")
except ImportError:
    HAS_PIL = False
    print("⚠ PIL/Pillow not found - will copy original sprites without resizing")
    print("To install: pip3 install --user --break-system-packages Pillow")

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
        "sprites/sprites/sprites/pokemon/versions/generation-vii/ultra-sun-ultra-moon",
        "sprites/sprites/sprites/pokemon/versions/generation-vii/icons"
    ],
    8: [
        "sprites/sprites/sprites/pokemon/versions/generation-viii/icons"
    ],
    9: [
        # Gen IX Pokemon will fallback to Pokemon Home sprites
        "sprites/sprites/sprites/pokemon"  # Pokemon Home fallback
    ]
}

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
            sprite_file = os.path.join(sprite_path, f"{pokemon_id}.png")
            if os.path.exists(sprite_file):
                return sprite_file, f"Gen {generation} ({os.path.basename(sprite_path)})"
    
    # Fallback to Pokemon Home sprites
    home_sprite = f"sprites/sprites/sprites/pokemon/{pokemon_id}.png"
    if os.path.exists(home_sprite):
        return home_sprite, "Pokemon Home (fallback)"
    
    return None, "Not found"

def resize_sprite(input_path, output_path, target_size=(96, 96)):
    """Resize sprite to target dimensions using PIL with crispy pixel-perfect scaling."""
    if not HAS_PIL:
        # If PIL not available, just copy the file
        shutil.copy2(input_path, output_path)
        return False
    
    try:
        with Image.open(input_path) as img:
            # Convert to RGBA to handle transparency
            if img.mode != 'RGBA':
                img = img.convert('RGBA')
            
            original_size = img.size
            
            # For pixel art (small sprites), use nearest neighbor for crispy scaling
            # This preserves the pixelated retro look without blurring
            if original_size[0] <= 64 or original_size[1] <= 64:
                # Use NEAREST neighbor for pixel-perfect crispy scaling
                resized = img.resize(target_size, Image.Resampling.NEAREST)
                print(f"  📐 Crispy scaling: {original_size} → {target_size} (nearest-neighbor)")
            else:
                # For larger sprites, use high-quality resampling
                resized = img.resize(target_size, Image.Resampling.LANCZOS)
                print(f"  📐 Smooth scaling: {original_size} → {target_size} (lanczos)")
            
            resized.save(output_path, 'PNG', optimize=True)
            return True
    except Exception as e:
        print(f"Error resizing {input_path}: {e}")
        # Fallback to copy if resize fails
        shutil.copy2(input_path, output_path)
        return False

def main():
    """Extract earliest sprites for Pokemon 1-1025."""
    
    # Create output directory
    output_dir = Path("earliest_pokemon_sprites")
    output_dir.mkdir(exist_ok=True)
    
    print(f"🎯 Extracting earliest sprites for Pokemon #1-1025")
    print(f"📁 Output directory: {output_dir.absolute()}")
    print(f"🎨 Target size: 96x96 pixels (Pokemon Home standard)")
    print()
    
    # Statistics
    found = 0
    not_found = 0
    resized_count = 0
    copied_count = 0
    
    # Track which sources we're using
    source_stats = {}
    
    for pokemon_id in range(1, 1026):
        sprite_path, source = find_earliest_sprite(pokemon_id)
        
        if sprite_path:
            found += 1
            output_path = output_dir / f"{pokemon_id:04d}.png"  # Zero-padded filename
            
            # Track source statistics
            source_stats[source] = source_stats.get(source, 0) + 1
            
            # Resize/copy the sprite
            was_resized = resize_sprite(sprite_path, str(output_path))
            if was_resized:
                resized_count += 1
            else:
                copied_count += 1
                
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
    
    if HAS_PIL:
        print(f"Sprites resized to 96x96: {resized_count}")
        print(f"Sprites copied without resize: {copied_count}")
    else:
        print(f"Sprites copied (no resizing): {found}")
    
    print()
    print("SOURCE BREAKDOWN:")
    print("-" * 40)
    for source, count in sorted(source_stats.items()):
        print(f"{source}: {count} Pokemon")
    
    print()
    print(f"✅ Extraction complete! Sprites saved to: {output_dir.absolute()}")
    
    if not HAS_PIL:
        print("\n💡 To enable sprite resizing, install PIL:")
        print("pip3 install --user --break-system-packages Pillow")

if __name__ == "__main__":
    main()